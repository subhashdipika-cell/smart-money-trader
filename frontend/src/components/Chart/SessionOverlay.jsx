/**
 * SessionOverlay.jsx
 * ------------------
 * Shows ICT trading sessions, overlaps, and countdowns
 * overlaid on top of the TradingView chart iframe.
 *
 * Sessions (UTC):
 *   Asia      00:00 – 09:00
 *   London    07:00 – 16:00
 *   New York  13:00 – 22:00
 *
 * Overlaps:
 *   Asia + London    07:00 – 09:00
 *   London + New York 13:00 – 16:00
 */

import { useEffect, useState } from "react"

// ── Session definitions (UTC hours) ──────────────────────────────────────────
// All times in IST decimal hours (e.g. 5.5 = 05:30 IST)
// Asia:      05:30 – 14:30 IST
// London:    12:30 – 21:30 IST
// New York:  18:30 – 03:30 IST (crosses midnight)
// Asia+London overlap:      12:30 – 14:30 IST
// London+New York overlap:  18:30 – 21:30 IST

const SESSIONS = [
  {
    id:    "asia",
    label: "Asia",
    short: "AS",
    open:  5.5,
    close: 14.5,
    color: "#f0b429",
    bg:    "rgba(240,180,41,0.12)"
  },
  {
    id:    "london",
    label: "London",
    short: "LN",
    open:  12.5,
    close: 21.5,
    color: "#4dabf7",
    bg:    "rgba(77,171,247,0.12)"
  },
  {
    id:    "newyork",
    label: "New York",
    short: "NY",
    open:  18.5,
    close: 3.5,    // crosses midnight IST
    color: "#69db7c",
    bg:    "rgba(105,219,124,0.12)"
  }
]

const OVERLAPS = [
  {
    id:     "asia-london",
    label:  "Asia + London overlap",
    open:   12.5,
    close:  14.5,
    color:  "#da77f2",
    bg:     "rgba(218,119,242,0.15)"
  },
  {
    id:     "london-newyork",
    label:  "London + New York overlap",
    open:   18.5,
    close:  21.5,
    color:  "#ff6b6b",
    bg:     "rgba(255,107,107,0.15)"
  }
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function istHourDecimal(date) {
  // IST = UTC + 5:30
  const utcMs  = date.getTime()
  const istMs  = utcMs + (5.5 * 60 * 60 * 1000)
  const istDate = new Date(istMs)
  return istDate.getUTCHours() + istDate.getUTCMinutes() / 60 + istDate.getUTCSeconds() / 3600
}

function isActive(session, hourDecimal) {
  if (session.open < session.close) {
    return hourDecimal >= session.open && hourDecimal < session.close
  }
  // Crosses midnight
  return hourDecimal >= session.open || hourDecimal < session.close
}

function secondsUntil(targetHour, nowDate) {
  // Work in IST
  const istMs   = nowDate.getTime() + (5.5 * 60 * 60 * 1000)
  const istDate = new Date(istMs)
  const nowSec  = istDate.getUTCHours() * 3600 + istDate.getUTCMinutes() * 60 + istDate.getUTCSeconds()
  const targetSec = targetHour * 3600
  let diff = targetSec - nowSec
  if (diff < 0) diff += 86400
  return diff
}

function formatCountdown(totalSeconds) {
  const h = Math.floor(totalSeconds / 3600)
  const m = Math.floor((totalSeconds % 3600) / 60)
  const s = totalSeconds % 60
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`
  return `${String(m).padStart(2, "0")}m ${String(s).padStart(2, "0")}s`
}

function getSessionState(now) {
  const h = istHourDecimal(now)

  const activeSessions  = SESSIONS.filter(s => isActive(s, h))
  const activeOverlaps  = OVERLAPS.filter(s => isActive(s, h))

  // Next session to open
  let nextOpen = null
  let minOpenSecs = Infinity
  for (const s of SESSIONS) {
    if (!isActive(s, h)) {
      const secs = secondsUntil(s.open, now)
      if (secs < minOpenSecs) {
        minOpenSecs = secs
        nextOpen    = { ...s, secsUntilOpen: secs }
      }
    }
  }

  // Closing countdowns for active sessions
  const closingCountdowns = activeSessions.map(s => ({
    ...s,
    secsUntilClose: secondsUntil(s.close, now)
  }))

  // Overlap closing countdown
  const overlapCountdowns = activeOverlaps.map(s => ({
    ...s,
    secsUntilClose: secondsUntil(s.close, now)
  }))

  return {
    activeSessions,
    activeOverlaps,
    nextOpen,
    closingCountdowns,
    overlapCountdowns,
    h
  }
}

// ── Components ────────────────────────────────────────────────────────────────

function SessionPill({ color, bg, label, children }) {
  return (
    <div style={{
      display:      "flex",
      alignItems:   "center",
      gap:          "6px",
      background:   bg,
      border:       `1px solid ${color}55`,
      borderRadius: "6px",
      padding:      "4px 10px",
      fontSize:     "12px",
      color:        "#e6edf3",
      whiteSpace:   "nowrap"
    }}>
      <span style={{
        width: "7px", height: "7px",
        borderRadius: "50%",
        background: color,
        boxShadow: `0 0 6px ${color}`,
        flexShrink: 0
      }} />
      <span style={{ fontWeight: 600, color }}>{label}</span>
      {children && (
        <span style={{ color: "#8b949e", fontSize: "11px" }}>{children}</span>
      )}
    </div>
  )
}

function CountdownPill({ color, label, seconds }) {
  return (
    <div style={{
      display:      "flex",
      alignItems:   "center",
      gap:          "6px",
      background:   "rgba(255,255,255,0.04)",
      border:       `1px solid ${color}33`,
      borderRadius: "6px",
      padding:      "4px 10px",
      fontSize:     "11px",
      color:        "#8b949e",
      whiteSpace:   "nowrap"
    }}>
      <span style={{ color, fontWeight: 600 }}>{label}</span>
      closes in
      <span style={{
        fontFamily:  "monospace",
        color:       "#e6edf3",
        fontWeight:  700,
        fontSize:    "12px"
      }}>
        {formatCountdown(seconds)}
      </span>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function SessionOverlay({ children, height }) {
  const [state, setState] = useState(() => getSessionState(new Date()))

  useEffect(() => {
    const tick = setInterval(() => {
      setState(getSessionState(new Date()))
    }, 1000)
    return () => clearInterval(tick)
  }, [])

  const {
    activeSessions,
    activeOverlaps,
    nextOpen,
    closingCountdowns,
    overlapCountdowns
  } = state

  const hasOverlap    = activeOverlaps.length > 0
  const overlapColor  = hasOverlap ? activeOverlaps[0].color : null
  const overlapBg     = hasOverlap ? activeOverlaps[0].bg    : null

  return (
    <div style={{ position: "relative", width: "100%" }}>

      {/* ── Top bar ─────────────────────────────────────────────────────────── */}
      <div style={{
        display:        "flex",
        alignItems:     "center",
        flexWrap:       "wrap",
        gap:            "6px",
        padding:        "8px 12px",
        background:     hasOverlap
          ? overlapBg
          : activeSessions.length > 0
            ? activeSessions[0].bg
            : "rgba(255,255,255,0.02)",
        borderBottom:   hasOverlap
          ? `2px solid ${overlapColor}55`
          : activeSessions.length > 0
            ? `2px solid ${activeSessions[0].color}33`
            : "2px solid #1c2128",
        borderRadius:   "8px 8px 0 0",
        // top bar
        minHeight:      "42px",
        transition:     "background 0.5s ease"
      }}>

        {/* Active sessions */}
        {activeSessions.length === 0 ? (
          <SessionPill color="#555" bg="rgba(255,255,255,0.03)" label="Market Closed" />
        ) : (
          activeSessions.map(s => (
            <SessionPill key={s.id} color={s.color} bg={s.bg} label={s.label}>
              {String(Math.floor(s.open)).padStart(2,'0')}:{s.open % 1 === 0 ? '00' : '30'} – {String(Math.floor(s.close)).padStart(2,'0')}:{s.close % 1 === 0 ? '00' : '30'} IST
            </SessionPill>
          ))
        )}

        {/* Overlap badge */}
        {hasOverlap && (
          <SessionPill
            color={overlapColor}
            bg={overlapBg}
            label={activeOverlaps[0].label}
          >
            ⚡ High volatility
          </SessionPill>
        )}

        {/* Next session pill — inline next to active sessions */}
        {nextOpen && (
          <div style={{
            display:      "flex",
            alignItems:   "center",
            gap:          "6px",
            background:   `${nextOpen.color}11`,
            border:       `1px dashed ${nextOpen.color}55`,
            borderRadius: "6px",
            padding:      "4px 10px",
            fontSize:     "12px",
            whiteSpace:   "nowrap"
          }}>
            <span style={{
              fontSize: "9px", color: "#8b949e",
              textTransform: "uppercase", letterSpacing: "0.5px"
            }}>Next</span>
            <span style={{
              width: "7px", height: "7px",
              borderRadius: "50%",
              background: nextOpen.color,
              boxShadow: `0 0 5px ${nextOpen.color}`,
              flexShrink: 0
            }} />
            <span style={{ fontWeight: 700, color: nextOpen.color }}>
              {nextOpen.label}
            </span>
            <span style={{
              fontFamily: "monospace",
              color: "#e6edf3",
              fontWeight: 700,
              fontSize: "13px"
            }}>
              {formatCountdown(nextOpen.secsUntilOpen)}
            </span>
          </div>
        )}

        {/* Spacer */}
        <div style={{ flex: 1 }} />

        {/* Closing countdowns */}
        {closingCountdowns.map(s => (
          <CountdownPill
            key={s.id}
            color={s.color}
            label={s.label}
            seconds={s.secsUntilClose}
          />
        ))}

        {/* Overlap closing countdown */}
        {overlapCountdowns.map(s => (
          <CountdownPill
            key={s.id}
            color={s.color}
            label="Overlap"
            seconds={s.secsUntilClose}
          />
        ))}
      </div>

      {/* ── Session timeline bar — between top bar and chart ─────────────── */}
      <SessionTimeline now={state.h} />

      {/* ── Chart ───────────────────────────────────────────────────────────── */}
      <div style={{ position: "relative" }}>
        {children}


      </div>

    </div>
  )
}

// ── 24h timeline showing all sessions ────────────────────────────────────────

function SessionTimeline({ now }) {
  const totalHours = 24

  return (
    <div style={{
      padding:        "6px 12px 8px",
      background:     "#0d1117",
      borderTop:      "1px solid #1c2128",
      borderBottom:   "1px solid #1c2128"
    }}>
      <div style={{
        fontSize: "10px", color: "#8b949e",
        textTransform: "uppercase", letterSpacing: "0.5px",
        marginBottom: "5px"
      }}>
        24h session map (IST)
      </div>

      {/* Timeline bar */}
      <div style={{ position: "relative", height: "20px", borderRadius: "4px", overflow: "hidden", background: "#161b22" }}>

        {/* Session blocks */}
        {SESSIONS.map(s => (
          <div key={s.id} style={{
            position:  "absolute",
            left:      `${(s.open  / totalHours) * 100}%`,
            width:     `${((s.close - s.open) / totalHours) * 100}%`,
            height:    "100%",
            background: s.bg,
            borderLeft: `2px solid ${s.color}88`,
            display:   "flex",
            alignItems:"center",
            justifyContent: "center",
            fontSize:  "9px",
            fontWeight: 700,
            color:     s.color,
            letterSpacing: "0.5px"
          }}>
            {s.short}
          </div>
        ))}

        {/* Overlap blocks */}
        {OVERLAPS.map(s => (
          <div key={s.id} style={{
            position:  "absolute",
            left:      `${(s.open  / totalHours) * 100}%`,
            width:     `${((s.close - s.open) / totalHours) * 100}%`,
            height:    "40%",
            top:       "30%",
            background: s.color + "55",
            borderRadius: "2px"
          }} />
        ))}

        {/* Current time needle */}
        <div style={{
          position:  "absolute",
          left:      `${(now / totalHours) * 100}%`,
          width:     "2px",
          height:    "100%",
          background:"#ffffff",
          boxShadow: "0 0 4px #fff",
          zIndex:    10
        }} />
      </div>

      {/* Hour labels */}
      <div style={{
        display:        "flex",
        justifyContent: "space-between",
        marginTop:      "3px",
        fontSize:       "9px",
        color:          "#555"
      }}>
        {[0,3,6,9,12,15,18,21,24].map(h => (
          <span key={h}>{String(h).padStart(2,"0")}</span>
        ))}
      </div>
    </div>
  )
}
