"""The agent loop (ReAct style: think → act with a tool → observe → repeat).

    question
       │
       ▼
   ┌───────────────────────────┐
   │ LLM sees question + tools │◄─────────────┐
   └────────────┬──────────────┘              │
                │                             │ tool result (or error)
       wants a tool?  ──yes──► run tool in code ┘
                │ no
                ▼
          final answer   (stop after MAX_STEPS no matter what)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .tools import TOOL_SPECS, SqlTools, call_tool

MAX_STEPS = 8

SYSTEM_PROMPT = """You are a careful data analyst agent with access to a SQLite database.

How to work:
1. If you don't know the schema, call list_tables, then describe_table for the tables you need.
2. Write ONE read-only SQLite SELECT query with run_sql. Use exact column names from describe_table.
3. If run_sql returns an error or an unexpected result, fix the query and try again.
4. Answer ONLY from query results — never guess numbers. Round money to 2 decimals.

Data notes: dates are TEXT in YYYY-MM-DD format. Revenue = SUM(quantity * unit_price) from order_items,
counting only orders with status = 'completed' unless the user asks otherwise.

Final answer format: a short direct answer, then the line "SQL used:" followed by the final query."""


@dataclass
class AgentResult:
    question: str
    answer: str
    steps: list[dict] = field(default_factory=list)   # trace of every tool call
    stopped_reason: str = "answered"                   # answered | max_steps | llm_error

    @property
    def sql_queries(self) -> list[str]:
        return [s["args"].get("query", "") for s in self.steps if s["tool"] == "run_sql"]


class SqlAgent:
    def __init__(self, llm, tools: SqlTools, max_steps: int = MAX_STEPS):
        self.llm, self.tools, self.max_steps = llm, tools, max_steps

    def run(self, question: str, verbose: bool = False) -> AgentResult:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}]
        result = AgentResult(question=question, answer="")

        for step in range(1, self.max_steps + 1):
            try:
                reply = self.llm.chat(messages, TOOL_SPECS)
            except Exception as e:  # network / model errors shouldn't crash the app
                result.answer, result.stopped_reason = f"LLM error: {e}", "llm_error"
                return result

            if not reply["tool_calls"]:  # the model is done
                result.answer = (reply["text"] or "").strip()
                return result

            messages.append({"role": "assistant", "content": reply["text"] or "", "tool_calls": reply["tool_calls"]})
            for call in reply["tool_calls"]:
                output = call_tool(self.tools, call["name"], call["args"])
                result.steps.append({"step": step, "tool": call["name"], "args": call["args"],
                                     "ok": "error" not in output, "output_preview": str(output)[:200]})
                if verbose:
                    status = "✗" if "error" in output else "✓"
                    print(f"  [{step}] {status} {call['name']}({json.dumps(call['args'])[:120]})")
                messages.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"],
                                 "content": json.dumps(output, default=str)})

        result.answer = "I couldn't finish within the step limit. Try a more specific question."
        result.stopped_reason = "max_steps"
        return result
