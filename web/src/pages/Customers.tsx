import { api } from "../api";
import { fmtDateTime } from "../format";
import { Card, Empty, ErrorNote, useHealth, usePoll } from "../ui";

export default function Customers() {
  const tz = useHealth()?.business.timezone ?? "UTC";
  const list = usePoll(() => api.customers(), 8000);
  return (
    <>
      <h1 className="m-0 text-2xl font-bold">Customers</h1>
      <ErrorNote message={list.error} />
      <Card>
        {!list.data?.length ? <Empty>No customers yet. They are added when a caller gives their name and phone number.</Empty> : (
          <table><thead><tr><th>Name</th><th>Phone</th><th>Appointments</th><th>Upcoming</th><th>Customer since</th></tr></thead><tbody>
            {list.data.map((c) => <tr key={c.id}><td>{c.name}</td><td className="font-mono text-xs">{c.phone}</td><td>{c.appointment_count}</td><td>{c.upcoming_count ?? 0}</td><td>{fmtDateTime(c.created_at, tz)}</td></tr>)}</tbody></table>
        )}
      </Card>
    </>
  );
}
