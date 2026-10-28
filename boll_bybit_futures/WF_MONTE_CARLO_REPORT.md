# Bollinger Bands — Walk-Forward + Monte Carlo Results

## Дата анализа: 2026-07-26

## Walk-Forward Optimization (12m train / 3m test / 3m step)

### COREUSDT — ROBUST
- **Avg test return**: +18.05%
- **Avg test Sharpe**: 3.343
- **WF efficiency**: 0.808 (test_sharpe/train_sharpe)
- **Overfit ratio**: 7.29x
- **Profitable windows**: 2/2 (100%)
- **Optimal params**: BB(30, 2.0), TP=4.0, SL=1.5
- **Compound return**: +39.30%

### AEROUSDT — OK
- **Avg test return**: +8.52%
- **Avg test Sharpe**: 1.733
- **WF efficiency**: 0.322
- **Overfit ratio**: 7.30x
- **Profitable windows**: 1/2 (50%)
- **Optimal params**: BB(30, 3.0) or BB(20, 2.5)
- **Compound return**: +17.04%

### JASMYUSDT — OVERFITTED
- **Avg test return**: +1.02%
- **Avg test Sharpe**: 0.311
- **WF efficiency**: 0.119
- **Overfit ratio**: 66.83x
- **Profitable windows**: 1/2 (50%)

### BRETTUSDT — FAILED
- **Avg test return**: -5.62%
- **Avg test Sharpe**: -1.048
- **WF efficiency**: -0.348
- **Profitable windows**: 0/2 (0%)

### BABYUSDT — NO DATA
- Only 2000 candles (from 2025-04-03), insufficient for 12m train windows

---

## Monte Carlo Validation (10,000 simulations)

### COREUSDT — ROBUST
- Permutation test: 100% profitable
- Sharpe stable: 100% positive
- Bootstrap 50%: 98.3% prob profitable
- Bootstrap 100%: 99.9% prob profitable
- Original: +136.36%, Sharpe 2.733, MaxDD 12.13%

### AEROUSDT — ROBUST
- Permutation test: 100% profitable
- Sharpe stable: 100% positive
- Bootstrap 50%: 94.0% prob profitable
- Bootstrap 100%: 98.7% prob profitable
- Original: +61.85%, Sharpe 2.470, MaxDD 12.70%

---

## Выводы

1. **COREUSDT** — лучший кандидат для live trading
   - Стабильные параметры BB(30, 2.0) с TP=4.0, SL=1.5
   - WF efficiency 0.808 — хорошая генерализация
   - Monte Carlo подтверждает робастность

2. **AEROUSDT** — второй кандидат
   - Худшая стабильность параметров (2 разных оптимальных набора)
   - WF efficiency 0.322 — ниже порога 0.5
   - Monte Carlo подтверждает робастность

3. **Оптимальные параметры для COREUSDT**:
   - bb_timeperiod = 30
   - bb_nbdevup = 2.0
   - bb_nbdevdn = 2.0
   - take_profit_multiplier = 4.0
   - stop_loss_multiplier = 1.5

4. **Рекомендация**: Запускать live trading на COREUSDT с вышеуказанными параметрами

---

## Следующие шаги
1. Live trading debug mode на тестнете с COREUSDT
2. Тест на других таймфреймах (4h, 1h)
3. Портфельная диверсификация (CORE + AERO)
