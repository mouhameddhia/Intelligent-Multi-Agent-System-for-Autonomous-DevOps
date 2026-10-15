import { Activity, BookOpen, ClipboardList, FlaskConical, Network, Radio } from "lucide-react";
import type { ReactNode } from "react";
import ReplayMenu from "./components/ReplayMenu";
import { cx } from "./components/ui";
import { useApp } from "./lib/store";
import ArchitecturePage from "./pages/Architecture";
import AuditPage from "./pages/Audit";
import EvaluationPage from "./pages/Evaluation";
import IncidentsPage from "./pages/Incidents";
import KnowledgePage from "./pages/Knowledge";

const NAV: { path: string; label: string; icon: ReactNode; title: string; subtitle: string }[] = [
  { path: "/incidents", label: "Incidents", icon: <Activity size={17} />, title: "Incidents", subtitle: "Live feed and agent pipeline for each incident" },
  { path: "/architecture", label: "Architecture", icon: <Network size={17} />, title: "Architecture", subtitle: "Connector layer, agents and the orchestration graph — live" },
  { path: "/evaluation", label: "Evaluation", icon: <FlaskConical size={17} />, title: "Evaluation", subtitle: "Baseline (static alerts + manual investigation) vs the agent system, scored against ground truth" },
  { path: "/audit", label: "Audit log", icon: <ClipboardList size={17} />, title: "Audit log", subtitle: "Every action, policy decision and approval" },
  { path: "/knowledge", label: "Knowledge", icon: <BookOpen size={17} />, title: "Knowledge & policies", subtitle: "History store (TF-IDF similarity) and PolicyGate rules" },
];

export default function App() {
  const { route, navigate, health, connected, incidents, toasts } = useApp();
  const section = "/" + (route.split("/")[1] || "incidents");
  const page = NAV.find((n) => n.path === section) ?? NAV[0];
  const awaiting = incidents.filter((i) => i.status === "awaiting_approval").length;

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="flex w-60 shrink-0 flex-col border-r border-slate-800 bg-slate-950">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 shadow-lg shadow-indigo-900/50">
            <svg viewBox="0 0 32 32" className="h-5 w-5"><path d="M5 21l6-12 5 8 3-4 5 8" stroke="#fff" strokeWidth="2.8" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </div>
          <div>
            <div className="text-[15px] font-semibold tracking-tight text-white">AutoOps</div>
            <div className="text-[11px] text-slate-500">Multi-agent incident response</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 px-3">
          {NAV.map((n) => (
            <button key={n.path} onClick={() => navigate(n.path)}
              className={cx("flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition",
                section === n.path ? "bg-indigo-500/15 text-indigo-200 ring-1 ring-inset ring-indigo-500/30" : "text-slate-400 hover:bg-slate-900 hover:text-slate-200")}>
              {n.icon}
              <span className="flex-1 text-left">{n.label}</span>
              {n.path === "/incidents" && awaiting > 0 && (
                <span className="rounded-full bg-amber-500 px-1.5 text-[10px] font-bold text-slate-950">{awaiting}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="space-y-2 border-t border-slate-800 px-5 py-4 text-[11px] text-slate-500">
          <div className="flex items-center justify-between">
            <span>Live stream</span>
            <span className={cx("flex items-center gap-1.5", connected ? "text-emerald-400" : "text-rose-400")}>
              <Radio size={12} className={connected ? "animate-pulse" : ""} /> {connected ? "connected" : "offline"}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span>LLM</span>
            <span className={health?.mock_llm || health?.llm_chain?.[0] === "mock" ? "text-amber-300" : "text-emerald-300"}>
              {health ? (health.llm_chain[0] === "mock" ? "MOCK (deterministic)" : health.llm_chain[0]) : "…"}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span>Adapters</span>
            <span className="text-slate-300">
              {health ? (Object.values(health.adapters).every((a) => a === "simulated") ? "simulated" : "mixed") : "…"}
            </span>
          </div>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-4 border-b border-slate-800 bg-slate-950/80 px-6 py-3.5 backdrop-blur">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold tracking-tight text-white">{page.title}</h1>
            <p className="truncate text-xs text-slate-500">{page.subtitle}</p>
          </div>
          <ReplayMenu />
        </header>
        <div className="min-h-0 flex-1 overflow-auto">
          {section === "/incidents" && <IncidentsPage />}
          {section === "/architecture" && <ArchitecturePage />}
          {section === "/evaluation" && <EvaluationPage />}
          {section === "/audit" && <AuditPage />}
          {section === "/knowledge" && <KnowledgePage />}
        </div>
      </main>

      <div className="pointer-events-none fixed bottom-4 right-4 z-50 space-y-2">
        {toasts.map((t) => (
          <div key={t.id} className={cx("pointer-events-auto rounded-lg border px-4 py-2.5 text-sm shadow-xl",
            t.kind === "ok" ? "border-emerald-500/30 bg-slate-900 text-emerald-200" : "border-rose-500/40 bg-slate-900 text-rose-200")}>
            {t.msg}
          </div>
        ))}
      </div>
    </div>
  );
}
