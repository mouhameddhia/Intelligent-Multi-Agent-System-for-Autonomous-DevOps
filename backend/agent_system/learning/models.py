"""Pydantic models for the opt-in, per-account learning layer."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

GLOBAL_SCOPE = "global"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HumanDecision(BaseModel):
    decision: Literal["approve", "deny"] | None = None
    # The decision route does not capture a reason today, so this stays empty
    # until the API gains one.
    reason: str = ""
    approver: str = ""
    decided_at: str | None = None


class EngineerFeedback(BaseModel):
    build_passed: bool | None = None
    liked_fix: bool | None = None
    system_efficient: bool | None = None
    comment: str = ""
    resolution_path: str = ""
    submitted_at: str | None = None


class EvaluationResult(BaseModel):
    """Evaluator output stored on a LearningRecord.

    outcome_reward is deterministic (-1..1, None = unknown outcome, excluded
    from learning). The two rubric scores come from the LLM and are advisory;
    they are None when the model was unavailable or the record was excluded.
    """

    scope: str = GLOBAL_SCOPE
    outcome_reward: float | None = None
    reward_basis: Literal["rebuild", "feedback", "decision", "judge", "none"] = "none"
    diagnosis_quality: int | None = None
    fix_quality: int | None = None
    rationale: str = ""
    llm_status: Literal["ok", "unavailable", "skipped"] = "skipped"
    # Fingerprint of the outcome fields the evaluation was based on, so a
    # later feedback submission marks it stale.
    outcome_signature: str = ""
    evaluator_model: str = ""
    evaluated_at: str = Field(default_factory=utc_now)


class Rule(BaseModel):
    """One advisory lesson. support_count is the number of distinct incidents
    that bore on it; success_count how many of those had a positive outcome,
    so failures are support_count - success_count."""

    id: str
    scope: str = GLOBAL_SCOPE
    text: str
    failure_category: str
    evidence_incident_ids: list[str] = Field(default_factory=list)
    support_count: int = 0
    success_count: int = 0
    status: Literal["candidate", "active", "retired"] = "candidate"

    @property
    def failure_count(self) -> int:
        return self.support_count - self.success_count


class PriorCount(BaseModel):
    success: int = 0
    failure: int = 0


class PolicyVersion(BaseModel):
    """An immutable snapshot of the policy. `priors` is keyed
    "<failure_category>|<fix_action_type>"."""

    version: int
    scope: str = GLOBAL_SCOPE
    status: Literal["active", "previous", "archived"] = "active"
    rules: list[Rule] = Field(default_factory=list)
    priors: dict[str, PriorCount] = Field(default_factory=dict)
    source_record_ids: list[str] = Field(default_factory=list)
    change_summary: str = ""
    created_at: str = Field(default_factory=utc_now)


class LearningRunResult(BaseModel):
    status: Literal["published", "no_change", "not_due", "nothing_to_learn", "llm_failed", "busy", "disabled"]
    version: int | None = None
    records_used: int = 0
    operations_applied: int = 0
    operations_rejected: list[str] = Field(default_factory=list)
    records_evaluated: int = 0


class LearningRecord(BaseModel):
    """One closed incident, reduced to structured fields only. Raw console
    logs and full reports are deliberately not stored here."""

    incident_id: str
    scope: str = GLOBAL_SCOPE
    failure_signature: str
    failure_category: str
    diagnosis_summary: str = ""
    proposed_steps: list[str] = Field(default_factory=list)
    fix_action_type: str
    human_decision: HumanDecision = Field(default_factory=HumanDecision)
    fix_applied: bool | None = None
    rebuild_result: Literal["pass", "fail", "unknown"] = "unknown"
    engineer_feedback: EngineerFeedback | None = None
    judge_scores: dict[str, int | None] = Field(default_factory=dict)
    policy_version_used: int | None = None
    # Written only by the evaluator; the recorder never overwrites it.
    evaluation: EvaluationResult | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
