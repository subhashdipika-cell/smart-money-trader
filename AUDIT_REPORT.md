# Smart Money Trader — Full Project Audit
**Date:** 2026-06-10 · Scope: all frontend pages + all backend strategies + API wiring

> ## ✅ Update: all issues below were FIXED on 2026-06-10
>
> Files changed: `App.jsx`, `MT5Panel.jsx`, `GoldDashboard.jsx`, `StrategyTester.jsx`,
> `PnLDashboard.jsx`, `live_signal_service.py`, `gold_mt5_history.py`, `choch_backtest.py`,
> `momentum_scalper.py`, `atr_trailing_strategy.py`, `htf_signal_generator.py`,
> `smc_swing_strategy.py`, `trading_executor.py`.
>
> **Two things to do after pulling these changes:**
> 1. **Clear the MT5 data cache** (Tester page → "🗑 Clear Cache" button). Old cache
>    files still contain the uncorrected broker-time timestamps; the next backtest
>    will re-fetch with the fix applied.
> 2. **Restart the backend** so the resolver and executor changes take effect.
>
> Note: backtest win rates (especially CHoCH strategies) will look LOWER than before.
> That's not a regression — the old numbers were inflated by trades that never filled.
> The new numbers are the honest ones.

## What's working correctly ✅

- **API wiring:** Every endpoint the frontend calls exists in the backend (`/pnl/summary`, `/learning/stats`, `/sentiment`, `/mt4/*`, `/prices`, `/strategy-tester/*`, `/backtest/strategy`, `/signals/*`, `/scalper/*`, `/gold/*`). No broken routes.
- **SL/TP direction math:** Checked every strategy (FRVP, London Breakout, M5 Mean Reversion, H4 Break-Retest, SMC Swing, ETH/BTC Momentum, ATR Trailing, CHoCH, scalper, HTF ICT). No inverted buy/sell logic anywhere.
- **Position sizing:** SignalCard risk-based sizing and Gold `_pnl()` lot math (1 lot = 100 oz) are correct.
- **Live order placement:** `trading_executor.py` uses correct BUY_LIMIT/SELL_LIMIT types, native MT5 expiry, duplicate guards, and magic numbers.
- **ETH/BTC Momentum backtest:** the cleanest strategy — daily macro trend is shifted 1 day to avoid lookahead. Well done.
- **PnL Dashboard, Learning, Sentiment tabs:** display logic is sound.

---

## Critical bugs 🔴

### 1. `setAllSignals` is not defined — Account form throws an error
`App.jsx` line 437 calls `setAllSignals([])` but no such state exists. Every time the setup form is submitted, a ReferenceError is thrown and the lines after it (`setSentSignals([])`, `setLastUpdated(null)`, `setError("")`) never run.
**Fix:** delete the `setAllSignals([])` line.

### 2. Dashboard "Live signals" panel is always empty on fresh load
The dashboard and the header "Open" counter read `sentSignals`, but `/signals/sent` is only fetched **when the History tab is opened**. Until you visit History once, the dashboard shows 0 signals.
**Fix:** fetch `/signals/sent` on the dashboard tab too (or on an interval regardless of tab).

### 3. CHoCH backtests count trades that never filled (inflated win rates)
`choch_backtest.py`: entry is a *limit order* below/above price, but `_simulate_forward()` starts checking TP/SL immediately without verifying price ever returned to the entry. Price often runs straight to "TP" without the order ever filling → fake wins. Affects all 6 CHoCH tester strategies (scalp/intraday/swing × BTC/ETH).
**Fix:** in the simulation, require price to touch `entry` first; only then track SL/TP.

### 4. Signal resolver records real losses as "EXPIRED" (0 points)
`live_signal_service.py` Rule 3 expires any OPEN signal once price moves 1.5% against it — **without checking whether the trade had already entered**. An entered BUY that fell through its SL gets logged as EXPIRED/0 pts instead of LOSS. This inflates win rate, History stats, and feeds wrong data to the learning engine.
**Fix:** apply Rule 3 only when `entry_hit` is false (i.e., still awaiting entry).

### 5. MT5 candle timestamps mislabeled as UTC — session strategies trade the wrong hours
`gold_mt5_history.py` line 170 treats MT5 bar times as UTC, but MT5 brokers (incl. Vantage) run server time at UTC+2/+3. So in backtests, the "Asian box 00:00–08:00 GMT", "London 08:00–11:00", and "NY 13:00–16:00" windows are shifted by 2–3 hours. Affects London Breakout, M5 Mean Reversion, and FRVP's 16:00 IST logic whenever MT5 data is used.
**Fix:** subtract the broker's UTC offset before localizing (or detect offset by comparing the latest bar time to actual UTC now).

---

## Moderate issues 🟡

### 6. History ⇄ MT5 Trader desync (from earlier review, still open)
- MT5 panel fetches `/mt4/trades` (current mode only) but shows Demo/Live/Paper filter tabs — filtering data it never fetched. Should fetch `?mode=all`.
- History fetches once per visit, never refreshes (`historyRefreshKey` is dead code).
- MT5Panel, GoldDashboard, and StrategyTester hardcode `http://127.0.0.1:8000` instead of the shared `API_URLS` config.

### 7. History filter buttons don't re-render
Filters are stored in `window._hf` / `window._mt5af` and "refreshed" via `setActiveTab(TABS.history)` — setting state to its current value, which React ignores. Clicks appear dead until the 15-second price poll forces a render. Convert to real `useState` in a proper component.

### 8. Local MT5 trades have no numeric timestamp
`trading_executor.py` saves trades with `time_ist` only. History sorts by `timestamp` → these trades sink to the bottom regardless of recency, and the Monthly Performance table's month-grouping can skip them.
**Fix:** save `"timestamp": int(time.time()*1000)` when logging each trade.

### 9. Scalper backtest inaccuracies (momentum_scalper.py)
- Entry uses next bar's **close** while the comment says "next open" — uses information from a bar that hasn't finished.
- The entry bar's full high/low is then checked against SL/TP, though part of that bar happened before entry.
- Profitable TIMEOUT exits are counted as LOSS in the win-rate.
- Backtest min-stop (0.0005) doesn't match live min-stop (50 points).

### 10. ATR Trailing backtest: same-bar trail-then-stop
The stop is raised using the current bar's high, then the same bar's low is checked against the raised stop. In reality the low may have come first. Slightly optimistic results. Also `profit_factor` shows 0 (instead of ∞) when there are no losses.

### 11. HTF ICT generator identifies assets by price, not symbol
`htf_signal_generator.py`: `is_btc = price > 30000`, `is_gold = 3000 < price < 10000`. If ETH trades above $3,000 it's treated as Gold and gets Gold's SL band (5–30 pts instead of 15–35). The function already receives `symbol` — use it.

### 12. SMC Swing: mild lookahead in liquidity levels
Fractal swings need 5 *future* bars to confirm, then are forward-filled from the swing bar — so sweeps can be detected against levels that weren't yet confirmable in real time.

### 13. Resolver fill rule disagrees with real MT5 fills
The resolver only counts an entry as filled if a candle *closes* beyond it; a real MT5 limit order fills on touch. Signal outcomes in History can therefore disagree with actual MT5 trade results for the same setup.

---

## Minor issues ⚪

- `PnLDashboard` 30/60/90/180-day filter buttons set state that is never used — they do nothing.
- `learningLoading` / `sentimentLoading` are never set to `true`, so loading messages never show.
- Monthly table: months created only from MT5 trades get a raw `"2026-06"` label (uses `toMonthKey` instead of `toMonthLabel`); the "Current" badge marks the newest month even if it's an old one.
- `NiftyChart.jsx` is dead code (never imported).
- CHoCH docs say "one trade per day per direction" — code allows one per day total; Scalping style uses H4 bias though docs say H1.
- Timeout/EOD trades and "CLOSED" rows are handled slightly differently between History and MT5 pages (cosmetic).

## Suggested fix order
1. #1 and #2 (quick App.jsx fixes, immediately visible)
2. #6 + #7 + #8 (makes History/MT5 consistent and trustworthy)
3. #4 (stops corrupting win-rate/learning data going forward)
4. #3, #5, #9, #10, #11 (backtest accuracy — affects which strategies you choose to deploy)
