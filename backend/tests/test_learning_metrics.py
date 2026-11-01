import pytest

from agent_system.learning.metrics import compute_metrics


def rec(decision=None, applied=None, rebuild="unknown", version=None):
    return {
        "incident_id": "x", "policy_version_used": version, "rebuild_result": rebuild,
        "fix_applied": applied, "human_decision": {"decision": decision},
    }


def by_version(metrics):
    return {row["policy_version_used"]: row for row in metrics["by_policy_version"]}


def test_empty_input():
    metrics = compute_metrics([])
    assert metrics["by_policy_version"] == []
    assert metrics["overall"]["incidents"] == 0
    assert metrics["overall"]["approval_rate"] is None


def test_rates_and_counts_for_one_group():
    records = [
        rec("approve", True, "pass"), rec("approve", True, "fail"), rec("approve", True, "pass"),
        rec("approve", False, "unknown"), rec("deny", False, "unknown"),
    ]
    row = by_version(compute_metrics(records))[None]
    assert row["incidents"] == 5
    assert (row["approved"], row["denied"], row["approval_rate"]) == (4, 1, 0.8)
    assert (row["rebuild_known"], row["rebuild_passed"], row["rebuild_pass_rate"]) == (3, 2, 0.6667)
    assert (row["fixes_applied"], row["first_fix_known"], row["first_fix_succeeded"]) == (3, 3, 2)
    assert row["first_fix_success_rate"] == 0.6667


def test_first_fix_ignores_manual_resolutions_but_rebuild_rate_counts_them():
    records = [rec("approve", False, "pass"), rec("approve", True, "pass"), rec("approve", True, "fail")]
    row = by_version(compute_metrics(records))[None]
    assert row["rebuild_pass_rate"] == 0.6667  # all three known
    assert row["first_fix_success_rate"] == 0.5  # only the two applied fixes


def test_applied_fix_with_unknown_rebuild_is_not_in_the_first_fix_denominator():
    row = by_version(compute_metrics([rec("approve", True, "unknown")]))[None]
    assert row["fixes_applied"] == 1 and row["first_fix_known"] == 0
    assert row["first_fix_success_rate"] is None


def test_grouped_per_policy_version_with_no_policy_first():
    records = [
        rec("approve", True, "fail"), rec("approve", True, "pass", version=1),
        rec("approve", True, "pass", version=2), rec("deny", False, "unknown", version=2),
    ]
    metrics = compute_metrics(records)
    assert [row["policy_version_used"] for row in metrics["by_policy_version"]] == [None, 1, 2]
    rows = by_version(metrics)
    assert rows[None]["rebuild_pass_rate"] == 0.0
    assert rows[1]["rebuild_pass_rate"] == 1.0
    assert rows[2]["approval_rate"] == 0.5
    assert metrics["overall"]["incidents"] == 4


def test_non_integer_versions_group_as_no_policy():
    assert list(by_version(compute_metrics([rec(version="3")]))) == [None]


@pytest.mark.parametrize("missing", [{}, {"human_decision": None}])
def test_sparse_records_do_not_crash(missing):
    assert compute_metrics([missing])["overall"]["incidents"] == 1
