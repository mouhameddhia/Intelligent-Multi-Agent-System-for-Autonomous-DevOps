# Learning layer

The learning layer lets PipeGuard improve its remediation advice from its own
history **without changing any model weights**. It records closed incidents,
evaluates them, distills lessons into a versioned policy, and adds the active
policy to the Remediation agent's prompt on the next incident.

## Guarantees

- **Offline.** Recording, evaluation and learning run in a background thread
  (or on request through the API). They never run inside an investigation
  request and never block or fail one.
- **Silent fallback.** If the registry is empty, unreachable, disabled or
  errors in any way, Remediation receives exactly the prompt it received before
  this layer existed. A golden-prompt test enforces this byte for byte.
- **Advisory only.** The policy adds text to the Remediation prompt. It does not
  touch `human_approval` or `agents/fix.py`, and a test checks that neither
  module references the learning package. Every proposed action still needs
  human approval.
- **No raw logs.** Records and lessons are built from structured fields: the
  diagnosis summary, proposed steps, decision, fix result, rebuild result,
  feedback and judge scores. Console logs are never copied into them.
- **Scoped.** Every record, rule, policy version and evaluation carries
  `scope`, currently always `"global"`, so per-user scopes can be added later
  without a migration.

## Data flow

```text
investigation ──► engineer decides ──► (optional) engineer submits feedback
                       │                          │
                       └────────────┬─────────────┘
                                    ▼
                 recorder: build LearningRecord  (background thread)
                                    ▼
                 evaluator: outcome_reward + rubric scores
                                    ▼
          5 new evaluated records? ── no ──► wait
                                    │ yes (or POST /api/learning/run)
                                    ▼
                 learner: LLM proposes operations ─► validated in code
                                    ▼
                 new PolicyVersion (rules + priors), active + previous kept
                                    ▼
   next investigation: Remediation prompt gets "Learned guidance ..." section
   and the incident stores policy_version_used
```

## Code map

All under `backend/agent_system/learning/` unless noted.

| File | Role |
| --- | --- |
| `models.py` | Pydantic models: `LearningRecord`, `EvaluationResult`, `Rule`, `PolicyVersion`, `PriorCount`, `LearningRunResult` |
| `classify.py` | Deterministic `failure_category`, `failure_signature`, `fix_action_type` |
| `store.py` | Record storage (`learning_records`); Mongo and in-memory implementations |
| `recorder.py` | Builds a record when an incident closes; `schedule_record` runs it in a thread |
| `export.py` | Preference pairs as JSONL for possible future DPO (nothing is trained) |
| `evaluator.py` | `outcome_reward` and the LLM rubric |
| `learner.py` | Operation validation, promotion, priors, the learning run |
| `registry.py` | Versioned policy storage, rollback, cached active-policy lookup |
| `render.py` | Renders the prompt section, capped at about 800 tokens |
| `injection.py` | `get_guidance(state)` used by Remediation; replay override |
| `trigger.py` | Runs evaluate then learn, one run at a time |
| `metrics.py` | Per-policy-version outcome rates |
| `replay.py` | Offline comparison of a policy version against no policy |
| `backend/app/api/learning.py` | The `/api/learning` router |

Existing code touched: `db.py` (`get_collection`), `config.py` (two settings),
`agents/remediation.py` (guidance injection), `orchestrator/state.py`
(`policy_version_used`), and `app/api/investigation.py` (a `schedule_record`
call after a decision and after feedback, and `policy_version_used` stored on
the incident). Existing routes and their request and response shapes are
unchanged.

## MongoDB collections

| Collection | Contents |
| --- | --- |
| `learning_records` | One `LearningRecord` per closed incident, keyed by `incident_id`, with its `evaluation` once evaluated |
| `learning_policies` | One document per policy version, keyed `<scope>:<version>`, rules and priors embedded |
| `learning_state` | Per scope, the incident ids already learned from |
| `incidents` | Unchanged, plus a `policy_version_used` field |

Everything is a no-op without `MONGODB_URI`.

## Records

A record is created or updated whenever a decision is made or feedback arrives,
so one incident has one record that fills in over time.

| Field | Source |
| --- | --- |
| `failure_category` | Classifier over the diagnosis report, `incident_type`, and file names |
| `failure_signature` | Hash of category, `incident_type` and touched file names |
| `fix_action_type` | `code_patch` if a fix was applied, otherwise keywords over the proposed steps |
| `human_decision` | approve or deny; `reason` is empty because the decision route has no reason field |
| `fix_applied` | `fix_result.applied`; false on a denial |
| `rebuild_result` | `pass` or `fail` only if feedback exists, otherwise `unknown` |
| `policy_version_used` | Copied from the incident |

Categories are `test_assertion`, `dependency`, `build_compile`, `infra_flaky`,
`config` and `other`. Fix types are `code_patch`, `rollback`, `restart`,
`dependency_update`, `config_change`, `rerun` and `other`. The classifier is
keyword based, so it is deterministic and cheap but coarse.

The rebuild result is only what the engineer reports in feedback. The feedback
model defaults `build_passed` to false, so a stored `false` cannot be told apart
from "not reported". The presence of a feedback object is the signal.

## Evaluation

`outcome_reward` is in [-1, 1] and comes from the first available signal:

| Order | Signal | Reward |
| --- | --- | --- |
| 1 | Rebuild result | +1.0 pass, -1.0 fail |
| 2 | Engineer feedback (`liked_fix`) when the rebuild is unknown | +0.6 / -0.6 |
| 3 | Decision | +0.3 approve, -0.5 deny |
| 4 | Mean judge score | -0.2 to +0.2 |

An approval whose fix was not applied is skipped at tier 3. A record with no
signal has `outcome_reward = None`: its outcome is unknown and it is excluded
from learning (and costs no LLM call).

The LLM part uses `JUDGE_MODEL` and returns two separate 0-100 scores,
`diagnosis_quality` and `fix_quality`, with a short rationale. It is advisory;
if the model is unavailable the scores stay empty and the reward is unaffected.
An evaluation records a fingerprint of the outcome it saw, so later feedback
marks it stale and it is redone.

## Learning

Once at least `LEARNING_BATCH_SIZE` (default 5) evaluated records have not yet
been used, the learner sends up to 20 of them, oldest first, plus the current
rules to `LLM_MODEL`. The model replies with operations:

```json
{"operations": [
  {"op": "ADD", "text": "...", "failure_category": "dependency", "evidence_incident_ids": ["..."]},
  {"op": "EDIT", "rule_id": "...", "text": "..."},
  {"op": "UPVOTE", "rule_id": "...", "evidence_incident_ids": ["..."]},
  {"op": "DOWNVOTE", "rule_id": "...", "evidence_incident_ids": ["..."]},
  {"op": "REMOVE", "rule_id": "...", "reason": "..."}
]}
```

The model only proposes. Code validates each operation and drops invalid ones
individually, listing them in the run result:

- exact shape; unknown fields or operations are rejected
- rule text: one line, 15 to 300 characters, and no mention of approvals,
  reviews, prompts, instructions or links
- cited incidents must exist in the batch and match the rule's category
- ADD and UPVOTE may cite only incidents with a positive reward; DOWNVOTE only
  negative ones
- no duplicate rules, at most 30 live rules and 15 operations per run

Counts and status are then computed in code, not by the model:

- `support_count` is the number of distinct cited incidents; `success_count`
  the positive ones. Failures are the difference.
- A candidate becomes `active` at `support_count >= 2` with more successes than
  failures. An active rule whose failures overtake its successes is retired
  automatically. `REMOVE` retires a rule explicitly; retired rules are kept.
- **Priors** are success and failure counts per `(failure_category,
  fix_action_type)`, recomputed from the sign of every evaluated reward.

Records are marked used only after the LLM answered, so a failed call is
retried at the next trigger. Rules are learned only from positively rewarded
incidents; failures lower rule counts and show up in the priors.

The learner runs when a record is recorded and enough are waiting, or through
`POST /api/learning/run`. One run at a time per process.

## Policy registry

Each run that changes the rules or priors publishes a new immutable
`PolicyVersion` with the next number. The registry keeps one `active` and one
`previous` version; older ones become `archived` and cannot be rolled back to.
`rollback` archives the active version and reactivates the previous one. A crash
mid-publish leaves two active versions (the newest wins); a crash mid-rollback
leaves none (no guidance), never the version being rolled back.

## Remediation integration

Before Remediation calls its model, `get_guidance(state)` classifies the
diagnosed failure, loads the active policy and renders a section titled
**Learned guidance from past incidents (advisory)**. It contains the priors for
that category, ranked by success rate, and the active rules for that category,
ranked by net outcome, trimmed to about 800 tokens. Candidate and retired rules
are never shown. The section is appended to the human message after any
historical context, and the evidence prompt gains one rule telling the model to
treat it as general advice, not a past incident.

If anything is missing or fails, the result is empty and nothing changes. When
guidance is used, `policy_version_used` is set in the graph state and stored on
the incident, and from there on the learning record.

The active policy is read through a 60-second in-process cache (failures are
cached as "no policy"), so an unreachable database costs at most one lookup per
minute. Publishing or rolling back in the same process clears it. Another
backend process sees a change within the TTL.

## API

All routes are under `/api/learning` and have no authentication, like the rest
of the API.

| Route | Purpose |
| --- | --- |
| `GET /policy` | Active policy. `?failure_category=test_assertion` also returns the exact section Remediation would receive |
| `GET /versions` | All versions, newest first, with rule counts. `?include_rules=true` adds the rules |
| `POST /run` | Evaluate and learn now. Body `{"force": true, "wait": false}`; both default as shown. Returns 202 and runs in the background, or with `wait` returns the run result. 409 if a run is in progress |
| `POST /rollback` | Restore the previous version; 409 if there is none |
| `GET /metrics` | Record counts plus rates overall and per policy version |

Storage read errors return 503. Without MongoDB the read routes return empty
results.

## Metrics

Grouped by `policy_version_used`; records made while no policy was active group
under `null`. A rate is `null` when its denominator is zero.

- `approval_rate`: approved / (approved + denied)
- `rebuild_pass_rate`: rebuilds that passed / rebuilds with a known result,
  including manual fixes
- `first_fix_success_rate`: among incidents where PipeGuard applied a fix, the
  share that rebuilt green

These are descriptive. With few incidents per version they are noisy, and
nothing controls for the mix of failures between versions.

## Replay

Replay checks whether a policy version helps, without touching production data.

```bash
cd backend
python -m agent_system.learning.replay --version 2 --limit 10 --json replay.json
```

It selects resolved incidents (rebuild passed) that were not used to build that
version or any earlier one, re-runs Remediation on each with the version's
guidance and again with none, and scores both sets of steps against what
actually fixed the incident using `JUDGE_MODEL`. It prints a per-incident table,
mean scores and a better/equal/worse count over incidents that actually received
guidance. Nothing is written to MongoDB. It needs `MONGODB_URI`, `GROQ_API_KEY`
and `JUDGE_API_KEY`, and costs two Remediation and two judge calls per incident.
Scores are single LLM samples: treat small differences as noise, and treat the
result as a smoke test, not evidence.

## Preference-pair export

```bash
cd backend
python -c "from agent_system.learning.export import export_preference_pairs as e; print(e('pairs.jsonl'))"
```

Writes approved-vs-denied and passed-vs-failed-rebuild pairs, built only between
incidents with the same failure signature (pass `group_by="failure_category"`
for wider grouping). Each line has `prompt`, `chosen`, `rejected`, the incident
ids and the basis. Nothing is trained.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `LEARNING_ENABLED` | `true` | Background evaluation and learning, and prompt injection. `false` leaves Remediation exactly as before; `POST /run` still works |
| `LEARNING_BATCH_SIZE` | `5` | New evaluated records needed to trigger a learning run |
| `LLM_MODEL`, `GROQ_API_KEY` | existing | The learner |
| `JUDGE_MODEL`, `JUDGE_API_KEY` | existing | The evaluator rubric and replay scoring |
| `MONGODB_URI`, `MONGODB_DB` | existing | Required for anything to persist |

## Known limitations

- Background evaluation and learning calls share Groq rate limits with
  investigations; `agents/judge.py` notes a tight limit on the judge model. A
  background run can make a concurrent judge call fail, which degrades that
  verdict but not the investigation. Set `LEARNING_ENABLED=false` to stop it.
- One learning run at a time per process; multiple backend workers are not
  coordinated, matching the in-memory LangGraph checkpointer.
- A record already learned from is not relearned if its reward later changes;
  priors are recomputed, rule counts are not.
- `human_decision.reason` stays empty until the decision route accepts a reason.
- Loading the active policy reads every stored version; fine for a long time,
  but it grows with the number of learning runs.
- The classifier is keyword based and the rule-text filter is deliberately
  conservative, so some good lessons will be rejected.

## Testing

```bash
python -m pip install -r requirements.txt   # includes pytest
python -m pytest
```

Tests use fake LLMs and in-memory stores, and `backend/tests/conftest.py` blanks
the MongoDB URI and API keys so a real `.env` is never reached.
`backend/tests/golden/remediation_prompts.json` holds the Remediation prompts
captured before the layer existed.
