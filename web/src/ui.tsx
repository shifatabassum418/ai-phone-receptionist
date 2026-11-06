import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { Health } from "./api";
import type { useCall } from "./useCall";

export const HealthContext = createContext<Health | null>(null);
export const useHealth = () => useContext(HealthContext);

/** Re-runs `load` now and every `ms`; returns the latest data and any error. */
export function usePoll<T>(load: () => Promise<T>, ms = 5000, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let stop = false;
    const run = () => load().then((d) => { if (!stop) { setData(d); setError(null); } }).catch((e: Error) => { if (!stop) setError(e.message); });
    void run();
    const id = window.setInterval(run, ms);
    return () => { stop = true; window.clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, tick, ...deps]);
  return { data, error, reload: () => setTick((t) => t + 1) };
}

const TONES = { good: "border-good/50 text-good", warn: "border-warn/60 text-warn", bad: "border-bad/60 text-bad", accent: "border-accent/60 text-accent", dim: "border-line text-dim" };
export type Tone = keyof typeof TONES;

export const Badge = ({ tone = "dim", children }: { tone?: Tone; children: ReactNode }) => (
  <span className={`inline-block whitespace-nowrap rounded-full border bg-white/[0.03] px-2.5 py-0.5 text-xs font-medium ${TONES[tone]}`}>{children}</span>
);

export const statusTone = (s: string): Tone =>
  ({ active: "accent", ended: "dim", transferred: "warn", booked: "good", cancelled: "dim", ok: "good", error: "warn", failed: "bad", blocked: "bad", pending_confirmation: "accent" } as Record<string, Tone>)[s] ?? "dim";

export const Card = ({ title, right, children, className = "" }: { title?: string; right?: ReactNode; children: ReactNode; className?: string }) => (
  <section className={`rounded-3xl border border-line bg-panel/80 shadow-[0_10px_40px_-20px_rgba(76,29,149,.6)] backdrop-blur ${className}`}>
    {(title || right) && <header className="flex items-center justify-between gap-2 border-b border-line px-5 py-3"><h2 className="m-0 text-base font-semibold">{title}</h2>{right}</header>}
    <div className="p-5">{children}</div>
  </section>
);

export const Empty = ({ children }: { children: ReactNode }) => <p className="m-0 py-4 text-center text-sm text-dim">{children}</p>;
export const ErrorNote = ({ message }: { message: string | null }) => message ? <p role="alert" className="m-0 rounded-xl border border-bad/50 bg-bad/10 px-3 py-2 text-sm text-bad">{message}</p> : null;

export const Json = ({ value }: { value: unknown }) => (
  <pre className="m-0 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-ink p-3 font-mono text-xs text-dim">{JSON.stringify(value, null, 2)}</pre>
);

export function MockBanner() {
  const health = useHealth();
  if (!health?.mock_mode) return null;
  const mocks = Object.entries(health.components).filter(([, c]) => c.mode === "mock");
  const label: Record<string, string> = { stt: "speech recognition", llm: "language model", tts: "voice", calendar: "calendar", notifier: "text messages" };
  return (
    <div role="status" className="mx-4 mt-4 rounded-2xl border border-warn/40 bg-warn/10 px-4 py-2.5 text-sm text-warn lg:mx-6 lg:mt-6">
      <strong>MOCK MODE.</strong> Simulated: {mocks.map(([k]) => label[k]).join(", ")}. Bookings are real records in the local database and calendar, but nothing external is contacted.
    </div>
  );
}

/** The live call is owned by App so it keeps running while you browse other pages. */
export const CallContext = createContext<ReturnType<typeof useCall> | null>(null);
export function useCallControls() {
  const ctl = useContext(CallContext);
  if (!ctl) throw new Error("CallContext is missing");
  return ctl;
}
