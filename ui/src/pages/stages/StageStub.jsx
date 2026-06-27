/**
 * ui/src/pages/stages/StageStub.jsx — ManhwaStudio v2
 * ─────────────────────────────────────────────────────────────────────────────
 * Placeholder detail view for stages whose full detail UI is not built yet
 * (Translate, Dub, Sync, Assemble, PDF Slice, Upscale). Still fully runnable —
 * shows status and a Run button so the stage works while its detail view is
 * built out next, one at a time, following the Refine/Detect pattern.
 */

import { colors, fonts } from "../../theme"
import { DetailHeader, DetailCenter } from "./common"

export default function StageStub({ label, hint, status, progress, busy, onRun }) {
  return (
    <div>
      <DetailHeader
        title={label} subtitle={hint}
        status={status} progress={progress} busy={busy}
        onRun={onRun} runLabel={status === "done" ? "Re-run" : "Run"}
      />
      <DetailCenter>
        <div style={{ fontSize: 28, color: colors.muted }}>◇</div>
        <div style={{ color: colors.textDim, fontSize: fonts.base, marginTop: 8 }}>
          Detail view for this stage is coming next.
        </div>
        <div style={{ color: colors.muted, fontSize: fonts.sm, maxWidth: 320, marginTop: 6 }}>
          It follows the same pattern as Refine and Detect — built one stage at a
          time. The stage already runs from here and from its rail button.
        </div>
      </DetailCenter>
    </div>
  )
}
