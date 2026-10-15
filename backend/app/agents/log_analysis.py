"""LogAnalysis agent: deterministic grouping of raw lines into patterns; the LLM only interprets the top groups."""
from __future__ import annotations

from typing import Any

from ..connectors.base import LogLine, ProviderSet
from ..llm.client import LLMClient
from ..models.agents import LogAnalysisInput, LogAnalysisOutput
from ..models.incident import LogAnalysisSummary, LogPattern
from .log_parser import parse_logs

SYSTEM = (
    "You are a site reliability engineer. You receive the top log pattern groups (templates with counts) for an "
    "incident, never raw logs. Explain what they indicate. Return JSON: "
    '{"interpretation": "<2-3 sentences>", "key_errors": ["<short phrase>", ...]}'
)


def mock_interpret(payload: dict[str, Any]) -> dict[str, Any]:
    groups = payload["top_patterns"]
    errs = [g for g in groups if g["level"] in ("ERROR", "FATAL")]
    warns = [g for g in groups if g["level"] == "WARN"]
    parts = []
    if errs:
        e = errs[0]
        parts.append(f"The dominant error is '{e['template'][:120]}' ({e['count']}× from {', '.join(e['sources'])}).")
        # prefer the error emitted by the incident service itself as the likely origin
        own = [g for g in errs if payload["service"] in " ".join(g["sources"])]
        if own and own[0] is not e:
            parts.append(f"{payload['service']} itself reports '{own[0]['template'][:120]}' ({own[0]['count']}×), "
                         f"which looks like the origin; the other errors are downstream symptoms.")
    if warns:
        parts.append(f"Warnings: '{warns[0]['template'][:100]}' ({warns[0]['count']}×).")
    if not parts:
        parts.append("No error-level patterns in the window; logs look like normal traffic.")
    key = [g["template"][:80] for g in (errs + warns)[:4]]
    return {"interpretation": " ".join(parts), "key_errors": key}


class LogAnalysisAgent:
    name = "log_analysis"

    def __init__(self, providers: ProviderSet, llm: LLMClient) -> None:
        self.p = providers
        self.llm = llm

    def _collect(self, inp: LogAnalysisInput) -> list[LogLine]:
        lines: list[LogLine] = []
        if inp.pipeline and inp.pipeline.failed_stage:
            lines += self.p.pipeline.get_pipeline_logs(inp.pipeline.pipeline_id, inp.pipeline.run_id)
        if inp.runtime_logs:
            lines += self.p.logging.query_logs(inp.service, inp.window.start, inp.window.end, limit=50000)
        return lines

    def run(self, inp: LogAnalysisInput) -> LogAnalysisOutput:
        try:
            lines = self._collect(inp)
        except Exception as e:  # noqa: BLE001
            lines = []
            missing = [f"logs ({type(e).__name__})"]
        else:
            missing = [] if lines else ["logs"]
        result = parse_logs(lines)
        top = result.top(inp.top_n_for_llm)
        payload = {
            "incident_id": inp.incident_id,
            "service": inp.service,
            "raw_line_count": result.raw_line_count,
            "top_patterns": [{"id": g.pattern_id, "level": g.level, "count": g.count, "template": g.template,
                              "sources": list(g.sources)[:3]} for g in top],
        }
        llm = self.llm.complete_json("interpret_logs", SYSTEM, payload, mock_interpret,
                                     validate=lambda d: isinstance(d.get("interpretation"), str))
        patterns = [LogPattern(pattern_id=g.pattern_id, template=g.template, count=g.count, level=g.level,
                               first_seen=g.first_seen, last_seen=g.last_seen, sample=g.sample[:400],
                               sources=list(g.sources)[:5]) for g in result.top(15)]
        summary = LogAnalysisSummary(
            raw_line_count=result.raw_line_count, pattern_count=len(result.groups), patterns_sent_to_llm=len(top),
            reduction_ratio=result.reduction_ratio(len(top)),
            interpretation=str(llm.data.get("interpretation", ""))[:1200],
            key_errors=[str(k)[:160] for k in llm.data.get("key_errors", [])][:6])
        return LogAnalysisOutput(incident_id=inp.incident_id, patterns=patterns, summary=summary,
                                 llm_provider=llm.provider, missing_data=missing)
