import { useState, useEffect } from "react"

const API = "http://127.0.0.1:8000"

const ASSET_META = [
  { key: "BTC",  label: "BTC",  color: "#f7931a", bg: "rgba(247,147,26,.12)"  },
  { key: "ETH",  label: "ETH",  color: "#627eea", bg: "rgba(98,126,234,.12)"  },
  { key: "Gold", label: "Gold", color: "#f0b429", bg: "rgba(240,180,41,.12)"  },
]

/**
 * LiveStrategySelector
 * -------------------
 * The three BTC / ETH / Gold strategy-picker boxes.
 * Self-contained: manages its own API calls and state.
 * Used at the bottom of the Strategy Tester page.
 */
export default function LiveStrategySelector() {
  const [assetSignals,   setAssetSignals]   = useState({ BTC: true, ETH: true, Gold: true })
  const [assetStrategies, setAssetStrategies] = useState({
    BTC:  ["HTF_ICT_Intraday"],
    ETH:  ["HTF_ICT_Intraday"],
    Gold: ["HTF_ICT_Intraday"],
  })
  const [stratCatalogue, setStratCatalogue] = useState({})
  const [assetToggling,  setAssetToggling]  = useState(null)
  const [stratToggling,  setStratToggling]  = useState(null)

  const fetchConfig = async () => {
    try {
      const [assetR, stratR, catR] = await Promise.all([
        fetch(`${API}/signals/assets`),
        fetch(`${API}/signals/asset-strategies`),
        fetch(`${API}/signals/strategy-catalogue`),
      ])
      setAssetSignals(await assetR.json())
      setAssetStrategies(await stratR.json())
      setStratCatalogue(await catR.json())
    } catch (e) {}
  }

  useEffect(() => {
    fetchConfig()
  }, [])

  const toggleAsset = async (asset) => {
    if (assetToggling) return
    setAssetToggling(asset)
    try {
      const r = await fetch(`${API}/signals/assets/toggle?asset=${asset}`, { method: "POST" })
      setAssetSignals(await r.json())
    } catch (e) {}
    setAssetToggling(null)
  }

  const toggleStrategy = async (asset, strategyId) => {
    const key = `${asset}:${strategyId}`
    if (stratToggling) return
    setStratToggling(key)
    try {
      const r = await fetch(
        `${API}/signals/asset-strategies/toggle?asset=${asset}&strategy_id=${strategyId}`,
        { method: "POST" }
      )
      setAssetStrategies(await r.json())
    } catch (e) {}
    setStratToggling(null)
  }

  return (
    <div style={{
      background: "#161b22",
      border: "1px solid #1c2128",
      borderRadius: 10,
      padding: 20,
    }}>
      {/* Section header */}
      <div style={{
        fontSize: 11, color: "#8b949e", textTransform: "uppercase",
        letterSpacing: ".5px", marginBottom: 14,
      }}>
        Live Signal Engine — Active Strategies per Asset
      </div>

      {/* Three asset cards */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {ASSET_META.map(a => {
          const on       = assetSignals[a.key] !== false
          const busy     = assetToggling === a.key
          const activeList = Array.isArray(assetStrategies[a.key])
            ? assetStrategies[a.key]
            : [assetStrategies[a.key] || "HTF_ICT_Intraday"]
          const anyLive  = activeList.some(id => stratCatalogue[id]?.live !== false)

          const compatStrats = Object.entries(stratCatalogue)
            .filter(([, info]) => !info.assets || info.assets.includes(a.key))

          return (
            <div key={a.key} style={{
              background: on ? a.bg : "#161b22",
              border: `1px solid ${on ? a.color : "#30363d"}`,
              borderRadius: 8, padding: "8px 12px", minWidth: 180,
              opacity: busy ? 0.6 : 1, transition: "all .15s",
              boxShadow: on ? `0 0 10px ${a.color}20` : "none",
            }}>
              {/* Asset header row */}
              <div style={{
                display: "flex", alignItems: "center",
                justifyContent: "space-between", marginBottom: 8,
              }}>
                <span style={{ fontSize: 13, fontWeight: 800, color: on ? a.color : "#8b949e" }}>
                  {on ? "●" : "○"} {a.label}
                </span>
                <button
                  onClick={() => toggleAsset(a.key)}
                  disabled={!!assetToggling}
                  title={on ? `Pause ${a.label} signals` : `Resume ${a.label} signals`}
                  style={{
                    padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 700,
                    cursor: assetToggling ? "wait" : "pointer", border: "none",
                    background: on ? "rgba(248,81,73,.15)" : "rgba(63,185,80,.15)",
                    color: on ? "#f85149" : "#3fb950",
                  }}
                >
                  {busy ? "…" : on ? "Pause" : "Resume"}
                </button>
              </div>

              {/* Strategy checkboxes */}
              <div style={{
                fontSize: 10, color: "#8b949e", marginBottom: 5,
                textTransform: "uppercase", letterSpacing: ".3px",
              }}>
                Strategies (select multiple)
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {compatStrats.map(([id, info]) => {
                  const checked  = activeList.includes(id)
                  const isLast   = checked && activeList.length === 1
                  const togKey   = `${a.key}:${id}`
                  const loading  = stratToggling === togKey
                  return (
                    <label
                      key={id}
                      title={isLast ? "Can't remove last strategy" : info.desc}
                      style={{
                        display: "flex", alignItems: "center", gap: 6,
                        cursor: on && !isLast ? "pointer" : "default",
                        opacity: (!on || loading) ? 0.5 : 1,
                        userSelect: "none",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={!on || loading || isLast}
                        onChange={() => toggleStrategy(a.key, id)}
                        style={{ accentColor: a.color, width: 13, height: 13, cursor: "pointer" }}
                      />
                      <span style={{ fontSize: 11, color: checked ? "#e6edf3" : "#8b949e", flex: 1 }}>
                        {info.label || id}
                      </span>
                      <span style={{
                        fontSize: 9, fontWeight: 700,
                        color: info.live ? "#3fb950" : "#f0b429",
                      }}>
                        {info.live ? "LIVE" : "BT"}
                      </span>
                      {loading && <span style={{ fontSize: 10, color: "#8b949e" }}>…</span>}
                    </label>
                  )
                })}
              </div>

              {/* Status footer */}
              <div style={{
                marginTop: 6, fontSize: 10,
                color: anyLive ? "#3fb950" : "#f0b429",
                borderTop: "1px solid #21262d", paddingTop: 5,
              }}>
                {anyLive
                  ? `✓ ${activeList.length} strategy${activeList.length > 1 ? "s" : ""} active`
                  : "⚠ Backtest only · ICT used as fallback"}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
