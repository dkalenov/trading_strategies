# Bollinger Bands Bybit Futures — PROMPT_RESUME

## Краткое описание проекта
Полная инфраструктура бэктестинга + live trading бот для Bollinger Bands стратегии на Bybit Futures (USDT perpetual). Скопирована архитектура из UTBot_Strategy.

## Рабочая папка
`C:\Users\Dmitrii\Trading strategies\Github\trading-bots-and-strategies\boll_bybit_futures`

## Состояние на конец сессии

### Бэктест — все 4 варианта на BTCUSDT 1h (2024-01 — 2025-06, 13000 свечей)
| Вариант | Return | Sharpe | WR | Trades | MaxDD | PF |
|---------|--------|--------|-----|--------|-------|-----|
| basic | -11.98% | -0.257 | 36.1% | 460 | -27.46% | 0.95 |
| rsi_filter | -7.52% | -0.122 | 36.1% | 441 | -24.74% | 0.97 |
| squeeze | -18.33% | -0.983 | 32.3% | 127 | -19.93% | 0.79 |
| mean_reversion | -99.92% | -20.584 | 22.9% | 1657 | -99.92% | 0.18 |

**BTCUSDT не прибылен ни на одном варианте** (как и с UTBot).

### Batch backtest — 243 символа Bybit USDT perpetual
- **93 прибыльных из 243 (38%)**
- Средняя доходность: -6.07%
- Лучшие по Sharpe (мин 10 сделок):

| Символ | Return | Sharpe | WR | Сделки | MaxDD | PF |
|--------|--------|--------|-----|--------|-------|-----|
| HUMAUSDT | +13.71% | 7.661 | 60.0% | 20 | 3.37% | 2.99 |
| BABYUSDT | +29.94% | 5.470 | 49.2% | 63 | 4.60% | 1.94 |
| BANKUSDT | +12.89% | 4.476 | 48.6% | 37 | 4.73% | 1.72 |
| INITUSDT | +17.31% | 3.714 | 47.5% | 59 | 5.42% | 1.59 |
| 1000TOSHIUSDT | +41.21% | 2.958 | 44.6% | 148 | 9.08% | 1.43 |
| COREUSDT | +136.36% | 2.733 | 40.7% | 432 | 12.13% | 1.39 |
| AEROUSDT | +61.85% | 2.470 | 43.9% | 253 | 12.70% | 1.38 |
| CGPTUSDT | +20.42% | 2.009 | 36.4% | 132 | 10.71% | 1.31 |
| BRETTUSDT | +52.51% | 1.809 | 39.8% | 337 | 16.14% | 1.23 |
| JASMYUSDT | +66.49% | 1.632 | 38.5% | 431 | 17.58% | 1.22 |

- Лучшие по Return: COREUSDT (+136%), JASMYUSDT (+66%), AEROUSDT (+61%), BRETTUSDT (+52%)

### Live trading бот
- `live_testnet.py` — полный live bot для Bybit Futures
- Архитектура: WebSocket (kline stream) + REST API (ордера)
- 4 варианта стратегии, debug mode, dry run
- Запуск: `run_boll.bat` (нужно ввести API ключи)

## Файловая структура (26 файлов)
```
boll_bybit_futures/
├── config.py + config.ini      — Конфигурация
├── strategy.py                 — 4 варианта BB стратегии + индикаторы
├── backtester.py               — Реалистичный бэктестер
├── db.py                       — SQLite база
├── utils.py                    — Bybit V5 API download (кэш + пагинация)
├── bybit.py                    — Bybit API клиент (+ testnet)
├── main.py                     — CLI: бэктест, оптимизация, помесячно
├── batch_fast.py               — Batch бэктест ВСЕХ Bybit perpetual (параллельный)
├── walk_forward.py             — Walk-forward оптимизация
├── monte_carlo.py              — Monte Carlo валидация
├── filter_optimization.py      — Оптимизация фильтров
├── position_sizing.py          — Оптимизация размера позиции
├── dashboard.py                — HTML дашборд
├── live_testnet.py             — LIVE TRADING БОТ (Bybit Futures)
├── run_boll.bat                — Лаунчер для live бота
├── test_edge_cases.py          — Edge-case тесты
├── verify_backtest.py          — Проверка бэктеста
├── PROMPT_RESUME.md            — ЭТОТ ФАЙЛ
├── batch_results.json          — Результаты batch backtest
└── klines/                     — Кэш свечных данных
```

## Исправленные баги
1. **Bybit API interval format**: Bybit V5 требует `"5"` а не `"5m"` — исправлено в `utils.py` (добавлен `BYBIT_INTERVAL_MAP`)
2. **Bybit kline order**: Bybit V5 возвращает свечи от новых к старым — исправлена пагинация (`klines[0]` вместо `klines[-1]`)
3. **RSI off-by-one**: Цикл RSI выходил за границы массива `gains` — исправлено в `strategy.py`
4. **Batch symbols API**: Bybit использует `quoteCoin` а не `quoteCurrency` — исправлено в `batch_fast.py`
5. **Testnet fake data**: COREUSDT testnet возвращает фейковые данные (0.15) — добавлен флаг `--mainnet-data` для использования реальных данных mainnet + testnet для ордеров

## Walk-Forward + Monte Carlo Результаты (2026-07-26)

### Walk-Forward (12m train / 3m test / 3m step, max_evals=150)

| Символ | WF Avg Test Return | WF Sharpe | WF Efficiency | Overfit Ratio | Verdict |
|--------|-------------------|-----------|---------------|---------------|---------|
| **COREUSDT** | +18.05% | 3.343 | 0.808 | 7.29x | ROBUST |
| **AEROUSDT** | +8.52% | 1.733 | 0.322 | 7.30x | OK |
| JASMYUSDT | +1.02% | 0.311 | 0.119 | 66.83x | OVERFITTED |
| BRETTUSDT | -5.62% | -1.048 | -0.348 | -10.73x | FAILED |
| BABYUSDT | N/A | N/A | N/A | N/A | NO DATA (2000 candles) |

### Monte Carlo (10,000 simulations)

| Символ | Permutation Profit% | Sharpe Positive% | Bootstrap 50% Prob | Bootstrap 100% Prob |
|--------|---------------------|------------------|--------------------|--------------------|
| **COREUSDT** | 100% | 100% | 98.3% | 99.9% |
| **AEROUSDT** | 100% | 100% | 94.0% | 98.7% |

### Оптимальные параметры (COREUSDT)
```
bb_timeperiod = 30
bb_nbdevup = 2.0
bb_nbdevdn = 2.0
take_profit_multiplier = 4.0
stop_loss_multiplier = 1.5
```

## Следующие шаги
1. ~~Walk-forward оптимизация~~ — DONE (COREUSDT +18.05% avg test)
2. ~~Monte Carlo валидация~~ — DONE (COREUSDT 100% robust)
3. ~~Оптимизация параметров~~ — DONE (BB(30,2.0), TP=4.0, SL=1.5)
4. ~~Live bot debug mode~~ — DONE (dry-run работает на COREUSDT 1m)
5. **Popolnit testnet** — testnet balance = 0, nuzhno popolnit cherez https://testnet.bybit.com (Faucet)
6. **Vkluchit --live** — posle popolneniya zapustit `python live_testnet.py --symbol COREUSDT --interval 1m --variant basic --bb-timeperiod 30 --tp-multiplier 4.0 --sl-multiplier 1.5 --mainnet-data --live --debug`
7. **Фильтрация символов** — определить лучший набор для портфеля из 93 прибыльных
8. **Сравнение с UTBot** — BB vs UTBot на одних и тех же парах

## Как продолжить
```bash
# Войти в папку проекта
cd "C:\Users\Dmitrii\Trading strategies\Github\trading-bots-and-strategies\boll_bybit_futures"

# Прочитать PROMPT_RESUME.md
# Продолжить с walk-forward на лучших парах
python walk_forward.py --symbol COREUSDT --interval 1h --variant basic

# Или Monte Carlo
python monte_carlo.py --symbol COREUSDT --interval 1h --variant basic

# Или live trading debug mode
set BYBIT_TESTNET_API_KEY=...
set BYBIT_TESTNET_API_SECRET=...
python live_testnet.py --symbol COREUSDT --interval 1h --debug
```

## Конфигурация по умолчанию
```ini
[exchange]
symbol = BTCUSDT
interval = 1h
start_date = 2024-01
end_date = 2025-06

[strategy]
bb_timeperiod = 20
bb_nbdevup = 2.0
bb_nbdevdn = 2.0
take_profit_multiplier = 3.0
stop_loss_multiplier = 1.5

[backtest]
initial_capital = 100000
commission = 0.0004
slippage = 0.0002
leverage = 20
risk_pct = 1.0
funding_rate = 0.0001
```

## Важные замечания
- **Algofactory** — НЕ ТРОГАТЬ, это референс
- **UTBot_Strategy** — рабочий проект UTBot (отдельный)
- **boll_bybit_futures** — рабочий проект BB (этот)
- Данные скачиваются с Bybit V5 API, кэшируются в `klines/`
- Batch backtest запускается параллельно (8 воркеров), ~5 минут на 243 символа
