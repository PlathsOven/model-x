#!/usr/bin/env python3
"""ModelX live runner — drives N contracts concurrently on a real-time
wall-clock schedule with live headlines and price data.

Reads contract definitions from `contracts.yaml` (a list of contracts, each
with search terms, price ticker, news sources, and settlement date) and
agent configuration from `agents.yaml`. All agents participate in every
contract. Every `phase_duration_seconds`, one phase (MM or HF) fires, LLM
calls fan out concurrently, and state persists to SQLite.

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
    """Parse contracts.yaml into (List[MarketConfig], phase_duration_seconds)."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"contract config not found at {path}")

    data = _load_yaml(path)

    # Contracts list.
    contracts_raw = data.get("contracts")
    if not contracts_raw or not isinstance(contracts_raw, list):
        raise ValueError(f"{path}: missing or empty 'contracts:' list")

    # Phase duration (global, shared across all contracts).
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

    market_configs: List[MarketConfig] = []
    seen_ids: set = set()

    for i, cdata in enumerate(contracts_raw):
        if not isinstance(cdata, dict):
            raise ValueError(f"{path}: contracts[{i}] must be a mapping")
        for key in ("id", "name", "description", "settlement_date"):
            if key not in cdata:
                raise ValueError(f"{path}: contracts[{i}] missing required key {key!r}")

        market_id = str(cdata["id"])
        if market_id in seen_ids:
            raise ValueError(f"{path}: duplicate contract id {market_id!r}")
        seen_ids.add(market_id)

        search_terms = cdata.get("search_terms", [])
        if isinstance(search_terms, str):
            search_terms = [search_terms]

        raw_sources = cdata.get("news_sources", [])
        if isinstance(raw_sources, str):
            raw_sources = [raw_sources]

        settlement_date_str = str(cdata["settlement_date"])
        settlement_dt = parse_settlement_date(settlement_date_str)
        if settlement_dt is None:
            raise ValueError(
                f"{path}: contracts[{i}] ({market_id}) settlement_date must be "
                f"a parseable timestamp, got {settlement_date_str!r}"
            )

        market_configs.append(MarketConfig(
            id=market_id,
            name=str(cdata["name"]),
            description=str(cdata["description"]),
            settlement_date=settlement_date_str,
            multiplier=float(cdata.get("multiplier", 1.0)),
            position_limit=int(cdata.get("position_limit", 100)),
            max_size=int(cdata.get("max_size", 50)),
            settlement_datetime=settlement_dt,
            search_terms=[str(s) for s in search_terms],
            price_ticker=str(cdata["price_ticker"]) if cdata.get("price_ticker") else None,
            news_sources=[str(s) for s in raw_sources],
            max_headlines_per_cycle=int(cdata.get(
                "max_headlines_per_cycle",
                os.environ.get("MAX_HEADLINES_PER_CYCLE", "10"),
            )),
        ))

    if not market_configs:
        raise ValueError(f"{path}: no contracts defined under 'contracts:' key")

    return market_configs, phase_seconds


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
        max_tokens_raw = item.get("max_tokens")
        if max_tokens_raw is not None:
            try:
                max_tokens_val = int(max_tokens_raw)
            except (TypeError, ValueError):
                raise ValueError(
                    f"{path}: agent {item['name']!r} max_tokens must be an "
                    f"integer, got {max_tokens_raw!r}"
                )
            if max_tokens_val <= 0:
                raise ValueError(
                    f"{path}: agent {item['name']!r} max_tokens must be > 0, "
                    f"got {max_tokens_val}"
                )
        else:
            max_tokens_val = None
        specs.append(AgentSpec(
            name=str(item["name"]),
            model=str(item["model"]),
            role=role,
            max_tokens=max_tokens_val,
        ))
    return specs


# ---------- agent factory ----------

def build_agent(spec: AgentSpec) -> Agent:
    return OpenRouterAgent(model=spec.model, max_tokens=spec.max_tokens)


# ---------- main ----------

async def _run(args: Any) -> None:
    market_configs, phase_seconds = load_contract_config(args.contract)
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
        markets=market_configs,
        agent_specs=agent_specs,
    )

    if not os.path.isabs(args.db) and os.path.exists("/.dockerenv"):
        print(
            f"[run_live] WARNING: --db={args.db!r} is a relative path inside a "
            f"container. The DB will be written to $CWD/{args.db}, which on "
            f"Railway is /app/ (ephemeral — wiped on every redeploy). "
            f"Set DB_PATH to an absolute path on your persistent volume "
            f"(e.g. /data/modelx.db) to keep trade history across deploys.",
            file=sys.stderr, flush=True,
        )

    db = connect(args.db)
    supervisor = MarketSupervisor(
        config=config,
        db=db,
        agent_factory=build_agent,
    )
    supervisor.setup()

    print(
        f"[run_live] {len(market_configs)} contract(s), "
        f"{len(agent_specs)} agent(s), "
        f"phase_duration={phase_seconds}s, "
        f"db={args.db}",
        flush=True,
    )
    for mc in market_configs:
        print(
            f"[run_live]   market={mc.id!r}, "
            f"settlement={mc.settlement_date}, "
            f"search_terms={mc.search_terms}, "
            f"price_ticker={mc.price_ticker}, "
            f"news_sources={mc.news_sources}",
            flush=True,
        )
    print(flush=True)

    try:
        await supervisor.run_all()
    except KeyboardInterrupt:
        print("\n[run_live] interrupted by user — state persisted to db")

    print("\n[run_live] all markets entered PENDING_SETTLEMENT. Settle with:")
    for mc in market_configs:
        print(
            f"  python3 settle.py --market {mc.id} --value <float> --db {args.db}",
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
