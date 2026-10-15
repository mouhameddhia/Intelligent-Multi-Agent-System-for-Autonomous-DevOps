import type { ReactNode } from "react";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

export function Card({ title, icon, right, children, className, bodyClass }: {
  title?: ReactNode; icon?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; bodyClass?: string;
}) {
  return (
    <section className={cx("rounded-xl border border-slate-800 bg-slate-900/60 shadow-sm shadow-black/20", className)}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-800/80 px-4 py-2.5">
          <h3 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wide text-slate-300">
            {icon && <span className="text-slate-400">{icon}</span>}
            {title}
          </h3>
          {right}
        </header>
      )}
      <div className={cx("p-4", bodyClass)}>{children}</div>
    </section>
  );
}

export function Badge({ children, className, title }: { children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset", className ?? "bg-slate-800 text-slate-300 ring-slate-700")}>
      {children}
    </span>
  );
}

export function Button({ children, onClick, variant = "primary", disabled, className, type = "button", title }: {
  children: ReactNode; onClick?: () => void; variant?: "primary" | "ghost" | "danger" | "success" | "outline";
  disabled?: boolean; className?: string; type?: "button" | "submit"; title?: string;
}) {
  const v = {
    primary: "bg-indigo-600 text-white hover:bg-indigo-500 shadow shadow-indigo-900/40",
    success: "bg-emerald-600 text-white hover:bg-emerald-500 shadow shadow-emerald-900/40",
    danger: "bg-rose-600 text-white hover:bg-rose-500 shadow shadow-rose-900/40",
    ghost: "text-slate-300 hover:bg-slate-800",
    outline: "border border-slate-700 text-slate-200 hover:bg-slate-800",
  }[variant];
  return (
    <button type={type} title={title} disabled={disabled} onClick={onClick}
      className={cx("inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-40", v, className)}>
      {children}
    </button>
  );
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center text-slate-500">
      {icon && <div className="text-slate-600">{icon}</div>}
      <div className="text-sm font-medium text-slate-400">{title}</div>
      {children && <div className="max-w-md text-xs">{children}</div>}
    </div>
  );
}

export function Bar({ value, className }: { value: number; className?: string }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
      <div className={cx("h-full rounded-full transition-all duration-700", className ?? "bg-indigo-500")} style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

export function KV({ k, v, mono }: { k: string; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-xs">
      <span className="text-slate-500">{k}</span>
      <span className={cx("text-right text-slate-200", mono && "font-mono")}>{v}</span>
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <span className={cx("inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent", className)} />;
}
