# PipeGuard

PipeGuard is an autonomous DevOps intelligence platform for investigating failed
Jenkins builds. It combines a React dashboard, FastAPI integrations, and a
LangGraph investigation workflow to collect evidence, diagnose failures, propose
remediation, and apply a fix only after explicit engineer approval.

## What it does

The investigation workflow is:

1. Collect build and operational evidence.
2. Run monitoring and diagnosis agents.
3. Search stored incidents for relevant historical matches.
4. Generate a remediation recommendation and structured fix steps.
5. Audit the reports with an independent LLM judge.
6. Pause for human approval.
7. Apply the approved fix and store the incident for later review.

The backend also exposes connectors for:

- **Jenkins** - test a connection, list jobs and builds, and retrieve build
  evidence and logs.
- **Prometheus** - test readiness, inspect active targets, and execute instant
  or range PromQL queries.
- **GitHub** - validate a token, list repositories and commits, and retrieve
  commit details.
- **MongoDB** - persist incidents and search historical incidents when
  `MONGODB_URI` is configured.

The dashboard receives one Server-Sent Event as each investigation node
completes, so progress is visible while the investigation is running. When
Jenkins credentials or evidence are omitted, the agent workflow can use the
synthetic scenario included in the backend for local development.

## Repository layout

```text
.
├── backend/
│   ├── agent_system/       # LangGraph agents, state, tools, and persistence
│   │   └── learning/       # Offline learning layer (see "Learning layer" below)
│   ├── app/                # FastAPI API and external-service connectors
│   └── tests/              # pytest suite
├── frontend/               # React + Vite dashboard
├── requirements.txt        # Backend dependencies
├── LEARNING_LAYER.md       # Full learning-layer reference
├── PROJECT_STRUCTURE.md    # Annotated file tree
└── README.md
```

## Requirements

- Python 3.10 or newer
- Node.js 18 or newer and npm
- Optional: Jenkins, Prometheus, GitHub personal access token, and MongoDB

## Quick start

### 1. Start the backend

From the repository root, create and activate a virtual environment, then
install the dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Start the API on port `8000`:

```powershell
python -m uvicorn app.main:app --app-dir backend --reload --port 8000
```

The API is available at <http://127.0.0.1:8000>. Interactive OpenAPI
documentation is available at <http://127.0.0.1:8000/docs>.

### 2. Start the frontend

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open the dashboard at <http://localhost:5173>.

The frontend currently connects to `http://127.0.0.1:8000`. If the backend is
run on another host or port, update `API_URL` in
[`frontend/src/App.jsx`](frontend/src/App.jsx).

## Configuration

Create a repository-root `.env` file for optional settings. The backend reads
the following variables:

```dotenv
# Investigation LLM
GROQ_API_KEY=
LLM_MODEL=openai/gpt-oss-120b

# Legacy standalone agent service
AGENT_PORT=8001
USE_SYNTHETIC=true

# Independent LLM judge
JUDGE_API_KEY=
JUDGE_MODEL=qwen/qwen3.8-27b

# Incident persistence and historical matching
MONGODB_URI=
MONGODB_DB=pipeguard

# Learning layer (requires MONGODB_URI to persist anything)
LEARNING_ENABLED=true
LEARNING_BATCH_SIZE=5
```

`GROQ_API_KEY` is required when the monitoring, diagnosis, remediation, or fix
agents call the Groq-backed LLM. `JUDGE_API_KEY` is required for the
independent judge when it is enabled. The judge uses a separate model by
default so its audit is independent of the investigation agents.

`MONGODB_URI` enables incident persistence, historical matching, feedback, and
judge calibration. Install its optional driver with:

```powershell
python -m pip install -r backend/agent_system/requirements.txt
```

The root `requirements.txt` is sufficient for running the FastAPI application
without MongoDB persistence.

Keep all tokens and credentials in environment variables or a secret manager;
do not commit `.env` files.

## API overview

All endpoints below are served by the FastAPI application on port `8000`.

### Health

```text
GET /
GET /api/health
```

### Investigation

```text
POST /api/investigate
POST /api/investigate/stream
POST /api/investigate/decision
GET  /api/incidents
GET  /api/incidents/{incident_id}
GET  /api/judge/calibration
POST /api/incidents/{incident_id}/feedback
```

Start an investigation with a Jenkins build. The Jenkins credentials and
evidence fields are optional when using the synthetic local scenario:

```json
{
  "job_name": "example-job",
  "build_number": 42,
  "jenkins_url": "https://jenkins.example.com",
  "jenkins_username": "ci-user",
  "jenkins_api_token": "replace-me",
  "evidence": {},
  "github_token": "optional-github-token",
  "github_owner": "optional-owner",
  "github_repo": "optional-repository",
  "engineer_id": "devops-engineer",
  "source": "jenkins_build",
  "incident_type": "test_failure"
}
```

The response has status `awaiting_approval` and includes monitoring, diagnosis,
remediation, confidence, historical-match, independent-judge, and structured
fix-step reports. The `/api/investigate/stream` variant returns
`text/event-stream` events as each graph node completes and ends with the same
investigation payload.

Approve or deny the proposed fix using the returned `incident_id`:

```json
{
  "incident_id": "incident-id-from-the-previous-response",
  "decision": "approve",
  "approver": "devops-engineer"
}
```

The approval route resumes the LangGraph checkpoint. A denied decision ends the
workflow without applying a fix.

After the build is rerun, submit the outcome to
`POST /api/incidents/{incident_id}/feedback`:

```json
{
  "build_passed": true,
  "liked_fix": true,
  "system_efficient": true,
  "comment": "The remediation fixed the failing test.",
  "submitted_by": "devops-engineer",
  "resolution_path": "auto_fix",
  "followed_steps": {},
  "solved_step_id": null,
  "custom_solution": ""
}
```

Use `"resolution_path": "manual"` when an engineer applies the remediation
manually. In that case, `followed_steps` records the step IDs that were used
and `solved_step_id` identifies the step that resolved the incident.

`GET /api/judge/calibration` reports how often the judge's predicted outcome
matches the later engineer feedback.

### Jenkins

```text
POST /api/jenkins/test
POST /api/jenkins/jobs
POST /api/jenkins/builds
POST /api/jenkins/build-evidence
```

Jenkins requests use a JSON body containing `url`, `username`, and `api_token`.
Build history additionally accepts `job_name` and an optional `limit` from 1 to
100. Build evidence accepts `job_name` and a positive `build_number`.

### Prometheus

```text
POST /api/prometheus/test
POST /api/prometheus/targets
POST /api/prometheus/query
POST /api/prometheus/query-range
```

Prometheus requests use a JSON body containing `url`. Query requests also
include `query`; range queries include `start`, `end`, and an optional `step`
(default `30s`).

### GitHub

```text
POST /api/github/test
POST /api/github/repositories
POST /api/github/commits
POST /api/github/commit
```

GitHub requests accept a personal access token. Commit endpoints additionally
require the repository owner, repository name, and, for a single commit, a
7-to-64-character commit SHA. `per_page` is optional and must be between 1
and 100.

## Integration smoke tests with curl

Start the backend first:

```powershell
python -m uvicorn app.main:app --app-dir backend --reload --port 8000
```

Use `curl.exe` explicitly in PowerShell. Set these temporary variables in the
same terminal, replacing the placeholders locally:

```powershell
$BaseUrl = "http://127.0.0.1:8000"
$JenkinsUrl = "http://localhost:8080"
$JenkinsUser = "your-jenkins-user"
$JenkinsToken = "your-jenkins-api-token"
$PrometheusUrl = "http://localhost:9090"
$GitHubToken = "your-github-token"
$GitHubOwner = "your-github-owner"
$GitHubRepository = "your-github-repository"
$BuildJob = "your-jenkins-job"
$BuildNumber = 42
```

### Backend health

```powershell
curl.exe "$BaseUrl/api/health"
```

### Jenkins

```powershell
$JenkinsBody = @{
  url = $JenkinsUrl
  username = $JenkinsUser
  api_token = $JenkinsToken
} | ConvertTo-Json

curl.exe -X POST "$BaseUrl/api/jenkins/test" `
  -H "Content-Type: application/json" `
  -d $JenkinsBody

curl.exe -X POST "$BaseUrl/api/jenkins/jobs" `
  -H "Content-Type: application/json" `
  -d $JenkinsBody

$BuildsBody = @{
  url = $JenkinsUrl
  username = $JenkinsUser
  api_token = $JenkinsToken
  job_name = $BuildJob
  limit = 10
} | ConvertTo-Json

curl.exe -X POST "$BaseUrl/api/jenkins/builds" `
  -H "Content-Type: application/json" `
  -d $BuildsBody

$EvidenceBody = @{
  url = $JenkinsUrl
  username = $JenkinsUser
  api_token = $JenkinsToken
  job_name = $BuildJob
  build_number = $BuildNumber
} | ConvertTo-Json

curl.exe -X POST "$BaseUrl/api/jenkins/build-evidence" `
  -H "Content-Type: application/json" `
  -d $EvidenceBody
```

### Prometheus

```powershell
$PrometheusBody = @{ url = $PrometheusUrl } | ConvertTo-Json

curl.exe -X POST "$BaseUrl/api/prometheus/test" `
  -H "Content-Type: application/json" `
  -d $PrometheusBody

curl.exe -X POST "$BaseUrl/api/prometheus/targets" `
  -H "Content-Type: application/json" `
  -d $PrometheusBody

$QueryBody = @{
  url = $PrometheusUrl
  query = "up"
} | ConvertTo-Json

curl.exe -X POST "$BaseUrl/api/prometheus/query" `
  -H "Content-Type: application/json" `
  -d $QueryBody
```

### GitHub

```powershell
$GitHubBody = @{ token = $GitHubToken } | ConvertTo-Json

curl.exe -X POST "$BaseUrl/api/github/test" `
  -H "Content-Type: application/json" `
  -d $GitHubBody

curl.exe -X POST "$BaseUrl/api/github/repositories" `
  -H "Content-Type: application/json" `
  -d $GitHubBody

$CommitsBody = @{
  token = $GitHubToken
  owner = $GitHubOwner
  repository = $GitHubRepository
  per_page = 10
} | ConvertTo-Json

curl.exe -X POST "$BaseUrl/api/github/commits" `
  -H "Content-Type: application/json" `
  -d $CommitsBody
```

### MongoDB-backed incident endpoints

These endpoints return an empty result or not-found response when MongoDB is
not configured or is unreachable:

```powershell
curl.exe "$BaseUrl/api/incidents?limit=20"
curl.exe "$BaseUrl/api/judge/calibration?limit=500"
```

### Synthetic investigation

With `USE_SYNTHETIC=true` and valid LLM keys configured, start an investigation
without real Jenkins credentials:

```powershell
$InvestigationBody = @{
  job_name = "synthetic-job"
  build_number = 1
  evidence = @{}
  engineer_id = "devops-engineer"
  source = "synthetic"
} | ConvertTo-Json -Depth 5

curl.exe -X POST "$BaseUrl/api/investigate" `
  -H "Content-Type: application/json" `
  -d $InvestigationBody
```

The response should have `status` set to `awaiting_approval`. Save its
`incident_id` before testing the decision endpoint. Do not approve a fix unless
you have reviewed the remediation and structured steps.

## Learning layer

The learning layer lets PipeGuard get better at proposing fixes from its own
history. It **never changes model weights**. Instead it keeps a short, versioned
list of lessons learned from past incidents and shows the relevant ones to the
Remediation agent as advice the next time a similar build fails.

### Why it exists

Without it, every investigation starts from scratch. The Remediation agent can
recall one similar past incident (see historical matching), but it cannot
notice patterns across many incidents, such as "for dependency failures,
pinning the version has worked four times out of five, while just re-running the
build has failed three times". The learning layer captures those patterns.

### How it works

```text
engineer approves or denies a fix, later reports whether the rebuild passed
        │
        ▼
1. Record     the closed incident is reduced to structured fields
        │
        ▼
2. Evaluate   a reward from -1 to 1, plus an LLM quality score
        │
        ▼
3. Learn      after 5 new evaluated incidents, an LLM proposes lessons;
        │     the code checks every proposal before accepting it
        ▼
4. Publish    a new numbered policy version (the previous one is kept)
        │
        ▼
5. Apply      the next Remediation prompt gets the matching lessons
```

1. **Record.** When an engineer decides on a fix, or submits feedback, the
   incident becomes a learning record: the failure category, a short diagnosis
   summary, the proposed steps, the decision, whether a fix was applied, and the
   rebuild result. Raw console logs are never copied into it.
2. **Evaluate.** Each record gets an outcome reward from the strongest signal
   available: the rebuild result first, then engineer feedback, then the
   approve or deny decision, then the judge score. An incident with no known
   outcome is excluded from learning. A separate LLM pass scores the diagnosis
   and the fix on a 0-100 scale; this score is advisory.
3. **Learn.** Once five new evaluated incidents are waiting (configurable), the
   learner model proposes changes to the list of lessons: add, edit, upvote,
   downvote or remove. The model only proposes. Code checks each proposal and
   discards any that are malformed, cite incidents that do not exist or do not
   match, or contain text about approvals, prompts or links. Vote counts,
   promotion and retirement are computed by code from the recorded rewards, not
   taken from the model.
4. **Publish.** A lesson starts as a *candidate*. It becomes *active* once at
   least two incidents support it and successes outnumber failures. The learner
   also keeps success and failure counts for each pair of failure category and
   fix type, called *priors*. Every change creates a new policy version; the
   active and the previous versions are kept so you can roll back.
5. **Apply.** When Remediation runs, PipeGuard classifies the diagnosed failure
   and adds a section titled "Learned guidance from past incidents (advisory)"
   to the prompt. It contains the priors and the active lessons for that
   category, trimmed to about 800 tokens. Candidate lessons are never shown.

### Example

After five closed dependency failures, the learner might add the lesson
"Pin the dependency to the last known working version before rerunning". Two of
the incidents that passed support it, so it becomes active. Together with the
priors (for example, `dependency_update`: 4 succeeded, 1 failed), it appears in
the Remediation prompt for the next dependency failure. The engineer still
reviews and approves the result as usual.

### What it does not do

- It does not retrain or fine-tune any model.
- It does not run during an investigation. Recording, evaluation and learning
  happen in a background thread and never delay or fail a request.
- It cannot bypass human approval or change how fixes are applied. The policy is
  advice added to one prompt; the approval step and the fix code do not use it.
- If the policy is empty, disabled or unreadable, Remediation behaves exactly as
  it did before this layer existed.

### Controlling it

| Setting | Default | Effect |
| --- | --- | --- |
| `LEARNING_ENABLED` | `true` | `false` turns off background learning and prompt injection |
| `LEARNING_BATCH_SIZE` | `5` | New evaluated incidents needed to trigger a learning run |

Background learning calls share Groq rate limits with investigations. If you see
judge failures during busy periods, set `LEARNING_ENABLED=false`.

Endpoints (all under `/api/learning`; they require MongoDB to return data, and
like the rest of the API have no authentication):

```text
GET  /api/learning/policy      Active policy; add ?failure_category=dependency to see the exact prompt section
GET  /api/learning/versions    All policy versions, newest first (?include_rules=true for the rules)
POST /api/learning/run         Learn now (background by default; {"wait": true} to wait for the result)
POST /api/learning/rollback    Restore the previous policy version
GET  /api/learning/metrics     Approval, rebuild-pass and first-fix success rates per policy version
```

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/learning/policy
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/learning/run -ContentType "application/json" -Body '{"wait": true}'
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/learning/rollback
```

To check whether a policy version actually helps, replay held-out resolved
incidents with and without it (needs MongoDB and both API keys, and makes LLM
calls):

```powershell
cd backend
python -m agent_system.learning.replay --version 2 --limit 10
```

The architecture, data model, validation rules, metric definitions and known
limitations are in [`LEARNING_LAYER.md`](LEARNING_LAYER.md).

## Development commands

Run the backend tests from the repository root. They use fake LLMs and
in-memory stores and never reach MongoDB or a real model:

```powershell
python -m pytest
```

Frontend commands are run from `frontend/`:

```powershell
npm run dev       # Start the Vite development server
npm run build     # Create a production build
npm run lint      # Run ESLint
npm run preview   # Preview the production build
```

Backend API checks:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/health
```

The FastAPI application also publishes its complete, interactive schema at
<http://127.0.0.1:8000/docs>.

## Security notes

- Use Jenkins API tokens rather than passwords.
- Send credentials only over HTTPS in non-local environments.
- Never commit `GROQ_API_KEY`, GitHub tokens, Jenkins tokens, or MongoDB
  connection strings.
- If a credential is pasted into chat, source control, logs, or a terminal
  recording, revoke it and create a replacement immediately.
- Restrict CORS origins before deploying outside local development.
- Review every remediation recommendation before approving it.

## Current limitations

- The default frontend API URL is hard-coded for local development.
- The in-memory LangGraph checkpointer is process-local; use a durable
  checkpointer before running multiple backend workers.
- MongoDB must be configured for incident history, historical matching,
  feedback, and judge calibration; without it, those features are unavailable.
- The learning layer needs MongoDB and, with enough incidents, real Groq
  usage; it has only been tested with fake LLMs and in-memory stores. See the
  limitations in [`LEARNING_LAYER.md`](LEARNING_LAYER.md).
- External services must be reachable from the backend process.
- Production authentication, authorization, rate limiting, and secret
  management are not configured by this repository.
