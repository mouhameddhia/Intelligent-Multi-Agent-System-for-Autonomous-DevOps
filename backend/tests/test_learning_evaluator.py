import json

import pytest

from agent_system.learning import evaluator
from agent_system.learning.evaluator import (
    build_rubric_message,
    compute_outcome_reward,
    evaluate_incident,
    evaluate_pending,
    evaluate_record,
    included_in_learning,
    needs_evaluation,
    parse_rubric_output,
)
from agent_system.learning.recorder import build_record, record_incident_closed
from agent_system.learning.store import InMemoryRecordStore

from learning_fixtures import approved, denied, incident, with_feedback

GOOD_LLM = '{"diagnosis_quality": 82, "fix_quality": 74.4, "rationale": "Specific and consistent."}'


def rec(doc):
    return build_record(doc).model_dump(mode="json")


# ---- deterministic reward -------------------------------------------------

def test_rebuild_pass_and_fail():
    assert compute_outcome_reward(rec(with_feedback(approved(), True))) == (1.0, "rebuild")
    assert compute_outcome_reward(rec(with_feedback(approved(), False))) == (-1.0, "rebuild")


def test_rebuild_outranks_feedback_and_decision():
    # engineer liked the fix, but the rebuild failed: rebuild wins
    doc = with_feedback(approved(), False, liked_fix=True)
    assert compute_outcome_reward(rec(doc)) == (-1.0, "rebuild")


def test_feedback_tier_when_rebuild_unknown():
    doc = with_feedback(approved(), None, liked_fix=True)
    assert compute_outcome_reward(rec(doc)) == (0.6, "feedback")
    doc = with_feedback(approved(), None, liked_fix=False)
    assert compute_outcome_reward(rec(doc)) == (-0.6, "feedback")


def test_decision_tier():
    assert compute_outcome_reward(rec(approved())) == (0.3, "decision")
    assert compute_outcome_reward(rec(denied())) == (-0.5, "decision")


def test_approval_without_an_applied_fix_falls_through_to_judge():
    reward, basis = compute_outcome_reward(rec(approved(applied=False)))
    assert basis == "judge"
    assert reward == pytest.approx(0.1)  # mean(80,75,70)=75 -> (75-50)/50*0.2


def test_judge_tier_is_signed_and_small():
    low = rec(approved(applied=False, judge_scores={"monitoring": 20, "diagnosis": 20, "remediation": 20}))
    assert compute_outcome_reward(low) == (-0.12, "judge")


def test_unknown_outcome_is_excluded():
    doc = rec(with_feedback(incident(), None))
    doc["engineer_feedback"]["liked_fix"] = None
    doc["judge_scores"] = {"monitoring": None, "diagnosis": None, "remediation": None}
    assert compute_outcome_reward(doc) == (None, "none")


def test_reward_always_within_range():
    cases = [
        rec(with_feedback(approved(), True)), rec(with_feedback(approved(), False)),
        rec(approved()), rec(denied()),
        rec(approved(applied=False, judge_scores={"a": 0})),
        rec(approved(applied=False, judge_scores={"a": 100})),
    ]
    for case in cases:
        reward, _ = compute_outcome_reward(case)
        assert reward is None or -1.0 <= reward <= 1.0


def test_stronger_tier_always_outweighs_weaker():
    assert abs(evaluator.REBUILD_REWARD) > abs(evaluator.FEEDBACK_REWARD) > abs(evaluator.DENY_REWARD) >= abs(evaluator.APPROVE_REWARD) > evaluator.JUDGE_REWARD_SPAN


# ---- rubric parsing -------------------------------------------------------

def test_parse_valid_output():
    assert parse_rubric_output(GOOD_LLM) == {
        "diagnosis_quality": 82, "fix_quality": 74, "rationale": "Specific and consistent.",
    }


def test_parse_fenced_output_with_chatter():
    raw = "Sure!\n```json\n" + GOOD_LLM + "\n```"
    assert parse_rubric_output(raw)["diagnosis_quality"] == 82


@pytest.mark.parametrize("raw", ["", "no json here", "{broken", "[1,2]"])
def test_parse_unusable_output(raw):
    assert parse_rubric_output(raw) == {"diagnosis_quality": None, "fix_quality": None, "rationale": ""}


def test_parse_rejects_bad_scores_independently():
    out = parse_rubric_output('{"diagnosis_quality": 140, "fix_quality": true, "rationale": 5}')
    assert out == {"diagnosis_quality": None, "fix_quality": None, "rationale": ""}
    out = parse_rubric_output('{"diagnosis_quality": 40, "fix_quality": "x"}')
    assert out["diagnosis_quality"] == 40 and out["fix_quality"] is None


def test_rationale_is_bounded():
    out = parse_rubric_output(json.dumps({"diagnosis_quality": 1, "fix_quality": 1, "rationale": "x" * 900}))
    assert len(out["rationale"]) == 300


# ---- LLM prompt contents --------------------------------------------------

def test_rubric_message_uses_structured_fields_only():
    message = build_rubric_message(rec(with_feedback(approved(), True, comment="c" * 900)))
    assert "RAW LOG MUST NEVER" not in message
    assert "<incident>" in message and "calc.py" in message
    assert '"rebuild_result": "pass"' in message
    assert "c" * 301 not in message


# ---- evaluate_record ------------------------------------------------------

def test_evaluate_record_combines_reward_and_rubric():
    result = evaluate_record(rec(with_feedback(approved(), True)), llm_call=lambda s, h: GOOD_LLM)
    assert result.outcome_reward == 1.0 and result.reward_basis == "rebuild"
    assert (result.diagnosis_quality, result.fix_quality) == (82, 74)
    assert result.llm_status == "ok"
    assert result.scope == "global"


def test_llm_receives_system_and_human_messages():
    seen = []
    evaluate_record(rec(approved()), llm_call=lambda s, h: seen.append((s, h)) or GOOD_LLM)
    system, human = seen[0]
    assert "diagnosis_quality" in system and "<incident>" in human


def test_llm_failure_keeps_the_deterministic_reward():
    def boom(s, h):
        raise RuntimeError("quota")

    result = evaluate_record(rec(with_feedback(approved(), False)), llm_call=boom)
    assert result.outcome_reward == -1.0
    assert result.diagnosis_quality is None and result.llm_status == "unavailable"


def test_unparseable_llm_output_marks_unavailable():
    result = evaluate_record(rec(approved()), llm_call=lambda s, h: "I refuse")
    assert result.outcome_reward == 0.3 and result.llm_status == "unavailable"


def test_missing_judge_key_makes_default_call_unavailable(monkeypatch):
    monkeypatch.setattr(evaluator.settings, "judge_api_key", "")
    result = evaluate_record(rec(approved()))
    assert result.outcome_reward == 0.3 and result.llm_status == "unavailable"


def test_excluded_record_makes_no_llm_call():
    doc = rec(with_feedback(incident(), None))
    doc["engineer_feedback"]["liked_fix"] = None
    doc["judge_scores"] = {}

    def must_not_run(s, h):
        raise AssertionError("LLM should not be called")

    result = evaluate_record(doc, llm_call=must_not_run)
    assert result.outcome_reward is None and result.llm_status == "skipped"
    assert not included_in_learning(result)


def test_included_in_learning():
    assert included_in_learning({"outcome_reward": -1.0})
    assert not included_in_learning({"outcome_reward": None})
    assert not included_in_learning(None)


# ---- storage and staleness ------------------------------------------------

def _store_with(doc):
    store = InMemoryRecordStore()
    record_incident_closed(doc["_id"], store=store, loader=lambda _id: doc)
    return store


def test_evaluate_incident_stores_evaluation_on_the_record():
    store = _store_with(approved())
    result = evaluate_incident("inc-1", store=store, llm_call=lambda s, h: GOOD_LLM)
    assert result is not None
    stored = store.get("inc-1")["evaluation"]
    assert stored["outcome_reward"] == 0.3 and stored["fix_quality"] == 74
    assert not needs_evaluation(store.get("inc-1"))


def test_current_evaluation_is_not_redone():
    store = _store_with(approved())
    evaluate_incident("inc-1", store=store, llm_call=lambda s, h: GOOD_LLM)
    assert evaluate_incident("inc-1", store=store, llm_call=lambda s, h: GOOD_LLM) is None


def test_feedback_makes_the_evaluation_stale_and_keeps_it_until_redone():
    docs = {"inc-1": approved()}
    store = InMemoryRecordStore()
    record_incident_closed("inc-1", store=store, loader=docs.get)
    evaluate_incident("inc-1", store=store, llm_call=lambda s, h: GOOD_LLM)

    docs["inc-1"] = with_feedback(approved(), True)
    record_incident_closed("inc-1", store=store, loader=docs.get)

    stored = store.get("inc-1")
    assert stored["evaluation"]["reward_basis"] == "decision"  # recorder did not clobber it
    assert needs_evaluation(stored)

    evaluate_incident("inc-1", store=store, llm_call=lambda s, h: GOOD_LLM)
    assert store.get("inc-1")["evaluation"]["outcome_reward"] == 1.0


def test_evaluate_pending_handles_a_batch():
    store = InMemoryRecordStore()
    docs = {"a": approved("a"), "b": denied("b"), "c": with_feedback(approved("c"), True)}
    for incident_id in docs:
        record_incident_closed(incident_id, store=store, loader=docs.get)
    evaluate_incident("a", store=store, llm_call=lambda s, h: GOOD_LLM)

    done = evaluate_pending(store=store, llm_call=lambda s, h: GOOD_LLM)
    assert sorted(done) == ["b", "c"]
    assert evaluate_pending(store=store, llm_call=lambda s, h: GOOD_LLM) == []


def test_evaluation_never_raises_on_store_errors():
    class Broken(InMemoryRecordStore):
        def get(self, incident_id):
            raise RuntimeError("mongo down")

        def list(self, scope="global", limit=5000):
            raise RuntimeError("mongo down")

    assert evaluate_incident("x", store=Broken()) is None
    assert evaluate_pending(store=Broken()) == []
