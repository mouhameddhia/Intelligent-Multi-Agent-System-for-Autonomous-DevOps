"""Records each single-shot LLM call (exact prompt, raw output, tokens,
latency) into state["llm_calls"]. Investigations persist this list to Mongo,
and training/export_sft.py turns verified calls into fine-tuning examples,
so the training prompts are byte-for-byte the production prompts."""
import time

from .config import settings

_ROLES = {"system": "system", "human": "user", "ai": "assistant"}


def record_call(agent: str, messages: list[tuple[str, str]], result, started: float) -> dict:
    """`started` is a time.perf_counter() value taken just before llm.invoke()."""
    usage = getattr(result, "usage_metadata", None) or {}
    metadata = getattr(result, "response_metadata", None) or {}
    extra = getattr(result, "additional_kwargs", None) or {}
    return {
        "agent": agent,
        "model": metadata.get("model_name") or settings.llm_model,
        "messages": [{"role": _ROLES[role], "content": content} for role, content in messages],
        "output": result.content,
        # Present only when settings.llm_include_reasoning is on.
        "reasoning": extra.get("reasoning_content"),
        "finish_reason": metadata.get("finish_reason"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }


def append_call(state: dict, entry: dict) -> list[dict]:
    return [*(state.get("llm_calls") or []), entry]
