"""
ui/theme.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Single source of truth for every colour, font, and status mapping used
across the application.

ALL tab files import from here.  To change any colour or font, edit this
file once — every tab picks up the change automatically.

Previously these constants were copy-pasted into six separate files:
  gui.py, library_tab.py, pipeline_tab.py,
  dub_tab.py, settings_tab.py, logs_tab.py

Usage
─────
    from ui.theme import *          # pull everything into local namespace
    # or explicit:
    from ui.theme import BG, ACCENT, FL, STATUS_COLORS
"""

# ── Colour palette ─────────────────────────────────────────────────────────────

BG       = "#0e0e0f"    # app background — near-black
PANEL    = "#141416"    # elevated surface (sidebars, top bars)
PANEL2   = "#1a1a1c"    # double-elevated surface (card backgrounds)
BORDER   = "#2a2a2e"    # subtle dividers and widget borders
ACCENT   = "#ff6b35"    # primary action colour (orange)
ACCENT2  = "#c94e1f"    # darker accent (hover / destructive buttons)
TEXT     = "#f0f0f0"    # primary body text
TEXT_DIM = "#aaaaaa"    # secondary / label text
MUTED    = "#666666"    # placeholder / disabled text
SUCCESS  = "#4ade80"    # green — done / ok
ERROR    = "#f87171"    # red — failed / destructive
WARNING  = "#fbbf24"    # amber — running / caution
INFO     = "#60a5fa"    # blue — informational
BTN_BG   = "#2a2a2e"    # default button background
BTN_FG   = "#f0f0f0"    # default button foreground
SEL_BG   = "#2a2020"    # selected-row highlight (warm dark)


# ── Typography ─────────────────────────────────────────────────────────────────

_F   = "Courier New"           # base monospace family

FL   = (_F,  9, "bold")        # section labels / headings
FB   = (_F, 10)                # body / entry text
FS   = (_F,  8)                # small captions / status text
FBTN = (_F, 10, "bold")        # button labels
FLOG = (_F,  9)                # log output (same size as FL but non-bold)

# Serif fonts — used only in gui.py's logo header
FH1  = ("Georgia", 22, "bold") # "MANHWA" word
FH2  = ("Georgia", 22)         # "STUDIO" word
FH3  = ("Georgia",  9, "italic")# subtitle


# ── Status → colour mapping ────────────────────────────────────────────────────

STATUS_COLORS: dict = {
    "pending": MUTED,
    "running": WARNING,
    "done":    SUCCESS,
    "failed":  ERROR,
    "skipped": "#444444",
}

STATUS_ICONS: dict = {
    "pending": "○",
    "running": "●",
    "done":    "✓",
    "failed":  "✗",
    "skipped": "—",
}


# ── Log level → colour mapping ─────────────────────────────────────────────────
# Used by the Logs tab and the status-bar mirror in gui.py.

LOG_COLORS: dict = {
    "accent":  ACCENT,
    "success": SUCCESS,
    "error":   ERROR,
    "warning": WARNING,
    "info":    INFO,
    "muted":   MUTED,
}
