"""Records single-shot LLM calls in state for offline fine-tuning.

The API persists these records only when STORE_LLM_CALLS=true because prompts
can contain source excerpts and console logs.
"""
import time

from .config import settings

_ROLES = {"system": "system", "human": "user", "ai": "assistant"}


def record_call(agent: str, messages: list[tuple[str, str]], result, started: float) -> dict:
    """`started` is a time.perf_counter() value taken just before llm.invoke()."""
    usage = getattr(result, "usage_metadata", None) or {}
    metadata = getattr(result, "response_metadata", None) or {}
    extra = getattr(result, "additional_kwargs", None) or {}
    model = metadata.get("model_name") or metadata.get("model")
    if not model:
        model = settings.local_llm_model or settings.llm_model
    provider = metadata.get("provider")
    if not provider:
        provider = (
            "ollama" if settings.local_llm_model and model == settings.local_llm_model
            else "groq" if model == settings.llm_model or metadata.get("model_name")
            else "unknown"
        )
    return {
        "agent": agent,
        "model": model,
        "provider": provider,
        "messages": [{"role": _ROLES[role], "content": content} for role, content in messages],
        "output": result.content,
        # Present only when settings.llm_include_reasoning is on.
        "reasoning": extra.get("reasoning_content"),
        "finish_reason": metadata.get("finish_reason") or metadata.get("done_reason"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }


def performance_summary(calls: list[dict], *, qlora_requested: bool) -> dict:
    """Keep compact, non-prompt performance metrics on each incident."""
    metrics = [
        {
            key: call.get(key)
            for key in (
                "agent", "provider", "model", "input_tokens", "output_tokens",
                "latency_ms", "finish_reason",
            )
        }
        for call in (calls or [])
    ]

    def total(key: str):
        values = [entry[key] for entry in metrics if isinstance(entry.get(key), int)]
        return sum(values) if values else None

    return {
        "requested_mode": "qlora" if qlora_requested else "groq_baseline",
        "qlora_requested": qlora_requested,
        "calls": metrics,
        "call_count": len(metrics),
        "total_agent_latency_ms": total("latency_ms"),
        "total_input_tokens": total("input_tokens"),
        "total_output_tokens": total("output_tokens"),
        "providers_used": sorted({
            entry["provider"] for entry in metrics
            if isinstance(entry.get("provider"), str) and entry["provider"] != "unknown"
        }),
    }


def append_call(state: dict, entry: dict) -> list[dict]:
    return [*(state.get("llm_calls") or []), entry]
