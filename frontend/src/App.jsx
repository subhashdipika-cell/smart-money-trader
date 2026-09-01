import { useEffect, useMemo, useState } from "react"
import axios from "axios"

import "./App.css"
import logoSrc from "./assets/Logo.png"

// Inject header-tabs style once
const headerTabsStyle = `
  .header-tabs { display: flex; align-items: center; gap: 4px; margin: 0; padding: 0; border: none; background: none; }
  .header-tabs button { padding: 6px 14px; font-size: 13px; border-radius: 8px; }
`
if (!document.getElementById("header-tabs-style")) {
  const s = document.createElement("style")
  s.id = "header-tabs-style"
  s.textContent = headerTabsStyle
  document.head.appendChild(s)
}
import TradingViewChart from "./components/Chart/TradingViewChart"
import SessionOverlay from "./components/Chart/SessionOverlay"
import EventsTicker from "./components/Chart/EventsTicker"
import PnLDashboard from "./components/PnL/PnLDashboard"
import SignalCard from "./components/Signals/SignalCard"
import MT5Panel from "./components/MT5Panel"
import LearningDashboard from "./components/Learning/LearningDashboard"
import StrategyTester from "./components/StrategyTester/StrategyTester"
import StrategyBuilder from "./components/StrategyBuilder/StrategyBuilder"
import MoneyManagement from "./components/MoneyManagement/MoneyManagement"
import Activation from "./components/Activation/Activation"

const API_URLS = [
  import.meta.env.VITE_API_URL,
  "http://127.0.0.1:8000",
  "http://127.0.0.1:8001"
].filter(Boolean)

const ENGINE_BASE = API_URLS[0] || "http://127.0.0.1:8000"

const CHART_HEIGHT        = 640
const ACCOUNT_STORAGE_KEY = "smart-money-trader-account"
const TABS                = { dashboard: "dashboard", pnl: "pnl", learning: "learning", sentiment: "sentiment", history: "history", mt5: "mt5", tester: "tester", builder: "builder", money: "money" }

const MARKETS = {
  BTC: {
    id: "BTC", label: "BTC/USD", apiSymbol: "BTCUSDT",
    chartSymbol: "VANTAGE:BTCUSD", unitLabel: "BTC",
    lotLabel: "lot", lotSize: 1,
    lotNote: "BTC CFD on Vantage. 1 lot = 1 BTC."
  },
  ETH: {
    id: "ETH", label: "ETH/USD", apiSymbol: "ETHUSDT",
    chartSymbol: "VANTAGE:ETHUSD", unitLabel: "ETH",
    lotLabel: "lot", lotSize: 1,
    lotNote: "ETH CFD on Vantage. 1 lot = 1 ETH."
  },
  GOLD: {
    id: "GOLD", label: "XAU/USD", apiSymbol: "XAUUSD",
    chartSymbol: "VANTAGE:XAUUSD", unitLabel: "oz",
    lotLabel: "standard lot", lotSize: 100,
    lotNote: "Gold signals paused — live data source being upgraded. Chart still available."
  }
}

const ALL_MARKETS    = Object.values(MARKETS)
const MARKET_OPTIONS = ALL_MARKETS

const DEFAULT_SETUP = {
  capital: "", capitalCurrency: "INR",
  riskPercent: 1, usdtInr: "", marketId: "BTC"
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function toNumber(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

function normalizeCandles(candles) {
  return candles
    .map((c) => ({
      time: c.time, open: toNumber(c.open),
      high: toNumber(c.high), low: toNumber(c.low), close: toNumber(c.close)
    }))
    .filter((c) => c.time && c.high && c.low)
}

function hasMarketPayload(data) {
  return Boolean(
    data &&
    Array.isArray(data.candles) && Array.isArray(data.signals) &&
    Array.isArray(data.fvgs)    && Array.isArray(data.order_blocks) &&
    Array.isArray(data.bos)     && Array.isArray(data.sweeps)
  )
}

function hasSentPayload(data) {
  return Boolean(data && Array.isArray(data.signals))
}

function buildApiUrl(apiUrl, path) {
  return `${apiUrl.replace(/\/$/, "")}${path}`
}

function buildMarketPath(path, symbol) {
  const sep = path.includes("?") ? "&" : "?"
  return `${path}${sep}symbol=${encodeURIComponent(symbol)}`
}

function readStoredAccount() {
  try {
    const stored          = JSON.parse(localStorage.getItem(ACCOUNT_STORAGE_KEY))
    const capitalCurrency = stored?.capitalCurrency === "USD" ? "USD" : "INR"
    const hasRate         = capitalCurrency === "USD" || Number(stored?.usdtInr) > 0
    if (
      stored && Number(stored.capital) > 0 &&
      Number(stored.riskPercent) > 0 && hasRate && MARKETS[stored.marketId]
    ) {
      return { ...stored, capitalCurrency }
    }
  } catch { return null }
  return null
}

function getEngineErrorMessage(error) {
  if (error?.response?.status === 503)
    return "Smart-money engine is running, but Binance market data is unavailable."
  return "Smart-money engine is offline at 127.0.0.1:8000 and 127.0.0.1:8001. The TradingView chart remains externally live."
}

function getEngineStatusLabel(error, lastUpdated) {
  if (!error) return lastUpdated ? "Live" : "Checking"
  return error.includes("Binance market data") ? "No data" : "Engine offline · Chart live"
}

async function fetchEngine(path, validate, timeout) {
  let lastError, reachableEngineError
  for (const apiUrl of [...new Set(API_URLS)]) {
    try {
      const response = await axios.get(buildApiUrl(apiUrl, path), { timeout })
      if (validate(response.data)) return response.data
      lastError = new Error(`Unexpected response from ${apiUrl}`)
    } catch (error) {
      if (error?.response) reachableEngineError = error
      lastError = error
    }
  }
  throw reachableEngineError || lastError
}

function formatSignalTime(timestamp) {
  const n = Number(timestamp)
  if (!Number.isFinite(n)) return { date: "--", time: "--" }
  const d = new Date(n)
  return {
    date: d.toLocaleDateString(),
    time: d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
  }
}

function formatPrice(value) {
  const n = Number(value)
  return Number.isFinite(n)
    ? n.toLocaleString(undefined, { maximumFractionDigits: 2 })
    : "--"
}


function OutcomePill({ outcome, points }) {
  if (!outcome || outcome === "OPEN") return (
    <span className="outcome-pill open">OPEN
      <div style={{fontSize:"10px",color:"#8b949e",fontWeight:400}}>awaiting entry</div>
    </span>
  )
  if (outcome === "ACTIVE") return (
    <span style={{ background:"rgba(63,185,80,0.12)", color:"#3fb950",
      padding:"3px 10px", borderRadius:"6px", fontSize:"12px", fontWeight:600,
      border:"1px solid rgba(63,185,80,0.3)" }}>🟢 Active</span>
  )
  if (outcome === "EXPIRED") return (
    <span className="outcome-pill" style={{
      background:"rgba(139,148,158,0.15)", color:"#8b949e",
      border:"1px solid #30363d", padding:"3px 10px",
      borderRadius:"6px", fontSize:"12px", fontWeight:600
    }}>Expired</span>
  )
  if (outcome === "CANCELLED") return (
    <span className="outcome-pill" style={{
      background:"rgba(210,130,50,0.1)", color:"#d08050",
      border:"1px solid rgba(210,130,50,0.3)", padding:"3px 10px",
      borderRadius:"6px", fontSize:"12px", fontWeight:600
    }}>Cancelled</span>
  )
  if (outcome === "CLOSED") return (
    <span style={{ background:"rgba(139,148,158,0.1)", color:"#8b949e",
      padding:"3px 10px", borderRadius:"6px", fontSize:"12px", fontWeight:600,
      border:"1px solid #30363d" }}>Closed</span>
  )
  const isWin   = outcome === "WIN"
  const pts     = Number(points)
  const hasPoints = Number.isFinite(pts)
  const sign    = isWin ? "+" : ""
  const label   = hasPoints ? `${sign}${pts.toFixed(2)} pts` : isWin ? "WIN" : "LOSS"
  return <span className={`outcome-pill ${isWin ? "win" : "loss"}`}>{label}</span>
}

// ── Asset badge shown on each signal card in the all-signals panel ────────────
function AssetBadge({ symbol }) {
  const map = { BTCUSDT: "BTC", ETHUSDT: "ETH", PAXGUSDT: "GOLD" }
  const label = map[symbol] || symbol
  const colorMap = { BTC: "#f7931a", ETH: "#627eea", GOLD: "#d4af37" }
  const color = colorMap[label] || "#888"
  return (
    <span style={{
      display: "inline-block",
      padding: "2px 8px",
      borderRadius: "4px",
      fontSize: "11px",
      fontWeight: 700,
      letterSpacing: "0.5px",
      background: color + "22",
      color: color,
      border: `1px solid ${color}55`,
      marginBottom: "6px"
    }}>
      {label}
    </span>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────

function App() {
  // ── License gate ────────────────────────────────────────────────────────────
  const [license, setLicense] = useState(undefined)   // undefined = checking
  useEffect(() => {
    let cancelled = false
    const check = () => fetch(`${(import.meta.env.VITE_API_URL || "http://127.0.0.1:8000")}/license/status`)
      .then(r => r.json())
      .then(d => { if (!cancelled) setLicense(d) })
      .catch(() => { if (!cancelled) setLicense(null) })   // backend down → don't block
    check()
    const t = setInterval(check, 6 * 60 * 60 * 1000)        // re-check twice a day
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  const [activeTab, setActiveTab]               = useState(TABS.dashboard)
  const [chartMarketId, setChartMarketId]       = useState(null) // null = follow account setting
  const [account, setAccount]                   = useState(() => readStoredAccount())
  const [setupForm, setSetupForm]               = useState(() => readStoredAccount() || DEFAULT_SETUP)

  // Per-market data for the chart (only selected market)
  const [candles, setCandles]                   = useState([])
  const [fvgs, setFvgs]                         = useState([])
  const [orderBlocks, setOrderBlocks]           = useState([])
  const [bos, setBos]                           = useState([])
  const [sweeps, setSweeps]                     = useState([])


  // History
  const [sentSignals, setSentSignals]           = useState([])
  const [mt5Trades, setMt5Trades]               = useState([])
  const [learningStats, setLearningStats]       = useState(null)
  const [pnlData, setPnlData]                   = useState({ demo: null, live: null, paper: null })
  const [pnlLoading, setPnlLoading]             = useState(false)
  const [sentimentData, setSentimentData]       = useState(null)
  const [learningLoading, setLearningLoading]   = useState(false)
  const [sentimentLoading, setSentimentLoading] = useState(false)
  const [isHistoryLoading, setIsHistoryLoading] = useState(false)

  // History tab filters
  const [hFilter, setHFilter]               = useState("ALL")
  const [mt5AcctFilter, setMt5AcctFilter]   = useState("ALL_ACCT")

  const [error, setError]                       = useState("")
  const [historyError, setHistoryError]         = useState("")
  const [lastUpdated, setLastUpdated]           = useState(null)
  const [livePrices, setLivePrices]             = useState({})
  const [stoppingEngine, setStoppingEngine]     = useState(false)

  const market         = MARKETS[chartMarketId] || MARKETS[account?.marketId] || MARKETS[setupForm.marketId] || MARKETS.BTC
  const isAccountReady = Boolean(
    account && Number(account.capital) > 0 && Number(account.riskPercent) > 0 &&
    (account.capitalCurrency === "USD" || Number(account.usdtInr) > 0)
  )

  // ── Fetch chart data for selected market ────────────────────────────────────
  useEffect(() => {
    let isMounted = true

    const fetchChartData = async () => {
      try {
        const data = await fetchEngine(
          buildMarketPath("", market.apiSymbol),
          hasMarketPayload, 15000
        )
        if (!isMounted) return
        setCandles(normalizeCandles(data.candles || []))
        setFvgs(data.fvgs || [])
        setOrderBlocks(data.order_blocks || [])
        setBos(data.bos || [])
        setSweeps(data.sweeps || [])
        setLastUpdated(new Date())
        setError("")
      } catch (err) {
        if (isMounted) setError(getEngineErrorMessage(err))
      }
    }

    if (!isAccountReady) return () => { isMounted = false }
    fetchChartData()
    const interval = setInterval(fetchChartData, 10000)
    return () => { isMounted = false; clearInterval(interval) }
  }, [isAccountReady, market.apiSymbol])

  // Live signals now use sentSignals filtered to OPEN/active only

  // ── Fetch P&L data ───────────────────────────────────────────────────────
  useEffect(() => {
    let isMounted = true
    const fetchPnl = async () => {
      setPnlLoading(true)
      try {
        const [demo, live, paper] = await Promise.all([
          fetch(`${API_URLS[0]}/pnl/summary?mode=demo`,  { signal: AbortSignal.timeout(15000) }).then(r => r.json()),
          fetch(`${API_URLS[0]}/pnl/summary?mode=live`,  { signal: AbortSignal.timeout(15000) }).then(r => r.json()),
          fetch(`${API_URLS[0]}/pnl/summary?mode=paper`, { signal: AbortSignal.timeout(15000) }).then(r => r.json()),
        ])
        if (isMounted) setPnlData({ demo, live, paper })
      } catch (e) {
        if (isMounted) setPnlData({ demo: null, live: null, paper: null })
      } finally {
        if (isMounted) setPnlLoading(false)
      }
    }
    if (activeTab === TABS.pnl && isAccountReady) {
      fetchPnl()
      const iv = setInterval(fetchPnl, 30000)
      return () => { isMounted = false; clearInterval(iv) }
    }
    return () => { isMounted = false }
  }, [activeTab, isAccountReady])

  // ── Fetch Learning stats ──────────────────────────────────────────────────
  useEffect(() => {
    let isMounted = true
    const fetchLearning = async () => {
      if (!learningStats) setLearningLoading(true)
      try {
        const res = await fetch(`${ENGINE_BASE}/learning/stats`, { signal: AbortSignal.timeout(15000) })
        if (!res.ok) throw new Error("Learning fetch failed")
        const data = await res.json()
        if (isMounted) setLearningStats(data)
      } catch (e) {
        console.warn("Learning fetch failed", e)
      } finally {
        if (isMounted) setLearningLoading(false)
      }
    }
    if (activeTab === TABS.learning && isAccountReady) {
      fetchLearning()
      const iv = setInterval(fetchLearning, 60000)
      return () => { isMounted = false; clearInterval(iv) }
    }
    return () => { isMounted = false }
  }, [activeTab, isAccountReady])

  // ── Fetch Sentiment data ──────────────────────────────────────────────────
  useEffect(() => {
    let isMounted = true
    const fetchSentiment = async () => {
      if (!sentimentData) setSentimentLoading(true)
      try {
        const res = await fetch(`${ENGINE_BASE}/sentiment`, { signal: AbortSignal.timeout(15000) })
        if (!res.ok) throw new Error("Sentiment fetch failed")
        const data = await res.json()
        if (isMounted) setSentimentData(data)
      } catch (e) {
        console.warn("Sentiment fetch failed", e)
      } finally {
        if (isMounted) setSentimentLoading(false)
      }
    }
    if (activeTab === TABS.sentiment && isAccountReady) {
      fetchSentiment()
      const iv = setInterval(fetchSentiment, 60000)
      return () => { isMounted = false; clearInterval(iv) }
    }
    return () => { isMounted = false }
  }, [activeTab, isAccountReady])

  // ── Fetch sent signals + MT5 trades (Dashboard live signals + History tab) ─
  useEffect(() => {
    let isMounted = true
    const fetchHistory = async (showLoading) => {
      if (showLoading) setIsHistoryLoading(true)
      try {
        const [sigData, tradeRes] = await Promise.allSettled([
          fetchEngine("/signals/sent", hasSentPayload, 15000),
          fetch(`${API_URLS[0]}/mt4/trades?mode=all`, { signal: AbortSignal.timeout(10000) }).then(r => r.json()),
        ])
        if (!isMounted) return
        if (sigData.status === "fulfilled") setSentSignals(sigData.value.signals || [])
        if (tradeRes.status === "fulfilled") setMt5Trades(tradeRes.value.trades || [])
        setHistoryError("")
      } catch {
        if (isMounted) setHistoryError("Unable to retrieve history right now.")
      } finally {
        if (isMounted) setIsHistoryLoading(false)
      }
    }
    if ((activeTab === TABS.history || activeTab === TABS.dashboard) && isAccountReady) {
      fetchHistory(true)
      const iv = setInterval(() => fetchHistory(false), 15000)
      return () => { isMounted = false; clearInterval(iv) }
    }
    return () => { isMounted = false }
  }, [activeTab, isAccountReady])

  // ── Fetch live prices for all instruments ──────────────────────────────
  useEffect(() => {
    const fetchPrices = async () => {
      try {
        const res = await fetch(`${ENGINE_BASE}/prices`, { signal: AbortSignal.timeout(8000) })
        if (res.ok) setLivePrices(await res.json())
      } catch {}
    }
    fetchPrices()
    const iv = setInterval(fetchPrices, 15000)
    return () => clearInterval(iv)
  }, [])

  const marketStats = useMemo(() => {
    const latest   = candles[candles.length - 1]
    const previous = candles[candles.length - 2]
    const move     = latest && previous ? latest.close - previous.close : 0
    return {
      latestPrice: latest
        ? latest.close.toLocaleString(undefined, { maximumFractionDigits: 2 })
        : "--",
      move:      Math.abs(move).toLocaleString(undefined, { maximumFractionDigits: 2 }),
      direction: move >= 0 ? "up" : "down"
    }
  }, [candles])

  const engineStatusLabel = getEngineStatusLabel(error, lastUpdated)
  const riskAmount        = isAccountReady
    ? Number(account.capital) * (Number(account.riskPercent) / 100) : 0
  const accountCurrency   = account?.capitalCurrency || setupForm.capitalCurrency || "INR"

  const handleSetupSubmit = (event) => {
    event.preventDefault()
    const nextAccount = {
      capital:         Number(setupForm.capital),
      capitalCurrency: setupForm.capitalCurrency,
      riskPercent:     Number(setupForm.riskPercent),
      usdtInr:         Number(setupForm.usdtInr),
      marketId:        setupForm.marketId
    }
    if (
      nextAccount.capital <= 0 || nextAccount.riskPercent <= 0 ||
      !["INR", "USD"].includes(nextAccount.capitalCurrency) ||
      (nextAccount.capitalCurrency === "INR" && nextAccount.usdtInr <= 0) ||
      !MARKETS[nextAccount.marketId]
    ) return
    localStorage.setItem(ACCOUNT_STORAGE_KEY, JSON.stringify(nextAccount))
    setAccount(nextAccount)
    setSentSignals([])
    setLastUpdated(null)
    setError("")
  }

  const handleStopEngine = async () => {
    const confirmed = window.confirm(
      "Stop the SMT trading engine? This does not close MT5 or any existing positions."
    )
    if (!confirmed) return

    setStoppingEngine(true)
    try {
      const response = await fetch(`${ENGINE_BASE}/system/stop`, {
        method: "POST",
        headers: { "X-SMT-Shutdown": "confirm" },
        signal: AbortSignal.timeout(5000),
      })
      if (!response.ok) throw new Error("Shutdown request was rejected")
      setError("SMT engine stopped. MT5 and existing positions remain open; close this dashboard tab when finished.")
      setLastUpdated(null)
    } catch {
      setStoppingEngine(false)
      setError("Unable to stop SMT from the dashboard. The engine may already be offline.")
    }
  }

  // License gate: block the app until activated (backend reachable + not activated)
  if (license && license.activated === false) {
    return <Activation status={license} onActivated={setLicense} />
  }

  return (
    <main className="dashboard-shell">

      {/* ── Setup overlay ───────────────────────────────────────────────────── */}
      {!isAccountReady && (
        <div className="setup-overlay" role="dialog" aria-modal="true">
          <form className="setup-panel" onSubmit={handleSetupSubmit}>
            <div>
              <p className="eyebrow">Startup setup</p>
              <h2>Trading capital</h2>
            </div>
            <div className="currency-toggle" role="group" aria-label="Capital currency">
              <span className={setupForm.capitalCurrency === "USD" ? "active" : ""}>USD</span>
              <label className="switch-control">
                <input
                  checked={setupForm.capitalCurrency === "INR"} type="checkbox"
                  onChange={(e) => setSetupForm({
                    ...setupForm, capitalCurrency: e.target.checked ? "INR" : "USD"
                  })}
                />
                <span className="switch-track"><span className="switch-thumb" /></span>
              </label>
              <span className={setupForm.capitalCurrency === "INR" ? "active" : ""}>INR</span>
            </div>
            <label>Capital deployed
              <input min="1" required step="1" type="number" value={setupForm.capital}
                onChange={(e) => setSetupForm({ ...setupForm, capital: e.target.value })} />
            </label>
            {setupForm.capitalCurrency === "INR" && (
              <label>USDT to INR rate
                <input min="1" required step="0.01" type="number" value={setupForm.usdtInr}
                  onChange={(e) => setSetupForm({ ...setupForm, usdtInr: e.target.value })} />
              </label>
            )}
            <label>Risk per trade
              <input min="0.1" required step="0.1" type="number" value={setupForm.riskPercent}
                onChange={(e) => setSetupForm({ ...setupForm, riskPercent: e.target.value })} />
            </label>
            <label>Market
              <select value={setupForm.marketId}
                onChange={(e) => setSetupForm({ ...setupForm, marketId: e.target.value })}>
                {MARKET_OPTIONS.map((o) => (
                  <option key={o.id} value={o.id}>{o.label}</option>
                ))}
              </select>
            </label>
            <button type="submit">Start dashboard</button>
          </form>
        </div>
      )}

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 16px", height: "64px", flexShrink: 0,
        background: "linear-gradient(90deg, #0a0f1a 0%, #0d1520 60%, #0a1628 100%)",
        borderBottom: "1px solid #1a2744",
        boxShadow: "0 2px 20px rgba(0,0,0,0.4)"
      }}>

        {/* ── Logo — clicks to Dashboard ── */}
        <button
          type="button"
          onClick={() => setActiveTab(TABS.dashboard)}
          style={{
            display: "flex", alignItems: "center", gap: "10px",
            flexShrink: 0, background: "none", border: "none",
            cursor: "pointer", padding: "4px 8px", borderRadius: "10px",
            transition: "background 0.15s",
            outline: activeTab === TABS.dashboard ? "1.5px solid #4dabf7" : "none",
            boxShadow: activeTab === TABS.dashboard ? "0 0 10px #4dabf730" : "none",
          }}
          title="Go to Dashboard"
        >
          <img
            src={logoSrc}
            alt="Smart Money Trader"
            style={{ height: "44px", width: "auto", objectFit: "contain",
              filter: "drop-shadow(0 0 8px rgba(32,178,80,0.3))" }}
          />
          <div style={{ lineHeight: 1.1, textAlign: "left" }}>
            <div style={{ fontSize: "15px", fontWeight: 800, color: "#e6edf3",
              letterSpacing: "0.3px" }}>
              Smart Money Trader
            </div>
            <div style={{ fontSize: "10px", color: "#8b949e", letterSpacing: "0.5px" }}>
              {market.label} · {lastUpdated
                ? `Updated ${lastUpdated.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" })} IST`
                : "Connecting…"}
            </div>
          </div>
        </button>

        {/* ── Nav tabs ── */}
        <nav style={{ display: "flex", alignItems: "center", gap: "2px" }}>
          {[
            { key: TABS.pnl,       label: "P&L",        icon: "📈" },
            { key: TABS.learning,  label: "Learning",   icon: "🧠" },
            { key: TABS.sentiment, label: "Sentiment",  icon: "🌍" },
            { key: TABS.history,   label: "History",    icon: "📋" },
            { key: TABS.mt5,       label: "MT5",        icon: "🤖" },
            { key: TABS.tester,    label: "Tester",     icon: "🧪" },
            { key: TABS.builder,   label: "Builder",    icon: "🛠️" },
            { key: TABS.money,     label: "Money",      icon: "💰" }
          ].map(tab => (
            <button
              key={tab.key}
              type="button"
              onClick={() => setActiveTab(tab.key)}
              style={{
                display: "flex", alignItems: "center", gap: "4px",
                padding: "6px 9px", borderRadius: "8px", border: "none",
                fontSize: "13px", fontWeight: activeTab === tab.key ? 700 : 500,
                cursor: "pointer", transition: "all 0.15s ease",
                background: activeTab === tab.key
                  ? "linear-gradient(135deg, #1a3a5c, #0d2d4a)"
                  : "transparent",
                color: activeTab === tab.key ? "#4dabf7" : "#8b949e",
                borderBottom: activeTab === tab.key ? "2px solid #4dabf7" : "2px solid transparent",
              }}
            >
              <span style={{ fontSize: "14px" }}>{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </nav>

        {/* ── Right actions ── */}
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexShrink: 0 }}>

          {/* Open signals count pill */}
          <div style={{
            display: "flex", alignItems: "center", gap: "6px",
            padding: "6px 10px", background: "#0d1117",
            border: "1px solid #1c2128", borderRadius: "8px", fontSize: "12px"
          }}>
            <div style={{ textAlign: "center" }}>
              <div style={{ color: "#8b949e", fontSize: "10px", letterSpacing: "0.5px", textTransform: "uppercase" }}>Open</div>
              <div style={{ color: sentSignals.filter(s => s.outcome === "OPEN").length > 0 ? "#69db7c" : "#e6edf3", fontWeight: 700 }}>
                {sentSignals.filter(s => s.outcome === "OPEN").length}
              </div>
            </div>
          </div>

          {/* Engine status */}
          <div style={{
            display: "flex", alignItems: "center", gap: "6px",
            padding: "6px 12px", borderRadius: "8px",
            background: !error && lastUpdated ? "rgba(32,178,80,0.1)" : "rgba(255,100,100,0.1)",
            border: `1px solid ${!error && lastUpdated ? "rgba(32,178,80,0.3)" : "rgba(255,100,100,0.3)"}`,
            fontSize: "12px",
            color: !error && lastUpdated ? "#69db7c" : "#f85149"
          }}>
            <span style={{
              width: "7px", height: "7px", borderRadius: "50%",
              background: !error && lastUpdated ? "#69db7c" : "#f85149",
              boxShadow: !error && lastUpdated ? "0 0 6px #69db7c" : "0 0 6px #f85149",
              animation: !error && lastUpdated ? "pulse 2s infinite" : "none"
            }} />
            {engineStatusLabel}
          </div>
          <button
            type="button"
            onClick={handleStopEngine}
            disabled={stoppingEngine}
            title="Stop the SMT engine without closing MT5 or its positions"
            style={{
              padding: "6px 10px", borderRadius: "8px", cursor: stoppingEngine ? "wait" : "pointer",
              border: "1px solid #a62b36", background: "rgba(248,81,73,0.12)",
              color: "#ff7b72", fontSize: "12px", fontWeight: 700, opacity: stoppingEngine ? 0.6 : 1,
            }}
          >
            {stoppingEngine ? "Stopping…" : "Stop engine"}
          </button>
        </div>
      </header>

      {/* ── Stats ──────────────────────────────────────────────────────────── */}


      {error && <div className="notice">{error}</div>}

      {/* ── Tabs ───────────────────────────────────────────────────────────── */}


      {/* ── Live Dashboard ─────────────────────────────────────────────────── */}
      {activeTab === TABS.dashboard ? (
        <section className="workspace-grid">

          {/* Chart — shows selected market */}
          <div className="chart-panel">
            <EventsTicker />
            <div className="chart-stage">
              <SessionOverlay height={CHART_HEIGHT}>
                <TradingViewChart key={market.chartSymbol} height={CHART_HEIGHT} symbol={market.chartSymbol} />
              </SessionOverlay>
            </div>
          </div>

          {/* Signals panel — shows ALL markets */}
          <aside className="signals-panel">
            <div className="panel-heading">
              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                <p className="eyebrow" style={{ marginBottom: 0 }}>Confirmed signals</p>
                <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                  {Object.values(MARKETS).map(m => (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => setChartMarketId(m.id)}
                      style={{
                        padding:      "3px 10px",
                        borderRadius: "6px",
                        border:       `1px solid ${
                          (chartMarketId || account?.marketId) === m.id ? "#4dabf7" : "#30363d"
                        }`,
                        background:   (chartMarketId || account?.marketId) === m.id
                          ? "rgba(77,171,247,0.15)"
                          : "rgba(255,255,255,0.04)",
                        color:        (chartMarketId || account?.marketId) === m.id ? "#4dabf7" : "#8b949e",
                        fontSize:     "11px",
                        fontWeight:   (chartMarketId || account?.marketId) === m.id ? 700 : 500,
                        cursor:       "pointer",
                        transition:   "all 0.15s"
                      }}
                    >
                      {m.id}
                    </button>
                  ))}
                </div>
                <h2 style={{ margin: 0 }}>Live signals</h2>
              </div>
              <span>{sentSignals.filter(s => s.outcome === "OPEN").length}</span>
            </div>

            <div className="signal-list">
              {sentSignals.filter(s => s.outcome === "OPEN").length > 0 ? (
                sentSignals.filter(s => s.outcome === "OPEN").map((signal, index) => {
                  const sigMarket = Object.values(MARKETS).find(m => m.apiSymbol === signal.symbol) || market
                  const sentTime  = signal.timestamp
                    ? new Date(Number(signal.timestamp)).toLocaleTimeString("en-IN", {
                        hour: "2-digit", minute: "2-digit", second: "2-digit",
                        timeZone: "Asia/Kolkata"
                      }) + " IST"
                    : signal.sent_at || "--"
                  return (
                    <div key={`live-${signal.timestamp}-${index}`}>
                      <div style={{
                        display: "flex", alignItems: "center",
                        justifyContent: "space-between",
                        marginBottom: "4px"
                      }}>
                        <AssetBadge symbol={signal.symbol} />
                        <span style={{ fontSize: "10px", color: "#4dabf7", fontFamily: "monospace" }}>
                          📲 {sentTime}
                        </span>
                      </div>
                      <SignalCard
                        account={account}
                        market={sigMarket}
                        signal={{ ...signal, confluences: signal.confluences || [] }}
                      />
                    </div>
                  )
                })
              ) : (
                <div className="empty-state">
                  No active signals. Next confirmed signal will appear here.
                </div>
              )}
            </div>
          </aside>
        </section>

      ) : activeTab === TABS.pnl ? (

        /* ── P&L tab ─────────────────────────────────────────────────────── */
        <PnLDashboard
          pnlData={pnlData}
          loading={pnlLoading}
        />

      ) : activeTab === TABS.learning ? (

        /* ── Learning tab — full dashboard with Strategy Tuner ────────────── */
        <LearningDashboard />

      ) : activeTab === TABS.sentiment ? (

        /* ── Sentiment tab ─────────────────────────────────────────────────── */
        <section className="history-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">Market sentiment</p><h2>Sentiment Analysis</h2></div>
          </div>
          {sentimentLoading ? (
            <div className="empty-state">Fetching sentiment data…</div>
          ) : sentimentData ? (
            <div style={{ padding: "20px", display: "flex", flexDirection: "column", gap: "16px" }}>

              {/* Overview */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px,1fr))", gap: "12px" }}>
                {[
                  { label: "Overall",       value: sentimentData.overall_label,    color: sentimentData.total_score >= 1 ? "#69db7c" : sentimentData.total_score <= -1 ? "#f85149" : "#c9d1d9" },
                  { label: "Fear & Greed",  value: sentimentData.fear_greed_score != null ? `${sentimentData.fear_greed_score}/100` : "—", color: "#c9d1d9" },
                  { label: "F&G Label",     value: sentimentData.fear_greed_label || "—", color: "#c9d1d9" },
                  { label: "Geo Risk",      value: sentimentData.geo_risk || "—",  color: sentimentData.geo_risk === "HIGH" ? "#f85149" : sentimentData.geo_risk === "MEDIUM" ? "#f0b429" : "#69db7c" },
                  { label: "BUY Filter",    value: sentimentData.signal_filter?.BUY  ? "✅ Allowed" : "🚫 Blocked", color: sentimentData.signal_filter?.BUY  ? "#69db7c" : "#f85149" },
                  { label: "SELL Filter",   value: sentimentData.signal_filter?.SELL ? "✅ Allowed" : "🚫 Blocked", color: sentimentData.signal_filter?.SELL ? "#69db7c" : "#f85149" }
                ].map(c => (
                  <div key={c.label} style={{ background: "#0d1117", border: "1px solid #1c2128", borderRadius: "8px", padding: "14px 16px" }}>
                    <div style={{ fontSize: "11px", color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "6px" }}>{c.label}</div>
                    <div style={{ fontSize: "18px", fontWeight: 700, color: c.color }}>{c.value}</div>
                  </div>
                ))}
              </div>

              {/* Fear & Greed bias */}
              {sentimentData.fear_greed_bias && (
                <div style={{ background: "#161b22", border: "1px solid #1c2128", borderRadius: "8px", padding: "14px 16px" }}>
                  <div style={{ fontSize: "11px", color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "6px" }}>Smart Money Interpretation</div>
                  <div style={{ color: "#c9d1d9", fontSize: "14px" }}>{sentimentData.fear_greed_bias}</div>
                </div>
              )}

              {/* News keywords */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                {sentimentData.bullish_hits?.length > 0 && (
                  <div style={{ background: "rgba(105,219,124,0.06)", border: "1px solid #69db7c33", borderRadius: "8px", padding: "14px 16px" }}>
                    <div style={{ fontSize: "11px", color: "#69db7c", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "8px" }}>📈 Bullish Keywords</div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                      {sentimentData.bullish_hits.map(k => (
                        <span key={k} style={{ background: "#69db7c22", color: "#69db7c", padding: "2px 8px", borderRadius: "4px", fontSize: "12px" }}>{k}</span>
                      ))}
                    </div>
                  </div>
                )}
                {sentimentData.bearish_hits?.length > 0 && (
                  <div style={{ background: "rgba(248,81,73,0.06)", border: "1px solid #f8514933", borderRadius: "8px", padding: "14px 16px" }}>
                    <div style={{ fontSize: "11px", color: "#f85149", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "8px" }}>📉 Bearish Keywords</div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                      {sentimentData.bearish_hits.map(k => (
                        <span key={k} style={{ background: "#f8514922", color: "#f85149", padding: "2px 8px", borderRadius: "4px", fontSize: "12px" }}>{k}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* Geo hits */}
              {sentimentData.geo_hits?.length > 0 && (
                <div style={{ background: "rgba(240,180,41,0.06)", border: "1px solid #f0b42933", borderRadius: "8px", padding: "14px 16px" }}>
                  <div style={{ fontSize: "11px", color: "#f0b429", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "8px" }}>⚠️ Geopolitical Keywords</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                    {sentimentData.geo_hits.map(k => (
                      <span key={k} style={{ background: "#f0b42922", color: "#f0b429", padding: "2px 8px", borderRadius: "4px", fontSize: "12px" }}>{k}</span>
                    ))}
                  </div>
                </div>
              )}

              <div style={{ fontSize: "11px", color: "#8b949e", textAlign: "right" }}>
                Last fetched: {sentimentData.fetched_at}
              </div>
            </div>
          ) : (
            <div className="empty-state">Sentiment data unavailable.</div>
          )}
        </section>

      ) : activeTab === TABS.history ? (

        /* ── History tab ─────────────────────────────────────────────────── */
        (() => {
          // Filters (hFilter / mt5AcctFilter) are real React state declared at App level
          // Convert MT5 trades into history-row shape (apply account-type sub-filter)
          const mt5Rows = mt5Trades
            .filter(t => t.mt5_state === "closed" || t.mt5_state === "active")
            .filter(t => mt5AcctFilter === "ALL_ACCT" || (t.mode||"demo").toUpperCase() === mt5AcctFilter)
            .map(t => {
              const realized = (t.realized_usd !== null && t.realized_usd !== undefined)
                ? t.realized_usd : null
              let outcome
              if (t.mt5_state === "active") {
                outcome = "ACTIVE"
              } else if (t.status === "cancelled") {
                outcome = "CANCELLED"
              } else if (realized !== null) {
                outcome = realized > 0 ? "WIN" : realized < 0 ? "LOSS" : "CLOSED"
              } else {
                outcome = "CLOSED"  // P&L unknown — don't falsely show LOSS
              }
              // Timestamp: prefer numeric timestamp, fall back to parsing time_ist
              let ts = t.timestamp
              if (!ts && t.time_ist) {
                // "2026-06-06 16:33 IST" → parse as UTC approx
                ts = new Date(t.time_ist.replace(" IST", "")).getTime() || undefined
              }
              // Derive strategy_tag from saved field, or fall back to setup string mapping
              let strat_tag = t.strategy_tag || ""
              if (!strat_tag && t.setup) {
                if (t.setup.includes("FVG") && t.setup.includes("EMA")) strat_tag = "HTF_ICT_Intraday"
                else if (t.setup.includes("EMA Pullback"))              strat_tag = "EMA20_Intraday"
                else if (t.setup.includes("Sweep"))                     strat_tag = "ICT_Scalping"
                else if (t.setup.includes("BB") || t.setup.includes("RSI")) strat_tag = "momentum_scalp"
              }
              if (!strat_tag) {
                strat_tag = (t.source === "mt5_direct" || t.source === "mt5_history")
                  ? "MT5_Manual" : "HTF_ICT_Intraday"   // default to primary strategy
              }
              return {
                _source:       "mt5",
                timestamp:     ts,
                time_ist:      t.time_ist || "",
                symbol:        t.symbol,
                signal:        t.direction,
                strategy_tag:  strat_tag,
                entry:         t.entry,
                sl:            t.sl,
                tp:            t.tp,
                lot:           t.lot,
                outcome,
                realized_usd:  realized,
                mt5_state:     t.mt5_state,
                ticket:        t.ticket,
              }
            })

          // Merge: signals + MT5 trades, sorted newest first
          const allRows = [...sentSignals, ...mt5Rows].sort((a, b) => {
            const ta = parseInt(a.timestamp) || 0
            const tb = parseInt(b.timestamp) || 0
            return tb - ta
          })

          const filtered = allRows.filter(s => {
            if (hFilter === "ALL") return true
            if (hFilter === "MT5") return s._source === "mt5"
            return s.outcome === hFilter
          })

          const wins   = allRows.filter(s => s.outcome === "WIN").length
          const losses = allRows.filter(s => s.outcome === "LOSS").length
          const open   = allRows.filter(s => s.outcome === "OPEN").length
          const total  = wins + losses
          const wr     = total > 0 ? (wins / total * 100).toFixed(1) : null
          const totalPnl = mt5Rows.reduce((sum, r) => sum + (r.realized_usd || 0), 0)

          return (
            <section className="history-panel">
              {/* Summary strip */}
              <div style={{ display:"flex", alignItems:"center", gap:16, padding:"14px 20px",
                borderBottom:"1px solid #1c2128", flexWrap:"wrap" }}>
                {[
                  { label:"Total",    value: allRows.length,  color:"#e6edf3" },
                  { label:"Wins",     value: wins,            color:"#69db7c" },
                  { label:"Losses",   value: losses,          color:"#f85149" },
                  { label:"Open",     value: open,            color:"#f0b429" },
                  { label:"Win Rate", value: wr ? `${wr}%` : "—", color: wr >= 50 ? "#69db7c" : "#f85149" },
                  { label:"MT5 P&L",  value: `${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(2)}`, color: totalPnl >= 0 ? "#69db7c" : "#f85149" },
                ].map(c => (
                  <div key={c.label} style={{ textAlign:"center", minWidth:70 }}>
                    <div style={{ fontSize:10, color:"#8b949e", textTransform:"uppercase", letterSpacing:".5px" }}>{c.label}</div>
                    <div style={{ fontSize:20, fontWeight:800, color:c.color }}>{c.value}</div>
                  </div>
                ))}

                {/* Live prices — BTC, ETH, Gold */}
                <div style={{ display:"flex", gap:12, margin:"0 auto" }}>
                  {[
                    { key:"BTCUSD",  label:"BTC",  color:"#f7931a" },
                    { key:"ETHUSD",  label:"ETH",  color:"#627eea" },
                    { key:"XAUUSD+", label:"Gold", color:"#f0b429" },
                  ].map(({ key, label, color }) => {
                    const p = livePrices[key]?.price
                    return (
                      <div key={key} style={{ textAlign:"center" }}>
                        <div style={{ fontSize:10, color:"#8b949e", textTransform:"uppercase", letterSpacing:".5px" }}>{label}</div>
                        <div style={{ fontSize:14, fontWeight:800, color, fontFamily:"monospace" }}>
                          {p ? `$${p.toLocaleString()}` : "—"}
                        </div>
                      </div>
                    )
                  })}
                </div>

                {/* Filter buttons */}
                <div style={{ display:"flex", gap:6 }}>
                  {["ALL","OPEN","WIN","LOSS","EXPIRED","CANCELLED","MT5"].map(f => (
                    <button key={f} onClick={() => setHFilter(f)}
                      style={{ padding:"5px 12px", borderRadius:6, border:"none", cursor:"pointer",
                        fontSize:12, fontWeight: hFilter===f ? 700 : 500,
                        background: hFilter===f ? "#1a3a5c" : "#161b22",
                        color: hFilter===f ? "#4dabf7" : "#8b949e" }}>
                      {f}
                    </button>
                  ))}
                  {/* MT5 account-type sub-filter — shown whenever MT5 rows are visible */}
                  <span style={{ color:"#30363d", margin:"0 4px" }}>|</span>
                  {[
                    { key:"ALL_ACCT", label:"All Accounts",  color:"#8b949e" },
                    { key:"DEMO",     label:"🟡 Demo",       color:"#f0b429" },
                    { key:"LIVE",     label:"🔴 Real Live",  color:"#f85149" },
                    { key:"PAPER",    label:"📋 Paper",      color:"#3fb950" },
                  ].map(f => (
                    <button key={f.key}
                      onClick={() => setMt5AcctFilter(f.key)}
                      style={{ padding:"5px 10px", borderRadius:6, border:"none", cursor:"pointer",
                        fontSize:11, fontWeight: mt5AcctFilter===f.key ? 700 : 400,
                        background: mt5AcctFilter===f.key ? "rgba(88,166,255,0.08)" : "#161b22",
                        color: mt5AcctFilter===f.key ? f.color : "#555d68",
                        outline: mt5AcctFilter===f.key ? `1px solid ${f.color}40` : "none" }}>
                      {f.label}
                    </button>
                  ))}
                </div>
              </div>

              {historyError && <div className="notice history-notice">{historyError}</div>}

              <div className="history-table-wrap">
                <table className="history-table">
                  <thead>
                    <tr>
                      <th>Date / Time</th>
                      <th>Symbol</th>
                      <th>Signal</th>
                      <th>Strategy</th>
                      <th>Entry</th>
                      <th>SL</th>
                      <th>TP</th>
                      <th>Style</th>
                      <th>Status</th>
                      <th>Result ($)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((signal, index) => {
                      const isMt5Row   = signal._source === "mt5"
                      const direction  = signal.signal === "BUY" ? "buy" : "sell"
                      // For MT5 rows, time_ist is already formatted; for signals use formatSignalTime
                      let dateDisplay, timeDisplay
                      if (isMt5Row && signal.time_ist) {
                        const parts = signal.time_ist.replace(" IST","").split(" ")
                        dateDisplay = parts[0] || "--"
                        timeDisplay = parts[1] || "--"
                      } else {
                        const signalTime = formatSignalTime(signal.timestamp)
                        dateDisplay = signalTime.date
                        timeDisplay = signalTime.time
                      }

                      // Strategy badge — colour-coded per strategy type
                      const strategyColors = {
                        "HTF_ICT_Intraday":  { bg: "#1a3a5c", color: "#58a6ff",  label: "1H FVG + EMA",     style: "Intraday" },
                        "EMA20_Intraday":    { bg: "#1a3a2a", color: "#56d364",  label: "20 EMA Pullback",  style: "Intraday" },
                        "ICT_Scalping":      { bg: "#3a1a3a", color: "#d2a8ff",  label: "ICT Scalp",        style: "Scalp"    },
                        "ICT_Intraday":      { bg: "#1a3a5c", color: "#79c0ff",  label: "ICT Intraday",     style: "Intraday" },
                        "ICT_Swing":         { bg: "#3a2a1a", color: "#ffa657",  label: "ICT Swing",        style: "Swing"    },
                        "momentum_scalp":    { bg: "#2a1a3a", color: "#c084fc",  label: "BB+RSI Scalper",   style: "Scalp"    },
                        "MT5_Manual":        { bg: "#1c2128", color: "#8b949e",  label: "MT5 Manual",       style: "—"        },
                        "MT5_Bot":           { bg: "#1a2a1a", color: "#56d364",  label: "MT5 Bot",          style: "—"        },
                        "ETH_Momentum":      { bg: "#1a2a1a", color: "#3fb950",  label: "ETH Momentum",     style: "Swing"    },
                        "eth_momentum":      { bg: "#1a2a1a", color: "#3fb950",  label: "ETH Momentum",     style: "Swing"    },
                        "BTC_Momentum":      { bg: "#1a1a0d", color: "#f7931a",  label: "BTC Momentum",     style: "Swing"    },
                        "btc_momentum":      { bg: "#1a1a0d", color: "#f7931a",  label: "BTC Momentum",     style: "Swing"    },
                        "london_breakout":   { bg: "#1a2a3a", color: "#79c0ff",  label: "London Breakout",  style: "Intraday" },
                        "smc_swing":         { bg: "#2a1a2a", color: "#d2a8ff",  label: "SMC Swing",        style: "Swing"    },
                        "SMC_Swing":         { bg: "#2a1a2a", color: "#d2a8ff",  label: "SMC Swing",        style: "Swing"    },
                        "htf_liquidity_sweep": { bg: "#2a1a10", color: "#f0a030", label: "HTF Sweep",        style: "Swing"    },
                        "HTF_Sweep":         { bg: "#2a1a10", color: "#f0a030",  label: "HTF Sweep",        style: "Swing"    },
                        "oliver_velez_swing": { bg: "#0d1f2d", color: "#58a6ff", label: "OV Swing",          style: "Swing"    },
                        "OV_Swing":          { bg: "#0d1f2d", color: "#58a6ff",  label: "OV Swing",          style: "Swing"    },
                        "oliver_velez_btc":  { bg: "#1a1a0d", color: "#f7931a", label: "OV BTC",             style: "Swing"    },
                        "OV_BTC":            { bg: "#1a1a0d", color: "#f7931a",  label: "OV BTC",             style: "Swing"    },
                      }
                      const tag     = signal.strategy_tag || ""
                      const setup   = signal.setup || ""
                      const derivedTag = tag || (
                        (setup.includes("FVG") && setup.includes("EMA")) ? "HTF_ICT_Intraday" :
                        (setup.includes("EMA Pullback"))                  ? "EMA20_Intraday"   :
                        (setup.includes("Sweep"))                         ? "ICT_Scalping"     :
                        ((signal.confluences || []).some(c => c.includes("FVG"))) ? "HTF_ICT_Intraday" : ""
                      )
                      const badge      = strategyColors[derivedTag]
                      const badgeLabel = badge ? badge.label : (setup || derivedTag || "—")

                      // Style column: Scalp / Intraday / Swing
                      const styleVal = badge ? badge.style : (
                        derivedTag.toLowerCase().includes("scalp") ? "Scalp" :
                        derivedTag.toLowerCase().includes("swing") ? "Swing" :
                        derivedTag ? "Intraday" : "—"
                      )
                      const styleColor = styleVal === "Scalp" ? "#c084fc" : styleVal === "Swing" ? "#ffa657" : styleVal === "Intraday" ? "#58a6ff" : "#8b949e"

                      // Result column
                      const realized = signal.realized_usd
                      const hasResult = realized !== null && realized !== undefined
                      const resultColor = hasResult ? (realized > 0 ? "#69db7c" : realized < 0 ? "#f85149" : "#8b949e") : "#8b949e"

                      return (
                        <tr key={`${signal._source||"sig"}-${signal.symbol}-${signal.timestamp || index}-${index}`}
                          style={{ borderLeft: isMt5Row ? "2px solid #30363d" : "none" }}>
                          <td style={{ whiteSpace:"nowrap" }}>
                            <div style={{ fontSize:13 }}>{dateDisplay}</div>
                            <div style={{ fontSize:11, color:"#8b949e" }}>{timeDisplay}</div>
                          </td>
                          <td>
                            <div style={{ fontWeight:700 }}>{signal.symbol || "--"}</div>
                            {isMt5Row
                              ? <div style={{ fontSize:10, color:"#f0b429" }}>MT5</div>
                              : <div style={{ fontSize:11, color:"#8b949e" }}>{signal.timeframe || ""}</div>}
                          </td>
                          <td>
                            <span className={`signal-pill ${direction}`}>{signal.signal || "—"}</span>
                          </td>
                          <td>
                            <span style={{
                              display: "inline-block",
                              background: badge ? badge.bg : "#1c2128",
                              color:      badge ? badge.color : "#8b949e",
                              padding:    "2px 7px", borderRadius: 4,
                              fontSize:   11, fontWeight: 600, whiteSpace: "nowrap",
                            }}>
                              {badgeLabel}
                            </span>
                          </td>
                          <td style={{ fontFamily:"monospace" }}>{formatPrice(signal.entry)}</td>
                          <td style={{ fontFamily:"monospace", color:"#f85149" }}>{signal.sl ? formatPrice(signal.sl) : "—"}</td>
                          <td style={{ fontFamily:"monospace", color:"#69db7c" }}>{signal.tp ? formatPrice(signal.tp) : "—"}</td>

                          {/* Style: Scalp / Intraday / Swing */}
                          <td style={{ textAlign:"center" }}>
                            <span style={{ color: styleColor, fontSize:11, fontWeight:600 }}>{styleVal}</span>
                          </td>

                          {/* Status (was Outcome) */}
                          <td><OutcomePill outcome={signal.outcome} points={signal.points} /></td>

                          {/* Result ($) — realized P&L for MT5 trades */}
                          <td style={{ textAlign:"right", fontFamily:"monospace", fontWeight:700, color: resultColor }}>
                            {hasResult
                              ? `${realized > 0 ? "+" : ""}$${realized.toFixed(2)}`
                              : <span style={{ color:"#8b949e", fontSize:11 }}>—</span>}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>

                {isHistoryLoading && <div className="empty-state">Loading signal history…</div>}
                {!isHistoryLoading && filtered.length === 0 && (
                  <div className="empty-state">No signals match the selected filter.</div>
                )}
              </div>

              {/* ── Monthly Performance Table ─────────────────────────────── */}
              {(() => {
                // Helper: get month key from a timestamp (ms or s)
                const toMonthKey = (ts) => {
                  if (!ts) return null
                  const n  = parseInt(ts)
                  const ms = n > 1e12 ? n : n * 1000
                  const ist = new Date(ms + 5.5 * 3600 * 1000)
                  return `${ist.getUTCFullYear()}-${String(ist.getUTCMonth()+1).padStart(2,"0")}`
                }
                const toMonthLabel = (ts) => {
                  const n  = parseInt(ts)
                  const ms = n > 1e12 ? n : n * 1000
                  const ist = new Date(ms + 5.5 * 3600 * 1000)
                  return ist.toLocaleString("default",{month:"long",year:"numeric",timeZone:"UTC"})
                }

                // Group signals by month
                const monthMap = {}
                sentSignals.forEach(s => {
                  if (!s.timestamp) return
                  const key = toMonthKey(s.timestamp)
                  if (!key) return
                  if (!monthMap[key]) monthMap[key] = { label: toMonthLabel(s.timestamp), signals:[], mt5closed:[] }
                  monthMap[key].signals.push(s)
                })

                // Monthly table account-type filter (reuses the same History filter)
                const monthMt5Filter = mt5AcctFilter

                // Group closed MT5 trades by month for Net P&L ($)
                mt5Trades.forEach(t => {
                  if (t.mt5_state !== "closed" || t.realized_usd == null) return
                  if (monthMt5Filter !== "ALL_ACCT" && (t.mode||"demo").toUpperCase() !== monthMt5Filter) return
                  const key = toMonthKey(t.timestamp)
                  if (!key) return
                  if (!monthMap[key]) monthMap[key] = { label: toMonthLabel(t.timestamp), signals:[], mt5closed:[] }
                  monthMap[key].mt5closed.push(t)
                })

                const months = Object.keys(monthMap).sort().reverse()
                if (months.length === 0) return null

                const monthRows = months.map(key => {
                  const { label, signals: ms, mt5closed } = monthMap[key]
                  const wins   = ms.filter(s => s.outcome === "WIN")
                  const losses = ms.filter(s => s.outcome === "LOSS")
                  const total  = wins.length + losses.length
                  const wr     = total > 0 ? (wins.length / total * 100).toFixed(1) : null

                  const netPts    = ms.reduce((acc, s) => acc + (parseFloat(s.points) || 0), 0)
                  const netUsd    = mt5closed.reduce((acc, t) => acc + (t.realized_usd || 0), 0)
                  const hasNetUsd = mt5closed.length > 0

                  const maxProfit = wins.length > 0
                    ? Math.max(...wins.map(s => parseFloat(s.points) || 0)) : 0

                  let maxDD = 0, curDD = 0
                  ms.filter(s => ["WIN","LOSS"].includes(s.outcome))
                    .sort((a,b) => (a.timestamp||0)-(b.timestamp||0))
                    .forEach(s => {
                      if (s.outcome === "LOSS") { curDD += Math.abs(parseFloat(s.points)||0); if(curDD>maxDD) maxDD=curDD }
                      else curDD = 0
                    })

                  const tagWins = {}
                  wins.forEach(s => {
                    const tag = s.setup || s.strategy_tag || "1H FVG+EMA"
                    tagWins[tag] = (tagWins[tag]||0) + 1
                  })
                  const bestStrategy = Object.keys(tagWins).sort((a,b)=>tagWins[b]-tagWins[a])[0] || "—"

                  return { key, label, total: ms.length, wins: wins.length, losses: losses.length,
                           wr, netPts, netUsd, hasNetUsd, maxProfit, maxDD, bestStrategy }
                })

                return (
                  <div style={{ marginTop:32, padding:"0 0 24px" }}>
                    <div style={{ padding:"12px 20px", borderTop:"1px solid #1c2128",
                      borderBottom:"1px solid #1c2128", marginBottom:12,
                      display:"flex", alignItems:"center", gap:12 }}>
                      <span style={{ fontSize:13, fontWeight:700, color:"#e6edf3" }}>
                        📅 Monthly Performance Record
                      </span>
                      <span style={{ fontSize:11, color:"#8b949e" }}>
                        All strategies · Auto-refreshes each month
                      </span>
                      {monthMt5Filter !== "ALL_ACCT" && (
                        <span style={{ fontSize:11, padding:"2px 8px", borderRadius:4,
                          background: monthMt5Filter==="LIVE" ? "rgba(248,81,73,0.12)" : monthMt5Filter==="DEMO" ? "rgba(240,180,41,0.12)" : "rgba(63,185,80,0.12)",
                          color: monthMt5Filter==="LIVE" ? "#f85149" : monthMt5Filter==="DEMO" ? "#f0b429" : "#3fb950",
                          fontWeight:600 }}>
                          {monthMt5Filter==="LIVE" ? "🔴 Real Live P&L" : monthMt5Filter==="DEMO" ? "🟡 Demo P&L" : "📋 Paper P&L"}
                        </span>
                      )}
                    </div>

                    <div style={{ overflowX:"auto", padding:"0 8px" }}>
                      <table style={{ width:"100%", borderCollapse:"collapse", fontSize:12 }}>
                        <thead>
                          <tr style={{ color:"#8b949e", textTransform:"uppercase",
                            fontSize:10, letterSpacing:".5px" }}>
                            {["Month","Trades","Wins","Losses","Win Rate",
                              "Net P/L (PTS)","Net P&L ($)","Max Profit (PTS)","Max Drawdown (PTS)","Best Strategy"
                            ].map(h => (
                              <th key={h} style={{ padding:"8px 12px", textAlign:"left",
                                borderBottom:"1px solid #1c2128", whiteSpace:"nowrap" }}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {monthRows.map((row, i) => {
                            const nowIst = new Date(Date.now() + 5.5 * 3600 * 1000)
                            const currentKey = `${nowIst.getUTCFullYear()}-${String(nowIst.getUTCMonth()+1).padStart(2,"0")}`
                            const isCurrentMonth = row.key === currentKey
                            return (
                              <tr key={row.key} style={{
                                background: isCurrentMonth ? "rgba(77,171,247,.04)" : "transparent",
                                borderBottom:"1px solid #1c2128",
                              }}>
                                <td style={{ padding:"10px 12px", fontWeight:700, color:"#e6edf3",
                                  whiteSpace:"nowrap" }}>
                                  {row.label}
                                  {isCurrentMonth && (
                                    <span style={{ marginLeft:6, fontSize:10, color:"#4dabf7",
                                      background:"rgba(77,171,247,.12)", padding:"1px 6px",
                                      borderRadius:4 }}>Current</span>
                                  )}
                                </td>
                                <td style={{ padding:"10px 12px", color:"#e6edf3" }}>{row.total}</td>
                                <td style={{ padding:"10px 12px", color:"#69db7c", fontWeight:600 }}>{row.wins}</td>
                                <td style={{ padding:"10px 12px", color:"#f85149", fontWeight:600 }}>{row.losses}</td>
                                <td style={{ padding:"10px 12px" }}>
                                  {row.wr !== null ? (
                                    <span style={{
                                      fontWeight:700,
                                      color: parseFloat(row.wr) >= 60 ? "#69db7c"
                                           : parseFloat(row.wr) >= 45 ? "#f0b429" : "#f85149"
                                    }}>{row.wr}%</span>
                                  ) : <span style={{ color:"#8b949e" }}>—</span>}
                                </td>
                                {/* Net P/L (PTS) */}
                                <td style={{ padding:"10px 12px", fontFamily:"monospace", fontWeight:700,
                                  color: row.netPts >= 0 ? "#69db7c" : "#f85149" }}>
                                  {row.netPts >= 0 ? "+" : ""}{row.netPts.toFixed(2)}
                                </td>
                                {/* Net P&L ($) */}
                                <td style={{ padding:"10px 12px", fontFamily:"monospace", fontWeight:700,
                                  color: row.hasNetUsd ? (row.netUsd >= 0 ? "#69db7c" : "#f85149") : "#8b949e" }}>
                                  {row.hasNetUsd
                                    ? `${row.netUsd >= 0 ? "+" : ""}$${row.netUsd.toFixed(2)}`
                                    : "—"}
                                </td>
                                {/* Max Profit (PTS) */}
                                <td style={{ padding:"10px 12px", fontFamily:"monospace", color:"#69db7c" }}>
                                  +{row.maxProfit.toFixed(2)}
                                </td>
                                {/* Max Drawdown (PTS) */}
                                <td style={{ padding:"10px 12px", fontFamily:"monospace", color:"#f85149" }}>
                                  -{row.maxDD.toFixed(2)}
                                </td>
                                <td style={{ padding:"10px 12px" }}>
                                  <span style={{ background:"#1a3a5c", color:"#58a6ff",
                                    padding:"2px 8px", borderRadius:4, fontSize:11,
                                    fontWeight:600, whiteSpace:"nowrap" }}>
                                    {row.bestStrategy}
                                  </span>
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )
              })()}

            </section>
          )
        })()

      ) : activeTab === TABS.mt5 ? (

        /* ── MT5 Trader tab ──────────────────────────────────────────────── */
        <section style={{padding:"0"}}>
          <MT5Panel />
        </section>

      ) : activeTab === TABS.tester ? (

        /* ── Strategy Tester tab ─────────────────────────────────────────── */
        <StrategyTester />

      ) : activeTab === TABS.builder ? (

        /* ── Strategy Builder tab ────────────────────────────────────────── */
        <StrategyBuilder />

      ) : activeTab === TABS.money ? (

        /* ── Money Management tab ────────────────────────────────────────── */
        <MoneyManagement />

      ) : null}
    </main>
  )
}

export default App
