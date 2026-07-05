import { useEffect, useState, useCallback } from "react"

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

const C = {
  bg:"#0d1117", card:"#161b22", border:"#1c2128",
  gold:"#f0b429", green:"#69db7c", red:"#f85149",
  blue:"#4dabf7", muted:"#8b949e", text:"#e6edf3", purple:"#d2a8ff",
}

// ── Form structure — mirrors the "Daily Trading Plan Journal" document ────────
const SECTIONS = [
  { icon:"🌅", title:"1 · Pre-Market Preparation", fields:[
    { k:"date",            label:"Date",                         type:"date" },
    { k:"market",          label:"Market",                       type:"select",
      options:["Crypto","Gold","Forex","Nifty","BankNifty","Stocks"] },
    { k:"market_bias",     label:"Market Bias",                  type:"select",
      options:["Bullish","Bearish","Neutral"] },
    { k:"global_cues",     label:"Global Cues / News Impact",    type:"text" },
    { k:"economic_events", label:"Key Economic Events Today",    type:"text" },
    { k:"watchlist",       label:"Watchlist",                    type:"text" },
  ]},
  { icon:"📐", title:"2 · Key Levels", fields:[
    { k:"prev_day_high",     label:"Previous Day High",          type:"text" },
    { k:"prev_day_low",      label:"Previous Day Low",           type:"text" },
    { k:"support_levels",    label:"Support Levels",             type:"text" },
    { k:"resistance_levels", label:"Resistance Levels",          type:"text" },
    { k:"gap",               label:"Gap Up / Gap Down",          type:"select",
      options:["No Gap","Gap Up","Gap Down"] },
    { k:"liquidity_zones",   label:"Important Zones / Liquidity Areas", type:"text" },
  ]},
  { icon:"🎯", title:"3 · Trade Execution Plan", fields:[
    { k:"setup_name",            label:"Setup Name",             type:"text" },
    { k:"entry_criteria",        label:"Entry Criteria",         type:"textarea" },
    { k:"invalidation_criteria", label:"Invalidation Criteria",  type:"textarea" },
    { k:"entry_price",   label:"Entry Price",        type:"text" },
    { k:"stop_loss",     label:"Stop Loss",          type:"text" },
    { k:"target1",       label:"Target 1",           type:"text" },
    { k:"target2",       label:"Target 2",           type:"text" },
    { k:"risk_per_trade", label:"Risk Per Trade",    type:"text" },
    { k:"max_trades",    label:"Maximum Trades Today", type:"text" },
  ]},
  { icon:"🧘", title:"4 · Emotional Check-In", fields:[
    { k:"mood",        label:"Mood Before Market",      type:"text" },
    { k:"confidence",  label:"Confidence Level (1–10)", type:"number", min:1, max:10 },
    { k:"fomo",        label:"Did I feel FOMO?",        type:"select", options:["No","Yes"] },
    { k:"disciplined", label:"Was I disciplined?",      type:"select", options:["Yes","No"] },
  ]},
  { icon:"🌙", title:"5 · Post-Market Review", fields:[
    { k:"total_trades",    label:"Total Trades Taken",  type:"number" },
    { k:"winning_trades",  label:"Winning Trades",      type:"number" },
    { k:"losing_trades",   label:"Losing Trades",       type:"number" },
    { k:"net_pl",          label:"Net P/L ($)",         type:"text" },
    { k:"best_trade",      label:"Best Trade of the Day",  type:"text" },
    { k:"worst_trade",     label:"Worst Trade of the Day", type:"text" },
    { k:"biggest_mistake", label:"Biggest Mistake",     type:"textarea" },
    { k:"lesson_learned",  label:"Lesson Learned",      type:"textarea" },
    { k:"plan_tomorrow",   label:"Plan for Tomorrow",   type:"textarea" },
  ]},
]

const EMPTY = Object.fromEntries(
  SECTIONS.flatMap(s => s.fields.map(f => [f.k, f.type === "select" ? f.options[0] : ""]))
)

const today = () => new Date().toISOString().slice(0, 10)

function Field({ f, value, onChange }) {
  const base = { width:"100%", background:C.bg, color:C.text, border:`1px solid ${C.border}`,
    borderRadius:6, padding:"8px 10px", fontSize:13, boxSizing:"border-box" }
  return (
    <div style={{ display:"flex", flexDirection:"column", gap:4 }}>
      <label style={{ fontSize:10, color:C.muted, textTransform:"uppercase", letterSpacing:".4px" }}>{f.label}</label>
      {f.type === "select" ? (
        <select style={base} value={value} onChange={e => onChange(e.target.value)}>
          {f.options.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : f.type === "textarea" ? (
        <textarea style={{ ...base, minHeight:54, resize:"vertical" }} value={value}
          onChange={e => onChange(e.target.value)} />
      ) : (
        <input style={base} type={f.type} min={f.min} max={f.max} value={value}
          onChange={e => onChange(e.target.value)} />
      )}
    </div>
  )
}

const plColor = v => v > 0 ? C.green : v < 0 ? C.red : C.muted

export default function TradeJournal() {
  const [view,     setView]     = useState("entries")   // entries | form | analysis
  const [entries,  setEntries]  = useState([])
  const [analysis, setAnalysis] = useState(null)
  const [form,     setForm]     = useState({ ...EMPTY, date: today() })
  const [saveMsg,  setSaveMsg]  = useState("")
  const [expanded, setExpanded] = useState(null)

  const load = useCallback(async () => {
    try {
      const [eR, aR] = await Promise.all([
        fetch(`${API}/journal/manual`),
        fetch(`${API}/journal/manual/analysis`),
      ])
      if (eR.ok) setEntries((await eR.json()).entries || [])
      if (aR.ok) setAnalysis(await aR.json())
    } catch (e) { console.warn("[Journal]", e) }
  }, [])

  useEffect(() => { load() }, [load])

  const save = async () => {
    setSaveMsg("")
    try {
      const r = await fetch(`${API}/journal/manual`, {
        method:"POST", headers:{ "Content-Type":"application/json" },
        body: JSON.stringify(form),
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Save failed")
      setSaveMsg("✅ Saved")
      setForm({ ...EMPTY, date: today() })
      load()
      setView("entries")
    } catch (e) { setSaveMsg(`❌ ${e.message}`) }
  }

  const editEntry = (e) => { setForm({ ...EMPTY, ...e }); setView("form"); setSaveMsg("") }

  const deleteEntry = async (id) => {
    if (!window.confirm("Delete this journal entry?")) return
    await fetch(`${API}/journal/manual/${id}`, { method:"DELETE" })
    load()
  }

  const Tab = ({ id, label }) => (
    <button onClick={() => setView(id)} style={{
      padding:"7px 16px", borderRadius:7, border:"none", cursor:"pointer",
      fontSize:12, fontWeight: view===id ? 700 : 500,
      background: view===id ? "rgba(240,180,41,.15)" : C.card,
      color: view===id ? C.gold : C.muted }}>
      {label}
    </button>
  )

  const GroupTable = ({ title, groups, nameHeader }) => {
    const rows = Object.entries(groups || {}).filter(([k]) => k !== "Unknown" || Object.keys(groups).length === 1)
    if (!rows.length) return null
    return (
      <div style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:8, padding:14 }}>
        <div style={{ fontSize:11, color:C.muted, textTransform:"uppercase", letterSpacing:".5px", marginBottom:8 }}>{title}</div>
        <table style={{ width:"100%", borderCollapse:"collapse", fontSize:12 }}>
          <thead><tr>
            {[nameHeader,"Days","Green Days","Net P/L"].map(h => (
              <th key={h} style={{ padding:"4px 8px", textAlign:"left", color:C.muted, fontSize:10,
                textTransform:"uppercase", borderBottom:`1px solid ${C.border}` }}>{h}</th>))}
          </tr></thead>
          <tbody>
            {rows.map(([k, g]) => (
              <tr key={k} style={{ borderBottom:`1px solid ${C.border}` }}>
                <td style={{ padding:"6px 8px", fontWeight:700, color:C.text }}>{k}</td>
                <td style={{ padding:"6px 8px", color:C.muted }}>{g.days}</td>
                <td style={{ padding:"6px 8px", color:C.green }}>{g.green}</td>
                <td style={{ padding:"6px 8px", fontFamily:"monospace", fontWeight:700, color:plColor(g.net) }}>
                  {g.net >= 0 ? "+" : ""}{g.net}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  return (
    <div style={{ display:"flex", flexDirection:"column", gap:16 }}>
      <div style={{ display:"flex", gap:8, alignItems:"center" }}>
        <Tab id="entries"  label={`📚 Entries (${entries.length})`} />
        <Tab id="form"     label="✍️ New Entry" />
        <Tab id="analysis" label="📈 Analysis" />
        {saveMsg && <span style={{ fontSize:12, color:saveMsg.startsWith("✅")?C.green:C.red }}>{saveMsg}</span>}
      </div>

      {/* ── Entry form ──────────────────────────────────────────────────── */}
      {view === "form" && (
        <div style={{ display:"flex", flexDirection:"column", gap:14 }}>
          {SECTIONS.map(sec => (
            <div key={sec.title} style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:10, padding:16 }}>
              <div style={{ fontSize:13, fontWeight:800, color:C.gold, marginBottom:12 }}>{sec.icon} {sec.title}</div>
              <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))", gap:12 }}>
                {sec.fields.map(f => (
                  <div key={f.k} style={f.type === "textarea" ? { gridColumn:"1 / -1" } : undefined}>
                    <Field f={f} value={form[f.k] ?? ""} onChange={v => setForm(p => ({ ...p, [f.k]: v }))} />
                  </div>
                ))}
              </div>
            </div>
          ))}
          <div style={{ display:"flex", gap:10 }}>
            <button onClick={save} style={{ padding:"10px 26px", borderRadius:8, border:"none",
              cursor:"pointer", fontSize:13, fontWeight:800,
              background:"rgba(105,219,124,.18)", color:C.green }}>
              💾 {form.id ? "Update Entry" : "Save Entry"}
            </button>
            <button onClick={() => { setForm({ ...EMPTY, date: today() }); setSaveMsg("") }}
              style={{ padding:"10px 18px", borderRadius:8, border:`1px solid ${C.border}`,
              cursor:"pointer", fontSize:12, background:C.card, color:C.muted }}>
              Clear
            </button>
          </div>
        </div>
      )}

      {/* ── Entries list ───────────────────────────────────────────────── */}
      {view === "entries" && (
        entries.length === 0 ? (
          <div style={{ color:C.muted, fontSize:13, padding:24, textAlign:"center",
            background:C.card, border:`1px dashed ${C.border}`, borderRadius:10 }}>
            No journal entries yet. Click <b style={{ color:C.gold }}>✍️ New Entry</b> to log your first trading day.
          </div>
        ) : (
          <div style={{ display:"flex", flexDirection:"column", gap:10 }}>
            {entries.map(e => {
              const pl     = parseFloat(String(e.net_pl ?? "").replace(/[$,₹]/g, ""))
              const hasPl  = !Number.isNaN(pl)
              const isOpen = expanded === e.id
              return (
                <div key={e.id} style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:10 }}>
                  <div onClick={() => setExpanded(isOpen ? null : e.id)}
                    style={{ display:"flex", alignItems:"center", gap:14, padding:"12px 16px", cursor:"pointer", flexWrap:"wrap" }}>
                    <span style={{ fontWeight:800, color:C.text }}>{e.date || "—"}</span>
                    <span style={{ fontSize:11, color:C.blue }}>{e.market}</span>
                    <span style={{ fontSize:11, color:e.market_bias==="Bullish"?C.green:e.market_bias==="Bearish"?C.red:C.muted }}>
                      {e.market_bias}</span>
                    {e.setup_name && <span style={{ fontSize:11, color:C.purple }}>{e.setup_name}</span>}
                    <span style={{ fontSize:11, color:C.muted }}>
                      {e.total_trades || 0} trades · {e.winning_trades || 0}W/{e.losing_trades || 0}L</span>
                    {hasPl && <span style={{ fontFamily:"monospace", fontWeight:800, color:plColor(pl) }}>
                      {pl >= 0 ? "+" : ""}{pl}</span>}
                    {e.disciplined === "No" && <span style={{ fontSize:10, color:C.red }}>⚠ undisciplined</span>}
                    {e.fomo === "Yes" && <span style={{ fontSize:10, color:C.gold }}>😬 FOMO</span>}
                    <span style={{ marginLeft:"auto", display:"flex", gap:8 }}>
                      <button onClick={ev => { ev.stopPropagation(); editEntry(e) }}
                        style={{ background:"none", border:"none", color:C.blue, cursor:"pointer", fontSize:12 }}>✏️ Edit</button>
                      <button onClick={ev => { ev.stopPropagation(); deleteEntry(e.id) }}
                        style={{ background:"none", border:"none", color:C.red, cursor:"pointer", fontSize:12 }}>🗑</button>
                      <span style={{ color:C.muted, fontSize:11 }}>{isOpen ? "▲" : "▼"}</span>
                    </span>
                  </div>
                  {isOpen && (
                    <div style={{ padding:"0 16px 14px", display:"grid",
                      gridTemplateColumns:"repeat(auto-fit,minmax(240px,1fr))", gap:10 }}>
                      {SECTIONS.map(sec => (
                        <div key={sec.title} style={{ background:C.bg, borderRadius:8, padding:"10px 12px" }}>
                          <div style={{ fontSize:10, color:C.gold, fontWeight:700, marginBottom:6 }}>{sec.icon} {sec.title}</div>
                          {sec.fields.filter(f => String(e[f.k] ?? "").trim() !== "").map(f => (
                            <div key={f.k} style={{ fontSize:11, color:"#c9d1d9", marginBottom:3 }}>
                              <span style={{ color:C.muted }}>{f.label}: </span>{String(e[f.k])}
                            </div>
                          ))}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )
      )}

      {/* ── Analysis ───────────────────────────────────────────────────── */}
      {view === "analysis" && (
        !analysis || !analysis.days ? (
          <div style={{ color:C.muted, fontSize:13, padding:24, textAlign:"center",
            background:C.card, border:`1px dashed ${C.border}`, borderRadius:10 }}>
            Nothing to analyse yet — save a few journal entries first.
          </div>
        ) : (
          <div style={{ display:"flex", flexDirection:"column", gap:14 }}>
            <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(140px,1fr))", gap:10 }}>
              {[
                ["Days Logged",   analysis.days, C.text],
                ["Total Trades",  analysis.total_trades, C.text],
                ["Trade Win Rate", analysis.trade_win_rate != null ? `${analysis.trade_win_rate}%` : "—",
                  (analysis.trade_win_rate||0) >= 50 ? C.green : C.red],
                ["Net P/L",       `${analysis.net_pl >= 0 ? "+" : ""}${analysis.net_pl}`, plColor(analysis.net_pl)],
                ["Green / Red Days", `${analysis.green_days} / ${analysis.red_days}`,
                  analysis.green_days >= analysis.red_days ? C.green : C.red],
                ["Avg Day P/L",   analysis.avg_day_pl != null ? `${analysis.avg_day_pl >= 0 ? "+" : ""}${analysis.avg_day_pl}` : "—",
                  plColor(analysis.avg_day_pl)],
              ].map(([l, v, c]) => (
                <div key={l} style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:8, padding:"12px 14px" }}>
                  <div style={{ fontSize:10, color:C.muted, textTransform:"uppercase", letterSpacing:".4px", marginBottom:4 }}>{l}</div>
                  <div style={{ fontSize:20, fontWeight:800, color:c }}>{v}</div>
                </div>
              ))}
            </div>

            {(analysis.best_day || analysis.worst_day) && (
              <div style={{ display:"flex", gap:10, flexWrap:"wrap", fontSize:12 }}>
                {analysis.best_day && (
                  <span style={{ background:"rgba(105,219,124,.08)", border:"1px solid rgba(105,219,124,.3)",
                    color:C.green, borderRadius:6, padding:"6px 12px" }}>
                    🏆 Best day: {analysis.best_day.date} ({analysis.best_day.pl >= 0 ? "+" : ""}{analysis.best_day.pl})
                  </span>)}
                {analysis.worst_day && (
                  <span style={{ background:"rgba(248,81,73,.08)", border:"1px solid rgba(248,81,73,.3)",
                    color:C.red, borderRadius:6, padding:"6px 12px" }}>
                    💀 Worst day: {analysis.worst_day.date} ({analysis.worst_day.pl})
                  </span>)}
              </div>
            )}

            <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(300px,1fr))", gap:12 }}>
              <GroupTable title="🧘 Discipline vs results" groups={analysis.by_discipline} nameHeader="Disciplined?" />
              <GroupTable title="😬 FOMO vs results"       groups={analysis.by_fomo}       nameHeader="Felt FOMO?" />
              <GroupTable title="🧭 By market bias"        groups={analysis.by_bias}       nameHeader="Bias" />
              <GroupTable title="💱 By market"             groups={analysis.by_market}     nameHeader="Market" />
              <GroupTable title="🎯 By setup"              groups={analysis.by_setup}      nameHeader="Setup" />
            </div>

            {(analysis.mistakes?.length > 0 || analysis.lessons?.length > 0) && (
              <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:12 }}>
                {analysis.mistakes?.length > 0 && (
                  <div style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:8, padding:14 }}>
                    <div style={{ fontSize:11, color:C.red, textTransform:"uppercase", letterSpacing:".5px", marginBottom:8 }}>
                      ❌ Mistakes to stop repeating</div>
                    {analysis.mistakes.map((m, i) => (
                      <div key={i} style={{ fontSize:12, color:"#c9d1d9", marginBottom:6 }}>
                        <span style={{ color:C.muted }}>{m.date}: </span>{m.text}</div>
                    ))}
                  </div>
                )}
                {analysis.lessons?.length > 0 && (
                  <div style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:8, padding:14 }}>
                    <div style={{ fontSize:11, color:C.green, textTransform:"uppercase", letterSpacing:".5px", marginBottom:8 }}>
                      ✅ Lessons learned</div>
                    {analysis.lessons.map((m, i) => (
                      <div key={i} style={{ fontSize:12, color:"#c9d1d9", marginBottom:6 }}>
                        <span style={{ color:C.muted }}>{m.date}: </span>{m.text}</div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )
      )}
    </div>
  )
}
