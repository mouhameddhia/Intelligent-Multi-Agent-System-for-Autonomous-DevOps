import type { AuditRow, EvalReport, Health, HistoryRow, Incident, PolicyRule, Scenario } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* ignore */ }
    throw new Error(`${r.status}: ${detail}`);
  }
  return r.json() as Promise<T>;
}

export const api = {
  health: () => req<Health>("/api/health"),
  scenarios: () => req<Scenario[]>("/api/scenarios"),
  incidents: () => req<Incident[]>("/api/incidents"),
  incident: (id: string) => req<Incident>(`/api/incidents/${id}`),
  replay: (id: string) => req<Incident>(`/api/scenarios/${id}/replay`, { method: "POST" }),
  approve: (id: string, decision: "approved" | "rejected", reason: string, approver: string) =>
    req<Incident>(`/api/incidents/${id}/approval`, { method: "POST", body: JSON.stringify({ decision, reason, approver }) }),
  reset: () => req<{ ok: boolean }>("/api/incidents", { method: "DELETE" }),
  audit: (incidentId?: string) => req<AuditRow[]>(`/api/audit${incidentId ? `?incident_id=${incidentId}` : ""}`),
  architecture: () => req<{ providers: Record<string, string>; real_adapters: Record<string, { enabled: boolean; allow_writes: boolean }>; agents: string[]; llm_chain: string[]; policies: PolicyRule[] }>("/api/architecture"),
  evaluation: () => req<{ running: boolean; report: EvalReport | null }>("/api/evaluation"),
  runEvaluation: () => req<{ running: boolean }>("/api/evaluation/run", { method: "POST" }),
  history: (q?: string) => req<{ results: HistoryRow[]; counts?: Record<string, number> }>(`/api/history${q ? `?q=${encodeURIComponent(q)}&k=8` : ""}`),
  policies: () => req<{ rules: PolicyRule[]; default: { verdict: string; reason: string } }>("/api/policies"),
};
