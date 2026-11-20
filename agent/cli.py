"""Ask the agent questions from the terminal.

    python -m agent.cli "Which 3 products made the most revenue?"
    python -m agent.cli                      # interactive mode
    LLM_BACKEND=claude python -m agent.cli "..."
"""
from __future__ import annotations

import sys

from .agent import SqlAgent
from .llm import make_llm
from .sample_db import DB_PATH, build
from .tools import SqlTools


def main() -> None:
    if not DB_PATH.exists():
        build()
    agent = SqlAgent(make_llm(), SqlTools(DB_PATH))
    questions = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else None

    def ask(q: str) -> None:
        print(f"\n❓ {q}\n🔧 Agent steps:")
        res = agent.run(q, verbose=True)
        print(f"\n💬 {res.answer}\n   ({len(res.steps)} tool calls, {res.stopped_reason})")

    if questions:
        ask(questions[0])
        return
    print("SQL Analyst Agent. Ask about the shop database (customers, products, orders). Ctrl+C to quit.")
    while True:
        try:
            q = input("\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if q:
            ask(q)


if __name__ == "__main__":
    main()
