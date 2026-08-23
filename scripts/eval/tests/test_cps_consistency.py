"""
Regression tests for the cps/rushed staleness bug.

Two defects were found by the eval harness on real sessions:
  1. Four independent duration implementations disagreed, so a cue's stored
     `cps` depended on which code path last wrote it. Sessions 42 and 64 had
     IDENTICAL timings but different stored values.
  2. After re-segmentation, session 53's stored values belonged to other cues
     entirely - the implied durations were another session's, shifted by one.

The fix: one canonical rule in speech/cps.py, and recompute at the persistence
boundary so derived fields cannot survive an edit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from speech import cps  # noqa: E402

from scripts.eval import metrics as m  # noqa: E402


# ── the canonical rule ───────────────────────────────────────────────────────
def test_effective_duration_extends_into_trailing_silence():
    cues = [{"start": 0.0, "end": 2.0}, {"start": 5.0, "end": 6.0}]
    assert cps.effective_duration(cues, 0) == pytest.approx(5.0 - 0.0 - 0.24)


def test_effective_duration_uses_own_span_when_packed():
    cues = [{"start": 0.0, "end": 2.0}, {"start": 2.1, "end": 4.0}]
    assert cps.effective_duration(cues, 0) == pytest.approx(2.0)


def test_effective_duration_last_cue_not_extended():
    assert cps.effective_duration([{"start": 0.0, "end": 2.0}], 0) == pytest.approx(2.0)


@pytest.mark.parametrize("bad", [
    [{"start": "x", "end": None}],
    [{}],
    [{"start": 0.0}],
])
def test_effective_duration_never_raises_on_malformed(bad):
    assert cps.effective_duration(bad, 0) == 0.0


def test_effective_duration_out_of_range_index():
    assert cps.effective_duration([], 0) == 0.0


def test_min_gap_comes_from_config_not_a_literal():
    """The 0.24 breath must have exactly one definition."""
    cues = [{"start": 0.0, "end": 1.0}, {"start": 10.0, "end": 11.0}]
    assert cps.effective_duration(cues, 0) == pytest.approx(10.0 - cps.config.DUB_SPEECH_MIN_GAP)


# ── the eval harness and the pipeline must agree ──────────────────────────────
@pytest.mark.parametrize("cues", [
    [{"start": 0.0, "end": 2.0}, {"start": 5.0, "end": 6.0}],
    [{"start": 0.0, "end": 2.0}, {"start": 2.1, "end": 4.0}],
    [{"start": 1.5, "end": 3.0}],
    [{"start": 0.0, "end": 1.0}, {"start": 1.05, "end": 2.0}, {"start": 9.0, "end": 10.0}],
])
def test_harness_and_pipeline_compute_the_same_window(cues):
    """This equality is the whole point - if it breaks, every metric is a lie."""
    for i in range(len(cues)):
        assert cps.effective_duration(cues, i) == pytest.approx(m.usable_duration(cues, i))


# ── language normalisation ───────────────────────────────────────────────────
@pytest.mark.parametrize("raw,code", [("French", "fr"), ("fr", "fr"), ("fr-FR", "fr"),
                                      ("Korean", "ko"), ("", "")])
def test_normalise_lang(raw, code):
    assert cps.normalise_lang(raw) == code


def test_display_name_selects_cjk_thresholds():
    """Passing 'Korean' must not silently fall through to Latin thresholds."""
    assert cps.max_cps(cps.normalise_lang("Korean")) == cps.max_cps("ko")
    assert cps.max_cps(cps.normalise_lang("Korean")) < cps.max_cps("fr")


# ── recompute at the persistence boundary ────────────────────────────────────
def test_recompute_overwrites_a_stale_value():
    cues = [{"start": 0.0, "end": 1.0, "translated": "a" * 12, "cps": 99.9, "rushed": False}]
    assert cps.recompute_cue_metrics(cues, "French") == 1
    assert cues[0]["cps"] == pytest.approx(12.0)
    assert cues[0]["rushed"] is False


def test_recompute_reports_zero_changes_when_already_correct():
    cues = [{"start": 0.0, "end": 1.0, "translated": "a" * 12}]
    cps.recompute_cue_metrics(cues, "French")
    assert cps.recompute_cue_metrics(cues, "French") == 0  # idempotent


def test_recompute_fixes_misaligned_values_after_resegmentation():
    """Session 53's defect: values that belong to a different cue alignment."""
    cues = [
        {"start": 0.0, "end": 1.0, "translated": "a" * 12, "cps": 40.0},
        {"start": 1.0, "end": 3.0, "translated": "b" * 40, "cps": 12.0},
    ]
    assert cps.recompute_cue_metrics(cues, "fr") == 2
    assert cues[0]["cps"] == pytest.approx(12.0)
    assert cues[1]["cps"] == pytest.approx(20.0)


def test_recompute_marks_rushed_correctly():
    cues = [{"start": 0.0, "end": 1.0, "translated": "a" * 30}]  # 30 > max 24
    cps.recompute_cue_metrics(cues, "fr")
    assert cues[0]["rushed"] is True


def test_recompute_zeroes_an_untranslated_cue():
    cues = [{"start": 0.0, "end": 2.0, "text": "source only", "cps": 18.0, "rushed": True}]
    cps.recompute_cue_metrics(cues, "fr")
    assert cues[0]["cps"] == 0.0
    assert cues[0]["rushed"] is False


def test_recompute_skips_non_dict_entries():
    cues = [None, {"start": 0.0, "end": 1.0, "translated": "a" * 12}]
    cps.recompute_cue_metrics(cues, "fr")  # must not raise
    assert cues[1]["cps"] == pytest.approx(12.0)


def test_spoken_text_never_uses_source():
    assert cps.spoken_text({"text": "src"}) == ""
    assert cps.spoken_text({"translated": "t", "text": "src"}) == "t"
    assert cps.spoken_text({"dubbed": "d", "text": "src"}) == "d"
