# Architecture

## Data flow of one run

1. `POST /api/scenarios/{id}/replay` → `RunManager.create` loads a `SimulatedWorld`, builds the `ProviderSet` from
   `config/adapters.yaml`, saves a stub `Incident`, and starts the LangGraph graph in a worker thread.
2. Every node is wrapped: it marks its `AgentStep` running, persists the incident, publishes `agent.started` over SSE,
   runs the agent with a typed input, merges the typed output into the incident, writes audit rows, persists, publishes
   `agent.completed`. An agent exception flags the incident (`needs_attention`) and routes to `record`.
3. `human_approval` calls LangGraph `interrupt()`. The run is parked in an `InMemorySaver` checkpoint keyed by the incident
   id. `POST /api/incidents/{id}/approval` resumes it with `Command(resume=...)`. If the process restarted, the run is
   rehydrated: a new graph is built and `update_state(..., as_node="policy_gate")` restores the stored incident.
4. `execute` dispatches the action type to a provider method (`rollback`, `patch_config`, `scale`, `trigger_pipeline`,
   `create_pull_request`, …). Real adapters raise `ReadOnlyError` unless `allow_writes: true`.
5. `verify` re-runs Monitor health checks (replicas, pod reasons, SLO metrics back within baseline + 3σ, restart counters
   flat, pipeline green and tests passing for PR/rerun fixes).
6. `record` closes the incident and, when resolved, writes a normalized entry back into the history store.

## Routing

| From | Condition | To |
|---|---|---|
| monitor | nothing detected / service unknown | record |
| log_analysis | no logs **and** no other evidence | record (needs_attention) |
| diagnosis | top confidence < 0.30 | record (needs_attention) |
| remediation | no action could be built | record |
| policy_gate | auto / auto_logged | execute |
| policy_gate | require_approval (incl. alternative of a forbidden action) | human_approval |
| policy_gate | forbidden without alternative | record (blocked) |
| human_approval | rejected | record (rejected) |
| execute | failure (incl. read-only refusal) | record |

## LLM usage

| Agent | Payload (summaries only) | LLM may | Guard |
|---|---|---|---|
| LogAnalysis | top 8 masked pattern groups + counts | interpret | output must contain `interpretation` |
| Diagnosis | trigger, interpretation, top patterns, anomaly summaries, change metadata + ≤1,200-char diff, pod reasons, candidates | re-rank + explain | ids restricted to candidates, blended 65/35 with heuristic score |
| Remediation | top hypothesis + chosen action | write rationale | cannot change action type/target |

Payloads are capped at 12k characters. Providers are tried in `config/llm.yaml` order; failures fall through; the
deterministic mock answers last so the pipeline never blocks on an LLM.

## Choices worth knowing

* **SQLite** for incidents, audit and history: zero-ops, file-based, fine for an MVP. Alternatives considered:
  SQLite FTS5 (BM25 instead of TF-IDF), DuckDB (analytics on history), Postgres + pgvector or Qdrant/Chroma with
  sentence embeddings (semantic retrieval at scale).
* **TF-IDF** similarity (scikit-learn) is deterministic and offline; embeddings would improve recall on paraphrased symptoms.
* **Heuristic-first diagnosis** keeps results reproducible and explainable; the LLM adds narrative and tie-breaking.
