import { useState } from "react"

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(n, dec = 2) {
  if (n == null || !isFinite(Number(n))) return "—"
  return Number(n).toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec })
}

function fmtUsd(n, forceSign = true) {
  if (n == null || !isFinite(Number(n))) return "—"
  const v    = Number(n)
  const sign = forceSign ? (v >= 0 ? "+" : "-") : (v < 0 ? "-" : "")
  return `${sign}$${fmt(Math.abs(v))}`
}

function pnlColor(v) {
  const n = Number(v)
  return n > 0 ? "#3fb950" : n < 0 ? "#f85149" : "#8b949e"
}

const SYMBOL_ICON = { BTCUSD:"₿", ETHUSD:"Ξ", "XAUUSD+":"Au" }
const STRAT_LABEL = {
  HTF_ICT_Intraday: "1H FVG + EMA",
  EMA20_Intraday:   "20 EMA Pullback",
  ICT_Scalping:     "ICT Scalp",
  ICT_Intraday:     "ICT Intraday",
  ICT_Swing:        "ICT Swing",
  momentum_scalp:   "BB+RSI Scalper",
}

// ── Sub-components ────────────────────────────────────────────────────────────

function MetricCard({ label, value, sub, color, accent }) {
  return (
    <div style={{
      background: "#0d1117", border: `1px solid ${accent || "#1c2128"}`,
      borderTop: `3px solid ${accent || "#30363d"}`,
      borderRadius: "10px", padding: "16px 18px",
    }}>
      <div style={{ fontSize: 10, color: "#8b949e", textTransform: "uppercase",
        letterSpacing: ".6px", marginBottom: 8 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: color || "#e6edf3",
        fontFamily: "monospace" }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: "#8b949e", marginTop: 4 }}>{sub}</div>}
    </div>
  )
}

// ── Equity Curve (pure SVG, no dependencies) ──────────────────────────────────

function EquityCurve({ curve }) {
  if (!curve || curve.length < 2) return (
    <div style={{ textAlign:"center", color:"#8b949e", padding:"40px",
      background:"#0d1117", borderRadius:10, border:"1px solid #1c2128" }}>
      Not enough closed trades yet to draw an equity curve.
    </div>
  )

  const W = 900, H = 220, PAD = { top:16, right:24, bottom:32, left:60 }
  const iW = W - PAD.left - PAD.right
  const iH = H - PAD.top  - PAD.bottom

  const vals  = curve.map(p => p.pnl)
  const minV  = Math.min(0, ...vals)
  const maxV  = Math.max(0, ...vals)
  const range = maxV - minV || 1

  const xScale = (i) => PAD.left + (i / (curve.length - 1)) * iW
  const yScale = (v) => PAD.top  + iH - ((v - minV) / range) * iH

  // Build polyline points
  const pts = curve.map((p, i) => `${xScale(i)},${yScale(p.pnl)}`).join(" ")

  // Fill area under/over zero line
  const zeroY  = yScale(0)
  const firstX = xScale(0)
  const lastX  = xScale(curve.length - 1)
  const fillPts = `${firstX},${zeroY} ${pts} ${lastX},${zeroY}`

  // Y-axis ticks
  const tickCount = 5
  const yTicks = Array.from({ length: tickCount + 1 }, (_, i) =>
    minV + (range / tickCount) * i
  )

  // X-axis labels — show ~5 evenly spaced dates
  const xStep = Math.max(1, Math.floor(curve.length / 5))
  const xLabels = curve
    .filter((_, i) => i % xStep === 0 || i === curve.length - 1)
    .map((p, _, arr) => ({ date: p.date.slice(5), x: xScale(curve.indexOf(p)) }))

  const lastVal   = vals[vals.length - 1]
  const lineColor = lastVal >= 0 ? "#3fb950" : "#f85149"

  return (
    <div style={{ background:"#0d1117", border:"1px solid #1c2128",
      borderRadius:10, padding:"16px", overflowX:"auto" }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width:"100%", height:"auto", display:"block" }}>
        {/* Zero line */}
        <line x1={PAD.left} y1={zeroY} x2={W - PAD.right} y2={zeroY}
          stroke="#30363d" strokeWidth={1} strokeDasharray="4,4" />

        {/* Y-axis grid + labels */}
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={PAD.left} y1={yScale(v)} x2={W - PAD.right} y2={yScale(v)}
              stroke="#161b22" strokeWidth={1} />
            <text x={PAD.left - 6} y={yScale(v) + 4} textAnchor="end"
              fontSize={9} fill="#555d68">
              {v >= 0 ? "+" : ""}{v.toFixed(2)}
            </text>
          </g>
        ))}

        {/* X-axis labels */}
        {xLabels.map((l, i) => (
          <text key={i} x={l.x} y={H - 6} textAnchor="middle"
            fontSize={9} fill="#555d68">{l.date}</text>
        ))}

        {/* Fill */}
        <polygon points={fillPts}
          fill={lineColor} fillOpacity={0.08} />

        {/* Line */}
        <polyline points={pts}
          fill="none" stroke={lineColor} strokeWidth={2} strokeLinejoin="round" />

        {/* Last value dot */}
        <circle cx={xScale(curve.length-1)} cy={yScale(lastVal)}
          r={4} fill={lineColor} />
        <text x={xScale(curve.length-1)+8} y={yScale(lastVal)+4}
          fontSize={10} fill={lineColor} fontWeight="bold">
          {fmtUsd(lastVal)}
        </text>
      </svg>
    </div>
  )
}

// ── Tab config ────────────────────────────────────────────────────────────────

const TABS = [
  { key: "demo",  label: "Demo Account",  icon: "🟡", accent: "#f0b429", desc: "Live MT5 demo account — real-time data" },
  { key: "live",  label: "Real Account",  icon: "🔴", accent: "#f85149", desc: "Real money trades — from live trade history" },
  { key: "paper", label: "Paper Account", icon: "📋", accent: "#58a6ff", desc: "Simulated paper trades — no real money" },
]

// ── Main Component ────────────────────────────────────────────────────────────

export default function PnLDashboard({ pnlData, loading }) {
  const [activeTab, setActiveTab] = useState("demo")

  const tab = TABS.find(t => t.key === activeTab)

  if (loading) return (
    <section style={{ padding:32, textAlign:"center", color:"#8b949e" }}>
      Loading P&amp;L data…
    </section>
  )

  // Per-tab data
  const tabData = pnlData?.[activeTab]

  const { account = null, open_positions = [], closed_trades = [],
          equity_curve = [], by_symbol = {}, by_strategy = {}, metrics = {} } = tabData || {}

  const m = metrics || {}

  return (
    <section style={{ padding:"20px 24px", display:"flex", flexDirection:"column", gap:24,
      fontFamily:"'Inter', system-ui, monospace", color:"#e6edf3" }}>

      {/* ── Tab bar ──────────────────────────────────────────────────────── */}
      <div style={{ display:"flex", gap:10 }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => setActiveTab(t.key)}
            style={{
              flex: 1, padding:"14px 16px", borderRadius:10, cursor:"pointer",
              border: activeTab === t.key ? `2px solid ${t.accent}` : "2px solid #30363d",
              background: activeTab === t.key ? `${t.accent}18` : "#0d1117",
              color: activeTab === t.key ? t.accent : "#8b949e",
              textAlign:"left", transition:"all .15s",
            }}>
            <div style={{ fontSize:18, marginBottom:4 }}>{t.icon}</div>
            <div style={{ fontWeight:700, fontSize:13 }}>{t.label}</div>
            <div style={{ fontSize:10, opacity:.7, marginTop:2 }}>{t.desc}</div>
          </button>
        ))}
      </div>

      {/* Error / empty state */}
      {tabData?.error && (
        <div style={{ padding:16, background:"#2d1b1b", borderRadius:8,
          color:"#f85149", fontSize:13 }}>
          ⚠ Could not load data: {tabData.error}
        </div>
      )}
      {!tabData && !loading && (
        <div style={{ padding:16, color:"#8b949e", fontSize:13, textAlign:"center" }}>
          No data yet for this account.
        </div>
      )}

      {tabData && !tabData.error && <>

      {/* ── Section 1: Account Snapshot ──────────────────────────────────── */}
      <div>
        <p style={{ fontSize:11, color:"#8b949e", textTransform:"uppercase",
          letterSpacing:"1px", margin:"0 0 12px" }}>{tab.icon} {tab.label} Snapshot</p>
        <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(160px,1fr))", gap:12 }}>
          <MetricCard label="Balance"
            value={account ? `$${fmt(account.balance)}` : "—"}
            sub={account ? `${account.currency} · 1:${account.leverage}` : ""}
            accent="#1c2128" />
          <MetricCard label="Equity"
            value={account ? `$${fmt(account.equity)}` : "—"}
            sub="Includes open P&L"
            color={account && account.equity >= account.balance ? "#3fb950" : "#f85149"}
            accent="#1c2128" />
          <MetricCard label="Free Margin"
            value={account ? `$${fmt(account.free_margin)}` : "—"}
            accent="#1c2128" />
          <MetricCard label="Open Positions"
            value={open_positions.length}
            sub={open_positions.length > 0
              ? fmtUsd(m.open_unrealized) + " unrealized"
              : "No open trades"}
            color={m.open_unrealized > 0 ? "#3fb950" : m.open_unrealized < 0 ? "#f85149" : "#8b949e"}
            accent={m.open_unrealized > 0 ? "#162d1f" : m.open_unrealized < 0 ? "#2d1b1b" : "#1c2128"} />
        </div>
      </div>

      {/* ── Section 2: Key Metrics ───────────────────────────────────────── */}
      <div>
        <div style={{ display:"flex", alignItems:"center", gap:12, marginBottom:12 }}>
          <p style={{ fontSize:11, color:"#8b949e", textTransform:"uppercase",
            letterSpacing:"1px", margin:0 }}>📊 Performance Metrics</p>
        </div>

        <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))", gap:12 }}>
          <MetricCard label="Net P&L ($)"
            value={fmtUsd(m.net_pnl)}
            sub={`${m.total_trades || 0} closed trades`}
            color={pnlColor(m.net_pnl)}
            accent={m.net_pnl > 0 ? "#162d1f" : m.net_pnl < 0 ? "#2d1b1b" : "#1c2128"} />
          <MetricCard label="Win Rate"
            value={m.win_rate != null ? `${m.win_rate}%` : "—"}
            sub={`${m.wins || 0}W · ${m.losses || 0}L`}
            color={m.win_rate >= 60 ? "#3fb950" : m.win_rate >= 45 ? "#f0b429" : "#f85149"}
            accent="#1c2128" />
          <MetricCard label="Profit Factor"
            value={m.profit_factor != null ? fmt(m.profit_factor) : "—"}
            sub="Gross profit ÷ gross loss"
            color={m.profit_factor >= 1.5 ? "#3fb950" : m.profit_factor >= 1 ? "#f0b429" : "#f85149"}
            accent="#1c2128" />
          <MetricCard label="Avg Win"
            value={fmtUsd(m.avg_win)}
            color="#3fb950"
            accent="#162d1f" />
          <MetricCard label="Avg Loss"
            value={m.avg_loss ? `-$${fmt(m.avg_loss)}` : "—"}
            color="#f85149"
            accent="#2d1b1b" />
          <MetricCard label="Max Drawdown"
            value={m.max_drawdown ? `-$${fmt(m.max_drawdown)}` : "$0.00"}
            sub="Peak-to-trough"
            color="#f85149"
            accent="#2d1b1b" />
          <MetricCard label="Best Trade"
            value={fmtUsd(m.best_trade)}
            color="#3fb950"
            accent="#162d1f" />
          <MetricCard label="Worst Trade"
            value={m.worst_trade != null ? `-$${fmt(Math.abs(m.worst_trade))}` : "—"}
            color="#f85149"
            accent="#2d1b1b" />
        </div>
      </div>

      {/* ── Section 3: Equity Curve ──────────────────────────────────────── */}
      <div>
        <p style={{ fontSize:11, color:"#8b949e", textTransform:"uppercase",
          letterSpacing:"1px", margin:"0 0 12px" }}>📈 Equity Curve (Cumulative Realized P&L)</p>
        <EquityCurve curve={equity_curve} />
      </div>

      {/* ── Section 4: By Symbol + By Strategy ──────────────────────────── */}
      <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:16 }}>

        {/* By Symbol */}
        <div>
          <p style={{ fontSize:11, color:"#8b949e", textTransform:"uppercase",
            letterSpacing:"1px", margin:"0 0 12px" }}>💱 By Symbol</p>
          <div style={{ background:"#0d1117", border:"1px solid #1c2128",
            borderRadius:10, overflow:"hidden" }}>
            <table style={{ width:"100%", borderCollapse:"collapse", fontSize:12 }}>
              <thead>
                <tr style={{ borderBottom:"1px solid #1c2128" }}>
                  {["Symbol","Trades","W/L","Net P&L","P. Factor"].map(h => (
                    <th key={h} style={{ padding:"8px 12px", textAlign:"left",
                      color:"#8b949e", fontSize:10, textTransform:"uppercase",
                      letterSpacing:".4px" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(by_symbol).length === 0 ? (
                  <tr><td colSpan={5} style={{ padding:"16px", color:"#8b949e",
                    textAlign:"center" }}>No data</td></tr>
                ) : Object.entries(by_symbol)
                    .sort((a,b) => b[1].net - a[1].net)
                    .map(([sym, s]) => {
                      const wr = s.trades > 0 ? Math.round(s.wins / s.trades * 100) : 0
                      const pf = s.gross_loss > 0 ? (s.gross_profit / s.gross_loss).toFixed(2) : "∞"
                      return (
                        <tr key={sym} style={{ borderBottom:"1px solid #161b22" }}>
                          <td style={{ padding:"8px 12px" }}>
                            <span style={{ background:"#161b22", padding:"2px 6px",
                              borderRadius:4, fontSize:11, fontWeight:700,
                              color:"#58a6ff" }}>
                              {SYMBOL_ICON[sym] || ""} {sym}
                            </span>
                          </td>
                          <td style={{ padding:"8px 12px", color:"#8b949e" }}>{s.trades}</td>
                          <td style={{ padding:"8px 12px" }}>
                            <span style={{ color:"#3fb950" }}>{s.wins}W</span>
                            <span style={{ color:"#8b949e" }}>/</span>
                            <span style={{ color:"#f85149" }}>{s.trades - s.wins}L</span>
                            <span style={{ color:"#555d68", fontSize:10,
                              marginLeft:4 }}>({wr}%)</span>
                          </td>
                          <td style={{ padding:"8px 12px", fontWeight:700,
                            fontFamily:"monospace", color: pnlColor(s.net) }}>
                            {fmtUsd(s.net)}
                          </td>
                          <td style={{ padding:"8px 12px",
                            color: pf === "∞" || Number(pf) >= 1.5 ? "#3fb950"
                                 : Number(pf) >= 1 ? "#f0b429" : "#f85149" }}>
                            {pf}
                          </td>
                        </tr>
                      )
                    })}
              </tbody>
            </table>
          </div>
        </div>

        {/* By Strategy */}
        <div>
          <p style={{ fontSize:11, color:"#8b949e", textTransform:"uppercase",
            letterSpacing:"1px", margin:"0 0 12px" }}>🧠 By Strategy</p>
          <div style={{ background:"#0d1117", border:"1px solid #1c2128",
            borderRadius:10, overflow:"hidden" }}>
            <table style={{ width:"100%", borderCollapse:"collapse", fontSize:12 }}>
              <thead>
                <tr style={{ borderBottom:"1px solid #1c2128" }}>
                  {["Strategy","Trades","W/L","Net P&L","P. Factor"].map(h => (
                    <th key={h} style={{ padding:"8px 12px", textAlign:"left",
                      color:"#8b949e", fontSize:10, textTransform:"uppercase",
                      letterSpacing:".4px" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(by_strategy).length === 0 ? (
                  <tr><td colSpan={5} style={{ padding:"16px", color:"#8b949e",
                    textAlign:"center" }}>No data</td></tr>
                ) : Object.entries(by_strategy)
                    .sort((a,b) => b[1].net - a[1].net)
                    .map(([strat, s]) => {
                      const label = STRAT_LABEL[strat] || strat
                      const wr    = s.trades > 0 ? Math.round(s.wins / s.trades * 100) : 0
                      const pf    = s.gross_loss > 0 ? (s.gross_profit / s.gross_loss).toFixed(2) : "∞"
                      return (
                        <tr key={strat} style={{ borderBottom:"1px solid #161b22" }}>
                          <td style={{ padding:"8px 12px" }}>
                            <span style={{ background:"#1a3a5c", color:"#58a6ff",
                              padding:"2px 7px", borderRadius:4,
                              fontSize:11, fontWeight:600, whiteSpace:"nowrap" }}>
                              {label}
                            </span>
                          </td>
                          <td style={{ padding:"8px 12px", color:"#8b949e" }}>{s.trades}</td>
                          <td style={{ padding:"8px 12px" }}>
                            <span style={{ color:"#3fb950" }}>{s.wins}W</span>
                            <span style={{ color:"#8b949e" }}>/</span>
                            <span style={{ color:"#f85149" }}>{s.trades - s.wins}L</span>
                            <span style={{ color:"#555d68", fontSize:10,
                              marginLeft:4 }}>({wr}%)</span>
                          </td>
                          <td style={{ padding:"8px 12px", fontWeight:700,
                            fontFamily:"monospace", color: pnlColor(s.net) }}>
                            {fmtUsd(s.net)}
                          </td>
                          <td style={{ padding:"8px 12px",
                            color: pf === "∞" || Number(pf) >= 1.5 ? "#3fb950"
                                 : Number(pf) >= 1 ? "#f0b429" : "#f85149" }}>
                            {pf}
                          </td>
                        </tr>
                      )
                    })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* ── Section 5: Open Positions ────────────────────────────────────── */}
      {open_positions.length > 0 && (
        <div>
          <p style={{ fontSize:11, color:"#8b949e", textTransform:"uppercase",
            letterSpacing:"1px", margin:"0 0 12px" }}>
            🟢 Open Positions ({open_positions.length})
          </p>
          <div style={{ background:"#0d1117", border:"1px solid #1c2128",
            borderRadius:10, overflow:"hidden" }}>
            <table style={{ width:"100%", borderCollapse:"collapse", fontSize:12 }}>
              <thead>
                <tr style={{ borderBottom:"1px solid #1c2128" }}>
                  {["Ticket","Symbol","Dir","Entry","Current","SL","TP","Lots","Unrealized P&L","Swap"].map(h => (
                    <th key={h} style={{ padding:"8px 12px", textAlign:"left",
                      color:"#8b949e", fontSize:10, textTransform:"uppercase",
                      letterSpacing:".4px", whiteSpace:"nowrap" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {open_positions.map((p, i) => (
                  <tr key={i} style={{ borderBottom:"1px solid #161b22" }}>
                    <td style={{ padding:"8px 12px", color:"#555d68",
                      fontSize:10 }}>{p.ticket}</td>
                    <td style={{ padding:"8px 12px", fontWeight:700,
                      color:"#58a6ff" }}>{p.symbol}</td>
                    <td style={{ padding:"8px 12px" }}>
                      <span style={{
                        background: p.direction==="BUY" ? "rgba(63,185,80,0.12)" : "rgba(248,81,73,0.12)",
                        color:      p.direction==="BUY" ? "#3fb950" : "#f85149",
                        padding:"2px 7px", borderRadius:4,
                        fontSize:11, fontWeight:700 }}>
                        {p.direction}
                      </span>
                    </td>
                    <td style={{ padding:"8px 12px", fontFamily:"monospace" }}>{fmt(p.entry)}</td>
                    <td style={{ padding:"8px 12px", fontFamily:"monospace",
                      color: p.direction==="BUY"
                        ? (p.current >= p.entry ? "#3fb950" : "#f85149")
                        : (p.current <= p.entry ? "#3fb950" : "#f85149") }}>
                      {fmt(p.current)}
                    </td>
                    <td style={{ padding:"8px 12px", fontFamily:"monospace",
                      color:"#f85149" }}>{fmt(p.sl)}</td>
                    <td style={{ padding:"8px 12px", fontFamily:"monospace",
                      color:"#3fb950" }}>{fmt(p.tp)}</td>
                    <td style={{ padding:"8px 12px", color:"#8b949e" }}>{p.lot}</td>
                    <td style={{ padding:"8px 12px", fontWeight:700,
                      fontFamily:"monospace", color: pnlColor(p.unrealized) }}>
                      {fmtUsd(p.unrealized)}
                    </td>
                    <td style={{ padding:"8px 12px", fontFamily:"monospace",
                      color:"#8b949e", fontSize:11 }}>{fmtUsd(p.swap, false)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div style={{ fontSize:10, color:"#555d68", textAlign:"right" }}>
        {activeTab === "demo" ? "Live MT5 data" : `${activeTab} trade history`} · All times UTC · Auto-refreshes every 30s
      </div>

      </> /* end tabData conditional */}
    </section>
  )
}
