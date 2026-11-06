"""Speech providers. STT turns caller audio (PCM16 mono) into text; TTS turns the agent's reply into audio."""
import io
import wave
from dataclasses import dataclass
from typing import Protocol

import httpx


class SpeechError(Exception):
    pass


@dataclass
class AudioClip:
    data: bytes
    format: str   # mp3 | wav


class STTProvider(Protocol):
    name: str
    is_mock: bool

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str: ...


class TTSProvider(Protocol):
    name: str
    is_mock: bool

    async def synthesize(self, text: str) -> AudioClip | None: ...


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class OpenAICompatibleSTT:
    """POST {base_url}/audio/transcriptions (OpenAI, Groq Whisper, ...)."""

    name, is_mock = "openai", False

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30):
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
        self._client = httpx.AsyncClient(timeout=timeout)

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        try:
            resp = await self._client.post(f"{self.base_url}/audio/transcriptions", headers={"Authorization": f"Bearer {self.api_key}"},
                                           data={"model": self.model, "language": "en", "temperature": "0"},
                                           files={"file": ("audio.wav", pcm_to_wav(pcm, sample_rate), "audio/wav")})
        except httpx.HTTPError as e:
            raise SpeechError(f"STT request failed: {e}") from e
        if resp.status_code >= 400:
            raise SpeechError(f"STT error {resp.status_code}: {resp.text[:200]}")
        return (resp.json().get("text") or "").strip()


class MockSTT:
    """Cannot recognise speech. Test hook: audio that starts with b'MOCK:' is returned as that text; anything else is 'unintelligible'."""

    name, is_mock = "mock", True

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        return pcm[5:].decode("utf-8", "replace").strip() if pcm.startswith(b"MOCK:") else ""


class EdgeTTS:
    name, is_mock = "edge", False

    def __init__(self, voice: str):
        self.voice = voice

    async def synthesize(self, text: str) -> AudioClip | None:
        import edge_tts
        chunks = []
        try:
            async for part in edge_tts.Communicate(text, self.voice).stream():
                if part["type"] == "audio":
                    chunks.append(part["data"])
        except Exception as e:  # noqa: BLE001 - the library raises many unrelated error types
            raise SpeechError(f"TTS failed: {e}") from e
        if not chunks:
            raise SpeechError("TTS returned no audio")
        return AudioClip(b"".join(chunks), "mp3")


class MockTTS:
    """Produces no audio. The dashboard speaks the text with the browser's built-in voice instead."""

    name, is_mock = "mock", True

    async def synthesize(self, text: str) -> AudioClip | None:
        return None
