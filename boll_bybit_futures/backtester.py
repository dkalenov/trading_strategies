"""
Bollinger Bands Strategy — Realistic Backtester module.

Event-driven backtester with:
- Position sizing: risk_pct of capital per trade
- Funding rate: 0.01% every 8h (futures)
- Commission: 0.04% taker (Bybit futures)
- Slippage: dynamic based on order size
- Proper PnL with quantity
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from strategy import (
    BollingerBandsCore, BollingerRSIFilter, BollingerSqueezeFilter,
    BollingerMeanReversion, Signal, StrategyParams,
    calculate_bollinger_bands, calculate_rsi, calculate_atr
)


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: Optional[pd.Timestamp] = None
    side: str = 'LONG'
    entry_price: float = 0.0
    exit_price: float = 0.0
    quantity: float = 0.0
    notional: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    commission: float = 0.0
    slippage_cost: float = 0.0
    funding_cost: float = 0.0
    exit_reason: str = ''
    stop_loss: float = 0.0
    take_profit: float = 0.0
    max_favorable_pct: float = 0.0
    max_adverse_pct: float = 0.0

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0


@dataclass
class BacktestStats:
    initial_capital: float = 0.0
    final_capital: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_duration_bars: int = 0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    avg_trade_duration_hours: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    total_commission: float = 0.0
    total_slippage: float = 0.0
    total_funding: float = 0.0
    long_trades: int = 0
    short_trades: int = 0
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0


FUNDING_INTERVAL_HOURS = 8.0

_BARS_PER_YEAR = {
    '1m': 525600, '3m': 175200, '5m': 105120, '15m': 35040, '30m': 17520,
    '1h': 8760, '2h': 4380, '4h': 2190, '6h': 1460, '8h': 1095, '12h': 730,
    '1d': 365, '3d': 122, '1w': 52,
}


def interval_bars_per_year(interval: str) -> int:
    if interval not in _BARS_PER_YEAR:
        raise ValueError(f"Unsupported interval: {interval}")
    return _BARS_PER_YEAR[interval]


class Backtester:
    def __init__(self, initial_capital: float = 100000,
                 risk_pct: float = 0.01,
                 max_leverage: int = 20,
                 commission_rate: float = 0.00055,
                 slippage_rate: float = 0.0002,
                 funding_rate: float = 0.0001,
                 funding_interval_bars: Optional[float] = None,
                 bars_per_year: Optional[int] = None,
                 interval: Optional[str] = None):
        """Bar-by-bar backtester.

        Cost model (each cost is counted exactly once):
          * slippage is applied to the fill prices (entry and exit), so it is
            already inside gross PnL. Trade.slippage_cost is informational.
          * commission is charged on notional at entry and exit.
          * funding is charged as a cost on full notional for every 8h period
            held (conservative: it ignores that shorts receive funding when
            the rate is positive).

        Pass ``interval`` (e.g. '1h', '5m') so funding periods and Sharpe
        annualisation match the bar size. Explicit ``funding_interval_bars``
        or ``bars_per_year`` override the values derived from ``interval``.
        """
        self.initial_capital = initial_capital
        self.risk_pct = risk_pct
        self.max_leverage = max_leverage
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.funding_rate = funding_rate
        if interval is not None:
            derived_bars_per_year = interval_bars_per_year(interval)
        else:
            derived_bars_per_year = 8760
        self.bars_per_year = bars_per_year if bars_per_year is not None else derived_bars_per_year
        if funding_interval_bars is None:
            bar_hours = 8760.0 / self.bars_per_year
            funding_interval_bars = FUNDING_INTERVAL_HOURS / bar_hours
        self.funding_interval_bars = funding_interval_bars

    def run(self, df: pd.DataFrame, strategy_variant: str = 'basic',
            params: Optional[StrategyParams] = None) -> tuple[BacktestStats, list[Trade]]:
        if params is None:
            params = StrategyParams()

        closes = df['Close'].values
        upper, middle, lower = calculate_bollinger_bands(
            closes, params.bb_timeperiod, params.bb_nbdevup, params.bb_nbdevdn
        )
        bb_widths = (upper - lower) / middle

        match strategy_variant:
            case 'basic':
                return self._run_basic(df, upper, middle, lower, params)
            case 'rsi_filter':
                return self._run_rsi_filter(df, upper, middle, lower, params)
            case 'squeeze':
                return self._run_squeeze(df, upper, middle, lower, bb_widths, params)
            case 'mean_reversion':
                return self._run_mean_reversion(df, upper, middle, lower, params)
            case _:
                raise ValueError(f"Unknown strategy variant: {strategy_variant}")

    def _open_position(self, entry_price, timestamp, side, sl, tp, capital):
        sl_distance = abs(entry_price - sl)
        max_loss = capital * self.risk_pct
        quantity_by_risk = max_loss / sl_distance if sl_distance > 0 else 0
        notional_by_risk = quantity_by_risk * entry_price
        max_notional = capital * self.max_leverage
        notional = min(notional_by_risk, max_notional)
        quantity = notional / entry_price
        slippage_cost = notional * self.slippage_rate  # informational: already inside entry_price
        commission = notional * self.commission_rate

        return {
            'entry_price': entry_price,
            'entry_time': timestamp,
            'side': side,
            'stop_loss': sl,
            'take_profit': tp,
            'quantity': quantity,
            'notional': notional,
            'entry_commission': commission,
            'entry_slippage': slippage_cost,
            'max_favorable_pct': 0.0,
            'max_adverse_pct': 0.0,
        }

    def _close_position(self, position, exit_price, timestamp, reason, bars_held):
        notional = position['notional']
        quantity = position['quantity']
        exit_slippage = notional * self.slippage_rate  # informational: already inside exit_price
        exit_commission = notional * self.commission_rate

        if position['side'] == 'LONG':
            gross_pnl = (exit_price - position['entry_price']) * quantity
        else:
            gross_pnl = (position['entry_price'] - exit_price) * quantity

        # Funding is paid on the full position notional (not just the margin/
        # "borrowed" part) — this matches how Bybit and other perpetual
        # exchanges actually charge funding, regardless of leverage used.
        funding_periods = bars_held / self.funding_interval_bars
        funding_cost = notional * self.funding_rate * funding_periods

        total_commission = position['entry_commission'] + exit_commission
        total_slippage = position['entry_slippage'] + exit_slippage
        # Slippage is already reflected in entry/exit prices; do not subtract it again.
        total_costs = total_commission + funding_cost
        net_pnl = gross_pnl - total_costs
        pnl_pct = gross_pnl / notional * 100 if notional > 0 else 0
        duration_hours = (timestamp - position['entry_time']).total_seconds() / 3600

        return Trade(
            entry_time=position['entry_time'],
            exit_time=timestamp,
            side=position['side'],
            entry_price=position['entry_price'],
            exit_price=exit_price,
            quantity=quantity,
            notional=notional,
            pnl=net_pnl,
            pnl_pct=pnl_pct,
            commission=total_commission,
            slippage_cost=total_slippage,
            funding_cost=funding_cost,
            exit_reason=reason,
            stop_loss=position['stop_loss'],
            take_profit=position['take_profit'],
            max_favorable_pct=position['max_favorable_pct'],
            max_adverse_pct=position['max_adverse_pct'],
        )

    def _unrealized_pnl(self, position, current_price):
        if position is None:
            return 0.0
        quantity = position['quantity']
        if position['side'] == 'LONG':
            return (current_price - position['entry_price']) * quantity
        return (position['entry_price'] - current_price) * quantity

    def _check_exit(self, position, high, low, close, signal, timestamp, entry_bar, i):
        """Check if position should be exited (SL/TP/signal). Returns (trade, new_capital) or (None, 0)."""
        bars_held = i - entry_bar

        if position['side'] == 'LONG':
            position['max_favorable_pct'] = max(
                position['max_favorable_pct'],
                (high - position['entry_price']) / position['entry_price'] * 100
            )
            position['max_adverse_pct'] = max(
                position['max_adverse_pct'],
                (position['entry_price'] - low) / position['entry_price'] * 100
            )
        else:
            position['max_favorable_pct'] = max(
                position['max_favorable_pct'],
                (position['entry_price'] - low) / position['entry_price'] * 100
            )
            position['max_adverse_pct'] = max(
                position['max_adverse_pct'],
                (high - position['entry_price']) / position['entry_price'] * 100
            )

        exit_price = None
        exit_reason = ''

        if position['side'] == 'LONG':
            if low <= position['stop_loss']:
                exit_price, exit_reason = position['stop_loss'], 'STOP_LOSS'
            elif high >= position['take_profit']:
                exit_price, exit_reason = position['take_profit'], 'TAKE_PROFIT'
            elif signal == Signal.SELL:
                exit_price, exit_reason = close, 'SIGNAL_SELL'
        else:
            if high >= position['stop_loss']:
                exit_price, exit_reason = position['stop_loss'], 'STOP_LOSS'
            elif low <= position['take_profit']:
                exit_price, exit_reason = position['take_profit'], 'TAKE_PROFIT'
            elif signal == Signal.BUY:
                exit_price, exit_reason = close, 'SIGNAL_BUY'

        if exit_price is not None:
            # Exits are market orders: the fill is worse than the trigger level.
            if position['side'] == 'LONG':
                exit_price *= (1 - self.slippage_rate)
            else:
                exit_price *= (1 + self.slippage_rate)
            trade = self._close_position(position, exit_price, timestamp, exit_reason, bars_held)
            return trade, trade.pnl
        return None, 0

    def _run_basic(self, df, upper, middle, lower, params) -> tuple[BacktestStats, list[Trade]]:
        core = BollingerBandsCore(params)
        capital = self.initial_capital
        position = None
        entry_bar = 0
        trades = []
        equity_curve = [capital]
        warmup = params.bb_timeperiod + 1

        for i in range(warmup, len(df)):
            close = df['Close'].iloc[i]
            high = df['High'].iloc[i]
            low = df['Low'].iloc[i]
            timestamp = df.index[i]

            signal = core.update(close, upper[i], middle[i], lower[i])

            if position is not None:
                trade, pnl = self._check_exit(position, high, low, close, signal, timestamp, entry_bar, i)
                if trade:
                    trades.append(trade)
                    capital += pnl
                    position = None

            if position is None and capital > 0:
                if signal == Signal.BUY:
                    entry_price = close * (1 + self.slippage_rate)
                    sl = entry_price * (1 - params.stop_loss_multiplier * (upper[i] - lower[i]) / 2 / entry_price)
                    tp = entry_price * (1 + params.take_profit_multiplier * (upper[i] - lower[i]) / 2 / entry_price)
                    position = self._open_position(entry_price, timestamp, 'LONG', sl, tp, capital)
                    entry_bar = i
                elif signal == Signal.SELL:
                    entry_price = close * (1 - self.slippage_rate)
                    sl = entry_price * (1 + params.stop_loss_multiplier * (upper[i] - lower[i]) / 2 / entry_price)
                    tp = entry_price * (1 - params.take_profit_multiplier * (upper[i] - lower[i]) / 2 / entry_price)
                    position = self._open_position(entry_price, timestamp, 'SHORT', sl, tp, capital)
                    entry_bar = i

            equity_curve.append(capital + self._unrealized_pnl(position, close) if position else capital)

        if position is not None:
            bars_held = len(df) - 1 - entry_bar
            exit_price = df['Close'].iloc[-1] * (1 - self.slippage_rate) if position['side'] == 'LONG' else df['Close'].iloc[-1] * (1 + self.slippage_rate)
            trade = self._close_position(position, exit_price, df.index[-1], 'END_OF_DATA', bars_held)
            trades.append(trade)
            capital += trade.pnl
            equity_curve[-1] = capital  # final point includes exit costs

        return self._compute_stats(trades, equity_curve), trades

    def _run_rsi_filter(self, df, upper, middle, lower, params) -> tuple[BacktestStats, list[Trade]]:
        closes = df['Close'].values
        rsi = calculate_rsi(closes, params.rsi_period)
        core = BollingerRSIFilter(params)
        capital = self.initial_capital
        position = None
        entry_bar = 0
        trades = []
        equity_curve = [capital]
        warmup = max(params.bb_timeperiod, params.rsi_period) + 1

        for i in range(warmup, len(df)):
            close = df['Close'].iloc[i]
            high = df['High'].iloc[i]
            low = df['Low'].iloc[i]
            timestamp = df.index[i]

            signal = core.update(close, upper[i], middle[i], lower[i], rsi[i])

            if position is not None:
                trade, pnl = self._check_exit(position, high, low, close, signal, timestamp, entry_bar, i)
                if trade:
                    trades.append(trade)
                    capital += pnl
                    position = None

            if position is None and capital > 0:
                if signal == Signal.BUY:
                    entry_price = close * (1 + self.slippage_rate)
                    sl = entry_price * (1 - params.stop_loss_multiplier * (upper[i] - lower[i]) / 2 / entry_price)
                    tp = entry_price * (1 + params.take_profit_multiplier * (upper[i] - lower[i]) / 2 / entry_price)
                    position = self._open_position(entry_price, timestamp, 'LONG', sl, tp, capital)
                    entry_bar = i
                elif signal == Signal.SELL:
                    entry_price = close * (1 - self.slippage_rate)
                    sl = entry_price * (1 + params.stop_loss_multiplier * (upper[i] - lower[i]) / 2 / entry_price)
                    tp = entry_price * (1 - params.take_profit_multiplier * (upper[i] - lower[i]) / 2 / entry_price)
                    position = self._open_position(entry_price, timestamp, 'SHORT', sl, tp, capital)
                    entry_bar = i

            equity_curve.append(capital + self._unrealized_pnl(position, close) if position else capital)

        if position is not None:
            bars_held = len(df) - 1 - entry_bar
            exit_price = df['Close'].iloc[-1] * (1 - self.slippage_rate) if position['side'] == 'LONG' else df['Close'].iloc[-1] * (1 + self.slippage_rate)
            trade = self._close_position(position, exit_price, df.index[-1], 'END_OF_DATA', bars_held)
            trades.append(trade)
            capital += trade.pnl
            equity_curve[-1] = capital  # final point includes exit costs

        return self._compute_stats(trades, equity_curve), trades

    def _run_squeeze(self, df, upper, middle, lower, bb_widths, params) -> tuple[BacktestStats, list[Trade]]:
        core = BollingerSqueezeFilter(params)
        capital = self.initial_capital
        position = None
        entry_bar = 0
        trades = []
        equity_curve = [capital]
        warmup = params.bb_timeperiod + 50  # Need 50 bars for avg width

        for i in range(warmup, len(df)):
            close = df['Close'].iloc[i]
            high = df['High'].iloc[i]
            low = df['Low'].iloc[i]
            timestamp = df.index[i]

            signal = core.update(close, upper[i], middle[i], lower[i], bb_widths, i)

            if position is not None:
                trade, pnl = self._check_exit(position, high, low, close, signal, timestamp, entry_bar, i)
                if trade:
                    trades.append(trade)
                    capital += pnl
                    position = None

            if position is None and capital > 0:
                if signal == Signal.BUY:
                    entry_price = close * (1 + self.slippage_rate)
                    sl = entry_price * (1 - params.stop_loss_multiplier * (upper[i] - lower[i]) / 2 / entry_price)
                    tp = entry_price * (1 + params.take_profit_multiplier * (upper[i] - lower[i]) / 2 / entry_price)
                    position = self._open_position(entry_price, timestamp, 'LONG', sl, tp, capital)
                    entry_bar = i
                elif signal == Signal.SELL:
                    entry_price = close * (1 - self.slippage_rate)
                    sl = entry_price * (1 + params.stop_loss_multiplier * (upper[i] - lower[i]) / 2 / entry_price)
                    tp = entry_price * (1 - params.take_profit_multiplier * (upper[i] - lower[i]) / 2 / entry_price)
                    position = self._open_position(entry_price, timestamp, 'SHORT', sl, tp, capital)
                    entry_bar = i

            equity_curve.append(capital + self._unrealized_pnl(position, close) if position else capital)

        if position is not None:
            bars_held = len(df) - 1 - entry_bar
            exit_price = df['Close'].iloc[-1] * (1 - self.slippage_rate) if position['side'] == 'LONG' else df['Close'].iloc[-1] * (1 + self.slippage_rate)
            trade = self._close_position(position, exit_price, df.index[-1], 'END_OF_DATA', bars_held)
            trades.append(trade)
            capital += trade.pnl
            equity_curve[-1] = capital  # final point includes exit costs

        return self._compute_stats(trades, equity_curve), trades

    def _run_mean_reversion(self, df, upper, middle, lower, params) -> tuple[BacktestStats, list[Trade]]:
        core = BollingerMeanReversion(params)
        capital = self.initial_capital
        position = None
        entry_bar = 0
        trades = []
        equity_curve = [capital]
        warmup = params.bb_timeperiod + 1

        for i in range(warmup, len(df)):
            close = df['Close'].iloc[i]
            high = df['High'].iloc[i]
            low = df['Low'].iloc[i]
            timestamp = df.index[i]

            side_before = core.position_side
            signal = core.update(close, upper[i], middle[i], lower[i])

            if position is not None:
                trade, pnl = self._check_exit(position, high, low, close, signal, timestamp, entry_bar, i)
                if trade:
                    trades.append(trade)
                    capital += pnl
                    position = None
                    # SL/TP/signal exits all leave the strategy flat.
                    core.position_side = 0

            # An exit signal (emitted while the strategy held a position) must
            # not be reinterpreted as a fresh entry on the same bar.
            entry_allowed = (side_before == 0)
            if position is None and capital > 0 and entry_allowed:
                if signal == Signal.BUY:
                    entry_price = close * (1 + self.slippage_rate)
                    sl = entry_price * (1 - params.stop_loss_multiplier * (upper[i] - lower[i]) / 2 / entry_price)
                    tp = middle[i]  # Mean reversion: TP at middle band
                    position = self._open_position(entry_price, timestamp, 'LONG', sl, tp, capital)
                    entry_bar = i
                elif signal == Signal.SELL:
                    entry_price = close * (1 - self.slippage_rate)
                    sl = entry_price * (1 + params.stop_loss_multiplier * (upper[i] - lower[i]) / 2 / entry_price)
                    tp = middle[i]  # Mean reversion: TP at middle band
                    position = self._open_position(entry_price, timestamp, 'SHORT', sl, tp, capital)
                    entry_bar = i

            if position is None:
                core.position_side = 0  # e.g. entry skipped because capital <= 0

            equity_curve.append(capital + self._unrealized_pnl(position, close) if position else capital)

        if position is not None:
            bars_held = len(df) - 1 - entry_bar
            exit_price = df['Close'].iloc[-1] * (1 - self.slippage_rate) if position['side'] == 'LONG' else df['Close'].iloc[-1] * (1 + self.slippage_rate)
            trade = self._close_position(position, exit_price, df.index[-1], 'END_OF_DATA', bars_held)
            trades.append(trade)
            capital += trade.pnl
            equity_curve[-1] = capital  # final point includes exit costs

        return self._compute_stats(trades, equity_curve), trades

    def _compute_stats(self, trades: list[Trade], equity_curve: list[float]) -> BacktestStats:
        stats = BacktestStats()
        stats.initial_capital = self.initial_capital
        stats.total_trades = len(trades)

        if not trades:
            stats.final_capital = self.initial_capital
            return stats

        stats.final_capital = equity_curve[-1] if equity_curve else self.initial_capital
        # Accounting identity: no open positions and no external flows at the end.
        expected_final = self.initial_capital + sum(t.pnl for t in trades)
        assert abs(stats.final_capital - expected_final) < 1e-6 * max(1.0, abs(expected_final)), (
            f"equity {stats.final_capital} != initial + sum(trade pnl) {expected_final}")
        stats.total_return_pct = (stats.final_capital - self.initial_capital) / self.initial_capital * 100

        peak = equity_curve[0]
        max_dd = 0.0
        max_dd_bars = 0
        current_dd_bars = 0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
                current_dd_bars = 0
            else:
                current_dd_bars += 1
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                max_dd_bars = current_dd_bars
        stats.max_drawdown_pct = max_dd
        stats.max_drawdown_duration_bars = max_dd_bars

        returns = np.diff(equity_curve) / np.array(equity_curve[:-1]) if len(equity_curve) > 1 else []
        returns = [r for r in returns if not np.isnan(r) and np.isfinite(r)]
        if len(returns) > 1 and np.std(returns) > 0:
            ann = np.sqrt(self.bars_per_year)
            stats.sharpe_ratio = np.mean(returns) / np.std(returns) * ann
            neg_returns = [r for r in returns if r < 0]
            if neg_returns and np.std(neg_returns) > 0:
                stats.sortino_ratio = np.mean(returns) / np.std(neg_returns) * ann
        if stats.max_drawdown_pct > 0:
            stats.calmar_ratio = stats.total_return_pct / stats.max_drawdown_pct

        winners = [t for t in trades if t.is_winner]
        losers = [t for t in trades if not t.is_winner]
        stats.winning_trades = len(winners)
        stats.losing_trades = len(losers)
        stats.win_rate = len(winners) / len(trades) * 100 if trades else 0

        long_trades = [t for t in trades if t.side == 'LONG']
        short_trades = [t for t in trades if t.side == 'SHORT']
        stats.long_trades = len(long_trades)
        stats.short_trades = len(short_trades)
        long_winners = [t for t in long_trades if t.is_winner]
        short_winners = [t for t in short_trades if t.is_winner]
        stats.long_win_rate = len(long_winners) / len(long_trades) * 100 if long_trades else 0
        stats.short_win_rate = len(short_winners) / len(short_trades) * 100 if short_trades else 0

        stats.avg_win_pct = np.mean([t.pnl_pct for t in winners]) if winners else 0.0
        stats.avg_loss_pct = np.mean([t.pnl_pct for t in losers]) if losers else 0.0

        total_win = sum(t.pnl for t in winners) if winners else 0
        total_loss = abs(sum(t.pnl for t in losers)) if losers else 0
        stats.profit_factor = total_win / total_loss if total_loss > 0 else float('inf')

        stats.expectancy = np.mean([t.pnl for t in trades])

        durations = []
        for t in trades:
            if t.exit_time and t.entry_time:
                durations.append((t.exit_time - t.entry_time).total_seconds() / 3600)
        stats.avg_trade_duration_hours = np.mean(durations) if durations else 0

        max_consec_wins = 0
        max_consec_losses = 0
        current_consec = 0
        for t in trades:
            if t.is_winner:
                current_consec = current_consec + 1 if current_consec > 0 else 1
                max_consec_wins = max(max_consec_wins, current_consec)
            else:
                current_consec = current_consec - 1 if current_consec < 0 else -1
                max_consec_losses = max(max_consec_losses, abs(current_consec))
        stats.max_consecutive_wins = max_consec_wins
        stats.max_consecutive_losses = max_consec_losses

        stats.total_commission = sum(t.commission for t in trades)
        stats.total_slippage = sum(t.slippage_cost for t in trades)
        stats.total_funding = sum(t.funding_cost for t in trades)

        return stats
