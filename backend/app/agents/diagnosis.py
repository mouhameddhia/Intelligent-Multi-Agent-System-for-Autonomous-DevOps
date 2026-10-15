"""Diagnosis agent: ranks root-cause hypotheses with evidence.

Combines (1) log pattern groups, (2) the preceding change, (3) metric anomalies, (4) orchestration/pipeline state and
(5) similar past incidents (TF-IDF over the history store). A deterministic, signature-based scorer produces
candidates with evidence; the LLM may re-rank and explain them but cannot invent categories (guarded merge).
"""
from __future__ import annotations

from typing import Any

from ..connectors.base import ProviderSet
from ..llm.client import LLMClient
from ..models.agents import DiagnosisInput, DiagnosisOutput
from ..models.incident import Evidence, Hypothesis
from ..store.history import HistoryIndex

SYSTEM = (
    "You are an expert SRE performing root cause analysis. You receive an incident summary and candidate root-cause "
    "hypotheses with evidence computed by deterministic analyzers. Re-rank the candidates by likelihood. You may only "
    "use the candidate ids given. Return JSON: "
    '{"ranking": [{"id": "<candidate id>", "confidence": <0..1>, "reasoning": "<one sentence>"}], '
    '"summary": "<one or two sentences>"}'
)
LLM_BLEND = 0.35  # weight of the LLM confidence in the final score


def _has(text: str, terms: list[str]) -> list[str]:
    t = text.lower()
    return [x for x in terms if x.lower() in t]


class DiagnosisAgent:
    name = "diagnosis"

    def __init__(self, providers: ProviderSet, llm: LLMClient, history: HistoryIndex, signatures: dict[str, Any]) -> None:
        self.p = providers
        self.llm = llm
        self.history = history
        self.weights: dict[str, float] = signatures.get("weights", {})
        self.signatures: list[dict[str, Any]] = signatures.get("signatures", [])

    def _canary_deployments(self, inp: DiagnosisInput) -> list[str]:
        if not inp.namespace:
            return []
        out = []
        for d in self.p.orchestration.list_deployments(inp.namespace):
            labels = (d.config or {}).get("labels", {})
            if d.service == inp.service and d.name != inp.service and ("canary" in d.name or labels.get("track") == "canary"):
                out.append(d.name)
        return out

    def _history_query(self, inp: DiagnosisInput) -> str:
        parts = [inp.service, inp.trigger.description]
        parts += [p.template for p in inp.patterns if p.level in ("ERROR", "FATAL", "WARN")][:6]
        if inp.deployment:
            parts += [p.reason or "" for p in inp.deployment.pods]
        if inp.change:
            parts.append(inp.change.message)
        return " ".join(parts)

    def score(self, inp: DiagnosisInput) -> tuple[list[Hypothesis], list[dict[str, Any]]]:
        w = self.weights
        canaries = self._canary_deployments(inp)
        similar = self.history.search(self._history_query(inp), k=6,
                                      exclude_sources=tuple(inp.exclude_history_sources))
        err_patterns = [p for p in inp.patterns if p.level in ("ERROR", "FATAL", "WARN")]
        pod_reasons = {p.reason for p in (inp.deployment.pods if inp.deployment else []) if p.reason}
        event_reasons = {e.split(":", 1)[0].strip() for e in inp.deployment.recent_events} if inp.deployment else set()
        quality = self.p.quality.get_quality_report(inp.pipeline.branch or "main") if inp.pipeline else None

        hyps: list[Hypothesis] = []
        for sig in self.signatures:
            cat = sig["category"]
            kinds = sig.get("trigger_kinds")
            if cat in ("code_regression", "flaky_test") and inp.trigger.kind != "pipeline_failure":
                continue
            if kinds and inp.trigger.kind not in kinds:
                continue
            if sig.get("requires_canary") and not canaries:
                continue
            ev: list[Evidence] = []
            total = 0.0

            # logs: matching error/warn groups; stronger when emitted by (or naming) the incident service
            best = 0.0
            for p in err_patterns:
                hits = _has(p.template + " " + p.sample, sig.get("log_terms", []))
                if not hits:
                    continue
                srcs = " ".join(p.sources)
                origin = inp.service in srcs or inp.service in p.template or (cat == "stale_canary" and "canary" in srcs)
                s = 1.0 if origin else 0.6
                if s > best:
                    best = s
                ev.append(Evidence(kind="log", ref=f"log:{p.pattern_id}", weight=round(w.get("logs", 0) * s, 3),
                                   description=f"{p.count}× [{p.level}] {p.template[:140]} ({', '.join(p.sources[:2])})"))
            total += w.get("logs", 0) * best
            ev = sorted(ev, key=lambda e: e.weight, reverse=True)[:3]

            # orchestration state
            k8s_hits = [r for r in sig.get("k8s_reasons", []) if r in pod_reasons or r in event_reasons]
            if k8s_hits:
                total += w.get("k8s", 0)
                ev.append(Evidence(kind="k8s", ref=f"k8s:{inp.deployment.name if inp.deployment else inp.service}",
                                   weight=w.get("k8s", 0),
                                   description=f"Pod/event reasons: {', '.join(k8s_hits)}"))
            if cat == "stale_canary":
                total += w.get("k8s", 0) * 0.5
                ev.append(Evidence(kind="k8s", ref=f"k8s:deployment/{canaries[0]}", weight=round(w.get("k8s", 0) * 0.5, 3),
                                   description=f"Canary deployment(s) still running for {inp.service}: {', '.join(canaries)}"))

            # preceding change
            if inp.change:
                text = " ".join(inp.change.files_changed) + " " + inp.change.message
                hits = _has(text, sig.get("change_terms", []))
                if hits:
                    s = min(1.0, len(hits) / 2)
                    total += w.get("change", 0) * s
                    ev.append(Evidence(kind="change", ref=f"change:{inp.change.commit_sha[:7]}",
                                       weight=round(w.get("change", 0) * s, 3),
                                       description=f"Commit {inp.change.commit_sha[:7]} by {inp.change.author}: "
                                                   f"'{inp.change.message}' (matches: {', '.join(hits[:3])})"))

            # metrics
            m_hits = [a for a in inp.anomalies if a.metric in sig.get("metrics", [])]
            if m_hits:
                total += w.get("metrics", 0)
                a = m_hits[0]
                ev.append(Evidence(kind="metric", ref=f"metric:{a.service}/{a.metric}", weight=w.get("metrics", 0),
                                   description=f"{a.service}/{a.metric} anomalous (z={a.score}, {a.baseline:g} → {a.observed:g})"))

            # pipeline / quality
            if inp.pipeline and inp.pipeline.failed_stage and any(st in inp.pipeline.failed_stage for st in sig.get("pipeline_stages", [])):
                total += w.get("pipeline", 0)
                ev.append(Evidence(kind="pipeline", ref=f"pipeline:{inp.pipeline.name}/{inp.pipeline.run_id}",
                                   weight=w.get("pipeline", 0),
                                   description=f"Run {inp.pipeline.run_id} failed at stage '{inp.pipeline.failed_stage}'"))
                if quality and quality.tests_failed and cat == "code_regression":
                    ev.append(Evidence(kind="quality", ref=f"quality:{quality.ref[:7]}", weight=0.0,
                                       description=f"{quality.tests_failed}/{quality.tests_total} tests failing: {'; '.join(quality.failed_tests[:2])}"))

            # similar past incidents
            same = [h for h in similar if h.get("category") == cat]
            if same:
                s = min(1.0, same[0]["score"] / 0.35)
                total += w.get("history", 0) * s
                for h in same[:2]:
                    ev.append(Evidence(kind="history", ref=f"history:{h['id']}", weight=round(w.get("history", 0) * s, 3),
                                       description=f"{h['id']} ({h['service']}, sim {h['score']:.2f}): {h['root_cause']} → {h['fix']}"))

            if total <= 0.05:
                continue
            desc = sig.get("description", "")
            if cat == "stale_canary":
                desc += f" Candidate: deployment/{canaries[0]}."
            hyps.append(Hypothesis(
                id=f"H{len(hyps) + 1}", rank=0, title=sig["title"].format(service=inp.service), category=cat,
                component=inp.service, description=desc, confidence=round(min(0.97, total), 3), evidence=ev,
                similar_incidents=[h["id"] for h in same[:3]]))
        hyps.sort(key=lambda h: h.confidence, reverse=True)
        return hyps, similar

    def run(self, inp: DiagnosisInput) -> DiagnosisOutput:
        candidates, similar = self.score(inp)
        candidates = candidates[:5]
        payload = {
            "incident_id": inp.incident_id,
            "service": inp.service,
            "environment": inp.environment,
            "trigger": inp.trigger.description,
            "log_interpretation": inp.log_summary.interpretation if inp.log_summary else None,
            "top_log_patterns": [{"level": p.level, "count": p.count, "template": p.template[:160]} for p in inp.patterns[:6]],
            "metric_anomalies": [f"{a.service}/{a.metric}: {a.baseline:g} -> {a.observed:g} (z={a.score})" for a in inp.anomalies[:4]],
            "change": {"sha": inp.change.commit_sha[:7], "message": inp.change.message, "files": inp.change.files_changed,
                       "diff_excerpt": inp.change.diff[:1200]} if inp.change else None,
            "pods": [f"{p.name}: {p.reason or p.phase} restarts={p.restarts}" for p in (inp.deployment.pods if inp.deployment else [])][:6],
            "candidates": [{"id": h.id, "category": h.category, "title": h.title, "heuristic_confidence": h.confidence,
                            "evidence": [e.description for e in h.evidence][:5]} for h in candidates],
        }

        def mock(pl: dict[str, Any]) -> dict[str, Any]:
            ranking = [{"id": c["id"], "confidence": c["heuristic_confidence"],
                        "reasoning": f"Supported by {len(c['evidence'])} evidence item(s): " + "; ".join(e[:80] for e in c["evidence"][:2])}
                       for c in pl["candidates"]]
            top = pl["candidates"][0] if pl["candidates"] else None
            summary = (f"Most likely: {top['title']} (confidence {top['heuristic_confidence']:.0%})." if top
                       else "No hypothesis is supported by the available evidence.")
            return {"ranking": ranking, "summary": summary}

        ids = {h.id for h in candidates}
        llm = self.llm.complete_json("rank_hypotheses", SYSTEM, payload, mock,
                                     validate=lambda d: isinstance(d.get("ranking"), list))
        by_id = {h.id: h for h in candidates}
        for r in llm.data.get("ranking", []):
            h = by_id.get(str(r.get("id")))
            if h is None or str(r.get("id")) not in ids:
                continue  # guard: the LLM cannot invent hypotheses
            try:
                c = float(r.get("confidence", h.confidence))
            except (TypeError, ValueError):
                c = h.confidence
            c = max(0.0, min(1.0, c))
            h.confidence = round(min(0.97, (1 - LLM_BLEND) * h.confidence + LLM_BLEND * c), 3)
            if r.get("reasoning") and llm.provider != "mock":
                h.description = f"{h.description} LLM: {str(r['reasoning'])[:300]}"
        ranked = sorted(candidates, key=lambda h: h.confidence, reverse=True)[:3]
        for i, h in enumerate(ranked, start=1):
            h.rank = i
            h.id = f"H{i}"
        sufficient = bool(ranked) and ranked[0].confidence >= 0.3
        summary = str(llm.data.get("summary") or "")[:600]
        if not sufficient:
            summary = "Insufficient evidence for a confident diagnosis. " + summary
        return DiagnosisOutput(incident_id=inp.incident_id, hypotheses=ranked, summary=summary,
                               llm_provider=llm.provider, sufficient_evidence=sufficient)
