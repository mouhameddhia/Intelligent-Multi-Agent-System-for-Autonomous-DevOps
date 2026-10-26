"""Confirms the existing human-approval gate still guards apply_fix and was
not bypassed while wiring in the judge gate: apply_fix is only reachable
when approval_status == "approve"."""

from langgraph.graph import END

from agent_system.orchestrator.graph import _route_after_approval


def test_approve_routes_to_apply_fix():
    assert _route_after_approval({"approval_status": "approve"}) == "apply_fix"


def test_deny_routes_to_end():
    assert _route_after_approval({"approval_status": "deny"}) == END


def test_missing_approval_status_routes_to_end():
    assert _route_after_approval({}) == END
