import { api } from "../api";
import { fmtDateTime } from "../format";
import { Link } from "../App";
import { Badge, Card, Empty, ErrorNote, statusTone, useHealth, usePoll } from "../ui";

export default function Dashboard() {
  const tz = useHealth()?.business.timezone ?? "UTC";
  const calls = usePoll(() => api.calls(), 5000);
  const appts = usePoll(() => api.appointments("booked"), 5000);
  const now = Date.now();
  const upcoming = (appts.data ?? []).filter((a) => new Date(a.starts_at).getTime() >= now);
  const transferred = (calls.data ?? []).filter((c) => c.status === "transferred");
  const stats: [string, number, string][] = [
    ["Calls", calls.data?.length ?? 0, "all time"], ["Active calls", (calls.data ?? []).filter((c) => c.status === "active").length, "right now"],
    ["Appointments booked", appts.data?.length ?? 0, "all time"], ["Upcoming", upcoming.length, "still ahead"], ["Transferred to a human", transferred.length, "handoff requested"],
  ];
  return (
    <>
      <div className="flex items-center justify-between"><h1 className="m-0 text-2xl font-bold">Dashboard</h1><Link to="/call" className="rounded-xl btn-primary px-3 py-1.5 text-sm no-underline">Start a test call</Link></div>
      <ErrorNote message={calls.error ?? appts.error} />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {stats.map(([label, value, note]) => (
          <div key={label} className="rounded-3xl border border-line bg-panel/80 px-5 py-4 shadow-[0_10px_40px_-20px_rgba(76,29,149,.6)]"><div className="text-xs text-dim">{label}</div><div className="font-[family-name:var(--font-display)] text-4xl font-bold text-accent-soft">{value}</div><div className="text-xs text-dim">{note}</div></div>
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Recent calls" right={<Link to="/calls" className="text-xs text-accent no-underline">View all</Link>}>
          {!calls.data?.length ? <Empty>No calls yet. Start a test call to see it here.</Empty> : (
            <table><thead><tr><th>Started</th><th>Caller</th><th>Intent</th><th>Status</th></tr></thead><tbody>
              {calls.data.slice(0, 6).map((c) => (
                <tr key={c.id}><td><Link to={`/calls/${c.id}`} className="text-accent no-underline">{fmtDateTime(c.started_at, tz)}</Link></td><td>{c.customer_name ?? "Unknown"}</td>
                  <td>{c.intent ?? "-"}</td><td><Badge tone={statusTone(c.status)}>{c.status}</Badge></td></tr>
              ))}</tbody></table>
          )}
        </Card>
        <Card title="Upcoming appointments" right={<Link to="/appointments" className="text-xs text-accent no-underline">Manage</Link>}>
          {!upcoming.length ? <Empty>No upcoming appointments.</Empty> : (
            <table><thead><tr><th>When</th><th>Customer</th><th>Service</th></tr></thead><tbody>
              {upcoming.slice(0, 6).map((a) => <tr key={a.id}><td>{fmtDateTime(a.starts_at, tz)}</td><td>{a.customer_name}</td><td>{a.service}</td></tr>)}</tbody></table>
          )}
        </Card>
      </div>
      <Card title="Calls transferred to a human">
        {!transferred.length ? <Empty>No calls have needed a human yet.</Empty> : (
          <table><thead><tr><th>Started</th><th>Caller</th><th>Reason</th></tr></thead><tbody>
            {transferred.slice(0, 5).map((c) => <tr key={c.id}><td><Link to={`/calls/${c.id}`} className="text-accent no-underline">{fmtDateTime(c.started_at, tz)}</Link></td><td>{c.customer_name ?? "Unknown"}</td><td>{c.handoff_reason}</td></tr>)}</tbody></table>
        )}
      </Card>
    </>
  );
}
