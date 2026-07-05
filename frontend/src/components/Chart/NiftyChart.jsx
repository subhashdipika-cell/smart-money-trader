/**
 * NiftyChart.jsx
 * -------------
 * Renders a live candlestick chart for Nifty 50 using data
 * fetched from our own backend (/  with symbol=NIFTY50).
 * Uses lightweight-charts (free, open-source by TradingView).
 */

import { useEffect, useRef, useState } from "react"

const LW_CDN = "https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"

// Load lightweight-charts from CDN once
function loadLightweightCharts() {
  return new Promise((resolve, reject) => {
    if (window.LightweightCharts) {
      resolve(window.LightweightCharts)
      return
    }
    const script = document.createElement("script")
    script.src   = LW_CDN
    script.onload  = () => resolve(window.LightweightCharts)
    script.onerror = () => reject(new Error("Failed to load lightweight-charts"))
    document.head.appendChild(script)
  })
}

const API_URLS = [
  import.meta.env.VITE_API_URL,
  "http://127.0.0.1:8000",
  "http://127.0.0.1:8001"
].filter(Boolean)

async function fetchNiftyCandles() {
  for (const base of API_URLS) {
    try {
      const res  = await fetch(`${base.replace(/\/$/, "")}/?symbol=NIFTY50`)
      const data = await res.json()
      if (data && Array.isArray(data.candles)) return data
    } catch { /* try next */ }
  }
  return null
}

export default function NiftyChart({ height = 640 }) {
  const containerRef = useRef(null)
  const chartRef     = useRef(null)
  const seriesRef    = useRef(null)
  const [status, setStatus]   = useState("loading")   // loading | live | closed | error
  const [lastPrice, setLastPrice] = useState(null)
  const [lastUpdate, setLastUpdate] = useState(null)

  // ── Init chart ─────────────────────────────────────────────────────────────
  useEffect(() => {
    let destroyed = false
    let interval  = null

    const init = async () => {
      try {
        const LW = await loadLightweightCharts()
        if (destroyed || !containerRef.current) return

        // Create chart
        const chart = LW.createChart(containerRef.current, {
          width:  containerRef.current.clientWidth,
          height: height,
          layout: {
            background: { color: "#0d1117" },
            textColor:  "#c9d1d9"
          },
          grid: {
            vertLines:   { color: "#1c2128" },
            horzLines:   { color: "#1c2128" }
          },
          crosshair: { mode: LW.CrosshairMode.Normal },
          rightPriceScale: {
            borderColor: "#30363d",
            scaleMargins: { top: 0.1, bottom: 0.1 }
          },
          timeScale: {
            borderColor:     "#30363d",
            timeVisible:     true,
            secondsVisible:  false,
            tickMarkFormatter: (time) => {
              const d = new Date(time * 1000)
              return d.toLocaleTimeString("en-IN", {
                hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata"
              })
            }
          }
        })

        const series = chart.addCandlestickSeries({
          upColor:        "#26a641",
          downColor:      "#f85149",
          borderUpColor:  "#26a641",
          borderDownColor:"#f85149",
          wickUpColor:    "#26a641",
          wickDownColor:  "#f85149"
        })

        chartRef.current  = chart
        seriesRef.current = series

        // Resize observer
        const ro = new ResizeObserver(() => {
          if (containerRef.current && chartRef.current) {
            chartRef.current.applyOptions({
              width: containerRef.current.clientWidth
            })
          }
        })
        ro.observe(containerRef.current)

        // Fetch and update candles
        const update = async () => {
          const data = await fetchNiftyCandles()

          if (!data) {
            setStatus("error")
            return
          }

          if (data.detail && data.detail.includes("closed")) {
            setStatus("closed")
            return
          }

          const candles = (data.candles || [])
            .filter(c => c.timestamp && c.open && c.high && c.low && c.close)
            .map(c => ({
              time:  Math.floor(Number(c.timestamp) / 1000),
              open:  Number(c.open),
              high:  Number(c.high),
              low:   Number(c.low),
              close: Number(c.close)
            }))
            // Remove duplicate timestamps
            .filter((c, i, arr) => i === 0 || c.time !== arr[i - 1].time)
            .sort((a, b) => a.time - b.time)

          if (candles.length > 0 && seriesRef.current) {
            seriesRef.current.setData(candles)
            chartRef.current.timeScale().fitContent()
            const last = candles[candles.length - 1]
            setLastPrice(last.close)
            setLastUpdate(new Date())
            setStatus("live")
          }
        }

        await update()
        interval = setInterval(update, 15000)   // refresh every 15s

      } catch (err) {
        if (!destroyed) setStatus("error")
        console.error("NiftyChart error:", err)
      }
    }

    init()

    return () => {
      destroyed = true
      if (interval) clearInterval(interval)
      if (chartRef.current) {
        chartRef.current.remove()
        chartRef.current  = null
        seriesRef.current = null
      }
    }
  }, [height])

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div style={{ position: "relative", width: "100%", height }}>

      {/* Chart container */}
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />

      {/* Status overlays */}
      {status === "loading" && (
        <div style={overlayStyle}>
          <span style={{ color: "#8b949e", fontSize: 14 }}>
            Loading Nifty 50 candles…
          </span>
        </div>
      )}

      {status === "closed" && (
        <div style={overlayStyle}>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 32, marginBottom: 8 }}>🕘</div>
            <div style={{ color: "#c9d1d9", fontSize: 16, fontWeight: 600 }}>
              NSE Market Closed
            </div>
            <div style={{ color: "#8b949e", fontSize: 13, marginTop: 6 }}>
              Opens Monday – Friday, 9:15 AM IST
            </div>
          </div>
        </div>
      )}

      {status === "error" && (
        <div style={overlayStyle}>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 32, marginBottom: 8 }}>⚠️</div>
            <div style={{ color: "#f85149", fontSize: 14 }}>
              Unable to fetch Nifty 50 data
            </div>
            <div style={{ color: "#8b949e", fontSize: 12, marginTop: 4 }}>
              Check that the backend is running and Dhan API token is valid
            </div>
          </div>
        </div>
      )}

      {/* Live price badge */}
      {status === "live" && lastPrice && (
        <div style={{
          position: "absolute", top: 10, left: 12,
          background: "#138808cc",
          color: "#fff",
          padding: "3px 10px",
          borderRadius: 6,
          fontSize: 13,
          fontWeight: 700,
          backdropFilter: "blur(4px)"
        }}>
          ₹{lastPrice.toLocaleString("en-IN", { maximumFractionDigits: 2 })}
          {lastUpdate && (
            <span style={{ fontWeight: 400, marginLeft: 8, fontSize: 11, opacity: 0.8 }}>
              {lastUpdate.toLocaleTimeString("en-IN", {
                hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata"
              })} IST
            </span>
          )}
        </div>
      )}
    </div>
  )
}

const overlayStyle = {
  position: "absolute", inset: 0,
  display: "flex", alignItems: "center", justifyContent: "center",
  background: "#0d1117cc",
  backdropFilter: "blur(2px)",
  zIndex: 10
}
