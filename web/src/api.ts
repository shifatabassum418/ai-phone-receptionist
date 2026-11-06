export type CallStatus = "active" | "ended" | "transferred";
export interface Component { provider: string; mode: "mock" | "live"; detail: string }
export interface Health {
  status: string; mock_mode: boolean; components: Record<string, Component>; require_confirmation: boolean;
  business: { name: string; phone: string; address: string; timezone: string; hours: Record<string, [string, string] | null>; services: { name: string; duration_min: number }[] };
}
export interface CallSummary {
  id: string; started_at: string; ended_at: string | null; status: CallStatus; intent: string | null; handoff_reason: string | null;
  customer_name: string | null; customer_phone: string | null; message_count: number; tool_call_count: number; appointments_booked: number;
}
export interface Message { seq: number; role: "caller" | "agent" | "system"; content: string; created_at: string }
export interface ToolCallRow { id: string; turn: number; name: string; arguments: Record<string, unknown> | null; result: Record<string, unknown> | null; status: string; duration_ms: number; created_at: string }
export interface Appointment {
  id: string; customer_id: string; customer_name: string; customer_phone: string; service: string; starts_at: string; ends_at: string;
  status: "booked" | "cancelled"; session_id: string | null; created_at: string;
}
export interface CallDetail extends CallSummary { messages: Message[]; tool_calls: ToolCallRow[]; appointments: Appointment[] }
export interface Customer { id: string; name: string; phone: string; created_at: string; appointment_count: number; upcoming_count: number | null }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : Array.isArray(body.detail) ? "Please check the fields and try again." : `Request failed (${res.status})`;
    throw new Error(detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/health"),
  startSession: () => request<{ session_id: string; stream_url: string }>("/calls/session", { method: "POST" }),
  calls: (status?: string) => request<CallSummary[]>(`/calls${status ? `?status=${status}` : ""}`),
  call: (id: string) => request<CallDetail>(`/calls/${id}`),
  handoff: (sessionId: string) => request<unknown>("/handoff", json("POST", { session_id: sessionId })),
  appointments: (status = "all") => request<Appointment[]>(`/appointments?status=${status}`),
  createAppointment: (b: { customer_name: string; phone_number: string; service: string; date: string; time: string }) => request<Appointment>("/appointments", json("POST", b)),
  reschedule: (id: string, date: string, time: string) => request<Appointment>(`/appointments/${id}`, json("PUT", { date, time })),
  cancel: (id: string) => request<void>(`/appointments/${id}`, { method: "DELETE" }),
  customers: () => request<Customer[]>("/customers"),
};
