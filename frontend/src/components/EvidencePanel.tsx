import { Activity, BookMarked, Boxes, GitCommitHorizontal, ScrollText, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Area, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../lib/api";
import type { HistoryRow, Incident, MetricAnomaly } from "../lib/types";
import { LEVEL, clock, num, pct } from "../lib/format";
import { Badge, Empty, cx } from "./ui";

export type EvidenceTab = "logs" | "metrics" | "change" | "cluster" | "history";
export interface Focus { tab: EvidenceTab; ref?: string; nonce: number }

export function refToFocus(ref: string): Focus {
  const kind = ref.split(":")[0];
  const tab: EvidenceTab = kind === "log" ? "logs" : kind === "metric" ? "metrics" : kind === "change" ? "change"
    : kind === "history" ? "history" : "cluster";
  return { tab, ref, nonce: Date.now() };
}

const TABS: { id: EvidenceTab; label: string; icon: ReactNode }[] = [
  { id: "logs", label: "Log patterns", icon: <ScrollText size={14} /> },
  { id: "metrics", label: "Metrics", icon: <Activity size={14} /> },
  { id: "change", label: "Triggering change", icon: <GitCommitHorizontal size={14} /> },
  { id: "cluster", label: "Cluster & pipeline", icon: <Boxes size={14} /> },
  { id: "history", label: "Similar incidents", icon: <BookMarked size={14} /> },
];

function LogPatterns({ inc, focusRef }: { inc: Incident; focusRef?: string }) {
  const s = inc.log_summary;
  const max = Math.max(1, ...inc.log_patterns.map((p) => p.count));
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({});
  useEffect(() => {
    if (focusRef?.startsWith("log:")) {
      const el = rowRefs.current[focusRef.slice(4)];
      if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); }
    }
  }, [focusRef]);
  if (!s) return <Empty icon={<ScrollText size={28} />} title="Logs not analyzed yet" />;
  const sentIds = new Set(inc.log_patterns.slice(0, s.patterns_sent_to_llm).map((p) => p.pattern_id));
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        {[
          ["Raw log lines", num(s.raw_line_count)],
          ["Pattern groups", num(s.pattern_count)],
          ["Sent to LLM", `${s.patterns_sent_to_llm} groups`],
          ["Log reduction", pct(s.reduction_ratio, 1)],
        ].map(([k, v], i) => (
          <div key={k} className={cx("rounded-lg border px-3 py-2", i === 3 ? "border-emerald-500/30 bg-emerald-500/[0.06]" : "border-slate-800 bg-slate-950/40")}>
            <div className="text-[10px] uppercase tracking-wide text-slate-500">{k}</div>
            <div className={cx("text-lg font-semibold tabular-nums", i === 3 ? "text-emerald-300" : "text-slate-100")}>{v}</div>
          </div>
        ))}
      </div>
      <div className="rounded-lg border border-indigo-500/25 bg-indigo-500/[0.05] p-3 text-xs leading-relaxed text-slate-300">
        <div className="mb-1 flex items-center gap-1.5 font-semibold text-indigo-200"><Sparkles size={13} /> LLM interpretation of the top groups
          <span className="font-normal text-slate-500">({inc.steps.find((x) => x.agent === "log_analysis")?.llm_provider ?? "?"})</span></div>
        {s.interpretation}
      </div>
      <div className="max-h-[380px] overflow-auto rounded-lg border border-slate-800">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-slate-900 text-[10px] uppercase tracking-wide text-slate-500">
            <tr><th className="px-3 py-2 text-left">Level</th><th className="px-3 py-2 text-left">Count</th><th className="px-3 py-2 text-left">Pattern (masked template)</th><th className="px-3 py-2 text-left">Sources</th></tr>
          </thead>
          <tbody>
            {inc.log_patterns.map((p) => (
              <tr key={p.pattern_id} ref={(el) => { rowRefs.current[p.pattern_id] = el; }}
                className={cx("border-t border-slate-800/70 align-top", focusRef === `log:${p.pattern_id}` && "bg-indigo-500/10")}>
                <td className="px-3 py-2"><span className={cx("rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold", LEVEL[p.level] ?? LEVEL.INFO)}>{p.level}</span></td>
                <td className="w-32 px-3 py-2">
                  <div className="font-mono tabular-nums text-slate-200">{p.count.toLocaleString()}</div>
                  <div className="mt-1 h-1 rounded bg-slate-800"><div className={cx("h-1 rounded", p.level === "ERROR" || p.level === "FATAL" ? "bg-rose-400" : p.level === "WARN" ? "bg-amber-400" : "bg-sky-500")} style={{ width: `${(p.count / max) * 100}%` }} /></div>
                </td>
                <td className="px-3 py-2">
                  <div className="break-all font-mono text-[11px] text-slate-200">{p.template}</div>
                  <div className="mt-0.5 flex gap-2 text-[10px] text-slate-500">
                    <span className="font-mono">{p.pattern_id}</span>
                    {sentIds.has(p.pattern_id) && <span className="text-indigo-300">→ LLM context</span>}
                  </div>
                </td>
                <td className="px-3 py-2 text-slate-400">{p.sources.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MetricChart({ a }: { a: MetricAnomaly }) {
  const data = useMemo(() => a.series.map((p) => ({ t: clock(p.ts).slice(0, 5), ts: p.ts, v: p.value, anomaly: p.ts >= a.anomaly_start ? p.value : null })), [a]);
  const startLabel = data.find((d) => d.ts >= a.anomaly_start)?.t;
  const endLabel = data[data.length - 1]?.t;
  const fmt = (v: number) => (a.unit === "ratio" ? `${(v * 100).toFixed(1)}%` : `${Math.round(v * 10) / 10}${a.unit && a.unit !== "count" ? " " + a.unit : ""}`);
  return (
    <div className="h-64">
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 10, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#1e293b" vertical={false} />
          <XAxis dataKey="t" tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} axisLine={{ stroke: "#334155" }} interval={9} />
          <YAxis tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} axisLine={false} width={56} tickFormatter={fmt} />
          {startLabel && endLabel && <ReferenceArea x1={startLabel} x2={endLabel} fill="#f43f5e" fillOpacity={0.08} stroke="#f43f5e" strokeOpacity={0.25}
            label={{ value: `anomaly · z=${a.score}`, position: "insideTopLeft", fill: "#fda4af", fontSize: 11 }} />}
          <ReferenceLine y={a.baseline} stroke="#94a3b8" strokeDasharray="4 4" label={{ value: "baseline", position: "insideBottomRight", fill: "#94a3b8", fontSize: 10 }} />
          <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: "#94a3b8" }} formatter={(v: number) => [fmt(v), a.metric]} cursor={{ stroke: "#475569" }} />
          <Area type="monotone" dataKey="anomaly" stroke="none" fill="#f43f5e" fillOpacity={0.15} isAnimationActive={false} connectNulls={false} />
          <Line type="monotone" dataKey="v" stroke="#818cf8" strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

function Metrics({ inc, focusRef }: { inc: Incident; focusRef?: string }) {
  const [sel, setSel] = useState(0);
  useEffect(() => {
    if (focusRef?.startsWith("metric:")) {
      const i = inc.metric_anomalies.findIndex((a) => `metric:${a.service}/${a.metric}` === focusRef);
      if (i >= 0) setSel(i);
    }
  }, [focusRef, inc.metric_anomalies]);
  if (!inc.metric_anomalies.length) return <Empty icon={<Activity size={28} />} title="No metric anomalies">The EWMA detector found no anomalous series for this incident (e.g. a pure CI failure).</Empty>;
  const a = inc.metric_anomalies[Math.min(sel, inc.metric_anomalies.length - 1)];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {inc.metric_anomalies.map((m, i) => (
          <button key={i} onClick={() => setSel(i)}
            className={cx("rounded-md px-2 py-1 font-mono text-[11px] ring-1 ring-inset", i === sel ? "bg-indigo-500/20 text-indigo-200 ring-indigo-500/40" : "bg-slate-900 text-slate-400 ring-slate-700 hover:text-slate-200")}>
            {m.service}/{m.metric} <span className="text-rose-300">z={m.score}</span>
          </button>
        ))}
      </div>
      <div className="text-sm font-medium text-slate-200">{a.service} · {a.metric}</div>
      <MetricChart a={a} />
      <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        <div className="rounded-md bg-slate-950/50 px-3 py-2"><div className="text-slate-500">Detector</div><div className="font-mono text-slate-200">EWMA z-score</div></div>
        <div className="rounded-md bg-slate-950/50 px-3 py-2"><div className="text-slate-500">Baseline → observed</div><div className="font-mono text-slate-200">{a.baseline} → {a.observed}</div></div>
        <div className="rounded-md bg-slate-950/50 px-3 py-2"><div className="text-slate-500">Score / threshold</div><div className="font-mono text-rose-300">{a.score} / {a.threshold}</div></div>
        <div className="rounded-md bg-slate-950/50 px-3 py-2"><div className="text-slate-500">Anomaly start</div><div className="font-mono text-slate-200">{clock(a.anomaly_start)}</div></div>
      </div>
    </div>
  );
}

function Change({ inc }: { inc: Incident }) {
  const c = inc.change;
  if (!c) return <Empty icon={<GitCommitHorizontal size={28} />} title="No preceding change found" />;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-xs text-amber-200">{c.commit_sha.slice(0, 7)}</span>
        <span className="font-medium text-slate-100">{c.message}</span>
      </div>
      <div className="text-xs text-slate-500">{c.author} · {c.repository} · {new Date(c.timestamp).toLocaleString()}
        {inc.trigger && <> · <span className="text-amber-300">{Math.round((new Date(inc.trigger.detected_at).getTime() - new Date(c.timestamp).getTime()) / 60000)} min before detection</span></>}</div>
      <div className="flex flex-wrap gap-1.5">{c.files_changed.map((f) => <Badge key={f} className="bg-slate-800 font-mono text-slate-300 ring-slate-700">{f}</Badge>)}</div>
      {c.diff ? (
        <pre className="max-h-[360px] overflow-auto rounded-lg border border-slate-800 bg-slate-950 p-3 text-[11.5px] leading-[1.55]">
          {c.diff.split("\n").map((l, i) => (
            <div key={i} className={cx("px-1 font-mono",
              l.startsWith("+++") || l.startsWith("---") ? "text-slate-400 font-semibold" : l.startsWith("+") ? "bg-emerald-500/10 text-emerald-300"
                : l.startsWith("-") ? "bg-rose-500/10 text-rose-300" : l.startsWith("@@") ? "text-sky-400" : "text-slate-400")}>{l || " "}</div>
          ))}
        </pre>
      ) : <div className="text-xs text-slate-500">No diff available.</div>}
    </div>
  );
}

function Cluster({ inc }: { inc: Incident }) {
  const d = inc.deployment;
  const p = inc.pipeline;
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div>
        <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Deployment</div>
        {d ? (
          <div className="space-y-2 text-xs">
            <div className="font-mono text-slate-200">{d.namespace}/{d.name} <span className="text-slate-500">({d.environment})</span></div>
            <div className="text-slate-400">Replicas ready: <span className={d.replicas_ready === d.replicas_desired ? "text-emerald-300" : "text-rose-300"}>{d.replicas_ready}/{d.replicas_desired}</span>
              {d.current_revision != null && <> · revision {d.current_revision}{d.previous_revision != null && <> (prev {d.previous_revision})</>}</>}</div>
            {d.image && <div className="break-all font-mono text-slate-400">image: {d.image}{d.previous_image && d.previous_image !== d.image && <span className="text-slate-500"> (prev {d.previous_image})</span>}</div>}
            <div className="space-y-1">
              {d.pods.map((pod) => (
                <div key={pod.name} className="flex items-center justify-between rounded bg-slate-950/50 px-2 py-1 font-mono text-[11px]">
                  <span className="truncate text-slate-300">{pod.name}</span>
                  <span className={pod.ready && !pod.reason ? "text-emerald-300" : "text-rose-300"}>{pod.reason ?? pod.phase} · ↻{pod.restarts}</span>
                </div>
              ))}
            </div>
            {d.recent_events.length > 0 && (
              <div className="space-y-1 pt-1">
                {d.recent_events.map((e, i) => <div key={i} className="text-[11px] text-amber-200/80">⚠ {e}</div>)}
              </div>
            )}
          </div>
        ) : <div className="text-xs text-slate-500">No deployment context (CI-only incident).</div>}
      </div>
      <div>
        <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Pipeline</div>
        {p ? (
          <div className="space-y-1 text-xs">
            <div className="font-mono text-slate-200">{p.name} · run {p.run_id}</div>
            <div className="text-slate-400">status <span className="text-rose-300">{p.status}</span>{p.failed_stage && <> at stage <span className="font-mono text-rose-300">{p.failed_stage}</span></>}</div>
            <div className="text-slate-500">branch {p.branch} · provider {p.provider}</div>
          </div>
        ) : <div className="text-xs text-slate-500">No failing pipeline involved.</div>}
        {inc.trigger && (
          <>
            <div className="mb-2 mt-4 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Detection signals</div>
            <ul className="space-y-1 text-xs text-slate-300">{inc.trigger.signals.map((s, i) => <li key={i}>• {s}</li>)}</ul>
          </>
        )}
      </div>
    </div>
  );
}

function History({ inc, focusRef }: { inc: Incident; focusRef?: string }) {
  const [rows, setRows] = useState<HistoryRow[]>([]);
  const ids = useMemo(() => Array.from(new Set(inc.hypotheses.flatMap((h) => h.evidence.filter((e) => e.kind === "history").map((e) => e.ref.slice(8))))), [inc.hypotheses]);
  useEffect(() => { api.history().then((r) => setRows(r.results)).catch(() => undefined); }, []);
  const shown = rows.filter((r) => ids.includes(r.id));
  if (!ids.length) return <Empty icon={<BookMarked size={28} />} title="No similar past incidents referenced" />;
  return (
    <div className="space-y-2">
      <div className="text-xs text-slate-500">Retrieved by TF-IDF cosine similarity over the history store (synthetic incidents + runbooks; LogChunks/RCAEval excluded).</div>
      {shown.map((r) => (
        <div key={r.id} className={cx("rounded-lg border p-3 text-xs", focusRef === `history:${r.id}` ? "border-indigo-500/50 bg-indigo-500/10" : "border-slate-800 bg-slate-950/40")}>
          <div className="flex items-center gap-2">
            <span className="font-mono font-semibold text-indigo-300">{r.id}</span>
            <Badge>{r.source}</Badge><Badge>{r.service}</Badge>{r.category && <Badge className="bg-slate-800 font-mono text-slate-300 ring-slate-700">{r.category}</Badge>}
            {r.fix_action && <Badge className="bg-slate-800 text-slate-300 ring-slate-700">fix: {r.fix_action}</Badge>}
          </div>
          <div className="mt-1.5 text-slate-300">{r.symptoms}</div>
          <div className="mt-1 text-slate-400"><span className="text-slate-500">Root cause:</span> {r.root_cause} · <span className="text-slate-500">Fix:</span> {r.fix}</div>
        </div>
      ))}
    </div>
  );
}

export default function EvidencePanel({ inc, focus }: { inc: Incident; focus: Focus | null }) {
  const [tab, setTab] = useState<EvidenceTab>("logs");
  useEffect(() => { if (focus) setTab(focus.tab); }, [focus]);
  const focusRef = focus?.ref;
  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-1 border-b border-slate-800">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={cx("-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs font-medium transition",
              tab === t.id ? "border-indigo-400 text-indigo-200" : "border-transparent text-slate-500 hover:text-slate-300")}>
            {t.icon}{t.label}
          </button>
        ))}
      </div>
      {tab === "logs" && <LogPatterns inc={inc} focusRef={focusRef} />}
      {tab === "metrics" && <Metrics inc={inc} focusRef={focusRef} />}
      {tab === "change" && <Change inc={inc} />}
      {tab === "cluster" && <Cluster inc={inc} />}
      {tab === "history" && <History inc={inc} focusRef={focusRef} />}
    </div>
  );
}
