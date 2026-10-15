"""LLM fallback chain: skips providers without keys, falls through on errors/invalid output, ends at the mock."""
from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.llm.client import LLMClient, _extract_json


def make_client(settings: Settings, monkeypatch, chain) -> LLMClient:
    s = settings.model_copy(update={"mock_llm": False})
    c = LLMClient(s)
    from app.llm.client import ProviderCfg
    c.chain = [ProviderCfg(**p) for p in chain]
    return c


CHAIN = [
    {"name": "p1", "base_url": "http://p1", "model": "m", "api_key_env": "P1_KEY"},
    {"name": "p2", "base_url": "http://p2", "model": "m", "api_key_env": "P2_KEY"},
]


def fake_post(responses):
    def post(url, json=None, headers=None, timeout=None):
        r = responses[url.split("/")[2]]
        if isinstance(r, Exception):
            raise r
        return httpx.Response(200, json={"choices": [{"message": {"content": r}}]}, request=httpx.Request("POST", url))
    return post


def test_falls_through_to_second_provider(settings, monkeypatch):
    monkeypatch.setenv("P1_KEY", "x")
    monkeypatch.setenv("P2_KEY", "y")
    monkeypatch.setattr(httpx, "post", fake_post({"p1": httpx.ConnectError("down"), "p2": '```json\n{"interpretation": "ok"}\n```'}))
    res = make_client(settings, monkeypatch, CHAIN).complete_json("t", "sys", {"a": 1}, lambda p: {"interpretation": "mock"})
    assert res.provider == "p2" and res.data == {"interpretation": "ok"}
    assert res.attempts == ["p1:ConnectError", "p2:ok"]


def test_invalid_output_falls_back_to_mock(settings, monkeypatch):
    monkeypatch.setenv("P1_KEY", "x")
    monkeypatch.delenv("P2_KEY", raising=False)
    monkeypatch.setattr(httpx, "post", fake_post({"p1": '{"wrong": 1}'}))
    res = make_client(settings, monkeypatch, CHAIN).complete_json(
        "t", "sys", {}, lambda p: {"interpretation": "mock"}, validate=lambda d: "interpretation" in d)
    assert res.provider == "mock" and res.data == {"interpretation": "mock"}


def test_mock_only_never_calls_network(settings, monkeypatch):
    monkeypatch.setenv("P1_KEY", "x")
    def boom(*a, **k):
        raise AssertionError("network called")
    monkeypatch.setattr(httpx, "post", boom)
    c = make_client(settings, monkeypatch, CHAIN)
    c.mock_only = True
    assert c.complete_json("t", "s", {}, lambda p: {"k": 1}).provider == "mock"


@pytest.mark.parametrize("text", ['<think>hmm</think>{"a": 1}', 'Sure! {"a": 1} done', '```{"a": 1}```'])
def test_extract_json(text):
    assert _extract_json(text) == {"a": 1}
