import { useEffect, useState, type ReactNode } from "react";
import { api, type Health } from "./api";
import { CallContext, HealthContext, MockBanner } from "./ui";
import { useCall } from "./useCall";
import Appointments from "./pages/Appointments";
import CallConsole from "./pages/CallConsole";
import CallDetailPage from "./pages/CallDetail";
import Calls from "./pages/Calls";
import Customers from "./pages/Customers";
import Dashboard from "./pages/Dashboard";

const NAV = [["/dashboard", "Dashboard"], ["/call", "Live call"], ["/calls", "Calls"], ["/appointments", "Appointments"], ["/customers", "Customers"]] as const;

export function navigate(path: string) {
  window.history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export const Link = ({ to, children, className = "" }: { to: string; children: ReactNode; className?: string }) => (
  <a href={to} className={className} onClick={(e) => { e.preventDefault(); navigate(to); }}>{children}</a>
);

function usePath() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const on = () => setPath(window.location.pathname);
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  return path;
}

export default function App() {
  const path = usePath();
  const callControls = useCall();
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);
  useEffect(() => { api.health().then(setHealth).catch(() => setOffline(true)); }, []);
  useEffect(() => { if (path === "/") window.history.replaceState(null, "", "/dashboard"); }, [path]);

  const callId = path.startsWith("/calls/") ? path.split("/")[2] : null;
  const page =
    callId ? <CallDetailPage id={callId} /> :
    path === "/call" ? <CallConsole /> :
    path === "/calls" ? <Calls /> :
    path === "/appointments" ? <Appointments /> :
    path === "/customers" ? <Customers /> : <Dashboard />;

  return (
    <HealthContext.Provider value={health}>
      <CallContext.Provider value={callControls}>
      <div className="grid min-h-screen lg:grid-cols-[15rem_1fr]">
        <nav aria-label="Main" className="border-b border-line bg-panel/70 p-4 backdrop-blur lg:sticky lg:top-0 lg:h-screen lg:border-b-0 lg:border-r">
          <div className="mb-5 flex items-center gap-3">
            <span aria-hidden className="btn-primary grid h-10 w-10 shrink-0 place-items-center rounded-2xl text-lg">✦</span>
            <div className="min-w-0"><p className="m-0 text-xs text-dim">AI Phone Receptionist</p>
              <p className="m-0 truncate font-[family-name:var(--font-display)] text-base font-bold">{health?.business.name ?? "Loading…"}</p></div>
          </div>
          <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0 lg:flex-col">
            {NAV.map(([to, label]) => {
              const active = path === to || (to === "/calls" && !!callId) || (to === "/dashboard" && path === "/");
              return <li key={to}><Link to={to} className={`block rounded-xl px-3.5 py-2 text-sm no-underline transition-colors ${active ? "bg-accent/20 font-semibold text-accent-soft ring-1 ring-accent/40" : "text-dim hover:bg-white/5 hover:text-fg"}`}>{label}</Link></li>;
            })}
          </ul>
        </nav>
        <div className="min-w-0">
          <MockBanner />
          <main className="space-y-4 p-4 lg:p-6">
            {offline && <p role="alert" className="m-0 rounded-xl border border-bad/50 bg-bad/10 px-3 py-2 text-sm text-bad">Cannot reach the API. Start the backend (uvicorn app.main:app --port 8000) and reload.</p>}
            {page}
          </main>
        </div>
      </div>
      </CallContext.Provider>
    </HealthContext.Provider>
  );
}
