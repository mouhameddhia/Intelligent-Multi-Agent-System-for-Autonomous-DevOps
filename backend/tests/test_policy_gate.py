"""PolicyGate rules (config/policies.yaml): levels, first-match, fail-safe default, forbidden + alternative."""
from __future__ import annotations

import pytest

from app.agents.policy_gate import PolicyGate
from app.models.agents import PolicyInput
from app.models.incident import ActionTarget, ActionType, ProposedAction, Risk


@pytest.fixture(scope="module")
def gate(settings):
    return PolicyGate(settings.load_yaml("policies.yaml"))


def action(t: ActionType, env: str, risk: Risk = Risk.medium, name: str = "svc") -> ProposedAction:
    return ProposedAction(type=t, target=ActionTarget(kind="deployment", name=name, namespace="ns", environment=env),
                          expected_effect="x", risk=risk)


def verdict(gate: PolicyGate, a: ProposedAction) -> str:
    return gate.evaluate(PolicyInput(incident_id="INC-T", action=a)).decision.verdict


@pytest.mark.parametrize("t", [ActionType.read, ActionType.diagnose, ActionType.propose])
@pytest.mark.parametrize("env", ["production", "staging", "ci"])
def test_read_diagnose_propose_are_automatic(gate, t, env):
    assert verdict(gate, action(t, env, Risk.low)) == "auto"


@pytest.mark.parametrize("t", [ActionType.restart, ActionType.rerun_pipeline])
@pytest.mark.parametrize("env", ["staging", "ci"])
def test_restart_rerun_in_staging_are_auto_logged(gate, t, env):
    assert verdict(gate, action(t, env, Risk.low)) == "auto_logged"


@pytest.mark.parametrize("t", [ActionType.rollback, ActionType.restart, ActionType.config_change, ActionType.scale])
def test_production_changes_require_approval(gate, t):
    assert verdict(gate, action(t, "production", Risk.medium)) == "require_approval"


def test_delete_in_production_is_forbidden_with_safer_alternative(gate):
    out = gate.evaluate(PolicyInput(incident_id="INC-T", action=action(ActionType.delete_resource, "production", Risk.high, "frontend-canary")))
    d = out.decision
    assert d.verdict == "forbidden"
    assert d.rule_id == "P010-forbid-prod-delete"
    assert d.blocked_action is not None and d.blocked_action.type == ActionType.delete_resource
    assert d.alternative is not None and d.alternative.type == ActionType.scale
    assert d.alternative.parameters == {"replicas": 0}
    assert d.alternative.target.name == "frontend-canary"
    # the alternative is re-evaluated by the same rules and is never forbidden
    assert out.effective_verdict == "require_approval"
    assert out.effective_action.source == "policy_gate_alternative"


def test_delete_outside_production_requires_approval(gate):
    assert verdict(gate, action(ActionType.delete_resource, "staging", Risk.high)) == "require_approval"


def test_high_risk_needs_human_even_in_staging(gate):
    assert verdict(gate, action(ActionType.restart, "staging", Risk.high)) == "require_approval"


def test_pull_request_is_auto_logged(gate):
    assert verdict(gate, action(ActionType.pull_request, "ci", Risk.low)) == "auto_logged"


def test_unknown_environment_falls_back_to_approval(gate):
    d = gate.evaluate(PolicyInput(incident_id="INC-T", action=action(ActionType.rollback, "mars", Risk.low))).decision
    assert d.verdict == "require_approval"
    assert d.rule_id == "DEFAULT"


def test_first_match_wins():
    g = PolicyGate({"rules": [
        {"id": "A", "action_types": ["restart"], "verdict": "auto"},
        {"id": "B", "action_types": ["restart"], "verdict": "forbidden"},
    ]})
    assert g.check(ActionType.restart, "production") == ("auto", "A", "")


def test_gate_does_not_use_llm():
    import inspect

    import app.agents.policy_gate as mod
    assert "llm" not in inspect.getsource(mod).lower().replace("no llm", "")
