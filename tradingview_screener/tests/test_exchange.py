import hashlib
import hmac
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from urllib.parse import urlencode
from exchange.binance.client import Client
from exchange.binance.futures import Futures


def test_signature_is_deterministic_and_matches_independent_hmac():
    client = Client("fake-key", "fake-secret", testnet=True)
    params = {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": 0.01,
              "timestamp": 1700000000000, "recvWindow": 5000}

    sig1 = client._sign(params)
    sig2 = client._sign(params)
    assert sig1 == sig2

    expected = hmac.new(b"fake-secret", urlencode(params, doseq=True).encode(),
                         hashlib.sha256).hexdigest()
    assert sig1 == expected
    assert len(sig1) == 64
    int(sig1, 16)


def test_signature_changes_when_params_change():
    client = Client("k", "s", testnet=True)
    base = {"symbol": "BTCUSDT", "quantity": 0.01, "timestamp": 1700000000000}
    sig_a = client._sign(base)
    sig_b = client._sign({**base, "quantity": 0.02})
    assert sig_a != sig_b


def test_testnet_and_mainnet_urls_differ():
    testnet_client = Client("k", "s", testnet=True)
    mainnet_client = Client("k", "s", testnet=False)
    assert "testnet" in testnet_client.base_url
    assert "testnet" not in mainnet_client.base_url


def test_futures_inherits_client_signing():
    # Futures(Client) - the futures-specific methods must not need to
    # re-implement signing.
    futures = Futures("k", "s", testnet=True)
    assert hasattr(futures, "_sign")
    assert hasattr(futures, "new_algo_stop_market_order")
    assert hasattr(futures, "new_limit_order")
