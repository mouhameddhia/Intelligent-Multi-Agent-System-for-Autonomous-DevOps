"""Remediation agent: proposes a fix (rerun, restart, rollback, config change, PR, scale, delete) for the top hypothesis.

It never executes. The action type comes from the runbook and similar past incidents (weighted vote over the template's
allowed actions); parameters are computed from live provider state; the LLM only writes the rationale.
"""
from __future__ import annotations

import re
from typing import Any

from ..connectors.base import ProviderSet
from ..llm.client import LLMClient
from ..models.agents import RemediationInput, RemediationOutput
from ..models.incident import ActionTarget, ActionType, ProposedAction, Risk
from ..store.db import Store

SYSTEM = (
    "You are an SRE writing the justification for a proposed remediation. You get the diagnosis and the chosen action. "
    "Do not change the action. Return JSON: "
    '{"rationale": "<2 sentences>", "expected_effect": "<1 sentence>"}'
)
RISK_ORDER = [Risk.low, Risk.medium, Risk.high]


def _mi(v: str) -> int:
    m = re.match(r"(\d+)\s*(Mi|Gi)?", str(v))
    if not m:
        return 0
    n = int(m.group(1))
    return n * 1024 if m.group(2) == "Gi" else n


class RemediationAgent:
    name = "remediation"

    def __init__(self, providers: ProviderSet, llm: LLMClient, store: Store, templates: dict[str, Any]) -> None:
        self.p = providers
        self.llm = llm
        self.store = store
        self.templates: dict[str, Any] = templates.get("templates", {})
        self.base_risk: dict[str, str] = templates.get("risk", {})

    def choose_action(self, category: str, similar_ids: list[str]) -> tuple[ActionType, dict[str, float]]:
        tpl = self.templates.get(category, {})
        allowed = tpl.get("allowed_actions", [tpl.get("default_action", "restart")])
        votes: dict[str, float] = {a: 0.0 for a in allowed}
        for row in self.store.list_history(retrieval_only=True, exclude_sources=("agent",)):
            if row.get("category") != category or row.get("fix_action") not in votes:
                continue
            w = 1.0 if row["source"] == "runbook" else 0.8 if row["id"] in similar_ids else 0.3
            votes[row["fix_action"]] += w
        if not any(votes.values()):
            return ActionType(tpl.get("default_action", allowed[0])), votes
        return ActionType(max(votes, key=lambda a: (votes[a], a == tpl.get("default_action")))), votes

    def risk(self, action: ActionType, env: str) -> Risk:
        r = Risk(self.base_risk.get(action.value, "medium"))
        if env == "production" and r == Risk.low and action not in (ActionType.pull_request, ActionType.read):
            r = Risk.medium
        return r

    def build(self, inp: RemediationInput, action: ActionType) -> ProposedAction | None:
        h = inp.hypothesis
        ns = inp.namespace
        env = inp.environment
        svc = inp.service
        params: dict[str, Any] = {}
        target = ActionTarget(kind="deployment", name=svc, namespace=ns, environment=env)
        dep = self.p.orchestration.get_deployment(ns, svc) if ns else None
        cfg = (dep.config if dep else {}) or {}

        if action == ActionType.config_change and h.category == "memory_limit":
            cur = cfg.get("resources", {}).get("limits", {}).get("memory", "128Mi")
            prev = cfg.get("previous", {}).get("resources", {}).get("limits", {}).get("memory")
            new = max(_mi(cur) * 2, _mi(prev or "0"), 256)
            params = {"patch": {"resources": {"limits": {"memory": f"{new}Mi"}}}, "from": cur, "to": f"{new}Mi"}
        elif action == ActionType.config_change:
            prev = {k: v for k, v in cfg.get("previous", {}).items()}
            if not prev:
                return self.build(inp, ActionType.rollback) if dep and len(dep.revisions) > 1 else None
            changed = {k: {"from": cfg.get(k), "to": v} for k, v in prev.items()}
            params = {"configmap": cfg.get("configmap"), "patch": {"data": prev}, "changes": changed}
        elif action == ActionType.rollback:
            if not dep or len(dep.revisions) < 2:
                return None
            good = next((r for r in reversed(dep.revisions[:-1]) if r.image != dep.image), dep.revisions[-2])
            params = {"to_revision": good.revision, "from_image": dep.image, "to_image": good.image}
        elif action == ActionType.pull_request:
            if not inp.change:
                return None
            target = ActionTarget(kind="repository", name=svc, namespace=None, environment=env)
            params = {"repository": inp.change.repository, "revert_commit": inp.change.commit_sha,
                      "branch": f"autofix/revert-{inp.change.commit_sha[:7]}",
                      "title": f"Revert \"{inp.change.message}\"", "files": inp.change.files_changed}
        elif action == ActionType.rerun_pipeline:
            if not inp.pipeline:
                return None
            target = ActionTarget(kind="pipeline", name=inp.pipeline.pipeline_id, namespace=None, environment=env)
            params = {"ref": inp.pipeline.branch or "main", "run_id": inp.pipeline.run_id}
        elif action in (ActionType.delete_resource, ActionType.scale) and h.category == "stale_canary":
            canary = next((r.ref.split("/", 1)[1] for e in [h.evidence] for r in e
                           if r.ref.startswith("k8s:deployment/")), None)
            if not canary:
                return None
            target = ActionTarget(kind="deployment", name=canary, namespace=ns, environment=env)
            params = {"kind": "deployment"} if action == ActionType.delete_resource else {"replicas": 0}
        elif action == ActionType.restart:
            params = {}
        else:
            params = {}

        tpl = self.templates.get(h.category, {})
        return ProposedAction(
            type=action, target=target, parameters=params, expected_effect=tpl.get("expected_effect", "Service recovers."),
            risk=self.risk(action, env), reversible=action not in (ActionType.delete_resource,),
            hypothesis_id=h.id)

    def run(self, inp: RemediationInput) -> RemediationOutput:
        action_type, votes = self.choose_action(inp.hypothesis.category, inp.hypothesis.similar_incidents)
        action = self.build(inp, action_type)
        if action is None:
            return RemediationOutput(incident_id=inp.incident_id, action=None, llm_provider="none",
                                     summary=f"No safe automated remediation could be built for {inp.hypothesis.category}; "
                                             "missing data for the action parameters.")
        payload = {
            "incident_id": inp.incident_id,
            "diagnosis": {"title": inp.hypothesis.title, "confidence": inp.hypothesis.confidence,
                          "evidence": [e.description for e in inp.hypothesis.evidence][:4]},
            "action": {"type": action.type.value, "target": action.target.model_dump(), "parameters": action.parameters,
                       "risk": action.risk.value},
            "history_votes": votes,
        }

        def mock(pl: dict[str, Any]) -> dict[str, Any]:
            a = pl["action"]
            basis = "runbook and similar past incidents" if any(pl["history_votes"].values()) else "the default playbook"
            return {
                "rationale": (f"{pl['diagnosis']['title']} is the top hypothesis ({pl['diagnosis']['confidence']:.0%}). "
                              f"Following {basis}, the proposed fix is {a['type'].replace('_', ' ')} on "
                              f"{a['target']['kind']}/{a['target']['name']} in {a['target']['environment']}."),
            }

        llm = self.llm.complete_json("remediation_rationale", SYSTEM, payload, mock,
                                     validate=lambda d: isinstance(d.get("rationale"), str))
        action.rationale = str(llm.data.get("rationale", ""))[:800]
        if llm.provider != "mock" and isinstance(llm.data.get("expected_effect"), str):
            action.expected_effect = llm.data["expected_effect"][:400]
        return RemediationOutput(incident_id=inp.incident_id, action=action, llm_provider=llm.provider,
                                 summary=f"Proposed {action.type.value.replace('_', ' ')} on {action.target.kind}/"
                                         f"{action.target.name} ({action.target.environment}), risk {action.risk.value}.")
