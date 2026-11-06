const RATE = 16000;
const FRAME = 1600;            // 100 ms
const END_SILENCE_MS = 900;
const WORKLET = `class Cap extends AudioWorkletProcessor { process(i) { const c = i[0][0]; if (c) this.port.postMessage(c.slice(0)); return true; } } registerProcessor("cap", Cap);`;

export interface MicHandlers {
  onFrame: (pcm: Int16Array) => void;
  onLevel: (level: number) => void;
  onSpeechStart: () => void;
  onSpeechEnd: () => void;
}

function resample(input: Float32Array, from: number): Float32Array {
  if (from === RATE) return input;
  const ratio = from / RATE, out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const p = i * ratio, i0 = Math.floor(p), i1 = Math.min(i0 + 1, input.length - 1);
    out[i] = input[i0] * (1 - (p - i0)) + input[i1] * (p - i0);
  }
  return out;
}

/** Opens the microphone and splits it into utterances with an adaptive energy VAD. Returns a stop function. */
export async function startMic(h: MicHandlers): Promise<() => void> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  const ctx = new AudioContext({ sampleRate: RATE });
  await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" })));
  const node = new AudioWorkletNode(ctx, "cap");
  ctx.createMediaStreamSource(stream).connect(node);
  let pending = new Float32Array(0), noise = 0.004, speaking = false, silentMs = 0, loud = 0;
  const preroll: Int16Array[] = [];

  const handle = (frame: Float32Array) => {
    let sum = 0;
    for (const v of frame) sum += v * v;
    const rms = Math.sqrt(sum / frame.length), isLoud = rms > Math.max(0.012, noise * 3);
    const pcm = new Int16Array(frame.length);
    frame.forEach((v, i) => { const c = Math.max(-1, Math.min(1, v)); pcm[i] = c < 0 ? c * 0x8000 : c * 0x7fff; });
    h.onLevel(Math.min(1, rms * 8));
    if (!speaking) {
      preroll.push(pcm);
      if (preroll.length > 3) preroll.shift();
      if (!isLoud) { noise = Math.min(0.05, noise * 0.95 + rms * 0.05); loud = 0; return; }
      if (++loud < 2) return;
      speaking = true; silentMs = 0; h.onSpeechStart();
      preroll.splice(0).forEach(h.onFrame);
      return;
    }
    h.onFrame(pcm);
    silentMs = isLoud ? 0 : silentMs + 100;
    if (silentMs >= END_SILENCE_MS) { speaking = false; loud = 0; h.onSpeechEnd(); }
  };

  node.port.onmessage = (ev: MessageEvent<Float32Array>) => {
    const chunk = resample(ev.data, ctx.sampleRate), merged = new Float32Array(pending.length + chunk.length);
    merged.set(pending); merged.set(chunk, pending.length);
    let off = 0;
    for (; merged.length - off >= FRAME; off += FRAME) handle(merged.subarray(off, off + FRAME));
    pending = merged.slice(off);
  };
  return () => { node.disconnect(); stream.getTracks().forEach((t) => t.stop()); void ctx.close(); };
}

export class AudioQueue {
  private el = new Audio();
  private queue: string[] = [];
  private playing = false;
  constructor() {
    this.el.onended = () => this.next();
    this.el.onerror = () => this.next();
  }
  enqueue(data: ArrayBuffer, format: string): void {
    this.queue.push(URL.createObjectURL(new Blob([data], { type: format === "mp3" ? "audio/mpeg" : "audio/wav" })));
    if (!this.playing) this.next();
  }
  stop(): void {
    this.queue.forEach(URL.revokeObjectURL);
    this.queue = [];
    this.playing = false;
    this.el.pause();
    if ("speechSynthesis" in window) speechSynthesis.cancel();
  }
  private next(): void {
    const url = this.queue.shift();
    if (!url) { this.playing = false; return; }
    this.playing = true;
    this.el.src = url;
    void this.el.play().catch(() => this.next());
  }
}

export function speak(text: string): void {
  if ("speechSynthesis" in window) speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}
