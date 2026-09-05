# SMT strategy evaluation — 2026-09-05

## Scope and safety boundary

This evaluation used read-only OHLC data from SMT's dedicated terminal only:
`C:\Program Files\Vantage Markets MT5 Terminal\terminal64.exe` on the
`VantageMarkets-Demo` server.  It did not submit, modify, cancel, or close an
order.  The separate `D:\MT5IntelliTrade\terminal64.exe` installation was not
opened or queried.

The test window was the most recent 180 days available from that terminal.  The
custom-strategy candidates were also split into two independent 90-day periods.
Backtests are research evidence, not a promise of profitability.  They omit
commission, fill slippage, swap, and latency; current quoted spreads were
BTCUSD 17.08, ETHUSD 2.46, and XAUUSD+ 0.15 price units, so marginal results
must be treated as failing the deployment gate.

## Results and routing decision

| Asset | Strategy | 180-day result | Two-window check | Decision |
| --- | --- | --- | --- | --- |
| BTC | BTC Dual-TF Momentum | PF 0.66; -5,316.29 points; 20 resolved trades | Not applicable | Disabled: materially negative before costs. |
| BTC | BTC BOS Trend (`custom_1781159562203_2`) | PF 1.50; 106 trades | PF 1.22 / 1.89 over 53 / 51 trades | Retained for DEMO monitoring only. |
| BTC | EMA20 Pullback | No valid historical sample from its live-only scan | Not applicable | Disabled: no cost-aware, reproducible evidence. |
| ETH | ETH Dual-TF Momentum | PF 1.20; 27 trades; +141.22 points | Not applicable | Asset paused: sample is below 30 and edge is too small after costs. |
| Gold | ATR Trailing | PF 0.98; -63.96 points; 147 trades | Not applicable | Disabled: negative before costs. |
| Gold | H4 Break-and-Retest | PF 0.99; -6.96 points; 15 trades | Not applicable | Disabled: negative and under-sampled. |
| Gold | MACD Trend RR3 (`custom_1781159562198_1`) | PF 1.52; 118 trades | PF 1.70 / 1.33 over 59 / 58 trades | Retained for DEMO monitoring only. |
| Gold | EMA Rider (`custom_1781159562213_4`) | PF 1.38; 88 trades | PF 1.32 / 1.50 over 43 / 44 trades | Retained for DEMO monitoring only. |

## Active configuration after the review

- BTC: `custom_1781159562203_2` only.
- ETH: disabled at the asset switch, so the engine cannot fall back to an
  unreviewed ICT strategy.
- Gold: `custom_1781159562198_1` and `custom_1781159562213_4` only.

This is a risk reduction, not a claim that the retained strategies will make a
profit.  Keep routing in DEMO/PAPER mode.  Re-evaluate each retained strategy
after at least 30 reconciled, cost-inclusive demo exits and disable it if the
cost-inclusive profit factor falls below 1.20 or its drawdown breaches its
approved risk limit.
