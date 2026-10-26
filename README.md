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