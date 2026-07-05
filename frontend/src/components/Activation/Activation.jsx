import { useState } from "react"

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

export default function Activation({ status, onActivated }) {
  const [code, setCode]   = useState("")
  const [err, setErr]     = useState("")
  const [busy, setBusy]   = useState(false)

  const expired = status?.reason === "expired"

  const activate = async (e) => {
    e.preventDefault()
    setErr(""); setBusy(true)
    try {
      const r = await fetch(`${API}/license/activate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Activation failed")
      onActivated(d)
    } catch (e2) { setErr(String(e2.message || e2)) }
    finally { setBusy(false) }
  }

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
                  background: "linear-gradient(135deg, #0a0f1a 0%, #0d1520 60%, #0a1628 100%)" }}>
      <form onSubmit={activate} style={{ width: "440px", background: "#161b22", border: "1px solid #21262d",
                                         borderRadius: "16px", padding: "32px", textAlign: "center" }}>
        <div style={{ fontSize: "40px", marginBottom: "8px" }}>🔐</div>
        <h2 style={{ color: "#e6edf3", margin: "0 0 6px" }}>Smart Money Trader</h2>
        <div style={{ color: "#8b949e", fontSize: "13px", marginBottom: "20px" }}>
          {expired
            ? <>Your license expired on <b style={{ color: "#f85149" }}>{status.expired_on}</b>. Enter a new activation code to continue.</>
            : "Enter your activation code to start using the application."}
        </div>

        <input
          autoFocus
          value={code}
          onChange={e => setCode(e.target.value)}
          placeholder="SMT-12M-YYYYMMDD-XXXXXXXXXX"
          style={{ width: "100%", boxSizing: "border-box", textAlign: "center", letterSpacing: "1px",
                   background: "#0d1117", border: "1px solid #30363d", borderRadius: "10px",
                   color: "#e6edf3", padding: "12px", fontSize: "15px", fontFamily: "monospace" }}
        />

        {err && <div style={{ color: "#f85149", fontSize: "13px", marginTop: "10px" }}>{err}</div>}

        <button type="submit" disabled={busy || !code.trim()}
                style={{ marginTop: "16px", width: "100%", padding: "12px", borderRadius: "10px",
                         border: "none", cursor: "pointer", fontWeight: 700, fontSize: "14px",
                         background: busy ? "#21262d" : "linear-gradient(135deg, #1f6feb, #1a3a5c)",
                         color: "#fff" }}>
          {busy ? "Activating…" : "Activate"}
        </button>

        <div style={{ color: "#8b949e", fontSize: "11px", marginTop: "16px", lineHeight: 1.6 }}>
          Licenses are valid for 6 or 12 months from activation.<br />
          Contact your provider for an activation code.
        </div>
      </form>
    </div>
  )
}
