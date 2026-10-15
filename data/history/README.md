# History store seed data

| File / folder | Loader | Used for retrieval & prompts | Notes |
|---|---|---|---|
| `synthetic_incidents.yaml` | `synthetic` | yes | 34 synthetic past incidents of the Online Boutique estate |
| `runbooks.yaml` | `runbook` | yes | Runbook entries normalized to the same incident format |
| `ghalogs/*.jsonl` | `ghalogs` | yes | Stub loader for GHALogs (GitHub Actions run logs). Drop more JSONL rows here. |
| `logchunks/*.jsonl` | `logchunks` | **no (test only)** | Stub loader for LogChunks; reserved for evaluating the log parser |
| `rcaeval/*.jsonl` | `rcaeval` | **no (test only)** | Stub loader for RCAEval; reserved for evaluating diagnosis |

All loaders normalize rows into one format: `service, time, symptoms, root_cause, fix` (+ `category`, `fix_action`).
