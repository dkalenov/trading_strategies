import requests, hmac, hashlib, urllib.parse, time, os

key = os.environ.get("BINANCE_API_KEY", "")
secret = os.environ.get("BINANCE_API_SECRET", "")

if not key or not secret:
    print("ERROR: BINANCE_API_KEY / BINANCE_API_SECRET not set")
    exit(1)

ts = int(time.time() * 1000)
params = {"timestamp": ts}
query = urllib.parse.urlencode(params)
sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
params["signature"] = sig
resp = requests.get(
    "https://testnet.binance.vision/api/v3/account",
    params=params,
    headers={"X-MBX-APIKEY": key},
    timeout=10,
)
if resp.status_code == 200:
    for b in resp.json()["balances"]:
        if float(b["free"]) > 0:
            print(f"  {b['asset']}: {b['free']}")
else:
    print(f"  Error {resp.status_code}: {resp.text[:200]}")
