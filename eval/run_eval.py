"""Evaluate the agent: each question's correct answer is computed from the database with a
hand-written "gold" SQL query, then we check whether the agent's answer contains it.

    python eval/run_eval.py                     # Ollama (free, local)
    LLM_BACKEND=claude python eval/run_eval.py  # Claude
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.agent import SqlAgent  # noqa: E402
from agent.llm import make_llm  # noqa: E402
from agent.sample_db import DB_PATH, build  # noqa: E402
from agent.tools import SqlTools  # noqa: E402

QUESTIONS = Path(__file__).parent / "questions.json"


def expected_value(con, gold_sql: str):
    return con.execute(gold_sql).fetchone()[0]


def answer_matches(answer: str, expected) -> bool:
    answer = re.split(r"SQL used:", answer, flags=re.I)[0]  # score the answer, not the SQL shown after it
    if isinstance(expected, (int, float)):
        nums = [float(n.replace(",", "")) for n in re.findall(r"-?\d[\d,]*\.?\d*", answer)]
        tol = max(0.01, abs(expected) * 0.005)  # 0.5% tolerance for rounding
        return any(abs(n - expected) <= tol for n in nums)
    return re.search(rf"\b{re.escape(str(expected).lower())}\b", answer.lower()) is not None


def main() -> None:
    if not DB_PATH.exists():
        build()
    con = sqlite3.connect(DB_PATH)
    agent = SqlAgent(make_llm(), SqlTools(DB_PATH))
    qs = json.loads(QUESTIONS.read_text())
    correct, total_steps, errors_recovered, t0 = 0, 0, 0, time.time()

    for q in qs:
        exp = expected_value(con, q["gold_sql"])
        res = agent.run(q["question"])
        ok = answer_matches(res.answer, exp)
        correct += ok
        total_steps += len(res.steps)
        had_error = any(not s["ok"] for s in res.steps)
        errors_recovered += had_error and ok
        print(f"{'✅' if ok else '❌'} {q['id']}: expected {exp!r} | {len(res.steps)} tool calls"
              f"{' | recovered from SQL error' if had_error and ok else ''}")
        if not ok:
            print(f"     agent said: {res.answer[:160]!r}")

    n = len(qs)
    print(f"\n=== Summary ({n} questions) ===")
    print(f"Accuracy:            {correct}/{n} ({correct / n:.0%})")
    print(f"Avg tool calls:      {total_steps / n:.1f}")
    print(f"Self-corrections:    {errors_recovered} answers correct after a failed query")
    print(f"Time:                {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
