/**
 * EventsTicker.jsx
 * ----------------
 * Scrolling ticker showing upcoming high-impact economic events.
 * Replaces the "EXECUTION CHART" label above the TradingView chart.
 */

import { useEffect, useRef, useState } from "react"

const API_URLS = [
  import.meta.env.VITE_API_URL,
  "http://127.0.0.1:8000",
  "http://127.0.0.1:8001"
].filter(Boolean)

async function fetchEvents() {
  for (const base of API_URLS) {
    try {
      const res  = await fetch(`${base.replace(/\/$/, "")}/market-events`)
      const data = await res.json()
      if (data?.events) return data.events
    } catch { /* try next */ }
  }
  return []
}

const IMPACT_COLORS = {
  High:   { color: "#f85149", bg: "rgba(248,81,73,0.12)",  dot: "#f85149" },
  Medium: { color: "#f0b429", bg: "rgba(240,180,41,0.10)", dot: "#f0b429" }
}

const CURRENCY_FLAGS = {
  USD: "🇺🇸", EUR: "🇪🇺", GBP: "🇬🇧",
  JPY: "🇯🇵", CNY: "🇨🇳", XAU: "🥇"
}

export default function EventsTicker() {
  const [events, setEvents]       = useState([])
  const [loading, setLoading]     = useState(true)
  const [paused, setPaused]       = useState(false)
  const [selected, setSelected]   = useState(null)
  const tickerRef                 = useRef(null)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      const data = await fetchEvents()
      setEvents(data)
      setLoading(false)
    }
    load()
    // Refresh every 10 minutes
    const interval = setInterval(load, 10 * 60 * 1000)
    return () => clearInterval(interval)
  }, [])

  // Auto-scroll
  useEffect(() => {
    if (paused || !tickerRef.current || events.length === 0) return
    const el  = tickerRef.current
    let frame = null
    let pos   = 0

    const scroll = () => {
      pos += 0.4
      if (pos >= el.scrollWidth / 2) pos = 0
      el.scrollLeft = pos
      frame = requestAnimationFrame(scroll)
    }

    frame = requestAnimationFrame(scroll)
    return () => cancelAnimationFrame(frame)
  }, [events, paused])

  if (loading) {
    return (
      <div style={wrapStyle}>
        <span style={{ color: "#8b949e", fontSize: "11px" }}>📅 Loading market events…</span>
      </div>
    )
  }

  if (events.length === 0) {
    return (
      <div style={wrapStyle}>
        <span style={{ color: "#8b949e", fontSize: "11px" }}>📅 No high-impact events this week</span>
      </div>
    )
  }

  // Duplicate events for seamless loop
  const doubled = [...events, ...events]

  return (
    <>
      {/* ── Ticker bar ── */}
      <div
        style={wrapStyle}
        onMouseEnter={() => setPaused(true)}
        onMouseLeave={() => setPaused(false)}
      >
        <span style={{
          fontSize: "10px", fontWeight: 700, color: "#4dabf7",
          letterSpacing: "1px", textTransform: "uppercase",
          flexShrink: 0, paddingRight: "12px",
          borderRight: "1px solid #1c2128", marginRight: "12px"
        }}>
          📅 EVENTS
        </span>

        <div
          ref={tickerRef}
          style={{
            display: "flex", alignItems: "center", gap: "0",
            overflow: "hidden", flex: 1,
            maskImage: "linear-gradient(90deg, transparent, black 5%, black 95%, transparent)"
          }}
        >
          {doubled.map((ev, i) => {
            const imp    = IMPACT_COLORS[ev.impact] || IMPACT_COLORS.Medium
            const flag   = CURRENCY_FLAGS[ev.currency] || "🌐"
            const isNext = ev.is_upcoming && ev.minutes_away != null && ev.minutes_away <= 60

            return (
              <div
                key={i}
                onClick={() => setSelected(selected?.title === ev.title ? null : ev)}
                style={{
                  display:     "flex",
                  alignItems:  "center",
                  gap:         "6px",
                  padding:     "3px 12px 3px 10px",
                  marginRight: "4px",
                  borderRadius: "5px",
                  background:   isNext ? imp.bg : "transparent",
                  border:       isNext ? `1px solid ${imp.color}44` : "1px solid transparent",
                  cursor:       "pointer",
                  flexShrink:   0,
                  transition:   "background 0.2s"
                }}
              >
                <span style={{
                  width: "6px", height: "6px", borderRadius: "50%",
                  background: imp.dot,
                  boxShadow: isNext ? `0 0 5px ${imp.dot}` : "none",
                  flexShrink: 0
                }} />
                <span style={{ fontSize: "12px" }}>{flag}</span>
                <span style={{ fontSize: "11px", color: imp.color, fontWeight: 700 }}>
                  {ev.currency}
                </span>
                <span style={{ fontSize: "11px", color: "#c9d1d9" }}>
                  {ev.title}
                </span>
                <span style={{ fontSize: "10px", color: "#8b949e" }}>
                  {ev.datetime_ist}
                </span>
                {isNext && (
                  <span style={{
                    fontSize: "10px", fontWeight: 700,
                    color: "#fff",
                    background: imp.color,
                    padding: "1px 5px",
                    borderRadius: "3px"
                  }}>
                    {ev.minutes_away < 60
                      ? `in ${ev.minutes_away}m`
                      : `in ${Math.round(ev.minutes_away / 60)}h`}
                  </span>
                )}
                <span style={{
                  width: "1px", height: "12px",
                  background: "#1c2128", marginLeft: "8px", flexShrink: 0
                }} />
              </div>
            )
          })}
        </div>
      </div>

      {/* ── Detail popup on click ── */}
      {selected && (
        <div style={{
          background: "#161b22", border: "1px solid #1c2128",
          borderRadius: "8px", padding: "12px 16px",
          marginTop: "6px", fontSize: "13px",
          display: "flex", alignItems: "center",
          justifyContent: "space-between", gap: "16px"
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ fontSize: "18px" }}>
              {CURRENCY_FLAGS[selected.currency] || "🌐"}
            </span>
            <div>
              <div style={{ fontWeight: 700, color: "#e6edf3" }}>{selected.title}</div>
              <div style={{ fontSize: "11px", color: "#8b949e", marginTop: "2px" }}>
                {selected.currency} · {selected.impact} Impact · {selected.datetime_ist}
              </div>
            </div>
          </div>
          <div style={{
            padding: "4px 12px", borderRadius: "6px",
            background: (IMPACT_COLORS[selected.impact] || IMPACT_COLORS.Medium).bg,
            color:      (IMPACT_COLORS[selected.impact] || IMPACT_COLORS.Medium).color,
            fontWeight: 700, fontSize: "12px"
          }}>
            {selected.impact} Impact
          </div>
          <button
            onClick={() => setSelected(null)}
            style={{
              background: "none", border: "none",
              color: "#8b949e", cursor: "pointer", fontSize: "16px"
            }}
          >×</button>
        </div>
      )}
    </>
  )
}

const wrapStyle = {
  display:      "flex",
  alignItems:   "center",
  padding:      "6px 12px",
  background:   "#0d1117",
  borderBottom: "1px solid #1c2128",
  height:       "34px",
  overflow:     "hidden"
}
