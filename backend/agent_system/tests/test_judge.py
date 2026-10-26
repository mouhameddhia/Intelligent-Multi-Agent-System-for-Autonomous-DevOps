"""Unit tests for the independent Gemini judge gate (agents/judge.py).

Covers: marker parsing, the fail-closed contract on API errors, and the
fail-closed contract on an unparseable response. No real network calls are
made — _call_gemini is patched everywhere a verdict value matters.
"""

from unittest.mock import patch

from agent_system.agents.judge import extract_judge_verdict, judge_fix

_COMMON_KWARGS = dict(
    target_file="refund.py",
    original_content="def calculate_refund(amount, fee_percent=15):\n    fee = amount * fee_percent\n    return amount - fee\n",
    new_content="def calculate_refund(amount, fee_percent=15):\n    fee = amount * fee_percent / 100\n    return amount - fee\n",
    test_filename="test_refund.py",
    test_content="def test_standard_refund():\n    assert calculate_refund(100) == 85.0\n",
    diagnosis_report="The fee is not divided by 100.",
    remediation_report="Divide fee_percent by 100 before subtracting.",
)


class TestExtractJudgeVerdict:
    def test_plain_pass(self):
        reasoning, verdict = extract_judge_verdict("JUDGE_VERDICT: pass\nLooks correct.")
        assert verdict == "pass"
        assert "Looks correct." in reasoning

    def test_plain_fail(self):
        reasoning, verdict = extract_judge_verdict("JUDGE_VERDICT: fail\nOverfits to one test.")
        assert verdict == "fail"
        assert "Overfits" in reasoning

    def test_markdown_bold_marker(self):
        _, verdict = extract_judge_verdict("**JUDGE_VERDICT:** pass\nFine.")
        assert verdict == "pass"

    def test_case_insensitive(self):
        _, verdict = extract_judge_verdict("judge_verdict: FAIL\nbad diff")
        assert verdict == "fail"

    def test_missing_marker_returns_none(self):
        reasoning, verdict = extract_judge_verdict("I think this fix is probably fine.")
        assert verdict is None
        assert "probably fine" in reasoning

    def test_empty_text_returns_none(self):
        _, verdict = extract_judge_verdict("")
        assert verdict is None

    def test_last_marker_wins(self):
        # Defensive: if the model repeats the marker, the later one governs,
        # matching confidence.py's documented behavior for its own marker.
        _, verdict = extract_judge_verdict(
            "JUDGE_VERDICT: pass\n...\nJUDGE_VERDICT: fail\nActually no."
        )
        assert verdict == "fail"


class TestJudgeFixFailsClosed:
    def test_pass_verdict_is_returned_as_pass(self):
        with patch(
            "agent_system.agents.judge._call_gemini",
            return_value="JUDGE_VERDICT: pass\nThe fix correctly divides by 100.",
        ):
            result = judge_fix(**_COMMON_KWARGS)
        assert result["verdict"] == "pass"
        assert result["source"] == "gemini"

    def test_fail_verdict_is_returned_as_fail(self):
        with patch(
            "agent_system.agents.judge._call_gemini",
            return_value="JUDGE_VERDICT: fail\nThe fix only handles one case.",
        ):
            result = judge_fix(**_COMMON_KWARGS)
        assert result["verdict"] == "fail"
        assert result["source"] == "gemini"

    def test_api_exception_fails_closed(self):
        """A judge outage must reject, never silently approve."""
        with patch(
            "agent_system.agents.judge._call_gemini",
            side_effect=RuntimeError("Could not reach Gemini: connection refused"),
        ):
            result = judge_fix(**_COMMON_KWARGS)
        assert result["verdict"] == "fail"
        assert result["source"] == "error"
        assert "connection refused" in result["reasoning"]

    def test_unparseable_response_fails_closed(self):
        """A response with no JUDGE_VERDICT marker must reject, not default-approve."""
        with patch(
            "agent_system.agents.judge._call_gemini",
            return_value="This looks like a reasonable change overall.",
        ):
            result = judge_fix(**_COMMON_KWARGS)
        assert result["verdict"] == "fail"
        assert result["source"] == "unparsed"

    def test_missing_api_key_fails_closed(self):
        """No GEMINI_API_KEY configured must reject, not skip the gate."""
        with patch("agent_system.agents.judge.settings") as mock_settings:
            mock_settings.gemini_api_key = ""
            mock_settings.gemini_model = "gemini-2.5-flash"
            result = judge_fix(**_COMMON_KWARGS)
        assert result["verdict"] == "fail"
        assert result["source"] == "error"
