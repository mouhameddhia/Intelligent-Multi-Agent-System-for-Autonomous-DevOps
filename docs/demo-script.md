## 5-minute demo script

Best order: **c → e → b → d → Evaluation** (production approval first, then the safety story, then autonomy, then the numbers).

1. **(0:00) Architecture tab.** One sentence per layer: connectors (all *Simulated* here, real adapters read-only),
   the LangGraph graph, the gate, the LLM chain. Leave it open.
2. **(0:30) Replay c — bad image in production.** Click *Replay scenario → c*. Switch to *Architecture*: the active node
   moves Monitor → … → Policy Gate and stops glowing amber on **Human Approval**. Back to *Incidents*:
   * Stepper shows each agent's duration. *Evidence → Log patterns*: 4,745 lines → 19 patterns → 8 sent to the LLM (99.8% reduction).
   * Hypothesis #1 **bad image (97%)** with evidence chips — click `log:…` and `change:b71d2a9` to jump to the pattern and the diff.
   * *Metrics*: frontend p95 latency with the anomaly shaded. Action card: **rollback to revision 23**, risk medium,
     gate *Requires approval (P050)*. Type a reason, **Approve** → Execute → Verify (4/4 replicas, latency back to baseline) → Resolved.
3. **(2:00) Replay e — the forbidden action.** The runbook and a past incident say "delete the stale canary"; Remediation
   proposes `delete_resource deployment/frontend-canary` in production. The gate shows it **struck through and BLOCKED
   (P010)** and substitutes **scale to 0** (reversible), which needs approval. Approve with a reason → error rate recovers.
4. **(3:00) Replay b — autonomous in staging.** OOMKilled cartservice: diagnosis *memory limit*, fix *raise limit to 512Mi*,
   gate *automatic · logged* → executes and verifies with no human. Point at the *Audit log* entry.
5. **(3:40) Replay d — CI.** GitHub Actions unit-tests fail; diagnosis distinguishes *code regression* (assertion + src change)
   from *flaky test*; opens a revert PR automatically (PRs are auto-logged; merging stays with code review).
6. **(4:15) Evaluation tab → Run evaluation.** All 5 scenarios replay headless against ground truth: agents detect in ~24 s
   vs ~4 min for static alerts, reach the correct diagnosis in ~24 s vs ~40 min modeled manual work, top-1 100% vs 60%,
   ~99% of log lines kept out of the LLM, 1 unsafe action blocked (the baseline executes it), 3/5 human approvals.
   Stress that the baseline's manual time is a documented model while the agent numbers are measured.
7. (Optional) Scenario a, or *Reject* an approval to show nothing executes.

