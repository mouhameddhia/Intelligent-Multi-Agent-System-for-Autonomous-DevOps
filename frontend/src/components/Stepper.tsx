import { Check, CircleDashed, Hand, Loader2, Minus, X } from "lucide-react";
import type { AgentStep } from "../lib/types";
import { AGENTS, STEP_COLORS, fmtMs } from "../lib/format";
import { cx } from "./ui";

function StepIcon({ s }: { s: AgentStep["status"] }) {
  if (s === "completed") return <Check size={15} strokeWidth={3} />;
  if (s === "running") return <Loader2 size={15} className="animate-spin" />;
  if (s === "waiting") return <Hand size={14} />;
  if (s === "failed") return <X size={15} strokeWidth={3} />;
  if (s === "skipped") return <Minus size={14} />;
  return <CircleDashed size={14} />;
}

export default function Stepper({ steps, selected, onSelect }: {
  steps: AgentStep[]; selected?: string | null; onSelect?: (agent: string) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <ol className="flex min-w-[860px] items-start">
        {steps.map((st, i) => {
          const meta = AGENTS[st.agent] ?? { label: st.agent, short: st.agent, desc: "" };
          const next = steps[i + 1];
          const lineDone = st.status === "completed" || st.status === "skipped";
          return (
            <li key={st.agent} className="relative flex flex-1 flex-col items-center">
              {next && (
                <div className={cx("absolute left-1/2 top-[17px] h-0.5 w-full", lineDone ? "bg-emerald-500/50" : "bg-slate-800")}>
                  {st.status === "running" && <div className="h-full w-1/2 animate-pulse bg-indigo-400/60" />}
                </div>
              )}
              <button onClick={() => onSelect?.(st.agent)} title={meta.desc}
                className={cx("relative z-10 flex h-9 w-9 items-center justify-center rounded-full border-2 transition",
                  STEP_COLORS[st.status], selected === st.agent && "ring-2 ring-indigo-400 ring-offset-2 ring-offset-slate-900",
                  (st.status === "running" || st.status === "waiting") && "shadow-[0_0_18px] shadow-current")}>
                <StepIcon s={st.status} />
              </button>
              <div className="mt-2 px-1 text-center">
                <div className={cx("text-[12px] font-medium", st.status === "pending" || st.status === "skipped" ? "text-slate-500" : "text-slate-200")}>
                  {meta.label}
                </div>
                <div className="mt-0.5 h-4 text-[10px] text-slate-500">
                  {st.status === "waiting" ? <span className="text-amber-300">waiting for human</span>
                    : st.status === "running" ? <span className="text-indigo-300">running…</span>
                    : st.status === "skipped" ? "skipped"
                    : st.duration_ms != null ? fmtMs(st.duration_ms) : ""}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
