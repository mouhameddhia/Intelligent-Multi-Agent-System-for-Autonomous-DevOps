# PipeGuard — Testing, Results, and Performance Evaluation

**Purpose:** a maintained evidence ledger for presentation section 6 and engineering decisions. Update it whenever a feature is integrated, a defect is fixed, or a benchmark/test is run. Keep both the baseline and changed result so regressions and gains are visible over time.

**Evidence labels**
- **Measured here:** run against the current project during the dated work recorded below.
- **Project-reported:** results documented by a teammate or earlier project work; retained as reported, not independently repeated in this session.
- **Not measured:** there is no comparable recorded result yet. This is not a zero score.

## Current scorecard

| Area | Latest recorded result | Evidence / interpretation |
|---|---|---|
| Default agent route (Groq) | 3.024 s agent-call total; 3.236 s two-agent end-to-end; 1,673 input and 841 output tokens | **Measured here**, one synthetic paired benchmark. |
| Optional QLoRA route (Ollama) | 135.491 s agent-call total; 135.611 s end-to-end; 1,602 input and 962 output tokens | **Measured here**, same synthetic failure case. About 42× slower and 14% more output tokens in this one run. Both routes identified the failing test and target function. |
| QLoRA answer structure | 7 remediation steps vs 5 Groq; confidence markers 95/90 vs 100/85 | One sample each; model-reported confidence is not calibrated. Does not establish better remediation quality. |
| Basic QLoRA toggle/fallback regression | 3 focused tests passed | **Measured here**, 2026-11-02. |
| Frontend production build | Passed; 272 modules; 502.24 kB JavaScript bundle | **Measured here**, 2026-11-02. Vite reports the existing >500 kB chunk warning. |
| Chatbot retrieval / answer quality | Harness defined for 22 labeled retrieval queries across 9 topics, plus 5 abstention questions; no result report found | **Not measured in this ledger.** The harness writes `RAG_EVALUATION.md`; run it before presenting numerical chatbot quality claims. |
| Investigations page request path | Optimized 2026-11-02: page load reduced from 2 HTTP requests / 4 MongoDB operations to 1 HTTP request / 2 MongoDB operations; response now projects dashboard fields only. | **Measured structurally from implementation**, not a wall-clock benchmark. No live DB timing or payload-size comparison available yet. |
| Personalized remediation opt-in | Default/off path preserves the current prompt; on path selects only the signed-in account's policy and adds advisory guidance when an active policy exists. | **Measured in regression tests**, including cross-account isolation. Live provider latency, token delta, and outcome lift are not measured yet. |
| Personalized remediation paired outcome replay | Offline replay harness exists, but no production-data replay result is recorded. | **Not measured**; requires an active policy, resolved held-out account incidents, and configured model/judge access. |
| Learning metric and guidance unit tests | 25 dependency-independent tests passed in this session (`test_learning_metrics.py`, `test_learning_render.py`, `test_learning_classify.py`). | **Measured here**, 2026-11-02. These validate calculations/rendering/classification, not treatment quality or production outcomes. |
| Full backend suite in this session | Not collected: normal pytest startup fails in the installed LangSmith/urllib3 plugin; with plugin autoload disabled, test collection fails because `langgraph` and `langchain_groq` are missing. | No backend-wide pass rate can be claimed from this session. |
| Live investigation, fix, and rebuild success | Historical injected-defect check: local suite had 1 failing assertion and 2 passing tests. Jenkins build #35 succeeded before the defect push; post-push Jenkins result was not recorded. | Historical project context; the 1/2 result is a tiny check, not a broad reliability rate. |

## Model and workflow benchmarks

### Runtime route: Groq baseline vs optional local QLoRA

**Date:** 2026-11-02 · **Evidence:** measured here · **Sample:** one paired synthetic `PipeGuard-Lab` failure (`test_calculate_total`, expected 30, received 31).

| Metric | Groq baseline | QLoRA enabled | Change / reading |
|---|---:|---:|---|
| Agent-call latency | 3.024 s | 135.491 s | QLoRA ≈ 44.8× slower on agent calls |
| End-to-end two-agent workflow | 3.236 s | 135.611 s | QLoRA ≈ 41.9× slower |
| Input tokens | 1,673 | 1,602 | −4.2% |
| Output tokens | 841 | 962 | +14.4% |
| Monitoring / Remediation latency | 1.341 / 1.683 s | 40.036 / 95.455 s | Local generation dominates latency |
| Failing test and target function identified | Yes / Yes | Yes / Yes | Both passed the two recorded checks |
| Structured remediation steps | 5 | 7 | Count only; correctness was not independently judged |

Each route's Remediation agent received that route's Monitoring output, so the second-stage prompts were not byte-identical. QLoRA used `keep_alive=0`, including unload overhead. This is a smoke comparison, not a statistically reliable evaluation or a successful live code fix. No independent LLM judge or post-fix build result was recorded. Raw measurements: [`backend/training/QLORA_BENCHMARK_REPORT.md`](backend/training/QLORA_BENCHMARK_REPORT.md).

**Assessment:** local QLoRA ran successfully and may offer local execution/data-control benefits, but the measured laptop run was much slower and showed no quality win on this case. Groq remains the default; the optional local route falls back to Groq when Ollama is unavailable. Cost, throughput at 10,000+ investigations, and hardware-scale economics have **not** been measured here.

### Fine-tuning evaluation: colleague-reported benchmark results

These figures are transcribed from [`backend/training/README.md`](backend/training/README.md) and the collaborator's evaluation work. They are useful prior evidence, but have **not** been rerun independently as part of this ledger. Dataset performance should not be presented as proof of production superiority.

| Dataset / question | Reported comparison | Takeaway and limitation |
|---|---|---|
| `sample_project`, 10 held-out mutants per agent | Qwen3-4B Monitoring format 100%→100%; Remediation format 90%→90% (base→fine-tuned). Fix success was 10/10 for both Qwen variants and teacher under the production prompt. | No measured fix-success gain on this easy set. |
| `sample_shop`, 39 held-out examples from 10 unseen functions | Qwen3-4B Monitoring grounding 73%→100% (p=0.03); Remediation grounding 94%→100% (p=1.0). Fix success: fine-tuned Qwen 15/17 (88%) vs base 14/17 (82%), p=1.0. | Significant Monitoring grounding gain on this set; no significant Remediation or fix-success gain. Small sample. |
| RunBugRun, 40 real short contest bugs unseen in training | Failing-test identification: Qwen3-4B base 18/40 (45%) vs fine-tuned 40/40 (100%). Fix success: base 38/40 (95%), fine-tuned 40/40 (100%), teacher 38/40 (95%). | Strong reported Monitoring gain; fix-success difference was not significant (2 vs 0, p=0.5). Limited to short single-file contest programs. |
| Pooled shop + RunBugRun fix success, 57 cases | Fine-tuned Qwen3-4B 96% vs teacher 93%; reported non-inferior within a 10-point margin. | Supports comparable performance on these benchmark sets, not superiority or generalization to large repositories/infrastructure failures. |

## Other recorded checks and known measurement gaps

| Feature / workflow | What is recorded | What should be measured next |
|---|---|---|
| Judge / Fix workflow | The training README reports historical benchmark-driven Fix-agent changes: with the old prompt, 3/60 runs broke builds; minimal-diff prompting produced 60/60 passing in that experiment. On 175 recorded builds, test-file selection was initially wrong 148 times (85%), then correct 175/175 after the selection fix. | Preserve the same cases and report before/after denominators, build pass rate, exact restoration, and regression count when the Judge/Fix implementation changes. These reported figures are not a production-wide success rate. |
| Personalized remediation | A live same-build pair was attempted on 2026-11-02, but the off run was persisted as enabled and the on run used no policy (`policy_version_used=null`), so it is not a valid treatment comparison. This session fixed the frontend state capture, batched enrollment to avoid a lost trigger, surfaced policy status/manual build, and added a visible applied-policy result. Focused learning API/recorder tests pass; LangGraph-dependent route tests remain unavailable because `langgraph` is missing. | Complete a live pair after the active account policy is built; confirm `personalization_enabled=false` on baseline and non-null `policy_version_used` on treatment. Measure quality and runtime across more than one case. |
| Chatbot “most recent incident” and remediation advice | Historical inconsistent answers were observed; no quantified before/after evaluation is stored. | Use a labeled set with latest-incident ground truth and incident-ID checks; measure exact incident match, factual faithfulness, citation correctness, and abstention on unsupported details. The existing RAG harness provides a starting point. |
| Integration refresh persistence, investigation counts, Grafana embed | Fixes were discussed in prior work, but no reproducible numerical before/after measurements are retained. | Add targeted regression cases and record observed counts, refresh survival, embed behavior, and pass/fail evidence after the next relevant change. |
| End-to-end CI failure-to-fix cycle | One injected failure was checked locally; the post-push Jenkins outcome is unknown. | Record build ID, injected defect, expected failure, agent diagnosis, proposed fix, actual test result, and subsequent pipeline outcome for each live run. |
| API security boundaries | Jenkins/GitHub/Loki/Prometheus/SonarQube/Kubernetes/Grafana proxy routes previously accepted unauthenticated requests; they now require a PipeGuard bearer session. The legacy standalone agent endpoint also requires a session and binds to loopback when launched directly. Seven unauthenticated proxy cases are covered by a focused test. `npm audit --offline` reports 0 frontend dependency advisories from its cached audit data. | Run a full backend dependency audit after installing `pip-audit` and resolving/pinning backend dependencies; perform a deployment-level network/SSRF review because these connectors intentionally reach user-configured private services. |

## Objectives: current evidence status

- **Incident analysis and remediation support:** demonstrated in the synthetic QLoRA/Groq example and benchmark suites; broad live-system success rate is not established.
- **Historical incident retrieval and chatbot answers:** evaluation harness exists, but its current score report has not been produced/recorded here.
- **Optional model choice with safe fallback:** focused toggle/fallback regression passed (3 tests); a larger live-model outage matrix has not been recorded.
- **Personalized remediation:** integration is present and prior project work reports regression coverage; paired outcome and live performance evaluation remain pending. This session passed only the dependency-independent metrics/render/classify subset (25 tests); prompt injection/replay tests could not be collected in the current environment.
- **Performance and quality claims:** one runtime comparison and collaborator-reported model benchmarks are available; more paired samples and live end-to-end outcomes are needed for robust claims.

## Update protocol

For every meaningful change, append a dated entry below and update the scorecard only when a comparable result exists. Record the baseline and changed revision/configuration, test or benchmark command, sample size, numerator/denominator, latency and token/cost metrics where relevant, evidence link, and limitations. Keep a result as “not measured” until it has been run. Never compare unlike datasets or prompts as if they were a controlled pair.

### New evaluation entry template

```markdown
### YYYY-MM-DD — <feature, fix, or integration>
- Change / revision:
- Baseline and treatment:
- Test/benchmark and command:
- Sample and environment:
- Metrics (include numerator/denominator and units):
- Result and before→after:
- Verdict (improved / unchanged / regressed / inconclusive):
- Evidence (report, run/build ID, logs):
- Limitations / follow-up:
```

## Change log

### 2026-11-02 — Initial ledger
- Seeded with the available local QLoRA runtime comparison, existing fine-tuning evaluation results, focused toggle/fallback checks, frontend build status, historical pipeline test context, and current evaluation gaps.
- No RAG evaluation was run while creating this ledger; no new application tests were run for this documentation-only change.

### 2026-11-02 — Investigations page load optimization
- Change: consolidated dashboard incidents and KPI loading into one endpoint; selected only fields displayed by the dashboard; replaced the app's unused initial 20-document fetch with a count-only request.
- Baseline → changed: opening the page used 2 parallel HTTP requests and 4 MongoDB operations (two list reads and two counts) → 1 HTTP request and 2 MongoDB operations (one projected list and one count). App startup's separate list read was reduced to a count-only read.
- Verification: backend modules passed `python -m py_compile`; frontend `npm run build` passed (272 modules, 499.89 kB JS bundle).
- Limitation: no live MongoDB timing or before/after payload bytes were captured, so no latency percentage is claimed.

### 2026-11-02 — Account-scoped learning layer integration
- Source reviewed: GitHub branch `learning-layer`, commits `2ae4868` (learning implementation) and `d020443` (documentation). Its engine was integrated without branch-switching the working tree; unrelated changes to Jenkins/Trivy and the existing frontend were not imported.
- Safety gap in the source branch: production policy scope was global and `/api/learning` routes had no authentication. The integrated route now scopes records, policy versions, metrics, and rollback to the authenticated account; Remediation requires the per-investigation `use_personalized_answers` flag. The switch is off by default. After explicit consent, the API enrolls at most five recent closed investigations for that account as an initial batch; no broad historical backfill occurs.
- Before → after prompt behavior: with the switch off, an active stored policy does not alter the prompt and is not recorded as used; with the switch on and an active account policy, an advisory guidance section is added and the policy version is stored on the incident. The synthetic in-memory acceptance test generated a policy from 5 opted-in examples; this is a code-path check, not a live model-quality result.
- Workflow measurements: Remediation calls are now included in per-agent latency/token summaries both when source-inspection tools are available and on the no-evidence fallback path; no live provider timing sample was available in this workspace.
- Final verification: **248 backend tests passed** (includes bounded opt-in enrollment and unauthenticated-route checks); backend `py_compile` passed; `git diff --check` passed; frontend build passed (272 modules, 503.17 kB JavaScript, Vite size warning).
- Live service check: with host-level access, backend health returned HTTP 200 at `127.0.0.1:8000`, the frontend returned HTTP 200 at `[::1]:5173`, and the learning routes were present in OpenAPI. Unauthenticated `/api/learning/policy` and `/metrics` returned 401 as intended.
- Live workflow status: not run because this session has no signed-in PipeGuard browser session or account credentials/token, and local browser control is disabled. MongoDB/LLM outcome and latency samples remain unmeasured.
- Opt-in history behavior: consent now enrolls up to five recent closed incidents for the authenticated account; API regression coverage passed. The live authenticated enrollment itself was not executed in this session.
- Next live benchmark: capture same-build baseline (switch off) and treatment (switch on after that account has an active learned policy), recording `pipeline_duration_ms`, per-agent latency/tokens, `policy_version_used`, rebuild outcome, and sample counts. Until an active policy exists, switch-on prompts correctly fall back to baseline and cannot demonstrate a treatment effect.

### 2026-11-02 — Personalized remediation evaluation attempt
- Change / revision: evaluated the already-integrated opt-in personalization path and its metrics/replay code; no application code changed. The repository guidance requires keeping this ledger current for every evaluation.
- Baseline and treatment: intended comparison is the same held-out resolved incidents with `use_personalized_answers` off versus on under the same active account policy. No eligible live incidents/policy/provider setup was verified in this workspace, so this paired experiment was not run.
- Test/benchmark and command: `pytest -q` failed during plugin loading (`requests_toolbelt`/LangSmith cannot import urllib3's removed `appengine` module). `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q` then failed collection because `langgraph` and `langchain_groq` are not installed. The dependency-independent subset, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q backend/tests/test_learning_metrics.py backend/tests/test_learning_render.py backend/tests/test_learning_classify.py`, passed **25/25** in 0.05 s.
- Sample and environment: 25 unit tests across deterministic metric calculations, guidance rendering, and failure classification; no live incident sample, LLM calls, MongoDB, or signed-in PipeGuard account.
- Metrics (business): approval rate, rebuild pass rate, first-fix success, and judged remediation-quality lift are **not measured** for personalization off/on. No usable customer/incident denominator was available.
- Metrics (performance): paired end-to-end or agent latency, prompt/input tokens, output tokens, provider cost, and throughput are **not measured**. The renderer has a configured maximum of 800 estimated guidance tokens (rough estimate of 4 characters/token); this is a code cap, not a measured token delta or cost.
- Result and before→after: the inspected implementation leaves baseline prompts unchanged when disabled/no usable policy and appends advisory account-policy guidance when enabled with a matching active policy. No measured business or provider performance change can be reported. The offline paired replay uses two remediation calls plus two judge calls per incident and has a test harness, but its tests were not executable here due missing dependencies.
- Verdict: **inconclusive** for treatment effectiveness; deterministic component checks passed.
- Evidence: test outputs above; implementation in `backend/agent_system/learning/{injection,render,replay,metrics}.py`; regression cases in `backend/tests/test_learning_{injection,replay,metrics,render,classify}.py`.
- Limitations / follow-up: install the declared backend dependencies in a suitable environment, then run the paired replay on account-scoped resolved incidents excluded from policy training. Use multiple samples and preferably repeat/judge calibration; separately collect same-build latency and token/cost records. The 25 passing unit tests do not establish user value, outcome lift, or production-scale performance.

### 2026-11-02 — Live personalized remediation toggle verification
- Change / revision: no code changed. Ran two live investigations for `PipeGuard-Lab` build `#36` using the signed-in PipeGuard app; first with the personalization switch visibly off, then with it on after the user approved enrollment of up to five recent closed incident summaries, steps, decisions, and feedback for personalization scoring. Raw Jenkins logs were excluded from the approved sharing scope.
- Baseline and treatment: intended baseline record `d6d1f808-1fb0-4c2a-994e-0e15b887a625` (created `2026-11-02T22:22:58Z`) and treatment record `fc2a555f-022d-4c38-9605-39f63f53d235` (created `2026-11-02T22:33:40Z`). Both persisted `personalization_enabled: true`; the treatment also persisted `policy_version_used: null`. Therefore the app's off-state was not reflected in the baseline record, and no learned policy was applied in the treatment. This is not a valid off/on causal pair.
- Test/benchmark and command: manually triggered one live streamed investigation for the same Jenkins job/build with the UI switch off and one with it on; inspected both saved incident documents in MongoDB Atlas. No test suite was run for this evaluation.
- Sample and environment: 2 live investigation records, one intended per condition; one job/build, authenticated account, live backend and configured providers. The paired sample has 0 verified off runs and 0 verified policy-applied runs. Both report frontend post-submit error `setIncidentHistory` undefined even though the backend produced and persisted the reports.
- Metrics (business): Judge overall score was 93 for the intended-off record and 85 for the intended-on record; overall confidence 94 and 92 respectively. The reports differed, and remediation step counts were 7 and 8, but both records say personalization was enabled and the treatment has no policy version, so these differences cannot be attributed to personalization. Approval, fix, rebuild, and first-fix outcomes are not measured (both saved records remain `awaiting_approval`).
- Metrics (performance): `pipeline_duration_ms` was 38,344 ms for the intended-off record and 39,564 ms for the intended-on record, a raw difference of +1,220 ms (+3.18%). This is a two-run descriptive comparison with different model outputs, not a controlled performance estimate. Per-agent latency, token counts, provider cost, and throughput are not measured.
- Result and before→after: toggle-on intent was saved as true, but `policy_version_used=null` proves the treatment did not consume a learned policy. The off intent was also saved as true, showing a state/persistence discrepancy. This run confirms the concern that the UI result can look nearly identical without active personalization; it does not test the effect of an active policy.
- Verdict: **inconclusive / feature not verified live**. The control flags are unreliable for the intended pair and no active policy was applied.
- Evidence: MongoDB Atlas `pipeguard.incidents` records above; backend code gates persisted personalization on `request.use_personalized_answers and settings.learning_enabled` and stores `result.get("policy_version_used")` in `backend/app/api/investigation.py`; `backend/agent_system/learning/injection.py` returns no guidance when there is no active account policy. Frontend error source: `frontend/src/App.jsx` calls `setIncidentHistory` although the component defines only `setIncidentHistoryCount`.
- Limitations / follow-up: determine why the visibly-off request persisted true (inspect the actual outgoing stream request and live server version/config), ensure the account has a published active policy, then repeat the pair and verify `personalization_enabled=false` on baseline and a non-null `policy_version_used` on treatment. Fix the post-submit `setIncidentHistory` reference so the UI can complete its success path. This pair has only one record per intended arm, no valid control/treatment assignment, and no completed approval/build outcome; it supports no production-wide quality or performance claim.

### 2026-11-02 — Personalization trigger, investigation feedback, and API access fixes
- Change / revision: fixed the undefined `setIncidentHistory` success-path call; capture the personalization switch in a synchronous ref and persist off immediately; include `personalization_enabled` and `policy_version_used` in investigation responses and the streamed completion event; display whether guidance was actually applied. Initial consent enrollment now processes the bounded batch serially and triggers learning once, avoiding concurrent workers losing the trigger to the process-wide busy lock. Added account-scoped `/api/learning/status` plus explicit “Build policy now” and refresh controls in the Pipelines page.
- Baseline and changed behavior: prior live pair had both records persisted enabled and treatment policy version null. Now the off state is read synchronously into the request and reflected in saved/API response state; the treatment UI distinguishes “requested but no active policy” from “policy vN applied.” Enrollment schedules one worker for all selected records. Actual runtime flag persistence and policy publication after restart are not measured in this session.
- Security changes: provider proxy routes now require an authenticated account, and the frontend adds the session bearer token to authenticated API calls. The legacy standalone `/api/investigate` endpoint now requires authentication, has bounded job/build inputs, returns a generic 500 message, and binds to loopback without reload when started directly.
- Test/benchmark and command: `npm run lint` passed; `npm run build` passed (273 modules; 505.95 kB JS bundle, Vite size warning); `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q backend/tests/test_learning_api.py -k 'not router_is_registered_on_the_main_app' backend/tests/test_learning_learner.py backend/tests/test_learning_registry.py backend/tests/test_learning_recorder.py backend/tests/test_learning_store.py backend/tests/test_learning_evaluator.py backend/tests/test_learning_metrics.py backend/tests/test_learning_render.py backend/tests/test_learning_classify.py backend/tests/test_api_auth_boundaries.py` passed **175 tests**, with one route-registration test deselected; `python3 -m compileall -q backend/app backend/agent_system` and `git diff --check` passed; `npm audit --audit-level=moderate --offline` reported **0 vulnerabilities** from local cached advisory data.
- Sample and environment: 175 backend unit/API checks and seven unauthenticated provider-proxy cases, plus static compilation, frontend lint/build, and offline npm audit. No live learning pipeline was invoked after the code change.
- Metrics (business): no new production outcome metrics measured. Expected baseline saved flag is false; expected treatment must show non-null policy version. Historical live records above are unchanged and remain invalid for causal comparison.
- Metrics (performance): not measured. Added a synchronous status endpoint/manual user-triggered run; no API latency or end-to-end timings were sampled.
- Result and before→after: static/unit checks pass for the changed learning and access-control behavior. Route-level investigation tests could not be collected because this environment lacks `langgraph`; the route-registration check was deselected for that same missing dependency. `pip-audit` is not installed, and the backend dependency list has no lockfile, so backend dependency advisories are not measured.
- Verdict: **code-path fixes verified by focused checks; live policy application still requires a post-restart investigation.** The earlier null policy proves no matching guidance version was used, but the account’s policy state was not inspected, so the precise live cause remains unverified. Enrollment alone does not guarantee a published policy; the multi-thread race could also leave an incomplete batch without another trigger. Status and manual build controls now show eligibility and build results.
- Evidence: changes in `frontend/src/App.jsx`, `frontend/src/components/PersonalizationToggle.jsx`, `backend/agent_system/learning/recorder.py`, `backend/app/api/learning.py`, `backend/app/api/investigation.py`, provider API routers, and `backend/agent_system/main.py`; regression checks in `backend/tests/test_learning_api.py`, `backend/tests/test_learning_recorder.py`, and `backend/tests/test_api_auth_boundaries.py`.
- Limitations / follow-up: restart backend and frontend so these changes are loaded; in the Pipelines page inspect learning status, build a policy when opted-in outcomes are available, and repeat a paired investigation. Confirm one baseline with `personalization_enabled=false` and one treatment with a non-null `policy_version_used`. The npm audit used offline advisory cache; backend dependency audit and deployment-level SSRF/network policy review remain outstanding. Focused results do not establish production security or personalized quality lift.
