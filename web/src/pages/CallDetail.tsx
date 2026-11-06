import { api } from "../api";
import { fmtDateTime, fmtDuration, fmtTime } from "../format";
import { Link } from "../App";
import { Badge, Card, Empty, ErrorNote, Json, statusTone, useHealth, usePoll } from "../ui";

export default function CallDetailPage({ id }: { id: string }) {
  const tz = useHealth()?.business.timezone ?? "UTC";
  const { data: c, error } = usePoll(() => api.call(id), 4000, [id]);
  if (error && !c) return <><Link to="/calls" className="text-sm text-accent no-underline">Back to calls</Link><ErrorNote message={error} /></>;
  if (!c) return <p className="text-dim">Loading call…</p>;
  return (
    <>
      <Link to="/calls" className="text-sm text-accent no-underline">Back to calls</Link>
      <div className="flex flex-wrap items-center gap-3"><h1 className="m-0 text-2xl font-bold">Call {c.id.slice(0, 8)}</h1><Badge tone={statusTone(c.status)}>{c.status}</Badge>{c.intent && <Badge tone="accent">intent: {c.intent}</Badge>}</div>
      {c.handoff_reason && <div role="alert" className="rounded-2xl border border-warn bg-warn/10 px-4 py-3 text-sm"><strong className="text-warn">Human handoff requested.</strong> Reason: {c.handoff_reason}</div>}
      <p className="m-0 text-sm text-dim">Started {fmtDateTime(c.started_at, tz)}, {fmtDuration(c.started_at, c.ended_at)}. Caller: {c.customer_name ? `${c.customer_name} (${c.customer_phone})` : "not identified"}.</p>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Transcript">
          {!c.messages.length ? <Empty>No messages.</Empty> : (
            <ol className="m-0 list-none space-y-2 p-0">{c.messages.map((m) => (
              <li key={m.seq} className="text-sm"><span className={`mr-2 text-xs ${m.role === "caller" ? "text-accent" : "text-dim"}`}>{fmtTime(m.created_at, tz)} {m.role}</span>{m.content}</li>))}</ol>
          )}
        </Card>
        <Card title={`Tool calls (${c.tool_calls.length})`}>
          {!c.tool_calls.length ? <Empty>No tools were used.</Empty> : (
            <ul className="m-0 list-none space-y-2 p-0">{c.tool_calls.map((t) => (
              <li key={t.id} className="rounded-xl border border-line bg-raised px-3 py-2"><details>
                <summary className="flex cursor-pointer items-center justify-between gap-2 text-sm"><span><span className="font-mono">{t.name}</span> <span className="text-xs text-dim">turn {t.turn}</span></span>
                  <span className="flex items-center gap-2"><span className="text-xs text-dim">{t.duration_ms} ms</span><Badge tone={statusTone(t.status)}>{t.status.replace("_", " ")}</Badge></span></summary>
                <div className="mt-2 space-y-2"><Json value={t.arguments} /><Json value={t.result} /></div></details></li>))}</ul>
          )}
        </Card>
      </div>
      <Card title="Appointments from this call">
        {!c.appointments.length ? <Empty>No appointment was booked on this call.</Empty> : (
          <table><thead><tr><th>When</th><th>Service</th><th>Status</th></tr></thead><tbody>{c.appointments.map((a) => (
            <tr key={a.id}><td>{fmtDateTime(a.starts_at, tz)}</td><td>{a.service}</td><td><Badge tone={statusTone(a.status)}>{a.status}</Badge></td></tr>))}</tbody></table>
        )}
      </Card>
    </>
  );
}
