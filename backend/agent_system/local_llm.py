"""The local fine-tuned student (backend/training/README.md), served by Ollama.

When settings.local_llm_model is set, the single-call Monitoring and Remediation
steps (the calls the student was distilled on) run on it instead of Groq.
Diagnosis, Fix and the tool-calling fallback paths stay on the large model."""
from langchain_core.messages import AIMessage

from .config import settings


class LocalStudent:
    """ChatOllama with Qwen3's <think> block moved out of the answer, so agents
    parse the reply exactly like a Groq one: the answer in `content`, the
    reasoning where Groq puts it (additional_kwargs["reasoning_content"])."""

    def __init__(self, max_tokens: int):
        from langchain_ollama import ChatOllama

        self._chat = ChatOllama(
            model=settings.local_llm_model,
            base_url=settings.local_llm_url,
            temperature=0,
            num_predict=max_tokens,
            # Ollama's default context is too short for the evidence prompts.
            num_ctx=settings.local_llm_num_ctx,
            num_gpu=settings.local_llm_num_gpu,
        )

    def invoke(self, messages) -> AIMessage:
        return split_thinking(self._chat.invoke(messages))


def split_thinking(result: AIMessage) -> AIMessage:
    text = result.content if isinstance(result.content, str) else ""
    reasoning = (result.additional_kwargs or {}).get("reasoning_content")
    if "</think>" in text:
        thinking, text = text.split("</think>", 1)
        reasoning = thinking.replace("<think>", "").strip()
    return result.model_copy(update={
        "content": text.strip(),
        "additional_kwargs": {**(result.additional_kwargs or {}), "reasoning_content": reasoning},
    })


def local_student(max_tokens: int) -> LocalStudent | None:
    """The local model for a distilled single-call step, or None to use Groq."""
    return LocalStudent(max_tokens) if settings.local_llm_model else None
