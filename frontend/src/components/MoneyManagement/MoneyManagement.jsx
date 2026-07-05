import { useState, useEffect, useCallback } from "react"

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

const card   = { background: "#161b22", border: "1px solid #21262d", borderRadius: "12px", padding: "18px", marginBottom: "16px" }
const label_ = { fontSize: "11px", color: "#8b949e", marginBottom: "4px", display: "block", letterSpacing: "0.4px" }
const input_ = { background: "#0d1117", border: "1px solid #30363d", borderRadius: "8px", color: "#e6edf3", padding: "8px 10px", fontSize: "14px", width: "100%", boxSizing: "border-box" }
const h3_    = { color: "#e6edf3", fontSize: "14px", fontWeight: 700, margin: "0 0 12px 0" }
const btn    = (bg, color = "#fff") => ({ background: bg, color, border: "none", borderRadius: "8px", padding: "8px 14px", fontSize: "12px", fontWeight: 600, cursor: "pointer" })

const floorLot = (lot, step = 0.01) => Math.floor(lot / step + 1e-9) * step

export default function MoneyManagement() {
  // ── Inputs ─────────────────────────────────────────────────────────────────
  const [capital, setCapital]       = useState(10000)
  const [riskTrade, setRiskTrade]   = useState(1)     // % per trade
  const [riskDay, setRiskDay]       = useState(3)     // % per day
  const [asset, setAsset]           = useState("Gold")
  const [timeframe, setTimeframe]   = useState("1h")
  const [customSl, setCustomSl]     = useState("")    // optional manual SL in points
  const [rr, setRr]                 = useState(2)

  // ── Market info ────────────────────────────────────────────────────────────
  const [info, setInfo]     = useState(null)
  const [error, setError]   = useState("")
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState("")

  const loadInfo = useCallback(async () => {
    setLoading(true); setError("")
    try {
      const r = await fetch(`${API}/money/market-info?asset=${asset}&timeframe=${timeframe}`)
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Failed to load market data")
      setInfo(d)
    } catch (e) {
      setInfo(null); setError(String(e.message || e))
    } finally { setLoading(false) }
  }, [asset, timeframe])

  useEffect(() => { loadInfo() }, [loadInfo])

  const flash = (m) => { setNotice(m); setTimeout(() => setNotice(""), 4000) }

  // ── Core math ──────────────────────────────────────────────────────────────
  const cap       = Number(capital) || 0
  const riskUsd   = cap * (Number(riskTrade) || 0) / 100      // $ at risk per trade
  const dayUsd    = cap * (Number(riskDay) || 0) / 100        // $ at risk per day
  const maxLosses = riskUsd > 0 ? Math.floor(dayUsd / riskUsd) : 0

  // One sizing row: given SL distance in points → lot size + real risk
  const sizeFor = (slPts) => {
    if (!info || !slPts || slPts <= 0) return null
    const cs      = info.contract_size
    const rawLot  = riskUsd / (slPts * cs)
    const lot     = Math.max(floorLot(rawLot, info.lot_step), 0)
    const tooSmall = rawLot < info.min_lot
    const usedLot = tooSmall ? info.min_lot : lot
    const realRisk = usedLot * slPts * cs                      // $ actually lost if SL hits
    return {
      slPts:    +slPts.toFixed(2),
      lot:      +usedLot.toFixed(2),
      rawLot,
      tooSmall,
      realRisk: +realRisk.toFixed(2),
      tpPts:    +(slPts * (Number(rr) || 2)).toFixed(2),
      tpUsd:    +(usedLot * slPts * (Number(rr) || 2) * cs).toFixed(2),
    }
  }

  const atr  = info?.atr || 0
  const rows = info ? [
    { name: "Tight (1.0 × ATR)",    ...sizeFor(atr * 1.0) },
    { name: "Standard (1.5 × ATR)", ...sizeFor(atr * 1.5), recommended: true },
    { name: "Wide (2.0 × ATR)",     ...sizeFor(atr * 2.0) },
    ...(Number(customSl) > 0 ? [{ name: `Custom (${customSl} pts)`, ...sizeFor(Number(customSl)) }] : []),
  ].filter(r => r.slPts) : []

  // ── Apply lot to MT5 config ────────────────────────────────────────────────
  const applyLot = async (lot) => {
    if (!info) return
    if (!window.confirm(`Set ${info.mt5_symbol} trading lot size to ${lot}?\nThis is what the trade executor will use for every ${asset} order.`)) return
    try {
      const r = await fetch(`${API}/mt4/config`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lot_sizes: { [info.mt5_symbol]: lot } }),
      })
      const d = await r.json()
      if (!r.ok || d.error) throw new Error(d.error || "Save failed")
      flash(`${info.mt5_symbol} lot size set to ${lot} ✔`)
      loadInfo()
    } catch (e) { setError(String(e.message || e)) }
  }

  return (
    <div style={{ maxWidth: "1100px", margin: "0 auto", padding: "20px" }}>
      <div style={{ marginBottom: "14px" }}>
        <h2 style={{ color: "#e6edf3", margin: 0, fontSize: "20px" }}>💰 Money Management</h2>
        <div style={{ color: "#8b949e", fontSize: "12px", marginTop: "4px" }}>
          Position sizing from your capital and risk limits — lot size and SL suggestions per asset.
        </div>
      </div>

      {notice && (
        <div style={{ ...card, background: "#0d2818", border: "1px solid #1f6f3f", color: "#3fb950", padding: "10px 16px" }}>
          {notice}
        </div>
      )}

      {/* ── 1 · Your risk profile ── */}
      <div style={card}>
        <h3 style={h3_}>1 · Your risk profile</h3>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: "12px" }}>
          <div>
            <label style={label_}>ACCOUNT CAPITAL (USD)</label>
            <input style={input_} type="number" min="0" value={capital}
                   onChange={e => setCapital(e.target.value)} />
          </div>
          <div>
            <label style={label_}>RISK PER TRADE (%)</label>
            <input style={input_} type="number" step="0.1" min="0" value={riskTrade}
                   onChange={e => setRiskTrade(e.target.value)} />
          </div>
          <div>
            <label style={label_}>RISK PER DAY (%)</label>
            <input style={input_} type="number" step="0.1" min="0" value={riskDay}
                   onChange={e => setRiskDay(e.target.value)} />
          </div>
          <div>
            <label style={label_}>ASSET</label>
            <select style={input_} value={asset} onChange={e => setAsset(e.target.value)}>
              <option>Gold</option><option>BTC</option><option>ETH</option>
            </select>
          </div>
          <div>
            <label style={label_}>ATR TIMEFRAME</label>
            <select style={input_} value={timeframe} onChange={e => setTimeframe(e.target.value)}>
              <option value="15m">15 min (intraday)</option>
              <option value="1h">1 hour (swing)</option>
            </select>
          </div>
        </div>

        {/* Risk summary strip */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "10px", marginTop: "14px" }}>
          {[
            ["Risk per trade", `$${riskUsd.toFixed(2)}`, "#4dabf7"],
            ["Daily risk cap", `$${dayUsd.toFixed(2)}`, "#d29922"],
            ["Max losing trades/day", maxLosses, maxLosses >= 2 ? "#3fb950" : "#f85149"],
            ["After that", "STOP trading for the day", "#f85149"],
          ].map(([lab, val, color]) => (
            <div key={lab} style={{ background: "#0d1117", borderRadius: "10px", padding: "12px", textAlign: "center" }}>
              <div style={{ fontSize: "11px", color: "#8b949e" }}>{lab}</div>
              <div style={{ fontSize: "16px", fontWeight: 700, color, marginTop: "4px" }}>{val}</div>
            </div>
          ))}
        </div>
        {Number(riskTrade) > 2 && (
          <div style={{ marginTop: "10px", color: "#d29922", fontSize: "12px" }}>
            ⚠ Risking more than 2% per trade is aggressive — most professionals stay at 0.5–2%.
          </div>
        )}
      </div>

      {/* ── 2 · Live market + suggestions ── */}
      <div style={card}>
        <h3 style={h3_}>
          2 · Lot size &amp; SL suggestions
          {info && <span style={{ color: "#8b949e", fontWeight: 400 }}>
            {"  "}({asset} @ {info.price} · ATR {info.atr} pts on {timeframe}
            {info.current_lot != null && <> · current configured lot: <b style={{ color: "#4dabf7" }}>{info.current_lot}</b></>})
          </span>}
        </h3>

        {loading && <div style={{ color: "#8b949e", fontSize: "13px" }}>Loading market data…</div>}
        {error && <div style={{ color: "#f85149", fontSize: "13px", marginBottom: "8px" }}>{error}</div>}

        {info && (
          <>
            <div style={{ display: "flex", gap: "12px", alignItems: "flex-end", marginBottom: "14px" }}>
              <div style={{ width: "180px" }}>
                <label style={label_}>CUSTOM SL (POINTS, OPTIONAL)</label>
                <input style={input_} type="number" step="any" min="0" placeholder="e.g. 8"
                       value={customSl} onChange={e => setCustomSl(e.target.value)} />
              </div>
              <div style={{ width: "140px" }}>
                <label style={label_}>RISK : REWARD</label>
                <input style={input_} type="number" step="0.5" min="0.5" value={rr}
                       onChange={e => setRr(e.target.value)} />
              </div>
              <button style={{ ...btn("#1a3a5c", "#4dabf7"), padding: "9px 14px" }} onClick={loadInfo}>
                ↻ Refresh price/ATR
              </button>
            </div>

            <table style={{ width: "100%", fontSize: "13px", color: "#e6edf3", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ color: "#8b949e", textAlign: "left" }}>
                  {["SL style", "SL (points)", "Lot size", "Risk if SL hits", `Profit at TP (RR ${rr})`, "TP distance", ""].map(hd =>
                    <th key={hd} style={{ padding: "8px", borderBottom: "1px solid #21262d" }}>{hd}</th>)}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid #161b22",
                                       background: r.recommended ? "rgba(31,111,235,0.07)" : "transparent" }}>
                    <td style={{ padding: "10px 8px", fontWeight: r.recommended ? 700 : 400 }}>
                      {r.name} {r.recommended && <span style={{ color: "#4dabf7", fontSize: "11px" }}>★ suggested</span>}
                    </td>
                    <td style={{ padding: "10px 8px" }}>{r.slPts}</td>
                    <td style={{ padding: "10px 8px", fontWeight: 700, color: r.tooSmall ? "#d29922" : "#3fb950" }}>
                      {r.lot}{r.tooSmall && " (min)"}
                    </td>
                    <td style={{ padding: "10px 8px", color: r.realRisk > riskUsd * 1.05 ? "#f85149" : "#e6edf3" }}>
                      ${r.realRisk}
                    </td>
                    <td style={{ padding: "10px 8px", color: "#3fb950" }}>${r.tpUsd}</td>
                    <td style={{ padding: "10px 8px" }}>{r.tpPts} pts</td>
                    <td style={{ padding: "10px 8px" }}>
                      <button style={btn("#238636")} onClick={() => applyLot(r.lot)}>
                        Use as {asset} lot
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {rows.some(r => r.tooSmall) && (
              <div style={{ marginTop: "10px", color: "#d29922", fontSize: "12px" }}>
                ⚠ "(min)" rows: your risk budget is smaller than the 0.01 minimum lot allows —
                if SL hits you'd lose more than {riskTrade}% (shown in red). Either widen capital,
                accept the higher risk, or skip the trade.
              </div>
            )}

            <div style={{ marginTop: "12px", padding: "10px 14px", background: "#0d1117", borderRadius: "10px",
                          color: "#8b949e", fontSize: "12px", lineHeight: 1.6 }}>
              <b style={{ color: "#e6edf3" }}>How this works:</b> lot size = risk $ ÷ (SL points × contract size).
              {asset === "Gold"
                ? " Gold: 1.0 lot = 100 oz, so each point of movement = $100 per lot (0.01 lot = $1/point)."
                : ` ${asset}: 1.0 lot = 1 ${asset}, so each $1 of price movement = $1 per lot.`}
              {" "}ATR ({timeframe}) measures recent volatility — an SL tighter than 1×ATR gets stopped out by normal noise.
            </div>
          </>
        )}
      </div>
    </div>
  )
}
