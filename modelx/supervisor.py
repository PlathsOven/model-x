"""Global wall-clock supervisor that drives every active market.

A single `MarketSupervisor.run_all()` coroutine owns the only timer in the
system. It sleeps until the next wall-clock boundary aligned to the global
`phase_duration_seconds` (e.g. with 1800s ticks: 00:00, 00:30, 01:00, …),
then calls `MarketRunner.step()` for every active market in parallel via
`asyncio.gather`.

This guarantees that all markets advance their phases on the same tick.
A new contract added to `contracts.yaml` between ticks joins on the next one.
A settled market is skipped automatically (its `is_active()` returns False).

Concurrency model:
- Single asyncio event loop, no threads.
- All markets share one sqlite3.Connection — the loop serializes db writes.
- LLM calls inside each market run in parallel within the phase deadline.
"""

import asyncio
import math
import sqlite3
import time
from datetime import datetime
from typing import Callable, Dict, List

from .agents.base import Agent
from .config import AgentSpec, GlobalConfig
from .market_runner import MarketRunner, build_runner


AgentFactory = Callable[[AgentSpec], Agent]


class MarketSupervisor:
    """Owns the global tick loop and a runner per configured market."""

    def __init__(
        self,
        config: GlobalConfig,
        db: sqlite3.Connection,
        agent_factory: AgentFactory,
    ):
        self.config = config
        self.db = db
        self.agent_factory = agent_factory
        self._runners: Dict[str, MarketRunner] = {}

    def setup(self) -> None:
        """Build a runner for every market in the config."""
        for market_config in self.config.markets:
            runner = build_runner(
                config=market_config,
                db=self.db,
                agent_specs=self.config.agent_specs,
                agent_factory=self.agent_factory,
            )
            self._runners[market_config.id] = runner
            last_ts = runner.market.last_phase_ts
            ts_str = _fmt_ts(last_ts) if last_ts > 0 else "none"
            print(
                f"[supervisor] loaded market {market_config.id!r}: "
                f"state={runner.market.state}, last_phase={ts_str}, "
                f"pending_mm={runner.market.pending_mm}",
                flush=True,
            )

    async def run_all(self) -> None:
        """Drive the global tick loop until all markets are inactive."""
        if not self._runners:
            self.setup()

        phase_seconds = self.config.phase_duration_seconds
        print(
            f"[supervisor] starting global clock with phase_duration={phase_seconds}s "
            f"({len(self._runners)} markets)",
            flush=True,
        )

        while True:
            active = [r for r in self._runners.values() if r.is_active()]
            if not active:
                print(
                    "[supervisor] no active markets remaining — exiting tick loop",
                    flush=True,
                )
                return

            tick_at = _next_wall_clock_tick(phase_seconds)
            wait = max(0.0, tick_at - time.time())
            print(
                f"[supervisor] next tick at {_fmt_ts(tick_at)} "
                f"(in {wait:.1f}s); {len(active)} active market(s)",
                flush=True,
            )
            await asyncio.sleep(wait)

            phase_deadline = tick_at + phase_seconds
            await asyncio.gather(
                *[r.step(phase_deadline, tick_at) for r in active],
                return_exceptions=True,
            )


def _next_wall_clock_tick(phase_seconds: float) -> float:
    """Return the next Unix epoch second that is a multiple of phase_seconds.

    With phase_seconds == 1800, ticks land on every half-hour boundary
    (00:00, 00:30, 01:00, …) so all markets share the same global rhythm.
    Always strictly in the future — if `now` is exactly on a boundary, the
    next tick is `now + phase_seconds`.
    """
    now = time.time()
    next_tick = math.floor(now / phase_seconds) * phase_seconds + phase_seconds
    return next_tick


def _fmt_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")
