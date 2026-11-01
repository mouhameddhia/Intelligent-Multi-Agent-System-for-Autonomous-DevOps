"""The local student switch (agent_system/local_llm.py): Qwen3's <think> block
is split from the answer, and with settings.local_llm_model set the
single-call Remediation step runs on Ollama, not Groq. No network calls."""

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage

from agent_system.agents.remediation import run_remediation
from agent_system.config import settings
from agent_system.local_llm import local_student, split_thinking

_STATE = {
    "job_name": "webshop-backend",
    "build_number": 12,
    "evidence": {"jenkins": {"status": "ok"}},
    "monitoring_report": "test_orders.py::test_checkout fails.",
    "diagnosis_report": "tax.py::taxes_by_rate applies the discount per line.",
}

_ANSWER = (
    "===STEPS_JSON_START===\n"
    '["Apply the discount once in taxes_by_rate", "Run test_orders.py again"]\n'
    "===STEPS_JSON_END===\n"
    "Apply the discount once in taxes_by_rate.\n"
    "AGENT_CONFIDENCE: 80"
)


def test_think_block_is_moved_out_of_the_answer():
    result = split_thinking(AIMessage(content="<think>\nThe tax test fails.\n</think>\n\nThe answer."))
    assert result.content == "The answer."
    assert result.additional_kwargs["reasoning_content"] == "The tax test fails."


def test_answer_without_think_block_is_unchanged():
    result = split_thinking(AIMessage(content="The answer."))
    assert result.content == "The answer."
    assert result.additional_kwargs["reasoning_content"] is None


def test_no_local_model_by_default():
    with patch.object(settings, "local_llm_model", ""):
        assert local_student(max_tokens=100) is None


@patch("agent_system.agents.remediation.ChatGroq")
@patch("langchain_ollama.ChatOllama")
def test_remediation_runs_on_the_local_student(mock_ollama, mock_groq):
    mock_ollama.return_value.invoke.return_value = AIMessage(
        content=f"<think>reasoning</think>{_ANSWER}",
        response_metadata={"model": "pipeguard-qwen3-4b", "done_reason": "stop"},
    )
    with patch.object(settings, "local_llm_model", "pipeguard-qwen3-4b"):
        result = run_remediation(dict(_STATE))

    assert not mock_groq.return_value.invoke.called, "Groq must not answer when the local student is set"
    assert mock_ollama.call_args.kwargs["model"] == "pipeguard-qwen3-4b"
    assert result["remediation_confidence"] == 80
    assert len(result["steps"]) == 2
    call = result["llm_calls"][-1]
    assert call["model"] == "pipeguard-qwen3-4b"
    assert call["reasoning"] == "reasoning"
    assert "<think>" not in call["output"]


@patch("agent_system.agents.remediation.ChatGroq")
def test_remediation_stays_on_groq_without_local_model(mock_groq):
    mock_groq.return_value.invoke.return_value = MagicMock(
        content=_ANSWER, usage_metadata={}, response_metadata={}, additional_kwargs={},
    )
    with patch.object(settings, "local_llm_model", ""):
        result = run_remediation(dict(_STATE))

    assert mock_groq.return_value.invoke.called
    assert result["remediation_confidence"] == 80
