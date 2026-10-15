"""Execute step: performs an authorized action strictly through provider interfaces."""
from __future__ import annotations

from ..connectors.base import ActionOutcome, ProviderSet, ReadOnlyError
from ..models.agents import ExecuteInput, ExecuteOutput
from ..models.incident import ActionType, ExecutionResult, utcnow


class Executor:
    name = "execute"

    def __init__(self, providers: ProviderSet) -> None:
        self.p = providers

    def _dispatch(self, inp: ExecuteInput) -> ActionOutcome:
        a = inp.action
        t = a.target
        ns = t.namespace or ""
        prm = a.parameters
        if a.type == ActionType.rerun_pipeline:
            return self.p.pipeline.trigger_pipeline(t.name, prm.get("ref"))
        if a.type == ActionType.restart:
            return self.p.orchestration.restart(ns, t.name)
        if a.type == ActionType.rollback:
            return self.p.orchestration.rollback(ns, t.name, prm.get("to_revision"))
        if a.type == ActionType.config_change:
            return self.p.orchestration.patch_config(ns, t.name, prm.get("patch", {}))
        if a.type == ActionType.scale:
            return self.p.orchestration.scale(ns, t.name, int(prm.get("replicas", 0)))
        if a.type == ActionType.delete_resource:
            return self.p.orchestration.delete(ns, prm.get("kind", t.kind), t.name)
        if a.type == ActionType.pull_request:
            body = (f"Automated remediation for incident {inp.incident_id}.\n\n{a.rationale}\n\n"
                    f"Reverts {prm.get('revert_commit', '')}. Expected effect: {a.expected_effect}")
            return self.p.repository.create_pull_request(
                prm.get("repository", inp.repository or ""), prm.get("title", "Automated fix"), body,
                {"service": t.name, "branch": prm.get("branch"), "revert_commit": prm.get("revert_commit")})
        raise ValueError(f"action type {a.type.value} is not executable")

    def run(self, inp: ExecuteInput) -> ExecuteOutput:
        try:
            out = self._dispatch(inp)
            status = "success" if out.ok else "failed"
            res = ExecutionResult(status=status, action_type=inp.action.type, provider=out.provider, details=out.message,
                                  output=out.output, executed_at=utcnow())
        except ReadOnlyError as e:
            res = ExecutionResult(status="failed", action_type=inp.action.type, provider="read-only", details=str(e),
                                  executed_at=utcnow())
        except Exception as e:  # noqa: BLE001
            res = ExecutionResult(status="failed", action_type=inp.action.type, provider="error",
                                  details=f"{type(e).__name__}: {e}", executed_at=utcnow())
        return ExecuteOutput(incident_id=inp.incident_id, result=res)
