import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


class LLMError(Exception):
    pass


@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCallRequest:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: "call_" + uuid.uuid4().hex[:12])


@dataclass
class LLMMessage:
    role: str                                   # system | user | assistant | tool
    content: str | None = None
    tool_calls: list[ToolCallRequest] | None = None
    tool_call_id: str | None = None
    name: str | None = None                     # tool name when role == tool


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)


class LLMProvider(Protocol):
    name: str
    is_mock: bool

    async def chat(self, messages: list[LLMMessage], tools: list[ToolSchema]) -> LLMResponse: ...


def to_openai_messages(messages: list[LLMMessage]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""})
        elif m.role == "assistant" and m.tool_calls:
            out.append({"role": "assistant", "content": m.content, "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in m.tool_calls]})
        else:
            out.append({"role": m.role, "content": m.content or ""})
    return out


class OpenAICompatibleLLM:
    """Any server implementing POST {base_url}/chat/completions with tool calling (Groq, OpenAI, OpenRouter, Ollama...)."""

    name, is_mock = "openai", False

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30):
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
        self._client = httpx.AsyncClient(timeout=timeout)

    async def chat(self, messages: list[LLMMessage], tools: list[ToolSchema]) -> LLMResponse:
        payload = {"model": self.model, "temperature": 0.2, "max_tokens": 400, "messages": to_openai_messages(messages),
                   "tools": [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools],
                   "tool_choice": "auto"}
        for attempt in range(3):
            try:
                resp = await self._client.post(f"{self.base_url}/chat/completions", json=payload, headers={"Authorization": f"Bearer {self.api_key}"})
            except httpx.HTTPError as e:
                raise LLMError(f"LLM request failed: {e}") from e
            if resp.status_code == 429 or resp.status_code >= 500:
                await asyncio.sleep(1.5 * 2**attempt)
                continue
            if resp.status_code >= 400:
                raise LLMError(f"LLM error {resp.status_code}: {resp.text[:200]}")
            try:
                msg = resp.json()["choices"][0]["message"]
            except (KeyError, IndexError, ValueError) as e:
                raise LLMError("LLM returned an unexpected payload") from e
            calls = []
            for c in msg.get("tool_calls") or []:
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append(ToolCallRequest(c["function"]["name"], args if isinstance(args, dict) else {}, c.get("id") or "call_" + uuid.uuid4().hex[:12]))
            return LLMResponse(msg.get("content"), calls)
        raise LLMError("LLM kept returning rate-limit or server errors")
