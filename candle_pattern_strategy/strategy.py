"""
Candlestick Pattern Strategy — Pattern detection + signal generation.

Detects 11 candlestick patterns with weighted scoring and ATR-based filtering.
"""

import numpy as np
import pandas as pd


# Pattern weights for signal strength scoring
DEFAULT_WEIGHTS = {
    'bullish_engulfing': 1.2,
    'bearish_engulfing': 1.2,
    'piercing_line': 1.0,
    'dark_cloud': 1.0,
    'hammer': 0.9,
    'inverted_hammer': 0.9,
    'morning_star': 1.3,
    'evening_star': 1.3,
    'three_white_soldiers': 1.4,
    'three_black_crows': 1.4,
    'harami_cross': 0.8,
}


def detect_patterns(df, atr_period=14, doji_th=0.15,
                    hammer_lower_mult=2.0, hammer_upper_mult=0.3,
                    small_body_factor=0.4, min_body_atr=0.15,
                    vol_ma_period=20, pattern_weights=None):
    """
    Detect candlestick patterns on OHLCV data.

    Returns DataFrame with columns:
        Pattern, Signal (1=bull, -1=bear, 0=none), Signal_strength, ATR
    """
    if pattern_weights is None:
        pattern_weights = DEFAULT_WEIGHTS

    df = df.copy()

    # Normalize column names
    colmap = {}
    for name in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if name in df.columns:
            colmap[name] = name
        elif name.lower() in df.columns:
            colmap[name] = name.lower()
        else:
            colmap[name] = None

    if None in [colmap['Open'], colmap['High'], colmap['Low'], colmap['Close']]:
        raise ValueError("DataFrame must contain Open/High/Low/Close")

    O = df[colmap['Open']].astype(float)
    H = df[colmap['High']].astype(float)
    L = df[colmap['Low']].astype(float)
    C = df[colmap['Close']].astype(float)
    V = df[colmap['Volume']] if colmap.get('Volume') else None

    prev_O = O.shift(1)
    prev_H = H.shift(1)
    prev_L = L.shift(1)
    prev_C = C.shift(1)
    pre_prev_O = O.shift(2)
    pre_prev_C = C.shift(2)

    # ATR
    tr = pd.concat([
        (H - L).abs(),
        (H - prev_C).abs(),
        (L - prev_C).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(atr_period, min_periods=1).mean()

    # Body and shadows
    body = C - O
    body_abs = body.abs()
    upper_shadow = H - np.maximum(O, C)
    lower_shadow = np.minimum(O, C) - L
    rng = (H - L).replace(0, np.nan)
    body_pct_of_range = body_abs / rng

    # Volume MA
    vol_ma = V.rolling(vol_ma_period, min_periods=1).mean() if V is not None else None

    min_body = min_body_atr * atr

    # Doji
    is_doji = (body_abs <= doji_th * rng) | (rng == 0)

    # Hammer
    hammer = (
        (lower_shadow >= hammer_lower_mult * body_abs) &
        (upper_shadow <= hammer_upper_mult * body_abs) &
        (body_pct_of_range <= 0.5) &
        (body_abs >= min_body)
    )

    # Inverted Hammer
    inverted_hammer = (
        (upper_shadow >= hammer_lower_mult * body_abs) &
        (lower_shadow <= hammer_upper_mult * body_abs) &
        (body_pct_of_range <= 0.5) &
        (body_abs >= min_body)
    )

    # Engulfing
    prev_body_abs = (prev_C - prev_O).abs()
    bullish_engulfing = (
        (prev_C < prev_O) &
        (C > O) &
        (O <= prev_C) & (C >= prev_O) &
        (body_abs > prev_body_abs) &
        (body_abs >= min_body)
    )

    bearish_engulfing = (
        (prev_C > prev_O) &
        (C < O) &
        (O >= prev_C) & (C <= prev_O) &
        (body_abs > prev_body_abs) &
        (body_abs >= min_body)
    )

    # Piercing / Dark Cloud
    prev_mid = (prev_O + prev_C) / 2.0
    piercing = (
        (prev_C < prev_O) &
        (O < prev_L) &
        (C > prev_mid) &
        (C < prev_O) &
        (body_abs >= min_body)
    )

    dark_cloud = (
        (prev_C > prev_O) &
        (O > prev_H) &
        (C < prev_mid) &
        (C > prev_O) &
        (body_abs >= min_body)
    )

    # Morning / Evening Star
    pre_prev_body_abs = (pre_prev_C - pre_prev_O).abs()
    morning_star = (
        (pre_prev_C < pre_prev_O) &
        (prev_body_abs <= small_body_factor * pre_prev_body_abs) &
        (C > O) &
        (C > (pre_prev_O + pre_prev_C) / 2.0) &
        (body_abs >= min_body)
    )

    evening_star = (
        (pre_prev_C > pre_prev_O) &
        (prev_body_abs <= small_body_factor * pre_prev_body_abs) &
        (C < O) &
        (C < (pre_prev_O + pre_prev_C) / 2.0) &
        (body_abs >= min_body)
    )

    # Three White Soldiers / Three Black Crows
    o0, o1, o2 = O.shift(2), O.shift(1), O
    c0, c1, c2 = C.shift(2), C.shift(1), C

    tws = (
        (c0 > o0) & (c1 > o1) & (c2 > o2) &
        (c2 > c1) & (c1 > c0) &
        (o1 > o0) & (o2 > o1) &
        ((c0 - o0) >= min_body) &
        ((c1 - o1) >= min_body) &
        ((c2 - o2) >= min_body)
    )

    tbc = (
        (c0 < o0) & (c1 < o1) & (c2 < o2) &
        (c2 < c1) & (c1 < c0) &
        (o1 < o0) & (o2 < o1) &
        ((o0 - c0) >= min_body) &
        ((o1 - c1) >= min_body) &
        ((o2 - c2) >= min_body)
    )

    # Harami Cross
    prev_body_top = np.maximum(prev_O, prev_C)
    prev_body_bot = np.minimum(prev_O, prev_C)
    harami_cross = (
        is_doji &
        (H < prev_body_top) &
        (L > prev_body_bot) &
        (prev_body_abs >= 0.5 * rng.shift(1).fillna(0))
    )

    # Build signal series
    pattern_names = [
        'bullish_engulfing', 'bearish_engulfing', 'piercing_line', 'dark_cloud',
        'hammer', 'inverted_hammer', 'morning_star', 'evening_star',
        'three_white_soldiers', 'three_black_crows', 'harami_cross'
    ]
    pattern_masks = [
        bullish_engulfing, bearish_engulfing, piercing, dark_cloud,
        hammer, inverted_hammer, morning_star, evening_star,
        tws, tbc, harami_cross
    ]
    # NOTE: harami_cross direction is intentionally 0 (non-directional). A harami
    # cross is a reversal-context pattern whose bias depends on the preceding
    # trend, which this function does not know. As coded, it is detected and
    # labeled but NEVER produces a BUY/SELL signal (signal stays 0) or a trade.
    # It is effectively diagnostic-only in the current implementation.
    pattern_directions = [1, -1, 1, -1, 1, -1, 1, -1, 1, -1, 0]

    signal = pd.Series(0, index=df.index, dtype=int)
    strength = pd.Series(0.0, index=df.index)
    pattern_name = pd.Series('', index=df.index)

    for name, mask, direction in zip(pattern_names, pattern_masks, pattern_directions):
        weight = pattern_weights.get(name, 1.0)
        hits = mask & (signal == 0)
        signal.loc[hits] = direction
        strength.loc[hits] = weight
        pattern_name.loc[hits] = name

    # Boost strength if volume > MA
    if vol_ma is not None:
        vol_boost = (V > vol_ma * 1.2).astype(float) * 0.3
        strength = strength + vol_boost

    df['Pattern'] = pattern_name
    df['Signal'] = signal
    df['Signal_strength'] = strength
    df['ATR'] = atr

    return df


def apply_filters(df, ema_fast=50, ema_slow=200, min_strength=0.0,
                  min_atr_pct=0.0, patterns_only=None, use_trend_filter=True):
    """
    Apply trend and quality filters to signals.

    Args:
        df: DataFrame with Signal, Signal_strength, ATR, Close columns
        ema_fast: Fast EMA period for trend
        ema_slow: Slow EMA period for trend
        min_strength: Min signal strength to keep
        min_atr_pct: Min ATR as % of price (filter low volatility)
        patterns_only: List of pattern names to keep (None = all)
        use_trend_filter: apply the EMA trend filter. Each filter is independent:
            turning the trend filter off leaves strength / ATR% / pattern filters active.

    Returns: filtered DataFrame
    """
    df = df.copy()

    # EMA trend filter
    # adjust=False uses the standard recursive EMA formula (matches TradingView /
    # exchange charts). pandas' default (adjust=True) weights early bars
    # differently and can diverge by ~0.3% of price for several hundred bars
    # after warmup, which is enough to occasionally flip the trend classification.
    df['EMA_fast'] = df['Close'].ewm(span=ema_fast, adjust=False).mean()
    df['EMA_slow'] = df['Close'].ewm(span=ema_slow, adjust=False).mean()

    # Only trade with trend
    bull_trend = (df['Close'] > df['EMA_fast']) & (df['EMA_fast'] > df['EMA_slow'])
    bear_trend = (df['Close'] < df['EMA_fast']) & (df['EMA_fast'] < df['EMA_slow'])

    if use_trend_filter:
        df.loc[(df['Signal'] == 1) & ~bull_trend, 'Signal'] = 0
        df.loc[(df['Signal'] == -1) & ~bear_trend, 'Signal'] = 0

    # Min strength filter
    if min_strength > 0:
        df.loc[df['Signal_strength'] < min_strength, 'Signal'] = 0

    # Min ATR filter
    if min_atr_pct > 0:
        df['ATR_pct'] = df['ATR'] / df['Close'] * 100
        df.loc[df['ATR_pct'] < min_atr_pct, 'Signal'] = 0

    # Pattern filter
    if patterns_only is not None:
        df.loc[~df['Pattern'].isin(patterns_only), 'Signal'] = 0

    return df
