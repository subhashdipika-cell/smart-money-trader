import { useState, useEffect } from "react";

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

export default function MT5Panel() {
  const [mode, setMode]           = useState("paper");
  const [account, setAccount]     = useState(null);
  const [trades, setTrades]       = useState([]);
  const [loading, setLoading]     = useState(false);
  const [confirm, setConfirm]     = useState(null);
  const [prices, setPrices]       = useState({});
  const [expireHrs, setExpireHrs] = useState(8);
  const [cancelMsg, setCancelMsg] = useState("");
  const [tradeFilter, setTradeFilter]       = useState("ALL"); // ALL | DEMO | LIVE | PAPER

  const fetchStatus = async () => {
    try {
      const r = await fetch(`${API}/mt4/status`);
      const d = await r.json();
      setMode(d.mode || "paper");
      setAccount(d.account || null);
    } catch(e) {}
  };

  const fetchTrades = async () => {
    try {
      // mode=all → trades from every account (paper/demo/live), same as History page
      const r = await fetch(`${API}/mt4/trades?mode=all`);
      const d = await r.json();
      setTrades(d.trades || []);
    } catch(e) {}
  };

  const fetchPrices = async () => {
    try {
      const r = await fetch(`${API}/prices`);
      const d = await r.json();
      setPrices(d);
    } catch(e) {}
  };

  useEffect(() => {
    fetchStatus();
    fetchTrades();
    fetchPrices();
    const iv = setInterval(() => { fetchStatus(); fetchTrades(); fetchPrices(); }, 15000);
    return () => clearInterval(iv);
  }, []);

  const switchMode = async (newMode) => {
    if (newMode !== "paper" && !confirm) { setConfirm(newMode); return; }
    setLoading(true); setConfirm(null);
    try {
      await fetch(`${API}/mt4/mode/${newMode}`, { method: "POST" });
      setMode(newMode);
      // Auto-refresh MT5 connection after switching to demo/live
      if (newMode !== "paper") {
        const r = await fetch(`${API}/mt4/refresh`);
        const d = await r.json();
        if (d.account) setAccount(d.account);
      }
      await fetchStatus();
    } catch(e) {}
    setLoading(false);
  };

  const cancelOrder = async (ticket) => {
    if (!window.confirm(`Cancel pending order #${ticket}?`)) return;
    try {
      const r = await fetch(`${API}/mt4/cancel/${ticket}`, { method: "DELETE" });
      const d = await r.json();
      if (d.success) {
        setCancelMsg(`✅ Order #${ticket} cancelled successfully.`);
        setTimeout(() => setCancelMsg(""), 4000);
        setTimeout(fetchTrades, 800);
      } else {
        setCancelMsg(`❌ Failed: ${d.error}`);
        setTimeout(() => setCancelMsg(""), 5000);
      }
    } catch(e) {
      setCancelMsg("❌ Network error — is backend running?");
      setTimeout(() => setCancelMsg(""), 5000);
    }
  };

  const cancelExpired = async () => {
    if (!window.confirm(`Cancel all pending orders older than ${expireHrs} hours?`)) return;
    setLoading(true);
    try {
      const r = await fetch(`${API}/mt4/cancel-expired?max_age_hours=${expireHrs}`, { method: "POST" });
      const d = await r.json();
      const count = d.cancelled?.length || 0;
      setCancelMsg(count > 0
        ? `✅ Cancelled ${count} expired order(s).`
        : `ℹ️ No orders older than ${expireHrs}h found.`);
      setTimeout(() => setCancelMsg(""), 5000);
      if (count > 0) setTimeout(fetchTrades, 800);
    } catch(e) {
      setCancelMsg("❌ Network error");
      setTimeout(() => setCancelMsg(""), 4000);
    }
    setLoading(false);
  };

  const closeAll = async () => {
    if (!window.confirm("Close ALL open positions?")) return;
    await fetch(`${API}/mt4/close-all`, { method: "POST" });
    setTimeout(fetchTrades, 1000);
  };

  const refreshMT5 = async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API}/mt4/refresh`);
      const d = await r.json();
      if (d.account) setAccount(d.account);
    } catch(e) {}
    setLoading(false);
  };

  const modeColor = { paper: "#3fb950", demo: "#f0b429", live: "#f85149" };
  const modeIcon  = { paper: "📋", demo: "🟡", live: "🔴" };
  const modeBg    = { paper: "#162d1f", demo: "#2d2005", live: "#2d1b1b" };

  return (
    <div style={{ background:"#0d1117", color:"#e6edf3", padding:"24px",
      fontFamily:"monospace", minHeight:"100vh" }}>

      {/* Header — title + account cards + mode buttons all in one row */}
      <div style={{ display:"flex", alignItems:"center", gap:"16px", marginBottom:"20px", flexWrap:"wrap" }}>

        {/* Left: title + status badge */}
        <div style={{ flexShrink:0 }}>
          <h2 style={{ margin:0, fontSize:"20px", color:"#58a6ff" }}>🤖 MT5 Auto-Trader</h2>
          <p style={{ margin:"4px 0 6px", color:"#8b949e", fontSize:"13px" }}>
            VantageMarkets-Demo · Account 25497856
          </p>
          <div style={{ display:"inline-flex", alignItems:"center", gap:"6px",
            padding:"4px 10px", borderRadius:"20px",
            background: modeBg[mode], border:`1px solid ${modeColor[mode]}` }}>
            <span style={{ width:"7px", height:"7px", borderRadius:"50%",
              background:modeColor[mode], boxShadow:`0 0 6px ${modeColor[mode]}`,
              display:"inline-block" }}/>
            <span style={{ fontSize:"11px", fontWeight:"bold", color:modeColor[mode] }}>
              {mode === "paper" && "PAPER"}
              {mode === "demo"  && "DEMO LIVE"}
              {mode === "live"  && "REAL LIVE"}
            </span>
          </div>
        </div>

        {/* Centre: compact account cards */}
        {account && (
          <div style={{ display:"flex", gap:"8px", flex:1 }}>
            {[
              { label:"MODE",        value: mode.toUpperCase(), color: modeColor[mode] },
              { label:"BALANCE",     value: isNaN(Number(account.balance)) || account.balance === "—" || account.balance === "Paper" ? (account.balance || "—") : `$${Number(account.balance).toFixed(2)}` },
              { label:"EQUITY",      value: isNaN(Number(account.equity))  || account.equity  === "—" ? "—" : `$${Number(account.equity).toFixed(2)}` },
              { label:"FREE MARGIN", value: isNaN(Number(account.free_margin)) || account.free_margin === "—" ? "—" : `$${Number(account.free_margin).toFixed(2)}` },
            ].map(item => (
              <div key={item.label} style={{ background:"#161b22", borderRadius:"8px",
                padding:"8px 12px", border:"1px solid #30363d", flex:1, minWidth:0 }}>
                <p style={{ margin:"0 0 2px", color:"#8b949e", fontSize:"10px", textTransform:"uppercase", letterSpacing:".4px" }}>{item.label}</p>
                <p style={{ margin:0, fontSize:"14px", fontWeight:"bold",
                  color: item.color || "#e6edf3", whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis" }}>{item.value}</p>
              </div>
            ))}
          </div>
        )}

        {/* Right: mode buttons */}
        <div style={{ display:"flex", gap:"8px", alignItems:"center", flexShrink:0 }}>
          {[
            { key:"paper", label:"📋 Paper",    desc:"No real orders" },
            { key:"demo",  label:"🟡 Demo Live", desc:"MT5 Demo account" },
            { key:"live",  label:"🔴 Real Live", desc:"Real money!" }
          ].map(btn => (
            <button key={btn.key} onClick={() => switchMode(btn.key)}
              disabled={loading} title={btn.desc}
              style={{
                padding:"10px 18px", borderRadius:"8px",
                border: mode === btn.key ? `2px solid ${modeColor[btn.key]}` : "1px solid #30363d",
                background: mode === btn.key ? modeBg[btn.key] : "#161b22",
                color: mode === btn.key ? modeColor[btn.key] : "#8b949e",
                fontWeight: mode === btn.key ? 700 : 500,
                cursor:"pointer", fontSize:"13px",
                boxShadow: mode === btn.key ? `0 0 10px ${modeColor[btn.key]}40` : "none",
                transition:"all 0.2s"
              }}>
              {btn.label}
            </button>
          ))}
          <button onClick={refreshMT5} disabled={loading}
            title="Connect to MT5 and refresh account info"
            style={{ padding:"10px 14px", borderRadius:"8px",
              border:"1px solid #30363d", background:"#161b22",
              color: loading ? "#8b949e" : "#58a6ff",
              cursor:"pointer", fontSize:"13px" }}>
            {loading ? "⏳" : "🔄 Refresh"}
          </button>
        </div>
      </div>

      {/* Confirmation dialog */}
      {confirm && (
        <div style={{ background: confirm==="live" ? "#2d1b1b" : "#2d2005",
          border:`1px solid ${modeColor[confirm]}`,
          borderRadius:"8px", padding:"16px", marginBottom:"20px" }}>
          <p style={{ color:modeColor[confirm], margin:"0 0 8px", fontWeight:"bold", fontSize:"15px" }}>
            {confirm === "demo" ? "🟡 Switch to Demo Live Trading?" : "⚠️ Switch to REAL MONEY Trading?"}
          </p>
          <p style={{ color:"#e6edf3", margin:"0 0 12px", fontSize:"13px" }}>
            {confirm === "demo"
              ? "Signals will place REAL orders on your MT5 Demo account ($1,000 demo funds). MT5 must be running."
              : "⛔ Signals will place orders with REAL MONEY on your live account. Make sure live credentials are set in mt4_config.json."}
          </p>
          <div style={{ display:"flex", gap:"8px" }}>
            <button onClick={() => switchMode(confirm)} style={{
              padding:"8px 16px", background:modeBg[confirm],
              color:modeColor[confirm], border:`1px solid ${modeColor[confirm]}`,
              borderRadius:"6px", cursor:"pointer", fontWeight:"bold"
            }}>Yes, Switch</button>
            <button onClick={() => setConfirm(null)} style={{
              padding:"8px 16px", background:"#21262d", color:"#8b949e",
              border:"1px solid #30363d", borderRadius:"6px", cursor:"pointer"
            }}>Cancel</button>
          </div>
        </div>
      )}


      {/* ── REAL LIVE warning banner ─────────────────────────────────────── */}
      {mode === "live" && (
        <div style={{
          background:"linear-gradient(90deg,#2d0000,#1a0000)",
          border:"2px solid #f85149", borderRadius:"10px",
          padding:"14px 20px", marginBottom:"20px",
          display:"flex", alignItems:"center", gap:"14px"
        }}>
          <span style={{ fontSize:"28px" }}>⛔</span>
          <div>
            <p style={{ margin:"0 0 2px", color:"#f85149", fontWeight:800, fontSize:"15px",
              letterSpacing:".5px" }}>REAL MONEY — LIVE TRADING ACTIVE</p>
            <p style={{ margin:0, color:"#e6edf3", fontSize:"12px" }}>
              All signals are placing orders with real funds on your live MT5 account.
              Switch to <strong>Demo</strong> or <strong>Paper</strong> to stop.
            </p>
          </div>
          <button onClick={() => switchMode("demo")} style={{
            marginLeft:"auto", padding:"8px 16px", borderRadius:"6px",
            background:"#2d2005", border:"1px solid #f0b429",
            color:"#f0b429", cursor:"pointer", fontWeight:700, fontSize:"12px", flexShrink:0
          }}>Switch to Demo</button>
        </div>
      )}

      {/* Lot sizes */}
      <div style={{ marginBottom:"24px" }}>
        <p style={{ color:"#8b949e", fontSize:"11px", textTransform:"uppercase",
          letterSpacing:"0.5px", margin:"0 0 8px" }}>Minimum Lot Sizes</p>
        <div style={{ display:"flex", gap:"10px" }}>
          {[
            ["BTCUSD",  "BTCUSD",  "0.01 lot", "~1 BTC notional", "#f7931a"],
            ["ETHUSD",  "ETHUSD",  "0.01 lot", "~0.1 ETH",        "#627eea"],
            ["XAUUSD+", "XAUUSD+", "0.01 lot", "~1 oz Gold",      "#f0b429"],
          ].map(([s, priceKey, l, v, accent]) => {
            const livePrice = prices[priceKey]?.price;
            return (
              <div key={s} style={{ background:"#161b22", borderRadius:"8px",
                padding:"12px 16px", border:`1px solid #30363d`, flex:1, textAlign:"center",
                borderTop: `2px solid ${accent}` }}>
                <p style={{ margin:"0 0 4px", color: accent, fontSize:"13px", fontWeight:"bold" }}>{s}</p>
                <p style={{ margin:"0 0 2px", color:"#e6edf3", fontSize:"20px", fontWeight:800, fontFamily:"monospace" }}>
                  {livePrice ? `$${livePrice.toLocaleString()}` : "—"}
                </p>
                <p style={{ margin:"0 0 2px", color:"#8b949e", fontSize:"11px" }}>{l}</p>
                <p style={{ margin:0, color:"#8b949e", fontSize:"10px" }}>{v}</p>
              </div>
            );
          })}
        </div>
      </div>

      {/* Cancel message toast */}
      {cancelMsg && (
        <div style={{ marginBottom:"10px", padding:"8px 14px", borderRadius:"8px",
          background: cancelMsg.startsWith("✅") ? "rgba(63,185,80,0.12)" : cancelMsg.startsWith("ℹ") ? "rgba(77,171,247,0.12)" : "rgba(248,81,73,0.12)",
          border: `1px solid ${cancelMsg.startsWith("✅") ? "#3fb950" : cancelMsg.startsWith("ℹ") ? "#58a6ff" : "#f85149"}`,
          color: cancelMsg.startsWith("✅") ? "#3fb950" : cancelMsg.startsWith("ℹ") ? "#58a6ff" : "#f85149",
          fontSize:"13px", fontWeight:600 }}>
          {cancelMsg}
        </div>
      )}

      {/* Trade log header */}
      {/* Mode filter tabs — lets you view DEMO / LIVE / PAPER trades separately */}
      <div style={{ display:"flex", gap:"6px", marginBottom:"10px", flexWrap:"wrap" }}>
        {[
          { key:"ALL",   label:"All",        color:"#58a6ff" },
          { key:"DEMO",  label:"🟡 Demo",    color:"#f0b429" },
          { key:"LIVE",  label:"🔴 Real",    color:"#f85149" },
          { key:"PAPER", label:"📋 Paper",   color:"#3fb950" },
        ].map(f => (
          <button key={f.key} onClick={() => setTradeFilter(f.key)}
            style={{
              padding:"4px 12px", borderRadius:"5px", border:"none", cursor:"pointer",
              fontSize:"11px", fontWeight: tradeFilter===f.key ? 700 : 500,
              background: tradeFilter===f.key ? "rgba(88,166,255,0.1)" : "#161b22",
              color: tradeFilter===f.key ? f.color : "#8b949e",
              outline: tradeFilter===f.key ? `1px solid ${f.color}` : "1px solid #30363d",
            }}>{f.label}</button>
        ))}
      </div>

      <div style={{ display:"flex", justifyContent:"space-between",
        alignItems:"center", marginBottom:"10px", flexWrap:"wrap", gap:"8px" }}>
        {(() => {
          const visible = tradeFilter === "ALL" ? trades
            : trades.filter(t => (t.mode||"demo").toUpperCase() === tradeFilter)
          return (
            <p style={{ color:"#8b949e", fontSize:"11px", textTransform:"uppercase",
              letterSpacing:"0.5px", margin:0 }}>
              Trade Log ({visible.length}{tradeFilter!=="ALL" ? ` · ${tradeFilter}` : ""}) —&nbsp;
              <span style={{ color:"#3fb950" }}>
                {visible.filter(t=>t.mt5_state==="active").length} active
              </span>
              &nbsp;·&nbsp;
              <span style={{ color:"#f0b429" }}>
                {visible.filter(t=>t.mt5_state==="pending").length} pending
              </span>
              &nbsp;·&nbsp;
              <span style={{ color:"#8b949e" }}>
                {visible.filter(t=>t.mt5_state==="closed").length} closed
              </span>
            </p>
          )
        })()}

        <div style={{ display:"flex", gap:"8px", alignItems:"center" }}>
          {/* Auto-expiry control */}
          <div style={{ display:"flex", alignItems:"center", gap:"6px",
            background:"#0d1117", border:"1px solid #30363d",
            borderRadius:"6px", padding:"4px 10px" }}>
            <span style={{ fontSize:"11px", color:"#8b949e", whiteSpace:"nowrap" }}>
              Auto-cancel after
            </span>
            <select value={expireHrs} onChange={e => setExpireHrs(Number(e.target.value))}
              style={{ background:"#0d1117", color:"#e6edf3", border:"none",
                fontSize:"12px", cursor:"pointer", outline:"none" }}>
              {[2, 4, 6, 8, 12, 24].map(h => (
                <option key={h} value={h}>{h}h</option>
              ))}
            </select>
          </div>

          <button onClick={cancelExpired} disabled={loading} style={{
            padding:"5px 12px", background:"#2d2005", color:"#f0b429",
            border:"1px solid #f0b429", borderRadius:"6px",
            cursor:"pointer", fontSize:"12px", fontWeight:600 }}>
            ⏰ Cancel Expired
          </button>

          {trades.some(t=>t.status==="open") && mode !== "paper" && (
            <button onClick={closeAll} style={{
              padding:"5px 12px", background:"#2d1b1b", color:"#f85149",
              border:"1px solid #f85149", borderRadius:"6px",
              cursor:"pointer", fontSize:"12px", fontWeight:600 }}>
              ✕ Close All
            </button>
          )}
        </div>
      </div>

      {/* Trade log table */}
      <div style={{ background:"#161b22", borderRadius:"8px",
        overflow:"auto", border:"1px solid #30363d" }}>
        {trades.length === 0 ? (
          <p style={{ color:"#8b949e", padding:"24px", textAlign:"center", margin:0 }}>
            No trades yet. Waiting for SMT signals...
          </p>
        ) : (
          <table style={{ width:"100%", borderCollapse:"collapse", fontSize:"12px" }}>
            <thead>
              <tr style={{ background:"#0d1117" }}>
                {["Time","Symbol","Dir","Entry","SL","TP","Lots","Unreal Pts","Unreal USD","Mode","Status","Ticket","Action"].map(h => (
                  <th key={h} style={{ padding:"10px 10px", color:"#8b949e",
                    textAlign:"left", borderBottom:"1px solid #30363d",
                    fontWeight:"normal", whiteSpace:"nowrap" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(tradeFilter === "ALL" ? trades
                : trades.filter(t => (t.mode||"demo").toUpperCase() === tradeFilter)
              ).slice(0,100).map((t,i) => {
                const uPts     = t.unreal_pts;
                const uUsd     = t.unreal_usd;
                const uColor   = uPts > 0 ? "#3fb950" : uPts < 0 ? "#f85149" : "#8b949e";
                // Unknown state: only treat as pending if the local log still says "open";
                // otherwise the order was filled/cancelled long ago → show Closed.
                const isPending = t.mt5_state === "pending" ||
                                  (t.mt5_state === undefined && t.status === "open");
                const isActive  = t.mt5_state === "active";
                const isClosed  = t.mt5_state === "closed" ||
                                  (t.mt5_state === undefined && t.status !== "open");

                return (
                  <tr key={i} style={{
                    borderBottom:"1px solid #21262d",
                    opacity: isClosed ? 0.5 : 1,
                    background: isPending ? "rgba(240,180,41,0.03)" : "transparent"
                  }}>
                    <td style={{ padding:"8px 10px", color:"#8b949e", fontSize:"11px",
                      whiteSpace:"nowrap" }}>{t.time_ist}</td>
                    <td style={{ padding:"8px 10px", color:"#58a6ff", fontWeight:"bold" }}>{t.symbol}</td>
                    <td style={{ padding:"8px 10px" }}>
                      <span style={{
                        background: t.direction==="BUY" ? "#162d1f" : "#2d1b1b",
                        color: t.direction==="BUY" ? "#3fb950" : "#f85149",
                        padding:"2px 8px", borderRadius:"4px", fontSize:"11px", fontWeight:"bold"
                      }}>{t.direction}</span>
                    </td>
                    <td style={{ padding:"8px 10px", color:"#e6edf3" }}>{t.entry}</td>
                    <td style={{ padding:"8px 10px", color:"#f85149" }}>{t.sl}</td>
                    <td style={{ padding:"8px 10px", color:"#3fb950" }}>{t.tp}</td>
                    <td style={{ padding:"8px 10px", color:"#e6edf3" }}>{t.lot}</td>

                    {/* Unrealized P&L (active) or Realized P&L (closed) */}
                    <td style={{ padding:"8px 10px", color:uColor, fontWeight:"bold" }}>
                      {isActive && uPts !== null && uPts !== undefined
                        ? `${uPts > 0 ? "+" : ""}${uPts}`
                        : <span style={{ color:"#8b949e", fontSize:"11px" }}>—</span>}
                    </td>
                    <td style={{ padding:"8px 10px", fontWeight:"bold" }}>
                      {isActive && uUsd !== null && uUsd !== undefined ? (
                        <span style={{ color: uColor }}>{uUsd > 0 ? "+" : ""}${uUsd}</span>
                      ) : isClosed && t.realized_usd !== null && t.realized_usd !== undefined ? (
                        <span style={{ color: t.realized_usd > 0 ? "#3fb950" : t.realized_usd < 0 ? "#f85149" : "#8b949e" }}>
                          {t.realized_usd > 0 ? "+" : ""}${t.realized_usd}
                        </span>
                      ) : (
                        <span style={{ color:"#8b949e", fontSize:"11px" }}>—</span>
                      )}
                    </td>

                    <td style={{ padding:"8px 10px" }}>
                      <span style={{ color:modeColor[t.mode]||"#8b949e", fontSize:"11px" }}>
                        {modeIcon[t.mode]||"?"} {t.mode}
                      </span>
                    </td>

                    {/* Status badge — clearly shows Pending vs Active vs Closed */}
                    <td style={{ padding:"8px 10px" }}>
                      {isPending ? (
                        <span style={{ background:"rgba(240,180,41,0.12)", color:"#f0b429",
                          padding:"2px 8px", borderRadius:"4px", fontSize:"11px", fontWeight:"bold" }}>
                          ⏳ Pending
                        </span>
                      ) : isActive ? (
                        <span style={{ background:"rgba(63,185,80,0.12)", color:"#3fb950",
                          padding:"2px 8px", borderRadius:"4px", fontSize:"11px", fontWeight:"bold" }}>
                          🟢 Active
                        </span>
                      ) : isClosed ? (
                        <span style={{ background:"rgba(139,148,158,0.1)", color:"#8b949e",
                          padding:"2px 8px", borderRadius:"4px", fontSize:"11px" }}>
                          ✅ Filled/Closed
                        </span>
                      ) : (
                        <span style={{ background:"#1b2a3b", color:"#58a6ff",
                          padding:"2px 8px", borderRadius:"4px", fontSize:"11px" }}>
                          {t.status}
                        </span>
                      )}
                    </td>

                    <td style={{ padding:"8px 10px", color:"#8b949e", fontSize:"11px" }}>
                      {String(t.ticket).slice(0,18)}
                    </td>

                    {/* Cancel / Close button for ALL live orders */}
                    <td style={{ padding:"8px 10px" }}>
                      {!isClosed ? (
                        <button
                          onClick={() => cancelOrder(t.ticket)}
                          title={isPending
                            ? `Cancel pending order #${t.ticket}`
                            : `Close active position #${t.ticket} at market price`}
                          style={{
                            padding:"3px 10px",
                            background: isPending ? "#2d2005" : "#2d1b1b",
                            color:      isPending ? "#f0b429"  : "#f85149",
                            border:     `1px solid ${isPending ? "#f0b42966" : "#f8514966"}`,
                            borderRadius:"5px", cursor:"pointer",
                            fontSize:"11px", fontWeight:600,
                            transition:"all .15s"
                          }}>
                          {isPending ? "🗑 Cancel" : "✕ Close"}
                        </button>
                      ) : (
                        <span style={{ color:"#30363d", fontSize:"11px" }}>—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}