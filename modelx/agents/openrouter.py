"""OpenRouter-backed Agent: one integration that works with any model on the OpenRouter API.

`httpx` is imported lazily inside `_call` so the rest of the agent module
(parsing helpers, type coercion, fakes used in tests) loads on a system
without httpx installed.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

from ..matching import BookLevel
from ..models import Order, Quote
from .base import Agent, AgentContext, format_book
from .prompts import HF_SYSTEM_PROMPT, MM_SYSTEM_PROMPT


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# ---------- parsing / coercion helpers ----------

def strip_json(text: str) -> str:
    """Extract a JSON object from a model response.

    Strips ``` fences (with or without language tag), then trims to the
    outermost {...} block. Returns the original text if no braces found.
    """
    text = text.strip()
    if text.startswith("```"):
        # Drop the fence opening line ("```" or "```json").
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
        # Drop the trailing fence if present.
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def to_float(v: Any, default: float = 0.0) -> float:
    if v is None or isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return default
    return default


def to_int(v: Any, default: int = 0) -> int:
    if v is None or isinstance(v, bool):
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        try:
            return int(float(v.strip()))
        except ValueError:
            return default
    return default


def parse_response(text: str) -> Dict[str, Any]:
    """Strip markdown fences then parse the embedded JSON object."""
    return json.loads(strip_json(text))


# ---------- Agent ----------

class OpenRouterAgent(Agent):
    """An Agent driven by any model served via the OpenRouter API.

    Pass `client` to inject a fake httpx-compatible client in tests; the
    real `httpx.post` is only invoked when `client is None`.
    """

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        timeout: float = 60.0,
        client: Optional[Any] = None,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key and client is None:
            raise ValueError(
                "OpenRouterAgent: no API key. Set OPENROUTER_API_KEY or pass api_key="
            )
        self.timeout = timeout
        self._client = client
        # Per-call trace log: each entry has phase / cycle / request / response
        # / parsed JSON / decision (or error). Caller dumps this to disk after
        # the episode for downstream debugging and reasoning audit.
        self.traces: List[Dict[str, Any]] = []

    # ---- HTTP ----

    def _request_args(self, system_prompt: str) -> Dict[str, Any]:
        """Build the headers/body shared by sync and async call paths."""
        return {
            "headers": {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            "json": {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "Submit your decision now."},
                ],
            },
            "timeout": self.timeout,
        }

    def _call(self, system_prompt: str) -> str:
        args = self._request_args(system_prompt)
        if self._client is not None:
            resp = self._client.post(OPENROUTER_URL, **args)
        else:
            import httpx  # lazy: only required for real network calls
            resp = httpx.post(OPENROUTER_URL, **args)
        resp.raise_for_status()
        data = resp.json()
        if "choices" not in data:
            log.error("OpenRouter response missing 'choices' for model=%s: %s",
                      self.model, json.dumps(data)[:2000])
            raise ValueError(
                f"OpenRouter response missing 'choices' for model {self.model}. "
                f"Response: {json.dumps(data)[:500]}"
            )
        return data["choices"][0]["message"]["content"]

    async def _call_async(self, system_prompt: str) -> str:
        """Async variant — opens a per-call AsyncClient so the live scheduler
        can hit many models in parallel without blocking the event loop."""
        args = self._request_args(system_prompt)
        if self._client is not None and hasattr(self._client, "post_async"):
            # Test injection point: a fake client may expose `post_async`.
            resp = await self._client.post_async(OPENROUTER_URL, **args)
        else:
            import httpx
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    OPENROUTER_URL,
                    headers=args["headers"],
                    json=args["json"],
                )
        resp.raise_for_status()
        data = resp.json()
        if "choices" not in data:
            log.error("OpenRouter response missing 'choices' for model=%s: %s",
                      self.model, json.dumps(data)[:2000])
            raise ValueError(
                f"OpenRouter response missing 'choices' for model {self.model}. "
                f"Response: {json.dumps(data)[:500]}"
            )
        return data["choices"][0]["message"]["content"]

    # ---- Agent interface ----

    def _record(
        self,
        phase: str,
        ctx: AgentContext,
        prompt: str,
        raw_response: Optional[str],
        parsed: Optional[Dict[str, Any]],
        decision: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        self.traces.append({
            "phase": phase,
            "cycle_id": ctx.cycle_id,
            "cycle_number": ctx.cycle_number,
            "account_id": ctx.account_id,
            "model": self.model,
            "request": prompt,
            "raw_response": raw_response,
            "parsed": parsed,
            "decision": decision,
            "error": error,
        })

    def _build_mm_prompt(self, ctx: AgentContext) -> str:
        return MM_SYSTEM_PROMPT.format(
            position_limit=ctx.position_limit,
            max_size=ctx.max_size,
            contract_question=ctx.contract.description,
            settlement_date=ctx.settlement_date,
            multiplier=ctx.contract.multiplier,
            position=ctx.position,
            pnl=ctx.pnl,
            cycle_number=ctx.cycle_number,
            total_cycles=ctx.total_cycles,
            trade_history=ctx.trade_history,
            information_log=ctx.information_log,
        )

    def _build_hf_prompt(self, ctx: AgentContext, book: List[BookLevel]) -> str:
        return HF_SYSTEM_PROMPT.format(
            position_limit=ctx.position_limit,
            max_size=ctx.max_size,
            contract_question=ctx.contract.description,
            settlement_date=ctx.settlement_date,
            multiplier=ctx.contract.multiplier,
            position=ctx.position,
            pnl=ctx.pnl,
            cycle_number=ctx.cycle_number,
            total_cycles=ctx.total_cycles,
            trade_history=ctx.trade_history,
            information_log=ctx.information_log,
            order_book=format_book(book),
        )

    def _parse_quote(
        self,
        ctx: AgentContext,
        prompt: str,
        raw_response: str,
    ) -> Quote:
        parsed = parse_response(raw_response)
        quote = Quote(
            id=f"{ctx.cycle_id}:{ctx.account_id}:q",
            cycle_id=ctx.cycle_id,
            contract_id=ctx.contract.id,
            account_id=ctx.account_id,
            bid_price=to_float(parsed.get("bid_price")),
            bid_size=to_int(parsed.get("bid_size")),
            ask_price=to_float(parsed.get("ask_price")),
            ask_size=to_int(parsed.get("ask_size")),
        )
        self._record(
            "MM", ctx, prompt, raw_response, parsed,
            decision={
                "bid_price": quote.bid_price,
                "bid_size": quote.bid_size,
                "ask_price": quote.ask_price,
                "ask_size": quote.ask_size,
            },
        )
        return quote

    def _parse_order(
        self,
        ctx: AgentContext,
        prompt: str,
        raw_response: str,
    ) -> Optional[Order]:
        parsed = parse_response(raw_response)
        side = str(parsed.get("side", "pass")).strip().lower()
        size = to_int(parsed.get("size"))
        if side == "pass" or side not in ("buy", "sell") or size <= 0:
            self._record(
                "HF", ctx, prompt, raw_response, parsed,
                decision={"side": "pass", "size": 0},
            )
            return None
        order = Order(
            id=f"{ctx.cycle_id}:{ctx.account_id}:o",
            cycle_id=ctx.cycle_id,
            contract_id=ctx.contract.id,
            account_id=ctx.account_id,
            side=side,
            size=size,
        )
        self._record(
            "HF", ctx, prompt, raw_response, parsed,
            decision={"side": order.side, "size": order.size},
        )
        return order

    def get_quote(self, ctx: AgentContext) -> Optional[Quote]:
        prompt = self._build_mm_prompt(ctx)
        raw_response: Optional[str] = None
        try:
            raw_response = self._call(prompt)
            return self._parse_quote(ctx, prompt, raw_response)
        except Exception as e:
            self._record(
                "MM", ctx, prompt, raw_response, None,
                error=f"{type(e).__name__}: {e}",
            )
            raise

    def get_order(self, ctx: AgentContext, book: List[BookLevel]) -> Optional[Order]:
        prompt = self._build_hf_prompt(ctx, book)
        raw_response: Optional[str] = None
        try:
            raw_response = self._call(prompt)
            return self._parse_order(ctx, prompt, raw_response)
        except Exception as e:
            self._record(
                "HF", ctx, prompt, raw_response, None,
                error=f"{type(e).__name__}: {e}",
            )
            raise

    async def get_quote_async(self, ctx: AgentContext) -> Optional[Quote]:
        prompt = self._build_mm_prompt(ctx)
        raw_response: Optional[str] = None
        try:
            raw_response = await self._call_async(prompt)
            return self._parse_quote(ctx, prompt, raw_response)
        except Exception as e:
            self._record(
                "MM", ctx, prompt, raw_response, None,
                error=f"{type(e).__name__}: {e}",
            )
            raise

    async def get_order_async(
        self,
        ctx: AgentContext,
        book: List[BookLevel],
    ) -> Optional[Order]:
        prompt = self._build_hf_prompt(ctx, book)
        raw_response: Optional[str] = None
        try:
            raw_response = await self._call_async(prompt)
            return self._parse_order(ctx, prompt, raw_response)
        except Exception as e:
            self._record(
                "HF", ctx, prompt, raw_response, None,
                error=f"{type(e).__name__}: {e}",
            )
            raise
