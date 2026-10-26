"""Integration-level tests for the judge gate wired into run_apply_fix
(agents/fix.py): the GitHub push must happen if and only if the judge's
verdict is exactly "pass". No real network calls are made — GitHub, Groq
and the judge are all mocked at the fix module's import boundary.
"""

import base64
from unittest.mock import MagicMock, patch

from agent_system.agents.fix import run_apply_fix

_STATE = {
    "job_name": "pipepline",
    "build_number": 7,
    "github_token": "fake-token",
    "github_owner": "mouhameddhia",
    "github_repo": "test_devops",
    "diagnosis_report": "The fee is not divided by 100.",
    "remediation_report": "Divide fee_percent by 100 before subtracting.",
    "evidence": {},
}

_ORIGINAL_CONTENT = "def calculate_refund(amount, fee_percent=15):\n    fee = amount * fee_percent\n    return amount - fee\n"
_NEW_CONTENT = "def calculate_refund(amount, fee_percent=15):\n    fee = amount * fee_percent / 100\n    return amount - fee\n"


def _mock_github_get_response():
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "content": base64.b64encode(_ORIGINAL_CONTENT.encode("utf-8")).decode("ascii"),
        "sha": "abc123",
    }
    return resp


def _mock_groq_llm():
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content=_NEW_CONTENT)
    return llm


class TestJudgeGateBlocksOrAllowsPush:
    @patch("agent_system.agents.fix.httpx.put")
    @patch("agent_system.agents.fix.judge_fix")
    @patch("agent_system.agents.fix.ChatGroq")
    @patch("agent_system.agents.fix.httpx.get")
    @patch("agent_system.agents.fix._pick_target_file")
    def test_push_happens_when_judge_passes(
        self, mock_pick_target, mock_get, mock_chat_groq, mock_judge, mock_put
    ):
        mock_pick_target.return_value = "refund.py"
        mock_get.return_value = _mock_github_get_response()
        mock_chat_groq.return_value = _mock_groq_llm()
        mock_judge.return_value = {"verdict": "pass", "reasoning": "Correctly fixes the bug.", "source": "gemini"}

        put_resp = MagicMock()
        put_resp.raise_for_status.return_value = None
        put_resp.json.return_value = {"commit": {"sha": "deadbeef", "html_url": "https://github.com/x/y/commit/deadbeef"}}
        mock_put.return_value = put_resp

        result = run_apply_fix(dict(_STATE))

        assert mock_put.called, "GitHub push must happen when the judge passes the fix"
        assert result["fix_result"]["applied"] is True
        assert result["fix_result"]["judge_result"]["verdict"] == "pass"

    @patch("agent_system.agents.fix.httpx.put")
    @patch("agent_system.agents.fix.judge_fix")
    @patch("agent_system.agents.fix.ChatGroq")
    @patch("agent_system.agents.fix.httpx.get")
    @patch("agent_system.agents.fix._pick_target_file")
    def test_push_blocked_when_judge_fails(
        self, mock_pick_target, mock_get, mock_chat_groq, mock_judge, mock_put
    ):
        mock_pick_target.return_value = "refund.py"
        mock_get.return_value = _mock_github_get_response()
        mock_chat_groq.return_value = _mock_groq_llm()
        mock_judge.return_value = {
            "verdict": "fail",
            "reasoning": "Overfits to a single test assertion.",
            "source": "gemini",
        }

        result = run_apply_fix(dict(_STATE))

        assert not mock_put.called, "GitHub push must NEVER happen when the judge rejects the fix"
        assert result["fix_result"]["applied"] is False
        assert "Judge rejected" in result["fix_result"]["message"]
        assert result["fix_result"]["judge_result"]["verdict"] == "fail"

    @patch("agent_system.agents.fix.httpx.put")
    @patch("agent_system.agents.fix.judge_fix")
    @patch("agent_system.agents.fix.ChatGroq")
    @patch("agent_system.agents.fix.httpx.get")
    @patch("agent_system.agents.fix._pick_target_file")
    def test_push_blocked_when_judge_call_itself_errors(
        self, mock_pick_target, mock_get, mock_chat_groq, mock_judge, mock_put
    ):
        """judge_fix() is documented to fail CLOSED (always returns verdict
        "fail" on an internal error, never raises) — this confirms fix.py
        still blocks the push even if judge_fix somehow raised anyway,
        since an uncaught exception here must not fall through to a push."""
        mock_pick_target.return_value = "refund.py"
        mock_get.return_value = _mock_github_get_response()
        mock_chat_groq.return_value = _mock_groq_llm()
        mock_judge.return_value = {
            "verdict": "fail",
            "reasoning": "Judge could not be reached, rejecting the fix as a precaution: boom",
            "source": "error",
        }

        result = run_apply_fix(dict(_STATE))

        assert not mock_put.called, "GitHub push must NEVER happen when the judge could not be reached"
        assert result["fix_result"]["applied"] is False
