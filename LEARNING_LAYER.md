# Account-scoped personalized remediation

## What it does

An optional learning layer turns outcomes from an account's opted-in investigations into short, advisory guidance for future Remediation prompts. It does not train model weights. Monitoring, Diagnosis, the Judge, the approval interrupt, and the Fix agent keep their existing behavior.

## User control and privacy

- The **Personalized answers** switch appears on the Investigations page and is off by default. Its setting is stored in browser local storage per signed-in account and is sent with each new investigation.
- Enabling it requires an in-product notice. On consent, up to five recent closed investigations from that account are enrolled as an initial learning batch; future investigations started with the switch on can also produce learning records. No other history is enrolled.
- Records and policy versions use the authenticated account ID as their scope. Learning APIs require a signed-in session. A policy from one account cannot be selected for another account.
- Learning records contain bounded diagnosis summaries, proposed steps, decisions, feedback, and outcome data. Raw Jenkins console logs are not copied into the learning records. Structured summaries, steps, and feedback may be sent to the configured Judge and learner model providers for scoring and rule generation.
- Turning the switch off prevents guidance from entering new investigation prompts. Previously opted-in records remain stored and may continue to be evaluated; turning the switch off does not delete them.
- Learned text is advisory. It is validated before promotion, capped when rendered, and cannot change approval or apply a fix. Human approval remains required.

## Workflow

1. A new investigation records whether personalization was enabled and the policy version actually used.
2. On first opt-in, the authenticated account's five most recent closed investigations are marked opted-in and queued for structured record creation. Later, when a future opted-in incident receives a decision or engineer feedback, a daemon worker creates or updates its record; it does not block the API request.
3. The evaluator assigns an outcome reward from the strongest available signal (rebuild result, feedback, decision, then Judge scores). Unknown outcomes are excluded.
4. After the configured batch threshold (default 5), the learner may propose operations. Code validates rules, evidence, categories, support, and limits before publishing a version.
5. A future opted-in investigation reads the active policy for that account and appends relevant guidance to the Remediation prompt. Policy reads are cached for 60 seconds; unavailable storage falls back to the original prompt.

## Business and performance metrics

Each incident already stores `pipeline_duration_ms` and compact per-agent `llm_performance` (provider, model, tokens, latency). It now also stores `personalization_enabled` and `policy_version_used`. The authenticated `GET /api/learning/metrics` endpoint reports opted-in record counts and, overall and per policy version:

- approval rate among decided incidents;
- rebuild pass rate among incidents with known rebuild results;
- first-fix success among PipeGuard-applied fixes with a reported rebuild result.

Rates are descriptive and should be presented with their denominators. They are not causal evidence by themselves: incident mixes may differ between policy versions. Use the offline replay command below for a paired exploratory comparison; it uses LLM judgments and is a smoke test, not a definitive benchmark.

## Controls and API

The authenticated endpoints are under `/api/learning`:

- `GET /policy` and `GET /versions` inspect only the signed-in account's policy;
- `GET /metrics` reports that account's learning/business metrics;
- `POST /opt-in` enrolls up to five recent closed incidents after the user accepts the in-product notice;
- `POST /run` evaluates and learns from that account's opted-in records;
- `POST /rollback` restores that account's previous policy version.

Set `LEARNING_ENABLED=false` in backend configuration to disable learning and injection globally, including manual learning runs. The UI switch only controls personalization for that account's future investigations. If MongoDB or the policy lookup is unavailable, the existing Remediation prompt is used unchanged.

Run the learning tests from the project root:

```bash
python -m pytest backend/tests -q
```

Offline replay (configured MongoDB and model keys required):

```bash
cd backend
python -m agent_system.learning.replay --version 2 --limit 10 --json replay.json
```
