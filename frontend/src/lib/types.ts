// Mirrors backend/app/models/incident.py (see schema/incident.schema.json).

export type Severity = "critical" | "high" | "medium" | "low";
export type IncidentStatus =
  | "monitoring" | "investigating" | "diagnosing" | "proposing" | "gating" | "awaiting_approval"
  | "executing" | "verifying" | "resolved" | "rejected" | "blocked" | "verification_failed"
  | "needs_attention" | "no_incident";
export type Verdict = "auto" | "auto_logged" | "require_approval" | "forbidden";
export type StepStatus = "pending" | "running" | "completed" | "skipped" | "failed" | "waiting";

export interface Trigger {
  kind: "pipeline_failure" | "deployment_failure" | "pod_failure" | "metric_anomaly";
  source: string;
  description: string;
  detected_at: string;
  fault_started_at?: string | null;
  signals: string[];
}
export interface ChangeInfo {
  repository: string; commit_sha: string; author: string; message: string; timestamp: string;
  files_changed: string[]; diff: string;
}
export interface PipelineInfo {
  provider: string; pipeline_id: string; name: string; run_id: string; status: string;
  failed_stage?: string | null; branch?: string | null; url?: string | null;
}
export interface PodState { name: string; phase: string; reason?: string | null; restarts: number; ready: boolean }
export interface DeploymentInfo {
  namespace: string; name: string; service: string; environment: string;
  current_revision?: number | null; previous_revision?: number | null; image?: string | null; previous_image?: string | null;
  replicas_ready?: number | null; replicas_desired?: number | null; pods: PodState[]; recent_events: string[];
}
export interface LogPattern {
  pattern_id: string; template: string; count: number; level: string; first_seen?: string | null;
  last_seen?: string | null; sample: string; sources: string[];
}
export interface LogSummary {
  raw_line_count: number; pattern_count: number; patterns_sent_to_llm: number; reduction_ratio: number;
  interpretation: string; key_errors: string[];
}
export interface MetricPoint { ts: string; value: number }
export interface MetricAnomaly {
  metric: string; service: string; unit: string; detector: string; baseline: number; observed: number;
  score: number; threshold: number; anomaly_start: string; series: MetricPoint[];
}
export interface Evidence { kind: string; ref: string; description: string; weight: number }
export interface Hypothesis {
  id: string; rank: number; title: string; category: string; component: string; description: string;
  confidence: number; evidence: Evidence[]; similar_incidents: string[];
}
export interface ActionTarget { kind: string; name: string; namespace?: string | null; environment: string }
export interface ProposedAction {
  type: string; target: ActionTarget; parameters: Record<string, unknown>; expected_effect: string;
  risk: "low" | "medium" | "high"; reversible: boolean; rationale: string; hypothesis_id?: string | null; source: string;
}
export interface PolicyDecision {
  verdict: Verdict; rule_id: string; reason: string; action_type: string; environment: string; evaluated_at: string;
  blocked_action?: ProposedAction | null; alternative?: ProposedAction | null; alternative_verdict?: Verdict | null;
}
export interface Approval {
  required: boolean; decision: "pending" | "approved" | "rejected" | "not_required";
  approver?: string | null; reason?: string | null; timestamp?: string | null;
}
export interface ExecutionResult {
  status: "success" | "failed" | "skipped"; action_type: string; provider: string; details: string;
  output: Record<string, unknown>; executed_at: string;
}
export interface VerificationCheck { name: string; passed: boolean; detail: string }
export interface VerificationResult { status: "healthy" | "unhealthy" | "inconclusive"; checks: VerificationCheck[]; verified_at: string }
export interface TimelineEvent { ts: string; agent: string; kind: string; message: string; data: Record<string, unknown> }
export interface AgentStep {
  agent: string; status: StepStatus; started_at?: string | null; finished_at?: string | null;
  duration_ms?: number | null; summary: string; llm_provider?: string | null;
}
export interface Incident {
  id: string; title: string; scenario_id?: string | null; source: string; severity: Severity; status: IncidentStatus;
  service?: string | null; environment?: string | null; created_at: string; updated_at: string;
  time_window?: { start: string; end: string } | null; trigger?: Trigger | null; change?: ChangeInfo | null;
  pipeline?: PipelineInfo | null; deployment?: DeploymentInfo | null; log_patterns: LogPattern[];
  log_summary?: LogSummary | null; metric_anomalies: MetricAnomaly[]; hypotheses: Hypothesis[];
  proposed_action?: ProposedAction | null; policy_decision?: PolicyDecision | null; approval?: Approval | null;
  execution_result?: ExecutionResult | null; verification_result?: VerificationResult | null;
  timeline: TimelineEvent[]; steps: AgentStep[]; missing_data: string[];
  metrics: { detection_delay_s?: number | null; analysis_wall_ms?: number | null; raw_log_lines?: number | null; llm_context_lines?: number | null };
  llm_providers_used: string[];
}
export interface Scenario {
  id: string; order: number; title: string; summary: string; service: string; environment: string; namespace: string;
  expected: { policy_verdict: Verdict; blocked: boolean };
}
export interface Health {
  status: string; mock_llm: boolean; llm_chain: string[]; adapters: Record<string, string>;
  history: Record<string, number>; step_delay_s: number;
}
export interface AuditRow { id: number; ts: string; incident_id: string | null; actor: string; action: string; outcome: string; detail: Record<string, unknown> }

export interface EvalSide {
  mean_time_to_detect_s: number; mean_time_to_diagnosis_s: number; top1_accuracy: number; top3_accuracy: number;
  mean_log_reduction: number; unsafe_actions_blocked: number; unsafe_actions_executed: number;
  human_approvals_needed: number; resolved: number | null; fix_correct: number | null;
}
export interface EvalScenario {
  scenario_id: string; title: string; environment: string;
  ground_truth: { category: string; component: string; root_cause: string; fix: { type: string; target: string } };
  agent: {
    incident_id: string; status: string; detection_delay_s: number; analysis_wall_s: number; time_to_diagnosis_s: number;
    predicted: { category: string; component: string; confidence: number }[]; rank_of_truth: number | null;
    top1: boolean; top3: boolean; raw_log_lines: number; patterns: number; lines_to_llm: number; log_reduction: number;
    action: string | null; fix_correct: boolean; policy_verdict: Verdict | null; unsafe_blocked: number;
    approvals_needed: number; verified: string | null; resolved: boolean; llm_providers: string[];
  };
  baseline: {
    alert: string; detection_delay_s: number; investigation_s: number; time_to_diagnosis_s: number; guesses: string[];
    top1: boolean; top3: boolean; runbook_action: string | null; unsafe_action_executed: boolean; approvals_needed: number;
    breakdown: Record<string, number>;
  };
}
export interface EvalReport {
  generated_at: string; llm_chain: string[]; scenario_count: number; scenarios: EvalScenario[];
  summary: { agent: EvalSide; baseline: EvalSide }; method: { top3_review_penalty_s: number; notes: string };
}
export interface HistoryRow {
  id: string; service: string; time: string; symptoms: string; root_cause: string; fix: string;
  category: string | null; fix_action: string | null; source: string; use_for_retrieval: number; score?: number;
}
export interface PolicyRule {
  id: string; description?: string; action_types?: string[]; environments?: string[]; risk?: string[];
  verdict: Verdict; alternative?: { type: string; parameters?: Record<string, unknown> };
}
