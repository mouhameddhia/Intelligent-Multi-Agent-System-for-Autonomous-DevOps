import { AlertTriangle, CheckCircle2, ClipboardCheck, Cpu, GitBranch, Inbox, ListTree, Lightbulb, Microscope, Play, Rocket, Trash2, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ActionCard from "../components/ActionCard";
import EvidencePanel, { refToFocus, type Focus } from "../components/EvidencePanel";
import Hypotheses from "../components/Hypotheses";
import ReplayMenu, { EnvBadge } from "../components/ReplayMenu";
import Stepper from "../components/Stepper";
import { Badge, Card, Empty, KV, cx } from "../components/ui";
import { api } from "../lib/api";
import { AGENTS, SEVERITY, STATUS, clock, fmtDuration, fmtMs, humanize, isActive, timeAgo } from "../lib/format";
import { useApp } from "../lib/store";
import type { Incident } from "../lib/types";

function StatusPill({ s }: { s: Incident["status"] }) {
  const m = STATUS[s];
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset", m.cls)}>
      <span className={cx("h-1.5 w-1.5 rounded-full", m.dot, isActive(s) && "animate-pulse")} />{m.label}
    </span>
  );
}

function Feed({ selected }: { selected?: string }) {
  const { incidents, navigate, refresh, toast } = useApp();
  return (
    <div className="flex h-full flex-col border-r border-slate-800 bg-slate-950/40">
      <div className="flex items-center justify-between px-4 py-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">Live incident feed</div>
        {incidents.length > 0 && (
          <button title="Clear demo incidents" className="text-slate-600 hover:text-slate-300"
            onClick={async () => { await api.reset(); await refresh(); navigate("/incidents"); toast("Feed cleared"); }}>
            <Trash2 size={14} />
          </button>
        )}
      </div>
      <div className="flex-1 space-y-2 overflow-auto px-3 pb-4">
        {incidents.length === 0 && (
          <Empty icon={<Inbox size={28} />} title="No incidents yet">Use “Replay scenario” to inject a simulated incident and watch the agents work.</Empty>
        )}
        {incidents.map((i) => {
          const running = i.steps.find((s) => s.status === "running" || s.status === "waiting");
          const done = i.steps.filter((s) => s.status === "completed" || s.status === "skipped").length;
          return (
            <button key={i.id} onClick={() => navigate(`/incidents/${i.id}`)}
              className={cx("w-full rounded-lg border p-3 text-left transition",
                selected === i.id ? "border-indigo-500/50 bg-indigo-500/[0.08]" : "border-slate-800 bg-slate-900/50 hover:border-slate-700",
                i.status === "awaiting_approval" && selected !== i.id && "border-amber-500/40")}>
              <div className="flex items-center justify-between gap-2">
                <Badge className={SEVERITY[i.severity]}>{i.severity}</Badge>
                <span className="text-[10px] text-slate-500">{timeAgo(i.created_at)}</span>
              </div>
              <div className="mt-1.5 line-clamp-2 text-[13px] font-medium leading-snug text-slate-100">{i.title}</div>
              <div className="mt-2 flex items-center justify-between gap-2">
                <StatusPill s={i.status} />
                <span className="font-mono text-[10px] text-slate-500">{i.id}</span>
              </div>
              <div className="mt-2 flex gap-0.5">
                {i.steps.map((s) => (
                  <div key={s.agent} className={cx("h-1 flex-1 rounded-full",
                    s.status === "completed" ? "bg-emerald-500/70" : s.status === "running" ? "animate-pulse bg-indigo-400" : s.status === "waiting" ? "animate-pulse bg-amber-400"
                      : s.status === "failed" ? "bg-rose-500" : s.status === "skipped" ? "bg-slate-700" : "bg-slate-800")} />
                ))}
              </div>
              {running && <div className="mt-1.5 text-[10px] text-indigo-300">{AGENTS[running.agent]?.label} {running.status === "waiting" ? "· waiting for you" : "· working"} ({done}/9)</div>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function AgentActivity({ inc, selected, onSelect }: { inc: Incident; selected: string | null; onSelect: (a: string) => void }) {
  return (
    <div className="space-y-1.5">
      {inc.steps.map((s) => (
        <button key={s.agent} onClick={() => onSelect(s.agent)}
          className={cx("flex w-full items-start gap-3 rounded-lg px-2.5 py-2 text-left transition",
            selected === s.agent ? "bg-slate-800/70" : "hover:bg-slate-800/40", s.status === "pending" && "opacity-50")}>
          <span className={cx("mt-1 h-2 w-2 shrink-0 rounded-full",
            s.status === "completed" ? "bg-emerald-400" : s.status === "running" ? "animate-pulse bg-indigo-400" : s.status === "waiting" ? "animate-pulse bg-amber-400" : s.status === "failed" ? "bg-rose-400" : "bg-slate-600")} />
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-2">
              <span className="text-xs font-semibold text-slate-200">{AGENTS[s.agent]?.label ?? s.agent}</span>
              {s.llm_provider && <Badge className={s.llm_provider === "mock" ? "bg-amber-500/10 text-amber-300 ring-amber-500/20" : "bg-violet-500/10 text-violet-300 ring-violet-500/30"}>LLM: {s.llm_provider}</Badge>}
              {s.agent === "policy_gate" && s.status === "completed" && <Badge>rules only</Badge>}
              <span className="ml-auto text-[10px] tabular-nums text-slate-500">{s.duration_ms != null ? fmtMs(s.duration_ms) : ""}</span>
            </span>
            <span className="mt-0.5 block text-[11px] leading-snug text-slate-400">{s.summary || (s.status === "pending" ? AGENTS[s.agent]?.desc : s.status)}</span>
          </span>
        </button>
      ))}
    </div>
  );
}

function Outcome({ inc }: { inc: Incident }) {
  const e = inc.execution_result;
  const v = inc.verification_result;
  if (!e && !v) {
    return <Empty icon={<Rocket size={26} />} title="Nothing executed yet">Execution happens only after the Policy Gate (and a human, if required) allows it.</Empty>;
  }
  return (
    <div className="space-y-3">
      {e && (
        <div className={cx("rounded-lg p-3 ring-1 ring-inset", e.status === "success" ? "bg-cyan-500/[0.07] ring-cyan-500/30" : "bg-rose-500/10 ring-rose-500/30")}>
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-100"><Play size={14} /> Executed {humanize(e.action_type)}</div>
          <div className="mt-1 font-mono text-[11px] text-slate-300">{e.details}</div>
          <div className="mt-1 text-[10px] text-slate-500">via {e.provider} · {clock(e.executed_at)}</div>
        </div>
      )}
      {v && (
        <div>
          <div className={cx("mb-2 flex items-center gap-2 text-sm font-semibold", v.status === "healthy" ? "text-emerald-300" : "text-rose-300")}>
            {v.status === "healthy" ? <CheckCircle2 size={16} /> : <XCircle size={16} />} Verification: {v.status}
          </div>
          <ul className="space-y-1">
            {v.checks.map((c) => (
              <li key={c.name} className="flex items-start gap-2 text-xs">
                {c.passed ? <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-emerald-400" /> : <XCircle size={13} className="mt-0.5 shrink-0 text-rose-400" />}
                <span className="text-slate-300">{c.name}</span>
                <span className="ml-auto text-right font-mono text-[10px] text-slate-500">{c.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Timeline({ inc }: { inc: Incident }) {
  return (
    <ol className="relative space-y-2 border-l border-slate-800 pl-4">
      {inc.timeline.map((t, i) => (
        <li key={i} className="relative text-xs">
          <span className={cx("absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full border-2 border-slate-900",
            t.kind === "blocked" || t.kind === "error" || t.kind === "rejected" ? "bg-rose-400" : t.kind === "requested" ? "bg-amber-400" : "bg-slate-500")} />
          <span className="font-mono text-[10px] text-slate-500">{clock(t.ts)}</span>{" "}
          <span className="font-semibold text-slate-300">{AGENTS[t.agent]?.label ?? t.agent}</span>{" "}
          <span className="text-slate-400">{t.message}</span>
        </li>
      ))}
    </ol>
  );
}

function Detail({ inc }: { inc: Incident }) {
  const [focus, setFocus] = useState<Focus | null>(null);
  const [selStep, setSelStep] = useState<string | null>(null);
  const evidenceRef = useRef<HTMLDivElement>(null);
  const actionRef = useRef<HTMLDivElement>(null);
  const openEvidence = (ref: string) => {
    setFocus(refToFocus(ref));
    evidenceRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const ttd = inc.metrics.detection_delay_s;
  return (
    <div className="space-y-4 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge className={SEVERITY[inc.severity]}>{inc.severity}</Badge>
            <StatusPill s={inc.status} />
            <EnvBadge env={inc.environment} />
            {inc.trigger && <Badge>{humanize(inc.trigger.kind)}</Badge>}
            <span className="font-mono text-xs text-slate-500">{inc.id}</span>
          </div>
          <h2 className="mt-2 text-xl font-semibold tracking-tight text-white">{inc.title}</h2>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
            {inc.service && <span className="flex items-center gap-1"><Cpu size={12} /> {inc.service}</span>}
            {inc.deployment && <span>{inc.deployment.namespace}</span>}
            {inc.scenario_id && <span className="flex items-center gap-1"><GitBranch size={12} /> scenario {inc.scenario_id}</span>}
            {inc.trigger && <span>detected {clock(inc.trigger.detected_at)} (sim time)</span>}
            {ttd != null && <span className="text-emerald-400">time to detect {fmtDuration(ttd)}</span>}
          </div>
        </div>
      </div>

      {inc.missing_data.length > 0 && (
        <div className="flex items-start gap-2 rounded-lg border border-rose-500/30 bg-rose-500/[0.07] px-3 py-2 text-xs text-rose-200">
          <AlertTriangle size={14} className="mt-0.5" /> Flagged for a human — missing or insufficient data: {inc.missing_data.join("; ")}
        </div>
      )}

      <Card bodyClass="px-4 pb-3 pt-5"><Stepper steps={inc.steps} selected={selStep} onSelect={setSelStep} /></Card>

      <div className="grid gap-4 xl:grid-cols-12">
        <div className="space-y-4 xl:col-span-7">
          <Card title="Ranked root-cause hypotheses" icon={<Lightbulb size={14} />}
            right={inc.steps.find((s) => s.agent === "diagnosis")?.summary ? <span className="max-w-[55%] truncate text-[11px] text-slate-500">{inc.steps.find((s) => s.agent === "diagnosis")?.summary}</span> : null}>
            <Hypotheses items={inc.hypotheses} onEvidence={openEvidence} />
          </Card>
        </div>
        <div className="space-y-4 xl:col-span-5" ref={actionRef}>
          <Card title="Action proposal & Policy Gate" icon={<ClipboardCheck size={14} />}
            className={inc.status === "awaiting_approval" ? "border-amber-500/40 shadow-lg shadow-amber-900/20" : ""}>
            <ActionCard inc={inc} />
          </Card>
          <Card title="Execution & verification" icon={<Rocket size={14} />}><Outcome inc={inc} /></Card>
        </div>
      </div>

      <div ref={evidenceRef} className="scroll-mt-4">
        <Card title="Evidence" icon={<Microscope size={14} />}><EvidencePanel inc={inc} focus={focus} /></Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-12">
        <Card title="Agent outputs" icon={<ListTree size={14} />} className="xl:col-span-7">
          <AgentActivity inc={inc} selected={selStep} onSelect={setSelStep} />
        </Card>
        <Card title="Timeline" className="xl:col-span-5" bodyClass="max-h-[420px] overflow-auto p-4">
          <Timeline inc={inc} />
          <div className="mt-4 border-t border-slate-800 pt-3">
            <KV k="LLM context" v={inc.metrics.llm_context_lines != null ? `${inc.metrics.llm_context_lines} pattern groups (summaries only)` : "—"} />
            <KV k="Analysis wall-clock" v={inc.metrics.analysis_wall_ms != null ? fmtMs(inc.metrics.analysis_wall_ms) : "—"} />
            <KV k="LLM providers" v={Array.from(new Set(inc.llm_providers_used.map((p) => p.split(":")[1]))).join(", ") || "—"} />
          </div>
        </Card>
      </div>
    </div>
  );
}

export default function IncidentsPage() {
  const { route, byId, incidents, scenarios } = useApp();
  const id = route.split("/")[2];
  const [loaded, setLoaded] = useState<Incident | null>(null);
  const inc = (id && byId[id]) || loaded;
  useEffect(() => {
    if (id && !byId[id]) api.incident(id).then(setLoaded).catch(() => setLoaded(null));
  }, [id, byId]);
  const current = inc ?? (id ? null : incidents[0] ?? null);

  return (
    <div className="flex h-full">
      <div className="w-[330px] shrink-0"><Feed selected={current?.id} /></div>
      <div className="min-w-0 flex-1 overflow-auto">
        {current ? <Detail key={current.id} inc={current} /> : (
          <div className="mx-auto max-w-2xl p-10">
            <div className="rounded-2xl border border-slate-800 bg-gradient-to-b from-slate-900 to-slate-950 p-8 text-center">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-indigo-600/20 text-indigo-300"><Play size={22} /></div>
              <h2 className="text-lg font-semibold text-white">Replay a simulated incident</h2>
              <p className="mx-auto mt-2 max-w-md text-sm text-slate-400">
                Agents detect, analyze logs, diagnose, propose a fix, pass the Policy Gate, wait for your approval, execute, verify and record — all through the connector layer, offline.
              </p>
              <div className="mt-5 flex justify-center"><ReplayMenu /></div>
              <div className="mt-6 grid gap-2 text-left text-xs text-slate-400 sm:grid-cols-2">
                {scenarios.map((s) => <div key={s.id} className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2"><span className="font-semibold text-slate-200">{String.fromCharCode(96 + s.order)}.</span> {s.title}</div>)}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
