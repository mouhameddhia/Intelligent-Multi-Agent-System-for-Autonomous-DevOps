import { ClipboardList, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, Empty, cx } from "../components/ui";
import { api } from "../lib/api";
import { useApp } from "../lib/store";
import type { AuditRow } from "../lib/types";

const OUTCOME: Record<string, string> = {
  forbidden: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
  require_approval: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
  pending: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
  approved: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
  rejected: "bg-slate-500/10 text-slate-300 ring-slate-500/30",
  success: "bg-cyan-500/10 text-cyan-300 ring-cyan-500/30",
  healthy: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
  resolved: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
  auto: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
  auto_logged: "bg-teal-500/10 text-teal-300 ring-teal-500/30",
  failed: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
  error: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
  unhealthy: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
};

export default function AuditPage() {
  const { incidents, navigate } = useApp();
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const load = () => api.audit().then(setRows).catch(() => undefined);
  const lastUpdate = incidents[0]?.updated_at;
  useEffect(() => { load(); }, [lastUpdate]);
  const shown = useMemo(() => rows.filter((r) => !q || JSON.stringify(r).toLowerCase().includes(q.toLowerCase())), [rows, q]);
  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center gap-3">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter (incident id, actor, action, outcome…)"
          className="w-96 rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-200 placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none" />
        <Button variant="outline" onClick={load}><RefreshCw size={14} /> Refresh</Button>
        <span className="text-xs text-slate-500">{shown.length} entries · append-only (SQLite)</span>
      </div>
      <Card bodyClass="p-0">
        {shown.length === 0 ? <Empty icon={<ClipboardList size={28} />} title="No audit entries yet" /> : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-slate-900 text-[10px] uppercase tracking-wide text-slate-500">
              <tr><th className="px-3 py-2 text-left">Time</th><th className="px-3 py-2 text-left">Incident</th><th className="px-3 py-2 text-left">Actor</th><th className="px-3 py-2 text-left">Action</th><th className="px-3 py-2 text-left">Outcome</th><th className="px-3 py-2 text-left">Detail</th></tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.id} className={cx("cursor-pointer border-t border-slate-800/70 align-top hover:bg-slate-800/30", open === r.id && "bg-slate-800/30")} onClick={() => setOpen(open === r.id ? null : r.id)}>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-slate-500">{new Date(r.ts).toLocaleTimeString()}</td>
                  <td className="px-3 py-2">{r.incident_id && <button className="font-mono text-indigo-300 hover:underline" onClick={(e) => { e.stopPropagation(); navigate(`/incidents/${r.incident_id}`); }}>{r.incident_id}</button>}</td>
                  <td className="px-3 py-2 text-slate-300">{r.actor}</td>
                  <td className="px-3 py-2 font-mono text-slate-200">{r.action}</td>
                  <td className="px-3 py-2">{r.outcome && <Badge className={OUTCOME[r.outcome]}>{r.outcome}</Badge>}</td>
                  <td className="max-w-[520px] px-3 py-2 text-slate-400">
                    {open === r.id ? <pre className="whitespace-pre-wrap break-all font-mono text-[10.5px] text-slate-300">{JSON.stringify(r.detail, null, 2)}</pre>
                      : <span className="line-clamp-1 break-all font-mono text-[10.5px]">{JSON.stringify(r.detail)}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
