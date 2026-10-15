"""FastAPI application: REST endpoints, Server-Sent Events stream and the static dashboard."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..connectors.registry import adapter_selection, build_providers
from ..connectors.simulated import SimulatedWorld, list_scenarios
from ..evaluation.evaluate import evaluate
from ..events import EventBus
from ..llm.client import LLMClient
from ..models.agents import ApprovalDecision
from ..models.incident import AGENT_ORDER, Incident
from ..orchestrator.runner import RunManager
from ..store.db import Store
from ..store.history import HistoryIndex, seed_history

log = logging.getLogger("app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class AppState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = Store(settings.db_path)
        self.bus = EventBus()
        self.llm = LLMClient(settings)
        self.history = HistoryIndex(self.store)
        self.runs = RunManager(settings, self.store, self.bus, self.history, self.llm)
        self.eval_running = False
        self.eval_lock = threading.Lock()


class ApprovalRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    reason: str = Field(min_length=3, max_length=1000)
    approver: str = Field(default="on-call engineer", min_length=1, max_length=100)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    state = AppState(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state.bus.bind_loop(asyncio.get_running_loop())
        if settings.seed_history:
            counts = seed_history(state.store, settings.data_dir / "history")
            log.info("history seeded: %s", counts)
        log.info("LLM chain: %s", state.llm.active_chain())
        yield

    app = FastAPI(title="Autonomous DevOps Agents", version="1.0.0", lifespan=lifespan)
    app.state.core = state
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def _get(incident_id: str) -> Incident:
        inc = state.store.get_incident(incident_id)
        if not inc:
            raise HTTPException(404, f"incident {incident_id} not found")
        return inc

    # -- meta -------------------------------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "mock_llm": settings.mock_llm, "llm_chain": state.llm.active_chain(),
                "adapters": adapter_selection(settings), "history": state.store.history_count(),
                "step_delay_s": settings.step_delay_s}

    @app.get("/api/architecture")
    def architecture() -> dict[str, Any]:
        sel = adapter_selection(settings)
        cfg = settings.load_yaml("adapters.yaml").get("adapters", {})
        real = {k: {"enabled": bool(v.get("enabled")), "allow_writes": bool(v.get("allow_writes"))} for k, v in cfg.items()}
        return {"providers": sel, "real_adapters": real, "agents": AGENT_ORDER, "llm_chain": state.llm.active_chain(),
                "policies": settings.load_yaml("policies.yaml").get("rules", [])}

    @app.get("/api/policies")
    def policies() -> dict[str, Any]:
        return settings.load_yaml("policies.yaml")

    # -- scenarios --------------------------------------------------------------------------------
    @app.get("/api/scenarios")
    def scenarios() -> list[dict[str, Any]]:
        return [{k: s.get(k) for k in ("id", "order", "title", "summary", "service", "environment", "namespace", "expected")}
                for s in list_scenarios(settings.scenarios_dir)]

    @app.post("/api/scenarios/{scenario_id}/replay", status_code=202)
    def replay(scenario_id: str) -> dict[str, Any]:
        if not (settings.scenarios_dir / scenario_id / "scenario.yaml").exists():
            raise HTTPException(404, f"unknown scenario {scenario_id}")
        inc = state.runs.start_async(scenario_id)
        return inc.model_dump(mode="json")

    # -- incidents --------------------------------------------------------------------------------
    @app.get("/api/incidents")
    def incidents(include_evaluation: bool = False, limit: int = Query(100, le=500)) -> list[dict[str, Any]]:
        items = state.store.list_incidents(limit=limit)
        if not include_evaluation:
            items = [i for i in items if i.source != "evaluation"]
        return [i.model_dump(mode="json") for i in items]

    @app.get("/api/incidents/{incident_id}")
    def incident(incident_id: str) -> dict[str, Any]:
        return _get(incident_id).model_dump(mode="json")

    @app.post("/api/incidents/{incident_id}/approval", status_code=202)
    def approval(incident_id: str, body: ApprovalRequest) -> dict[str, Any]:
        _get(incident_id)
        try:
            inc = state.runs.submit_approval(incident_id, ApprovalDecision(decision=body.decision,  # type: ignore[arg-type]
                                                                           approver=body.approver, reason=body.reason))
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return inc.model_dump(mode="json")

    @app.delete("/api/incidents")
    def reset() -> dict[str, Any]:
        state.store.delete_incidents("demo")
        return {"ok": True}

    @app.get("/api/audit")
    def audit(incident_id: str | None = None, limit: int = Query(500, le=5000)) -> list[dict[str, Any]]:
        return state.store.list_audit(incident_id, limit)

    @app.get("/api/history")
    def history(q: str | None = None, k: int = 8) -> dict[str, Any]:
        if q:
            return {"results": state.history.search(q, k=k)}
        return {"counts": state.store.history_count(), "results": state.store.list_history(retrieval_only=False)}

    @app.get("/api/scenarios/{scenario_id}/adapters")
    def scenario_adapters(scenario_id: str) -> dict[str, str]:
        return build_providers(settings, SimulatedWorld(settings.scenarios_dir, scenario_id)).describe()

    # -- evaluation -------------------------------------------------------------------------------
    @app.get("/api/evaluation")
    def evaluation() -> dict[str, Any]:
        return {"running": state.eval_running, "report": state.store.latest_evaluation()}

    @app.post("/api/evaluation/run", status_code=202)
    def run_evaluation() -> dict[str, Any]:
        with state.eval_lock:
            if state.eval_running:
                raise HTTPException(409, "evaluation already running")
            state.eval_running = True

        def job() -> None:
            try:
                evaluate(state.runs, progress=state.bus.publish)
            except Exception:  # noqa: BLE001
                log.exception("evaluation failed")
                state.bus.publish({"type": "evaluation.failed"})
            finally:
                state.eval_running = False

        threading.Thread(target=job, daemon=True, name="evaluation").start()
        return {"running": True}

    # -- live stream ------------------------------------------------------------------------------
    @app.get("/api/stream")
    async def stream(request: Request) -> StreamingResponse:
        q = state.bus.subscribe()

        async def gen() -> AsyncIterator[str]:
            try:
                yield f"event: hello\ndata: {json.dumps({'type': 'hello'})}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {msg}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                state.bus.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # -- static dashboard -------------------------------------------------------------------------
    dist = settings.frontend_dist
    if (dist / "index.html").exists():
        if (dist / "assets").exists():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> Any:
            if path.startswith("api/"):
                return JSONResponse({"detail": "not found"}, status_code=404)
            f = dist / path
            if path and f.is_file():
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    return app



