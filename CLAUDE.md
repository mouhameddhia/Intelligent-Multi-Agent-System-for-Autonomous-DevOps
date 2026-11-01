# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

PipeGuard: investigates failed Jenkins builds with a LangGraph multi-agent workflow, proposes remediation, and applies a fix only after explicit human approval. React/Vite dashboard + FastAPI backend. See `README.md` for the full API reference and env-var list.

## Commands

Backend (run from repo root, Windows/PowerShell, venv in `.venv`):

```powershell
python -m pip install -r requirements.txt
python -m pip install -r backend/agent_system/requirements.txt   # adds pymongo (optional persistence)
python -m uvicorn app.main:app --app-dir backend --reload --port 8000
```

Frontend (from `frontend/`): `npm install`, `npm run dev` (port 5173), `npm run build`, `npm run lint`.

Backend tests (pytest, listed in `requirements.txt`), from the repo root: `python -m pytest`, or one test with `python -m pytest backend/tests/test_learning_learner.py::test_name`. `backend/tests/conftest.py` blanks the Mongo URI and API keys so tests never reach real services. There is no backend linter.

## Architecture

Two Python packages under `backend/`, both importable only with `backend` on the path (hence `--app-dir backend`):

- `backend/app/` — the real API (`app.main:app`, port 8000). `api/` routers (jenkins, prometheus, github, investigation) over `connectors/` that wrap the external services. CORS is restricted to the Vite origins (localhost/127.0.0.1:5173).
- `backend/agent_system/` — the LangGraph investigation engine, imported by `app/api/investigation.py`. It also has its own legacy standalone FastAPI app (`agent_system/main.py`, `AGENT_PORT=8001`); the frontend does not use it.

### Investigation graph (`agent_system/orchestrator/graph.py`)

Linear pipeline: `monitoring → diagnosis → remediation → structure_steps → llm_judge → human_approval → (apply_fix | END)`. State is `InvestigationState` (`orchestrator/state.py`); node implementations live in `agents/`.

- `human_approval` uses LangGraph `interrupt()`. `POST /api/investigate` (and `/stream`) runs until the interrupt and returns `awaiting_approval`; `POST /api/investigate/decision` resumes the checkpoint by `incident_id`. Anything other than `"approve"` ends the run without applying a fix.
- The checkpointer is in-memory (`InMemorySaver`), so pending approvals are process-local — a restart or multiple workers loses them.
- `/api/investigate/stream` emits one SSE event per completed node; the frontend (`frontend/src/App.jsx`, `reportSections.js`) renders progress from these.
- The judge (`agents/judge.py`) is an independent LLM with its own key/model (`JUDGE_API_KEY`, `JUDGE_MODEL`) so its audit is not the same model as the investigation agents. Its verdict is shown to the engineer but never affects routing.
- `historical_match.py`, `agents/memory.py`, `db.py`: MongoDB-backed incident storage, historical matching, engineer feedback and judge calibration. All of this is disabled when `MONGODB_URI` is empty.
- With no Jenkins credentials/evidence, agents fall back to the synthetic scenario in `agent_system/data.py` (`USE_SYNTHETIC`).
- Settings come from `agent_system/config.py` (pydantic-settings), reading the repo-root `.env`. LLMs are Groq-backed (`GROQ_API_KEY`, `LLM_MODEL`).

### Learning layer (`agent_system/learning/`, routes in `app/api/learning.py`)

Offline loop that never changes model weights: closed incidents become `LearningRecord`s → evaluator (deterministic reward + `JUDGE_MODEL` rubric) → learner (`LLM_MODEL` proposes operations, code validates them) → versioned `PolicyVersion` (active + previous, rollback) → `injection.get_guidance()` appends an advisory section to the Remediation human message. Full detail in `LEARNING_LAYER.md`.

- Recording/evaluation/learning run in a background thread started by `schedule_record` (called from `decide()` and `submit_feedback()`), never in the request path. Everything is best-effort and silent on failure.
- With no usable policy (empty, disabled via `LEARNING_ENABLED=false`, or any error) the Remediation prompt must stay byte-identical to the original; `tests/golden/remediation_prompts.json` guards this. Don't edit those prompts without regenerating it deliberately.
- Learning code must not be referenced from `agents/fix.py` or `orchestrator/graph.py` (a test enforces it): the policy is advisory and cannot bypass `human_approval`.
- Lessons come from structured record fields only, never raw console logs. Every persisted model carries `scope="global"`.

### Frontend

Single-page React 19 app (`frontend/src/App.jsx`). The backend URL is hard-coded as `API_URL` (`http://127.0.0.1:8000`) in `App.jsx`.
