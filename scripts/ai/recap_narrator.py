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
relationships), so a character who changes over time (weak → Shadow Monarch)
stays the SAME entity. It is user-editable — the human is the safety net.

Resolve and narrate are kept in ONE call (not split into a separate agent) so
the model can self-correct ("this only makes sense if the ninja is Jin-Woo →
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
        f"later panels. If a face is hidden/masked, say so honestly.\n\n"
        f"Return ONLY a JSON array of exactly {n} objects, one per panel in order:\n"
        f'{{"scene":"where/what setting","characters":[{{"desc":"concrete appearance",'
        f'"position":"left/center/right/background","notable":"weapon/pose/expression"}}],'
        f'"dialogue":[{{"text":"bubble text","speaker_desc":"who appears to say it"}}],'
        f'"actions":["what is happening"],"mood":"tone"}}\n'
        f"No markdown, no commentary — JSON array only."
    )


def extract_facts(images_b64: List[str], provider: str, *, nvidia_key: str = "",
                  nvidia_vision_model: str = "", log: Callable) -> List[dict]:
    """VISION call. Returns a list of per-panel fact dicts (len == images)."""
    prompt = _vision_prompt(len(images_b64))
    if provider == "nvidia":
        import nvidia_provider
        raw = nvidia_provider.call_vision([("image/jpeg", b) for b in images_b64],
                                          prompt, nvidia_key, model=nvidia_vision_model)
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
        f"appearance AND story context. A masked/disguised/changed figure can be a known "
        f"character if the story implies it (e.g. someone who just said they'd infiltrate). "
        f"Assign confidence 0-1. If confidence < 0.6, DO NOT name them — call them 'a masked "
        f"figure', 'a hunter', etc.\n"
        f"2) NARRATE: write ONE narration entry per panel — vivid and flowing, present "
        f"tense, {style}. Give each panel the room it deserves (a sentence or a few); use "
        f"resolved names and relationships for depth; hedge when unsure. NEVER mention "
        f"panels, images, or pages. Do NOT invent facts not supported by the panel facts, "
        f"cast, registry, or memory.\n"
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
