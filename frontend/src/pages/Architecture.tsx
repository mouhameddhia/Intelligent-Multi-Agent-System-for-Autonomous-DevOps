import { useEffect, useMemo, useState } from "react";
import { Card, Badge, cx } from "../components/ui";
import { api } from "../lib/api";
import { AGENTS, STATUS } from "../lib/format";
import { useApp } from "../lib/store";
import type { Incident, StepStatus } from "../lib/types";

type Arch = Awaited<ReturnType<typeof api.architecture>>;

const W = 160, H = 74;
const NODES: Record<string, { x: number; y: number; uses: string; llm?: boolean }> = {
  monitor: { x: 40, y: 250, uses: "observability · orchestration · pipeline · repository" },
  log_analysis: { x: 250, y: 250, uses: "logging · pipeline", llm: true },
  diagnosis: { x: 460, y: 250, uses: "orchestration · quality · history", llm: true },
  remediation: { x: 670, y: 250, uses: "orchestration · history", llm: true },
  policy_gate: { x: 880, y: 250, uses: "policies.yaml (no LLM)" },
  human_approval: { x: 880, y: 420, uses: "LangGraph interrupt / resume" },
  execute: { x: 670, y: 420, uses: "orchestration · pipeline · repository" },
  verify: { x: 460, y: 420, uses: "orchestration · observability · pipeline" },
  record: { x: 250, y: 420, uses: "SQLite: incidents · audit · history" },
};
const EDGES: [string, string, string?][] = [
  ["monitor", "log_analysis"], ["log_analysis", "diagnosis"], ["diagnosis", "remediation"], ["remediation", "policy_gate"],
  ["policy_gate", "human_approval", "require approval"], ["human_approval", "execute", "approved"], ["execute", "verify"], ["verify", "record"],
];
const PROVIDERS = [
  { key: "pipeline", name: "PipelineProvider", real: "GitHubActionsAdapter" },
  { key: "logging", name: "LoggingProvider", real: "LokiAdapter" },
  { key: "observability", name: "ObservabilityProvider", real: "PrometheusAdapter" },
  { key: "repository", name: "RepositoryProvider", real: "GitHubActionsAdapter" },
  { key: "orchestration", name: "OrchestrationProvider", real: "KubernetesAdapter" },
  { key: "quality", name: "QualityProvider", real: "—" },
];

function anchor(n: string, side: "l" | "r" | "t" | "b") {
  const p = NODES[n];
  return side === "l" ? [p.x, p.y + H / 2] : side === "r" ? [p.x + W, p.y + H / 2] : side === "t" ? [p.x + W / 2, p.y] : [p.x + W / 2, p.y + H];
}

function edgePath(a: string, b: string): string {
  const A = NODES[a], B = NODES[b];
  if (A.y === B.y) {
    const [x1, y1] = A.x < B.x ? anchor(a, "r") : anchor(a, "l");
    const [x2, y2] = A.x < B.x ? anchor(b, "l") : anchor(b, "r");
    return `M${x1},${y1} L${x2},${y2}`;
  }
  const [x1, y1] = anchor(a, "b"); const [x2, y2] = anchor(b, "t");
  return `M${x1},${y1} L${x2},${y2}`;
}

const STROKE: Record<StepStatus | "idle", string> = {
  idle: "#334155", pending: "#334155", running: "#818cf8", waiting: "#fbbf24", completed: "#10b981", skipped: "#334155", failed: "#f43f5e",
};

export default function ArchitecturePage() {
  const { incidents, lastActive, byId } = useApp();
  const [arch, setArch] = useState<Arch | null>(null);
  const [follow, setFollow] = useState<string | "auto">("auto");
  useEffect(() => { api.architecture().then(setArch).catch(() => undefined); }, []);

  const inc: Incident | undefined = useMemo(() => {
    if (follow !== "auto") return byId[follow];
    if (lastActive && byId[lastActive.incidentId]) return byId[lastActive.incidentId];
    return incidents[0];
  }, [follow, lastActive, byId, incidents]);
  const st = (a: string): StepStatus | "idle" => inc?.steps.find((s) => s.agent === a)?.status ?? "idle";
  const autoPath = inc?.policy_decision && inc.approval && !inc.approval.required && inc.execution_result;
  const blocked = inc?.policy_decision?.verdict === "forbidden";

  return (
    <div className="space-y-4 p-6">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs text-slate-500">Following</span>
        <select value={follow} onChange={(e) => setFollow(e.target.value)}
          className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200 focus:outline-none">
          <option value="auto">latest active incident</option>
          {incidents.map((i) => <option key={i.id} value={i.id}>{i.id} · {i.title.slice(0, 50)}</option>)}
        </select>
        {inc && <span className={cx("rounded-full px-2 py-0.5 text-[11px] ring-1 ring-inset", STATUS[inc.status].cls)}>{inc.id} · {STATUS[inc.status].label}</span>}
        <div className="flex-1" />
        <span className="text-[11px] text-slate-500">Replay a scenario (top right) and watch the active node move.</span>
      </div>

      <Card bodyClass="p-2">
        <svg viewBox="0 0 1260 600" className="w-full">
          <defs>
            <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#64748b" /></marker>
            <marker id="arrOn" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#10b981" /></marker>
            <filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="7" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          </defs>

          {/* connector layer */}
          <rect x="20" y="14" width="1220" height="150" rx="14" fill="#0b1222" stroke="#1e293b" />
          <text x="40" y="40" fill="#94a3b8" fontSize="13" fontWeight="600" letterSpacing="1">CONNECTOR LAYER · provider interfaces (config/adapters.yaml)</text>
          {PROVIDERS.map((p, i) => {
            const x = 40 + i * 200; const sel = arch?.providers[p.key] ?? "simulated";
            return (
              <g key={p.key}>
                <rect x={x} y="54" width="184" height="92" rx="10" fill="#0f172a" stroke="#334155" />
                <text x={x + 12} y="77" fill="#e2e8f0" fontSize="13" fontWeight="600">{p.name}</text>
                <rect x={x + 12} y="88" width={sel === "simulated" ? 96 : 120} height="20" rx="5" fill={sel === "simulated" ? "#4f46e520" : "#10b98120"} stroke={sel === "simulated" ? "#6366f155" : "#10b98155"} />
                <text x={x + 20} y="102" fill={sel === "simulated" ? "#a5b4fc" : "#6ee7b7"} fontSize="11" fontFamily="monospace">● {sel === "simulated" ? "Simulated" : sel}</text>
                <text x={x + 12} y="126" fill="#64748b" fontSize="10">real: {p.real}</text>
                {p.real !== "—" && <text x={x + 12} y="139" fill="#475569" fontSize="9.5">read-only · off unless configured</text>}
              </g>
            );
          })}
          <rect x="40" y="180" width="1180" height="22" rx="6" fill="#1e293b" opacity="0.6" />
          <text x="630" y="195" textAnchor="middle" fill="#94a3b8" fontSize="11">agents call provider interfaces only — never tools directly · scenario fixtures replayed by SimulatedWorld</text>

          {/* orchestration graph */}
          <rect x="20" y="216" width="1040" height="300" rx="14" fill="none" stroke="#1e293b" strokeDasharray="6 6" />
          <text x="40" y="238" fill="#64748b" fontSize="11" letterSpacing="1">LANGGRAPH STATE GRAPH · shared state = Incident record</text>

          {EDGES.map(([a, b, label]) => {
            const done = st(a) === "completed" && st(b) !== "pending" && st(b) !== "idle" && st(b) !== "skipped";
            const active = st(a) === "completed" && (st(b) === "running" || st(b) === "waiting");
            const d = edgePath(a, b);
            const A = NODES[a], B = NODES[b];
            return (
              <g key={a + b}>
                <path d={d} stroke={done ? "#10b981" : "#334155"} strokeWidth={done ? 2.2 : 1.6} fill="none" markerEnd={done ? "url(#arrOn)" : "url(#arr)"}
                  strokeDasharray={active ? "6 4" : undefined} className={active ? "animate-dash" : undefined} />
                {label && <text x={(A.x + B.x) / 2 + W / 2 + (A.y === B.y ? 0 : 8)} y={(A.y + B.y) / 2 + H / 2 + (A.y === B.y ? -8 : 0)} fill="#64748b" fontSize="10" textAnchor={A.y === B.y ? "middle" : "start"}>{label}</text>}
              </g>
            );
          })}
          {/* auto path: gate → execute */}
          <path d={`M${NODES.policy_gate.x + 20},${NODES.policy_gate.y + H} Q ${NODES.execute.x + W + 40},${NODES.policy_gate.y + H + 40} ${NODES.execute.x + W - 20},${NODES.execute.y}`}
            stroke={autoPath ? "#14b8a6" : "#334155"} strokeWidth="1.5" fill="none" strokeDasharray="4 4" markerEnd="url(#arr)" />
          <text x={NODES.execute.x + W + 18} y={NODES.policy_gate.y + H + 30} fill={autoPath ? "#5eead4" : "#64748b"} fontSize="10">auto / auto_logged</text>
          {blocked && (
            <g>
              <rect x={NODES.policy_gate.x + 8} y={NODES.policy_gate.y - 30} width="144" height="20" rx="5" fill="#f43f5e22" stroke="#f43f5e88" />
              <text x={NODES.policy_gate.x + 80} y={NODES.policy_gate.y - 16} textAnchor="middle" fill="#fda4af" fontSize="10.5" fontWeight="600">⛔ forbidden → alternative</text>
            </g>
          )}

          {Object.entries(NODES).map(([k, n]) => {
            const s = st(k);
            const color = STROKE[s];
            const live = s === "running" || s === "waiting";
            return (
              <g key={k}>
                {live && <rect x={n.x - 4} y={n.y - 4} width={W + 8} height={H + 8} rx="14" fill="none" stroke={color} strokeWidth="3" filter="url(#glow)" className="animate-glow" />}
                <rect x={n.x} y={n.y} width={W} height={H} rx="11" fill={live ? (s === "waiting" ? "#422006" : "#1e1b4b") : s === "completed" ? "#052e2b" : "#0f172a"}
                  stroke={color} strokeWidth={live ? 2.5 : 1.5} strokeDasharray={s === "skipped" ? "4 4" : undefined} />
                <text x={n.x + 12} y={n.y + 24} fill={s === "skipped" ? "#64748b" : "#f1f5f9"} fontSize="13.5" fontWeight="600">{AGENTS[k].label}</text>
                {n.llm && <g><rect x={n.x + W - 40} y={n.y + 10} width="30" height="17" rx="4" fill="#7c3aed30" stroke="#8b5cf680" /><text x={n.x + W - 25} y={n.y + 22} textAnchor="middle" fill="#c4b5fd" fontSize="9.5" fontWeight="600">LLM</text></g>}
                {k === "policy_gate" && <g><rect x={n.x + W - 52} y={n.y + 10} width="42" height="17" rx="4" fill="#33415560" /><text x={n.x + W - 31} y={n.y + 22} textAnchor="middle" fill="#cbd5e1" fontSize="9.5">rules</text></g>}
                <text x={n.x + 12} y={n.y + 42} fill="#64748b" fontSize="9.5">{n.uses.length > 34 ? n.uses.slice(0, 33) + "…" : n.uses}</text>
                <text x={n.x + 12} y={n.y + 61} fill={color === "#334155" ? "#475569" : color} fontSize="10.5" fontWeight="600">
                  {s === "idle" ? "idle" : s === "waiting" ? "⏸ waiting for human" : s === "running" ? "● running" : s}
                </text>
              </g>
            );
          })}

          {/* side stores */}
          <g>
            <rect x="40" y="420" width="160" height="74" rx="11" fill="#0f172a" stroke="#334155" strokeDasharray="3 3" />
            <text x="52" y="444" fill="#e2e8f0" fontSize="12.5" fontWeight="600">History store</text>
            <text x="52" y="462" fill="#64748b" fontSize="9.5">SQLite · TF-IDF similarity</text>
            <text x="52" y="478" fill="#64748b" fontSize="9.5">{arch ? "synthetic + runbooks + GHALogs" : ""}</text>
            <path d={`M200,457 L250,457`} stroke="#334155" strokeDasharray="3 3" />
          </g>
          <g>
            <rect x="1080" y="250" width="150" height="74" rx="11" fill="#0f172a" stroke="#334155" strokeDasharray="3 3" />
            <text x="1092" y="274" fill="#e2e8f0" fontSize="12.5" fontWeight="600">policies.yaml</text>
            <text x="1092" y="292" fill="#64748b" fontSize="9.5">first match wins</text>
            <text x="1092" y="308" fill="#64748b" fontSize="9.5">default: require approval</text>
            <path d="M1080,287 L1040,287" stroke="#334155" strokeDasharray="3 3" />
          </g>
          <g>
            <rect x="1080" y="420" width="150" height="74" rx="11" fill="#0f172a" stroke="#7c3aed55" strokeDasharray="3 3" />
            <text x="1092" y="444" fill="#e2e8f0" fontSize="12.5" fontWeight="600">LLM fallback chain</text>
            {(arch?.llm_chain ?? ["mock"]).slice(0, 3).map((c, i) => <text key={c} x="1092" y={462 + i * 13} fill={c === "mock" ? "#fcd34d" : "#c4b5fd"} fontSize="9.5" fontFamily="monospace">{i + 1}. {c}</text>)}
          </g>

          <text x="40" y="560" fill="#64748b" fontSize="11">Only summaries (top log pattern groups, anomaly summaries, change metadata) enter LLM context — never raw logs. Missing data stops the graph and flags the incident.</text>
          <text x="40" y="580" fill="#64748b" fontSize="11">Execute is the only node allowed to mutate anything, and only after the Policy Gate (and a human when required) allowed it.</text>
        </svg>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Real adapters">
          <div className="space-y-2 text-xs">
            {arch && Object.entries(arch.real_adapters).map(([k, v]) => (
              <div key={k} className="flex items-center justify-between">
                <span className="font-mono text-slate-300">{k}</span>
                <span className="flex gap-1.5">
                  <Badge className={v.enabled ? "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30" : undefined}>{v.enabled ? "enabled" : "disabled"}</Badge>
                  <Badge className={v.allow_writes ? "bg-rose-500/10 text-rose-300 ring-rose-500/30" : "bg-sky-500/10 text-sky-300 ring-sky-500/30"}>{v.allow_writes ? "writes allowed" : "read-only"}</Badge>
                </span>
              </div>
            ))}
          </div>
        </Card>
        <Card title="Agent contracts">
          <ul className="space-y-1.5 text-xs text-slate-400">
            {Object.entries(AGENTS).map(([k, a]) => <li key={k}><span className="font-semibold text-slate-200">{a.label}</span> — {a.desc}</li>)}
          </ul>
        </Card>
        <Card title="Guarantees">
          <ul className="list-disc space-y-1.5 pl-4 text-xs text-slate-400">
            <li>Agents exchange typed JSON (Pydantic) tied to the incident id — no free-form chat.</li>
            <li>LLM can re-rank hypotheses but cannot invent categories or change the proposed action type.</li>
            <li>Every step, decision, approval and execution is written to the audit log.</li>
            <li>Approvals survive a restart: the run is rehydrated from SQLite and resumed.</li>
            <li>Resolved incidents feed back into the history store for future similarity search.</li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
