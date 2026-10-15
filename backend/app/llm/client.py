"""LLM fallback chain over OpenAI-compatible endpoints (Groq, OpenRouter, local Ollama…) with a deterministic mock.

Agents call `complete_json(task, system, payload, mock)`. `payload` must be a *summary* (never raw logs). The mock is a
pure function of the payload, so MOCK_LLM runs are reproducible and every code path stays exercised.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from ..config import Settings

log = logging.getLogger(__name__)

MAX_PAYLOAD_CHARS = 12000


@dataclass
class LLMResult:
    data: dict[str, Any]
    provider: str
    latency_ms: float
    attempts: list[str] = field(default_factory=list)


@dataclass
class ProviderCfg:
    name: str
    base_url: str
    model: str
    api_key_env: str
    local: bool = False

    def api_key(self) -> str | None:
        v = os.getenv(self.api_key_env)
        if self.local:
            return "ollama" if v and v.lower() in {"1", "true", "yes"} else None
        return v or None


def _extract_json(text: str) -> dict[str, Any]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in LLM output")
    return json.loads(text[start:end + 1])


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        cfg = settings.load_yaml("llm.yaml")
        self.mock_only = settings.mock_llm
        self.timeout = float(cfg.get("timeout_s", 20))
        self.temperature = float(cfg.get("temperature", 0.1))
        self.chain = [ProviderCfg(**p) for p in cfg.get("chain", [])]

    def active_chain(self) -> list[str]:
        if self.mock_only:
            return ["mock"]
        return [p.name for p in self.chain if p.api_key()] + ["mock"]

    def complete_json(self, task: str, system: str, payload: dict[str, Any],
                      mock: Callable[[dict[str, Any]], dict[str, Any]],
                      validate: Callable[[dict[str, Any]], bool] | None = None) -> LLMResult:
        user = json.dumps(payload, default=str)
        if len(user) > MAX_PAYLOAD_CHARS:  # hard guard: summaries only
            user = user[:MAX_PAYLOAD_CHARS]
        attempts: list[str] = []
        t0 = time.perf_counter()
        if not self.mock_only:
            for p in self.chain:
                key = p.api_key()
                if not key:
                    continue
                try:
                    data = self._call(p, key, system, user)
                    if validate and not validate(data):
                        raise ValueError("output failed validation")
                    attempts.append(f"{p.name}:ok")
                    return LLMResult(data, p.name, (time.perf_counter() - t0) * 1000, attempts)
                except Exception as e:  # noqa: BLE001 - any failure moves to the next provider
                    log.warning("LLM provider %s failed for %s: %s", p.name, task, e)
                    attempts.append(f"{p.name}:{type(e).__name__}")
        data = mock(payload)
        attempts.append("mock:ok")
        return LLMResult(data, "mock", (time.perf_counter() - t0) * 1000, attempts)

    def _call(self, p: ProviderCfg, key: str, system: str, user: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": p.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system + "\nRespond with a single JSON object only."},
                {"role": "user", "content": user},
            ],
        }
        if not p.local:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if "openrouter" in p.base_url:
            headers["X-Title"] = "autonomous-devops-agents"
        r = httpx.post(f"{p.base_url.rstrip('/')}/chat/completions", json=body, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return _extract_json(content)
