"""History store: seed loaders (normalize everything into one incident format) and TF-IDF similarity search.

Normalized row: id, service, time, symptoms, root_cause, fix, category, fix_action, source, use_for_retrieval.
LogChunks and RCAEval loaders mark rows `use_for_retrieval=False`: they are reserved for testing and never reach
prompts or similarity search.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

import yaml
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .db import Store

TEST_ONLY_SOURCES = ("logchunks", "rcaeval")


def _norm(row: dict[str, Any], source: str, retrieval: bool = True) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "service": str(row.get("service", "*")),
        "time": str(row.get("time", "")),
        "symptoms": str(row.get("symptoms", "")),
        "root_cause": str(row.get("root_cause", "")),
        "fix": str(row.get("fix", "")),
        "category": row.get("category"),
        "fix_action": row.get("fix_action"),
        "source": source,
        "use_for_retrieval": retrieval,
    }


def load_synthetic(path: Path) -> list[dict[str, Any]]:
    return [_norm(r, "synthetic") for r in yaml.safe_load(path.read_text()) or []]


def load_runbooks(path: Path) -> list[dict[str, Any]]:
    return [_norm(r, "runbook") for r in yaml.safe_load(path.read_text()) or []]


def _jsonl(folder: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for f in sorted(folder.glob("*.jsonl")):
        rows += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return rows


def load_ghalogs(folder: Path) -> list[dict[str, Any]]:
    """Stub loader for GHALogs-style GitHub Actions run records."""
    out = []
    for i, r in enumerate(_jsonl(folder)):
        out.append(_norm({
            "id": f"GHA-{i:04d}", "service": r.get("service", r.get("repo", "*")), "time": r.get("started_at", ""),
            "symptoms": f"{r.get('workflow')} / {r.get('job')} {r.get('conclusion')}: {r.get('log_excerpt', '')}",
            "root_cause": r.get("label", "unknown"), "fix": r.get("resolution", ""), "category": r.get("label"),
            "fix_action": "rerun_pipeline" if "rerun" in r.get("resolution", "") else None,
        }, "ghalogs"))
    return out


def load_logchunks(folder: Path) -> list[dict[str, Any]]:
    """Stub loader for LogChunks (build-log failure chunks). TEST ONLY — excluded from retrieval and prompts."""
    return [_norm({"id": f"LC-{r['log_id']}", "service": "*", "time": "", "symptoms": r.get("chunk", ""),
                   "root_cause": r.get("category", ""), "fix": "", "category": r.get("category")}, "logchunks", False)
            for r in _jsonl(folder)]


def load_rcaeval(folder: Path) -> list[dict[str, Any]]:
    """Stub loader for RCAEval cases. TEST ONLY — excluded from retrieval and prompts."""
    return [_norm({"id": f"RCA-{r['case_id']}", "service": r.get("root_cause_service", "*"), "time": r.get("inject_time", ""),
                   "symptoms": f"{r.get('fault_type')} fault in {r.get('system')}",
                   "root_cause": f"{r.get('fault_type')} on {r.get('root_cause_service')}", "fix": "",
                   "category": r.get("fault_type")}, "rcaeval", False)
            for r in _jsonl(folder)]


LOADERS: dict[str, tuple[str, Callable[[Path], list[dict[str, Any]]]]] = {
    "synthetic": ("synthetic_incidents.yaml", load_synthetic),
    "runbook": ("runbooks.yaml", load_runbooks),
    "ghalogs": ("ghalogs", load_ghalogs),
    "logchunks": ("logchunks", load_logchunks),
    "rcaeval": ("rcaeval", load_rcaeval),
}


def seed_history(store: Store, history_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, (rel, loader) in LOADERS.items():
        p = history_dir / rel
        if p.exists():
            counts[name] = store.upsert_history(loader(p))
    return counts


class HistoryIndex:
    """TF-IDF over symptoms + root cause + service of retrieval-eligible history rows."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._lock = threading.Lock()

    def search(self, query: str, k: int = 5, exclude_sources: tuple[str, ...] = (),
               min_score: float = 0.05) -> list[dict[str, Any]]:
        rows = self.store.list_history(retrieval_only=True, exclude_sources=TEST_ONLY_SOURCES + tuple(exclude_sources))
        if not rows or not query.strip():
            return []
        docs = [f"{r['service']} {r['symptoms']} {r['root_cause']}" for r in rows]
        with self._lock:
            vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, token_pattern=r"(?u)\b[\w\.:-]{2,}\b",
                                  stop_words="english")
            matrix = vec.fit_transform(docs + [query])
        sims = cosine_similarity(matrix[-1], matrix[:-1]).ravel()
        ranked = sorted(zip(rows, sims), key=lambda x: x[1], reverse=True)
        return [{**r, "score": round(float(s), 4)} for r, s in ranked[:k] if s >= min_score]
