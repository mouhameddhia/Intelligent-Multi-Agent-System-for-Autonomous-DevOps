# Project structure

```text
.
├── README.md                      Product overview, setup, API reference
├── LEARNING_LAYER.md              The offline learning layer
├── CLAUDE.md                      Guidance for Claude Code
├── requirements.txt               Backend dependencies (includes pytest)
├── pytest.ini                     Test paths; puts backend/ on the import path
├── backend/
│   ├── app/                       The FastAPI application (port 8000)
│   │   ├── main.py                App, CORS, router registration
│   │   ├── api/                   Routers
│   │   │   ├── jenkins.py
│   │   │   ├── prometheus.py
│   │   │   ├── github.py
│   │   │   ├── investigation.py   Investigate, stream, decision, incidents, feedback
│   │   │   └── learning.py        /api/learning: policy, versions, run, rollback, metrics
│   │   └── connectors/            Clients for Jenkins, Prometheus, GitHub
│   ├── agent_system/              The investigation engine
│   │   ├── config.py              Settings from the repo-root .env
│   │   ├── db.py                  Best-effort MongoDB access
│   │   ├── data.py                Synthetic scenario for local development
│   │   ├── historical_match.py    Verified recall of similar resolved incidents
│   │   ├── main.py                Legacy standalone agent API (port 8001)
│   │   ├── orchestrator/          LangGraph graph and shared state
│   │   ├── agents/                Monitoring, diagnosis, remediation, steps,
│   │   │                          judge, fix, confidence, report assembly, memory
│   │   ├── tools/                 Tools available to agents
│   │   └── learning/              Offline learning layer (see LEARNING_LAYER.md)
│   │       ├── models.py          Pydantic models
│   │       ├── classify.py        Failure category, signature, fix type
│   │       ├── store.py           Record storage
│   │       ├── recorder.py        Builds records when incidents close
│   │       ├── export.py          Preference-pair JSONL
│   │       ├── evaluator.py       Outcome reward and LLM rubric
│   │       ├── learner.py         Validated policy operations, promotion, priors
│   │       ├── registry.py        Versioned policy, rollback, cache
│   │       ├── render.py          Prompt section, about 800 tokens
│   │       ├── injection.py       Guidance for the Remediation prompt
│   │       ├── trigger.py         Background evaluate-then-learn
│   │       ├── metrics.py         Per-policy-version outcome rates
│   │       └── replay.py          Policy version vs no policy, offline
│   └── tests/                     pytest suite (fake LLMs, in-memory stores)
│       ├── conftest.py            Blocks real MongoDB and LLM access
│       ├── golden/                Remediation prompts from before the learning layer
│       └── test_learning_*.py
└── frontend/                      React + Vite dashboard (port 5173)
    └── src/
        ├── App.jsx
        └── reportSections.js
```

## Request flow

1. The dashboard calls `app/api/investigation.py`.
2. That route runs the LangGraph graph in `agent_system/orchestrator/graph.py`:
   monitoring, diagnosis, remediation, steps, judge, then a pause for approval.
3. After the engineer decides (and later gives feedback), the route saves the
   incident and schedules a learning record in a background thread.
4. The learning layer evaluates records and, every few, publishes a new policy
   version. Remediation reads the active version on the next investigation.
