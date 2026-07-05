import { useState, useEffect, useCallback } from "react"

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

// ── Styling helpers (match app dark theme) ───────────────────────────────────
const card   = { background: "#161b22", border: "1px solid #21262d", borderRadius: "12px", padding: "18px", marginBottom: "16px" }
const label_ = { fontSize: "11px", color: "#8b949e", marginBottom: "4px", display: "block", letterSpacing: "0.4px" }
const input_ = { background: "#0d1117", border: "1px solid #30363d", borderRadius: "8px", color: "#e6edf3", padding: "7px 10px", fontSize: "13px", width: "100%", boxSizing: "border-box" }
const btn    = (bg, color = "#fff") => ({ background: bg, color, border: "none", borderRadius: "8px", padding: "9px 18px", fontSize: "13px", fontWeight: 600, cursor: "pointer" })
const h3_    = { color: "#e6edf3", fontSize: "14px", fontWeight: 700, margin: "0 0 12px 0" }

function SettingsModal({ onClose }) {
  const [tgToken, setTgToken]   = useState("")
  const [tgChat, setTgChat]     = useState("")
  const [mt5, setMt5]           = useState({ login:"", password:"", server:"", live_login:"", live_password:"", live_server:"" })
  const [mt5Available, setMt5Available] = useState(false)
  const [mode, setMode]         = useState("")
  const [msg, setMsg]           = useState("")
  const [err, setErr]           = useState("")
  const [busy, setBusy]         = useState(false)
  const [lic, setLic]           = useState(null)
  const [licCode, setLicCode]   = useState("")

  useEffect(() => {
    fetch(`${API}/license/status`).then(r => r.json()).then(setLic).catch(() => {})
    fetch(`${API}/settings`).then(r => r.json()).then(d => {
      setTgToken(d.telegram?.bot_token || "")
      setTgChat(d.telegram?.chat_id || "")
      setMt5Available(!!d.mt5_available)
      setMode(d.mt5?.mode || "")
      setMt5({
        login:         d.mt5?.login ?? "",
        password:      d.mt5?.password ?? "",
        server:        d.mt5?.server ?? "",
        live_login:    d.mt5?.live_login ?? "",
        live_password: d.mt5?.live_password ?? "",
        live_server:   d.mt5?.live_server ?? "",
      })
    }).catch(() => setErr("Could not load settings — is the backend running?"))
  }, [])

  const save = async () => {
    setBusy(true); setErr(""); setMsg("")
    try {
      const r = await fetch(`${API}/settings`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ telegram: { bot_token: tgToken, chat_id: tgChat }, mt5 }),
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Save failed")
      setMsg(d.changed?.length ? `Saved: ${d.changed.join(", ")} ✔` : "Nothing changed (masked values are ignored).")
    } catch (e) { setErr(String(e.message || e)) }
    finally { setBusy(false) }
  }

  const activateLicense = async () => {
    setBusy(true); setErr(""); setMsg("")
    try {
      const r = await fetch(`${API}/license/activate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: licCode }),
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Activation failed")
      setLic(d); setLicCode("")
      setMsg(`License active until ${d.expires_on} ✔`)
    } catch (e) { setErr(String(e.message || e)) }
    finally { setBusy(false) }
  }

  const testTelegram = async () => {
    setBusy(true); setErr(""); setMsg("")
    try {
      const r = await fetch(`${API}/test-telegram`)
      const d = await r.json()
      setMsg(d.success || d.sent || r.ok ? "Test message sent — check your Telegram ✔" : "Test failed — check token and chat ID")
    } catch (e) { setErr(String(e.message || e)) }
    finally { setBusy(false) }
  }

  const field = (lab, val, set, type = "text", ph = "") => (
    <div>
      <label style={label_}>{lab}</label>
      <input style={input_} type={type} value={val} placeholder={ph}
             onChange={e => set(e.target.value)} />
    </div>
  )

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.65)", zIndex: 1000,
                  display: "flex", alignItems: "center", justifyContent: "center" }}
         onClick={onClose}>
      <div style={{ ...card, width: "640px", maxHeight: "85vh", overflowY: "auto", margin: 0 }}
           onClick={e => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "14px" }}>
          <h3 style={{ ...h3_, margin: 0 }}>⚙️ Settings</h3>
          <button style={btn("#21262d", "#e6edf3")} onClick={onClose}>✕ Close</button>
        </div>

        {/* Telegram */}
        <div style={{ marginBottom: "18px" }}>
          <div style={{ color: "#4dabf7", fontSize: "13px", fontWeight: 700, marginBottom: "8px" }}>📨 Telegram alerts</div>
          <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: "10px" }}>
            {field("BOT TOKEN", tgToken, setTgToken, "text", "paste full token to change")}
            {field("CHAT ID", tgChat, setTgChat)}
          </div>
          <div style={{ marginTop: "8px" }}>
            <button style={btn("#1a3a5c", "#4dabf7")} disabled={busy} onClick={testTelegram}>
              Send test message
            </button>
          </div>
        </div>

        {/* MT5 */}
        <div style={{ marginBottom: "18px" }}>
          <div style={{ color: "#4dabf7", fontSize: "13px", fontWeight: 700, marginBottom: "8px" }}>
            🤖 MT5 terminal {mode && <span style={{ color: "#8b949e", fontWeight: 400 }}>(current mode: {mode})</span>}
          </div>
          {!mt5Available && (
            <div style={{ color: "#d29922", fontSize: "12px", marginBottom: "8px" }}>
              MT5 module not loaded on the backend — fields are shown but won't apply until MetaTrader5 is available.
            </div>
          )}
          <div style={{ color: "#8b949e", fontSize: "11px", marginBottom: "6px" }}>DEMO ACCOUNT</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "10px", marginBottom: "10px" }}>
            {field("LOGIN", mt5.login, v => setMt5(m => ({ ...m, login: v })))}
            {field("PASSWORD", mt5.password, v => setMt5(m => ({ ...m, password: v })), "password", "type to change")}
            {field("SERVER", mt5.server, v => setMt5(m => ({ ...m, server: v })), "text", "e.g. VantageMarkets-Demo")}
          </div>
          <div style={{ color: "#8b949e", fontSize: "11px", marginBottom: "6px" }}>LIVE ACCOUNT</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "10px" }}>
            {field("LOGIN", mt5.live_login, v => setMt5(m => ({ ...m, live_login: v })))}
            {field("PASSWORD", mt5.live_password, v => setMt5(m => ({ ...m, live_password: v })), "password", "type to change")}
            {field("SERVER", mt5.live_server, v => setMt5(m => ({ ...m, live_server: v })), "text", "e.g. VantageMarkets-Live15")}
          </div>
          <div style={{ color: "#8b949e", fontSize: "11px", marginTop: "8px" }}>
            Passwords show as •••• — leave them untouched to keep the current ones. New connections use updated
            credentials on the next MT5 refresh.
          </div>
        </div>

        {/* License */}
        <div style={{ marginBottom: "18px" }}>
          <div style={{ color: "#4dabf7", fontSize: "13px", fontWeight: 700, marginBottom: "8px" }}>🔐 License</div>
          <div style={{ fontSize: "13px", marginBottom: "8px",
                        color: lic?.activated ? "#3fb950" : "#f85149" }}>
            {lic?.activated
              ? <>Active — expires <b>{lic.expires_on}</b> ({lic.days_left} days left, {lic.duration})</>
              : lic?.reason === "expired"
                ? <>Expired on {lic.expired_on} — enter a new code below.</>
                : "Not activated."}
          </div>
          <div style={{ display: "flex", gap: "10px" }}>
            <input style={{ ...input_, fontFamily: "monospace" }} placeholder="SMT-12M-YYYYMMDD-XXXXXXXXXX"
                   value={licCode} onChange={e => setLicCode(e.target.value)} />
            <button style={{ ...btn("#1a3a5c", "#4dabf7"), whiteSpace: "nowrap" }}
                    disabled={busy || !licCode.trim()} onClick={activateLicense}>
              Activate / Renew
            </button>
          </div>
        </div>

        {msg && <div style={{ color: "#3fb950", fontSize: "13px", marginBottom: "8px" }}>{msg}</div>}
        {err && <div style={{ color: "#f85149", fontSize: "13px", marginBottom: "8px" }}>{err}</div>}

        <button style={btn(busy ? "#21262d" : "#238636")} disabled={busy} onClick={save}>
          {busy ? "Working…" : "💾 Save settings"}
        </button>
      </div>
    </div>
  )
}

export default function StrategyBuilder() {
  // ── Catalogues from backend ────────────────────────────────────────────────
  const [catalogue, setCatalogue]         = useState([])
  const [exitCatalogue, setExitCatalogue] = useState([])

  // ── Strategy definition state ──────────────────────────────────────────────
  const [name, setName]           = useState("")
  const [asset, setAsset]         = useState("Gold")
  const [timeframe, setTimeframe] = useState("15m")
  const [direction, setDirection] = useState("both")
  const [conditions, setConditions] = useState([])
  const [exitConditions, setExitConditions] = useState([])
  const [slType, setSlType]       = useState("atr")
  const [slValue, setSlValue]     = useState(1.5)
  const [useTp, setUseTp]         = useState(true)
  const [rrRatio, setRrRatio]     = useState(2.0)
  const [days, setDays]           = useState(30)

  // ── UI state ───────────────────────────────────────────────────────────────
  const [running, setRunning]     = useState(false)
  const [result, setResult]       = useState(null)
  const [error, setError]         = useState("")
  const [saved, setSaved]         = useState([])
  const [savedId, setSavedId]     = useState(null)
  const [notice, setNotice]       = useState("")
  const [showSettings, setShowSettings] = useState(false)

  const loadSaved = useCallback(async () => {
    try {
      const res  = await fetch(`${API}/strategy-builder/list`)
      const data = await res.json()
      setSaved(data.strategies || [])
    } catch { /* backend offline */ }
  }, [])

  useEffect(() => {
    fetch(`${API}/strategy-builder/options`)
      .then(r => r.json())
      .then(d => { setCatalogue(d.conditions || []); setExitCatalogue(d.exits || []) })
      .catch(() => { setCatalogue([]); setExitCatalogue([]) })
    loadSaved()
  }, [loadSaved])

  const flash = (msg) => { setNotice(msg); setTimeout(() => setNotice(""), 4000) }

  // ── Condition row helpers ──────────────────────────────────────────────────
  const addCondition = () => {
    const first = catalogue[0]
    if (!first) return
    setConditions(c => [...c, makeCondition(first)])
  }

  const makeCondition = (meta) => {
    const cond = { type: meta.type }
    for (const p of meta.params || []) cond[p.name] = p.default
    return cond
  }

  const changeConditionType = (idx, type) => {
    const meta = catalogue.find(c => c.type === type)
    if (!meta) return
    setConditions(c => c.map((row, i) => (i === idx ? makeCondition(meta) : row)))
  }

  const changeParam = (idx, pname, value) => {
    setConditions(c => c.map((row, i) =>
      i === idx ? { ...row, [pname]: value } : row))
  }

  const removeCondition = (idx) =>
    setConditions(c => c.filter((_, i) => i !== idx))

  // ── Exit condition row helpers ─────────────────────────────────────────────
  const addExitCondition = () => {
    const first = exitCatalogue[0]
    if (!first) return
    setExitConditions(c => [...c, makeCondition(first)])
  }

  const changeExitType = (idx, type) => {
    const meta = exitCatalogue.find(c => c.type === type)
    if (!meta) return
    setExitConditions(c => c.map((row, i) => (i === idx ? makeCondition(meta) : row)))
  }

  const changeExitParam = (idx, pname, value) =>
    setExitConditions(c => c.map((row, i) =>
      i === idx ? { ...row, [pname]: value } : row))

  const removeExitCondition = (idx) =>
    setExitConditions(c => c.filter((_, i) => i !== idx))

  const buildDefinition = () => ({
    id:        savedId || undefined,
    name:      name.trim() || "Unnamed Strategy",
    asset, timeframe, direction,
    conditions,
    exit_conditions: exitConditions,
    risk: { sl_type: slType, sl_value: Number(slValue), rr_ratio: Number(rrRatio), use_tp: useTp },
  })

  // ── Actions ────────────────────────────────────────────────────────────────
  const runBacktest = async () => {
    setError(""); setResult(null)
    if (conditions.length === 0) { setError("Add at least one condition first."); return }
    setRunning(true)
    try {
      const res  = await fetch(`${API}/strategy-builder/backtest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ definition: buildDefinition(), days: Number(days) }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || "Backtest failed")
      setResult(data)
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setRunning(false)
    }
  }

  const saveStrategy = async () => {
    setError("")
    if (!name.trim()) { setError("Give your strategy a name before saving."); return }
    try {
      const res  = await fetch(`${API}/strategy-builder/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ definition: buildDefinition(), result }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || "Save failed")
      setSavedId(data.id)
      flash(`Saved ✔ (${data.id})`)
      loadSaved()
    } catch (e) { setError(String(e.message || e)) }
  }

  const deploy = async (strategyId, targetAsset, enable) => {
    setError("")
    try {
      const res  = await fetch(`${API}/strategy-builder/deploy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ strategy_id: strategyId, asset: targetAsset, enable }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || "Deploy failed")
      flash(enable ? `Added to ${targetAsset} signals ✔` : `Removed from ${targetAsset}`)
      loadSaved()
    } catch (e) { setError(String(e.message || e)) }
  }

  const deleteStrategy = async (strategyId) => {
    if (!window.confirm("Delete this strategy?")) return
    try {
      await fetch(`${API}/strategy-builder/${strategyId}`, { method: "DELETE" })
      if (savedId === strategyId) setSavedId(null)
      loadSaved()
    } catch { /* ignore */ }
  }

  const loadIntoForm = (entry) => {
    const d = entry.definition
    setSavedId(d.id); setName(d.name || "")
    setAsset(d.asset || "Gold"); setTimeframe(d.timeframe || "15m")
    setDirection(d.direction || "both")
    setConditions(d.conditions || [])
    setExitConditions(d.exit_conditions || [])
    setSlType(d.risk?.sl_type || "atr"); setSlValue(d.risk?.sl_value ?? 1.5)
    setRrRatio(d.risk?.rr_ratio ?? 2.0); setUseTp(d.risk?.use_tp ?? true)
    setResult(null)
    window.scrollTo({ top: 0, behavior: "smooth" })
  }

  const newStrategy = () => {
    setSavedId(null); setName(""); setConditions([]); setExitConditions([])
    setResult(null); setError("")
  }

  // ── Grouped catalogue for the dropdown ─────────────────────────────────────
  const categories = [...new Set(catalogue.map(c => c.category))]
  const summary    = result?.summary

  return (
    <div style={{ maxWidth: "1100px", margin: "0 auto", padding: "20px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "14px" }}>
        <div>
          <h2 style={{ color: "#e6edf3", margin: 0, fontSize: "20px" }}>🛠️ Strategy Builder</h2>
          <div style={{ color: "#8b949e", fontSize: "12px", marginTop: "4px" }}>
            Combine conditions → backtest → deploy good strategies to live signals.
          </div>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button style={btn("#21262d", "#e6edf3")} onClick={newStrategy}>+ New strategy</button>
          <button style={btn("#21262d", "#e6edf3")} onClick={() => setShowSettings(true)}>⚙️ Settings</button>
        </div>
      </div>

      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}

      {notice && (
        <div style={{ ...card, background: "#0d2818", border: "1px solid #1f6f3f", color: "#3fb950", padding: "10px 16px" }}>
          {notice}
        </div>
      )}

      {/* ── 1 · Basics ── */}
      <div style={card}>
        <h3 style={h3_}>1 · Strategy details</h3>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr 1fr", gap: "12px" }}>
          <div>
            <label style={label_}>NAME</label>
            <input style={input_} placeholder="e.g. London Sweep + RSI" value={name}
                   onChange={e => setName(e.target.value)} />
          </div>
          <div>
            <label style={label_}>ASSET</label>
            <select style={input_} value={asset} onChange={e => setAsset(e.target.value)}>
              <option>Gold</option><option>BTC</option><option>ETH</option>
            </select>
          </div>
          <div>
            <label style={label_}>TIMEFRAME</label>
            <select style={input_} value={timeframe} onChange={e => setTimeframe(e.target.value)}>
              <option value="5m">5 min</option><option value="15m">15 min</option>
              <option value="1h">1 hour</option><option value="4h">4 hour</option>
            </select>
          </div>
          <div>
            <label style={label_}>DIRECTION</label>
            <select style={input_} value={direction} onChange={e => setDirection(e.target.value)}>
              <option value="both">Long + Short</option>
              <option value="long">Long only</option>
              <option value="short">Short only</option>
            </select>
          </div>
        </div>
      </div>

      {/* ── 2 · Conditions ── */}
      <div style={card}>
        <h3 style={h3_}>2 · Entry conditions <span style={{ color: "#8b949e", fontWeight: 400 }}>(ALL must be true)</span></h3>

        {conditions.length === 0 && (
          <div style={{ color: "#8b949e", fontSize: "13px", marginBottom: "10px" }}>
            No conditions yet — click "Add condition" to start.
          </div>
        )}

        {conditions.map((cond, idx) => {
          const meta = catalogue.find(c => c.type === cond.type) || { params: [] }
          return (
            <div key={idx} style={{ display: "flex", gap: "10px", alignItems: "flex-end", marginBottom: "10px",
                                    background: "#0d1117", border: "1px solid #21262d", borderRadius: "10px", padding: "10px" }}>
              <div style={{ flex: "0 0 320px" }}>
                <label style={label_}>CONDITION</label>
                <select style={input_} value={cond.type} onChange={e => changeConditionType(idx, e.target.value)}>
                  {categories.map(cat => (
                    <optgroup key={cat} label={cat}>
                      {catalogue.filter(c => c.category === cat).map(c => (
                        <option key={c.type} value={c.type}>{c.label}</option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </div>
              {(meta.params || []).map(p => (
                <div key={p.name} style={{ flex: "0 0 130px" }}>
                  <label style={label_}>{p.label.toUpperCase()}</label>
                  {p.kind === "choice" ? (
                    <select style={input_} value={cond[p.name]}
                            onChange={e => changeParam(idx, p.name, e.target.value)}>
                      {p.choices.map(ch => <option key={ch} value={ch}>{ch}</option>)}
                    </select>
                  ) : (
                    <input style={input_} type="number" step="any" value={cond[p.name]}
                           onChange={e => changeParam(idx, p.name, e.target.value === "" ? "" : Number(e.target.value))} />
                  )}
                </div>
              ))}
              <button title="Remove" onClick={() => removeCondition(idx)}
                      style={{ ...btn("#21262d", "#f85149"), padding: "8px 12px", marginLeft: "auto" }}>✕</button>
            </div>
          )
        })}

        <button style={btn("#1a3a5c", "#4dabf7")} onClick={addCondition}>+ Add condition</button>
      </div>

      {/* ── 3 · Exit conditions ── */}
      <div style={card}>
        <h3 style={h3_}>3 · Exit conditions <span style={{ color: "#8b949e", fontWeight: 400 }}>(stop loss always applies — these close the trade early)</span></h3>

        {exitConditions.length === 0 && (
          <div style={{ color: "#8b949e", fontSize: "13px", marginBottom: "10px" }}>
            No extra exit rules — trades close at stop loss{useTp ? " or take profit" : ""} only.
          </div>
        )}

        {exitConditions.map((cond, idx) => {
          const meta = exitCatalogue.find(c => c.type === cond.type) || { params: [] }
          return (
            <div key={idx} style={{ display: "flex", gap: "10px", alignItems: "flex-end", marginBottom: "10px",
                                    background: "#0d1117", border: "1px solid #21262d", borderRadius: "10px", padding: "10px" }}>
              <div style={{ flex: "0 0 320px" }}>
                <label style={label_}>EXIT WHEN</label>
                <select style={input_} value={cond.type} onChange={e => changeExitType(idx, e.target.value)}>
                  {exitCatalogue.map(c => (
                    <option key={c.type} value={c.type}>{c.label}</option>
                  ))}
                </select>
              </div>
              {(meta.params || []).map(p => (
                <div key={p.name} style={{ flex: "0 0 130px" }}>
                  <label style={label_}>{p.label.toUpperCase()}</label>
                  {p.kind === "choice" ? (
                    <select style={input_} value={cond[p.name]}
                            onChange={e => changeExitParam(idx, p.name, e.target.value)}>
                      {p.choices.map(ch => <option key={ch} value={ch}>{ch}</option>)}
                    </select>
                  ) : (
                    <input style={input_} type="number" step="any" value={cond[p.name]}
                           onChange={e => changeExitParam(idx, p.name, e.target.value === "" ? "" : Number(e.target.value))} />
                  )}
                </div>
              ))}
              <button title="Remove" onClick={() => removeExitCondition(idx)}
                      style={{ ...btn("#21262d", "#f85149"), padding: "8px 12px", marginLeft: "auto" }}>✕</button>
            </div>
          )
        })}

        <button style={btn("#1a3a5c", "#4dabf7")} onClick={addExitCondition}>+ Add exit rule</button>
      </div>

      {/* ── 4 · Risk ── */}
      <div style={card}>
        <h3 style={h3_}>4 · Risk &amp; backtest settings</h3>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr 1fr", gap: "12px" }}>
          <div>
            <label style={label_}>STOP LOSS TYPE</label>
            <select style={input_} value={slType} onChange={e => setSlType(e.target.value)}>
              <option value="atr">ATR multiple</option>
              <option value="fixed">Fixed points</option>
            </select>
          </div>
          <div>
            <label style={label_}>{slType === "atr" ? "ATR MULTIPLE" : "SL POINTS"}</label>
            <input style={input_} type="number" step="any" value={slValue}
                   onChange={e => setSlValue(e.target.value)} />
          </div>
          <div>
            <label style={label_}>TAKE PROFIT</label>
            <select style={input_} value={useTp ? "yes" : "no"} onChange={e => setUseTp(e.target.value === "yes")}>
              <option value="yes">Use TP (R:R)</option>
              <option value="no">No TP — exit rules only</option>
            </select>
          </div>
          <div>
            <label style={label_}>RISK : REWARD</label>
            <input style={{ ...input_, opacity: useTp ? 1 : 0.4 }} type="number" step="any" value={rrRatio}
                   disabled={!useTp} onChange={e => setRrRatio(e.target.value)} />
          </div>
          <div>
            <label style={label_}>BACKTEST DAYS</label>
            <input style={input_} type="number" value={days}
                   onChange={e => setDays(e.target.value)} />
          </div>
        </div>

        <div style={{ marginTop: "16px", display: "flex", gap: "10px", alignItems: "center" }}>
          <button style={btn(running ? "#21262d" : "#1f6feb")} disabled={running} onClick={runBacktest}>
            {running ? "Running backtest…" : "▶ Run Backtest"}
          </button>
          <button style={btn("#238636")} onClick={saveStrategy}>💾 Save Strategy</button>
          {error && <span style={{ color: "#f85149", fontSize: "13px" }}>{error}</span>}
        </div>
      </div>

      {/* ── 4 · Results ── */}
      {summary && (
        <div style={card}>
          <h3 style={h3_}>5 · Backtest result <span style={{ color: "#8b949e", fontWeight: 400 }}>({summary.asset} · {summary.timeframe} · {summary.days}d · {summary.bars} bars)</span></h3>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: "10px", marginBottom: "14px" }}>
            {[
              ["Trades",        summary.total_trades],
              ["Win rate",      `${summary.win_rate}%`],
              ["Lot size",      summary.lot_size ?? "—", "#4dabf7"],
              ["Net P&L ($)",   summary.net_usd != null ? `$${summary.net_usd}` : "—",
                                (summary.net_usd ?? 0) >= 0 ? "#3fb950" : "#f85149"],
              ["Net points",    summary.net_points,   summary.net_points >= 0 ? "#3fb950" : "#f85149"],
              ["Profit factor", summary.profit_factor, summary.profit_factor >= 1.5 ? "#3fb950" : summary.profit_factor >= 1 ? "#d29922" : "#f85149"],
              ["Max drawdown",  summary.max_drawdown_usd != null
                                  ? `$${summary.max_drawdown_usd} / ${summary.max_drawdown} pts`
                                  : summary.max_drawdown, "#d29922"],
              ["W / L",         `${summary.wins} / ${summary.losses}`],
              ["Best trade",    summary.best_trade_usd != null
                                  ? `$${summary.best_trade_usd} / ${summary.best_trade} pts`
                                  : summary.best_trade ?? "—", "#3fb950"],
              ["Worst trade",   summary.worst_trade_usd != null
                                  ? `$${summary.worst_trade_usd} / ${summary.worst_trade} pts`
                                  : summary.worst_trade ?? "—", "#f85149"],
              ["Max SL (pts)",  summary.max_sl ?? "—"],
              ["Min SL (pts)",  summary.min_sl ?? "—"],
            ].map(([lab, val, color]) => (
              <div key={lab} style={{ background: "#0d1117", borderRadius: "10px", padding: "12px", textAlign: "center" }}>
                <div style={{ fontSize: "11px", color: "#8b949e" }}>{lab}</div>
                <div style={{ fontSize: "18px", fontWeight: 700, color: color || "#e6edf3", marginTop: "4px" }}>{val}</div>
              </div>
            ))}
          </div>

          <div style={{ padding: "10px 14px", borderRadius: "10px", fontSize: "13px", fontWeight: 600,
                        background: summary.profit_factor >= 1.5 ? "#0d2818" : summary.profit_factor >= 1 ? "#2d2305" : "#2d0d0d",
                        color:      summary.profit_factor >= 1.5 ? "#3fb950" : summary.profit_factor >= 1 ? "#d29922" : "#f85149" }}>
            {summary.verdict}
          </div>

          {summary.exit_reasons && Object.keys(summary.exit_reasons).length > 0 && (
            <div style={{ marginTop: "10px", display: "flex", gap: "8px", flexWrap: "wrap" }}>
              {Object.entries(summary.exit_reasons).map(([r, count]) => (
                <span key={r} style={{ fontSize: "12px", color: "#8b949e", background: "#0d1117",
                                       border: "1px solid #21262d", borderRadius: "20px", padding: "4px 12px" }}>
                  {r.replace("_", " ")}: <b style={{ color: "#e6edf3" }}>{count}</b>
                </span>
              ))}
            </div>
          )}

          {result.trades?.length > 0 && (
            <details style={{ marginTop: "12px" }}>
              <summary style={{ color: "#4dabf7", cursor: "pointer", fontSize: "13px" }}>
                Show trades ({result.trades.length})
              </summary>
              <div style={{ maxHeight: "300px", overflowY: "auto", marginTop: "8px" }}>
                <table style={{ width: "100%", fontSize: "12px", color: "#e6edf3", borderCollapse: "collapse" }}>
                  <thead>
                    <tr style={{ color: "#8b949e", textAlign: "left" }}>
                      {["Time", "Dir", "Entry", "SL", "TP", "Exit", "Reason", "Result", "PnL (pts)", "PnL ($)"].map(hd =>
                        <th key={hd} style={{ padding: "6px", borderBottom: "1px solid #21262d" }}>{hd}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.slice().reverse().map((t, i) => (
                      <tr key={i} style={{ borderBottom: "1px solid #161b22" }}>
                        <td style={{ padding: "5px 6px" }}>{t.time}</td>
                        <td style={{ padding: "5px 6px", color: t.direction === "BUY" ? "#3fb950" : "#f85149" }}>{t.direction}</td>
                        <td style={{ padding: "5px 6px" }}>{t.entry}</td>
                        <td style={{ padding: "5px 6px" }}>{t.sl}</td>
                        <td style={{ padding: "5px 6px" }}>{t.tp ?? "—"}</td>
                        <td style={{ padding: "5px 6px" }}>{t.exit}</td>
                        <td style={{ padding: "5px 6px", color: "#8b949e" }}>{(t.reason || "").replace("_", " ")}</td>
                        <td style={{ padding: "5px 6px", color: t.outcome === "win" ? "#3fb950" : "#f85149" }}>{t.outcome}</td>
                        <td style={{ padding: "5px 6px", color: t.pnl >= 0 ? "#3fb950" : "#f85149" }}>{t.pnl}</td>
                        <td style={{ padding: "5px 6px", color: t.pnl >= 0 ? "#3fb950" : "#f85149" }}>
                          {summary.lot_size != null
                            ? `$${(t.pnl * summary.lot_size * (summary.contract_size || 1)).toFixed(2)}`
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </div>
      )}

      {/* ── 5 · Saved strategies ── */}
      <div style={card}>
        <h3 style={h3_}>📚 Saved strategies</h3>
        {saved.length === 0 && (
          <div style={{ color: "#8b949e", fontSize: "13px" }}>Nothing saved yet.</div>
        )}
        {saved.map(entry => {
          const d   = entry.definition
          const sum = entry.last_result?.summary
          return (
            <div key={d.id} style={{ display: "flex", alignItems: "center", gap: "12px", padding: "12px",
                                     background: "#0d1117", border: "1px solid #21262d", borderRadius: "10px", marginBottom: "8px", flexWrap: "wrap" }}>
              <div style={{ flex: "1 1 220px" }}>
                <div style={{ color: "#e6edf3", fontWeight: 700, fontSize: "14px" }}>{d.name}</div>
                <div style={{ color: "#8b949e", fontSize: "12px", marginTop: "2px" }}>
                  {d.asset} · {d.timeframe} · {d.conditions?.length || 0} condition(s)
                  {sum && <> · WR {sum.win_rate}% · PF {sum.profit_factor}</>}
                </div>
                {entry.deployed_to?.length > 0 && (
                  <div style={{ color: "#3fb950", fontSize: "12px", marginTop: "2px" }}>
                    ⚡ Live on: {entry.deployed_to.join(", ")}
                  </div>
                )}
              </div>
              <button style={btn("#21262d", "#4dabf7")} onClick={() => loadIntoForm(entry)}>Edit</button>
              {entry.deployed_to?.includes(d.asset) ? (
                <button style={btn("#5c1a1a", "#f85149")} onClick={() => deploy(d.id, d.asset, false)}>
                  Remove from {d.asset}
                </button>
              ) : (
                <button style={btn("#238636")} onClick={() => deploy(d.id, d.asset, true)}>
                  ⚡ Add to {d.asset} signals
                </button>
              )}
              <button style={btn("#21262d", "#f85149")} onClick={() => deleteStrategy(d.id)}>🗑</button>
            </div>
          )
        })}
      </div>
    </div>
  )
}
