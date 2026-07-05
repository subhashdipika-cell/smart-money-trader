import { useEffect, useState, useCallback } from "react"
import TradeJournal from "./TradeJournal"

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

const C = {
  bg:"#0d1117", card:"#161b22", border:"#1c2128",
  gold:"#f0b429", green:"#69db7c", red:"#f85149",
  blue:"#4dabf7", muted:"#8b949e", text:"#e6edf3", purple:"#d2a8ff",
}

function Card({ label, value, sub, color }) {
  return (
    <div style={{ background:C.bg, border:`1px solid ${C.border}`, borderRadius:8, padding:"14px 16px" }}>
      <div style={{ fontSize:10, color:C.muted, textTransform:"uppercase", letterSpacing:".5px", marginBottom:6 }}>{label}</div>
      <div style={{ fontSize:22, fontWeight:700, color: color || C.text }}>{value ?? "—"}</div>
      {sub && <div style={{ fontSize:11, color:C.muted, marginTop:4 }}>{sub}</div>}
    </div>
  )
}

function SectionTitle({ icon, title, sub }) {
  return (
    <div style={{ display:"flex", alignItems:"baseline", gap:10, margin:"4px 0 2px" }}>
      <span style={{ fontSize:14, fontWeight:800, color:C.text }}>{icon} {title}</span>
      {sub && <span style={{ fontSize:11, color:C.muted }}>{sub}</span>}
    </div>
  )
}

const wrColor = wr => wr >= 0.55 ? C.green : wr >= 0.45 ? C.gold : C.red

export default function LearningDashboard() {
  const [page,     setPage]     = useState("learning")   // learning | journal
  const [stats,    setStats]    = useState(null)
  const [insights, setInsights] = useState(null)
  const [loading,  setLoading]  = useState(true)

  // Tuner state
  const [tStrat,   setTStrat]   = useState("htf_ict_intraday")
  const [tSymbol,  setTSymbol]  = useState("")
  const [tDays,    setTDays]    = useState(60)
  const [tuning,   setTuning]   = useState(false)
  const [tuneRes,  setTuneRes]  = useState(null)
  const [tuneErr,  setTuneErr]  = useState("")
  const [applying, setApplying] = useState(false)
  const [applyMsg, setApplyMsg] = useState("")
  const [relearnMsg, setRelearnMsg] = useState("")
  const [exportMsg,  setExportMsg]  = useState("")

  const load = useCallback(async () => {
    try {
      const [sR, iR] = await Promise.all([
        fetch(`${API}/learning/stats`,    { signal: AbortSignal.timeout(15000) }),
        fetch(`${API}/learning/insights`, { signal: AbortSignal.timeout(15000) }),
      ])
      if (sR.ok) setStats(await sR.json())
      if (iR.ok) setInsights(await iR.json())
    } catch (e) { console.warn("[Learning]", e) }
    setLoading(false)
  }, [])

  useEffect(() => {
    load()
    const iv = setInterval(load, 60000)
    return () => clearInterval(iv)
  }, [load])

  const tunable = insights?.tuner?.tunable || {}
  const applied = insights?.tuner?.applied || {}
  const savedTuning = insights?.tuner?.tuning || {}
  const cfg = tunable[tStrat]
  const symbol = tSymbol || cfg?.symbols?.[0] || ""

  const runOptimizer = async () => {
    setTuning(true); setTuneErr(""); setTuneRes(null); setApplyMsg("")
    try {
      const r = await fetch(
        `${API}/learning/optimize?strategy_id=${tStrat}&symbol=${symbol}&days=${tDays}`,
        { method:"POST", signal: AbortSignal.timeout(300000) })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Optimizer failed")
      setTuneRes(d)
    } catch (e) {
      setTuneErr(e.name === "TimeoutError"
        ? "Optimizer took too long (>5 min). Try fewer days."
        : e.message)
    }
    setTuning(false)
  }

  const applyBest = async () => {
    setApplying(true); setApplyMsg("")
    try {
      const r = await fetch(
        `${API}/learning/apply-tuning?strategy_id=${tStrat}&symbol=${symbol}`,
        { method:"POST" })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Apply failed")
      setApplyMsg(`✅ Live engine now uses ${d.applied.param} = ${d.applied.value}`)
      load()
    } catch (e) { setApplyMsg(`❌ ${e.message}`) }
    setApplying(false)
  }

  const removeApplied = async (strategyId) => {
    await fetch(`${API}/learning/apply-tuning?strategy_id=${strategyId}`, { method:"DELETE" })
    load()
  }

  const relearnNow = async () => {
    setRelearnMsg("⏳ Running learning cycle…")
    try {
      const r = await fetch(`${API}/learning/run`, { method:"POST" })
      const d = await r.json()
      setRelearnMsg(d.success ? `✅ ${d.notes}` : "❌ failed")
      load()
    } catch (e) { setRelearnMsg(`❌ ${e.message}`) }
    setTimeout(() => setRelearnMsg(""), 8000)
  }

  const exportMonth = async () => {
    setExportMsg("⏳ Exporting this month to Obsidian…")
    try {
      const r = await fetch(`${API}/journal/monthly-export`, { method:"POST" })
      const d = await r.json()
      setExportMsg(
        d.status === "SUCCESS" ? `✅ ${d.trades} trades → ${d.path}`
        : d.status === "EMPTY" ? `ℹ️ ${d.message}`
        : `❌ ${d.message || d.status}`)
    } catch (e) { setExportMsg(`❌ ${e.message}`) }
    setTimeout(() => setExportMsg(""), 10000)
  }

  const PageTab = ({ id, label }) => (
    <button onClick={() => setPage(id)} style={{
      padding:"8px 18px", borderRadius:8, border:"none", cursor:"pointer",
      fontSize:13, fontWeight: page===id ? 800 : 500,
      background: page===id ? "rgba(77,171,247,.14)" : "transparent",
      color: page===id ? C.blue : C.muted,
      borderBottom: page===id ? `2px solid ${C.blue}` : "2px solid transparent" }}>
      {label}
    </button>
  )

  if (page === "journal") return (
    <section className="history-panel" style={{ overflowY:"auto" }}>
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Daily trading plan journal</p>
          <h2>Trade Journal</h2>
        </div>
        <div style={{ display:"flex", gap:4 }}>
          <PageTab id="learning" label="🧠 Learning" />
          <PageTab id="journal"  label="📓 Journal" />
        </div>
      </div>
      <div style={{ padding:20 }}>
        <TradeJournal />
      </div>
    </section>
  )

  if (loading) return <section className="history-panel"><div className="empty-state">Loading learning data…</div></section>
  if (!stats)  return <section className="history-panel"><div className="empty-state">No learning data yet.</div></section>

  const assets      = insights?.knowledge?.assets || {}
  const suggRaw     = insights?.suggestions
  const suggestions = Array.isArray(suggRaw) ? suggRaw : (suggRaw?.suggestions || [])
  const strategies  = stats.strategies || []
  const dirBlocks   = Object.entries(insights?.knowledge?.direction_blocks || {})

  return (
    <section className="history-panel" style={{ overflowY:"auto" }}>
      <div className="panel-heading">
        <div><p className="eyebrow">Self-learning engine</p><h2>Learning & Strategy Tuning</h2></div>
        <div style={{ display:"flex", gap:8, alignItems:"center" }}>
          <PageTab id="learning" label="🧠 Learning" />
          <PageTab id="journal"  label="📓 Journal" />
          <button onClick={relearnNow} style={{ padding:"7px 14px", borderRadius:7, border:`1px solid ${C.border}`,
            background:"rgba(77,171,247,.1)", color:C.blue, cursor:"pointer", fontSize:12, fontWeight:700 }}>
            🔄 Re-learn now
          </button>
          <button onClick={exportMonth} style={{ padding:"7px 14px", borderRadius:7, border:`1px solid ${C.green}44`,
            background:"rgba(105,219,124,.1)", color:C.green, cursor:"pointer", fontSize:12, fontWeight:700 }}>
            ⬍ Export month → Obsidian
          </button>
        </div>
      </div>
      {relearnMsg && <div style={{ padding:"6px 20px", fontSize:12, color:C.muted }}>{relearnMsg}</div>}
      {exportMsg  && <div style={{ padding:"6px 20px", fontSize:12, color:C.muted }}>{exportMsg}</div>}

      <div style={{ padding:20, display:"flex", flexDirection:"column", gap:22 }}>

        {/* ── 1. Overview ─────────────────────────────────────────────────── */}
        <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))", gap:12 }}>
          <Card label="Signals Sent" value={stats.total_signals_sent} />
          <Card label="Resolved"     value={stats.total_resolved} />
          <Card label="Wins"         value={stats.total_wins} color={C.green} />
          <Card label="Win Rate"
            value={stats.overall_win_rate != null ? `${(stats.overall_win_rate*100).toFixed(1)}%` : "—"}
            color={stats.overall_win_rate != null ? wrColor(stats.overall_win_rate) : C.muted} />
          <Card label="Quality Gate" value={stats.min_quality_score} sub="min score to send a signal" />
          <Card label="Open Signals" value={stats.total_open} color={C.gold} />
        </div>
        <div style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:8, padding:"12px 16px",
          fontSize:13, color:"#c9d1d9" }}>
          🧠 {stats.notes}
        </div>

        {/* ── 2. Strategy Tuner ───────────────────────────────────────────── */}
        <div style={{ background:C.card, border:`1px solid rgba(210,168,255,.25)`, borderRadius:10, padding:16 }}>
          <SectionTitle icon="🔬" title="Strategy Tuner"
            sub="backtests every setting on a grid, finds the most profitable, and can apply it to the live engine" />

          <div style={{ display:"flex", gap:12, flexWrap:"wrap", alignItems:"flex-end", margin:"14px 0" }}>
            <div style={{ flex:"1 1 260px" }}>
              <label style={{ fontSize:10, color:C.muted, display:"block", marginBottom:4 }}>STRATEGY</label>
              <select value={tStrat} onChange={e => { setTStrat(e.target.value); setTSymbol(""); setTuneRes(null); setApplyMsg("") }}
                style={{ width:"100%", background:C.bg, color:C.text, border:`1px solid ${C.border}`, borderRadius:6, padding:"8px 10px", fontSize:13 }}>
                {Object.entries(tunable).map(([id, t]) => (
                  <option key={id} value={id}>{t.label} — tunes {t.param}</option>
                ))}
              </select>
            </div>
            <div style={{ flex:"0 0 130px" }}>
              <label style={{ fontSize:10, color:C.muted, display:"block", marginBottom:4 }}>SYMBOL</label>
              <select value={symbol} onChange={e => setTSymbol(e.target.value)}
                style={{ width:"100%", background:C.bg, color:C.text, border:`1px solid ${C.border}`, borderRadius:6, padding:"8px 10px", fontSize:13 }}>
                {(cfg?.symbols || []).map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div style={{ flex:"0 0 110px" }}>
              <label style={{ fontSize:10, color:C.muted, display:"block", marginBottom:4 }}>PERIOD</label>
              <select value={tDays} onChange={e => setTDays(Number(e.target.value))}
                style={{ width:"100%", background:C.bg, color:C.text, border:`1px solid ${C.border}`, borderRadius:6, padding:"8px 10px", fontSize:13 }}>
                {[30,60,90,180].map(d => <option key={d} value={d}>{d} days</option>)}
              </select>
            </div>
            <button onClick={runOptimizer} disabled={tuning} style={{
              padding:"9px 20px", borderRadius:8, border:"none", cursor:tuning?"not-allowed":"pointer",
              fontSize:13, fontWeight:700, background:tuning?"rgba(210,168,255,.08)":"rgba(210,168,255,.18)",
              color:tuning?C.muted:C.purple }}>
              {tuning ? "⏳ Testing every setting…" : "🔍 Find Best Settings"}
            </button>
          </div>

          {tuneErr && <div style={{ fontSize:12, color:C.red, marginBottom:10 }}>⚠ {tuneErr}</div>}

          {tuneRes && (
            <div style={{ display:"flex", flexDirection:"column", gap:10 }}>
              <table style={{ width:"100%", borderCollapse:"collapse", fontSize:12 }}>
                <thead>
                  <tr>
                    {[`${tuneRes.param}`, "Trades", "Win Rate", "Net P/L", "Profit Factor", ""].map(h => (
                      <th key={h} style={{ padding:"6px 10px", textAlign:"left", color:C.muted, fontSize:10,
                        textTransform:"uppercase", borderBottom:`1px solid ${C.border}` }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {tuneRes.results.map((r, i) => {
                    const isBest = !r.error && tuneRes.best && r.value === tuneRes.best.value
                    return (
                      <tr key={i} style={{ background:isBest?"rgba(105,219,124,.06)":"transparent",
                        borderBottom:`1px solid ${C.border}` }}>
                        <td style={{ padding:"7px 10px", fontWeight:700, color:isBest?C.green:C.text }}>{r.value}</td>
                        {r.error ? (
                          <td colSpan={5} style={{ padding:"7px 10px", color:C.red, fontSize:11 }}>{r.error}</td>
                        ) : (<>
                          <td style={{ padding:"7px 10px", color:C.muted }}>{r.trades}</td>
                          <td style={{ padding:"7px 10px", color:wrColor((r.win_rate||0)/100) }}>{r.win_rate}%</td>
                          <td style={{ padding:"7px 10px", fontFamily:"monospace", fontWeight:700,
                            color:r.net>=0?C.green:C.red }}>{r.net>=0?"+":""}{r.net}</td>
                          <td style={{ padding:"7px 10px", color:(r.profit_factor||0)>=1?C.green:C.red }}>{r.profit_factor ?? "—"}</td>
                          <td style={{ padding:"7px 10px", fontSize:11, color:C.green }}>{isBest?"★ BEST":""}</td>
                        </>)}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              <div style={{ display:"flex", alignItems:"center", gap:12, flexWrap:"wrap" }}>
                <button onClick={applyBest} disabled={applying} style={{
                  padding:"8px 18px", borderRadius:7, border:"none", cursor:"pointer",
                  fontSize:12, fontWeight:700, background:"rgba(105,219,124,.15)", color:C.green }}>
                  {applying ? "…" : `✅ Apply ${tuneRes.param} = ${tuneRes.best?.value} to live engine`}
                </button>
                {tuneRes.low_sample && (
                  <span style={{ fontSize:11, color:C.gold }}>
                    ⚠️ Best result has under 4 trades — low confidence, consider a longer period.
                  </span>
                )}
                {applyMsg && <span style={{ fontSize:12, color:applyMsg.startsWith("✅")?C.green:C.red }}>{applyMsg}</span>}
              </div>
            </div>
          )}

          {/* Applied tunings */}
          {Object.keys(applied).length > 0 && (
            <div style={{ marginTop:14, borderTop:`1px solid ${C.border}`, paddingTop:12 }}>
              <div style={{ fontSize:10, color:C.muted, textTransform:"uppercase", letterSpacing:".5px", marginBottom:8 }}>
                Active learned settings (used by the live engine)
              </div>
              <div style={{ display:"flex", gap:8, flexWrap:"wrap" }}>
                {Object.entries(applied).map(([tag, a]) => (
                  <div key={tag} style={{ display:"flex", alignItems:"center", gap:8, fontSize:12,
                    background:"rgba(105,219,124,.07)", border:"1px solid rgba(105,219,124,.25)",
                    borderRadius:6, padding:"5px 10px" }}>
                    <span style={{ color:C.green, fontWeight:700 }}>{tag}</span>
                    <span style={{ color:C.text }}>{a.param} = {a.value}</span>
                    <span style={{ color:C.muted, fontSize:10 }}>since {a.applied_at}</span>
                    <button onClick={() => removeApplied(a.strategy_id)} title="Revert to default"
                      style={{ background:"none", border:"none", color:C.red, cursor:"pointer", fontSize:12 }}>✕</button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* ── 3. Live strategy performance ─────────────────────────────────── */}
        {strategies.length > 0 && (
          <div style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:10, padding:16 }}>
            <SectionTitle icon="📊" title="Strategy performance (live signals)"
              sub="ranked by win rate × average reward — from your real signal history" />
            <table style={{ width:"100%", borderCollapse:"collapse", fontSize:12, marginTop:10 }}>
              <thead>
                <tr>
                  {["Strategy","Trades","W/L","Win Rate","Avg RR","Net Pts","Big Wins","Big Losses","Score"].map(h => (
                    <th key={h} style={{ padding:"6px 10px", textAlign:"left", color:C.muted, fontSize:10,
                      textTransform:"uppercase", borderBottom:`1px solid ${C.border}` }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {strategies.map(s => (
                  <tr key={s.strategy} style={{ borderBottom:`1px solid ${C.border}` }}>
                    <td style={{ padding:"7px 10px", fontWeight:700, color:C.blue }}>{s.strategy}</td>
                    <td style={{ padding:"7px 10px", color:C.muted }}>{s.total}</td>
                    <td style={{ padding:"7px 10px" }}>
                      <span style={{ color:C.green }}>{s.wins}W</span><span style={{ color:C.muted }}>/</span>
                      <span style={{ color:C.red }}>{s.losses}L</span>
                    </td>
                    <td style={{ padding:"7px 10px", fontWeight:700, color:wrColor(s.win_rate) }}>{(s.win_rate*100).toFixed(0)}%</td>
                    <td style={{ padding:"7px 10px", color:C.muted }}>{s.avg_rr}</td>
                    <td style={{ padding:"7px 10px", fontFamily:"monospace", fontWeight:700,
                      color:s.total_pts>=0?C.green:C.red }}>{s.total_pts>=0?"+":""}{s.total_pts}</td>
                    <td style={{ padding:"7px 10px", color:C.green }}>{s.BP}</td>
                    <td style={{ padding:"7px 10px", color:C.red }}>{s.BL}</td>
                    <td style={{ padding:"7px 10px", fontWeight:800, color:C.purple }}>{s.score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* ── 4. Per-asset knowledge ──────────────────────────────────────── */}
        {Object.keys(assets).length > 0 && (
          <div style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:10, padding:16 }}>
            <SectionTitle icon="🌐" title="What the engine has learned per asset"
              sub="thresholds, sessions and direction blocks update automatically every cycle" />
            <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(280px,1fr))", gap:12, marginTop:10 }}>
              {Object.entries(assets).map(([sym, k]) => (
                <div key={sym} style={{ background:C.bg, border:`1px solid ${C.border}`, borderRadius:8, padding:"12px 14px" }}>
                  <div style={{ display:"flex", justifyContent:"space-between", marginBottom:8 }}>
                    <span style={{ fontWeight:800, color:C.gold }}>{sym}</span>
                    <span style={{ fontSize:11, color:C.muted }}>{k.total_resolved} resolved</span>
                  </div>
                  <div style={{ fontSize:12, color:"#c9d1d9", display:"flex", flexDirection:"column", gap:5 }}>
                    <div>Win rate: <b style={{ color:wrColor(k.win_rate||0) }}>{k.win_rate!=null?`${(k.win_rate*100).toFixed(0)}%`:"—"}</b>
                      {k.loss_streak >= 3 && <span style={{ color:C.red }}> · ⚠️ {k.loss_streak} losses in a row</span>}</div>
                    <div>Quality gate: <b style={{ color:C.blue }}>{k.threshold}</b></div>
                    {k.session_perf && Object.keys(k.session_perf).length > 0 && (
                      <div>Sessions: {Object.entries(k.session_perf).map(([sess, sp]) => (
                        <span key={sess} style={{ marginRight:8 }}>
                          {sess} <b style={{ color:wrColor(sp.win_rate||0) }}>{((sp.win_rate||0)*100).toFixed(0)}%</b>
                        </span>
                      ))}</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
            {dirBlocks.length > 0 && (
              <div style={{ marginTop:12, display:"flex", gap:8, flexWrap:"wrap" }}>
                {dirBlocks.map(([key, b]) => (
                  <span key={key} style={{ fontSize:11, background:"rgba(248,81,73,.08)",
                    border:"1px solid rgba(248,81,73,.3)", color:C.red, borderRadius:6, padding:"4px 10px" }}>
                    ⛔ {b.reason}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── 5. Confluence win rates ─────────────────────────────────────── */}
        {Object.keys(stats.confluence_win_rates || {}).length > 0 && (
          <div style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:10, padding:16 }}>
            <SectionTitle icon="🧩" title="Confluence win rates"
              sub="signal ingredients that win get a score bonus; losers get penalised" />
            <div style={{ marginTop:10 }}>
              {Object.entries(stats.confluence_win_rates).map(([tag, wr]) => {
                const bonus = stats.confluence_bonuses?.[tag] || 0
                const pct   = (wr * 100).toFixed(1)
                const color = wrColor(wr)
                return (
                  <div key={tag} style={{ display:"flex", alignItems:"center", gap:10, marginBottom:8 }}>
                    <div style={{ flex:1, fontSize:12, color:"#c9d1d9" }}>{tag}</div>
                    <div style={{ width:120, height:6, background:C.border, borderRadius:3, overflow:"hidden" }}>
                      <div style={{ width:`${pct}%`, height:"100%", background:color }} />
                    </div>
                    <div style={{ width:48, textAlign:"right", fontSize:12, fontWeight:700, color }}>{pct}%</div>
                    <div style={{ width:36, textAlign:"right", fontSize:11,
                      color:bonus>0?C.green:bonus<0?C.red:C.muted }}>
                      {bonus>0?`+${bonus}`:bonus<0?bonus:"±0"}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* ── 6. Improvement suggestions ──────────────────────────────────── */}
        {suggestions.length > 0 && (
          <div style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:10, padding:16 }}>
            <SectionTitle icon="💡" title="Improvement suggestions" sub="generated automatically from your results" />
            <div style={{ display:"flex", flexDirection:"column", gap:8, marginTop:10 }}>
              {suggestions.slice(0, 10).map((s, i) => (
                <div key={i} style={{ fontSize:12, color:"#c9d1d9", background:C.bg,
                  border:`1px solid ${C.border}`, borderRadius:6, padding:"8px 12px", lineHeight:1.5 }}>
                  {typeof s === "string" ? s : (
                    <>
                      {s.priority && <b style={{ color:s.priority==="HIGH"?C.red:s.priority==="MEDIUM"?C.gold:C.muted,
                        marginRight:6 }}>[{s.priority}]</b>}
                      <b style={{ color:C.text }}>{s.title || s.category || ""}</b>
                      {(s.detail || s.suggestion || s.text) && <span> — {s.detail || s.suggestion || s.text}</span>}
                    </>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
