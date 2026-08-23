"""Evidence-backed, selectively retrieved memory for Recap narration.

This is intentionally a deterministic layer around the LLM rather than another
agent.  It stores canonical characters, visual appearances and narrated events
with their source panels, then returns a small relevant context block.  The
writer therefore receives facts it can cite, not a growing unverified synopsis.
"""

from __future__ import annotations

import json
import re
from typing import Iterable


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def _tokens(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-zA-Z][a-zA-Z0-9'-]{2,}", (value or "").lower())
            if word not in {"the", "with", "from", "that", "this", "and", "for", "are", "was"}}


def _is_name(label: str) -> bool:
    """Conservative heuristic: a role phrase should not become a fake name."""
    lower = (label or "").strip().lower()
    role_words = {"the", "a", "an", "man", "woman", "boy", "girl", "protagonist", "villain",
                  "hunter", "healer", "swordsman", "figure", "warrior", "old", "young", "masked"}
    words = set(re.findall(r"[a-z]+", lower))
    return bool(lower) and not words.intersection(role_words) and len(words) <= 4


def _stable_id(project_id: int, label: str, existing_ids: set[str]) -> str:
    base = f"char-{project_id}-{_norm(label) or 'unknown'}"
    candidate, suffix = base, 2
    while candidate in existing_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def sync_legacy_registry(db, project_id: int, registry: dict) -> dict[str, str]:
    """Import editable v1 registry entries once while preserving user corrections."""
    existing = db.list_story_characters(project_id)
    by_label: dict[str, str] = {}
    ids = {row["stable_id"] for row in existing}
    for row in existing:
        for label in [row.get("canonical_name"), row.get("role_label"), *(row.get("aliases") or [])]:
            if label:
                by_label[(label or "").strip().lower()] = row["stable_id"]
    # Legacy state is a one-time migration source, never an authority. Replaying
    # it on every read resurrected characters a reviewer had deliberately
    # deleted or renamed in Story Memory.
    if existing:
        return by_label
    try:
        retired = set(json.loads(db.get_setting(f"story_memory_retired_labels_{project_id}", "[]") or "[]"))
    except Exception:
        retired = set()
    for character in (registry.get("characters") or []):
        name = (character.get("name") or "").strip()
        if not name or name.lower() in retired:
            continue
        stable_id = by_label.get(name.lower())
        if not stable_id:
            stable_id = _stable_id(project_id, name, ids)
            ids.add(stable_id)
        db.upsert_story_character(
            project_id, stable_id,
            canonical_name=name if _is_name(name) else "",
            role_label="" if _is_name(name) else name,
            aliases=character.get("aliases") or [],
            appearance=character.get("current_appearance") or "",
            status=character.get("status") or "",
            panel_index=character.get("last_seen_panel"),
        )
        by_label[name.lower()] = stable_id
    return by_label


def retire_character_labels(db, project_id: int, character: dict) -> None:
    """Keep a deleted legacy entity from being imported again later."""
    try:
        retired = set(json.loads(db.get_setting(f"story_memory_retired_labels_{project_id}", "[]") or "[]"))
    except Exception:
        retired = set()
    for label in [character.get("canonical_name"), character.get("role_label"), *(character.get("aliases") or [])]:
        if label:
            retired.add(str(label).strip().lower())
    db.set_setting(f"story_memory_retired_labels_{project_id}", json.dumps(sorted(retired), ensure_ascii=False))


def retrieve_context(db, project_id: int, facts: list[dict], registry: dict, *, limit: int = 10) -> str:
    """Return the smallest useful memory slice for this chapter's identity pass.

    We use deterministic lexical/rule scoring before embeddings are available.
    This keeps the prompt bounded today and gives a clean insertion point for a
    Magi/ReID vector reranker later.
    """
    sync_legacy_registry(db, project_id, registry)
    query = _tokens(json.dumps(facts, ensure_ascii=False))
    scored = []
    for char in db.list_story_characters(project_id):
        fields = " ".join([char.get("canonical_name", ""), char.get("role_label", ""),
                            char.get("appearance", ""), char.get("status", ""),
                            " ".join(char.get("aliases") or [])])
        overlap = len(query.intersection(_tokens(fields)))
        recency = int(char.get("last_seen_panel") or 0) / 1_000_000
        scored.append((overlap * 10 + recency, char))
    picked = [char for _, char in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]]
    if not picked:
        return "(no confirmed characters yet)"
    lines = []
    for char in picked:
        display = char.get("canonical_name") or char.get("role_label") or char["stable_id"]
        bits = [f"- [{char['stable_id']}] {display}"]
        if char.get("aliases"): bits.append("aka " + ", ".join(char["aliases"][:3]))
        if char.get("appearance"): bits.append("appearance: " + char["appearance"])
        if char.get("status"): bits.append("status: " + char["status"])
        if char.get("last_seen_panel") is not None: bits.append(f"last verified: panel {char['last_seen_panel']}")
        lines.append(" | ".join(bits))
    return "\n".join(lines)


def record_chapter_identities(db, project_id: int, facts: list[dict], panel_ids: list[int],
                              bible: dict, registry: dict) -> dict[str, str]:
    """Persist the full-chapter identity decisions and visual evidence.

    Returns ``vision_tag -> stable character id``. Low-confidence role labels
    stay distinct and reviewable instead of being silently merged into a name.
    """
    label_to_id = sync_legacy_registry(db, project_id, registry)
    existing_ids = {row["stable_id"] for row in db.list_story_characters(project_id)}
    tag_to_id: dict[str, str] = {}
    identity_by_tag = {str(item.get("id") or "").strip(): item
                       for item in (bible.get("identities") or []) if isinstance(item, dict)}
    for tag, item in identity_by_tag.items():
        if not tag:
            continue
        label = (item.get("name_or_role") or "unknown character").strip()
        confidence = float(item.get("confidence") or 0)
        # A generic role is not an identity.  Never merge two people solely
        # because the model called both of them "the man" or "the hunter".
        # Named, high-confidence matches may reuse prior evidence; all other
        # decisions stay scoped to the vision tag until a human/strong evidence
        # confirms the merge.
        can_merge = _is_name(label) and confidence >= 0.75
        stable_id = label_to_id.get(label.lower()) if can_merge else None
        if not stable_id:
            stable_id = _stable_id(project_id, label if can_merge else f"{label}-{tag}", existing_ids)
            existing_ids.add(stable_id)
        db.upsert_story_character(
            project_id, stable_id,
            canonical_name=label if _is_name(label) and confidence >= 0.6 else "",
            role_label="" if _is_name(label) and confidence >= 0.6 else label,
            confidence=confidence,
        )
        if can_merge:
            label_to_id[label.lower()] = stable_id
        tag_to_id[tag] = stable_id

    for panel_id, fact in zip(panel_ids, facts):
        for person in (fact.get("characters") or []):
            tag = str(person.get("id") or "").strip()
            stable_id = tag_to_id.get(tag)
            if not stable_id:
                continue
            description = (person.get("desc") or "").strip()
            conf = float((identity_by_tag.get(tag) or {}).get("confidence") or 0)
            db.add_story_appearance(project_id, stable_id, panel_id, source_tag=tag,
                                    description=description, confidence=conf,
                                    evidence={"panel": panel_id, "vision": person,
                                              "magi": fact.get("magi_visual_evidence") or {}})
            db.upsert_story_character(project_id, stable_id, appearance=description,
                                      confidence=conf, panel_index=panel_id)
    return tag_to_id


def record_narrated_events(db, project_id: int, lines: Iterable[dict], facts: list[dict],
                           panel_ids: list[int], tag_to_id: dict[str, str]) -> None:
    facts_by_panel = {pid: fact for pid, fact in zip(panel_ids, facts)}
    for line in lines:
        panel = line.get("index")
        text = (line.get("text") or "").strip()
        if not text or panel is None:
            continue
        fact = facts_by_panel.get(panel) or {}
        participants = [tag_to_id[tag] for tag in [str(c.get("id") or "").strip()
                        for c in (fact.get("characters") or [])] if tag in tag_to_id]
        db.add_story_event(project_id, text, panel_from=panel, panel_to=panel,
                           participants=list(dict.fromkeys(participants)),
                           evidence={"panel": panel, "captions": fact.get("captions") or [],
                                     "dialogue": fact.get("dialogue") or [], "actions": fact.get("actions") or [],
                                     "magi": fact.get("magi_visual_evidence") or {}})


def snapshot(db, project_id: int) -> dict:
    return {"characters": db.list_story_characters(project_id), "events": db.list_story_events(project_id, 50),
            "history": db.story_memory_history_state(project_id)}
