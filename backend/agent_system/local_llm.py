"""Optional local QLoRA student, served by Ollama with a Groq fallback."""
import logging

from langchain_core.messages import AIMessage

from .config import settings

logger = logging.getLogger(__name__)


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
            # Release the 4B model after each request so an enabled preference
            # does not keep several GB resident between investigations.
            keep_alive=0,
        )

    def invoke(self, messages) -> AIMessage:
        result = split_thinking(self._chat.invoke(messages))
        metadata = result.response_metadata or {}
        return result.model_copy(update={
            "response_metadata": {
                **metadata,
                "model": metadata.get("model") or settings.local_llm_model,
                "provider": "ollama",
            },
        })


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


def local_student(max_tokens: int, *, enabled: bool = True) -> LocalStudent | None:
    """Return the local student only for investigations that enable QLoRA."""
    return LocalStudent(max_tokens) if enabled and settings.local_llm_model else None


def invoke_with_fallback(groq_model, messages, max_tokens: int, *, use_local_llm: bool = False):
    """Use QLoRA only when requested, falling back to Groq on local errors."""
    if not use_local_llm or not settings.local_llm_model:
        return groq_model.invoke(messages)
    try:
        student = local_student(max_tokens=max_tokens, enabled=use_local_llm)
        result = student.invoke(messages)
        metadata = getattr(result, "response_metadata", None) or {}
        finish_reason = metadata.get("finish_reason") or metadata.get("done_reason")
        if finish_reason in {"length", "max_tokens"}:
            logger.warning(
                "Local QLoRA response reached its token limit; falling back to Groq."
            )
            return groq_model.invoke(messages)
        if isinstance(result.content, str) and result.content.strip():
            return result
        logger.warning("Local QLoRA model returned an empty response; falling back to Groq.")
    except Exception:
        logger.exception("Local QLoRA model call failed; falling back to Groq.")
    return groq_model.invoke(messages)
