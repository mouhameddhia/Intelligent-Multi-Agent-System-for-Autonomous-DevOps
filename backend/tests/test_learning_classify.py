from agent_system.learning.classify import (
    classify_failure,
    classify_fix_action,
    failure_signature,
    files_touched,
)


def test_classify_failure_by_keywords():
    assert classify_failure("AssertionError: expected 4 got 5") == "test_assertion"
    assert classify_failure("ModuleNotFoundError: dependency missing, update requirements.txt") == "dependency"
    assert classify_failure("The agent hit a timeout and ran out of memory") == "infra_flaky"
    assert classify_failure("SyntaxError during compilation") == "build_compile"


def test_classify_failure_falls_back_to_other():
    assert classify_failure("") == "other"
    assert classify_failure("nothing recognisable here") == "other"


def test_incident_type_hint_breaks_a_tie():
    text = "timeout while a unit test ran"
    assert classify_failure(text) == "infra_flaky"
    assert classify_failure(text, "test_failure") == "test_assertion"


def test_words_match_on_boundaries():
    # "oom" must not match inside "room"
    assert classify_failure("there is plenty of room") == "other"


def test_files_touched_is_sorted_lowercase_and_deduped():
    evidence = {"github": {"files": [{"filename": "B.py"}, {"filename": "a.py"}, {"filename": "b.py"}, {}]}}
    assert files_touched(evidence) == ["a.py", "b.py"]
    assert files_touched(None) == []


def test_signature_is_stable_and_order_independent():
    a = failure_signature("dependency", "x", ["a.py", "b.py"])
    assert a == failure_signature("dependency", "x", ["b.py", "a.py"])
    assert a != failure_signature("config", "x", ["a.py", "b.py"])
    assert len(a) == 16


def test_fix_action_applied_fix_is_code_patch():
    assert classify_fix_action(["Restart the agent"], {"applied": True}) == "code_patch"


def test_fix_action_from_steps():
    assert classify_fix_action(["Roll back the last commit"], None) == "rollback"
    assert classify_fix_action(["Restart the service"], {"applied": False}) == "restart"
    assert classify_fix_action(["Upgrade the dependency"], None) == "dependency_update"
    assert classify_fix_action(["Rerun the pipeline"], None) == "rerun"
    assert classify_fix_action([], None) == "other"
