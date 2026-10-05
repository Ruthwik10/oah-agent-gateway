"""Built-in agent for the web console. Uses the same tool definitions the MCP server publishes.

Providers: NVIDIA API (build.nvidia.com, OpenAI-compatible; set NVIDIA_API_KEY) or Anthropic
(set ANTHROPIC_API_KEY). The model only reads tool results; trust verdicts are computed in code.
"""
from __future__ import annotations

import asyncio
import json
import os
from functools import lru_cache

from . import tools
from .server import INSTRUCTIONS, mcp

TOOL_FUNCS = {
    "data_overview": tools.data_overview, "list_indicators": tools.list_indicators,
    "get_indicator": tools.get_indicator, "one_health_snapshot": tools.one_health_snapshot,
    "check_record": tools.check_record, "explain_indicator": tools.explain_indicator,
    "draft_citizen_observation": tools.draft_citizen_observation,
}


@lru_cache(maxsize=1)
def _schemas() -> tuple:
    ts = asyncio.run(mcp.list_tools())
    return tuple({"name": t.name, "description": t.description, "input_schema": t.inputSchema}
                 for t in ts if t.name in TOOL_FUNCS)


def tool_schemas() -> list[dict]:
    return list(_schemas())


NVIDIA_BASE_URL = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NVIDIA_MODEL = os.environ.get("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")


def agent_provider() -> str | None:
    """NVIDIA (build.nvidia.com) is preferred when its key is set; Anthropic is the fallback."""
    if os.environ.get("NVIDIA_API_KEY"):
        return "nvidia"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def call_tool(name: str, args: dict | None, trace: list) -> str:
    try:
        out = TOOL_FUNCS[name](**(args or {}))
    except Exception as exc:  # bad arguments from the model: report back instead of crashing
        out = {"error": f"{type(exc).__name__}: {exc}"}
    trace.append((name, args or {}, out))
    return json.dumps(out, default=str)


def run_agent_nvidia(question: str, history: list[dict], trace: list, client=None):
    """Tool-calling loop over NVIDIA's OpenAI-compatible API (build.nvidia.com)."""
    if client is None:
        from openai import OpenAI
        client = OpenAI(base_url=NVIDIA_BASE_URL, api_key=os.environ["NVIDIA_API_KEY"])
    oa_tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["input_schema"]}} for t in tool_schemas()]
    msgs = history or [{"role": "system", "content": INSTRUCTIONS}]
    msgs = msgs + [{"role": "user", "content": question}]
    for _ in range(10):
        resp = client.chat.completions.create(model=NVIDIA_MODEL, messages=msgs, tools=oa_tools,
                                              tool_choice="auto", temperature=0.2, max_tokens=2000)
        m = resp.choices[0].message
        calls = m.tool_calls or []
        msgs.append({"role": "assistant", "content": m.content or "",
                     **({"tool_calls": [{"id": c.id, "type": "function",
                                         "function": {"name": c.function.name,
                                                      "arguments": c.function.arguments}} for c in calls]}
                        if calls else {})})
        if not calls:
            return m.content or "(no answer)", msgs
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            msgs.append({"role": "tool", "tool_call_id": c.id,
                         "content": call_tool(c.function.name, args, trace)[:24000]})
    return "Stopped after 10 tool rounds.", msgs


def run_agent_anthropic(question: str, history: list[dict], trace: list):
    import anthropic
    client = anthropic.Anthropic()
    model = os.environ.get("OAH_AGENT_MODEL", "claude-sonnet-5-5")
    msgs = history + [{"role": "user", "content": question}]
    for _ in range(10):
        resp = client.messages.create(model=model, max_tokens=2000, system=INSTRUCTIONS,
                                      tools=tool_schemas(), messages=msgs)
        msgs.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text"), msgs
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": call_tool(b.name, b.input, trace)[:60000]})
        msgs.append({"role": "user", "content": results})
    return "Stopped after 10 tool rounds.", msgs


def run_agent(question: str, history: list[dict], trace: list):
    if agent_provider() == "nvidia":
        return run_agent_nvidia(question, history, trace)
    return run_agent_anthropic(question, history, trace)
