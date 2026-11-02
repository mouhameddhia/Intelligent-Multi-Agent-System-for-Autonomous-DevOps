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
- **The failing test file**: the Fix agent is shown a test file that really
  fails (pytest's failure markers, preferring `test_<target file>`), fetched
  at its full path. It used to take the first test path in the log, a passing
  one in 85% of 175 recorded builds.
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
T4): a data generator over two sample projects and a set of real bugs, with automatic labels, an exporter,
and one Kaggle notebook that trains, evaluates and exports either student. A
fix-success benchmark (`training/eval_fix.py`) applies each model's proposal through
the production Fix agent and reruns the tests. Results: on single-token mutants
fine-tuning shows no gain, since every model finds them. On harder, cross-module
bugs in a webshop project, it significantly improves Monitoring (Qwen3-4B names the
failing test 73% → 100%, gpt-oss-20b follows the report format 59% → 95%), while
builds fixed barely move (94% and 88% vs. 88% and 82% for the base models, not
significant on 17 cases). On 40 real bugs from RunBugRun (contest submissions,
none seen in training), fine-tuned Qwen3-4B names the failing test in 40 of 40
Monitoring reports against 18 for the base model (p < 0.001), and is
non-inferior to gpt-oss-120b within 10 points on builds fixed (96% vs. 93%,
pooled with the shop, 57 cases) and on Monitoring grounding (`training/compare.py`).
Setting `LOCAL_LLM_MODEL` in `.env`
runs Monitoring and Remediation on the fine-tuned Qwen3-4B in Ollama (30–70 s per
call on a 4 GB GTX 1650); Diagnosis, Fix and the judge stay on the large models.
Monitoring allows 4,000 generated tokens: at 2,500, long pytest logs cut 5 of 120
reports on the real bugs off before their confidence line. See
`backend/training/README.md`.