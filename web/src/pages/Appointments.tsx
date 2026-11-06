import { useState, type FormEvent } from "react";
import { api, type Appointment } from "../api";
import { fmtDateTime, todayIn } from "../format";
import { Badge, Card, Empty, ErrorNote, statusTone, useHealth, usePoll } from "../ui";

const field = "rounded-xl border border-line bg-ink px-2 py-1.5 text-sm";
const button = "rounded-xl px-3 py-1.5 text-sm font-semibold";

function NewAppointment({ onDone }: { onDone: () => void }) {
  const health = useHealth();
  const tz = health?.business.timezone ?? "UTC";
  const [form, setForm] = useState({ customer_name: "", phone_number: "", service: health?.business.services[0]?.name ?? "", date: todayIn(tz, 1), time: "10:00" });
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try { await api.createAppointment({ ...form, service: form.service || health!.business.services[0].name }); setForm({ ...form, customer_name: "", phone_number: "" }); onDone(); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not create the appointment"); }
  }
  return (
    <Card title="New appointment">
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-dim">Name<input required value={form.customer_name} onChange={set("customer_name")} className={`${field} mt-1 block`} /></label>
        <label className="text-xs text-dim">Phone<input required minLength={7} value={form.phone_number} onChange={set("phone_number")} className={`${field} mt-1 block`} /></label>
        <label className="text-xs text-dim">Service<select value={form.service} onChange={set("service")} className={`${field} mt-1 block`}>{health?.business.services.map((s) => <option key={s.name}>{s.name}</option>)}</select></label>
        <label className="text-xs text-dim">Date<input type="date" required value={form.date} onChange={set("date")} className={`${field} mt-1 block`} /></label>
        <label className="text-xs text-dim">Time<input type="time" required step={900} value={form.time} onChange={set("time")} className={`${field} mt-1 block`} /></label>
        <button type="submit" className={`${button} btn-primary`}>Book</button>
      </form>
      {error && <div className="mt-3"><ErrorNote message={error} /></div>}
    </Card>
  );
}

function Row({ a, tz, onChange }: { a: Appointment; tz: string; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const [date, setDate] = useState(todayIn(tz, 1));
  const [time, setTime] = useState("10:00");
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>) => { setError(null); try { await fn(); setEditing(false); onChange(); } catch (e) { setError(e instanceof Error ? e.message : "Failed"); } };
  return (
    <tr>
      <td>{fmtDateTime(a.starts_at, tz)}</td><td>{a.customer_name}<div className="text-xs text-dim">{a.customer_phone}</div></td><td>{a.service}</td>
      <td><Badge tone={statusTone(a.status)}>{a.status}</Badge></td>
      <td>{a.status === "booked" && (editing ? (
        <div className="flex flex-wrap items-center gap-2"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} className={field} aria-label="New date" /><input type="time" step={900} value={time} onChange={(e) => setTime(e.target.value)} className={field} aria-label="New time" />
          <button onClick={() => void run(() => api.reschedule(a.id, date, time))} className={`${button} btn-primary`}>Move</button><button onClick={() => setEditing(false)} className="text-sm text-dim">Close</button></div>
      ) : (
        <div className="flex gap-3 text-sm"><button onClick={() => setEditing(true)} className="text-accent">Reschedule</button>
          <button onClick={() => { if (window.confirm(`Cancel ${a.customer_name}'s ${a.service}?`)) void run(() => api.cancel(a.id)); }} className="text-bad">Cancel</button></div>
      ))}{error && <div className="mt-1 text-xs text-bad" role="alert">{error}</div>}</td>
    </tr>
  );
}

export default function Appointments() {
  const tz = useHealth()?.business.timezone ?? "UTC";
  const [filter, setFilter] = useState<"booked" | "cancelled" | "all">("booked");
  const list = usePoll(() => api.appointments(filter), 5000, [filter]);
  return (
    <>
      <div className="flex items-center justify-between"><h1 className="m-0 text-2xl font-bold">Appointments</h1>
        <div role="tablist" className="flex gap-1">{(["booked", "cancelled", "all"] as const).map((f) => (
          <button key={f} role="tab" aria-selected={filter === f} onClick={() => setFilter(f)} className={`rounded-xl px-3 py-1 text-sm ${filter === f ? "bg-raised font-semibold" : "text-dim"}`}>{f === "booked" ? "Booked" : f === "cancelled" ? "Cancelled" : "All"}</button>))}</div></div>
      <NewAppointment onDone={list.reload} />
      <ErrorNote message={list.error} />
      <Card>
        {!list.data?.length ? <Empty>No appointments in this view.</Empty> : (
          <div className="overflow-x-auto"><table><thead><tr><th>When</th><th>Customer</th><th>Service</th><th>Status</th><th>Actions</th></tr></thead><tbody>
            {list.data.map((a) => <Row key={a.id} a={a} tz={tz} onChange={list.reload} />)}</tbody></table></div>
        )}
      </Card>
    </>
  );
}
