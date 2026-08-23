"""Unit tests for the fit metrics. Run: python -m pytest scripts/eval/tests -q"""

from __future__ import annotations

import pytest

from scripts.eval import metrics as m


# ── language normalisation ───────────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw,expected",
    [("French", "fr"), ("english", "en"), ("fr-FR", "fr"), ("ko_KR", "ko"),
     ("Japanese", "ja"), ("", ""), (None, "")],
)
def test_lang_code(raw, expected):
    assert m.lang_code(raw) == expected


def test_cjk_thresholds_are_lower():
    assert m.comfortable_cps("Korean") < m.comfortable_cps("French")
    assert m.max_cps("ja") < m.max_cps("en")


# ── usable duration ──────────────────────────────────────────────────────────
def test_usable_duration_uses_own_span_when_gap_is_tight():
    cues = [{"start": 0.0, "end": 2.0}, {"start": 2.1, "end": 4.0}]
    assert m.usable_duration(cues, 0) == pytest.approx(2.0)


def test_usable_duration_extends_into_trailing_silence():
    # 3s of dead air after the cue ends; usable = 5.0 - 0.0 - 0.24 breath
    cues = [{"start": 0.0, "end": 2.0}, {"start": 5.0, "end": 6.0}]
    assert m.usable_duration(cues, 0) == pytest.approx(4.76)


def test_usable_duration_last_cue_has_no_extension():
    cues = [{"start": 0.0, "end": 2.0}]
    assert m.usable_duration(cues, 0) == pytest.approx(2.0)


def test_usable_duration_tolerates_malformed_cue():
    assert m.usable_duration([{"start": "x", "end": None}], 0) == 0.0


# ── cps ──────────────────────────────────────────────────────────────────────
def test_cps_of_counts_stripped_characters():
    assert m.cps_of("  abcde  ", 1.0) == pytest.approx(5.0)


def test_cps_of_zero_duration_is_zero_not_error():
    assert m.cps_of("abc", 0.0) == 0.0


def test_target_chars_scales_with_duration():
    assert m.target_chars(2.0, "fr") == 2 * int(m.CPS_COMFORTABLE)
    assert m.target_chars(0.0, "fr") == 1  # never zero


# ── evaluate_fit ─────────────────────────────────────────────────────────────
def _cue(start, end, translated):
    return {"start": start, "end": end, "text": "src", "translated": translated}


def test_comfortable_line_is_counted_comfortable():
    # 20 chars in 2s at 20 CPS comfortable == exactly on target
    cues = [_cue(0.0, 2.0, "a" * 40)]
    rep = m.evaluate_fit(cues, "fr")
    assert rep.translated == 1
    assert rep.comfortable == 1
    assert rep.rushed == 0
    assert rep.mean_length_ratio == pytest.approx(1.0)


def test_rushed_line_is_flagged():
    cues = [_cue(0.0, 1.0, "a" * 30)]  # 30 CPS > max 24
    rep = m.evaluate_fit(cues, "fr")
    assert rep.rushed == 1
    assert rep.rushed_rate == pytest.approx(1.0)


def test_overrunning_line_is_flagged_and_measured():
    # 48 chars needs 2s at max 24 CPS, but only 1s of slot -> 1s overrun
    cues = [_cue(0.0, 1.0, "a" * 48)]
    rep = m.evaluate_fit(cues, "fr")
    assert rep.overrunning == 1
    assert rep.total_overrun_sec == pytest.approx(1.0)


def test_rushed_but_not_overrunning_is_possible():
    # 25 CPS: above comfortable+max? max is 24 so it's rushed; needed=25/24≈1.04s
    cues = [_cue(0.0, 1.0, "a" * 25)]
    rep = m.evaluate_fit(cues, "fr")
    assert rep.rushed == 1
    assert rep.overrunning == 1  # marginally over
    assert rep.worst_overrun_sec < 0.1


def test_empty_translation_counts_as_missing_coverage():
    cues = [_cue(0.0, 2.0, ""), _cue(3.0, 5.0, "a" * 20)]
    rep = m.evaluate_fit(cues, "fr")
    assert rep.empty == 1
    assert rep.translated == 1
    assert rep.coverage == pytest.approx(0.5)


def test_translated_text_never_falls_back_to_source():
    """Regression: falling back to `text` scored untranslated cues as successes."""
    assert m._translated_text({"translated": "x", "dubbed": "d"}) == "x"
    assert m._translated_text({"dubbed": "d", "text": "t"}) == "d"
    # `text` is the SOURCE line - it must never stand in for a translation
    assert m._translated_text({"text": "t"}) == ""
    assert m._translated_text({"translated": "  ", "text": "t"}) == ""
    assert m._translated_text({}) == ""


def test_source_text_accessor_reads_text():
    assert m._source_text({"text": "t"}) == "t"
    assert m._source_text({}) == ""


def test_empty_session_produces_zeroed_report_not_division_error():
    rep = m.evaluate_fit([], "fr")
    assert rep.cues == 0
    assert rep.coverage == 0.0
    assert rep.rushed_rate == 0.0
    assert rep.mean_cps == 0.0


def test_merge_combines_counts():
    a = m.evaluate_fit([_cue(0.0, 2.0, "a" * 40)], "fr")
    b = m.evaluate_fit([_cue(0.0, 1.0, "a" * 30)], "fr")
    merged = a.merge(b)
    assert merged.cues == 2
    assert merged.translated == 2
    assert merged.rushed == 1


# ── drift detection ──────────────────────────────────────────────────────────
def test_drift_detected_when_stored_cps_is_stale():
    cues = [{"start": 0.0, "end": 1.0, "translated": "a" * 10, "cps": 40.0}]
    out = m.shortening_effectiveness(cues, "fr")
    assert out["checked"] == 1
    assert out["drifted"] == 1


def test_no_drift_when_stored_cps_matches():
    cues = [{"start": 0.0, "end": 1.0, "translated": "a" * 10, "cps": 10.0}]
    out = m.shortening_effectiveness(cues, "fr")
    assert out["drifted"] == 0
    assert out["mean_abs_delta"] == pytest.approx(0.0)
