import hashlib
import hmac
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from urllib.parse import urlencode
from bot.exchange import BinanceFuturesClient


def test_signature_is_deterministic_and_matches_independent_hmac():
    client = BinanceFuturesClient("fake-key", "fake-secret", testnet=True)
    params = {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": 0.01,
              "timestamp": 1700000000000, "recvWindow": 5000}

    sig1 = client._sign(params)
    sig2 = client._sign(params)
    assert sig1 == sig2, "signing must be deterministic for the same params"

    expected = hmac.new(b"fake-secret", urlencode(params, doseq=True).encode(),
                         hashlib.sha256).hexdigest()
    assert sig1 == expected

    assert len(sig1) == 64
    int(sig1, 16)  # raises if it isn't valid hex


def test_signature_changes_when_params_change():
    client = BinanceFuturesClient("k", "s", testnet=True)
    base = {"symbol": "BTCUSDT", "quantity": 0.01, "timestamp": 1700000000000}
    sig_a = client._sign(base)
    sig_b = client._sign({**base, "quantity": 0.02})
    assert sig_a != sig_b


def test_testnet_and_mainnet_urls_differ():
    testnet_client = BinanceFuturesClient("k", "s", testnet=True)
    mainnet_client = BinanceFuturesClient("k", "s", testnet=False)
    assert "testnet" in testnet_client.base_url
    assert "testnet" not in mainnet_client.base_url
    assert testnet_client.base_url != mainnet_client.base_url
