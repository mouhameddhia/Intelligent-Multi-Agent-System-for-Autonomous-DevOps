"""Unit tests for the shared Groq 429 retry wrapper (agents/llm_retry.py),
used by every agent that calls the LLM (Monitoring, Diagnosis, Remediation,
Fix, and the legacy steps.py fallback)."""

from unittest.mock import MagicMock, patch

import pytest

from agent_system.agents.llm_retry import invoke_with_retry


class TestInvokeWithRetry:
    def test_succeeds_immediately_with_no_retry(self):
        call = MagicMock(return_value="ok")
        with patch("agent_system.agents.llm_retry.time.sleep") as mock_sleep:
            result = invoke_with_retry(call)
        assert result == "ok"
        assert call.call_count == 1
        assert not mock_sleep.called

    def test_retries_on_429_then_succeeds(self):
        call = MagicMock(side_effect=[
            Exception("Error code: 429 - rate_limit_exceeded, try again in 2.5s"),
            "ok",
        ])
        with patch("agent_system.agents.llm_retry.time.sleep") as mock_sleep:
            result = invoke_with_retry(call)
        assert result == "ok"
        assert call.call_count == 2
        mock_sleep.assert_called_once()
        assert mock_sleep.call_args[0][0] == pytest.approx(3.5)

    def test_raises_immediately_on_non_rate_limit_error(self):
        """A non-429 error must not be retried — only rate limits are."""
        call = MagicMock(side_effect=RuntimeError("some other failure"))
        with patch("agent_system.agents.llm_retry.time.sleep") as mock_sleep:
            with pytest.raises(RuntimeError, match="some other failure"):
                invoke_with_retry(call)
        assert call.call_count == 1
        assert not mock_sleep.called

    def test_raises_after_exhausting_max_attempts(self):
        call = MagicMock(side_effect=Exception("429 rate_limit_exceeded"))
        with patch("agent_system.agents.llm_retry.time.sleep"):
            with pytest.raises(Exception, match="429"):
                invoke_with_retry(call, max_attempts=3)
        assert call.call_count == 3

    def test_default_wait_when_no_duration_in_message(self):
        call = MagicMock(side_effect=[
            Exception("429 rate_limit_exceeded"),
            "ok",
        ])
        with patch("agent_system.agents.llm_retry.time.sleep") as mock_sleep:
            invoke_with_retry(call)
        mock_sleep.assert_called_once_with(10.0)
