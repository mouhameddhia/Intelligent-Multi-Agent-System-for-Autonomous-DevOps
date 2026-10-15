import type { IncidentStatus, Severity, StepStatus, Verdict } from "./types";

export const AGENTS: Record<string, { label: string; short: string; desc: string }> = {
  monitor: { label: "Monitor", short: "Detect", desc: "EWMA z-score on metrics, pod & pipeline health; opens the incident" },
  log_analysis: { label: "Log Analysis", short: "Logs", desc: "Deterministic grouping of raw lines; LLM interprets top groups only" },
  diagnosis: { label: "Diagnosis", short: "Diagnose", desc: "Signature scoring + change + metrics + TF-IDF history → ranked hypotheses" },
  remediation: { label: "Remediation", short: "Propose", desc: "Proposes a fix from runbooks and similar incidents — never executes" },
  policy_gate: { label: "Policy Gate", short: "Gate", desc: "Rule-based (policies.yaml), no LLM; may block and suggest a safer action" },
  human_approval: { label: "Human Approval", short: "Approve", desc: "LangGraph interrupt; resumes on approve/reject with a reason" },
  execute: { label: "Execute", short: "Execute", desc: "Performs the authorized action through provider interfaces only" },
  verify: { label: "Verify", short: "Verify", desc: "Monitor re-checks pods, SLO metrics and pipelines after the fix" },
  record: { label: "Record", short: "Record", desc: "Closes the incident, writes audit + history store entry" },
};

export const SEVERITY: Record<Severity, string> = {
  critical: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
  high: "bg-orange-500/15 text-orange-300 ring-orange-500/30",
  medium: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  low: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
};

export const STATUS: Record<IncidentStatus, { label: string; cls: string; dot: string }> = {
  monitoring: { label: "Monitoring", cls: "text-slate-300 bg-slate-500/10 ring-slate-500/30", dot: "bg-slate-400" },
  investigating: { label: "Investigating", cls: "text-indigo-300 bg-indigo-500/10 ring-indigo-500/30", dot: "bg-indigo-400" },
  diagnosing: { label: "Diagnosing", cls: "text-indigo-300 bg-indigo-500/10 ring-indigo-500/30", dot: "bg-indigo-400" },
  proposing: { label: "Proposing fix", cls: "text-indigo-300 bg-indigo-500/10 ring-indigo-500/30", dot: "bg-indigo-400" },
  gating: { label: "Policy check", cls: "text-violet-300 bg-violet-500/10 ring-violet-500/30", dot: "bg-violet-400" },
  awaiting_approval: { label: "Awaiting approval", cls: "text-amber-300 bg-amber-500/10 ring-amber-500/40", dot: "bg-amber-400" },
  executing: { label: "Executing", cls: "text-cyan-300 bg-cyan-500/10 ring-cyan-500/30", dot: "bg-cyan-400" },
  verifying: { label: "Verifying", cls: "text-cyan-300 bg-cyan-500/10 ring-cyan-500/30", dot: "bg-cyan-400" },
  resolved: { label: "Resolved", cls: "text-emerald-300 bg-emerald-500/10 ring-emerald-500/30", dot: "bg-emerald-400" },
  rejected: { label: "Rejected", cls: "text-slate-300 bg-slate-500/10 ring-slate-500/30", dot: "bg-slate-400" },
  blocked: { label: "Blocked", cls: "text-rose-300 bg-rose-500/10 ring-rose-500/30", dot: "bg-rose-400" },
  verification_failed: { label: "Verification failed", cls: "text-rose-300 bg-rose-500/10 ring-rose-500/30", dot: "bg-rose-400" },
  needs_attention: { label: "Needs attention", cls: "text-rose-300 bg-rose-500/10 ring-rose-500/30", dot: "bg-rose-400" },
  no_incident: { label: "No incident", cls: "text-slate-400 bg-slate-500/10 ring-slate-500/30", dot: "bg-slate-500" },
};

export const VERDICT: Record<Verdict, { label: string; cls: string; desc: string }> = {
  auto: { label: "Automatic", cls: "text-emerald-300 bg-emerald-500/10 ring-emerald-500/30", desc: "Allowed without review" },
  auto_logged: { label: "Automatic · logged", cls: "text-teal-300 bg-teal-500/10 ring-teal-500/30", desc: "Allowed and recorded in the audit log" },
  require_approval: { label: "Requires approval", cls: "text-amber-300 bg-amber-500/10 ring-amber-500/40", desc: "A human must approve before execution" },
  forbidden: { label: "Forbidden", cls: "text-rose-300 bg-rose-500/10 ring-rose-500/40", desc: "Blocked by policy" },
};

export const RISK: Record<string, string> = {
  low: "text-emerald-300 bg-emerald-500/10 ring-emerald-500/30",
  medium: "text-amber-300 bg-amber-500/10 ring-amber-500/30",
  high: "text-rose-300 bg-rose-500/10 ring-rose-500/30",
};

export const STEP_COLORS: Record<StepStatus, string> = {
  pending: "border-slate-700 bg-slate-900 text-slate-500",
  running: "border-indigo-400 bg-indigo-500/20 text-indigo-200",
  waiting: "border-amber-400 bg-amber-500/20 text-amber-200",
  completed: "border-emerald-500/70 bg-emerald-500/15 text-emerald-300",
  skipped: "border-slate-700 border-dashed bg-slate-900 text-slate-600",
  failed: "border-rose-500 bg-rose-500/15 text-rose-300",
};

export const LEVEL: Record<string, string> = {
  FATAL: "text-rose-200 bg-rose-600/30",
  ERROR: "text-rose-300 bg-rose-500/15",
  WARN: "text-amber-300 bg-amber-500/15",
  INFO: "text-sky-300 bg-sky-500/10",
  DEBUG: "text-slate-400 bg-slate-500/10",
};

export function humanize(s: string | null | undefined): string {
  if (!s) return "";
  return s.replace(/_/g, " ");
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 90) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const m = seconds / 60;
  if (m < 90) return `${m.toFixed(m < 10 ? 1 : 0)} min`;
  return `${(m / 60).toFixed(1)} h`;
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return "";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function timeAgo(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 5) return "just now";
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function pct(v: number | null | undefined, digits = 0): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function num(n: number | null | undefined): string {
  return n == null ? "—" : n.toLocaleString();
}

export const isActive = (s: IncidentStatus) =>
  !["resolved", "rejected", "blocked", "verification_failed", "needs_attention", "no_incident"].includes(s);
