"""One full end-to-end run per scenario in MOCK_LLM mode, checked against the scenario ground truth."""
from __future__ import annotations

import pytest

from app.connectors.simulated import list_scenarios
from app.config import get_settings
from app.models.agents import ApprovalDecision
from app.models.incident import IncidentStatus

SCENARIOS = list_scenarios(get_settings().scenarios_dir)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_full_loop(manager, store, scenario):
    """detect → analyze → diagnose → propose → gate → (approval) → execute → verify → record."""
    run = manager.create(scenario["id"], step_delay=0)
    inc = manager.execute(run)
    exp = scenario["expected"]
    gt = scenario["ground_truth"]

    # detection + log analysis
    assert inc.trigger is not None
    assert inc.service == gt["component"]
    assert inc.log_summary and inc.log_summary.raw_line_count > 0
    assert inc.log_summary.reduction_ratio > 0.9
    # diagnosis: correct root cause ranked first, with evidence
    top = inc.hypotheses[0]
    assert (top.category, top.component) == (gt["category"], gt["component"])
    assert top.evidence and 0 < top.confidence <= 1
    # policy
    assert inc.policy_decision and inc.policy_decision.verdict == exp["policy_verdict"]
    assert (inc.policy_decision.blocked_action is not None) == exp["blocked"]

    if inc.approval and inc.approval.required:
        assert inc.status == IncidentStatus.awaiting_approval
        assert inc.execution_result is None, "nothing may execute before approval"
        inc = manager.submit_approval(inc.id, ApprovalDecision(decision="approved", approver="pytest",
                                                               reason="looks correct"), wait=True)
    # execution + verification
    assert inc.execution_result and inc.execution_result.status == "success"
    fix = (inc.proposed_action.type.value, inc.proposed_action.target.name)
    assert fix == (gt["fix"]["type"], gt["fix"]["target"])
    assert inc.verification_result and inc.verification_result.status == "healthy"
    assert inc.status == IncidentStatus.resolved
    assert all(s.status in ("completed", "skipped") for s in inc.steps)
    # record: audit trail + history entry
    actions = [a["action"] for a in store.list_audit(inc.id)]
    assert "record" in actions and any(a.startswith("execute:") for a in actions)
    assert any(h["id"] == inc.id for h in store.list_history())


def test_rejection_stops_before_execution(manager, store):
    run = manager.create("catalog-bad-image", step_delay=0)
    inc = manager.execute(run)
    assert inc.status == IncidentStatus.awaiting_approval
    inc = manager.submit_approval(inc.id, ApprovalDecision(decision="rejected", approver="pytest",
                                                           reason="change freeze in effect"), wait=True)
    assert inc.status == IncidentStatus.rejected
    assert inc.execution_result is None
    assert run.ctx.world is not None and run.ctx.world.applied == []


def test_forbidden_action_is_never_executed(manager):
    run = manager.create("frontend-stale-canary", step_delay=0)
    inc = manager.execute(run)
    d = inc.policy_decision
    assert d.verdict == "forbidden" and d.blocked_action.type.value == "delete_resource"
    assert inc.proposed_action.type.value == "scale"
    manager.submit_approval(inc.id, ApprovalDecision(decision="approved", approver="pytest", reason="scale to zero ok"), wait=True)
    assert [a["action"] for a in run.ctx.world.applied] == ["scale"]


def test_approval_survives_restart(manager, settings, store):
    """Rehydrate an interrupted run from storage (e.g. after a server restart) and resume it."""
    inc = manager.execute(manager.create("checkout-config-crashloop", step_delay=0))
    manager.runs.clear()  # simulate a process restart losing the in-memory graph
    out = manager.submit_approval(inc.id, ApprovalDecision(decision="approved", approver="pytest", reason="after restart"), wait=True)
    assert out.status == IncidentStatus.resolved


def test_missing_logs_flags_incident(manager, monkeypatch):
    from app.connectors.simulated import SimulatedLoggingProvider, SimulatedPipelineProvider
    monkeypatch.setattr(SimulatedPipelineProvider, "get_pipeline_logs", lambda self, *a, **k: [])
    run = manager.create("ci-test-regression", step_delay=0)
    run.ctx.world.raw_logs = []
    inc = manager.execute(run)
    assert inc.status == IncidentStatus.needs_attention
    assert "logs" in inc.missing_data
    assert inc.execution_result is None


def test_evaluation_scores_against_ground_truth(manager):
    from app.evaluation.evaluate import evaluate
    report = evaluate(manager)
    a, b = report["summary"]["agent"], report["summary"]["baseline"]
    assert report["scenario_count"] == len(SCENARIOS)
    assert a["top1_accuracy"] == 1.0 and a["resolved"] == len(SCENARIOS)
    assert a["unsafe_actions_blocked"] == 1 and b["unsafe_actions_executed"] == 1
    assert a["mean_time_to_diagnosis_s"] < b["mean_time_to_diagnosis_s"]
    assert a["mean_log_reduction"] > 0.9
