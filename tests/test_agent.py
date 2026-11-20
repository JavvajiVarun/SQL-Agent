"""Run: pytest -q   (no LLM needed — a scripted fake LLM drives the agent)."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.agent import SqlAgent  # noqa: E402
from agent.llm import ScriptedLLM  # noqa: E402
from agent.sample_db import build  # noqa: E402
from agent.tools import SqlTools, call_tool  # noqa: E402
from eval.run_eval import answer_matches  # noqa: E402


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    return build(tmp_path_factory.mktemp("db") / "shop.db")


@pytest.fixture
def tools(db):
    return SqlTools(db)


# ---------- tools & guardrails ----------
def test_list_and_describe(tools):
    assert set(tools.list_tables()["tables"]) == {"customers", "products", "orders", "order_items"}
    d = tools.describe_table("products")
    assert [c["name"] for c in d["columns"]] == ["product_id", "name", "category", "price"]
    assert "error" in tools.describe_table("users")


@pytest.mark.parametrize("bad", [
    "DROP TABLE orders", "DELETE FROM orders", "UPDATE products SET price = 0",
    "SELECT 1; DROP TABLE orders", "INSERT INTO products VALUES (99,'x','y',1)", "PRAGMA table_info(orders)",
])
def test_writes_and_dangerous_sql_are_blocked(tools, bad):
    assert "error" in tools.run_sql(bad)


def test_database_is_physically_read_only(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM orders")


def test_select_works_and_rows_are_capped(tools):
    r = tools.run_sql("SELECT * FROM order_items")
    assert len(r["rows"]) == 50 and r["truncated"] is True
    assert tools.run_sql("SELECT COUNT(*) AS n FROM customers")["rows"] == [[300]]


def test_sql_errors_are_returned_not_raised(tools):
    assert "SQL error" in tools.run_sql("SELECT nope FROM customers")["error"]


def test_unknown_tool_and_bad_args(tools):
    assert "error" in call_tool(tools, "rm_rf", {})
    assert "error" in call_tool(tools, "describe_table", {"wrong": 1})


# ---------- agent loop ----------
def test_agent_uses_tools_then_answers(tools):
    llm = ScriptedLLM([
        {"tool_calls": [{"name": "list_tables"}]},
        {"tool_calls": [{"name": "run_sql", "args": {"query": "SELECT COUNT(*) FROM customers"}}]},
        {"text": "There are 300 customers.\nSQL used: SELECT COUNT(*) FROM customers"},
    ])
    res = SqlAgent(llm, tools).run("How many customers?")
    assert res.answer.startswith("There are 300")
    assert [s["tool"] for s in res.steps] == ["list_tables", "run_sql"]
    assert res.sql_queries == ["SELECT COUNT(*) FROM customers"]
    # the tool result was fed back to the model before its final answer
    assert any(m["role"] == "tool" and "300" in m["content"] for m in llm.calls[-1])


def test_agent_recovers_from_sql_error(tools):
    llm = ScriptedLLM([
        {"tool_calls": [{"name": "run_sql", "args": {"query": "SELECT COUNT(*) FROM customer"}}]},  # typo
        {"tool_calls": [{"name": "run_sql", "args": {"query": "SELECT COUNT(*) FROM customers"}}]},  # fixed
        {"text": "300 customers."},
    ])
    res = SqlAgent(llm, tools).run("How many customers?")
    assert [s["ok"] for s in res.steps] == [False, True]
    assert "no such table" in llm.calls[1][-1]["content"]  # model saw the error message


def test_agent_stops_at_max_steps(tools):
    llm = ScriptedLLM([{"tool_calls": [{"name": "list_tables"}]}] * 20)
    res = SqlAgent(llm, tools, max_steps=3).run("loop forever")
    assert res.stopped_reason == "max_steps" and len(res.steps) == 3


def test_agent_handles_llm_failure(tools):
    class Broken:
        def chat(self, *a):
            raise ConnectionError("Ollama is not running")
    res = SqlAgent(Broken(), tools).run("anything")
    assert res.stopped_reason == "llm_error" and "Ollama" in res.answer


# ---------- evaluation scoring ----------
def test_answer_matching():
    assert answer_matches("Total revenue is $123,456.78", 123456.78)
    assert answer_matches("about 123,460", 123456.78)          # within 0.5%
    assert not answer_matches("It is 99", 300)
    assert answer_matches("The top product is the Standing Desk.", "Standing Desk")
    assert not answer_matches("See the next page", "TX")                    # whole words only
    assert not answer_matches("Unknown.\nSQL used: SELECT 300", 300)       # SQL text is not scored


def test_gold_queries_all_run(db):
    import json
    con = sqlite3.connect(db)
    qs = json.loads((Path(__file__).resolve().parent.parent / "eval" / "questions.json").read_text())
    for q in qs:
        assert con.execute(q["gold_sql"]).fetchone()[0] is not None, q["id"]


def test_ollama_backend_speaks_ollama_format(tools, monkeypatch):
    """Fake Ollama server: checks the request shape and parses tool calls like the real API."""
    import httpx
    from agent.llm import OllamaLLM
    sent = []

    def fake_post(url, json, timeout):
        sent.append(json)
        if len(sent) == 1:
            msg = {"role": "assistant", "content": "",
                   "tool_calls": [{"function": {"name": "run_sql", "arguments": {"query": "SELECT COUNT(*) FROM customers"}}}]}
        else:
            msg = {"role": "assistant", "content": "There are 300 customers."}
        return httpx.Response(200, json={"message": msg}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    res = SqlAgent(OllamaLLM(model="qwen2.5:7b"), tools).run("How many customers?")
    assert res.answer == "There are 300 customers."
    assert sent[0]["tools"][0]["type"] == "function" and sent[0]["stream"] is False
    tool_msg = sent[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_name"] == "run_sql" and "300" in tool_msg["content"]
