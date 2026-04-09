#!/usr/bin/env python3
"""ModelX live multi-market runner.

Reads `markets.yaml` (market definitions + global phase duration) and
`agents.yaml` (agent registry, every agent trades every market). Persists
state to a SQLite database and drives the global wall-clock supervisor that
advances every market on each phase tick.

Markets remain in `RUNNING` state until they hit `num_cycles`, at which
point they enter `PENDING_SETTLEMENT`. Run `settle.py --market <id> --value
<float>` to finalize a market and write its lifetime stats.

Usage:
    OPENROUTER_API_KEY=sk-... python3 run_markets.py
    OPENROUTER_API_KEY=sk-... python3 run_markets.py \\
        --markets markets.yaml --agents agents.yaml --db modelx.db
"""

import argparse
import asyncio
import os
import sys
from typing import Any

# Make the modelx package importable when run from the repo root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modelx.agents.base import Agent
from modelx.agents.openrouter import OpenRouterAgent
from modelx.config import AgentSpec, load_config
from modelx.db import connect
from modelx.supervisor import MarketSupervisor


def build_agent(spec: AgentSpec) -> Agent:
    """Factory called by the supervisor to instantiate one Agent per
    (market, spec) pair. Live mode forbids HumanAgent (validated in config)."""
    return OpenRouterAgent(model=spec.model)


async def _run(args: Any) -> None:
    config = load_config(args.markets, args.agents)

    if not os.environ.get("OPENROUTER_API_KEY"):
        print(
            "error: OPENROUTER_API_KEY not set in environment "
            "(required for OpenRouter agents)",
            file=sys.stderr,
        )
        sys.exit(1)

    db = connect(args.db)
    supervisor = MarketSupervisor(
        config=config,
        db=db,
        agent_factory=build_agent,
    )
    supervisor.setup()

    print(
        f"[run_markets] {len(config.markets)} market(s), "
        f"{len(config.agent_specs)} agent(s), "
        f"phase_duration={config.phase_duration_seconds}s, "
        f"db={args.db}",
        flush=True,
    )

    try:
        await supervisor.run_all()
    except KeyboardInterrupt:
        print("\n[run_markets] interrupted by user — state persisted to db")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--markets", default="markets.yaml",
        help="markets config (YAML, default: markets.yaml)",
    )
    parser.add_argument(
        "--agents", default="agents.yaml",
        help="agents config (YAML, default: agents.yaml)",
    )
    parser.add_argument(
        "--db", default="modelx.db",
        help="SQLite db path (default: modelx.db)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
