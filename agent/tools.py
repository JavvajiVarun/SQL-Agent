"""The agent's tools. Each tool = a Python function + a JSON schema the LLM reads.

Safety (guardrails) lives HERE, in code — not in the prompt:
  • the database is opened READ-ONLY (SQLite mode=ro), so writes are impossible
  • only a single SELECT / WITH statement is accepted
  • results are capped at MAX_ROWS, queries are stopped after a time budget
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

MAX_ROWS = 50
TIME_LIMIT_S = 3.0
FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum)\b", re.I)


class SqlTools:
    def __init__(self, db_path: Path):
        if not Path(db_path).exists():
            raise FileNotFoundError(f"Database not found: {db_path}. Run: python -m agent.sample_db")
        self.con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)

    # ---- tool 1 -------------------------------------------------------------
    def list_tables(self) -> dict:
        rows = self.con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        return {"tables": [r[0] for r in rows]}

    # ---- tool 2 -------------------------------------------------------------
    def describe_table(self, table: str) -> dict:
        if table not in self.list_tables()["tables"]:
            return {"error": f"Unknown table '{table}'. Call list_tables first."}
        cols = self.con.execute(f'PRAGMA table_info("{table}")').fetchall()
        sample = self.con.execute(f'SELECT * FROM "{table}" LIMIT 3').fetchall()
        return {"table": table, "columns": [{"name": c[1], "type": c[2]} for c in cols],
                "sample_rows": [list(r) for r in sample]}

    # ---- tool 3 -------------------------------------------------------------
    def run_sql(self, query: str) -> dict:
        q = query.strip().rstrip(";").strip()
        if ";" in q:
            return {"error": "Only one statement is allowed."}
        if not re.match(r"^(select|with)\b", q, re.I) or FORBIDDEN.search(q):
            return {"error": "Only read-only SELECT queries are allowed."}
        deadline = time.monotonic() + TIME_LIMIT_S
        self.con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        try:
            cur = self.con.execute(q)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(MAX_ROWS + 1)
        except sqlite3.Error as e:  # returned to the LLM so it can fix its own query
            return {"error": f"SQL error: {e}"}
        finally:
            self.con.set_progress_handler(None, 0)
        return {"columns": cols, "rows": [list(r) for r in rows[:MAX_ROWS]],
                "truncated": len(rows) > MAX_ROWS}


TOOL_SPECS = [
    {"name": "list_tables", "description": "List all tables in the database.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "describe_table", "description": "Show a table's columns, types and 3 sample rows. Use before writing SQL.",
     "parameters": {"type": "object", "properties": {"table": {"type": "string"}}, "required": ["table"]}},
    {"name": "run_sql", "description": "Run ONE read-only SQLite SELECT query and return up to 50 rows. "
                                       "If it returns an error, fix the query and try again.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]


def call_tool(tools: SqlTools, name: str, args: dict) -> dict:
    fn = {"list_tables": tools.list_tables, "describe_table": tools.describe_table, "run_sql": tools.run_sql}.get(name)
    if fn is None:
        return {"error": f"Unknown tool '{name}'"}
    try:
        return fn(**(args or {}))
    except TypeError as e:
        return {"error": f"Bad arguments for {name}: {e}"}
