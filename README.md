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
- **Minimal diffs**: the Fix prompt (`build_fix_prompt`) asks for the
  smallest change that fixes the bug and keeps every other line, docstrings
  included, exactly as it was. Measured on 60 fixes, this took the diff from
  20 extra lines on average to none, and builds passing from 95% to 100%
  (`backend/training/eval_fix.py`).
- **No empty fixes**: gpt-oss-120b's hidden reasoning counts against the
  Fix call's token budget, so it could return an empty file. The budget is
  4,000 tokens, an empty answer is retried once, and an empty file is never
  judged or pushed.
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

## Fine-tuning (QLoRA distillation)

`backend/training/` distils the production model's reasoning and answers
(gpt-oss-120b) into LoRA adapters for Monitoring and Remediation, on Qwen3-4B
(runs locally in Ollama) and gpt-oss-20b (strongest that trains on a free Kaggle
T4): a data generator over two sample projects with automatic labels, an exporter,
and one Kaggle notebook that trains, evaluates and exports either student. A
fix-success benchmark (`training/eval_fix.py`) applies each model's proposal through
the production Fix agent and reruns the tests. Results: on single-token mutants
fine-tuning shows no gain, since every model finds them. On harder, cross-module
bugs in a webshop project, it significantly improves Monitoring (Qwen3-4B names the
failing test 73% → 100%, gpt-oss-20b follows the report format 59% → 95%), and both
fine-tuned students lead their base models on builds fixed (94% and 88% vs. 82% and
76%), though not significantly on 17 cases. Setting `LOCAL_LLM_MODEL` in `.env`
runs Monitoring and Remediation on the fine-tuned Qwen3-4B in Ollama (30–70 s per
call on a 4 GB GTX 1650). See `backend/training/README.md`.