import { Activity, BookMarked, Boxes, FileCode2, GitCommitHorizontal, ScrollText, TestTube2, Workflow } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { Hypothesis } from "../lib/types";
import { humanize, pct } from "../lib/format";
import { Badge, Bar, Empty, cx } from "./ui";

const KIND_ICON: Record<string, ReactNode> = {
  log: <ScrollText size={12} />, metric: <Activity size={12} />, change: <GitCommitHorizontal size={12} />,
  k8s: <Boxes size={12} />, pipeline: <Workflow size={12} />, history: <BookMarked size={12} />, quality: <TestTube2 size={12} />,
};

export default function Hypotheses({ items, onEvidence }: { items: Hypothesis[]; onEvidence: (ref: string) => void }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  if (!items.length) return <Empty icon={<FileCode2 size={28} />} title="No hypotheses yet">The Diagnosis agent ranks root causes once logs are analyzed.</Empty>;
  return (
    <div className="space-y-3">
      {items.map((h) => (
        <div key={h.id} className={cx("rounded-lg border p-3 transition", h.rank === 1 ? "border-indigo-500/40 bg-indigo-500/[0.06]" : "border-slate-800 bg-slate-900/40")}>
          <div className="flex items-start gap-3">
            <div className={cx("flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-sm font-bold",
              h.rank === 1 ? "bg-indigo-600 text-white" : "bg-slate-800 text-slate-400")}>{h.rank}</div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-slate-100">{h.title}</span>
                <Badge className="bg-slate-800 text-slate-300 ring-slate-700 font-mono">{humanize(h.category)}</Badge>
              </div>
              <div className="mt-2 flex items-center gap-3">
                <Bar value={h.confidence} className={h.rank === 1 ? "bg-gradient-to-r from-indigo-500 to-violet-400" : "bg-slate-500"} />
                <span className={cx("w-12 text-right text-sm font-semibold tabular-nums", h.rank === 1 ? "text-indigo-200" : "text-slate-400")}>{pct(h.confidence)}</span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-slate-400">{h.description}</p>
              {h.rank > 1 && h.evidence.length > 0 && (
                <button onClick={() => setExpanded((e) => ({ ...e, [h.id]: !e[h.id] }))} className="mt-1.5 text-[11px] text-indigo-300 hover:underline">
                  {expanded[h.id] ? "hide" : "show"} {h.evidence.length} evidence item{h.evidence.length > 1 ? "s" : ""}
                </button>
              )}
              {h.evidence.length > 0 && (h.rank === 1 || expanded[h.id]) && (
                <ul className="mt-2.5 space-y-1">
                  {h.evidence.map((e, i) => (
                    <li key={i} className="flex items-start gap-2 text-xs">
                      <button onClick={() => onEvidence(e.ref)} title="Show in evidence panel"
                        className="mt-px inline-flex shrink-0 items-center gap-1 rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-indigo-300 hover:bg-indigo-500/20 hover:text-indigo-200">
                        {KIND_ICON[e.kind]} {e.ref.length > 34 ? e.ref.slice(0, 34) + "…" : e.ref}
                      </button>
                      <span className="text-slate-400">{e.description}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
