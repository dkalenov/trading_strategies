"""
Breakout Spot Strategy — Realistic Backtester module.

Event-driven backtester with:
- Position sizing: fixed fraction of capital
- Commission: 0.1% taker (Binance spot)
- Slippage: 0.05%
- Proper PnL with quantity
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from strategy import BreakoutCore, Signal, StrategyParams
from utils import calculate_atr_from_df


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
    exit_reason: str = ''
    stop_loss: float = 0.0
    peak_price: float = 0.0
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
    calmar_ratio: float = 0.0          # annualized return / max drawdown
    return_over_maxdd: float = 0.0     # total return / max drawdown (not annualized)
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


class Backtester:
    def __init__(self, initial_capital: float = 10000,
                 risk_pct: float = 0.02,
                 commission_rate: float = 0.001,
                 slippage_rate: float = 0.0005,
                 bars_per_year: int = 2190):
        self.initial_capital = initial_capital
        self.risk_pct = risk_pct
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.bars_per_year = bars_per_year

    def run(self, df: pd.DataFrame, params: Optional[StrategyParams] = None) -> tuple[BacktestStats, list[Trade]]:
        if params is None:
            params = StrategyParams()

        atr_arr = calculate_atr_from_df(df, params.atr_period)
        return self._run_breakout(df, atr_arr, params)

    def _open_position(self, entry_price, timestamp, sl, capital):
        notional = capital * self.risk_pct / abs(entry_price - sl) * entry_price if abs(entry_price - sl) > 0 else 0
        max_notional = capital  # spot: no leverage
        notional = min(notional, max_notional)
        quantity = notional / entry_price if entry_price > 0 else 0

        slippage_cost = notional * self.slippage_rate
        commission = notional * self.commission_rate

        return {
            'entry_price': entry_price,
            'entry_time': timestamp,
            'stop_loss': sl,
            'quantity': quantity,
            'notional': notional,
            'entry_commission': commission,
            'entry_slippage': slippage_cost,
            'peak_price': entry_price,
            'max_favorable_pct': 0.0,
            'max_adverse_pct': 0.0,
        }

    def _close_position(self, position, exit_price, timestamp, reason, bars_held):
        notional = position['notional']
        quantity = position['quantity']

        exit_notional = exit_price * quantity
        exit_slippage = exit_notional * self.slippage_rate
        exit_commission = exit_notional * self.commission_rate

        gross_pnl = (exit_price - position['entry_price']) * quantity

        total_commission = position['entry_commission'] + exit_commission
        # Slippage is already inside entry_price / exit_price (they are worse than
        # the reference price), so it is reported but not subtracted a second time.
        total_slippage = position['entry_slippage'] + exit_slippage
        total_costs = total_commission

        net_pnl = gross_pnl - total_costs
        pnl_pct = net_pnl / notional * 100 if notional > 0 else 0  # net, consistent with is_winner

        return Trade(
            entry_time=position['entry_time'],
            exit_time=timestamp,
            side='LONG',
            entry_price=position['entry_price'],
            exit_price=exit_price,
            quantity=quantity,
            notional=notional,
            pnl=net_pnl,
            pnl_pct=pnl_pct,
            commission=total_commission,
            slippage_cost=total_slippage,
            exit_reason=reason,
            stop_loss=position['stop_loss'],
            peak_price=position['peak_price'],
            max_favorable_pct=position['max_favorable_pct'],
            max_adverse_pct=position['max_adverse_pct'],
        )

    def _unrealized_pnl(self, position, current_price):
        if position is None:
            return 0.0
        return (current_price - position['entry_price']) * position['quantity']

    def _run_breakout(self, df, atr_arr, params) -> tuple[BacktestStats, list[Trade]]:
        core = BreakoutCore(params)
        capital = self.initial_capital
        position = None
        entry_bar = 0
        trades = []
        equity_curve = [capital]

        # Warm up lookback
        warmup = params.lookback + 1

        for i in range(warmup, len(df)):
            close = df['Close'].iloc[i]
            high = df['High'].iloc[i]
            low = df['Low'].iloc[i]
            volume = df['Volume'].iloc[i] * close  # quote volume approximation
            timestamp = df.index[i]
            atr_val = atr_arr[i]

            if np.isnan(atr_val):
                equity_curve.append(capital + self._unrealized_pnl(position, close) if position else capital)
                continue

            # Track volume in quote terms for consistency
            signal = core.update(high, low, close, volume, atr_val)

            # Check trailing stop on existing position
            if position is not None:
                bars_held = i - entry_bar

                position['max_favorable_pct'] = max(
                    position['max_favorable_pct'],
                    (high - position['entry_price']) / position['entry_price'] * 100
                )
                position['max_adverse_pct'] = max(
                    position['max_adverse_pct'],
                    (position['entry_price'] - low) / position['entry_price'] * 100
                )
                position['peak_price'] = max(position['peak_price'], high)

                exit_price = None
                exit_reason = ''

                # Stop loss hit
                if low <= position['stop_loss']:
                    exit_price = position['stop_loss'] * (1 - self.slippage_rate)
                    exit_reason = 'STOP_LOSS'
                # Trailing stop: check if low touched the trailing stop level
                elif core.position_peak > 0:
                    ts_level = core.position_peak * (1 - params.trailing_stop_pct / 100)
                    if low <= ts_level:
                        exit_price = ts_level * (1 - self.slippage_rate)
                        exit_reason = 'TRAILING_STOP'
                    if high > core.position_peak:
                        core.position_peak = high

                if exit_price is not None:
                    trade = self._close_position(position, exit_price, timestamp, exit_reason, bars_held)
                    trades.append(trade)
                    capital += trade.pnl
                    position = None
                    core.close_position()

            # Open new position on BUY signal
            if position is None and capital > 0 and signal == Signal.BUY:
                entry_price = close * (1 + self.slippage_rate)
                sl = entry_price * (1 - params.stop_loss_multiplier * atr_val / entry_price)
                position = self._open_position(entry_price, timestamp, sl, capital)
                entry_bar = i
                core.open_position(entry_price)

            equity_curve.append(capital + self._unrealized_pnl(position, close) if position else capital)

        # Close any open position at end
        if position is not None:
            bars_held = len(df) - 1 - entry_bar
            exit_price = df['Close'].iloc[-1] * (1 - self.slippage_rate)
            trade = self._close_position(position, exit_price, df.index[-1], 'END_OF_DATA', bars_held)
            trades.append(trade)
            capital += trade.pnl
            equity_curve[-1] = capital  # final equity includes the closing costs

        return self._compute_stats(trades, equity_curve), trades

    def _compute_stats(self, trades: list[Trade], equity_curve: list[float]) -> BacktestStats:
        stats = BacktestStats()
        stats.initial_capital = self.initial_capital
        stats.total_trades = len(trades)

        if not trades:
            stats.final_capital = self.initial_capital
            return stats

        stats.final_capital = equity_curve[-1] if equity_curve else self.initial_capital
        stats.total_return_pct = (stats.final_capital - self.initial_capital) / self.initial_capital * 100

        # Drawdown
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

        # Sharpe ratio
        returns = np.diff(equity_curve) / np.array(equity_curve[:-1]) if len(equity_curve) > 1 else []
        returns = [r for r in returns if not np.isnan(r) and np.isfinite(r)]
        if len(returns) > 1 and np.std(returns) > 0:
            ann = np.sqrt(self.bars_per_year)
            stats.sharpe_ratio = np.mean(returns) / np.std(returns) * ann
            # Standard downside deviation: RMS of min(r, 0) over ALL returns.
            downside = np.sqrt(np.mean(np.minimum(np.array(returns), 0.0) ** 2))
            if downside > 0:
                stats.sortino_ratio = np.mean(returns) / downside * ann
        if stats.max_drawdown_pct > 0:
            stats.return_over_maxdd = stats.total_return_pct / stats.max_drawdown_pct
            years = len(equity_curve) / self.bars_per_year
            if years > 0 and stats.final_capital > 0:
                ann_return_pct = ((stats.final_capital / self.initial_capital) ** (1 / years) - 1) * 100
                stats.calmar_ratio = ann_return_pct / stats.max_drawdown_pct

        # Win/loss
        winners = [t for t in trades if t.is_winner]
        losers = [t for t in trades if not t.is_winner]
        stats.winning_trades = len(winners)
        stats.losing_trades = len(losers)
        stats.win_rate = len(winners) / len(trades) * 100

        # Averages
        stats.avg_win_pct = np.mean([t.pnl_pct for t in winners]) if winners else 0.0
        stats.avg_loss_pct = np.mean([t.pnl_pct for t in losers]) if losers else 0.0

        total_win = sum(t.pnl for t in winners) if winners else 0
        total_loss = abs(sum(t.pnl for t in losers)) if losers else 0
        stats.profit_factor = total_win / total_loss if total_loss > 0 else float('inf')

        stats.expectancy = np.mean([t.pnl for t in trades])

        # Duration
        durations = []
        for t in trades:
            if t.exit_time and t.entry_time:
                durations.append((t.exit_time - t.entry_time).total_seconds() / 3600)
        stats.avg_trade_duration_hours = np.mean(durations) if durations else 0

        # Consecutive wins/losses
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

        return stats
