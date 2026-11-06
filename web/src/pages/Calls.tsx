import { useState } from "react";
import { api } from "../api";
import { fmtDateTime, fmtDuration } from "../format";
import { Link } from "../App";
import { Badge, Card, Empty, ErrorNote, statusTone, useHealth, usePoll } from "../ui";

export default function Calls() {
  const tz = useHealth()?.business.timezone ?? "UTC";
  const [status, setStatus] = useState("");
  const calls = usePoll(() => api.calls(status || undefined), 5000, [status]);
  return (
    <>
      <div className="flex items-center justify-between"><h1 className="m-0 text-2xl font-bold">Calls</h1>
        <label className="text-sm text-dim">Status <select value={status} onChange={(e) => setStatus(e.target.value)} className="ml-1 rounded-xl border border-line bg-panel px-2 py-1 text-fg">
          <option value="">All</option><option value="active">Active</option><option value="ended">Ended</option><option value="transferred">Transferred</option></select></label></div>
      <ErrorNote message={calls.error} />
      <Card>
        {!calls.data?.length ? <Empty>No calls match. Start a test call from Live call.</Empty> : (
          <div className="overflow-x-auto"><table><thead><tr><th>Started</th><th>Duration</th><th>Caller</th><th>Intent</th><th>Status</th><th>Messages</th><th>Tool calls</th><th>Booked</th></tr></thead><tbody>
            {calls.data.map((c) => (
              <tr key={c.id}>
                <td><Link to={`/calls/${c.id}`} className="text-accent no-underline">{fmtDateTime(c.started_at, tz)}</Link></td><td>{fmtDuration(c.started_at, c.ended_at)}</td>
                <td>{c.customer_name ?? <span className="text-dim">Unknown</span>}</td><td>{c.intent ?? "-"}</td>
                <td><Badge tone={statusTone(c.status)}>{c.status}</Badge>{c.handoff_reason && <div className="mt-1 text-xs text-dim">{c.handoff_reason}</div>}</td>
                <td>{c.message_count}</td><td>{c.tool_call_count}</td><td>{c.appointments_booked}</td>
              </tr>
            ))}</tbody></table></div>
        )}
      </Card>
    </>
  );
}
