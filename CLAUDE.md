# CLAUDE.md — Autonomous DevOps Multi-Agent System

Read this before changing anything. It is the contract that keeps sessions consistent.

## What this is
An MVP of an autonomous DevOps incident pipeline built from cooperating agents. It runs the full loop on a
simulated incident: **detect → analyze logs → diagnose → propose fix → policy gate → human approval → execute →
verify → record**. It runs offline with no cluster: simulated adapters replay scenario fixtures through the same
code paths that real adapters use.

## Layout
```
backend/            FastAPI app (Python 3.11, Pydantic v2, LangGraph, SQLite)
  app/models/       Incident record (single shared state + stored unit) and agent I/O models
  app/connectors/   Provider interfaces (base.py), SimulatedAdapter per provider, thin real adapters, registry
  app/agents/       monitor, log_analysis, diagnosis, remediation, policy_gate, executor, detectors, log_parser
  app/orchestrator/ LangGraph state graph + run manager (interrupt/resume for human approval)
  app/llm/          LLM fallback chain (OpenAI-compatible providers) + deterministic MOCK_LLM
  app/store/        SQLite (incidents, audit log, history), history loaders, TF-IDF similarity
  app/evaluation/   Baseline vs agent evaluation computed from real runs against ground truth
  app/api/          REST + Server-Sent Events
  tests/            pytest (policy gate, log parser, schema, end-to-end per scenario in MOCK_LLM mode)
frontend/           React + Vite + TypeScript + Tailwind + Recharts dashboard (dark theme)
scenarios/          One folder per scenario: scenario.yaml, logs.txt, metrics.json, change.json, k8s.json, pipeline.json
schema/             incident.schema.json (exported from the Pydantic model — never hand-edit)
config/             adapters.yaml, policies.yaml, llm.yaml, signatures.yaml, remediations.yaml
data/history/       seed history (synthetic incidents, runbooks) + dataset loader stubs
docs/               architecture, evaluation method, demo script
```

## Architecture boundaries (do not break)
1. **Connector layer.** Agents call only provider interfaces in `app/connectors/base.py`:
   `PipelineProvider`, `LoggingProvider`, `ObservabilityProvider`, `RepositoryProvider`,
   `OrchestrationProvider`, `QualityProvider`. Never call a tool/SDK/HTTP API from an agent.
   Adapters are selected in `config/adapters.yaml`. Real adapters (GitHub Actions, Prometheus, Loki, Kubernetes)
   are read-only by default (`allow_writes: false`) and disabled unless configured.
2. **Incident record** (`app/models/incident.py`) is the shared graph state and the stored unit.
   After changing it run `make schema` to regenerate `schema/incident.schema.json` (a test checks they match).
3. **Agents** are modules with a typed `*Input` and `*Output` Pydantic model, tied to the incident id.
   No free-form chat between agents. The orchestrator merges outputs into the incident.
4. **LLM context gets summaries only** (top log pattern groups, metric anomaly summaries, change metadata),
   never raw logs. Every LLM call has a deterministic fallback; `MOCK_LLM=true` makes everything deterministic.
5. **PolicyGate is rule-based, no LLM.** Rules live in `config/policies.yaml`; first match wins; the default is
   `require_approval` (fail safe). Forbidden actions are blocked and replaced by a safer alternative that is
   re-evaluated by the gate.
6. **Remediation proposes only.** Only the `execute` node performs actions, and only through providers,
   and only after the gate (and a human, when required) allowed it.
7. **History store.** Retrieval uses `use_for_retrieval=1` rows only. LogChunks and RCAEval are test-only and
   must never reach prompts or similarity search. Evaluation runs never write into history.

## Conventions
- Type hints everywhere; Pydantic v2 models for anything crossing a boundary. Python 3.11 compatible syntax.
- Times are timezone-aware UTC `datetime`. IDs: `INC-xxxxxx`.
- Scenario timestamps are simulated; wall-clock durations are measured separately (`duration_ms`).
- Every agent step, policy decision, approval and execution writes an audit row (`store.audit`).
- SSE events: `{type, incident_id, agent?, incident?}`; types: `incident.updated`, `agent.started`,
  `agent.completed`, `approval.required`, `evaluation.progress`, `evaluation.completed`.
- Frontend: no component library; Tailwind utility classes; colors from the slate/indigo/emerald/amber/rose palette.
- Adding a scenario: create `scenarios/<id>/` with the same files (see `scenarios/generate_fixtures.py`),
  include `ground_truth` and `accepted_fixes`, then add it to the e2e test parametrization automatically
  (tests discover all scenario folders).

## Commands
- `docker compose up --build` — whole app on http://localhost:8000
- `make install && make dev` — backend :8000 + Vite :5173 without Docker
- `make test` — pytest (MOCK_LLM forced)
- `make schema` — regenerate JSON Schema; `make fixtures` — regenerate scenario fixtures
