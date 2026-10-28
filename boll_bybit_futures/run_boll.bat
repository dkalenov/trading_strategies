@echo off
set PYTHONUNBUFFERED=1

REM API keys must be set as environment variables before running:
REM   set BYBIT_TESTNET_API_KEY=your_key_here
REM   set BYBIT_TESTNET_API_SECRET=your_secret_here

if "%BYBIT_TESTNET_API_KEY%"=="" (
    echo ERROR: BYBIT_TESTNET_API_KEY not set
    echo Run: set BYBIT_TESTNET_API_KEY=your_key
    exit /b 1
)
if "%BYBIT_TESTNET_API_SECRET%"=="" (
    echo ERROR: BYBIT_TESTNET_API_SECRET not set
    echo Run: set BYBIT_TESTNET_API_SECRET=your_secret
    exit /b 1
)

cd /d "%~dp0"
python live_testnet.py --symbol COREUSDT --interval 1h --variant basic --bb-timeperiod 30 --tp-multiplier 4.0 --sl-multiplier 1.5 --mainnet-data --live --debug > boll.log 2>&1
