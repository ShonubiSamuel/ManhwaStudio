"""
scripts/ai/recap_narrator.py — the Recap storytelling pipeline.

A stateful, two-call-per-batch pipeline that turns cropped manga/manhwa panels
into a flowing, name-aware recap narration — not a robotic panel-by-panel
description. Based on the entity-memory pattern used by long-form visual-story
systems (MangaFlow story-section memory, LlmLink dual-LLM coreference,
VideoMemory entity banks):

    Call 1 — VISION  : each panel → honest structured facts (no name guessing).
    Call 2 — RESOLVE+NARRATE (one reasoned text pass):
        • resolve who each character is (appearance + STORY CONTEXT, so a
          masked/disguised figure can be a known character), confidence-gated;
        • narrate one line per panel, using resolved names + relationships,
          hedging when unsure;
        • update the living character registry + the rolling story memory.

State (character registry + rolling memory) lives in the project's
recap_state.json and is carried forward batch to batch. The registry is a
wiki-style LIVING profile (current appearance + history + aliases + status +
relationships), so a character who changes over time (weak → powerful form)
stays the SAME entity. It is user-editable — the human is the safety net.

Resolve and narrate are kept in ONE call (not split into a separate agent) so
the model can self-correct ("this only makes sense if the ninja is the hero →
raise confidence"), and to stay cheap/rate-limit-friendly (2 calls per batch).
"""

from __future__ import annotations

import json
import re
from typing import Callable, List, Optional


# ── JSON extraction (tolerant of fences / stray prose) ────────────────────────

def _extract_json(raw: str, kind: str):
    """Pull the first JSON array (kind='[') or object (kind='{') out of a model
    response, tolerating ```json fences and surrounding prose."""
    if not raw:
        return None
    txt = raw.strip()
    # strip code fences
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.DOTALL).strip()
    open_c, close_c = ("[", "]") if kind == "[" else ("{", "}")
    start = txt.find(open_c)
    if start < 0:
        return None
    # balance-scan to the matching close (handles nested braces + strings)
    depth, in_str, esc = 0, False, False
    for i in range(start, len(txt)):
        ch = txt[i]
        if in_str:
            if esc:            esc = False
            elif ch == "\\":   esc = True
            elif ch == '"':    in_str = False
            continue
        if ch == '"':          in_str = True
        elif ch == open_c:     depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(txt[start:i + 1])
                except Exception:
                    return None
    return None


# ── Registry (living wiki profiles) ───────────────────────────────────────────

def blank_state() -> dict:
    return {"registry": {"characters": []}, "memory": "", "cast_seed": ""}


def _uniq_extend(dst: list, items) -> None:
    for it in (items or []):
        it = (it or "").strip() if isinstance(it, str) else it
        if it and it not in dst:
            dst.append(it)


def merge_registry(registry: dict, updates: list) -> None:
    """Merge resolver 'registry_updates' into the persistent registry, BY NAME
    (case-insensitive). Scalar fields overwrite; list fields union; appearance
    changes append the old current_appearance to history."""
    chars = registry.setdefault("characters", [])
    by_name = {(c.get("name") or "").strip().lower(): c for c in chars}
    for u in (updates or []):
        if not isinstance(u, dict):
            continue
        name = (u.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        c = by_name.get(key)
        if c is None:
            c = {"name": name, "aliases": [], "current_appearance": "",
                 "appearance_history": [], "status": "", "relationships": [],
                 "first_seen_panel": u.get("last_seen_panel"),
                 "last_seen_panel": u.get("last_seen_panel")}
            chars.append(c)
            by_name[key] = c
        # appearance change → archive the old one
        new_app = (u.get("current_appearance") or "").strip()
        if new_app and new_app != (c.get("current_appearance") or "").strip():
            if c.get("current_appearance"):
                _uniq_extend(c.setdefault("appearance_history", []), [c["current_appearance"]])
            c["current_appearance"] = new_app
        if u.get("status"):
            c["status"] = u["status"].strip()
        _uniq_extend(c.setdefault("aliases", []), u.get("aliases"))
        _uniq_extend(c.setdefault("relationships", []), u.get("relationships"))
        _uniq_extend(c.setdefault("appearance_history", []), u.get("appearance_history"))
        if u.get("last_seen_panel") is not None:
            c["last_seen_panel"] = u["last_seen_panel"]
            if c.get("first_seen_panel") is None:
                c["first_seen_panel"] = u["last_seen_panel"]


def _registry_brief(registry: dict, max_chars: int = 24) -> str:
    """Compact the registry for the resolver prompt (names + the fields that
    matter for identity + narration). Cap to keep the prompt lean."""
    out = []
    for c in (registry.get("characters") or [])[:max_chars]:
        parts = [f"- {c.get('name')}"]
        if c.get("aliases"):          parts.append(f"aka {', '.join(c['aliases'][:4])}")
        if c.get("current_appearance"): parts.append(f"looks: {c['current_appearance']}")
        if c.get("status"):           parts.append(f"status: {c['status']}")
        if c.get("relationships"):    parts.append(f"ties: {'; '.join(c['relationships'][:4])}")
        out.append(" | ".join(parts))
    return "\n".join(out) if out else "(none yet)"


# ── Call 1: VISION — honest structured facts per panel ────────────────────────

def _vision_prompt(n: int) -> str:
    return (
        f"You are a precise visual analyst for a manhwa/manga recap. You are given "
        f"{n} sequential panel images from the SAME chapter, in reading order.\n"
        f"Report ONLY what is LITERALLY visible. Do NOT guess character names, do NOT "
        f"invent story, do NOT narrate. Describe appearances concretely (hair, build, "
        f"clothing, distinctive features, masks) so the SAME person can be recognised in "
        f"later panels. If a face is hidden/masked, say so honestly.\n"
        f"TRACK PEOPLE ACROSS THESE PANELS: give each distinct individual a short stable "
        f"'id' tag (e.g. 'black_hair_man', 'orange_healer') and REUSE the exact same id and "
        f"a consistent desc every time that same person reappears — even if their pose, "
        f"framing, or expression changes. This lets the recap follow recurring characters, "
        f"so be consistent.\n"
        f"TRANSCRIBE ALL TEXT — this is critical. Copy VERBATIM into 'captions' any narration "
        f"boxes, caption text, on-screen text (dates, signs, labels) and sound effects. These "
        f"are the AUTHOR'S OWN narration and carry the real story (dates, world-building, "
        f"exposition) — never drop them. Put only spoken speech-bubble lines in 'dialogue'.\n\n"
        f"Return ONLY a JSON array of exactly {n} objects, one per panel in order:\n"
        f'{{"scene":"where/what setting","characters":[{{"id":"stable tag reused for the same '
        f'person across panels","desc":"concrete appearance","position":"left/center/right/'
        f'background","notable":"weapon/pose/expression"}}],"captions":["verbatim narration-box '
        f'/ on-screen text — NOT character speech"],"dialogue":[{{"text":"bubble text",'
        f'"speaker_id":"id of the character who says it, or \'\' if unclear"}}],'
        f'"actions":["what is happening"],"mood":"tone"}}\n'
        f"No markdown, no commentary — JSON array only."
    )


def extract_facts(images_b64: List[str], provider: str, *, nvidia_key: str = "",
                  nvidia_vision_model: str = "", gemini_key: str = "",
                  gemini_vision_model: str = "", log: Callable) -> List[dict]:
    """VISION call. Returns a list of per-panel fact dicts (len == images)."""
    prompt = _vision_prompt(len(images_b64))
    if provider == "nvidia":
        import nvidia_provider
        raw = nvidia_provider.call_vision([("image/jpeg", b) for b in images_b64],
                                          prompt, nvidia_key, model=nvidia_vision_model)
    elif provider == "gemini":
        import gemini_provider
        raw = gemini_provider.call_vision([("image/jpeg", b) for b in images_b64],
                                          prompt, gemini_key, model=gemini_vision_model)
    else:
        from ai import openai_compat
        raw = openai_compat.call_vision(prompt, images_b64, provider=provider)
    facts = _extract_json(raw, "[")
    if not isinstance(facts, list):
        log(f"vision: unparseable facts: {raw[:200]}", "warning")
        facts = []
    # normalise length to the batch
    facts = [f if isinstance(f, dict) else {} for f in facts][:len(images_b64)]
    while len(facts) < len(images_b64):
        facts.append({})
    return facts


# ── Call 2: RESOLVE + NARRATE + update state (one reasoned text pass) ──────────

# Shared narration discipline — the rules that keep the recap tight, story-first
# (not slideshow-describing), name-aware, and dialogue-attributed. Used by BOTH
# the legacy per-batch prompt and the whole-chapter windowed prompt.
_NARRATE_RULES = (
    "   • LENGTH: tight — usually ONE punchy sentence (≤25 words); an action beat may run two "
    "short, driving sentences (≤35 words) when the fight needs room to breathe. Cut filler, "
    "never energy; never write a paragraph.\n"
    "   • TIGHT BUT NOT FLAT — this is an EXCITING recap, make every line LAND. Action must feel "
    "visceral and kinetic; emotion must hit hard. Use vivid, forceful verbs and real impact "
    "('he cracks the bat across one thug's skull, then drops the next'), NOT limp summary ('he "
    "swings a plank, crushing each thug in brutal succession'). Short punchy fragments are great "
    "for action ('He swings. One drops. Then another.'). Momentum and stakes over description.\n"
    "   • NO PADDING (energy comes from strong verbs + stakes, not word count): no adjective "
    "pile-ups ('reddened eyes glaring through exhaustion and dark circles'), no purple clauses "
    "('vomiting forth', 'swallowing him whole'). One concrete detail at most. Summarize groups "
    "('monsters pour out'), don't list every type. Never invent drama, timing, or events not shown.\n"
    "   • FOCUS ON WHAT MATTERS: narrate the action, emotion, or plot that drives the story — "
    "never incidental visual trivia ('his eye reflects a small silhouette', background clutter, "
    "exact colors). If a detail carries no story or feeling, drop it.\n"
    "   • CAPTIONS ARE THE STORY: when a panel has caption/narration text (the author's own "
    "narration boxes — dates, exposition, world-building), CONVEY THAT INFORMATION; it is the "
    "real story, not the picture. Weave in the facts it states (e.g. 'On January 1st 2020 the "
    "world changed — dungeons appeared and monsters poured out'). Land emotional caption lines "
    "with their full weight — you may quote a short punch line verbatim (his confession: 'I "
    "hated the world'). Never ignore caption text or replace it with invented action.\n"
    "   • CONSISTENT REFERENCE: refer to each character the SAME way every time. Once someone is "
    "established — by name, or by a role label like 'the protagonist' / 'the healer' — reuse "
    "THAT label or a pronoun (he/she/they). NEVER re-introduce an already-seen person with a "
    "fresh anonymous description ('a beaten victim', 'a broken silhouette', 'a lone figure'): "
    "that same beaten man IS the protagonist — call him the protagonist.\n"
    "   • Narrate the STORY, never the artwork or the act of watching. BANNED phrases: 'the "
    "scene', 'the panel', 'the view', 'the camera', 'we see', \"we're\", 'the scene "
    "shifts/sharpens/erupts', 'cut to', 'suddenly we'. Don't narrate transitions between panels.\n"
    "   • DIALOGUE: attribute any spoken line to WHO says it, by name or role — never float it "
    "as the narrator's own stray thought.\n"
    "   • Hedge ONLY when genuinely unsure. Never invent facts, names, or events not supported "
    "by the panel facts, cast, registry, identity map, or memory.\n"
    "   • SPOKEN punctuation only — this narration is read aloud by a TTS voice. NO em-dashes "
    "('—'), no semicolons, no parentheses: use a comma for a short pause and a period for a "
    "full stop. 'Monsters pour through: orcs, goblins, wolves.' NOT 'monsters pour through — "
    "orcs, goblins, wolves'.\n"
)


def _narrate_prompt(style: str, cast_seed: str, registry: dict, memory: str,
                    facts: List[dict], panel_ids: List[int]) -> str:
    facts_lines = []
    for pid, f in zip(panel_ids, facts):
        facts_lines.append(f"PANEL {pid}: " + json.dumps(f, ensure_ascii=False))
    cast_block = (f"\nAUTHORITATIVE CAST (user-provided — trust these names & looks):\n{cast_seed.strip()}\n"
                  if cast_seed.strip() else "")
    mem_block = f"\nSTORY SO FAR (recent context — continue it, don't repeat):\n{memory.strip()}\n" if memory.strip() else ""
    return (
        f"You are the storyteller AND continuity keeper for a manhwa/manga recap video.\n"
        f"NARRATION STYLE: {style}\n"
        f"{cast_block}"
        f"\nCHARACTER REGISTRY (known characters — REUSE these identities; a character may "
        f"change appearance over time yet still be the SAME person):\n{_registry_brief(registry)}\n"
        f"{mem_block}\n"
        f"NEW PANELS (structured visual facts, in reading order):\n" + "\n".join(facts_lines) + "\n\n"
        f"Do THREE things, IN THIS ORDER:\n"
        f"1) RESOLVE identities: for each character seen, decide WHO it is by matching "
        f"appearance AND story context. The facts tag each person with a stable 'id' (and "
        f"dialogue with 'speaker_id'); the SAME id across panels is the SAME individual — "
        f"resolve each id to ONE identity and hold it consistent across every panel. A "
        f"masked/disguised/changed figure can be a known character if the story implies it "
        f"(e.g. someone who just said they'd infiltrate). "
        f"Assign confidence 0-1. If confidence < 0.6, DO NOT name them — call them 'a masked "
        f"figure', 'a hunter', etc.\n"
        f"2) NARRATE: write ONE TIGHT narration entry per panel — present tense, {style}. "
        f"HARD RULES (a recap narrator, not a scene-by-scene describer):\n"
        f"{_NARRATE_RULES}"
        f"3) UPDATE state: registry_updates ONLY for characters that are new or changed this "
        f"batch (set current_appearance for appearance changes — the old one is archived "
        f"automatically); and a rewritten rolling 'memory' — a compact running summary "
        f"(aim ~120 words) that keeps the thread of the story for the next batch.\n\n"
        f"Return ONLY this JSON object (no markdown):\n"
        f'{{"narration":[{{"panel":<id>,"text":"..."}} for each panel],'
        f'"registry_updates":[{{"name":"...","aliases":["..."],"current_appearance":"...",'
        f'"status":"...","relationships":["..."],"last_seen_panel":<id>}}],'
        f'"memory":"updated rolling summary"}}'
    )


def _salvage_narration(raw: str) -> dict:
    """Extract every COMPLETE {"panel":N,"text":"..."} object from a possibly
    truncated response. The cut-off last item won't match (no closing quote), so
    we keep whatever finished — a truncated batch degrades to partial narration
    instead of a total failure."""
    out = {}
    for m in re.finditer(r'\{\s*"panel"\s*:\s*(\d+)\s*,\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}', raw):
        try:
            out[int(m.group(1))] = json.loads('"' + m.group(2) + '"').strip()
        except Exception:
            pass
    return out


def narrate_batch(facts: List[dict], panel_ids: List[int], style: str, state: dict,
                  *, provider: str, nvidia_key: str, lm_model: str, context_length: int,
                  log: Callable, max_tokens: int = 8000) -> dict:
    """RESOLVE+NARRATE text call. Mutates `state` (registry + memory) in place.
    Returns {"lines": [{"index", "text"}]}."""
    from ai import text_utils
    prompt = _narrate_prompt(style, state.get("cast_seed", ""), state.get("registry", {}),
                             state.get("memory", ""), facts, panel_ids)
    raw = text_utils.call_provider(
        prompt, provider=provider, api_key=nvidia_key, lm_model=lm_model,
        max_tokens=max_tokens, context_length=context_length, task="refine",
    )
    obj = _extract_json(raw, "{")
    if isinstance(obj, dict):
        # Normal path: narration + registry + memory all parsed.
        by_panel = {}
        for item in (obj.get("narration") or []):
            if isinstance(item, dict) and item.get("panel") is not None:
                by_panel[int(item["panel"])] = str(item.get("text") or "").strip()
        lines = [{"index": pid, "text": by_panel.get(pid, "")} for pid in panel_ids]
        merge_registry(state.setdefault("registry", {"characters": []}), obj.get("registry_updates") or [])
        new_mem = (obj.get("memory") or "").strip()
        if new_mem:
            state["memory"] = new_mem
        n = sum(1 for l in lines if l["text"])
        log(f"resolved {len(state['registry'].get('characters', []))} character(s); "
            f"narrated {n}/{len(panel_ids)} panel(s)", "muted")
        return {"lines": lines}

    # Truncated / malformed → salvage whatever narration completed. Registry &
    # memory are NOT updated this batch (they came after the cut-off); the next
    # batch continues from the last good state.
    salv = _salvage_narration(raw)
    if not salv:
        raise ValueError(f"Unparseable narration response: {raw[:300]}")
    lines = [{"index": pid, "text": salv.get(pid, "")} for pid in panel_ids]
    n = sum(1 for l in lines if l["text"])
    log(f"response was truncated — salvaged {n}/{len(panel_ids)} panel(s); "
        f"registry/memory not updated this batch", "warning")
    return {"lines": lines}


# ══════════════════════════════════════════════════════════════════════════════
# WHOLE-CHAPTER PIPELINE  (vision chunked → read-all → windowed narration)
# ──────────────────────────────────────────────────────────────────────────────
# The per-batch path above narrates 2-3 panels at a time, forward-only, so it can
# never look ahead — a character revealed in a late panel can't retro-fix an
# earlier "a dark-haired man". This pipeline fixes that:
#
#   Phase 1  VISION (chunked, done by the caller): every panel → rich facts.
#   Phase 2a UNDERSTAND (build_story_bible): ONE cheap text pass reads ALL the
#            facts and returns a small identity map + arc outline — full hindsight.
#   Phase 2b NARRATE (narrate_all): windowed narration that writes every panel a
#            tight line using the identity map + a rolling, compressing summary.
#            The summary is also the cross-chapter memory (chapter N+1 continues
#            from chapter N's summary).
# ══════════════════════════════════════════════════════════════════════════════

def _facts_digest(facts: List[dict], panel_ids: List[int]) -> str:
    """Compact, token-lean rendering of all panel facts for the whole-chapter
    understanding pass (ids + desc + dialogue + actions, one line per panel)."""
    out = []
    for pid, f in zip(panel_ids, facts):
        f = f or {}
        chars = []
        for c in (f.get("characters") or []):
            tag = (c.get("id") or "?").strip()
            desc = (c.get("desc") or "").strip()
            chars.append(f"{tag}({desc})" if desc else tag)
        dlg = []
        for d in (f.get("dialogue") or []):
            spk = (d.get("speaker_id") or "?").strip()
            dlg.append(f'{spk}:"{(d.get("text") or "").strip()}"')
        caps = [str(c).strip() for c in (f.get("captions") or []) if str(c).strip()]
        parts = [f"P{pid}"]
        if f.get("scene"):   parts.append(f"scene={str(f['scene']).strip()}")
        if chars:            parts.append("chars=" + "; ".join(chars))
        if caps:             parts.append("caption=" + " | ".join(caps))
        if f.get("actions"): parts.append("act=" + "; ".join(str(a) for a in f["actions"]))
        if dlg:              parts.append("say=" + " | ".join(dlg))
        magi = f.get("magi_visual_evidence") or {}
        if magi.get("summary"):
            parts.append("grounding=" + str(magi["summary"]))
        out.append(" · ".join(parts))
    return "\n".join(out)


def build_story_bible(facts: List[dict], panel_ids: List[int], state: dict, *,
                      provider: str, nvidia_key: str, lm_model: str,
                      context_length: int, log: Callable, max_tokens: int = 3000,
                      retrieved_memory: str = "") -> dict:
    """Phase 2a — READ EVERYTHING FIRST. One text pass over ALL panel facts →
    a small identity map + arc outline, so later narration has full-chapter
    hindsight (a face named in a late panel is known from the very first one)."""
    from ai import text_utils
    cast = (state.get("cast_seed") or "").strip()
    mem  = (state.get("memory") or "").strip()
    prompt = (
        "You are the continuity director for a manhwa/manga recap. Below are compact "
        "visual facts for EVERY panel of this chapter, in reading order. Each person "
        "carries a stable 'id' tag reused across panels; dialogue carries a 'speaker_id'. "
        "Text marked 'caption=' is the AUTHOR'S OWN narration (dates, exposition, "
        "world-building) — treat it as authoritative for the plot and the arc outline.\n"
        + (f"\nAUTHORITATIVE CAST (trust these names & looks):\n{cast}\n" if cast else "")
        + (f"\nRETRIEVED STORY LEDGER (confirmed identities with source-panel history; "
           f"use only when it fits the current evidence):\n{retrieved_memory}\n" if retrieved_memory else "")
        + (f"\nSTORY SO FAR (earlier chapters):\n{mem}\n" if mem else "")
        + "\nALL PANELS:\n" + _facts_digest(facts, panel_ids) + "\n\n"
        "Read the WHOLE chapter first. Then, using HINDSIGHT (someone revealed late is the "
        "same person in earlier panels; a line of dialogue that names or addresses someone "
        "tells you who a nearby unnamed figure is), do TWO things:\n"
        "1) IDENTITY MAP — for each id, decide who they are. IMPORTANT: the vision pass tags "
        "people PER CHUNK, so the SAME individual usually gets DIFFERENT ids across the chapter "
        "(the beaten man early on and the fighter later can be one person). MERGE them: give "
        "every id that is the same person the SAME name_or_role, so they read as one continuous "
        "character. Pick ONE label per person and reuse it for all their ids. A NAME may ONLY "
        "come from this chapter's own dialogue, the cast list above, or the retrieved story ledger above — "
        "NEVER invent a name and NEVER borrow one from any other manhwa or story you know. If no "
        "such name exists, use a stable role label ('the protagonist', 'the swordsman', 'the "
        "healer'). Assign a name only when reasonably sure (confidence ≥ 0.6); else the role label.\n"
        "2) ARC OUTLINE — 3–6 sentences capturing the chapter's throughline and its biggest "
        "beats/reveals: what a viewer must understand.\n\n"
        "Return ONLY this JSON (no markdown):\n"
        '{"identities":[{"id":"...","name_or_role":"...","confidence":0.0,"note":"why / revealed where"}],'
        '"outline":"3-6 sentence arc"}'
    )
    raw = text_utils.call_provider(
        prompt, provider=provider, api_key=nvidia_key, lm_model=lm_model,
        max_tokens=max_tokens, context_length=context_length, task="refine")
    obj = _extract_json(raw, "{")
    if not isinstance(obj, dict):
        log("story bible: unparseable — narration will resolve identity per-window", "warning")
        return {"identities": [], "outline": ""}
    ids = [i for i in (obj.get("identities") or []) if isinstance(i, dict)]
    log(f"story bible: mapped {len(ids)} character id(s); arc outline ready", "muted")
    return {"identities": ids, "outline": (obj.get("outline") or "").strip()}


def _bible_block(bible: dict) -> str:
    """Render the identity map + arc outline into a prompt block."""
    lines = []
    for it in (bible.get("identities") or []):
        if not isinstance(it, dict):
            continue
        who = (it.get("name_or_role") or "?").strip()
        tag = f"- {(it.get('id') or '?').strip()} → {who}"
        conf = it.get("confidence")
        if conf is not None:
            try:    tag += f" (conf {float(conf):.1f})"
            except (TypeError, ValueError): pass
        lines.append(tag)
    id_map = "\n".join(lines) if lines else "(none resolved)"
    block = ("\nIDENTITY MAP (id → who, resolved with FULL-CHAPTER hindsight — treat as "
             f"ground truth):\n{id_map}\n")
    if bible.get("outline"):
        block += f"\nCHAPTER ARC (the throughline you are recapping):\n{bible['outline'].strip()}\n"
    return block


def _window_size_for(n_panels: int, max_tokens: int) -> int:
    """How many panels to narrate per GLM call so the JSON never truncates.
    Budget ~140 output tokens per panel line + overhead for summary/registry."""
    cap = max(1, (int(max_tokens) - 900) // 140)
    return max(4, min(n_panels, cap))


def _narrate_window_prompt(style: str, cast_seed: str, bible: dict,
                           running_summary: str, facts: List[dict], panel_ids: List[int],
                           *, first: bool, last: bool) -> str:
    facts_lines = [f"PANEL {pid}: " + json.dumps(f or {}, ensure_ascii=False)
                   for pid, f in zip(panel_ids, facts)]
    cast_block = (f"\nAUTHORITATIVE CAST (trust these names & looks):\n{cast_seed.strip()}\n"
                  if cast_seed.strip() else "")
    sum_block = (f"\nSTORY SO FAR (already narrated — continue it, do NOT repeat or "
                 f"re-introduce known characters):\n{running_summary.strip()}\n"
                 if running_summary.strip() else "")
    scope = ("These are the FIRST panels of the recap — open the story cleanly."
             if first else "Continue seamlessly from the summary above.")
    return (
        f"You are the storyteller for a manhwa/manga recap video.\n"
        f"NARRATION STYLE: {style}\n"
        f"{cast_block}"
        f"{_bible_block(bible)}"
        f"{sum_block}\n"
        f"{scope}\n"
        f"PANELS TO NARRATE NOW (structured visual facts, in reading order):\n"
        + "\n".join(facts_lines) + "\n\n"
        f"Write ONE narration entry per panel, present tense, {style}.\n"
        f"HARD RULES (a recap narrator, not a scene-by-scene describer):\n"
        f"{_NARRATE_RULES}"
        f"   • The IDENTITY MAP is GROUND TRUTH: whatever it maps an id to — a name OR a role "
        f"like 'the protagonist' — that IS who they are from their first appearance here, even "
        f"when the panel facts describe them differently (beaten, masked, changed). Refer to "
        f"them by that ONE label every time; never re-introduce them as 'a beaten victim' / 'a "
        f"figure'. NEVER invent a name that isn't in the identity map, the cast, or the "
        f"retrieved story ledger, and never carry a name over from a different story.\n"
        f"   • Every panel listed above MUST get exactly one narration entry — never skip "
        f"or merge panels.\n\n"
        f"Then write an updated 'summary': a compact running recap (~120 words, COMPRESS "
        f"older parts) of everything narrated up to AND including this window, so the next "
        f"window and the next chapter continue the thread.\n\n"
        f"Return ONLY this JSON (no markdown):\n"
        f'{{"narration":[{{"panel":<id>,"text":"..."}} for EACH panel above],'
        f'"summary":"updated compact running summary"}}'
    )


def narrate_all(facts: List[dict], panel_ids: List[int], style: str, state: dict, *,
                provider: str, nvidia_key: str, lm_model: str, context_length: int,
                log: Callable, max_tokens: int = 8000, bible: Optional[dict] = None) -> dict:
    """Phase 2b — windowed narration with full-chapter hindsight. Splits panels
    into windows sized to the output budget; each window sees the identity map,
    arc outline, and a rolling compressing summary of everything narrated so far,
    and returns one tight line per panel. EVERY panel gets a line. Mutates `state`
    (registry + memory = the rolling summary). Returns {"lines":[{index,text}]}."""
    from ai import text_utils
    bible = bible or {"identities": [], "outline": ""}
    win = _window_size_for(len(panel_ids), max_tokens)
    n_windows = (len(panel_ids) + win - 1) // win
    running_summary = (state.get("memory") or "").strip()   # cross-chapter seed
    got: dict = {}
    log(f"story: narrating {len(panel_ids)} panel(s) in {n_windows} window(s) of ≤{win}", "muted")
    for wi, s in enumerate(range(0, len(panel_ids), win), 1):
        w_ids, w_facts = panel_ids[s:s + win], facts[s:s + win]
        log(f"story: window {wi}/{n_windows} — panels {w_ids[0]}–{w_ids[-1]}…", "muted")
        prompt = _narrate_window_prompt(
            style, state.get("cast_seed", ""), bible,
            running_summary, w_facts, w_ids, first=(wi == 1), last=(wi == n_windows))
        raw = text_utils.call_provider(
            prompt, provider=provider, api_key=nvidia_key, lm_model=lm_model,
            max_tokens=max_tokens, context_length=context_length, task="refine")
        obj = _extract_json(raw, "{")
        if isinstance(obj, dict):
            by_panel = {}
            for item in (obj.get("narration") or []):
                if isinstance(item, dict) and item.get("panel") is not None:
                    by_panel[int(item["panel"])] = str(item.get("text") or "").strip()
            new_sum = (obj.get("summary") or "").strip()
            if new_sum:
                running_summary = new_sum
        else:
            by_panel = _salvage_narration(raw)
            log(f"story: window {wi} malformed — salvaged {len(by_panel)}/{len(w_ids)} "
                f"panel(s)", "warning")
        for pid in w_ids:
            got[pid] = by_panel.get(pid, "")
    state["memory"] = running_summary
    lines = [{"index": pid, "text": got.get(pid, "")} for pid in panel_ids]
    n = sum(1 for l in lines if l["text"])
    log(f"story: narrated {n}/{len(panel_ids)} panel(s); Story Memory updated", "success")
    return {"lines": lines}
