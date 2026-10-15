# Evaluation method

Run it from the **Evaluation** tab or `POST /api/evaluation/run`. Every scenario in `/scenarios` is replayed through the
same graph used in the demo (`source="evaluation"`, step delay 0, a simulated approver, history writes disabled,
agent-written history excluded from retrieval to avoid leakage). Results are compared with each scenario's
`ground_truth` and `accepted_fixes`.

## Agent metrics (measured)

| Metric | Definition |
|---|---|
| Time to detect | simulated time between fault injection (`fault_index` in `metrics.json`) and the earliest Monitor signal (k8s Warning event, pipeline stage end, or EWMA anomaly confirmed by a 2nd point) |
| Time to correct diagnosis | detection + measured wall-clock of LogAnalysis + Diagnosis; +5 min human review if the truth is only in the top 3; if missed, the baseline's manual time |
| Top-1 / top-3 accuracy | (category, component) of the ranked hypotheses vs ground truth |
| Log reduction ratio | `1 − pattern groups sent to the LLM / raw log lines` |
| Unsafe actions blocked | PolicyGate `forbidden` verdicts |
| Human approvals needed | runs where the effective verdict was `require_approval` |
| Fix correct / resolved | proposed action ∈ accepted fixes; verification healthy after execution |

## Baseline (static rules + modeled manual investigation)

Static rules run on the same provider data: `HighErrorRate` (> 5% for 5 min), `HighLatencyP95` (> 500 ms for 5 min),
`KubePodCrashLooping` (restarts > 0 for 15 min), `KubeDeploymentReplicasMismatch` (> 0 for 15 min), CI failure
notification (immediate). If nothing fires, users report it after 30 min.

The first guess comes from the first alert's typical suspects, overridden by a telling pod reason seen during triage
(`kubectl get pods`: CrashLoopBackOff → config, OOMKilled → memory, ImagePullBackOff → image).

Manual investigation time (model, constants in `backend/app/evaluation/baseline.py`):

| Step | Minutes |
|---|---|
| Triage / context | 5 |
| Reading raw logs | lines ÷ 400 per minute, capped at 25 |
| Kubernetes inspection | 4 (not for CI) |
| Change correlation | 8 if a recent commit exists |
| Runbook / wiki search | 5 |
| Rework when the first guess is wrong | +60% of the above |

The baseline engineer applies the runbook fix for the true category with no gate; if the PolicyGate would forbid it
(e.g. deleting a production resource), it counts as an unsafe action executed.

## Caveats

The agent timings are real measurements on simulated data; the baseline's investigation time is a transparent model.
Five scenarios are a demonstration of the method, not a statistically meaningful benchmark. LogChunks and RCAEval loaders
exist so the parser and diagnosis can later be evaluated on public datasets without leaking them into retrieval.
