import { useEffect, useRef, useState } from "react";
import { ChevronDown, Play, ShieldAlert } from "lucide-react";
import { api } from "../lib/api";
import { useApp } from "../lib/store";
import { VERDICT } from "../lib/format";
import { Badge, Button, cx } from "./ui";

const ENV_CLS: Record<string, string> = {
  production: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
  staging: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
  ci: "bg-sky-500/10 text-sky-300 ring-sky-500/30",
};

export function EnvBadge({ env }: { env?: string | null }) {
  if (!env) return null;
  return <Badge className={ENV_CLS[env] ?? undefined}>{env}</Badge>;
}

export default function ReplayMenu({ compact }: { compact?: boolean }) {
  const { scenarios, navigate, toast, upsert } = useApp();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const replay = async (id: string) => {
    setBusy(id);
    try {
      const inc = await api.replay(id);
      upsert(inc);
      setOpen(false);
      navigate(`/incidents/${inc.id}`);
      toast(`Replaying scenario “${id}” → ${inc.id}`);
    } catch (e) {
      toast(String(e), "err");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="relative" ref={ref}>
      <Button onClick={() => setOpen((o) => !o)} className={compact ? "" : "px-4"}>
        <Play size={15} /> Replay scenario <ChevronDown size={14} className={cx("transition", open && "rotate-180")} />
      </Button>
      {open && (
        <div className="absolute right-0 z-30 mt-2 w-[460px] overflow-hidden rounded-xl border border-slate-700 bg-slate-900 shadow-2xl shadow-black/50">
          <div className="border-b border-slate-800 px-4 py-2.5 text-xs text-slate-400">
            Simulated incidents on the Online Boutique microservices. Each replay runs the full agent pipeline.
          </div>
          {scenarios.map((s) => (
            <button key={s.id} onClick={() => replay(s.id)} disabled={!!busy}
              className="group flex w-full items-start gap-3 border-b border-slate-800/70 px-4 py-3 text-left last:border-0 hover:bg-slate-800/60 disabled:opacity-50">
              <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-slate-800 text-xs font-semibold text-slate-300 group-hover:bg-indigo-600 group-hover:text-white">
                {String.fromCharCode(96 + s.order)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium text-slate-100">{s.title}</span>
                </span>
                <span className="mt-1 flex flex-wrap items-center gap-1.5">
                  <Badge>{s.service}</Badge>
                  <EnvBadge env={s.environment} />
                  <Badge className={VERDICT[s.expected.policy_verdict].cls}>
                    {s.expected.blocked && <ShieldAlert size={11} />} gate: {VERDICT[s.expected.policy_verdict].label}
                  </Badge>
                </span>
              </span>
              {busy === s.id && <span className="text-xs text-indigo-300">starting…</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
