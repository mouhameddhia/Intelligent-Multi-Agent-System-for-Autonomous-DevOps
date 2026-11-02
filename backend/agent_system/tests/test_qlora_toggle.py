import sys
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from agent_system.config import settings
from agent_system.local_llm import invoke_with_fallback


class FakeGroq:
    def __init__(self):
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        return AIMessage(content="Groq response")


def test_toggle_off_uses_groq_even_when_local_model_is_configured(monkeypatch):
    groq = FakeGroq()
    monkeypatch.setattr(settings, "local_llm_model", "pipeguard-qwen3-4b")

    result = invoke_with_fallback(
        groq, [("human", "hello")], max_tokens=64, use_local_llm=False
    )

    assert result.content == "Groq response"
    assert groq.calls == 1


def test_toggle_on_uses_ollama_and_marks_the_actual_provider(monkeypatch):
    monkeypatch.setattr(settings, "local_llm_model", "pipeguard-qwen3-4b")

    class FakeChatOllama:
        def __init__(self, **_kwargs):
            pass

        def invoke(self, _messages):
            return AIMessage(
                content="<think>reason</think>QLoRA response",
                response_metadata={"model": "pipeguard-qwen3-4b"},
            )

    monkeypatch.setitem(
        sys.modules, "langchain_ollama", SimpleNamespace(ChatOllama=FakeChatOllama)
    )
    groq = FakeGroq()

    result = invoke_with_fallback(
        groq, [("human", "hello")], max_tokens=64, use_local_llm=True
    )

    assert result.content == "QLoRA response"
    assert result.response_metadata["provider"] == "ollama"
    assert groq.calls == 0


def test_toggle_on_falls_back_to_groq_when_ollama_is_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "local_llm_model", "pipeguard-qwen3-4b")

    class UnavailableChatOllama:
        def __init__(self, **_kwargs):
            raise ConnectionError("Ollama unavailable")

    monkeypatch.setitem(
        sys.modules,
        "langchain_ollama",
        SimpleNamespace(ChatOllama=UnavailableChatOllama),
    )
    groq = FakeGroq()

    result = invoke_with_fallback(
        groq, [("human", "hello")], max_tokens=64, use_local_llm=True
    )

    assert result.content == "Groq response"
    assert groq.calls == 1
