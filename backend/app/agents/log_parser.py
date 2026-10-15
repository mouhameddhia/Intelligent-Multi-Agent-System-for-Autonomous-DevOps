"""Deterministic log parser: masks variable tokens, groups lines into templates and ranks the groups.

Only the top groups (template + count + one sample) are ever passed to an LLM.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime

from ..connectors.base import LogLine

LEVEL_WEIGHT = {"FATAL": 6.0, "CRITICAL": 6.0, "ERROR": 4.0, "WARN": 2.0, "WARNING": 2.0, "INFO": 0.5, "DEBUG": 0.2}

_MASKS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<UUID>"),
    (re.compile(r"https?://\S+"), "<URL>"),
    (re.compile(r"\b((?:id|pod|session|userId|request_id|trace_id|span_id)=)[^\s,;)]+", re.I), r"\1<ID>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<IP>"),
    (re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)*-[a-z0-9]{8,10}-[a-z0-9]{5}\b"), "<POD>"),
    (re.compile(r"\b[0-9a-f]{4,}(?:-[0-9a-f]{4,})+\b", re.I), "<HEX>"),
    (re.compile(r"\b[0-9a-f]{8,}\b", re.I), "<HEX>"),
    (re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{6,}\b", re.I), "<HEX>"),
    (re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{8,}\b"), "<ID>"),
    (re.compile(r"\b\d+(?:\.\d+)?(?=(?:ms|s|MiB|Mi|MB|KB|GB|%)\b)"), "<NUM>"),
    (re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])"), "<NUM>"),
]
_POD_SUFFIX = re.compile(r"-[a-z0-9]{8,10}-[a-z0-9]{5}$")


def normalize_source(source: str) -> str:
    """checkoutservice-7d9f8c6b5-x2k9p -> checkoutservice"""
    return _POD_SUFFIX.sub("", source)


def mask(message: str) -> str:
    out = message
    for rx, repl in _MASKS:
        out = rx.sub(repl, out)
    return re.sub(r"\s+", " ", out).strip()


@dataclass
class PatternGroup:
    pattern_id: str
    template: str
    level: str
    count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    sample: str = ""
    sources: "OrderedDict[str, int]" = field(default_factory=OrderedDict)

    @property
    def score(self) -> float:
        return LEVEL_WEIGHT.get(self.level, 0.5) * math.log1p(self.count)


@dataclass
class ParseResult:
    raw_line_count: int
    groups: list[PatternGroup]

    def top(self, n: int) -> list[PatternGroup]:
        return self.groups[:n]

    def reduction_ratio(self, n_sent: int) -> float:
        if self.raw_line_count == 0:
            return 0.0
        return round(1.0 - (min(n_sent, len(self.groups)) / self.raw_line_count), 4)


def parse_logs(lines: list[LogLine]) -> ParseResult:
    groups: dict[tuple[str, str], PatternGroup] = {}
    for ln in lines:
        level = (ln.level or "INFO").upper()
        tpl = mask(ln.message)
        key = (level, tpl)
        g = groups.get(key)
        if g is None:
            pid = "P" + hashlib.sha1(f"{level}|{tpl}".encode()).hexdigest()[:6].upper()
            g = groups[key] = PatternGroup(pattern_id=pid, template=tpl, level=level, sample=ln.message)
        g.count += 1
        if ln.ts:
            g.first_seen = ln.ts if g.first_seen is None or ln.ts < g.first_seen else g.first_seen
            g.last_seen = ln.ts if g.last_seen is None or ln.ts > g.last_seen else g.last_seen
        src = normalize_source(ln.source)
        g.sources[src] = g.sources.get(src, 0) + 1
    ranked = sorted(groups.values(), key=lambda g: (g.score, g.count), reverse=True)
    return ParseResult(raw_line_count=len(lines), groups=ranked)
