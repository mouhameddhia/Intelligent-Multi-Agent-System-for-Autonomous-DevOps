import { ArrowDown, Ban, CheckCircle2, Gavel, ShieldAlert, ShieldCheck, ThumbsDown, ThumbsUp, Undo2, UserCheck, Wrench } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { useApp } from "../lib/store";
import type { Incident, ProposedAction } from "../lib/types";
import { RISK, VERDICT, clock, humanize } from "../lib/format";
import { Badge, Button, Empty, Spinner, cx } from "./ui";

function Params({ p }: { p: Record<string, unknown> }) {
  const entries = Object.entries(p).filter(([k]) => k !== "files");
  if (!entries.length) return null;
  return (
    <div className="mt-2 rounded-md border border-slate-800 bg-slate-950/60 p-2 font-mono text-[11px] leading-relaxed text-slate-300">
      {entries.map(([k, v]) => (
        <div key={k} className="flex gap-2">
          <span className="shrink-0 text-slate-500">{k}:</span>
          <span className="break-all">{typeof v === "object" ? JSON.stringify(v) : String(v)}</span>
        </div>
      ))}
    </div>
  );
}

function ActionSummary({ a, struck }: { a: ProposedAction; struck?: boolean }) {
  return (
    <div className={cx(struck && "opacity-60")}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={cx("rounded-md bg-slate-800 px-2 py-1 font-mono text-sm font-semibold text-white", struck && "line-through decoration-rose-400 decoration-2")}>
          {a.type}
        </span>
        <span className="font-mono text-sm text-slate-300">{a.target.kind}/{a.target.name}</span>
        <Badge className={RISK[a.risk]}>risk: {a.risk}</Badge>
        {a.reversible ? <Badge className="bg-slate-800 text-slate-300 ring-slate-700"><Undo2 size={11} /> reversible</Badge>
          : <Badge className="bg-rose-500/10 text-rose-300 ring-rose-500/30">irreversible</Badge>}
      </div>
      <div className="mt-1 text-[11px] text-slate-500">
        {a.target.namespace ? `${a.target.namespace} · ` : ""}{a.target.environment}
      </div>
      <Params p={a.parameters} />
    </div>
  );
}

export default function ActionCard({ inc }: { inc: Incident }) {
  const { toast, upsert } = useApp();
  const [reason, setReason] = useState("");
  const [approver, setApprover] = useState("on-call engineer");
  const [busy, setBusy] = useState<string | null>(null);
  const a = inc.proposed_action;
  const d = inc.policy_decision;
  const awaiting = inc.status === "awaiting_approval";

  if (!a) {
    return <Empty icon={<Wrench size={28} />} title="No action proposed yet">The Remediation agent proposes a fix after diagnosis. It never executes on its own.</Empty>;
  }

  const decide = async (decision: "approved" | "rejected") => {
    if (reason.trim().length < 3) return;
    setBusy(decision);
    try {
      const out = await api.approve(inc.id, decision, reason.trim(), approver.trim() || "on-call engineer");
      upsert(out);
      toast(decision === "approved" ? "Approved — executing via provider…" : "Rejected — no action will be executed");
      setReason("");
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setBusy(null);
    }
  };

  const effVerdict = d ? (d.verdict === "forbidden" ? d.alternative_verdict ?? "forbidden" : d.verdict) : null;

  return (
    <div className="space-y-4">
      {d?.verdict === "forbidden" && d.blocked_action && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/[0.07] p-3">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-rose-300">
            <Ban size={14} /> Blocked by policy · {d.rule_id}
          </div>
          <ActionSummary a={d.blocked_action} struck />
          <p className="mt-2 text-xs text-rose-200/80">{d.blocked_action.rationale}</p>
          {d.alternative && (
            <div className="mt-3 flex items-center gap-2 text-xs font-medium text-emerald-300">
              <ArrowDown size={14} /> Safer alternative suggested by the gate
            </div>
          )}
        </div>
      )}

      <div>
        {!(d?.verdict === "forbidden") && (
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Proposed action</div>
        )}
        <ActionSummary a={a} />
        <div className="mt-3 space-y-1.5 text-xs">
          <div><span className="text-slate-500">Expected effect · </span><span className="text-slate-200">{a.expected_effect}</span></div>
          {a.rationale && <div><span className="text-slate-500">Rationale · </span><span className="text-slate-300">{a.rationale}</span></div>}
        </div>
      </div>

      {d && effVerdict && (
        <div className={cx("rounded-lg p-3 ring-1 ring-inset", VERDICT[effVerdict].cls)}>
          <div className="flex items-center gap-2 text-sm font-semibold">
            {effVerdict === "forbidden" ? <ShieldAlert size={16} /> : effVerdict === "require_approval" ? <Gavel size={16} /> : <ShieldCheck size={16} />}
            Policy Gate: {VERDICT[effVerdict].label}
          </div>
          <div className="mt-1 text-xs opacity-90">{d.reason}</div>
          <div className="mt-1 font-mono text-[10px] opacity-70">rule {d.rule_id} · {clock(d.evaluated_at)} · rule-based, no LLM</div>
        </div>
      )}

      {awaiting && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/[0.06] p-3">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-amber-200">
            <UserCheck size={16} /> Human approval required
          </div>
          <label className="mb-1 block text-[11px] text-slate-400">Reason (required)</label>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2}
            placeholder="e.g. Evidence points to the ConfigMap change; reverting is low risk."
            className="w-full resize-none rounded-md border border-slate-700 bg-slate-950 px-2.5 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none" />
          <div className="mt-2 flex items-center gap-2">
            <input value={approver} onChange={(e) => setApprover(e.target.value)}
              className="w-40 rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none" title="Approver" />
            <div className="flex-1" />
            <Button variant="danger" disabled={reason.trim().length < 3 || !!busy} onClick={() => decide("rejected")}>
              {busy === "rejected" ? <Spinner /> : <ThumbsDown size={14} />} Reject
            </Button>
            <Button variant="success" disabled={reason.trim().length < 3 || !!busy} onClick={() => decide("approved")}>
              {busy === "approved" ? <Spinner /> : <ThumbsUp size={14} />} Approve
            </Button>
          </div>
          {reason.trim().length < 3 && <div className="mt-1.5 text-[10px] text-slate-500">Enter a reason to enable the buttons. It is stored in the audit log.</div>}
        </div>
      )}

      {inc.approval && inc.approval.required && inc.approval.decision !== "pending" && (
        <div className={cx("flex items-start gap-2 rounded-lg p-3 text-xs ring-1 ring-inset",
          inc.approval.decision === "approved" ? "bg-emerald-500/10 text-emerald-200 ring-emerald-500/30" : "bg-slate-800/60 text-slate-300 ring-slate-700")}>
          {inc.approval.decision === "approved" ? <CheckCircle2 size={15} className="mt-0.5" /> : <ThumbsDown size={15} className="mt-0.5" />}
          <div>
            <div className="font-semibold">{humanize(inc.approval.decision)} by {inc.approval.approver} · {clock(inc.approval.timestamp)}</div>
            <div className="mt-0.5 opacity-90">“{inc.approval.reason}”</div>
          </div>
        </div>
      )}
      {inc.approval && !inc.approval.required && d && d.verdict !== "forbidden" && (
        <div className="text-[11px] text-slate-500">No human approval needed — executed automatically and logged.</div>
      )}
    </div>
  );
}
