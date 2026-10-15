import { CheckCircle2, FlaskConical, Play, ShieldAlert, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Button, Card, Empty, Spinner, cx } from "../components/ui";
import { api } from "../lib/api";
import { VERDICT, fmtDuration, pct } from "../lib/format";
import { useApp } from "../lib/store";
import type { EvalReport } from "../lib/types";

// Validated two-slot categorical palette (dataviz validator, dark mode): agent = indigo, baseline = amber.
const AGENT = "#6366f1";
const BASE = "#d97706";

function Kpi({ label, agent, base, better, hint }: { label: string; agent: ReactNode; base: ReactNode; better?: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4" title={hint}>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-2 flex items-end justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5 text-[10px] text-slate-500"><span className="h-2 w-2 rounded-sm" style={{ background: AGENT }} />Agents</div>
          <div className="text-2xl font-semibold tabular-nums text-white">{agent}</div>
        </div>
        <div className="text-right">
          <div className="flex items-center justify-end gap-1.5 text-[10px] text-slate-500"><span className="h-2 w-2 rounded-sm" style={{ background: BASE }} />Baseline</div>
          <div className="text-lg font-medium tabular-nums text-slate-400">{base}</div>
        </div>
      </div>
      {better && <div className="mt-2 text-[11px] font-medium text-emerald-300">{better}</div>}
    </div>
  );
}

function Chart({ data, title }: { data: { name: string; agent: number; baseline: number }[]; title: string }) {
  return (
    <Card title={title}>
      <div className="h-64">
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barGap={2} barCategoryGap="28%">
            <CartesianGrid stroke="#1e293b" vertical={false} />
            <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={{ stroke: "#334155" }} />
            <YAxis tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} axisLine={false} width={44} unit=" m" />
            <Tooltip cursor={{ fill: "#1e293b55" }} contentStyle={{ background: "#0f172a", border: "1px solid #334155", borderRadius: 8, fontSize: 12 }}
              labelStyle={{ color: "#e2e8f0" }} formatter={(v: number, n: string) => [fmtDuration(v * 60), n === "agent" ? "Agents" : "Baseline"]} />
            <Legend formatter={(v) => <span className="text-xs text-slate-300">{v === "agent" ? "Agent system" : "Baseline (static alerts + manual)"}</span>} />
            <Bar dataKey="agent" fill={AGENT} radius={[4, 4, 0, 0]} maxBarSize={26} />
            <Bar dataKey="baseline" fill={BASE} radius={[4, 4, 0, 0]} maxBarSize={26} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

const Yes = ({ ok }: { ok: boolean }) => ok ? <CheckCircle2 size={15} className="text-emerald-400" /> : <XCircle size={15} className="text-rose-400" />;

function Report({ r }: { r: EvalReport }) {
  const a = r.summary.agent, b = r.summary.baseline;
  const short = (id: string) => id.split("-").slice(0, 2).join("-");
  const ttdData = r.scenarios.map((s) => ({ name: short(s.scenario_id), agent: s.agent.detection_delay_s / 60, baseline: s.baseline.detection_delay_s / 60 }));
  const diagData = r.scenarios.map((s) => ({ name: short(s.scenario_id), agent: s.agent.time_to_diagnosis_s / 60, baseline: s.baseline.time_to_diagnosis_s / 60 }));
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Mean time to detect" agent={fmtDuration(a.mean_time_to_detect_s)} base={fmtDuration(b.mean_time_to_detect_s)}
          better={b.mean_time_to_detect_s > 0 ? `${(b.mean_time_to_detect_s / Math.max(a.mean_time_to_detect_s, 1)).toFixed(1)}× faster` : undefined} />
        <Kpi label="Mean time to correct diagnosis" agent={fmtDuration(a.mean_time_to_diagnosis_s)} base={fmtDuration(b.mean_time_to_diagnosis_s)}
          better={`${Math.round((1 - a.mean_time_to_diagnosis_s / b.mean_time_to_diagnosis_s) * 100)}% less time`} />
        <Kpi label="Top-1 / top-3 accuracy" agent={`${pct(a.top1_accuracy)} / ${pct(a.top3_accuracy)}`} base={`${pct(b.top1_accuracy)} / ${pct(b.top3_accuracy)}`} />
        <Kpi label="Log reduction (to LLM)" agent={pct(a.mean_log_reduction, 1)} base="0% (raw)" hint="1 − pattern groups sent to the LLM / raw log lines" />
        <Kpi label="Unsafe actions blocked" agent={a.unsafe_actions_blocked} base={`${b.unsafe_actions_blocked} (${b.unsafe_actions_executed} executed)`}
          better={b.unsafe_actions_executed ? "baseline follows the runbook's prod delete" : undefined} />
        <Kpi label="Human approvals needed" agent={`${a.human_approvals_needed} / ${r.scenario_count}`} base={`${b.human_approvals_needed} / ${r.scenario_count}`}
          better="only production changes need a human" />
        <Kpi label="Correct fix proposed" agent={`${a.fix_correct} / ${r.scenario_count}`} base="manual" />
        <Kpi label="Verified resolved" agent={`${a.resolved} / ${r.scenario_count}`} base="—" />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Chart data={ttdData} title="Time to detect per scenario (minutes)" />
        <Chart data={diagData} title="Time to correct diagnosis per scenario (minutes)" />
      </div>
      <Card title="Per-scenario results vs ground truth" bodyClass="p-0 overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-slate-900 text-[10px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-3 py-2 text-left">Scenario</th><th className="px-3 py-2 text-left">Ground truth</th>
              <th className="px-3 py-2 text-left">Agent top-1 (rank)</th><th className="px-3 py-2 text-left">Baseline first guess</th>
              <th className="px-3 py-2 text-right">TTD agent / base</th><th className="px-3 py-2 text-right">Diagnosis agent / base</th>
              <th className="px-3 py-2 text-left">Log lines → LLM</th><th className="px-3 py-2 text-left">Action · gate</th><th className="px-3 py-2 text-center">Verified</th>
            </tr>
          </thead>
          <tbody>
            {r.scenarios.map((s) => (
              <tr key={s.scenario_id} className="border-t border-slate-800 align-top">
                <td className="px-3 py-2.5"><div className="font-medium text-slate-200">{s.scenario_id}</div><div className="text-[10px] text-slate-500">{s.environment}</div></td>
                <td className="px-3 py-2.5"><div className="font-mono text-slate-300">{s.ground_truth.category}</div><div className="text-[10px] text-slate-500">fix: {s.ground_truth.fix.type} {s.ground_truth.fix.target}</div></td>
                <td className="px-3 py-2.5"><div className="flex items-center gap-1.5"><Yes ok={s.agent.top1} /><span className="font-mono text-slate-300">{s.agent.predicted[0]?.category}</span><span className="text-slate-500">({s.agent.rank_of_truth ?? "—"})</span></div>
                  <div className="text-[10px] text-slate-500">conf {pct(s.agent.predicted[0]?.confidence)}</div></td>
                <td className="px-3 py-2.5"><div className="flex items-center gap-1.5"><Yes ok={s.baseline.top1} /><span className="font-mono text-slate-300">{s.baseline.guesses[0]}</span></div>
                  <div className="text-[10px] text-slate-500">alert: {s.baseline.alert}</div></td>
                <td className="px-3 py-2.5 text-right tabular-nums"><span className="text-indigo-300">{fmtDuration(s.agent.detection_delay_s)}</span> / <span className="text-amber-300">{fmtDuration(s.baseline.detection_delay_s)}</span></td>
                <td className="px-3 py-2.5 text-right tabular-nums"><span className="text-indigo-300">{fmtDuration(s.agent.time_to_diagnosis_s)}</span> / <span className="text-amber-300">{fmtDuration(s.baseline.time_to_diagnosis_s)}</span></td>
                <td className="px-3 py-2.5 tabular-nums text-slate-300">{s.agent.raw_log_lines.toLocaleString()} → {s.agent.lines_to_llm} <span className="text-emerald-400">({pct(s.agent.log_reduction, 1)})</span></td>
                <td className="px-3 py-2.5"><div className="font-mono text-slate-300">{s.agent.action}</div>
                  {s.agent.policy_verdict && <Badge className={VERDICT[s.agent.policy_verdict].cls}>{s.agent.unsafe_blocked ? <><ShieldAlert size={11} /> delete blocked → safer alternative</> : VERDICT[s.agent.policy_verdict].label}</Badge>}</td>
                <td className="px-3 py-2.5 text-center"><div className="flex justify-center"><Yes ok={s.agent.resolved} /></div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Method">
        <div className="grid gap-4 text-xs leading-relaxed text-slate-400 md:grid-cols-2">
          <div>
            <div className="mb-1 font-semibold text-slate-200">Agent system (measured)</div>
            Each scenario is replayed through the real pipeline (same code as the demo) with a simulated approver. Time to detect = simulated time from fault
            injection to the first EWMA / pod / pipeline signal. Time to correct diagnosis = detection + measured wall-clock of Log Analysis + Diagnosis
            (+{fmtDuration(r.method.top3_review_penalty_s)} human review if the truth is only in the top 3). Accuracy compares ranked hypotheses with the scenario's ground truth.
          </div>
          <div>
            <div className="mb-1 font-semibold text-slate-200">Baseline (static rules + modeled manual work)</div>
            Static alert rules (HighErrorRate 5m, HighLatency 5m, KubePodCrashLooping 15m, ReplicasMismatch 15m, CI failure) run on the same data.
            Manual investigation is modeled: triage 5 min, reading raw logs at 400 lines/min (cap 25 min), k8s inspection 4 min, change correlation 8 min,
            runbook search 5 min, +60% rework when the first guess is wrong. The engineer applies the runbook fix with no policy gate.
          </div>
        </div>
        <div className="mt-3 text-[11px] text-slate-500">Generated {new Date(r.generated_at).toLocaleString()} · LLM chain: {r.llm_chain.join(" → ")} · see docs/evaluation.md</div>
      </Card>
    </div>
  );
}

export default function EvaluationPage() {
  const { evalReport, evalRunning, evalProgress, setEvalRunning, toast } = useApp();
  const run = async () => {
    try { setEvalRunning(true); await api.runEvaluation(); } catch (e) { setEvalRunning(false); toast(String(e), "err"); }
  };
  return (
    <div className="space-y-4 p-6">
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={run} disabled={evalRunning}>{evalRunning ? <Spinner /> : <Play size={14} />} {evalRunning ? "Running evaluation…" : "Run evaluation on all scenarios"}</Button>
        {evalRunning && evalProgress && <span className="text-xs text-slate-400">scenario {evalProgress.index + 1}/{evalProgress.total}: <span className="font-mono text-indigo-300">{evalProgress.scenario}</span></span>}
        {!evalRunning && evalReport && <span className="text-xs text-slate-500">Numbers are computed from real runs against ground truth — rerun any time.</span>}
      </div>
      {evalReport ? <div className={cx(evalRunning && "opacity-60 transition")}><Report r={evalReport} /></div> : (
        <Card><Empty icon={<FlaskConical size={30} />} title="No evaluation yet">Click “Run evaluation” to replay every scenario through the agents and the baseline and score both against ground truth. Takes a few seconds.</Empty></Card>
      )}
    </div>
  );
}
