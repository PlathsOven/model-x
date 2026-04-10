"""Agent smoke tests. Run: python3 tests/test_agents.py

Covers:
- JSON stripping / coercion helpers
- OpenRouterAgent end-to-end via a fake httpx-compatible client
- Pass / skip semantics
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modelx.agents.base import AgentContext, format_book
from modelx.agents.openrouter import (
    OpenRouterAgent,
    parse_response,
    strip_json,
    to_float,
    to_int,
)
from modelx.matching import BookLevel
from modelx.models import Contract


# ---------- parsing helpers ----------

def test_strip_json_raw():
    assert strip_json('{"a": 1}') == '{"a": 1}'


def test_strip_json_with_fence():
    raw = '```json\n{"a": 1}\n```'
    assert strip_json(raw) == '{"a": 1}'


def test_strip_json_no_lang_fence():
    raw = '```\n{"side": "buy", "size": 3}\n```'
    assert strip_json(raw) == '{"side": "buy", "size": 3}'


def test_strip_json_with_explanation():
    raw = "Sure, here's my decision:\n```json\n{\"x\": 1}\n```\nLet me know if you need more."
    assert strip_json(raw) == '{"x": 1}'


def test_strip_json_brace_only():
    raw = "Here you go: {\"y\": 2} done"
    assert strip_json(raw) == '{"y": 2}'


def test_parse_response_full():
    raw = '```json\n{"bid_price": "100.5", "bid_size": 5}\n```'
    parsed = parse_response(raw)
    assert parsed == {"bid_price": "100.5", "bid_size": 5}


def test_to_float_variants():
    assert to_float(1) == 1.0
    assert to_float(1.5) == 1.5
    assert to_float("2.5") == 2.5
    assert to_float(" 3 ") == 3.0
    assert to_float(None) == 0.0
    assert to_float("nope") == 0.0
    assert to_float(True) == 0.0  # bool excluded so models can't pass True for a number


def test_to_int_variants():
    assert to_int(5) == 5
    assert to_int(5.9) == 5
    assert to_int("7") == 7
    assert to_int("7.0") == 7
    assert to_int(None) == 0
    assert to_int("nope") == 0
    assert to_int(True) == 0


# ---------- format_book ----------

def test_format_book_empty():
    assert format_book([]) == "(empty)"


def test_format_book_sides():
    book = [
        BookLevel("mm-A", "ask", 102.0, 5),
        BookLevel("mm-B", "ask", 100.0, 3),
        BookLevel("mm-C", "bid", 99.0, 4),
        BookLevel("mm-D", "bid", 98.0, 2),
    ]
    out = format_book(book)
    # Asks ascending
    a_idx = out.index("100.0")
    b_idx = out.index("102.0")
    assert a_idx < b_idx
    # Bids descending
    c_idx = out.index("99.0")
    d_idx = out.index("98.0")
    assert c_idx < d_idx
    assert "Asks" in out and "Bids" in out


# ---------- OpenRouterAgent (with fake client) ----------

class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeClient:
    """Records requests and returns canned content."""

    def __init__(self, content):
        self.content = content
        self.last_request = None

    def post(self, url, headers=None, json=None, timeout=None):
        self.last_request = {"url": url, "headers": headers, "body": json, "timeout": timeout}
        return FakeResp(
            {"choices": [{"message": {"content": self.content}}]}
        )


def _ctx(**overrides):
    base = dict(
        account_id="mm-A",
        phase_id="cpi:1000000",
        contract=Contract(id="cpi", name="CPI", description="MoM CPI print"),
        phase_type="MM",
        phase_timestamp=1000000.0,
        position=0,
        pnl=0.0,
        trade_history="(no trades yet)",
        information_log="(no info yet)",
        settlement_date="2026-04-12",
    )
    base.update(overrides)
    return AgentContext(**base)


def test_openrouter_get_quote():
    raw = '```json\n{"bid_price": 100.0, "ask_price": 101.0, "bid_size": "5", "ask_size": 5, "reasoning": "neutral"}\n```'
    fake = FakeClient(raw)
    agent = OpenRouterAgent(model="anthropic/claude-sonnet-4", client=fake)
    quote = agent.get_quote(_ctx())
    assert quote is not None
    assert quote.id == "cpi:1000000:mm-A:q"
    assert quote.phase_id == "cpi:1000000"
    assert quote.contract_id == "cpi"
    assert quote.account_id == "mm-A"
    assert quote.bid_price == 100.0
    assert quote.bid_size == 5
    assert quote.ask_price == 101.0
    assert quote.ask_size == 5
    # Verify request shape
    assert fake.last_request["url"] == "https://openrouter.ai/api/v1/chat/completions"
    body = fake.last_request["body"]
    assert body["model"] == "anthropic/claude-sonnet-4"
    assert any(m["role"] == "system" for m in body["messages"])
    sys_msg = [m for m in body["messages"] if m["role"] == "system"][0]
    # The prompt should include the formatted state.
    assert "MoM CPI print" in sys_msg["content"]
    assert "2026-04-12" in sys_msg["content"]


def test_openrouter_get_order_buy():
    raw = '{"side": "buy", "size": 3, "reasoning": "edge"}'
    fake = FakeClient(raw)
    agent = OpenRouterAgent(model="anthropic/claude-sonnet-4", client=fake)
    order = agent.get_order(_ctx(account_id="hf-X"), book=[BookLevel("mm-A", "ask", 100.0, 5)])
    assert order is not None
    assert order.account_id == "hf-X"
    assert order.side == "buy"
    assert order.size == 3
    assert order.id == "cpi:1000000:hf-X:o"


def test_openrouter_get_order_pass_explicit():
    raw = '{"side": "pass", "size": 0}'
    fake = FakeClient(raw)
    agent = OpenRouterAgent(model="anthropic/claude-sonnet-4", client=fake)
    order = agent.get_order(_ctx(account_id="hf-X"), book=[])
    assert order is None


def test_openrouter_get_order_pass_via_zero_size():
    raw = '{"side": "buy", "size": 0}'
    fake = FakeClient(raw)
    agent = OpenRouterAgent(model="anthropic/claude-sonnet-4", client=fake)
    order = agent.get_order(_ctx(account_id="hf-X"), book=[])
    assert order is None


def test_openrouter_get_order_invalid_side():
    raw = '{"side": "hold", "size": 5}'
    fake = FakeClient(raw)
    agent = OpenRouterAgent(model="anthropic/claude-sonnet-4", client=fake)
    order = agent.get_order(_ctx(account_id="hf-X"), book=[])
    assert order is None


def test_openrouter_traces_capture_quote_and_order():
    """Every successful call appends a trace with prompt, raw, parsed, decision."""
    quote_raw = '{"bid_price": 100, "ask_price": 101, "bid_size": 5, "ask_size": 5, "reasoning": "neutral stance"}'
    fake = FakeClient(quote_raw)
    agent = OpenRouterAgent(model="anthropic/claude-sonnet-4", client=fake)
    agent.get_quote(_ctx())
    assert len(agent.traces) == 1
    t = agent.traces[0]
    assert t["phase"] == "MM"
    assert t["phase_id"] == "cpi:1000000"
    assert t["account_id"] == "mm-A"
    assert t["model"] == "anthropic/claude-sonnet-4"
    assert "neutral stance" in t["raw_response"]
    assert t["parsed"]["reasoning"] == "neutral stance"
    assert t["decision"] == {
        "bid_price": 100.0, "bid_size": 5, "ask_price": 101.0, "ask_size": 5,
    }
    assert t["error"] is None

    # Now an HF call on the same agent — second trace.
    fake.content = '{"side": "sell", "size": 4, "reasoning": "rich"}'
    agent.get_order(_ctx(account_id="hf-X"), book=[])
    assert len(agent.traces) == 2
    t2 = agent.traces[1]
    assert t2["phase"] == "HF"
    assert t2["decision"] == {"side": "sell", "size": 4}
    assert t2["parsed"]["reasoning"] == "rich"


def test_openrouter_traces_capture_pass_and_error():
    """Pass decisions and parse failures both leave a trace behind."""
    fake = FakeClient('{"side": "pass", "size": 0}')
    agent = OpenRouterAgent(model="anthropic/claude-sonnet-4", client=fake)
    agent.get_order(_ctx(account_id="hf-X"), book=[])
    assert agent.traces[-1]["decision"] == {"side": "pass", "size": 0}
    assert agent.traces[-1]["error"] is None

    # Garbage response — parse fails, trace records the error and re-raises.
    fake.content = "not even close to JSON"
    try:
        agent.get_order(_ctx(account_id="hf-X"), book=[])
    except Exception:
        pass
    err_trace = agent.traces[-1]
    assert err_trace["error"] is not None
    assert err_trace["raw_response"] == "not even close to JSON"
    assert err_trace["decision"] is None


TESTS = [
    test_strip_json_raw,
    test_strip_json_with_fence,
    test_strip_json_no_lang_fence,
    test_strip_json_with_explanation,
    test_strip_json_brace_only,
    test_parse_response_full,
    test_to_float_variants,
    test_to_int_variants,
    test_format_book_empty,
    test_format_book_sides,
    test_openrouter_get_quote,
    test_openrouter_get_order_buy,
    test_openrouter_get_order_pass_explicit,
    test_openrouter_get_order_pass_via_zero_size,
    test_openrouter_get_order_invalid_side,
    test_openrouter_traces_capture_quote_and_order,
    test_openrouter_traces_capture_pass_and_error,
]


if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failures += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failures += 1
    print()
    if failures:
        print(f"{failures} of {len(TESTS)} tests failed")
        sys.exit(1)
    print(f"All {len(TESTS)} tests passed")
