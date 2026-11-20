"""LLM backends with one common interface, so the agent loop doesn't care which model it uses.

    chat(messages, tools) -> {"text": str | None, "tool_calls": [{"id", "name", "args"}]}

Messages use a simple neutral format:
    {"role": "system" | "user" | "assistant", "content": str, "tool_calls": [...]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}
"""
from __future__ import annotations

import json
import os
import uuid

import httpx


class OllamaLLM:
    """Free local model via Ollama (https://ollama.com). Needs a tool-capable model, e.g. qwen2.5:7b or llama3.1."""

    def __init__(self, model: str | None = None, host: str | None = None):
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
        self.host = (host or os.getenv("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        msgs = []
        for m in messages:
            if m["role"] == "tool":
                msgs.append({"role": "tool", "content": m["content"], "tool_name": m["name"]})
            elif m.get("tool_calls"):
                msgs.append({"role": "assistant", "content": m.get("content") or "",
                             "tool_calls": [{"function": {"name": c["name"], "arguments": c["args"]}} for c in m["tool_calls"]]})
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        body = {"model": self.model, "messages": msgs, "stream": False, "options": {"temperature": 0},
                "tools": [{"type": "function", "function": t} for t in tools]}
        r = httpx.post(f"{self.host}/api/chat", json=body, timeout=180)
        r.raise_for_status()
        msg = r.json()["message"]
        calls = [{"id": uuid.uuid4().hex[:8], "name": c["function"]["name"],
                  "args": c["function"].get("arguments") or {}} for c in msg.get("tool_calls") or []]
        return {"text": msg.get("content") or None, "tool_calls": calls}


class ClaudeLLM:
    """Anthropic Claude (paid API). Set ANTHROPIC_API_KEY."""

    def __init__(self, model: str | None = None):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        out: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                continue
            if m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif m.get("tool_calls"):
                content = ([{"type": "text", "text": m["content"]}] if m.get("content") else []) + [
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]} for c in m["tool_calls"]]
                out.append({"role": "assistant", "content": content})
            else:
                out.append({"role": m["role"], "content": m["content"]})
        resp = self.client.messages.create(
            model=self.model, max_tokens=1024, system=system, messages=out,
            tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools])
        text = "".join(b.text for b in resp.content if b.type == "text") or None
        calls = [{"id": b.id, "name": b.name, "args": b.input} for b in resp.content if b.type == "tool_use"]
        return {"text": text, "tool_calls": calls}


class ScriptedLLM:
    """Plays back a fixed list of responses. Used in tests (no model, no cost)."""

    def __init__(self, script: list[dict]):
        self.script, self.calls = list(script), []

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        self.calls.append(json.loads(json.dumps(messages, default=str)))
        if not self.script:
            return {"text": "Out of script.", "tool_calls": []}
        step = self.script.pop(0)
        return {"text": step.get("text"), "tool_calls": [
            {"id": f"c{i}", "name": c["name"], "args": c.get("args", {})} for i, c in enumerate(step.get("tool_calls", []))]}


def make_llm(kind: str | None = None):
    kind = kind or os.getenv("LLM_BACKEND", "ollama")
    return ClaudeLLM() if kind == "claude" else OllamaLLM()
