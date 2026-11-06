import { useEffect, useRef, useState } from "react";
import { Link } from "../App";
import { Badge, Card, Empty, ErrorNote, Json, statusTone, useCallControls, useHealth } from "../ui";
import type { LiveTool } from "../useCall";

const STATE_ROWS: [string, string][] = [["customer_name", "Customer"], ["phone_number", "Phone"], ["service", "Service"], ["requested_date", "Requested date"],
  ["requested_time", "Requested time"], ["appointment_id", "Appointment id"], ["confirmed", "Confirmed"], ["handoff_requested", "Handoff requested"], ["awaiting_confirmation", "Awaiting yes/no"]];

function ToolItem({ t }: { t: LiveTool }) {
  return (
    <li className="rounded-xl border border-line bg-raised px-3 py-2">
      <details>
        <summary className="flex cursor-pointer items-center justify-between gap-2 text-sm"><span className="font-mono">{t.name}</span><span className="flex items-center gap-2"><span className="text-xs text-dim">{t.duration_ms} ms</span><Badge tone={statusTone(t.status)}>{t.status.replace("_", " ")}</Badge></span></summary>
        <div className="mt-2 space-y-2"><Json value={t.arguments} /><Json value={t.result} /></div>
      </details>
    </li>
  );
}

export default function CallConsole() {
  const health = useHealth();
  const { call, micOn, level, muted, setMuted, start, hangup, sendText, toggleMic, transfer } = useCallControls();
  const [text, setText] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [call.messages.length, call.thinking]);

  const live = call.phase === "live";
  const sttMock = health?.components.stt?.mode === "mock";
  const phaseLabel = { idle: "No call", connecting: "Connecting…", live: "Call in progress", ended: call.endStatus === "transferred" ? "Call transferred" : "Call ended" }[call.phase];
  const s = call.state;

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3"><h1 className="m-0 text-2xl font-bold">Live call</h1>
          <Badge tone={live ? "accent" : call.phase === "ended" ? "dim" : "dim"}>{live && <span className="pulse mr-1">●</span>}{phaseLabel}</Badge>
          {s?.intent && <Badge tone="accent">intent: {s.intent}</Badge>}</div>
        <div className="flex gap-2">
          {live && <button onClick={() => void transfer()} disabled={!!call.handoff} className="rounded-xl border border-warn/60 px-3 py-1.5 text-sm text-warn">Transfer to human</button>}
          {live ? <button onClick={hangup} className="rounded-xl bg-bad px-3 py-1.5 text-sm font-semibold text-white">Hang up</button>
            : <button onClick={() => void start()} disabled={call.phase === "connecting"} className="rounded-xl btn-primary px-3 py-1.5 text-sm">{call.phase === "ended" ? "Start a new call" : "Start call"}</button>}
        </div>
      </div>
      <ErrorNote message={call.error} />
      {call.handoff && (
        <div role="alert" className="rounded-2xl border border-warn bg-warn/10 px-4 py-3"><strong className="text-warn">{call.handoff.message}</strong><span className="ml-2 text-sm text-dim">Reason: {call.handoff.reason}. The AI no longer replies on this call.</span></div>
      )}
      {call.phase === "idle" && !call.error && <Card><Empty>Start a call to talk to the receptionist as a caller. {sttMock ? "Speech recognition is in mock mode, so type what the caller says." : "Use the microphone or type."}</Empty></Card>}

      {call.phase !== "idle" && (
        <div className="grid gap-4 xl:grid-cols-[1fr_24rem]">
          <Card title="Live transcript" className="flex min-h-[28rem] flex-col" right={<button onClick={() => setMuted(!muted)} className="text-xs text-dim hover:text-fg">{muted ? "Unmute reply voice" : "Mute reply voice"}</button>}>
            <ol aria-live="polite" className="m-0 flex max-h-[26rem] min-h-[18rem] flex-1 list-none flex-col gap-2 overflow-y-auto p-0">
              {call.messages.map((m) => (
                <li key={m.seq} className={m.role === "caller" ? "self-end" : m.role === "system" ? "self-center" : "self-start"}>
                  <div className={`max-w-[34rem] rounded-2xl px-3 py-2 text-sm ${m.role === "caller" ? "bg-accent/20" : m.role === "system" ? "border border-line text-dim" : "bg-raised"}`}>
                    <div className="mb-0.5 text-xs text-dim">{m.role === "caller" ? "Caller" : m.role === "agent" ? "Receptionist" : "System"}</div>{m.text}</div>
                </li>
              ))}
              {call.thinking && <li className="self-start text-sm text-dim"><span className="pulse">Receptionist is thinking…</span></li>}
              <div ref={bottom} />
            </ol>
            <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); sendText(text); setText(""); }}>
              <input value={text} onChange={(e) => setText(e.target.value)} disabled={!live} aria-label="What the caller says" placeholder={live ? "Type what the caller says…" : "Start a call first"}
                className="min-w-0 flex-1 rounded-xl border border-line bg-ink px-3 py-2 text-sm" />
              <button type="submit" disabled={!live || !text.trim()} className="rounded-xl btn-primary px-3 py-2 text-sm">Send</button>
              <button type="button" onClick={() => void toggleMic()} disabled={!live || sttMock} aria-pressed={micOn} title={sttMock ? "Speech recognition is in mock mode. Set STT_PROVIDER=openai to talk." : "Toggle microphone (headphones recommended)"}
                className={`relative overflow-hidden rounded-xl border px-3 py-2 text-sm ${micOn ? "border-good text-good" : "border-line text-fg"}`}>
                {micOn && <span aria-hidden className="absolute inset-y-0 left-0 bg-good/25" style={{ width: `${Math.round(level * 100)}%` }} />}<span className="relative">{micOn ? "Mic on" : "Mic off"}</span>
              </button>
            </form>
          </Card>

          <div className="space-y-4">
            <Card title="Session state">
              {!s ? <Empty>Waiting for the call to start.</Empty> : (
                <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                  {STATE_ROWS.map(([key, label]) => { const v = (s as unknown as Record<string, unknown>)[key]; return (
                    <div key={key} className="contents"><dt className="text-dim">{label}</dt><dd className="m-0 break-all font-mono text-xs leading-6">{v === null || v === "" ? <span className="text-dim">-</span> : String(v)}</dd></div>); })}
                </dl>
              )}
            </Card>
            <Card title="Appointment">
              {!call.appointment ? <Empty>No appointment on this call yet.</Empty> : (
                <div className="text-sm"><div className="font-semibold">{call.appointment.service}</div><div>{call.appointment.when}</div>
                  <div className="mt-1"><Badge tone={statusTone(call.appointment.status)}>{call.appointment.status}</Badge></div></div>
              )}
            </Card>
            <Card title={`Tool calls (${call.tools.length})`}>
              {!call.tools.length ? <Empty>The agent has not used any tools yet.</Empty> : <ul className="m-0 max-h-80 list-none space-y-2 overflow-y-auto p-0">{call.tools.map((t) => <ToolItem key={t.key} t={t} />)}</ul>}
            </Card>
            {call.sessionId && <p className="m-0 text-xs text-dim">Call id: <Link to={`/calls/${call.sessionId}`} className="font-mono text-accent no-underline">{call.sessionId.slice(0, 8)}</Link> (full log)</p>}
          </div>
        </div>
      )}
    </>
  );
}
