import { BookOpen, Search, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, Card } from "../components/ui";
import { api } from "../lib/api";
import { VERDICT } from "../lib/format";
import type { HistoryRow, PolicyRule } from "../lib/types";

export default function KnowledgePage() {
  const [rules, setRules] = useState<PolicyRule[]>([]);
  const [def, setDef] = useState<{ verdict: string; reason: string } | null>(null);
  const [rows, setRows] = useState<HistoryRow[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [q, setQ] = useState("pods CrashLoopBackOff after ConfigMap change, missing port in address");
  const [searched, setSearched] = useState(false);

  useEffect(() => {
    api.policies().then((p) => { setRules(p.rules); setDef(p.default); }).catch(() => undefined);
    api.history().then((h) => { setRows(h.results); setCounts(h.counts ?? {}); }).catch(() => undefined);
  }, []);
  const search = async () => {
    if (!q.trim()) { const h = await api.history(); setRows(h.results); setSearched(false); return; }
    const r = await api.history(q); setRows(r.results); setSearched(true);
  };

  return (
    <div className="grid gap-4 p-6 xl:grid-cols-5">
      <Card title="PolicyGate rules · config/policies.yaml" icon={<ShieldCheck size={14} />} className="xl:col-span-2">
        <ol className="space-y-2">
          {rules.map((r, i) => (
            <li key={r.id} className="rounded-lg border border-slate-800 bg-slate-950/40 p-3 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono font-semibold text-slate-200">{i + 1}. {r.id}</span>
                <Badge className={VERDICT[r.verdict].cls}>{VERDICT[r.verdict].label}</Badge>
              </div>
              <div className="mt-1 text-slate-400">{r.description}</div>
              <div className="mt-1.5 flex flex-wrap gap-1">
                {r.action_types?.map((a) => <Badge key={a} className="bg-slate-800 font-mono text-slate-300 ring-slate-700">{a}</Badge>)}
                {r.environments?.map((e) => <Badge key={e} className="bg-sky-500/10 text-sky-300 ring-sky-500/20">{e}</Badge>)}
                {r.risk?.map((e) => <Badge key={e} className="bg-rose-500/10 text-rose-300 ring-rose-500/20">risk {e}</Badge>)}
              </div>
              {r.alternative && <div className="mt-1.5 text-emerald-300">→ safer alternative: <span className="font-mono">{r.alternative.type} {JSON.stringify(r.alternative.parameters ?? {})}</span></div>}
            </li>
          ))}
          {def && <li className="rounded-lg border border-dashed border-slate-700 p-3 text-xs text-slate-400"><span className="font-mono text-slate-300">DEFAULT</span> → {def.verdict}: {def.reason}</li>}
        </ol>
      </Card>
      <Card title="History store" icon={<BookOpen size={14} />} className="xl:col-span-3"
        right={<div className="flex gap-1">{Object.entries(counts).map(([k, v]) => <Badge key={k} className={k === "logchunks" || k === "rcaeval" ? "bg-slate-800 text-slate-500 ring-slate-700" : undefined}>{k}: {v}</Badge>)}</div>}>
        <div className="mb-3 flex gap-2">
          <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()}
            className="flex-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-200 focus:border-indigo-500 focus:outline-none" />
          <Button onClick={search}><Search size={14} /> TF-IDF search</Button>
        </div>
        <div className="mb-2 text-[11px] text-slate-500">LogChunks and RCAEval rows are loaded for testing only and are excluded from search and prompts.</div>
        <div className="max-h-[640px] space-y-2 overflow-auto pr-1">
          {rows.map((r) => (
            <div key={r.id} className="rounded-lg border border-slate-800 bg-slate-950/40 p-3 text-xs">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono font-semibold text-indigo-300">{r.id}</span>
                <Badge>{r.source}</Badge><Badge>{r.service}</Badge>
                {r.category && <Badge className="bg-slate-800 font-mono text-slate-300 ring-slate-700">{r.category}</Badge>}
                {r.fix_action && <Badge className="bg-slate-800 text-slate-300 ring-slate-700">fix: {r.fix_action}</Badge>}
                {!r.use_for_retrieval && <Badge className="bg-slate-800 text-slate-500 ring-slate-700">test only</Badge>}
                {searched && r.score != null && <span className="ml-auto font-mono text-emerald-300">sim {r.score.toFixed(3)}</span>}
              </div>
              <div className="mt-1.5 text-slate-300">{r.symptoms}</div>
              <div className="mt-1 text-slate-500">Root cause: <span className="text-slate-400">{r.root_cause}</span> · Fix: <span className="text-slate-400">{r.fix}</span></div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
