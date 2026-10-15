"""PolicyGate: rule-based authorization of actions (no LLM). Rules come from config/policies.yaml.

First matching rule wins; the default is fail-safe (`require_approval`). A forbidden action is blocked and, when the
rule defines one, replaced by a safer alternative which is evaluated again by the same rules.
"""
from __future__ import annotations

from typing import Any

from ..models.agents import PolicyInput, PolicyOutput
from ..models.incident import ActionType, PolicyDecision, ProposedAction, Risk, utcnow

Verdict = str


class PolicyGate:
    name = "policy_gate"

    def __init__(self, policies: dict[str, Any]) -> None:
        self.rules: list[dict[str, Any]] = policies.get("rules", [])
        self.default: dict[str, Any] = policies.get("default", {"verdict": "require_approval", "reason": "default"})

    @staticmethod
    def _matches(rule: dict[str, Any], action_type: ActionType, env: str, risk: Risk | None) -> bool:
        if "action_types" in rule and action_type.value not in rule["action_types"]:
            return False
        if "environments" in rule and env not in rule["environments"]:
            return False
        if "risk" in rule and (risk is None or risk.value not in rule["risk"]):
            return False
        return True

    def match(self, action_type: ActionType, env: str, risk: Risk | None = None) -> dict[str, Any]:
        for rule in self.rules:
            if self._matches(rule, action_type, env, risk):
                return rule
        return {"id": "DEFAULT", **self.default}

    def check(self, action_type: ActionType, env: str, risk: Risk | None = None) -> tuple[Verdict, str, str]:
        """Lightweight check used for read/diagnose/propose steps."""
        rule = self.match(action_type, env, risk)
        return rule["verdict"], rule["id"], rule.get("description", rule.get("reason", ""))

    def evaluate(self, inp: PolicyInput) -> PolicyOutput:
        a = inp.action
        env = a.target.environment
        rule = self.match(a.type, env, a.risk)
        verdict = rule["verdict"]
        decision = PolicyDecision(verdict=verdict, rule_id=rule["id"],
                                  reason=rule.get("description", rule.get("reason", "")),
                                  action_type=a.type, environment=env, evaluated_at=utcnow())
        if verdict != "forbidden":
            return PolicyOutput(incident_id=inp.incident_id, decision=decision, effective_action=a, effective_verdict=verdict)

        decision.blocked_action = a
        alt_cfg = rule.get("alternative")
        if not alt_cfg:
            return PolicyOutput(incident_id=inp.incident_id, decision=decision, effective_action=a, effective_verdict="forbidden")
        alt = ProposedAction(
            type=ActionType(alt_cfg["type"]), target=a.target, parameters=dict(alt_cfg.get("parameters", {})),
            expected_effect=alt_cfg.get("expected_effect", a.expected_effect), risk=Risk(alt_cfg.get("risk", "medium")),
            reversible=True, hypothesis_id=a.hypothesis_id, source="policy_gate_alternative",
            rationale=f"Safer alternative suggested by policy {rule['id']}: the original '{a.type.value}' on "
                      f"{a.target.kind}/{a.target.name} in {env} is forbidden.")
        alt_rule = self.match(alt.type, env, alt.risk)
        alt_verdict = alt_rule["verdict"]
        if alt_verdict == "forbidden":  # never chain forbidden alternatives
            alt_verdict = "require_approval"
        decision.alternative = alt
        decision.alternative_verdict = alt_verdict
        decision.reason += f" Alternative '{alt.type.value}' evaluated by {alt_rule['id']}: {alt_verdict}."
        return PolicyOutput(incident_id=inp.incident_id, decision=decision, effective_action=alt, effective_verdict=alt_verdict)
