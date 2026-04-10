#!/usr/bin/env python3
"""ModelX live news runner — same architecture as run_markets.py, but with
real-time headlines and price data instead of a hardcoded info_schedule.

Reads a single contract definition from `contracts.yaml` (including search
terms, price ticker, and news sources) and agent configuration from
`agents.yaml`. Uses the same MarketSupervisor wall-clock loop as
`run_markets.py` — every `phase_duration_seconds`, one phase (MM or HF)
fires, LLM calls fan out concurrently, and state persists to SQLite.

Settle with `settle.py` when you know the real-world value.

Usage:
    OPENROUTER_API_KEY=sk-... python3 run_live.py
    OPENROUTER_API_KEY=sk-... python3 run_live.py \\
        --contract contracts.yaml --agents agents.yaml --db modelx.db
"""

import argparse
import asyncio
import os
import sys
from typing import Any, Dict, List

# Make the modelx package importable when run from the repo root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(override=True)

from modelx.agents.base import Agent
from modelx.agents.openrouter import OpenRouterAgent
from modelx.config import AgentSpec, GlobalConfig, MarketConfig, parse_settlement_date
from modelx.db import connect
from modelx.supervisor import MarketSupervisor


# ---------- YAML loading ----------

def _load_yaml(path: str) -> Dict[str, Any]:
    """Load a YAML file, preferring PyYAML with a mini-parser fallback."""
    try:
        import yaml  # type: ignore
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: top level must be a mapping")
        return data
    except ImportError:
        from modelx.config import _load_yaml as _cfg_load
        return _cfg_load(path)


# ---------- config loading ----------

def load_contract_config(path: str) -> tuple:
    """Parse contracts.yaml into (MarketConfig, phase_duration_seconds, news_sources, max_headlines)."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"contract config not found at {path}")

    data = _load_yaml(path)

    # Contract section.
    cdata = data.get("contract")
    if not cdata or not isinstance(cdata, dict):
        raise ValueError(f"{path}: missing 'contract:' section")
    for key in ("id", "name", "description"):
        if key not in cdata:
            raise ValueError(f"{path}: contract missing required key {key!r}")

    search_terms = cdata.get("search_terms", [])
    if isinstance(search_terms, str):
        search_terms = [search_terms]

    # News section.
    ndata = data.get("news", {})
    raw_sources = ndata.get("sources", [])
    if isinstance(raw_sources, str):
        raw_sources = [raw_sources]

    # Phase duration.
    phase = data.get("phase_duration_seconds")
    if phase is None:
        phase = os.environ.get("PHASE_DURATION_SECONDS")
    if phase is None:
        raise ValueError(
            f"{path}: missing top-level 'phase_duration_seconds' "
            f"(and PHASE_DURATION_SECONDS env var is not set)"
        )
    phase_seconds = float(phase)
    if phase_seconds <= 0:
        raise ValueError(f"{path}: phase_duration_seconds must be > 0")

    settlement_date_str = str(data.get("settlement_date", "TBD"))
    settlement_dt = parse_settlement_date(settlement_date_str)
    if settlement_dt is None:
        raise ValueError(
            f"{path}: settlement_date must be a parseable timestamp "
            f"(e.g. '2026-04-10 16:00:00T-04:00'), got {settlement_date_str!r}"
        )

    market_config = MarketConfig(
        id=str(cdata["id"]),
        name=str(cdata["name"]),
        description=str(cdata["description"]),
        settlement_date=settlement_date_str,
        multiplier=float(cdata.get("multiplier", 1.0)),
        position_limit=int(cdata.get("position_limit", 100)),
        max_size=int(data.get("max_size", 50)),
        settlement_datetime=settlement_dt,
        search_terms=[str(s) for s in search_terms],
        price_ticker=str(cdata["price_ticker"]) if cdata.get("price_ticker") else None,
        news_sources=[str(s) for s in raw_sources],
        max_headlines_per_cycle=int(ndata.get(
            "max_headlines_per_cycle",
            os.environ.get("MAX_HEADLINES_PER_CYCLE", "10"),
        )),
    )

    return market_config, phase_seconds


def load_agent_specs(path: str) -> List[AgentSpec]:
    """Parse agents.yaml into AgentSpec list."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"agent config not found at {path}")
    data = _load_yaml(path)
    agents = data.get("agents") or []
    if not isinstance(agents, list) or not agents:
        raise ValueError(f"{path}: no agents defined under top-level 'agents:' key")
    specs: List[AgentSpec] = []
    for item in agents:
        for key in ("name", "model", "role"):
            if key not in item:
                raise ValueError(f"{path}: agent {item!r} missing required key {key!r}")
        role = str(item["role"])
        if role not in ("MM", "HF"):
            raise ValueError(
                f"{path}: agent {item['name']!r} has invalid role {role!r} "
                f"(expected 'MM' or 'HF')"
            )
        if str(item["model"]) == "human":
            raise ValueError(
                f"{path}: agent {item['name']!r} uses model='human' which is "
                f"incompatible with live mode (would block the event loop)."
            )
        specs.append(AgentSpec(
            name=str(item["name"]),
            model=str(item["model"]),
            role=role,
        ))
    return specs


# ---------- agent factory ----------

def build_agent(spec: AgentSpec) -> Agent:
    return OpenRouterAgent(model=spec.model)


# ---------- main ----------

async def _run(args: Any) -> None:
    market_config, phase_seconds = load_contract_config(args.contract)
    agent_specs = load_agent_specs(args.agents)

    from modelx.agents.openrouter import get_key_pool
    try:
        pool = get_key_pool()
        print(f"[run_live] OpenRouter key pool: {pool.size} key(s)", flush=True)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    config = GlobalConfig(
        phase_duration_seconds=phase_seconds,
        markets=[market_config],
        agent_specs=agent_specs,
    )

    db = connect(args.db)
    supervisor = MarketSupervisor(
        config=config,
        db=db,
        agent_factory=build_agent,
    )
    supervisor.setup()

    print(
        f"[run_live] market={market_config.id!r}, "
        f"{len(agent_specs)} agent(s), "
        f"phase_duration={phase_seconds}s, "
        f"settlement={market_config.settlement_date}, "
        f"db={args.db}",
        flush=True,
    )
    print(
        f"[run_live] search_terms={market_config.search_terms}, "
        f"price_ticker={market_config.price_ticker}",
        flush=True,
    )
    print(
        f"[run_live] news_sources={market_config.news_sources}",
        flush=True,
    )
    print(flush=True)

    try:
        await supervisor.run_all()
    except KeyboardInterrupt:
        print("\n[run_live] interrupted by user — state persisted to db")

    print(
        f"\n[run_live] market entered PENDING_SETTLEMENT. "
        f"Settle with:\n"
        f"  python3 settle.py --market {market_config.id} --value <float> --db {args.db}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contract", default=os.environ.get("CONTRACT_YAML", "contracts.yaml"),
        help="contract + news config (YAML, default: $CONTRACT_YAML or contracts.yaml)",
    )
    parser.add_argument(
        "--agents", default=os.environ.get("AGENTS_YAML", "agents.yaml"),
        help="agents config (YAML, default: $AGENTS_YAML or agents.yaml)",
    )
    parser.add_argument(
        "--db", default=os.environ.get("DB_PATH", "modelx.db"),
        help="SQLite db path (default: $DB_PATH or modelx.db)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
