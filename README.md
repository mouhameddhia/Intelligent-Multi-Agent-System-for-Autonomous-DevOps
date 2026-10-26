cd /Users/spooky/PipeGuard
source .venv/bin/activate

## Independent LLM judge before auto-fix push (Gemini)

Before PipeGuard pushes an auto-generated fix to GitHub, a second, independent
model (Gemini) reviews the proposed diff against the original file, the
bug diagnosis, the remediation plan and the test file — separate from the
Groq model that generated the fix, so the same model is never grading its
own work.

- Code: `backend/agent_system/agents/judge.py` (the reviewer) and
  `backend/agent_system/agents/fix.py` (`run_apply_fix`, which calls the
  judge immediately after generating the fix and before the GitHub `PUT`).
- Config: `GEMINI_API_KEY` and `GEMINI_MODEL` in `.env` (see `config.py`).
  The Generative Language API must be enabled on the Google Cloud project
  the key belongs to.
- **Fails closed**: a missing API key, a network/HTTP error, a blocked or
  empty Gemini response, or a response with no parseable
  `JUDGE_VERDICT: pass`/`fail` marker are all treated as a rejection. A
  judge outage can only ever block a push, never let one through
  unreviewed.
- The existing human-approval step (`human_approval` in
  `orchestrator/graph.py`) is unchanged and still runs before `apply_fix`
  — the judge is an additional, internal gate inside that same node, not a
  replacement for human approval.
- Tests: `backend/agent_system/tests/test_judge.py`,
  `test_apply_fix_judge_gate.py` and `test_human_approval_gate.py`. Run
  with `cd backend && pytest agent_system/tests`.

## Run locally with Docker

The backend, MongoDB and a Jenkins (with Python and pytest) run in Docker; the
frontend runs with Vite. Useful on Windows, where Smart App Control can block
the backend's compiled Python packages.

1. Create `.env` at the repo root (never committed):
   ```
   GROQ_API_KEY=...
   GEMINI_API_KEY=...
   MONGODB_URI=mongodb://mongo:27017
   MONGODB_DB=pipeguard
   ```
2. `docker compose up -d --build` starts backend (:8000), MongoDB (:27017) and
   Jenkins (:8081; admin password:
   `docker compose exec jenkins cat /var/jenkins_home/secrets/initialAdminPassword`).
   `docker compose --profile tools up -d` adds Prometheus, Grafana, Loki and SonarQube.
3. `cd frontend && npm install && npm run dev`, then open http://localhost:5173.

The backend runs inside Docker, so URLs typed into the UI must be reachable
from the container: `http://jenkins:8080`, not `localhost`. The full list is at
the bottom of `docker-compose.yml`. Code edits under `backend/` reload automatically.

## LLM call logging

Monitoring and Remediation record each call's exact prompt, raw output, token
usage and latency (`agent_system/llm_log.py`). Investigations store them on the
incident in MongoDB as `llm_calls`, which is the training data for fine-tuning.

## Fine-tuning (QLoRA on gpt-oss-20b)

`backend/training/` distils the production model (gpt-oss-120b) into a LoRA
adapter on gpt-oss-20b for Monitoring and Remediation: a mutation-based data
generator with automatic labels, an exporter, and a Kaggle notebook that trains
and evaluates the adapter. See `backend/training/README.md`.