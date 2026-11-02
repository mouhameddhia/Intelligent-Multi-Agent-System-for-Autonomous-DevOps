"""Export preference pairs from learning records as JSONL for possible future
DPO. Nothing is trained here.

Two kinds of pair, both built between records that share a grouping key
(failure_signature by default) so the "prompt" context is comparable:
  - basis "decision": an approved record (chosen) vs a denied one (rejected)
  - basis "rebuild":  a record whose rebuild passed (chosen) vs one that failed

Each pair carries the incident ids so a future training step can rebuild the
exact original prompts; "prompt" is the chosen record's diagnosis summary, the
best shared context available without re-reading reports."""

import json
from itertools import product
from typing import IO, Any, Literal

from .models import GLOBAL_SCOPE
from .store import RecordStore, get_store

MAX_PAIRS_PER_GROUP = 5


def _steps_text(record: dict) -> str:
    return "\n".join(f"{i}. {s}" for i, s in enumerate(record.get("proposed_steps") or [], start=1))


def _pair(chosen: dict, rejected: dict, basis: str) -> dict[str, Any] | None:
    chosen_text, rejected_text = _steps_text(chosen), _steps_text(rejected)
    if not chosen_text or not rejected_text or chosen_text == rejected_text:
        return None
    return {
        "basis": basis,
        "scope": chosen.get("scope", GLOBAL_SCOPE),
        "failure_category": chosen.get("failure_category", ""),
        "failure_signature": chosen.get("failure_signature", ""),
        "prompt": chosen.get("diagnosis_summary", ""),
        "chosen": chosen_text,
        "rejected": rejected_text,
        "chosen_incident_id": chosen.get("incident_id", ""),
        "rejected_incident_id": rejected.get("incident_id", ""),
    }


def build_preference_pairs(
    records: list[dict],
    *,
    group_by: Literal["failure_signature", "failure_category"] = "failure_signature",
    max_pairs_per_group: int = MAX_PAIRS_PER_GROUP,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        groups.setdefault((record.get("scope", GLOBAL_SCOPE), record.get(group_by, "")), []).append(record)

    pairs: list[dict[str, Any]] = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda r: r.get("incident_id", ""))
        rules = (
            ("decision",
             [r for r in members if (r.get("human_decision") or {}).get("decision") == "approve"],
             [r for r in members if (r.get("human_decision") or {}).get("decision") == "deny"]),
            ("rebuild",
             [r for r in members if r.get("rebuild_result") == "pass"],
             [r for r in members if r.get("rebuild_result") == "fail"]),
        )
        for basis, chosen_pool, rejected_pool in rules:
            count = 0
            for chosen, rejected in product(chosen_pool, rejected_pool):
                if count >= max_pairs_per_group:
                    break
                pair = _pair(chosen, rejected, basis)
                if pair:
                    pairs.append(pair)
                    count += 1
    return pairs


def write_jsonl(pairs: list[dict[str, Any]], out: IO[str]) -> int:
    for pair in pairs:
        out.write(json.dumps(pair, ensure_ascii=False) + "\n")
    return len(pairs)


def export_preference_pairs(
    path: str,
    *,
    store: RecordStore | None = None,
    scope: str = GLOBAL_SCOPE,
    group_by: Literal["failure_signature", "failure_category"] = "failure_signature",
) -> int:
    """Write preference pairs to `path` as JSONL; returns the pair count."""
    records = (store or get_store()).list(scope=scope)
    pairs = build_preference_pairs(records, group_by=group_by)
    with open(path, "w", encoding="utf-8") as out:
        return write_jsonl(pairs, out)
