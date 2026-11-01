import json

import pytest

from agent_system.agents import remediation
from agent_system.learning import replay
from agent_system.learning.injection import get_guidance, policy_override
from agent_system.learning.models import PolicyVersion, PriorCount, Rule
from agent_system.learning.registry import InMemoryPolicyStore, PolicyRegistry, set_registry
from agent_system.learning.replay import (
    format_report,
    held_out_records,
    known_resolution,
    run_replay,
)
from agent_system.learning.store import InMemoryRecordStore

from learning_fixtures import approved
from remediation_scenarios import FakeChat

RULE = Rule(id="r1", text="Fix the function under test and rerun the failing unit test", failure_category="test_assertion",
            status="active", support_count=3, success_count=3)


def resolved_doc(incident_id, **kw):
    return {**approved(incident_id), "monitoring_report": "m", "solved_step_id": "s1", **kw}


def record(incident_id, rebuild="pass", category="test_assertion", created=None):
    return {"incident_id": incident_id, "scope": "global", "created_at": created or incident_id,
            "rebuild_result": rebuild, "failure_category": category, "diagnosis_summary": "Root cause: x"}


def world(record_ids=("h1", "h2"), sources=(), extra_records=()):
    store, registry = InMemoryRecordStore(), PolicyRegistry(InMemoryPolicyStore())
    for incident_id in record_ids:
        store.upsert(record(incident_id))
    for r in extra_records:
        store.upsert(r)
    registry.publish(PolicyVersion(
        version=0, rules=[RULE], priors={"test_assertion|code_patch": PriorCount(success=3)},
        source_record_ids=list(sources),
    ))
    docs = {i: resolved_doc(i) for i in list(record_ids) + [r["incident_id"] for r in extra_records]}
    return store, registry, docs


def fake_remediation(state):
    """Real guidance lookup (so policy_override is exercised), fake model."""
    guidance, version = get_guidance(state)
    out = {**state, "steps": [{"text": "guided fix" if guidance else "plain fix"}]}
    if version is not None:
        out["policy_version_used"] = version
    return out


def fake_judge(system, human):
    return json.dumps({"fix_quality": 90 if "guided fix" in human else 60, "rationale": "r"})


# ---- helpers ---------------------------------------------------------------------

def test_known_resolution_prefers_the_solved_step():
    doc = {"solved_step_id": "s2", "steps": [{"id": "s1", "text": "no"}, {"id": "s2", "text": "yes"}],
           "custom_solution": "custom", "fix_result": {"applied": True, "file": "a.py"}}
    assert known_resolution(doc) == "yes"
    assert known_resolution({"custom_solution": " custom "}) == "custom"
    assert "a.py" in known_resolution({"fix_result": {"applied": True, "file": "a.py"}})
    assert known_resolution({"fix_result": {"applied": False}}) == ""
    assert len(known_resolution({"custom_solution": "x" * 900})) == 400


def test_held_out_excludes_failures_and_records_that_built_the_policy():
    registry = PolicyRegistry(InMemoryPolicyStore())
    registry.publish(PolicyVersion(version=0, source_record_ids=["used1"]))
    registry.publish(PolicyVersion(version=0, source_record_ids=["used2"]))
    registry.publish(PolicyVersion(version=0, source_record_ids=["later"]))
    records = [record("used1"), record("used2"), record("later"), record("fail", rebuild="fail"),
               record("unk", rebuild="unknown"), record("new", created="z")]
    versions = registry.list_versions()
    ids = [r["incident_id"] for r in held_out_records(records, versions, 2)]
    assert ids == ["new", "later"]  # v3's sources were not used to build v2


# ---- run_replay ------------------------------------------------------------------

def test_replay_compares_policy_against_no_policy():
    store, registry, docs = world()
    result = run_replay(1, store=store, registry=registry, incident_loader=docs.get,
                        remediation_fn=fake_remediation, llm_call=fake_judge)
    assert [i.incident_id for i in result.items] == ["h2", "h1"]
    for item in result.items:
        assert item.guidance_applied
        assert (item.score_with_policy, item.score_without_policy, item.delta) == (90, 60, 30)
    s = result.summary
    assert (s.incidents, s.guidance_applied, s.mean_with_policy, s.mean_without_policy) == (2, 2, 90.0, 60.0)
    assert (s.wins, s.ties, s.losses) == (2, 0, 0)


def test_replay_does_not_change_the_active_policy_or_leak_the_override():
    store, registry, docs = world()
    run_replay(1, store=store, registry=registry, incident_loader=docs.get,
               remediation_fn=fake_remediation, llm_call=fake_judge)
    set_registry(registry)
    assert get_guidance({"diagnosis_report": docs["h1"]["diagnosis_report"], "incident_type": "test_failure"})[1] == 1
    with policy_override(None):
        assert get_guidance({"diagnosis_report": docs["h1"]["diagnosis_report"]}) == ("", None)


def test_replay_tests_the_requested_version_not_the_active_one():
    store, registry, docs = world()
    registry.publish(PolicyVersion(version=0))  # v2: active, but has no guidance at all
    result = run_replay(1, store=store, registry=registry, incident_loader=docs.get,
                        remediation_fn=fake_remediation, llm_call=fake_judge)
    assert result.summary.guidance_applied == 2


def test_incident_in_another_category_gets_no_guidance_and_ties():
    store, registry, docs = world(record_ids=("h1",))
    docs["h1"]["diagnosis_report"] = "Root cause: ModuleNotFoundError, update requirements.txt dependency"
    result = run_replay(1, store=store, registry=registry, incident_loader=docs.get,
                        remediation_fn=fake_remediation, llm_call=fake_judge)
    item = result.items[0]
    assert not item.guidance_applied and item.delta == 0
    assert result.summary.guidance_applied == 0 and result.summary.ties == 0  # ties only count guided ones
    assert "identical prompts" in format_report(result)


def test_training_incidents_are_not_replayed():
    store, registry, docs = world(sources=("h1",))
    result = run_replay(1, store=store, registry=registry, incident_loader=docs.get,
                        remediation_fn=fake_remediation, llm_call=fake_judge)
    assert [i.incident_id for i in result.items] == ["h2"]


def test_limit_applies_after_ordering_newest_first():
    store, registry, docs = world(record_ids=("h1", "h2", "h3"))
    result = run_replay(1, store=store, registry=registry, incident_loader=docs.get,
                        remediation_fn=fake_remediation, llm_call=fake_judge, limit=1)
    assert [i.incident_id for i in result.items] == ["h3"]


def test_incidents_without_reports_or_resolution_are_skipped_with_a_reason():
    store, registry, docs = world(record_ids=("ok", "noreports", "nores", "gone"))
    docs["noreports"]["diagnosis_report"] = ""
    docs["nores"].update(solved_step_id=None, fix_result=None)
    del docs["gone"]
    result = run_replay(1, store=store, registry=registry, incident_loader=docs.get,
                        remediation_fn=fake_remediation, llm_call=fake_judge)
    assert [i.incident_id for i in result.items] == ["ok"]
    assert len(result.skipped) == 3
    assert any("no recorded resolution" in s for s in result.skipped)


def test_one_failing_incident_does_not_stop_the_replay():
    store, registry, docs = world()

    def flaky(state):
        if state["incident_id"] == "h2":
            raise RuntimeError("model down")
        return fake_remediation(state)

    result = run_replay(1, store=store, registry=registry, incident_loader=docs.get,
                        remediation_fn=flaky, llm_call=fake_judge)
    by_id = {i.incident_id: i for i in result.items}
    assert "model down" in by_id["h2"].error and by_id["h2"].delta is None
    assert by_id["h1"].delta == 30
    assert result.summary.mean_with_policy == 90.0  # errored item excluded from means


def test_unparseable_judge_output_leaves_scores_empty():
    store, registry, docs = world(record_ids=("h1",))
    result = run_replay(1, store=store, registry=registry, incident_loader=docs.get,
                        remediation_fn=fake_remediation, llm_call=lambda s, h: "no")
    assert result.items[0].score_with_policy is None and result.summary.mean_with_policy is None


def test_unknown_version_raises():
    store, registry, docs = world()
    with pytest.raises(ValueError):
        run_replay(9, store=store, registry=registry, incident_loader=docs.get,
                   remediation_fn=fake_remediation, llm_call=fake_judge)


def test_replay_writes_nothing(monkeypatch):
    store, registry, docs = world()
    before = {r["incident_id"]: dict(r) for r in store.list()}
    run_replay(1, store=store, registry=registry, incident_loader=docs.get,
               remediation_fn=fake_remediation, llm_call=fake_judge)
    assert {r["incident_id"]: r for r in store.list()} == before
    assert len(registry.list_versions()) == 1


def test_judge_prompt_contains_the_known_resolution_and_new_steps():
    seen = []
    store, registry, docs = world(record_ids=("h1",))
    run_replay(1, store=store, registry=registry, incident_loader=docs.get, remediation_fn=fake_remediation,
               llm_call=lambda s, h: seen.append(h) or '{"fix_quality": 50}')
    assert "<incident>" in seen[0]
    assert "Fix the add function in calc.py" in seen[0] and "guided fix" in seen[0]
    assert "plain fix" in seen[1]


def test_end_to_end_with_the_real_remediation_agent(monkeypatch):
    """Real run_remediation (fake model): the with-policy prompt carries the
    guidance, the without-policy prompt does not."""
    sent = []

    class Chat(FakeChat):
        def invoke(self, messages):
            sent.append(messages[1][1])
            return super().invoke(messages)

    monkeypatch.setattr(remediation, "ChatGroq", Chat)
    store, registry, docs = world(record_ids=("h1",))
    result = run_replay(1, store=store, registry=registry, incident_loader=docs.get,
                        llm_call=lambda s, h: '{"fix_quality": 70}')
    assert result.items[0].guidance_applied
    assert "Learned guidance from past incidents (advisory)" in sent[0]
    assert "Learned guidance from past incidents (advisory)" not in sent[1]


def test_format_report_is_readable():
    store, registry, docs = world()
    result = run_replay(1, store=store, registry=registry, incident_loader=docs.get,
                        remediation_fn=fake_remediation, llm_call=fake_judge)
    text = format_report(result)
    assert text.startswith("Replay of policy version 1 against no policy")
    assert "+30" in text and "2 better, 0 equal, 0 worse" in text
    empty = format_report(run_replay(1, store=InMemoryRecordStore(), registry=registry,
                                     incident_loader=docs.get, remediation_fn=fake_remediation, llm_call=fake_judge))
    assert "No held-out resolved incidents" in empty


def test_cli_needs_mongodb(capsys):
    assert replay.main([]) == 1
    assert "MONGODB_URI" in capsys.readouterr().err
