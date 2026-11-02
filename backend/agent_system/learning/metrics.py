"""Outcome metrics per policy version, computed from learning records.

Definitions (each rate is None when its denominator is zero):
  approval_rate          approved / (approved + denied)
  rebuild_pass_rate      rebuilds that passed / rebuilds with a known result
                         (any resolution path, including manual fixes)
  first_fix_success_rate among incidents where PipeGuard applied a fix, how many
                         rebuilt green: the fix worked the first time it was tried
Records created while no policy was active group under policy_version_used=None.
"""

from typing import Any


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _summarise(records: list[dict]) -> dict[str, Any]:
    approved = sum(1 for r in records if (r.get("human_decision") or {}).get("decision") == "approve")
    denied = sum(1 for r in records if (r.get("human_decision") or {}).get("decision") == "deny")
    passed = sum(1 for r in records if r.get("rebuild_result") == "pass")
    failed = sum(1 for r in records if r.get("rebuild_result") == "fail")
    applied = [r for r in records if r.get("fix_applied") is True]
    first_known = [r for r in applied if r.get("rebuild_result") in ("pass", "fail")]
    first_ok = sum(1 for r in first_known if r.get("rebuild_result") == "pass")
    return {
        "incidents": len(records),
        "approved": approved,
        "denied": denied,
        "approval_rate": _rate(approved, approved + denied),
        "rebuild_known": passed + failed,
        "rebuild_passed": passed,
        "rebuild_pass_rate": _rate(passed, passed + failed),
        "fixes_applied": len(applied),
        "first_fix_known": len(first_known),
        "first_fix_succeeded": first_ok,
        "first_fix_success_rate": _rate(first_ok, len(first_known)),
    }


def compute_metrics(records: list[dict]) -> dict[str, Any]:
    groups: dict[int | None, list[dict]] = {}
    for record in records:
        version = record.get("policy_version_used")
        groups.setdefault(version if isinstance(version, int) else None, []).append(record)
    ordered = sorted(groups, key=lambda v: (v is not None, v or 0))
    return {
        "overall": _summarise(records),
        "by_policy_version": [{"policy_version_used": v, **_summarise(groups[v])} for v in ordered],
    }
