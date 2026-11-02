"""Phase 4: learned guidance reaches the Remediation prompt, and with no usable
policy the prompts are byte-for-byte what they were before the learning layer
(golden/remediation_prompts.json was captured from the pre-learning code)."""

import json
import pathlib

import pytest

from agent_system.agents import remediation
from agent_system.config import settings
from agent_system.learning import injection
from agent_system.learning.injection import SYSTEM_RULE
from agent_system.learning.models import PolicyVersion, PriorCount, Rule
from agent_system.learning.registry import (
    InMemoryPolicyStore,
    PolicyRegistry,
    get_active_policy_cached,
    invalidate_policy_cache,
    set_registry,
)
from agent_system.learning.render import SECTION_TITLE

from remediation_scenarios import SCENARIOS, run_scenario

GOLDEN = json.loads((pathlib.Path(__file__).parent / "golden" / "remediation_prompts.json").read_text(encoding="utf-8"))

RULE_TEXT = "Fix the function under test and rerun only the failing unit test first"
PRIOR_KEY = "test_assertion|code_patch"


def rule(rid="r1", text=RULE_TEXT, category="test_assertion", status="active"):
    return Rule(id=rid, text=text, failure_category=category, status=status, support_count=3, success_count=3)


def install(rules=None, priors=None):
    registry = PolicyRegistry(InMemoryPolicyStore())
    published = registry.publish(PolicyVersion(
        version=0,
        rules=rules if rules is not None else [rule()],
        priors=priors if priors is not None else {PRIOR_KEY: PriorCount(success=4, failure=1)},
    ))
    set_registry(registry)
    return registry, published


def assert_matches_golden(name):
    messages, result = run_scenario(SCENARIOS[name])
    assert messages == GOLDEN[name]["messages"]
    assert json.loads(json.dumps(result)) == GOLDEN[name]["result"]


# ---- empty / unusable policy: identical to today ------------------------------

@pytest.mark.parametrize("name", list(SCENARIOS))
def test_empty_registry_leaves_prompts_and_state_unchanged(name):
    assert_matches_golden(name)


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_policy_for_another_category_changes_nothing(name):
    install([rule(category="dependency")], {"dependency|rerun": PriorCount(success=2)})
    assert_matches_golden(name)


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_candidate_only_policy_without_priors_changes_nothing(name):
    install([rule(status="candidate")], {})
    assert_matches_golden(name)


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_disabled_flag_changes_nothing(name, monkeypatch):
    install()
    monkeypatch.setattr(settings, "learning_enabled", False)
    assert_matches_golden(name)


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_registry_failure_falls_back_silently(name, monkeypatch):
    class Broken(InMemoryPolicyStore):
        def load_versions(self, scope):
            raise RuntimeError("mongo down")

    set_registry(PolicyRegistry(Broken()))
    assert_matches_golden(name)


@pytest.mark.parametrize("failing", ["render_guidance", "classify_failure"])
def test_internal_failures_fall_back_silently(failing, monkeypatch):
    install()

    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(injection, failing, boom)
    assert_matches_golden("evidence")


# ---- guidance present -----------------------------------------------------------

def test_guidance_is_appended_to_the_human_message_only():
    _, published = install()
    messages, result = run_scenario(SCENARIOS["evidence"])
    (_, system), (_, human) = messages
    golden_system, golden_human = (m[1] for m in GOLDEN["evidence"]["messages"])

    assert human.startswith(golden_human)
    appended = human[len(golden_human):]
    assert appended.startswith("\n\n" + SECTION_TITLE)
    assert RULE_TEXT in appended
    assert "code_patch: 4 succeeded, 1 failed (n=5)" in appended
    assert result["policy_version_used"] == published.version == 1

    # the system prompt differs by exactly one added rule
    expected_system = golden_system.replace("supplied.\n\n- Write", "supplied.\n" + SYSTEM_RULE + "\n- Write")
    assert expected_system != golden_system
    assert system == expected_system


def test_guidance_comes_after_historical_context():
    install()
    (_, _), (_, human) = run_scenario(SCENARIOS["evidence_with_history"])[0]
    assert human.index("HISTORICAL CONTEXT") < human.index(SECTION_TITLE)


def test_legacy_path_also_gets_guidance():
    install()
    messages, result = run_scenario(SCENARIOS["legacy"])
    golden_system, golden_human = (m[1] for m in GOLDEN["legacy"]["messages"])
    assert messages[1][1].startswith(golden_human) and SECTION_TITLE in messages[1][1]
    assert messages[0][1] == golden_system + remediation._LEGACY_GUIDANCE_NOTE
    assert result["policy_version_used"] == 1


def test_non_guidance_state_is_otherwise_untouched():
    install()
    _, result = run_scenario(SCENARIOS["evidence"])
    golden = dict(GOLDEN["evidence"]["result"])
    result = json.loads(json.dumps(result))
    assert result.pop("policy_version_used") == 1
    assert result == golden


def test_only_active_rules_of_the_diagnosed_category_are_injected():
    install([
        rule("a", "Active rule for the diagnosed category"),
        rule("b", "Candidate rule that must stay out", status="candidate"),
        rule("c", "Retired rule that must stay out", status="retired"),
        rule("d", "Dependency rule from another category", category="dependency"),
    ])
    (_, _), (_, human) = run_scenario(SCENARIOS["evidence"])[0]
    assert "Active rule for the diagnosed category" in human
    for text in ("must stay out", "another category"):
        assert text not in human


def test_injected_section_respects_the_token_budget():
    from agent_system.learning.render import MAX_RENDER_TOKENS, estimate_tokens

    install([rule(f"r{i}", f"Lesson {i}: " + "y" * 280) for i in range(30)])
    (_, _), (_, human) = run_scenario(SCENARIOS["evidence"])[0]
    golden_human = GOLDEN["evidence"]["messages"][1][1]
    assert estimate_tokens(human[len(golden_human):]) <= MAX_RENDER_TOKENS + 2


def test_rollback_changes_the_next_prompt():
    registry, _ = install([rule("a", "Lesson from the first policy version")])
    registry.publish(PolicyVersion(version=0, rules=[rule("b", "Lesson from the second policy version")]))

    (_, _), (_, human) = run_scenario(SCENARIOS["evidence"])[0]
    assert "second policy version" in human and "first policy version" not in human

    registry.rollback()
    messages, result = run_scenario(SCENARIOS["evidence"])
    assert "first policy version" in messages[1][1] and "second policy version" not in messages[1][1]
    assert result["policy_version_used"] == 1


def test_guidance_never_reaches_the_fix_or_approval_nodes():
    import inspect

    from agent_system.agents import fix
    from agent_system.orchestrator import graph

    for module in (fix, graph):
        assert "learning" not in inspect.getsource(module)


# ---- policy cache ------------------------------------------------------------

class CountingStore(InMemoryPolicyStore):
    def __init__(self):
        super().__init__()
        self.loads = 0

    def load_versions(self, scope):
        self.loads += 1
        return super().load_versions(scope)


def test_policy_lookup_is_cached_within_the_ttl():
    store = CountingStore()
    registry = PolicyRegistry(store)
    registry.publish(PolicyVersion(version=0))
    set_registry(registry)
    store.loads = 0
    clock = iter([0.0, 10.0, 59.0, 61.0, 61.0]).__next__

    assert get_active_policy_cached(clock=clock).version == 1   # t=0 miss
    assert get_active_policy_cached(clock=clock).version == 1   # t=10 hit
    assert get_active_policy_cached(clock=clock).version == 1   # t=59 hit
    assert store.loads == 1
    get_active_policy_cached(clock=clock)                       # t=61 expired
    assert store.loads == 2


def test_failures_are_cached_so_a_dead_database_costs_one_lookup():
    class Broken(InMemoryPolicyStore):
        loads = 0

        def load_versions(self, scope):
            Broken.loads += 1
            raise RuntimeError("mongo down")

    set_registry(PolicyRegistry(Broken()))
    for _ in range(5):
        assert get_active_policy_cached() is None
    assert Broken.loads == 1


def test_publish_and_rollback_invalidate_the_cache():
    registry, _ = install()
    assert get_active_policy_cached().version == 1
    registry.publish(PolicyVersion(version=0))
    assert get_active_policy_cached().version == 2
    registry.rollback()
    assert get_active_policy_cached().version == 1
    invalidate_policy_cache()


# ---- acceptance: closed incidents -> policy -> next prompt ----------------------

def test_five_closed_incidents_reach_the_next_remediation_prompt():
    from agent_system.learning.evaluator import LlmCall  # noqa: F401  (import check)
    from agent_system.learning.recorder import record_incident_closed
    from agent_system.learning.store import InMemoryRecordStore, set_store
    from agent_system.learning.trigger import run_learning_pipeline

    from learning_fixtures import approved, with_feedback

    set_store(InMemoryRecordStore())
    set_registry(PolicyRegistry(InMemoryPolicyStore()))
    docs = {f"inc-{i}": with_feedback(approved(f"inc-{i}"), True) for i in range(5)}
    for incident_id in docs:
        record_incident_closed(incident_id, loader=docs.get)

    # before learning: prompt unchanged
    assert_matches_golden("evidence")

    result = run_learning_pipeline(
        eval_llm=lambda s, h: '{"diagnosis_quality": 80, "fix_quality": 80, "rationale": "ok"}',
        learn_llm=lambda s, h: json.dumps({"operations": [{
            "op": "ADD", "text": RULE_TEXT, "failure_category": "test_assertion",
            "evidence_incident_ids": ["inc-0", "inc-1"],
        }]}),
    )
    assert result.status == "published"

    messages, state = run_scenario(SCENARIOS["evidence"])
    assert SECTION_TITLE in messages[1][1] and RULE_TEXT in messages[1][1]
    assert "code_patch: 5 succeeded, 0 failed (n=5)" in messages[1][1]
    assert state["policy_version_used"] == 1
