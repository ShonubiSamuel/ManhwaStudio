/**
 * ui/src/pages/ComingSoon.jsx
 * Placeholder for app sections that aren't built yet (Transcription, Subtitle,
 * Real-Time, Voices). Keeps the new AI-Voice navigation complete while only
 * Voiceover is fully implemented.
 */

import { colors, fonts, radius } from "../theme"

export default function ComingSoon({ title, desc, icon = "✦" }) {
  return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", background: colors.bg, padding: 40 }}>
      <div style={{ textAlign: "center", maxWidth: 460 }}>
        <div style={{ fontSize: 44, marginBottom: 16, opacity: 0.85 }}>{icon}</div>
        <div style={{ display: "inline-block", fontSize: fonts.xs, fontWeight: fonts.bold, letterSpacing: "0.12em", color: colors.accent, border: `1px solid ${colors.accent}`, borderRadius: radius.full, padding: "3px 10px", marginBottom: 14 }}>
          COMING SOON
        </div>
        <h2 style={{ color: colors.text, fontSize: fonts.xxl, fontWeight: fonts.bold, marginBottom: 10 }}>{title}</h2>
        <p style={{ color: colors.muted, fontSize: fonts.md, lineHeight: 1.6 }}>{desc}</p>
      </div>
    </div>
  )
}
