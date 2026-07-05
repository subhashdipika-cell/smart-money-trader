import { useState, useEffect } from "react"
import LiveStrategySelector from "../LiveStrategySelector"

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

const C = {
  bg:"#0d1117", card:"#161b22", border:"#1c2128",
  gold:"#f0b429", green:"#69db7c", red:"#f85149",
  blue:"#4dabf7", muted:"#8b949e", text:"#e6edf3"
}

// ── Strategy catalogue ────────────────────────────────────────────────────────
// Each strategy tagged with one or more styles so the style dropdown filters them.
const ALL_STRATEGIES = [

  // ── GOLD / FRVP ───────────────────────────────────────────────────────────
  {
    id:              "gold_frvp_liquidity_trap",
    label:           "🏆 FRVP Liquidity Trap — Gold",
    style:           ["Intraday"],
    desc:            "Fixed Range Volume Profile (VAH/VAL) computed 00:00–16:00 IST. After 16:00 IST, enters on false breakouts above VAH (SHORT) or below VAL (LONG). Max 2 trades/day. Real 5-min Gold data from Yahoo Finance.",
    symbol:          "XAUUSD",
    backendStrategy: "gold_frvp_liquidity_trap",
    strategyTag:     "gold_frvp_liquidity_trap",
    isFrvp:          true,
    note:            "Uses MT5 local history (no day cap, offline). Falls back to Yahoo Finance if MT5 is closed. SL = $8–10 recommended. RR 1:1.5 (funded), 1:2 (retail), 1:3 (experienced).",
  },

  // ── GOLD — London Session Breakout (best-performing Gold strategy) ──────────
  {
    id:              "london_breakout",
    label:           "🌍 London Session Breakout — Gold",
    style:           ["Intraday"],
    desc:            "Marks the Asian session range (00:00–08:00 GMT) then enters on a breakout candle during London open (08:00–11:00 GMT). Volume > 5-bar MA required — rejects fake sweeps. ATR × 1.5 SL, max 1 trade/day.",
    symbol:          "XAUUSD",
    backendStrategy: "london_breakout",
    strategyTag:     "london_breakout",
    isAdvancedGold:  true,
    note:            "Best window: 30–90 days. Try RR 1:1.5, 1:2, 1:3 to find the sweet spot. Also test 30m timeframe for fewer but cleaner signals. Also testable and deployable from the Gold page.",
  },

  // Note: M5 Mean Reversion and H4 Break-Retest are Gold-only and live
  // exclusively on the Gold page (Strategy Lab tab).

  // ── SMC / ICT SWING — BTC & ETH ──────────────────────────────────────────
  {
    id:              "smc_swing_btc",
    label:           "🎯 SMC Liquidity Sweep — BTC",
    style:           ["Swing"],
    desc:            "ICT/SMC multi-timeframe swing. 4H identifies liquidity sweeps (Judas swings). 1H confirms with Market Structure Shift + Fair Value Gap. Partial TP: 50% at 1:2 RR, 50% at target. Tight LTF stop = high RR potential.",
    symbol:          "BTCUSD",
    backendStrategy: "smc_swing",
    strategyTag:     "SMC_Swing",
    isAdvancedGold:  true,
    note:            "Best window: 60–90 days. Try RR 2.5, 3.0, 4.0. Effective avg RR = (2 + target) ÷ 2. Data via yfinance — no MT5 needed.",
  },
  {
    id:              "smc_swing_eth",
    label:           "🎯 SMC Liquidity Sweep — ETH",
    style:           ["Swing"],
    desc:            "ICT/SMC multi-timeframe swing. 4H liquidity sweep + 1H MSS + FVG execution. ETH typically shows cleaner sweeps than BTC. Partial TP: 50% at 1:2, 50% at target RR.",
    symbol:          "ETHUSD",
    backendStrategy: "smc_swing",
    strategyTag:     "SMC_Swing",
    isAdvancedGold:  true,
    note:            "Best window: 60–90 days. Tighter stops on ETH than BTC — check avg_loss vs avg_win ratio. Data via yfinance.",
  },

  // ── SCALPING ─────────────────────────────────────────────────────────────
  {
    id:              "momentum_scalp_btc",
    label:           "🤖 M1 Momentum Scalper — BTC",
    style:           ["Scalping"],
    desc:            "BB(20,2σ) + RSI-3 extreme breakout on M1 candles. Market order, auto-closes in 5 min. Dual-layer duplicate protection. Magic: 202609.",
    symbol:          "BTCUSD",
    backendStrategy: "momentum_scalp",
    strategyTag:     "BB_RSI_Scalper",
    isScalper:       true,
    note:            "Live engine runs in background. RR field = TP multiplier vs SL. All positions close at 5 minutes automatically.",
  },
  {
    id:              "momentum_scalp_eth",
    label:           "🤖 M1 Momentum Scalper — ETH",
    style:           ["Scalping"],
    desc:            "BB(20,2σ) + RSI-3 extreme breakout on M1 candles. Market order, auto-closes in 5 min.",
    symbol:          "ETHUSD",
    backendStrategy: "momentum_scalp",
    strategyTag:     "BB_RSI_Scalper",
    isScalper:       true,
    note:            "Live engine runs in background. All positions force-close at 5 minutes.",
  },
  {
    id:              "momentum_scalp_gold",
    label:           "🤖 M1 Momentum Scalper — Gold",
    style:           ["Scalping"],
    desc:            "BB(20,2σ) + RSI-3 extreme breakout on M1 candles. Market order, auto-closes in 5 min.",
    symbol:          "XAUUSD+",
    backendStrategy: "momentum_scalp",
    strategyTag:     "BB_RSI_Scalper",
    isScalper:       true,
    note:            "Live engine runs in background. All positions force-close at 5 minutes.",
  },
  {
    id:      "choch_scalp_btc",
    label:   "🔄 SMC CHoCH Scalp — BTC",
    style:   ["Scalping"],
    desc:    "M15 CHoCH with H4 equilibrium filter. Entry at FVG top, SL below sweep wick. 1:2 RR.",
    symbol:  "BTCUSDT",
    backendStrategy: "choch_scalp",
  },
  {
    id:      "choch_scalp_eth",
    label:   "🔄 SMC CHoCH Scalp — ETH",
    style:   ["Scalping"],
    desc:    "M15 CHoCH with H4 equilibrium filter. Entry at FVG top, SL below sweep wick. 1:2 RR.",
    symbol:  "ETHUSDT",
    backendStrategy: "choch_scalp",
  },

  // ── INTRADAY ──────────────────────────────────────────────────────────────
  {
    id:              "htf_ict_intraday_btc",
    label:           "⚡ HTF ICT Intraday — BTC",
    style:           ["Intraday"],
    desc:            "The live production strategy for BTC. 1H Fair Value Gaps + 20 EMA regime filter. Entry at FVG top/bottom with EMA confluence. Same logic generating your live BTC signals.",
    symbol:          "BTCUSDT",
    backendStrategy: "htf_ict_intraday_btc",
    strategyTag:     "HTF_ICT_Intraday",
    note:            "This is the exact strategy running live. Backtest lets you verify performance on historical data.",
  },
  {
    id:              "htf_ict_intraday_eth",
    label:           "⚡ HTF ICT Intraday — ETH",
    style:           ["Intraday"],
    desc:            "The live production strategy for ETH. 1H Fair Value Gaps + 20 EMA regime filter. Entry at FVG top/bottom with EMA confluence. Same logic generating your live ETH signals.",
    symbol:          "ETHUSDT",
    backendStrategy: "htf_ict_intraday_eth",
    strategyTag:     "HTF_ICT_Intraday",
    note:            "This is the exact strategy running live. Backtest lets you verify performance on historical data.",
  },
  {
    id:             "atr_trailing_btc",
    label:          "🎯 ATR Chandelier Trailing Stop — BTC",
    style:          ["Intraday", "Swing"],
    desc:           "Rides trends with no fixed TP. Trails SL at 2.5×ATR from peak. Skips sideways markets automatically. Best for strong trending moves.",
    symbol:         "BTCUSDT",
    backendStrategy:"atr_trailing",
    strategyTag:    "ATR_Trailing",
    rrLabel:        "ATR Multiplier",  // repurpose RR field as ATR mult
    note:           "RR field = ATR multiplier (2.5 recommended). Higher = more room, smaller gains locked. Regime gate blocks sideways markets.",
  },
  {
    id:             "atr_trailing_eth",
    label:          "🎯 ATR Chandelier Trailing Stop — ETH",
    style:          ["Intraday", "Swing"],
    desc:           "Rides trends with no fixed TP. Trails SL at 2.5×ATR from peak. Skips sideways markets automatically. Best for strong trending moves.",
    symbol:         "ETHUSDT",
    backendStrategy:"atr_trailing",
    strategyTag:    "ATR_Trailing",
    rrLabel:        "ATR Multiplier",
    note:           "RR field = ATR multiplier (2.5 recommended). Regime gate blocks sideways markets automatically.",
  },
  {
    id:      "ict_signals_btc",
    label:   "📐 ICT Signals — BTC",
    style:   ["Intraday"],
    desc:    "ICT confluence engine: FVG + Order Block + BOS on M15–H1 Binance data.",
    symbol:  "BTCUSDT",
    backendStrategy: "ict_signals",
  },
  {
    id:      "ict_signals_eth",
    label:   "📐 ICT Signals — ETH",
    style:   ["Intraday"],
    desc:    "ICT confluence engine: FVG + Order Block + BOS on M15–H1 Binance data.",
    symbol:  "ETHUSDT",
    backendStrategy: "ict_signals",
  },
  {
    id:      "choch_intraday_btc",
    label:   "🔄 SMC CHoCH Intraday — BTC",
    style:   ["Intraday"],
    desc:    "M15 execution, H4 bias. CHoCH + FVG convergence. SL below swing low. 1:2 or 1:3 RR.",
    symbol:  "BTCUSDT",
    backendStrategy: "choch_intraday",
  },
  {
    id:      "choch_intraday_eth",
    label:   "🔄 SMC CHoCH Intraday — ETH",
    style:   ["Intraday"],
    desc:    "M15 execution, H4 bias. CHoCH + FVG convergence. SL below swing low. 1:2 or 1:3 RR.",
    symbol:  "ETHUSDT",
    backendStrategy: "choch_intraday",
  },

  // ── SWING ─────────────────────────────────────────────────────────────────
  {
    id:      "choch_swing_btc",
    label:   "🔄 SMC CHoCH Swing — BTC",
    style:   ["Swing"],
    desc:    "H1 execution, Daily bias. Strong structural CHoCH with FVG. Wide SL, scale out 50% at 1:2, trail rest to 1:3.",
    symbol:  "BTCUSDT",
    backendStrategy: "choch_swing",
  },
  {
    id:      "choch_swing_eth",
    label:   "🔄 SMC CHoCH Swing — ETH",
    style:   ["Swing"],
    desc:    "H1 execution, Daily bias. Strong structural CHoCH with FVG. Wide SL, scale out 50% at 1:2, trail rest to 1:3.",
    symbol:  "ETHUSDT",
    backendStrategy: "choch_swing",
  },

  // ── GOLD HTF LIQUIDITY SWEEP (Judas Swing) ────────────────────────────────
  {
    id:              "htf_liquidity_sweep",
    label:           "🏛️ Gold HTF Liquidity Sweep",
    style:           ["Swing"],
    desc:            "Daily BSL/SSL Judas Swing strategy. Marks Previous Day High/Low on 1m bars, waits for a sweep above PDH (Judas spike) then enters SHORT below the spike. Sweeps below PDL trigger LONG above the swing. SL beyond the sweep extreme. Daily breaker halts direction after 2 consecutive SL hits.",
    symbol:          "XAUUSD",
    backendStrategy: "htf_liquidity_sweep",
    strategyTag:     "HTF_Sweep",
    isAdvancedGold:  true,
    slLabel:         "Max Risk (pts)",
    rrLabel:         "RR Ratio",
    note:            "SL field = max risk in Gold points (default 4.0). RR field = reward-to-risk ratio (default 5.0). Best over 14–30d with 1m execution data.",
  },

  // ── BTC MOMENTUM (Dual-Timeframe) ─────────────────────────────────────────
  {
    id:              "btc_momentum",
    label:           "₿ BTC Dual-TF Momentum",
    style:           ["Swing"],
    desc:            "Same dual-timeframe momentum strategy as ETH — applied to Bitcoin. Daily 50/200 EMA sets macro regime (bull/bear). 4H MACD(12,26,9) crossover triggers entry only when RSI(14) is in the acceleration zone (45–65 long, 35–55 short). SL at recent 4H swing extreme. Trailing exit on MACD cross-back — no fixed TP.",
    symbol:          "BTCUSDT",
    backendStrategy: "btc_momentum",
    strategyTag:     "BTC_Momentum",
    isAdvancedGold:  true,
    rrLabel:         "RSI High (50–90)",
    note:            "RR field = RSI upper bound (default 65). SL field = RSI lower bound (default 45). Days = backtest window (180d recommended for Daily EMA warmup).",
  },

  // ── ETH MOMENTUM (Dual-Timeframe) ─────────────────────────────────────────
  {
    id:              "eth_momentum",
    label:           "⚡ ETH Dual-TF Momentum",
    style:           ["Swing"],
    desc:            "Daily 50/200 EMA macro filter sets direction (bull/bear). 4H MACD(12,26,9) crossover triggers entry only when RSI(14) is in the acceleration zone (45–65 long, 35–55 short). SL at recent 4H swing extreme. Trailing exit on MACD cross-back — no fixed TP. Designed to ride ETH trends while filtering chop.",
    symbol:          "ETHUSD",
    backendStrategy: "eth_momentum",
    strategyTag:     "ETH_Momentum",
    isAdvancedGold:  true,   // uses FRVP-style normaliser
    rrLabel:         "RSI High (50–90)",
    note:            "RR field = RSI upper bound (default 65). Lower = tighter momentum filter, fewer but cleaner entries. Days = backtest window (180d recommended for Daily EMA warmup).",
  },

  // ── OLIVER VELEZ SWING — BTC ──────────────────────────────────────────────
  {
    id:              "oliver_velez_btc",
    label:           "₿ Oliver Velez Swing — BTC",
    style:           ["Swing"],
    desc:            "Oliver Velez swing rules on Bitcoin daily bars. Elephant Bars, Bottoming/Double Tails, and Color Changes at the rising 20 MA or flat 200 MA. BTC-tuned: Elephant multiplier 1.5× (crypto-sized candles), 2% MA tolerance, 3-bar partial exit for faster crypto moves. Runner trails the 8 MA with Big Bar jam and bar-by-bar trailing.",
    symbol:          "BTCUSDT",
    backendStrategy: "oliver_velez_btc",
    strategyTag:     "OV_BTC",
    isAdvancedGold:  true,
    slLabel:         "Elephant Mult (1.0–3.0)",
    rrLabel:         "Bar Count (2–6)",
    note:            "SL field = Elephant Bar multiplier (default 1.5 for BTC). RR field = new-high bars before partial exit (default 3 — faster for crypto). Uses 365 days minimum for 200 MA warmup.",
  },

  // ── OLIVER VELEZ SWING (Stocks) ────────────────────────────────────────────
  {
    id:              "oliver_velez_swing",
    label:           "📈 Oliver Velez Swing",
    style:           ["Swing"],
    desc:            "Classic 2–10 day stock swing strategy. Identifies institutional Elephant Bars, Bottoming/Double Tails, and Bullish Color Changes at the rising 20 MA or flat 200 MA. Split position: half exits after 3–5 new-high bars; runner trails the 8 MA with Big Bar jam and bar-by-bar trailing. Works on any stock ticker (AAPL, MSFT, SPY, NVDA, etc.).",
    symbol:          "AAPL",
    backendStrategy: "oliver_velez_swing",
    strategyTag:     "OV_Swing",
    isAdvancedGold:  true,   // uses the same FRVP-style result normaliser
    slLabel:         "Elephant Mult (1.5–3.0)",
    rrLabel:         "Bar Count (3–5)",
    note:            "SL field = Elephant Bar multiplier (default 2.0 — body must be 2× average). RR field = new-high bars before partial exit (default 4). Symbol = any Yahoo Finance ticker. Use at least 365 days for the 200 MA warmup.",
  },

  // ── 9 & 20 EMA Pullback (Stock Burner) ───────────────────────────────────
  {
    id:              "ema_9_20_btc",
    label:           "📊 9/20 EMA Pullback — BTC",
    style:           ["Intraday", "Swing"],
    desc:            "Stock Burner strategy. 9 EMA above/below 20 EMA defines trend direction. Price pulls back to either EMA; entry on a rejection candle (green + close > prior close for longs; red + close near absolute low for shorts). Stop at 3-bar swing low/high. Minimum 1:3 R:R. Ranging market skipped automatically.",
    symbol:          "BTCUSDT",
    backendStrategy: "ema_9_20",
    strategyTag:     "EMA_9_20_Pullback",
    note:            "Best window: 60–90 days. Use RR 3.0 (strategy minimum). 4H sweet spot — try with multiple day windows to build sample size. Two-Strike Rule: skip asset after 2 consecutive stops.",
  },
  {
    id:              "ema_9_20_eth",
    label:           "📊 9/20 EMA Pullback — ETH",
    style:           ["Intraday", "Swing"],
    desc:            "Same 9/20 EMA Pullback logic applied to ETH. ETH often gives cleaner pullbacks than BTC due to lower liquidity. Entry on rejection candle off 9 or 20 EMA. Stop at 3-bar swing low/high. Min 1:3 R:R.",
    symbol:          "ETHUSDT",
    backendStrategy: "ema_9_20",
    strategyTag:     "EMA_9_20_Pullback",
    note:            "Compare ETH results vs BTC — ETH typically shows higher signal frequency but similar win rate. Best window: 60–90 days. Use RR 3.0.",
  },
  {
    id:              "golden_setup_xau",
    label:           "🔢 Golden Setup — XAU/USD",
    style:           ["Intraday", "Swing"],
    desc:            "Power of Stocks (Subasish) round-number breakout on Gold. Entry when close crosses above/below a 50-point round level aligned with structural trend (HH/HL or LH/LL). Stop 5 pts beyond the round level. Min 1:5 R:R. Active windows: US open (13:00–15:00 UTC) and morning volatility (02:00–04:00 UTC).",
    symbol:          "XAUUSD",
    backendStrategy: "golden_setup",
    strategyTag:     "Golden_Setup",
    note:            "Best window 60–90 days. Use RR 5.0 minimum; strategy targets 1:10–1:20. 70% partial exit at 1:3 R is the core mechanic — note the 'tp_partial' field in trade details.",
  },
  {
    id:              "golden_setup_btc",
    label:           "🔢 Golden Setup — BTC",
    style:           ["Intraday", "Swing"],
    desc:            "Power of Stocks round-number breakout on BTC. Entry at 1000-point round level crossovers aligned with structural trend. Stop 300 pts beyond the round level. Min 1:5 R:R. Active windows: US open (13:00–15:00 UTC) and morning volatility (02:00–04:00 UTC).",
    symbol:          "BTCUSDT",
    backendStrategy: "golden_setup",
    strategyTag:     "Golden_Setup",
    note:            "Best window 60–90 days. Use RR 5.0 minimum. BTC round numbers (97000, 98000, etc.) act as magnets — combine with Golden Setup for confluence.",
  },

  // ── ADAPTIVE S/R PRO ──────────────────────────────────────────────────────
  {
    id:              "adaptive_sr_gbpaud",
    label:           "📐 Adaptive S/R Pro — GBP/AUD",
    style:           ["Intraday", "Swing"],
    desc:            "Hybrid CMO/HMA + RSI + pivot engine that auto-identifies institutional supply/demand zones. Fast HMA-12 momentum shift confirms the level. RSI-9 < 25 gates BUY signals (oversold structural support); RSI-9 > 75 gates SELL signals (overbought resistance). One signal per fresh S/R level — no label clutter. SL: ATR × 1.5. TP: 1:2.5 R:R.",
    symbol:          "GBPAUD",
    backendStrategy: "adaptive_sr_gbpaud",
    strategyTag:     "Adaptive_SR",
    isAdvancedGold:  true,
    slLabel:         "ATR Multiplier (0.5–3.0)",
    note:            "SL field = ATR multiplier (default 1.5). Best window: 60 days. Use RR 2.5 — strict RSI 25/75 thresholds keep signal count low but quality high. Try GBP/AUD 1H for clearest S/R structure.",
  },
  {
    id:              "adaptive_sr_xauusd",
    label:           "📐 Adaptive S/R Pro — XAU/USD",
    style:           ["Intraday", "Swing"],
    desc:            "Adaptive S/R engine on Gold 1H. HMA momentum shift from a key pivot confirms institutional activity. RSI-9 thresholds 25/75 filter mid-range noise. ATR-based SL gives Gold's volatility breathing room. Strict one-signal-per-level rule eliminates overlapping signals.",
    symbol:          "XAUUSD",
    backendStrategy: "adaptive_sr_xauusd",
    strategyTag:     "Adaptive_SR",
    isAdvancedGold:  true,
    slLabel:         "ATR Multiplier (0.5–3.0)",
    note:            "SL field = ATR multiplier (default 1.5). Best window: 60 days. Use RR 2.5. Gold's intraday volatility benefits from ATR-scaled stops over fixed point stops.",
  },
  {
    id:              "adaptive_sr_btcusdt",
    label:           "📐 Adaptive S/R Pro — BTC",
    style:           ["Intraday", "Swing"],
    desc:            "Adaptive S/R applied to BTC 1H. CMO via HMA catches institutional momentum shifts at structural highs/lows. RSI-9 < 25 / > 75 gates ensure only high-conviction S/R bounces are traded. One signal per fresh level prevents over-trading.",
    symbol:          "BTCUSDT",
    backendStrategy: "adaptive_sr_btcusdt",
    strategyTag:     "Adaptive_SR",
    isAdvancedGold:  true,
    slLabel:         "ATR Multiplier (0.5–3.0)",
    note:            "SL field = ATR multiplier (default 1.5). Best window: 60 days. Use RR 2.5. BTC's wider swings mean ATR-based stops are essential — fixed stops will over-trigger on BTC.",
  },
]

const STYLES       = ["Scalping", "Intraday", "Swing"]
const RR_OPTIONS   = [1.0, 1.5, 2.0, 2.5, 3.0, 5.0]
const DAYS_OPTIONS = [30, 60, 90]
const FRVP_DAYS_OPTIONS = [14, 30, 60, 90, 180]  // MT5 has no cap — use full local history
const SL_OPTIONS   = [8.0, 9.0, 10.0]

// Default RR per style
const DEFAULT_RR = { Scalping: 2.0, Intraday: 2.0, Swing: 3.0 }

// Style descriptions
const STYLE_DESC = {
  Scalping: "Tight SL · Quick entries · M5–M15 timeframe · 1:1–1:2 RR",
  Intraday: "Medium SL · Same-day close · M15–H1 timeframe · 1:2–1:3 RR",
  Swing:    "Wide SL · Multi-day holds · H1–H4 timeframe · 1:3+ RR",
}

function StatCard({ label, value, sub, color }) {
  return (
    <div style={{ background:C.bg, border:`1px solid ${C.border}`, borderRadius:8, padding:"14px 16px" }}>
      <div style={{ fontSize:10, color:C.muted, textTransform:"uppercase", letterSpacing:".5px", marginBottom:6 }}>{label}</div>
      <div style={{ fontSize:22, fontWeight:800, color: color||C.text }}>{value ?? "—"}</div>
      {sub && <div style={{ fontSize:11, color:C.muted, marginTop:4 }}>{sub}</div>}
    </div>
  )
}

function ClearCacheButton() {
  const [state, setState] = useState("idle")  // "idle" | "clearing" | "done" | "error"
  const [msg,   setMsg]   = useState("")

  const handleClear = async () => {
    if (state === "clearing") return
    setState("clearing")
    setMsg("")
    try {
      const res  = await fetch(`${API}/gold/cache`, { method: "DELETE" })
      const data = await res.json()
      if (res.ok) {
        const freed = data.freed_kb > 0 ? `${data.freed_kb} KB freed` : "cache was empty"
        setMsg(`✅ ${data.deleted_files} file(s) deleted — ${freed}`)
        setState("done")
      } else {
        setMsg(`❌ ${data.detail || "Failed"}`)
        setState("error")
      }
    } catch (e) {
      setMsg(`❌ ${e.message}`)
      setState("error")
    }
    setTimeout(() => { setState("idle"); setMsg("") }, 4000)
  }

  return (
    <div style={{ display:"flex", flexDirection:"column", gap:4 }}>
      <button onClick={handleClear} disabled={state === "clearing"} title="Delete cached MT5 history files to free disk space. Next backtest re-fetches from MT5." style={{
        flex:"0 0 auto", padding:"9px 14px", borderRadius:8, border:`1px solid ${C.border}`,
        background:"#0d1117", color: state === "clearing" ? C.muted : C.muted,
        fontSize:13, fontWeight:600, cursor: state === "clearing" ? "not-allowed" : "pointer",
        whiteSpace:"nowrap", transition:"all .2s"
      }}>
        {state === "clearing" ? "🗑 Clearing…" : "🗑 Clear Cache"}
      </button>
      {msg && (
        <div style={{ fontSize:11, color: state === "error" ? C.red : C.green, maxWidth:180, lineHeight:1.3 }}>
          {msg}
        </div>
      )}
    </div>
  )
}

function OutcomeBadge({ outcome }) {
  const map = {
    WIN:       { bg:"rgba(105,219,124,.15)", color:C.green },
    LOSS:      { bg:"rgba(248,81,73,.15)",   color:C.red   },
    BE:        { bg:"rgba(204,93,232,.15)",  color:"#cc5de8"},
    EOD_CLOSE: { bg:"rgba(77,171,247,.15)",  color:C.blue  },
    OPEN:      { bg:"rgba(240,180,41,.12)",  color:C.gold  },
  }
  const s = map[outcome] || { bg:"rgba(139,148,158,.1)", color:C.muted }
  return (
    <span style={{ ...s, padding:"2px 8px", borderRadius:4, fontSize:11, fontWeight:700 }}>
      {outcome || "—"}
    </span>
  )
}

// Lightweight SVG equity line
function EquityLine({ trades, pipKey = "pnl_pips" }) {
  if (!trades || !trades.length) return null
  let cum = 0
  const pts = trades.map((t, i) => {
    cum += parseFloat(t[pipKey] || t.points || 0)
    return { x: i, y: cum }
  })
  const w = 560, h = 100, pad = 8
  const minY = Math.min(0, ...pts.map(p => p.y))
  const maxY = Math.max(0, ...pts.map(p => p.y))
  const ry   = maxY - minY || 1
  const sx   = i => pad + (i / (pts.length - 1 || 1)) * (w - pad * 2)
  const sy   = y => pad + ((maxY - y) / ry) * (h - pad * 2)
  const d    = pts.map((p, i) => `${i===0?"M":"L"} ${sx(i)} ${sy(p.y)}`).join(" ")
  const zY   = sy(0)
  const col  = cum >= 0 ? C.green : C.red
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display:"block" }}>
      <line x1={pad} y1={zY} x2={w-pad} y2={zY} stroke={C.border} strokeWidth={1} strokeDasharray="4 3"/>
      <path d={d} fill="none" stroke={col} strokeWidth={2} strokeLinejoin="round"/>
      <path d={`${d} L ${sx(pts.length-1)} ${zY} L ${pad} ${zY} Z`}
        fill={col} fillOpacity={.1}/>
    </svg>
  )
}

export default function StrategyTester() {
  const [style,      setStyle]      = useState("Scalping")
  const [stratId,    setStratId]    = useState("choch_scalp_btc")

  // ── Custom strategies from the Builder page (refreshed on every mount) ────
  const [builderStrats, setBuilderStrats] = useState([])
  useEffect(() => {
    fetch(`${API}/strategy-builder/list`)
      .then(r => r.json())
      .then(d => setBuilderStrats((d.strategies || []).map(({ definition: def }) => ({
        id:        def.id,
        label:     `🛠 ${def.name}`,
        style:     ["Scalping", "Intraday", "Swing"],   // visible in every tab
        desc:      `Custom Builder strategy — ${def.asset} · ${def.timeframe} · ` +
                   `${(def.conditions || []).length} entry condition(s), ` +
                   `${(def.exit_conditions || []).length} exit rule(s). Edit it on the Builder page.`,
        symbol:    ({ BTC: "BTCUSDT", ETH: "ETHUSDT", Gold: "XAUUSD" })[def.asset] || "XAUUSD",
        isBuilder: true,
        note:      "Runs with the strategy's saved conditions, exits and risk settings — only the Period field applies here. RR/SL fields are ignored.",
      }))))
      .catch(() => setBuilderStrats([]))
  }, [])

  // ── Configured trading lots (for $ conversion of point-based results) ─────
  const [lotSizes, setLotSizes] = useState({})
  useEffect(() => {
    fetch(`${API}/mt4/config`)
      .then(r => r.json())
      .then(cfg => setLotSizes(cfg.lot_sizes || {}))
      .catch(() => {})
  }, [])
  const [days,       setDays]       = useState(60)
  const [rr,         setRr]         = useState(2.0)
  const [slDistance, setSlDistance] = useState(8.0)   // FRVP = $ amount; Adaptive SR = ATR multiplier
  const [running,    setRunning]    = useState(false)
  const [results,    setResults]    = useState(null)
  const [error,      setError]      = useState("")

  // ── Scalper live engine state ─────────────────────────────────────────────
  const [scalperStatus,   setScalperStatus]   = useState(null)     // null | status object
  const [scalperLoading,  setScalperLoading]  = useState(false)
  const [scalperMsg,      setScalperMsg]      = useState("")

  const fetchScalperStatus = () => {
    fetch(`${API}/scalper/status`)
      .then(r => r.json())
      .then(s => setScalperStatus(s))
      .catch(() => {})
  }

  const startScalper = async () => {
    setScalperLoading(true); setScalperMsg("")
    // Collect all selected scalper symbols from the catalogue
    const scalperSymbols = ALL_STRATEGIES
      .filter(s => s.isScalper)
      .map(s => s.symbol)
      .filter((v, i, a) => a.indexOf(v) === i)
      .join(",")
    try {
      const res  = await fetch(`${API}/scalper/start?symbols=${encodeURIComponent(scalperSymbols)}&timeframe=M1`, { method:"POST" })
      const data = await res.json()
      if (data.success) {
        setScalperMsg(`✅ Scalper started on: ${data.symbols?.join(", ")}`)
        fetchScalperStatus()
      } else {
        setScalperMsg("❌ " + (data.error || "Start failed"))
      }
    } catch(e) { setScalperMsg("❌ " + e.message) }
    setScalperLoading(false)
  }

  const stopScalper = async () => {
    setScalperLoading(true); setScalperMsg("")
    try {
      const res  = await fetch(`${API}/scalper/stop`, { method:"POST" })
      const data = await res.json()
      if (data.success) { setScalperMsg("🛑 Scalper stopped."); setScalperStatus(prev => prev ? {...prev, running:false} : null) }
      else setScalperMsg("❌ " + (data.error || "Stop failed"))
    } catch(e) { setScalperMsg("❌ " + e.message) }
    setScalperLoading(false)
  }

  // Poll scalper status when a scalper strategy is selected
  useEffect(() => {
    const selected = ALL_STRATEGIES.find(s => s.id === stratId)
    if (!selected?.isScalper) return
    fetchScalperStatus()
    const id = setInterval(fetchScalperStatus, 10000)
    return () => clearInterval(id)
  }, [stratId])

  // ── Deploy state ────────────────────────────────────────────────────────────
  const [deployMode,      setDeployMode]      = useState("demo")   // paper | demo | live
  const [activeStrategies, setActiveStrategies] = useState({})      // { paper: [], demo: [], live: [] }
  const [deployMsg,       setDeployMsg]       = useState("")
  const [deploying,       setDeploying]       = useState(false)

  // Load active strategies from backend on mount
  useEffect(() => {
    fetch(`${API}/strategy/config`)
      .then(r => r.json())
      .then(cfg => {
        setActiveStrategies({
          paper: cfg.active_strategies_paper || cfg.active_strategies || [],
          demo:  cfg.active_strategies_demo  || cfg.active_strategies || [],
          live:  cfg.active_strategies_live  || [],
        })
      })
      .catch(() => {})
  }, [])

  const isDeployed = (tag) => (activeStrategies[deployMode] || []).includes(tag)

  const toggleDeploy = async () => {
    const tag = strat?.strategyTag
    if (!tag) { setDeployMsg("⚠️ This strategy cannot be deployed from tester yet."); return }
    setDeploying(true)
    setDeployMsg("")
    const deployed = isDeployed(tag)
    const method   = deployed ? "DELETE" : "POST"
    try {
      const res = await fetch(
        `${API}/strategy/activate?strategy_id=${tag}&mode=${deployMode}`,
        { method }
      )
      const data = await res.json()
      if (data.success) {
        setActiveStrategies(prev => ({ ...prev, [deployMode]: data.active }))
        setDeployMsg(deployed
          ? `✅ ${tag} removed from ${deployMode} trading.`
          : `✅ ${tag} is now active for ${deployMode} trading.`)
      } else {
        setDeployMsg("❌ " + (data.error || "Deploy failed"))
      }
    } catch(e) {
      setDeployMsg("❌ " + e.message)
    } finally {
      setDeploying(false)
    }
  }

  // ── Hidden strategies (localStorage-backed) ──────────────────────────────
  const [hiddenIds, setHiddenIds] = useState(() => {
    try { return JSON.parse(localStorage.getItem("smt_hidden_strategies") || "[]") }
    catch { return [] }
  })

  // Built-in catalogue + custom Builder strategies
  const CATALOGUE = [...ALL_STRATEGIES, ...builderStrats]

  const hideStrategy = (id) => {
    if (!window.confirm(`Remove "${CATALOGUE.find(s=>s.id===id)?.label || id}" from the Tester?\n\nYou can restore it later via the "Restore" link.`)) return
    const next = [...hiddenIds, id]
    setHiddenIds(next)
    localStorage.setItem("smt_hidden_strategies", JSON.stringify(next))
    setResults(null)
    setError("")
    // Auto-select the next visible strategy in the same style
    const visible = CATALOGUE.filter(s => s.style.includes(style) && !next.includes(s.id))
    if (visible.length) setStratId(visible[0].id)
  }

  const restoreAll = () => {
    setHiddenIds([])
    localStorage.removeItem("smt_hidden_strategies")
  }

  // Strategies for the selected style, minus hidden ones
  const filteredStrats = CATALOGUE.filter(s => s.style.includes(style) && !hiddenIds.includes(s.id))
  const strat = CATALOGUE.find(s => s.id === stratId && !hiddenIds.includes(s.id))
    || filteredStrats[0]
    || CATALOGUE[0]

  // When style changes, auto-select the first strategy of that style
  const handleStyleChange = (newStyle) => {
    setStyle(newStyle)
    setRr(DEFAULT_RR[newStyle] || 2.0)
    const first = ALL_STRATEGIES.find(s => s.style.includes(newStyle))
    if (first) {
      setStratId(first.id)
      // Reset days to a valid value for the new strategy
      if (first.isFrvp) setDays(30)
      else if (days < 30) setDays(30)
    }
    setResults(null)
    setError("")
  }

  const run = async () => {
    if (!strat) return
    setRunning(true); setError(""); setResults(null)
    try {
      // ── Custom Builder strategies: run through the tester endpoint ─────────
      if (strat.isBuilder) {
        const params = new URLSearchParams({ strategy_id: strat.id, days })
        const res = await fetch(`${API}/strategy-tester/run?${params}`,
          { method: "POST", signal: AbortSignal.timeout(180000) })
        if (!res.ok) {
          const err = await res.json()
          throw new Error(err.detail || `HTTP ${res.status}`)
        }
        const data = await res.json()
        // Net P&L card shows USD (computed by the engine from the configured lot)
        if (data.summary) data.summary.net_pnl = data.summary.net_usd ?? data.summary.net_points
        // Builder engine reports lowercase win/loss and BUY/SELL in `direction`
        data.trades = (data.trades || []).map(t => ({
          ...t,
          signal:    t.direction,                                  // BUY / SELL
          direction: t.direction === "BUY" ? "LONG" : "SHORT",     // table renders this
          outcome:   (t.outcome || "").toUpperCase(),              // WIN / LOSS
          result:    t.outcome === "win" ? "TP" : "SL",
          date:      (t.time || "").slice(0, 10),
          time:      (t.time || "").slice(11, 16),
        }))
        data.symbol = strat.symbol
        setResults(_normaliseFrvpResult(data))
        return
      }

      // ── Gold FRVP + Advanced Gold: use strategy-tester endpoint ─────────────
      if (strat.isFrvp || strat.isAdvancedGold) {
        const effectiveDays = strat.minDays ? Math.max(days, strat.minDays) : days
        const params = new URLSearchParams({
          strategy_id: strat.backendStrategy,
          days:        effectiveDays,
          sl_distance: slDistance,
          rr_ratio:    rr,
          symbol:      strat.symbol,
        })
        const res = await fetch(`${API}/strategy-tester/run?${params}`,
          { method: "POST", signal: AbortSignal.timeout(180000) })
        if (!res.ok) {
          const err = await res.json()
          throw new Error(err.detail || `HTTP ${res.status}`)
        }
        const data = await res.json()
        setResults(_normaliseFrvpResult(data))
        return
      }

      // ── All other strategies: existing backtest endpoint ──────────────────
      const backendStrat = strat.backendStrategy || strat.id
      const symbolMap = {
        "XAUUSD+": "XAUUSD", "BTCUSDT": "BTCUSDT", "ETHUSDT": "ETHUSDT",
        "BTCUSD":  "BTCUSDT", "ETHUSD":  "ETHUSDT",
      }
      const symbol = symbolMap[strat.symbol] || strat.symbol
      const url = `${API}/backtest/strategy?strategy=${backendStrat}&symbol=${symbol}&days=${days}&rr=${rr}`
      const res = await fetch(url, { signal: AbortSignal.timeout(180000) })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || `HTTP ${res.status}`)
      }
      setResults(await res.json())
    } catch(e) {
      const isTimeout = e.name === "TimeoutError" || /timed out/i.test(e.message || "")
      setError(isTimeout
        ? "Backtest took too long (>3 min). Try a shorter period (14–30 days), or check the backend console for errors."
        : e.message || "Backtest failed. Make sure backend is running.")
    } finally {
      setRunning(false)
    }
  }

  // Translate FRVP / advanced Gold response shape → display shape the UI understands
  function _normaliseFrvpResult(data) {
    const s = data.summary || {}

    // ── Detect format: FRVP uses total_trades; advanced/SMC uses total ────────
    const isFrvpFormat  = s.total_trades != null
    const isAdvancedFmt = !isFrvpFormat  // gold_advanced + SMC swing

    // P&L field: FRVP → net_pnl (USD); gold advanced → net_pnl (USD);
    //            SMC swing → net_points (price pts); fall back gracefully
    const netPnl = s.net_pnl ?? s.net_points ?? null

    return {
      strategy:       data.strategy_id || data.strategy,
      symbol:         data.symbol || "XAUUSD",   // respect actual symbol (BTC/ETH/Gold)
      days:           data.days || s.days || data.params?.days,
      ran_at:         data.ran_at,
      isFrvp:         true,          // flag for conditional rendering (reuse FRVP UI)
      summary: {
        total:         isFrvpFormat ? s.total_trades : s.total,
        wins:          s.wins,
        losses:        s.losses,
        win_rate:      s.win_rate,
        breakeven_wr:  s.breakeven_wr,
        net_pips:      null,
        net_points:    s.net_points ?? null,
        net_usd:       s.net_usd ?? null,
        lot_size:      s.lot_size ?? null,
        contract_size: s.contract_size ?? null,
        // Stat cards — all strategies populate these
        net_pnl:       netPnl,
        profit_factor: s.profit_factor,
        avg_win:       s.avg_win,
        avg_loss:      s.avg_loss,
        max_drawdown:  s.max_drawdown,
        best_trade:    s.best_trade ?? null,
        worst_trade:   s.worst_trade ?? null,
        expectancy:    s.expectancy ?? null,
        // FRVP-only extras (null-safe on other strategies)
        sl_used:       s.sl_used  ?? null,
        rr_used:       s.rr_used  ?? null,
      },
      // Translate individual trades so EquityLine + trade table work
      trades: (data.trades || []).map(t => {
        // Normalise direction: "LONG"→BUY, "SHORT"→SELL; BUY/SELL pass through
        const sig = t.signal || (t.direction === "LONG" ? "BUY" : "SELL")
        // Normalise outcome: "TP"→WIN, "SL"→LOSS; WIN/LOSS pass through
        const out = t.outcome || (t.result === "TP" ? "WIN" : "LOSS")
        return {
          ...t,
          signal:   sig,
          outcome:  out,
          // pnl_pips drives the equity line; prefer t.points (advanced/SMC), else t.pnl (FRVP)
          pnl_pips: t.points ?? t.pnl ?? 0,
          sl:       out === "WIN" ? null : (t.sl ?? t.exit ?? null),
          tp:       out === "WIN" ? (t.tp ?? t.exit ?? null) : null,
        }
      }),
      // Translate day breakdown
      days_breakdown: (data.day_breakdown || []).map(d => ({
        ...d,
        pnl_pips: d.pnl_points ?? d.pnl ?? 0,
      })),
      // Keep raw data alongside
      _frvp: data,
    }
  }

  const sm = results?.summary
  const trades = results?.trades || []

  // $ conversion factor for point-based results: lot × contract size
  const conv = (() => {
    const symMap = {
      BTCUSDT: ["BTCUSD", 1],  BTCUSD: ["BTCUSD", 1],
      ETHUSDT: ["ETHUSD", 1],  ETHUSD: ["ETHUSD", 1],
      XAUUSD:  ["XAUUSD+", 100], "XAUUSD+": ["XAUUSD+", 100],
    }
    const sym = results?.symbol || strat?.symbol
    const m = symMap[sym]
    if (!m) return null
    const lot = parseFloat(lotSizes[m[0]])
    if (!lot || Number.isNaN(lot)) return null
    return { lot, cs: m[1], f: lot * m[1] }
  })()
  const toUsd = (pts) => conv ? `${pts >= 0 ? "+" : "-"}$${Math.abs(pts * conv.f).toFixed(2)}` : null
  const days_bd = results?.days_breakdown || []

  return (
    <div style={{ padding:"20px 24px", display:"flex", flexDirection:"column", gap:20 }}>

      {/* ── Parameters panel ─────────────────────────────────────────────── */}
      <div style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:10, padding:20 }}>
        <div style={{ fontSize:11, color:C.muted, textTransform:"uppercase", letterSpacing:".5px", marginBottom:14 }}>
          Strategy Tester — Select parameters and run
        </div>

        {/* Style toggle */}
        <div style={{ display:"flex", gap:8, marginBottom:14 }}>
          {STYLES.map(s => (
            <button key={s} onClick={() => handleStyleChange(s)} style={{
              padding:"7px 20px", borderRadius:20, border:"none", cursor:"pointer",
              fontSize:13, fontWeight: style===s ? 700 : 500, transition:"all .15s",
              background: style===s
                ? s==="Scalping" ? "#0d2d4a"
                  : s==="Intraday" ? "#1a2d0d"
                  : "#2d1a0d"
                : "#161b22",
              color: style===s
                ? s==="Scalping" ? C.blue
                  : s==="Intraday" ? C.green
                  : C.gold
                : C.muted,
              boxShadow: style===s ? `0 0 10px ${s==="Scalping"?C.blue:s==="Intraday"?C.green:C.gold}30` : "none",
            }}>
              {s==="Scalping"?"⚡":s==="Intraday"?"📊":"🌙"} {s}
            </button>
          ))}
          <span style={{ fontSize:11, color:C.muted, alignSelf:"center", marginLeft:8 }}>
            {STYLE_DESC[style]}
          </span>
          {hiddenIds.length > 0 && (
            <button onClick={restoreAll} style={{
              marginLeft:"auto", background:"none", border:"none", cursor:"pointer",
              fontSize:11, color:C.muted, textDecoration:"underline", padding:0,
            }}>
              ↩ Restore {hiddenIds.length} hidden {hiddenIds.length === 1 ? "strategy" : "strategies"}
            </button>
          )}
        </div>

        <div style={{ display:"flex", gap:16, flexWrap:"wrap", alignItems:"flex-end" }}>

          {/* Strategy dropdown — filtered by style */}
          <div style={{ flex:"1 1 280px" }}>
            <label style={{ fontSize:11, color:C.muted, display:"block", marginBottom:5 }}>STRATEGY</label>
            <select value={stratId} onChange={e => {
              const newId = e.target.value
              setStratId(newId); setResults(null); setError("")
              const newStrat = CATALOGUE.find(s => s.id === newId)
              // Reset SL to sensible default for the chosen strategy type
              if (newStrat?.slLabel?.includes("ATR")) setSlDistance(1.5)
              else if (newStrat?.isFrvp) setSlDistance(8.0)
            }}
              style={{ width:"100%", background:"#0d1117", color:C.text, border:`1px solid ${C.border}`,
                borderRadius:6, padding:"8px 10px", fontSize:13 }}>
              {filteredStrats.map(s => (
                <option key={s.id} value={s.id}>{s.label}</option>
              ))}
            </select>
            <div style={{ fontSize:11, color:C.muted, marginTop:5 }}>{strat?.desc}</div>
          </div>

          {/* Days — FRVP capped at 45 (Yahoo Finance 5m limit ~59d) */}
          <div style={{ flex:"0 0 120px" }}>
            <label style={{ fontSize:11, color:C.muted, display:"block", marginBottom:5 }}>PERIOD</label>
            <select value={days} onChange={e => setDays(Number(e.target.value))}
              style={{ width:"100%", background:"#0d1117", color:C.text, border:`1px solid ${C.border}`,
                borderRadius:6, padding:"8px 10px", fontSize:13 }}>
              {(strat?.isFrvp || strat?.isAdvancedGold ? FRVP_DAYS_OPTIONS : DAYS_OPTIONS).map(d =>
                <option key={d} value={d}>{d} days</option>)}
            </select>
          </div>

          {/* RR */}
          <div style={{ flex:"0 0 120px" }}>
            <label style={{ fontSize:11, color:C.muted, display:"block", marginBottom:5 }}>
              {strat?.rrLabel || "RISK : REWARD"}
            </label>
            <select value={rr} onChange={e => setRr(Number(e.target.value))}
              style={{ width:"100%", background:"#0d1117", color:C.text, border:`1px solid ${C.border}`,
                borderRadius:6, padding:"8px 10px", fontSize:13 }}>
              {RR_OPTIONS.map(r => <option key={r} value={r}>1 : {r}</option>)}
            </select>
          </div>

          {/* SL Distance — FRVP: $ amount; Adaptive S/R: ATR multiplier */}
          {(strat?.isFrvp || strat?.slLabel) && (
            <div style={{ flex:"0 0 130px" }}>
              <label style={{ fontSize:11, color:C.muted, display:"block", marginBottom:5 }}>
                {strat?.slLabel ? strat.slLabel.toUpperCase() : "SL DISTANCE ($)"}
              </label>
              <select value={slDistance} onChange={e => setSlDistance(Number(e.target.value))}
                style={{ width:"100%", background:"#0d1117", color:C.text, border:`1px solid ${C.border}`,
                  borderRadius:6, padding:"8px 10px", fontSize:13 }}>
                {strat?.isFrvp
                  ? SL_OPTIONS.map(s => <option key={s} value={s}>${s}</option>)
                  : [0.5, 1.0, 1.5, 2.0, 2.5, 3.0].map(s => <option key={s} value={s}>{s}×</option>)
                }
              </select>
            </div>
          )}

          {/* Symbol (read-only display) */}
          <div style={{ flex:"0 0 100px" }}>
            <label style={{ fontSize:11, color:C.muted, display:"block", marginBottom:5 }}>SYMBOL</label>
            <div style={{ background:"#0d1117", color:C.gold, border:`1px solid ${C.border}`,
              borderRadius:6, padding:"8px 10px", fontSize:13, fontWeight:700 }}>
              {strat.symbol}
            </div>
          </div>

          {/* Run button */}
          <button onClick={run} disabled={running} style={{
            flex:"0 0 auto", padding:"9px 28px", borderRadius:8, border:"none",
            background: running ? "#1c2128" : "linear-gradient(135deg,#1a3a5c,#0d2d4a)",
            color: running ? C.muted : C.blue, fontSize:14, fontWeight:700,
            cursor: running ? "not-allowed" : "pointer", whiteSpace:"nowrap",
            boxShadow: running ? "none" : "0 0 12px rgba(77,171,247,.2)",
            transition:"all .2s"
          }}>
            {running ? "⏳ Running…" : "▶ Run Backtest"}
          </button>

          {/* Clear MT5 cache — FRVP only */}
          {strat?.isFrvp && (
            <ClearCacheButton />
          )}
        </div>

        {strat.note && (
          <div style={{ marginTop:10, fontSize:11, color:C.gold, background:"rgba(240,180,41,.07)",
            border:`1px solid rgba(240,180,41,.2)`, borderRadius:6, padding:"6px 12px" }}>
            ℹ️ {strat.note}
          </div>
        )}

        {/* ── Deploy panel ──────────────────────────────────────────────── */}
        <div style={{ marginTop:14, padding:"12px 14px", background:"#0d1117",
          border:`1px solid ${C.border}`, borderRadius:8 }}>
          <div style={{ fontSize:11, color:C.muted, textTransform:"uppercase",
            letterSpacing:".5px", marginBottom:10 }}>
            🚀 Deploy to Live / Demo / Paper Trading
          </div>
          <div style={{ display:"flex", gap:10, alignItems:"center", flexWrap:"wrap" }}>

            {/* Mode selector */}
            {["paper","demo","live"].map(m => (
              <button key={m} onClick={() => setDeployMode(m)} style={{
                padding:"5px 14px", borderRadius:6, border:"none", cursor:"pointer",
                fontSize:12, fontWeight: deployMode===m ? 700 : 500,
                background: deployMode===m
                  ? m==="live" ? "#3a0d0d" : m==="demo" ? "#0d2d1a" : "#1a1a3a"
                  : "#161b22",
                color: deployMode===m
                  ? m==="live" ? C.red : m==="demo" ? C.green : C.blue
                  : C.muted,
              }}>
                {m==="live" ? "🔴" : m==="demo" ? "🟡" : "📋"} {m.charAt(0).toUpperCase()+m.slice(1)}
              </button>
            ))}

            {/* Deploy / Remove button */}
            {strat?.strategyTag ? (
              <button onClick={toggleDeploy} disabled={deploying} style={{
                padding:"5px 18px", borderRadius:6, border:"none", cursor: deploying ? "not-allowed":"pointer",
                fontSize:12, fontWeight:700,
                background: isDeployed(strat.strategyTag)
                  ? "rgba(248,81,73,.15)" : "rgba(105,219,124,.15)",
                color: isDeployed(strat.strategyTag) ? C.red : C.green,
              }}>
                {deploying ? "…" : isDeployed(strat.strategyTag)
                  ? `✕ Remove from ${deployMode}` : `+ Add to ${deployMode}`}
              </button>
            ) : (
              <span style={{ fontSize:11, color:C.muted }}>
                This strategy is not yet deployable from Tester.
              </span>
            )}

            {/* Active strategies badge list */}
            <div style={{ display:"flex", gap:6, flexWrap:"wrap", marginLeft:8 }}>
              {(activeStrategies[deployMode] || []).map(tag => (
                <span key={tag} style={{
                  background:"rgba(77,171,247,.12)", color:C.blue,
                  padding:"2px 8px", borderRadius:4, fontSize:11, fontWeight:600,
                }}>
                  ✓ {tag}
                </span>
              ))}
              {(activeStrategies[deployMode] || []).length === 0 && (
                <span style={{ fontSize:11, color:C.muted }}>No active strategies for {deployMode}</span>
              )}
            </div>
          </div>

          {deployMsg && (
            <div style={{ marginTop:8, fontSize:12,
              color: deployMsg.startsWith("✅") ? C.green : C.red }}>
              {deployMsg}
            </div>
          )}
        </div>

        {/* ── Scalper Live Control Panel (only for scalper strategies) ────── */}
        {strat?.isScalper && (
          <div style={{ marginTop:14, padding:"14px 16px", background:"#0d1117",
            border:`1px solid ${scalperStatus?.running ? "#69db7c44" : C.border}`,
            borderRadius:8 }}>
            <div style={{ display:"flex", alignItems:"center", gap:12, flexWrap:"wrap" }}>
              <div>
                <div style={{ fontSize:11, color:C.muted, textTransform:"uppercase",
                  letterSpacing:".5px", marginBottom:4 }}>
                  🤖 Live Scalper Engine
                </div>
                <div style={{ fontSize:12, color: scalperStatus?.running ? C.green : C.muted }}>
                  {scalperStatus === null
                    ? "Status unknown — backend may be offline"
                    : scalperStatus.running
                      ? `● Running · ${scalperStatus.symbols?.join(", ")} · ${scalperStatus.timeframe}`
                      : "○ Stopped"
                  }
                </div>
              </div>

              <div style={{ display:"flex", gap:8, marginLeft:"auto" }}>
                <button onClick={startScalper}
                  disabled={scalperLoading || scalperStatus?.running}
                  style={{
                    padding:"6px 18px", borderRadius:6, border:"none", fontSize:12, fontWeight:700,
                    cursor: (scalperLoading || scalperStatus?.running) ? "not-allowed" : "pointer",
                    background: scalperStatus?.running ? "#1c2128" : "rgba(105,219,124,.15)",
                    color: scalperStatus?.running ? C.muted : C.green,
                  }}>
                  ▶ Start Engine
                </button>
                <button onClick={stopScalper}
                  disabled={scalperLoading || !scalperStatus?.running}
                  style={{
                    padding:"6px 18px", borderRadius:6, border:"none", fontSize:12, fontWeight:700,
                    cursor: (scalperLoading || !scalperStatus?.running) ? "not-allowed" : "pointer",
                    background: !scalperStatus?.running ? "#1c2128" : "rgba(248,81,73,.15)",
                    color: !scalperStatus?.running ? C.muted : C.red,
                  }}>
                  ■ Stop Engine
                </button>
              </div>
            </div>

            {/* Stats row */}
            {scalperStatus?.running && (
              <div style={{ display:"flex", gap:20, marginTop:12, flexWrap:"wrap" }}>
                <div style={{ fontSize:11 }}>
                  <span style={{ color:C.muted }}>Signals fired: </span>
                  <span style={{ color:C.text, fontWeight:700 }}>{scalperStatus.signals_fired ?? 0}</span>
                </div>
                <div style={{ fontSize:11 }}>
                  <span style={{ color:C.muted }}>Auto-closed: </span>
                  <span style={{ color:C.text, fontWeight:700 }}>{scalperStatus.positions_closed ?? 0}</span>
                </div>
                <div style={{ fontSize:11 }}>
                  <span style={{ color:C.muted }}>Last check: </span>
                  <span style={{ color:C.text, fontWeight:700 }}>{scalperStatus.last_check ?? "—"}</span>
                </div>
                {scalperStatus.last_signal && (
                  <div style={{ fontSize:11 }}>
                    <span style={{ color:C.muted }}>Last signal: </span>
                    <span style={{
                      color: scalperStatus.last_signal.direction === "BUY" ? C.green : C.red,
                      fontWeight:700
                    }}>
                      {scalperStatus.last_signal.direction} {scalperStatus.last_signal.symbol}
                    </span>
                    <span style={{ color:C.muted }}> @ {scalperStatus.last_signal.close}
                      &nbsp;(RSI {scalperStatus.last_signal.rsi}) · {scalperStatus.last_signal.time}
                    </span>
                  </div>
                )}
              </div>
            )}

            {scalperMsg && (
              <div style={{ marginTop:8, fontSize:12,
                color: scalperMsg.startsWith("✅") || scalperMsg.startsWith("🛑") ? C.green : C.red }}>
                {scalperMsg}
              </div>
            )}

            <div style={{ marginTop:8, fontSize:11, color:C.muted }}>
              ⏱ All scalper positions auto-close after 5 min · Magic# 202609 ·
              Signals fire on M1 candle close · Regime gate skips sideways markets
            </div>
          </div>
        )}
      </div>

      {/* ── Error ────────────────────────────────────────────────────────── */}
      {error && (
        <div style={{ background:"rgba(248,81,73,.08)", border:`1px solid ${C.red}`,
          borderRadius:8, padding:"12px 16px", color:C.red, fontSize:13 }}>
          ⚠ {error}
        </div>
      )}

      {/* ── Results ──────────────────────────────────────────────────────── */}
      {results && sm && (
        <>
          {/* Summary cards */}
          <div>
            <div style={{ display:"flex", alignItems:"center", gap:12, marginBottom:10, flexWrap:"wrap" }}>
              <div style={{ fontSize:11, color:C.muted, textTransform:"uppercase", letterSpacing:".5px" }}>
                Results — {results.symbol} · {results.days} days
                {results.ran_at && <span style={{ marginLeft:10, color:"#555" }}>Data from {results.ran_at}</span>}
              </div>

              {/* Delete button — only shown when strategy is losing */}
              {(() => {
                const isLosing = results.isFrvp
                  ? (sm.net_pnl ?? 0) < 0
                  : ((sm.win_rate ?? 0) < (sm.breakeven_wr ?? 50))
                return isLosing ? (
                  <button
                    onClick={() => hideStrategy(stratId)}
                    style={{
                      marginLeft:"auto", display:"flex", alignItems:"center", gap:6,
                      padding:"5px 14px", borderRadius:6, border:"1px solid rgba(248,81,73,.4)",
                      background:"rgba(248,81,73,.08)", color:C.red,
                      cursor:"pointer", fontSize:12, fontWeight:700,
                    }}
                  >
                    🗑 Remove losing strategy
                  </button>
                ) : null
              })()}
            </div>
            <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(130px,1fr))", gap:10 }}>
              <StatCard label="Total Setups"  value={sm.total} />
              <StatCard label="Wins"          value={sm.wins}         color={C.green} />
              <StatCard label="Losses"        value={sm.losses}       color={C.red}   />
              {sm.breakevens != null &&
                <StatCard label="Breakevens"  value={sm.breakevens}   color="#cc5de8" />}
              <StatCard label="Win Rate"
                value={sm.win_rate != null ? `${sm.win_rate}%` : "—"}
                sub={sm.breakeven_wr != null ? `Breakeven: ${sm.breakeven_wr}%` : ""}
                color={sm.breakeven_wr != null ? ((sm.win_rate||0) >= sm.breakeven_wr ? C.green : C.red) : C.text} />
              {sm.timeouts != null &&
                <StatCard label="Timeouts (5min)" value={sm.timeouts} color={C.gold} />}
              {/* FRVP uses dollar P&L; other strategies use pips/points */}
              {results.isFrvp ? (
                <StatCard label="Net P&L ($)"
                  value={sm.net_pnl != null ? `${sm.net_pnl >= 0 ? "+" : ""}$${sm.net_pnl}` : "—"}
                  sub={sm.lot_size != null
                    ? `Lot ${sm.lot_size} · ${(sm.net_points||0) >= 0 ? "+" : ""}${sm.net_points} pts`
                    : `SL $${sm.sl_used} · RR 1:${sm.rr_used}`}
                  color={(sm.net_pnl||0) >= 0 ? C.green : C.red} />
              ) : (() => {
                const pts = sm.net_pips ?? sm.net_points
                const showUsd = conv && pts != null
                return (
                  <StatCard label={showUsd ? "Net P&L ($)" : "Net P&L"}
                    value={showUsd
                      ? toUsd(pts)
                      : sm.net_pips != null
                        ? `${sm.net_pips > 0 ? "+" : ""}${sm.net_pips} pips`
                        : sm.net_points != null
                          ? `${sm.net_points > 0 ? "+" : ""}${sm.net_points} pts`
                          : "—"}
                    sub={showUsd
                      ? `Lot ${conv.lot} · ${pts > 0 ? "+" : ""}${Number(pts).toFixed(1)} pts`
                      : sm.net_usd != null ? `$${sm.net_usd} at 0.01 lot` : sm.note || ""}
                    color={((sm.net_pips||0)+(sm.net_points||0)) >= 0 ? C.green : C.red} />
                )
              })()}
              {/* Highest Profit / Highest Loss — shown for ALL strategies.
                  Uses the backend's best_trade/worst_trade when present,
                  otherwise computes them from the trade list. */}
              {(() => {
                const pl   = t => Number(t.pnl_pips ?? t.points ?? t.pnl ?? 0)
                const best = sm.best_trade  ?? (trades.length ? Math.max(...trades.map(pl)) : null)
                const worst= sm.worst_trade ?? (trades.length ? Math.min(...trades.map(pl)) : null)
                const fmt  = v => results.isFrvp
                  ? (sm.lot_size != null
                      ? `${v >= 0 ? "+" : "-"}$${Math.abs(v * sm.lot_size * (sm.contract_size || 1)).toFixed(2)} / ${Number(v).toFixed(1)} pts`
                      : `${v >= 0 ? "+" : "-"}$${Math.abs(v).toFixed(2)}`)
                  : (conv
                      ? `${toUsd(v)} / ${Number(v).toFixed(1)} pts`
                      : `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)} pts`)
                if (best == null && worst == null) return null
                return (
                  <>
                    {best != null && (
                      <StatCard label="Highest Profit" value={fmt(best)}
                        color={best > 0 ? C.green : C.muted} />
                    )}
                    {worst != null && (
                      <StatCard label="Highest Loss" value={fmt(worst)}
                        color={worst < 0 ? C.red : C.muted} />
                    )}
                  </>
                )
              })()}
              {/* FRVP extra stats */}
              {results.isFrvp && sm.profit_factor != null && (
                <StatCard label="Profit Factor"
                  value={sm.profit_factor}
                  color={sm.profit_factor >= 1 ? C.green : C.red} />
              )}
              {results.isFrvp && sm.avg_win != null && (
                <StatCard label="Avg Win" value={`$${sm.avg_win}`} color={C.green} />
              )}
              {results.isFrvp && sm.avg_loss != null && (
                <StatCard label="Avg Loss" value={`$${sm.avg_loss}`} color={C.red} />
              )}
              {results.isFrvp && sm.max_drawdown != null && (
                <StatCard label="Max Drawdown" value={`$${sm.max_drawdown}`} color={C.red} />
              )}
            </div>
          </div>

          {/* Equity curve */}
          <div style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:8, padding:16 }}>
            <div style={{ fontSize:11, color:C.muted, textTransform:"uppercase", letterSpacing:".5px", marginBottom:10 }}>
              Equity Curve {results.isFrvp ? "(USD P&L)" : "(pips)"}
            </div>
            <EquityLine trades={trades} pipKey={trades[0]?.pnl_pips !== undefined ? "pnl_pips" : "points"} />
          </div>

          {/* Day-wise table */}
          {days_bd.length > 0 && (
            <div style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:8, overflow:"hidden" }}>
              <div style={{ padding:"10px 14px", fontSize:11, color:C.muted, textTransform:"uppercase",
                letterSpacing:".5px", borderBottom:`1px solid ${C.border}` }}>Day-Wise Breakdown</div>
              <div style={{ overflowX:"auto" }}>
                <table style={{ width:"100%", borderCollapse:"collapse", fontSize:13 }}>
                  <thead>
                    <tr>
                      {["Date","Trades","Wins","Losses","Win Rate", results.isFrvp ? "P&L ($)" : "P&L Pips"].map(h => (
                        <th key={h} style={{ padding:"8px 12px", textAlign:"left", color:C.muted,
                          fontSize:11, textTransform:"uppercase", borderBottom:`1px solid ${C.border}` }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {[...days_bd].reverse().map((d, i) => {
                      const pnl = parseFloat(d.pnl_pips || 0)
                      return (
                        <tr key={i} style={{ borderBottom:`1px solid rgba(255,255,255,.03)` }}>
                          <td style={{ padding:"8px 12px" }}>{d.date}</td>
                          <td style={{ padding:"8px 12px" }}>{d.trades}</td>
                          <td style={{ padding:"8px 12px", color:C.green }}>{d.wins}</td>
                          <td style={{ padding:"8px 12px", color:C.red }}>{d.losses}</td>
                          <td style={{ padding:"8px 12px", color:(d.win_rate||0)>=50?C.green:C.red, fontWeight:700 }}>{d.win_rate}%</td>
                          <td style={{ padding:"8px 12px", color:pnl>=0?C.green:C.red }}>
                            {pnl>=0?"+":""}{pnl.toFixed ? pnl.toFixed(1) : pnl}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Trade log */}
          <div style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:8, overflow:"hidden" }}>
            <div style={{ padding:"10px 14px", fontSize:11, color:C.muted, textTransform:"uppercase",
              letterSpacing:".5px", borderBottom:`1px solid ${C.border}` }}>
              Trade Log (last {trades.length})
            </div>
            <div style={{ overflowX:"auto" }}>
              <table style={{ width:"100%", borderCollapse:"collapse", fontSize:12 }}>
                <thead>
                  <tr>
                    {(results.isFrvp
                      ? ["Date","Time (IST)","Dir","Entry","Exit","VAH","VAL","Result","P&L ($)"]
                      : ["Date","Dir","Entry","SL","TP","Setup / Score","MaxR","Outcome","P&L"]
                    ).map(h => (
                      <th key={h} style={{ padding:"7px 10px", textAlign:"left", color:C.muted,
                        fontSize:10, textTransform:"uppercase", borderBottom:`1px solid ${C.border}` }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...trades].reverse().map((t, i) => {
                    const pnl = parseFloat(t.pnl_pips ?? t.points ?? 0)
                    return (
                      <tr key={i} style={{ borderBottom:`1px solid rgba(255,255,255,.03)`,
                        background: i%2===0?"transparent":"rgba(255,255,255,.01)" }}>
                        <td style={{ padding:"7px 10px", color:C.muted, whiteSpace:"nowrap" }}>{t.date}</td>

                        {/* FRVP: show time + extra columns */}
                        {results.isFrvp ? (<>
                          <td style={{ padding:"7px 10px", color:C.muted, fontSize:11 }}>
                            {(t.time || "").slice(11,16) || "—"}
                          </td>
                          <td style={{ padding:"7px 10px" }}>
                            <span style={{ padding:"2px 7px", borderRadius:4, fontSize:11, fontWeight:700,
                              background: t.direction==="LONG"?"rgba(105,219,124,.15)":"rgba(248,81,73,.15)",
                              color: t.direction==="LONG"?C.green:C.red }}>
                              {t.direction==="LONG"?"▲ LONG":"▼ SHORT"}
                            </span>
                          </td>
                          <td style={{ padding:"7px 10px", fontFamily:"monospace", color:C.text }}>
                            {parseFloat(t.entry||0).toFixed(2)}
                          </td>
                          <td style={{ padding:"7px 10px", fontFamily:"monospace",
                            color: t.result==="TP"?C.green:C.red }}>
                            {parseFloat(t.exit||0).toFixed(2)}
                          </td>
                          <td style={{ padding:"7px 10px", fontFamily:"monospace", color:C.muted, fontSize:11 }}>
                            {t.vah ?? "—"}
                          </td>
                          <td style={{ padding:"7px 10px", fontFamily:"monospace", color:C.muted, fontSize:11 }}>
                            {t.val ?? "—"}
                          </td>
                          <td style={{ padding:"7px 10px" }}>
                            <span style={{
                              padding:"2px 8px", borderRadius:4, fontSize:11, fontWeight:700,
                              background: t.result==="TP"?"rgba(105,219,124,.15)":"rgba(248,81,73,.15)",
                              color: t.result==="TP"?C.green:C.red,
                            }}>{t.result}</span>
                          </td>
                          <td style={{ padding:"7px 10px", fontWeight:700, whiteSpace:"nowrap",
                            color: pnl>=0?C.green:C.red }}>
                            {sm?.lot_size != null
                              ? `${pnl>=0?"+":"-"}$${Math.abs(pnl * sm.lot_size * (sm.contract_size || 1)).toFixed(2)} / ${pnl.toFixed(1)} pts`
                              : `${pnl>=0?"+":""}${pnl.toFixed(2)}`}
                          </td>
                        </>) : (<>
                          {/* Standard strategies */}
                          <td style={{ padding:"7px 10px" }}>
                            <span style={{ padding:"2px 7px", borderRadius:4, fontSize:11, fontWeight:700,
                              background: (t.signal||t.direction)==="BUY"?"rgba(105,219,124,.15)":"rgba(248,81,73,.15)",
                              color: (t.signal||t.direction)==="BUY"?C.green:C.red }}>
                              {t.signal || t.direction || "—"}
                            </span>
                          </td>
                          <td style={{ padding:"7px 10px", fontFamily:"monospace", color:C.text }}>
                            {parseFloat(t.entry||0).toFixed(2)}
                          </td>
                          <td style={{ padding:"7px 10px", fontFamily:"monospace", color:C.red }}>
                            {parseFloat(t.sl||0).toFixed(2)}
                          </td>
                          <td style={{ padding:"7px 10px", fontFamily:"monospace", color:C.gold }}>
                            {t.tp || "—"}
                          </td>
                          <td style={{ padding:"7px 10px", color:C.muted, fontSize:11 }}>
                            {t.setup || t.condition || t.quality_score || "—"}
                          </td>
                          <td style={{ padding:"7px 10px", color:C.blue, fontSize:11 }}>
                            {t.max_move_r != null ? `${t.max_move_r}R` : "—"}
                          </td>
                          <td style={{ padding:"7px 10px" }}><OutcomeBadge outcome={t.outcome} /></td>
                          <td style={{ padding:"7px 10px", fontWeight:700, whiteSpace:"nowrap",
                            color: pnl>=0?C.green:C.red }}>
                            {conv
                              ? `${toUsd(pnl)} / ${pnl.toFixed ? pnl.toFixed(1) : pnl} pts`
                              : `${pnl>=0?"+":""}${pnl.toFixed ? pnl.toFixed(1) : pnl}`}
                          </td>
                        </>)}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {!running && !results && !error && (
        <div style={{ textAlign:"center", padding:"60px 20px", color:C.muted }}>
          <div style={{ fontSize:40, marginBottom:12 }}>📊</div>
          <div style={{ fontSize:15, marginBottom:6 }}>Select a strategy and click Run Backtest</div>
          <div style={{ fontSize:13 }}>Results will appear here — equity curve, day-wise breakdown, and full trade log</div>
        </div>
      )}

      {/* ── Live Signal Engine — strategy selector for each asset ────────────────────── */}
      <LiveStrategySelector />

    </div>
  )
}
