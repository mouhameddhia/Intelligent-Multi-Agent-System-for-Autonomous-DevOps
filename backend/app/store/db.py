"""SQLite persistence: incidents (JSON documents), audit log, history store and evaluation reports."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models.incident import Incident

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    scenario_id TEXT,
    source TEXT,
    status TEXT,
    severity TEXT,
    title TEXT,
    created_at TEXT,
    updated_at TEXT,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    incident_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    outcome TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS audit_incident ON audit(incident_id);
CREATE TABLE IF NOT EXISTS history (
    id TEXT PRIMARY KEY,
    service TEXT,
    time TEXT,
    symptoms TEXT,
    root_cause TEXT,
    fix TEXT,
    category TEXT,
    fix_action TEXT,
    source TEXT,
    use_for_retrieval INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    data TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # -- incidents --------------------------------------------------------------------------------
    def save_incident(self, inc: Incident) -> None:
        data = inc.model_dump_json()
        with self._lock:
            self._conn.execute(
                "INSERT INTO incidents(id, scenario_id, source, status, severity, title, created_at, updated_at, data)"
                " VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,"
                " severity=excluded.severity, title=excluded.title, updated_at=excluded.updated_at, data=excluded.data",
                (inc.id, inc.scenario_id, inc.source, inc.status.value, inc.severity.value, inc.title,
                 inc.created_at.isoformat(), inc.updated_at.isoformat(), data))
            self._conn.commit()

    def get_incident(self, incident_id: str) -> Incident | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM incidents WHERE id=?", (incident_id,)).fetchone()
        return Incident.model_validate_json(row["data"]) if row else None

    def list_incidents(self, source: str | None = None, limit: int = 100) -> list[Incident]:
        q = "SELECT data FROM incidents"
        args: list[Any] = []
        if source:
            q += " WHERE source=?"
            args.append(source)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [Incident.model_validate_json(r["data"]) for r in rows]

    def delete_incidents(self, source: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM audit WHERE incident_id IN (SELECT id FROM incidents WHERE source=?)", (source,))
            self._conn.execute("DELETE FROM incidents WHERE source=?", (source,))
            self._conn.commit()

    # -- audit ------------------------------------------------------------------------------------
    def audit(self, incident_id: str | None, actor: str, action: str, outcome: str = "",
              detail: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO audit(ts, incident_id, actor, action, outcome, detail) VALUES(?,?,?,?,?,?)",
                               (_now(), incident_id, actor, action, outcome, json.dumps(detail or {}, default=str)))
            self._conn.commit()

    def list_audit(self, incident_id: str | None = None, limit: int = 500, include_evaluation: bool = False) -> list[dict[str, Any]]:
        q = "SELECT a.* FROM audit a LEFT JOIN incidents i ON a.incident_id = i.id"
        conds: list[str] = []
        args: list[Any] = []
        if incident_id:
            conds.append("a.incident_id=?")
            args.append(incident_id)
        if not include_evaluation:
            conds.append("(i.source IS NULL OR i.source != 'evaluation')")
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY a.id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"] or "{}")} for r in rows]

    # -- history ----------------------------------------------------------------------------------
    def upsert_history(self, rows: list[dict[str, Any]]) -> int:
        with self._lock:
            for r in rows:
                self._conn.execute(
                    "INSERT OR REPLACE INTO history(id, service, time, symptoms, root_cause, fix, category, fix_action,"
                    " source, use_for_retrieval) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (r["id"], r["service"], r["time"], r["symptoms"], r["root_cause"], r["fix"], r.get("category"),
                     r.get("fix_action"), r["source"], 1 if r.get("use_for_retrieval", True) else 0))
            self._conn.commit()
        return len(rows)

    def list_history(self, retrieval_only: bool = True, exclude_sources: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        q = "SELECT * FROM history"
        conds, args = [], []
        if retrieval_only:
            conds.append("use_for_retrieval=1")
        if exclude_sources:
            conds.append(f"source NOT IN ({','.join('?' * len(exclude_sources))})")
            args.extend(exclude_sources)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY time DESC"
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    def history_count(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT source, COUNT(*) c FROM history GROUP BY source").fetchall()
        return {r["source"]: r["c"] for r in rows}

    # -- evaluations ------------------------------------------------------------------------------
    def save_evaluation(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO evaluations(created_at, data) VALUES(?,?)", (_now(), json.dumps(data, default=str)))
            self._conn.commit()

    def latest_evaluation(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM evaluations ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row["data"]) if row else None
