import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Component } from "./api";
import { AudioQueue, speak, startMic } from "./audio";

export interface LiveMessage { seq: number; role: "caller" | "agent" | "system"; text: string }
export interface LiveTool { key: string; name: string; status: string; duration_ms: number; arguments: Record<string, unknown> | null; result: Record<string, unknown> | null }
export interface SessionStateView {
  intent: string | null; customer_name: string | null; phone_number: string | null; service: string | null; appointment_id: string | null;
  requested_date: string | null; requested_time: string | null; confirmed: boolean; handoff_requested: boolean; awaiting_confirmation: string | null; turns: number;
}
export interface LiveAppointment { id: string; service: string; when: string; status: string }
export interface CallView {
  phase: "idle" | "connecting" | "live" | "ended";
  sessionId: string | null;
  endStatus: string | null;
  messages: LiveMessage[];
  tools: LiveTool[];
  state: SessionStateView | null;
  appointment: LiveAppointment | null;
  handoff: { reason: string; message: string } | null;
  components: Record<string, Component>;
  mockMode: boolean;
  thinking: boolean;
  error: string | null;
}
const IDLE: CallView = { phase: "idle", sessionId: null, endStatus: null, messages: [], tools: [], state: null, appointment: null, handoff: null, components: {}, mockMode: false, thinking: false, error: null };

export function useCall() {
  const [call, setCall] = useState<CallView>(IDLE);
  const [micOn, setMicOn] = useState(false);
  const [level, setLevel] = useState(0);
  const [muted, setMuted] = useState(false);
  const ws = useRef<WebSocket | null>(null);
  const player = useRef(new AudioQueue());
  const audioFormat = useRef("mp3");
  const stopMic = useRef<(() => void) | null>(null);
  const mutedRef = useRef(false);
  mutedRef.current = muted;

  const send = (obj: unknown) => { if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(obj)); };

  const closeMic = useCallback(() => { stopMic.current?.(); stopMic.current = null; setMicOn(false); setLevel(0); }, []);

  const handle = useCallback((e: Record<string, any>) => {  // eslint-disable-line @typescript-eslint/no-explicit-any
    switch (e.type) {
      case "session.ready": setCall((c) => ({ ...c, phase: "live", components: e.components, mockMode: e.mock_mode })); break;
      case "message":
        setCall((c) => c.messages.some((m) => m.seq === e.seq) ? c : { ...c, thinking: e.role === "caller" ? c.thinking : false, messages: [...c.messages, { seq: e.seq, role: e.role, text: e.text }] });
        if (e.role === "agent" && e.voice === "browser" && !mutedRef.current) speak(e.text);
        break;
      case "agent.status": setCall((c) => ({ ...c, thinking: true })); break;
      case "tool.call": setCall((c) => ({ ...c, tools: [{ key: `${e.turn}-${c.tools.length}`, name: e.name, status: e.status, duration_ms: e.duration_ms, arguments: e.arguments, result: e.result }, ...c.tools] })); break;
      case "session.state": setCall((c) => ({ ...c, thinking: false, state: e.state, appointment: e.appointment })); break;
      case "handoff": setCall((c) => ({ ...c, handoff: { reason: e.reason, message: e.message } })); break;
      case "audio": audioFormat.current = e.format; break;
      case "call.ended": setCall((c) => ({ ...c, phase: "ended", endStatus: e.status, thinking: false })); closeMic(); break;
      case "error": setCall((c) => ({ ...c, error: e.message })); break;
    }
  }, [closeMic]);

  const start = useCallback(async () => {
    setCall({ ...IDLE, phase: "connecting" });
    try {
      const { session_id, stream_url } = await api.startSession();
      const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${stream_url}`);
      socket.binaryType = "arraybuffer";
      socket.onmessage = (m) => { if (m.data instanceof ArrayBuffer) player.current.enqueue(m.data, audioFormat.current); else handle(JSON.parse(m.data)); };
      socket.onclose = () => { closeMic(); setCall((c) => (c.phase === "live" || c.phase === "connecting" ? { ...c, phase: "ended", endStatus: c.handoff ? "transferred" : "ended" } : c)); };
      socket.onerror = () => setCall((c) => ({ ...c, error: "The connection to the server failed." }));
      ws.current = socket;
      setCall((c) => ({ ...c, sessionId: session_id }));
    } catch (err) {
      setCall({ ...IDLE, error: err instanceof Error ? err.message : "Could not start a call" });
    }
  }, [handle, closeMic]);

  const hangup = useCallback(() => { send({ type: "hangup" }); player.current.stop(); closeMic(); }, [closeMic]);
  const sendText = useCallback((text: string) => { if (text.trim()) { player.current.stop(); send({ type: "text", text }); } }, []);

  const toggleMic = useCallback(async () => {
    if (stopMic.current) { closeMic(); return; }
    try {
      stopMic.current = await startMic({
        onFrame: (pcm) => ws.current?.readyState === WebSocket.OPEN && ws.current.send(pcm.buffer),
        onLevel: setLevel,
        onSpeechStart: () => { player.current.stop(); send({ type: "interrupt" }); },
        onSpeechEnd: () => send({ type: "audio.end" }),
      });
      setMicOn(true);
    } catch {
      setCall((c) => ({ ...c, error: "Microphone access was blocked. Allow it in the browser to talk to the receptionist." }));
    }
  }, [closeMic]);

  const transfer = useCallback(async () => { if (call.sessionId) await api.handoff(call.sessionId).catch(() => undefined); }, [call.sessionId]);

  useEffect(() => () => { ws.current?.close(); stopMic.current?.(); player.current.stop(); }, []);

  return { call, micOn, level, muted, setMuted, start, hangup, sendText, toggleMic, transfer };
}
