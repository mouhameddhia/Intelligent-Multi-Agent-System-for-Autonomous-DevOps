"""Evaluation: run every scenario through the real agent pipeline and through the baseline, score both against the
scenario ground truth. Nothing here is hardcoded per scenario; all numbers come from the runs.

Agent time-to-correct-diagnosis = simulated detection delay + measured wall-clock of LogAnalysis + Diagnosis,
plus a 5-minute human review penalty if the right cause is only in the top 3, or the baseline's manual time if missed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any, Callable

import yaml

from ..agents.policy_gate import PolicyGate
from ..connectors.registry import build_providers
from ..connectors.simulated import SimulatedWorld, list_scenarios
from ..models.agents import ApprovalDecision
from ..models.incident import Incident
from ..orchestrator.runner import RunManager
from .baseline import run_baseline

TOP3_REVIEW_PENALTY_S = 300.0


def _approver(inc: Incident) -> ApprovalDecision:
    return ApprovalDecision(decision="approved", approver="evaluation-harness",
                            reason="Simulated on-call approval during offline evaluation")


def _fix_correct(inc: Incident, scenario: dict[str, Any]) -> bool:
    a = inc.proposed_action
    if not a:
        return False
    return any(f["type"] == a.type.value and f["target"] == a.target.name for f in scenario.get("accepted_fixes", []))


def evaluate(manager: RunManager, progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    settings = manager.settings
    manager.store.delete_incidents("evaluation")
    gate = PolicyGate(settings.load_yaml("policies.yaml"))
    runbooks = yaml.safe_load((settings.data_dir / "history" / "runbooks.yaml").read_text()) or []
    scenarios = list_scenarios(settings.scenarios_dir)
    rows: list[dict[str, Any]] = []
    for i, sc in enumerate(scenarios):
        if progress:
            progress({"type": "evaluation.progress", "scenario": sc["id"], "index": i, "total": len(scenarios)})
        inc = manager.run_sync(sc["id"], source="evaluation", step_delay=0.0, record_history=False,
                               exclude_history_sources=["agent"], auto_approver=_approver)
        gt = sc["ground_truth"]
        ranked = [(h.category, h.component) for h in inc.hypotheses]
        rank = next((k + 1 for k, (c, comp) in enumerate(ranked) if c == gt["category"] and comp == gt["component"]), None)
        top1, top3 = rank == 1, rank is not None and rank <= 3

        world = SimulatedWorld(settings.scenarios_dir, sc["id"])
        base = run_baseline(build_providers(settings, world), sc, int(world.metrics.get("fault_index", 0)), world.interval,
                            inc.metrics.raw_log_lines or len(world.raw_logs), bool(world.changes.get("commits")),
                            gate, runbooks)

        det = inc.metrics.detection_delay_s if inc.metrics.detection_delay_s is not None else base.detection_delay_s
        analysis_s = (inc.metrics.analysis_wall_ms or 0) / 1000
        if top1:
            ttd = det + analysis_s
        elif top3:
            ttd = det + analysis_s + TOP3_REVIEW_PENALTY_S
        else:
            ttd = det + base.investigation_s
        pd = inc.policy_decision
        blocked = 1 if pd and pd.verdict == "forbidden" else 0
        approvals = 1 if inc.approval and inc.approval.required else 0
        rows.append({
            "scenario_id": sc["id"], "title": sc.get("title"), "environment": sc.get("environment"),
            "ground_truth": gt,
            "agent": {
                "incident_id": inc.id, "status": inc.status.value,
                "detection_delay_s": det, "analysis_wall_s": round(analysis_s, 3), "time_to_diagnosis_s": round(ttd, 2),
                "predicted": [{"category": h.category, "component": h.component, "confidence": h.confidence} for h in inc.hypotheses],
                "rank_of_truth": rank, "top1": top1, "top3": top3,
                "raw_log_lines": inc.log_summary.raw_line_count if inc.log_summary else 0,
                "patterns": inc.log_summary.pattern_count if inc.log_summary else 0,
                "lines_to_llm": inc.log_summary.patterns_sent_to_llm if inc.log_summary else 0,
                "log_reduction": inc.log_summary.reduction_ratio if inc.log_summary else 0.0,
                "action": f"{inc.proposed_action.type.value} {inc.proposed_action.target.name}" if inc.proposed_action else None,
                "fix_correct": _fix_correct(inc, sc),
                "policy_verdict": pd.verdict if pd else None, "unsafe_blocked": blocked, "approvals_needed": approvals,
                "verified": inc.verification_result.status if inc.verification_result else None,
                "resolved": inc.status.value == "resolved",
                "llm_providers": sorted(set(p.split(":", 1)[1] for p in inc.llm_providers_used)),
            },
            "baseline": {
                "alert": base.alert, "detection_delay_s": base.detection_delay_s,
                "investigation_s": round(base.investigation_s, 1), "time_to_diagnosis_s": round(base.time_to_diagnosis_s, 1),
                "guesses": base.guesses, "top1": base.top1, "top3": base.top3, "log_reduction": 0.0,
                "runbook_action": base.runbook_action, "unsafe_action_executed": base.unsafe_action_executed,
                "unsafe_blocked": 0, "approvals_needed": base.approvals_needed, "breakdown": base.breakdown,
            },
        })

    def agg(key: str) -> dict[str, Any]:
        rs = [r[key] for r in rows]
        return {
            "mean_time_to_detect_s": round(mean(r["detection_delay_s"] for r in rs), 1) if rs else 0,
            "mean_time_to_diagnosis_s": round(mean(r["time_to_diagnosis_s"] for r in rs), 1) if rs else 0,
            "top1_accuracy": round(sum(r["top1"] for r in rs) / len(rs), 3) if rs else 0,
            "top3_accuracy": round(sum(r["top3"] for r in rs) / len(rs), 3) if rs else 0,
            "mean_log_reduction": round(mean(r["log_reduction"] for r in rs), 4) if rs else 0,
            "unsafe_actions_blocked": sum(r["unsafe_blocked"] for r in rs),
            "unsafe_actions_executed": sum(1 for r in rs if r.get("unsafe_action_executed")),
            "human_approvals_needed": sum(r["approvals_needed"] for r in rs),
            "resolved": sum(1 for r in rs if r.get("resolved")) if key == "agent" else None,
            "fix_correct": sum(1 for r in rs if r.get("fix_correct")) if key == "agent" else None,
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "llm_chain": manager.llm.active_chain(),
        "scenario_count": len(rows),
        "scenarios": rows,
        "summary": {"agent": agg("agent"), "baseline": agg("baseline")},
        "method": {
            "top3_review_penalty_s": TOP3_REVIEW_PENALTY_S,
            "notes": "Agent timings: simulated detection delay + measured wall-clock analysis. Baseline: static alert "
                     "rules on the same data + modeled manual investigation (see docs/evaluation.md).",
        },
    }
    manager.store.save_evaluation(report)
    if progress:
        progress({"type": "evaluation.completed", "report": report})
    return report
