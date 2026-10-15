import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import type { EvalReport, Health, Incident, Scenario } from "./types";

interface StreamEvent {
  type: string;
  incident_id?: string;
  agent?: string;
  incident?: Incident;
  report?: EvalReport;
  scenario?: string;
  index?: number;
  total?: number;
}

interface AppState {
  incidents: Incident[];
  byId: Record<string, Incident>;
  scenarios: Scenario[];
  health: Health | null;
  connected: boolean;
  lastActive: { incidentId: string; agent: string } | null;
  evalProgress: { scenario: string; index: number; total: number } | null;
  evalReport: EvalReport | null;
  evalRunning: boolean;
  setEvalRunning: (v: boolean) => void;
  setEvalReport: (r: EvalReport | null) => void;
  upsert: (inc: Incident) => void;
  refresh: () => Promise<void>;
  route: string;
  navigate: (path: string) => void;
  toast: (msg: string, kind?: "ok" | "err") => void;
  toasts: { id: number; msg: string; kind: "ok" | "err" }[];
}

const Ctx = createContext<AppState | null>(null);

function currentRoute() {
  return window.location.hash.replace(/^#/, "") || "/incidents";
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [byId, setById] = useState<Record<string, Incident>>({});
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [connected, setConnected] = useState(false);
  const [lastActive, setLastActive] = useState<AppState["lastActive"]>(null);
  const [evalProgress, setEvalProgress] = useState<AppState["evalProgress"]>(null);
  const [evalReport, setEvalReport] = useState<EvalReport | null>(null);
  const [evalRunning, setEvalRunning] = useState(false);
  const [route, setRoute] = useState(currentRoute());
  const [toasts, setToasts] = useState<AppState["toasts"]>([]);
  const toastId = useRef(0);

  const toast = useCallback((msg: string, kind: "ok" | "err" = "ok") => {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);

  const upsert = useCallback((inc: Incident) => {
    if (inc.source === "evaluation") return;
    setById((m) => {
      const prev = m[inc.id];
      if (prev && prev.updated_at > inc.updated_at) return m;
      return { ...m, [inc.id]: inc };
    });
  }, []);

  const refresh = useCallback(async () => {
    const [incs, sc, h] = await Promise.all([api.incidents(), api.scenarios(), api.health()]);
    setById(Object.fromEntries(incs.map((i) => [i.id, i])));
    setScenarios(sc);
    setHealth(h);
  }, []);

  useEffect(() => {
    refresh().catch(() => undefined);
    api.evaluation().then((e) => { setEvalReport(e.report); setEvalRunning(e.running); }).catch(() => undefined);
  }, [refresh]);

  useEffect(() => {
    const onHash = () => setRoute(currentRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      es = new EventSource("/api/stream");
      es.onopen = () => setConnected(true);
      es.onerror = () => {
        setConnected(false);
        es?.close();
        retry = setTimeout(connect, 2000);
      };
      es.onmessage = (m) => {
        const ev = JSON.parse(m.data) as StreamEvent;
        if (ev.incident) upsert(ev.incident);
        if ((ev.type === "agent.started" || ev.type === "approval.required") && ev.incident_id && ev.agent && ev.incident?.source !== "evaluation") {
          setLastActive({ incidentId: ev.incident_id, agent: ev.agent });
        }
        if (ev.type === "evaluation.progress") {
          setEvalRunning(true);
          setEvalProgress({ scenario: ev.scenario ?? "", index: ev.index ?? 0, total: ev.total ?? 0 });
        }
        if (ev.type === "evaluation.completed" && ev.report) {
          setEvalReport(ev.report);
          setEvalRunning(false);
          setEvalProgress(null);
        }
        if (ev.type === "evaluation.failed") {
          setEvalRunning(false);
          setEvalProgress(null);
        }
      };
    };
    connect();
    return () => { es?.close(); if (retry) clearTimeout(retry); };
  }, [upsert]);

  const navigate = useCallback((path: string) => { window.location.hash = path; }, []);

  const incidents = useMemo(
    () => Object.values(byId).sort((a, b) => (a.created_at < b.created_at ? 1 : -1)),
    [byId],
  );

  const value: AppState = {
    incidents, byId, scenarios, health, connected, lastActive, evalProgress, evalReport, evalRunning,
    setEvalRunning, setEvalReport, upsert, refresh, route, navigate, toast, toasts,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside provider");
  return v;
}
