import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

from coint_pairs.stats import calculate_cointegration, calculate_half_life, calculate_pair_beta


def test_cointegration_accepts_numpy_arrays():
    """Regression test for the original bug: calculate_cointegration used to
    crash with AttributeError when given plain numpy arrays (only worked
    with pandas Series), silently returning hedge=nan every time.

    Cointegration is a log-space relationship, so the synthetic pair is
    built in log-space: log(y) = hedge_true * log(x) + mean-reverting
    noise, then exponentiated back to raw prices before being passed in
    (calculate_cointegration takes raw prices and logs internally).
    """
    rng = np.random.default_rng(0)
    n = 400
    log_x = np.cumsum(rng.normal(scale=0.02, size=n)) + np.log(100)
    hedge_true = 0.8
    ou_noise = np.zeros(n)
    phi = 0.85
    for i in range(1, n):
        ou_noise[i] = phi * ou_noise[i - 1] + rng.normal(scale=0.03)
    log_y = hedge_true * log_x + ou_noise
    x, y = np.exp(log_x), np.exp(log_y)

    flag, hedge, hl, pval = calculate_cointegration(x, y)
    assert not np.isnan(hedge), "hedge should not be nan for numpy array input"
    assert flag == 1, "a strongly cointegrated pair should be flagged"
    # OLS on a cointegrating regression is consistent but can carry visible
    # finite-sample bias, especially with a persistent regressor - this is
    # a sanity range, not a precision claim.
    assert 0.4 < hedge < 1.3


def test_cointegration_rejects_independent_walks():
    rng = np.random.default_rng(1)
    x = np.cumsum(rng.normal(size=300)) + 100
    y = np.cumsum(rng.normal(size=300)) + 50
    flag, hedge, hl, pval = calculate_cointegration(x, y)
    assert flag == 0, "two independent random walks should not usually be flagged as cointegrated"


def test_no_double_log():
    """The original worker script fed already log-transformed prices into
    a function that logs its input again. Sanity check here: feeding raw
    prices should recover the true hedge ratio; feeding pre-logged prices
    into the same function (so it gets double-logged) should recover
    something meaningfully different, since log(log(price)) is not the
    same relationship as log(price).
    """
    rng = np.random.default_rng(2)
    n = 400
    log_x = np.cumsum(rng.normal(scale=0.02, size=n)) + np.log(100)
    hedge_true = 0.8
    ou_noise = np.zeros(n)
    phi = 0.85
    for i in range(1, n):
        ou_noise[i] = phi * ou_noise[i - 1] + rng.normal(scale=0.03)
    log_y = hedge_true * log_x + ou_noise
    x, y = np.exp(log_x), np.exp(log_y)

    _, hedge_raw, _, _ = calculate_cointegration(x, y)
    _, hedge_double_logged, _, _ = calculate_cointegration(np.log(x), np.log(y))
    assert abs(hedge_raw - hedge_true) < 0.4, "hedge from raw prices should roughly recover the true hedge"
    assert abs(hedge_raw - hedge_double_logged) > 0.1, (
        "hedge from raw prices and from double-logged prices should differ substantially - "
        "if they don't, the double-log bug may have crept back in"
    )


def test_half_life_mean_reverting_series():
    rng = np.random.default_rng(3)
    n = 500
    spread = np.zeros(n)
    phi = 0.9  # half-life = -ln(2)/ln(0.9) ~= 6.58 bars
    for i in range(1, n):
        spread[i] = phi * spread[i - 1] + rng.normal(scale=1.0)
    hl = calculate_half_life(spread)
    assert 3 < hl < 12


def test_pair_beta_matches_ols_slope():
    rng = np.random.default_rng(4)
    market = rng.normal(size=1000)
    pair = 0.3 * market + rng.normal(scale=0.1, size=1000)
    beta = calculate_pair_beta(pair, market)
    assert 0.2 < beta < 0.4
