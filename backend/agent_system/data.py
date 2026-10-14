# Synthetic data organised by agent type.
# Each section mirrors what that agent would receive from real tools in production.
# Monitoring  → live data    (what Jenkins + Prometheus would provide right now)
# Diagnosis   → live + historical data  (commits, build history, recurring patterns)
# Remediation → knowledge base data     (past fixes, runbooks, team contacts)

# ---------------------------------------------------------------------------
# LIVE DATA  —  used by the Monitoring Agent
# ---------------------------------------------------------------------------

LIVE_BUILD = {
    "name": "payment-service",
    "build_number": 42,
    "result": "FAILURE",
    "failed_stage": "Run Tests",
    "duration_seconds": 202,
    "url": "http://jenkins.example.com/job/payment-service/42/",
    "timestamp": "2026-10-12T14:32:00Z",
    "branch": "main",
    "triggered_by": "push",
}

LIVE_CONSOLE_LOG = """\
Started by user admin
[Pipeline] Start of Pipeline

[Pipeline] stage: Checkout
Cloning repository https://github.com/example-org/payment-service.git
Checked out commit abc1234

[Pipeline] stage: Install Dependencies
+ pip install -r requirements.txt
Successfully installed all packages.

[Pipeline] stage: Run Tests
+ pytest tests/ -v
collected 47 items

tests/test_auth.py::test_login_valid            PASSED
tests/test_auth.py::test_login_invalid          PASSED
tests/test_payment.py::test_charge_valid        PASSED
tests/test_payment.py::test_refund_standard     FAILED
tests/test_payment.py::test_refund_partial      FAILED
tests/test_payment.py::test_currency_conversion FAILED

FAILURES:
tests/test_payment.py::test_refund_standard
  AssertionError: expected refund amount 100.0, got 85.0

tests/test_payment.py::test_refund_partial
  AssertionError: expected partial refund 50.0, got 42.5

tests/test_payment.py::test_currency_conversion
  AssertionError: conversion rate mismatch for USD -> EUR

44 passed, 3 failed
ERROR: 3 tests failed

[Pipeline] End of Pipeline
Build FAILED
"""

LIVE_SYSTEM_METRICS = {
    "collected_at": "2026-10-12T14:35:00Z",
    "note": "Metrics captured from Prometheus at time of failure",
    "cpu_usage_percent": 34,
    "memory_usage_percent": 61,
    "disk_io_mbps": 12,
    "active_threads": 8,
    "open_db_connections": 5,
    "verdict": "System resources were healthy during the build — infrastructure is not the cause.",
}

# ---------------------------------------------------------------------------
# HISTORICAL DATA  —  used by the Diagnosis Agent
# ---------------------------------------------------------------------------

RECENT_COMMITS = [
    {
        "sha": "abc1234",
        "author": "Ahmed Dhia",
        "date": "2026-10-12T12:10:00Z",
        "message": "refactor: update refund calculation logic",
        "files_changed": ["src/payments/refund.py", "src/payments/currency.py"],
    },
    {
        "sha": "def5678",
        "author": "Ahmed Dhia",
        "date": "2026-10-11T09:45:00Z",
        "message": "feat: add partial refund support",
        "files_changed": ["src/payments/refund.py", "tests/test_payment.py"],
    },
    {
        "sha": "ghi9012",
        "author": "Yasmine Ben Ali",
        "date": "2026-10-10T16:20:00Z",
        "message": "chore: update README",
        "files_changed": ["README.md"],
    },
]

BUILD_HISTORY = [
    {"build_number": 41, "result": "SUCCESS", "duration_seconds": 195, "date": "2026-10-11T18:00:00Z", "commit": "def5678"},
    {"build_number": 40, "result": "SUCCESS", "duration_seconds": 188, "date": "2026-10-11T10:00:00Z", "commit": "def5678"},
    {"build_number": 39, "result": "FAILURE", "duration_seconds": 201, "date": "2026-10-09T14:00:00Z", "commit": "xyz0001", "failed_stage": "Run Tests"},
    {"build_number": 38, "result": "SUCCESS", "duration_seconds": 193, "date": "2026-10-08T11:00:00Z", "commit": "xyz0001"},
    {"build_number": 37, "result": "SUCCESS", "duration_seconds": 190, "date": "2026-10-07T09:00:00Z", "commit": "abc9999"},
]

FAILURE_PATTERNS = [
    {
        "pattern": "test_payment.py assertion failures",
        "occurrences_last_30_days": 3,
        "last_seen": "2026-10-12T14:32:00Z",
        "common_cause": "Refund calculation logic changes without updating test expectations",
        "avg_resolution_hours": 1.5,
    },
    {
        "pattern": "Install Dependencies timeout",
        "occurrences_last_30_days": 1,
        "last_seen": "2026-10-01T08:00:00Z",
        "common_cause": "PyPI network timeout in CI environment",
        "avg_resolution_hours": 0.25,
    },
]

# ---------------------------------------------------------------------------
# KNOWLEDGE BASE  —  used by the Remediation Agent
# ---------------------------------------------------------------------------

PAST_FIXES = [
    {
        "incident_id": "INC-2026-089",
        "date": "2026-10-09",
        "failure_type": "test_payment.py assertion failure",
        "root_cause": "Refund rounding logic changed without updating tests",
        "fix_applied": "Reverted commit xyz0001. Re-implemented rounding with updated tests.",
        "resolution_time_hours": 1.0,
        "prevented_recurrence": False,
        "lesson_learned": "Refund calculation changes must include test updates in the same commit.",
    },
    {
        "incident_id": "INC-2026-041",
        "date": "2026-08-15",
        "failure_type": "Currency conversion mismatch",
        "root_cause": "Hardcoded exchange rate not updated after bank API change",
        "fix_applied": "Replaced hardcoded rate with live API call. Added rate validation test.",
        "resolution_time_hours": 3.5,
        "prevented_recurrence": True,
        "lesson_learned": "Never hardcode exchange rates. Always fetch from a trusted source.",
    },
]

RUNBOOKS = [
    {
        "title": "Handling test_payment.py failures",
        "applies_to": "AssertionError in payment tests",
        "steps": [
            "1. Identify the failing assertions in the console log.",
            "2. Find the most recent commit that touched refund.py or currency.py.",
            "3. Compare expected vs actual values to understand the logic change.",
            "4. If the change is unintentional: git revert <sha> and re-run the pipeline.",
            "5. If the change is intentional: update test expectations and get QA sign-off.",
            "6. Ensure the fix is reviewed by the payment team lead before merging.",
        ],
        "escalation": "If unresolved in 2 hours, escalate to payment team lead.",
    },
    {
        "title": "General pipeline failure response",
        "applies_to": "Any FAILURE build",
        "steps": [
            "1. Check the failed stage in the console log.",
            "2. Run the failing tests locally to reproduce.",
            "3. Check recent commits on the affected files.",
            "4. Apply fix on a feature branch, not directly on main.",
            "5. Ensure CI passes on the branch before merging.",
        ],
        "escalation": "If the build has been broken for more than 4 hours, notify the team lead.",
    },
]

TEAM_CONTACTS = {
    "payment_team_lead": {"name": "Yasmine Ben Ali", "email": "yasmine@example.com", "on_call": True},
    "devops_engineer": {"name": "Karim Mansour", "email": "karim@example.com", "on_call": False},
    "qa_lead": {"name": "Sara Trabelsi", "email": "sara@example.com", "on_call": False},
}
