#!/usr/bin/env python3
"""ModelX live demo — 20-cycle CPI episode driven from agents.yaml.

Usage:
    OPENROUTER_API_KEY=sk-... python3 run_demo.py
    OPENROUTER_API_KEY=sk-... python3 run_demo.py --config agents.yaml --db modelx_episode.db

Reads agent configuration from a YAML file (default: ./agents.yaml). Each entry
specifies (name, model, role). `model: human` uses the CLI HumanAgent;
everything else routes through OpenRouterAgent. The script has no hardcoded
agent names — add/remove participants by editing agents.yaml.

After all 20 cycles complete you'll be prompted for the settlement value;
final scores for every MM and HF then print to stdout, and the full request
/ response / decision trace per agent is dumped to episode_traces.json.
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List

# Make the modelx package importable when this script is run from the repo root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modelx.agents.base import Agent
from modelx.agents.human import HumanAgent
from modelx.agents.openrouter import OpenRouterAgent
from modelx.db import connect, upsert_account
from modelx.models import Account, Contract
from modelx.runner import Participant, run_episode
from modelx.scoring import score_hf, score_mm


# ---------- contract + info schedule ----------

CONTRACT = Contract(
    id="cpi-yoy-may-2025",
    name="US CPI YoY May 2025",
    description="US CPI YoY for May 2025 (BLS release).",
    multiplier=1.0,
    position_limit=100,
)

SETTLEMENT_DATE = "2025-06-11"
NUM_CYCLES = 5

INFO_SCHEDULE_RAW: Dict[int, str] = {
    0: "US CPI has printed 2.4%, 2.6%, 2.8%, 2.3%, 2.9% over the last five months. The Fed has held rates steady at the last two meetings. Core CPI has been trending slightly higher due to sticky shelter costs. Goldman Sachs economists forecast May CPI at 2.7% YoY, citing moderating energy prices offset by persistent services inflation.",
    1: "April PCE inflation came in at 2.5%, slightly below expectations of 2.6%. Markets interpreted this as disinflationary. May gasoline prices averaged $3.42/gallon, up 8% from April. However, used car prices fell 2.1% month-over-month according to the Manheim index.",
    2: "Cleveland Fed inflation nowcast for May CPI: 2.85% YoY. This model has had a mean absolute error of 0.12pp over the last 12 months. May shelter cost data from Zillow suggests continued moderation in rent growth, with observed rent index falling 0.3% month-over-month.",
    3: "2-year Treasury yield fell 5bps today to 4.18%, suggesting bond markets pricing in slightly lower inflation expectations. A prominent financial commentator on social media claims inside knowledge that May CPI will print above 3.0%. This person has no established track record.",
    4: "No new information. The BLS will release the May CPI report tomorrow morning.",
}

# Runner expects List[str] per cycle.
INFO_SCHEDULE: Dict[int, List[str]] = {k: [v] for k, v in INFO_SCHEDULE_RAW.items()}


# ---------- agents.yaml parsing ----------

def parse_agent_config(path: str) -> List[Dict[str, str]]:
    """Return the list of agent specs from `path`.

    Tries PyYAML first; falls back to a tiny parser that handles the
    documented format. Each spec is a dict with keys: name, model, role.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"agent config not found at {path}")
    try:
        import yaml  # type: ignore
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except ImportError:
        data = _mini_yaml(path)

    agents = data.get("agents") or []
    if not isinstance(agents, list) or not agents:
        raise ValueError(f"{path}: no agents defined under top-level 'agents:' key")
    for spec in agents:
        for key in ("name", "model", "role"):
            if key not in spec:
                raise ValueError(f"{path}: agent {spec!r} missing required key {key!r}")
        if spec["role"] not in ("MM", "HF"):
            raise ValueError(
                f"{path}: agent {spec['name']!r} has invalid role {spec['role']!r} "
                f"(expected 'MM' or 'HF')"
            )
    return agents


def _mini_yaml(path: str) -> Dict[str, Any]:
    """Minimal YAML parser for the agents.yaml format.

    Supports only:
        agents:
          - key1: value1
            key2: value2
            key3: value3
          - ...
    Comments (`# ...`) and blank lines are ignored. Quoted values are unquoted.
    """
    with open(path) as f:
        text = f.read()

    lines = []
    for raw in text.splitlines():
        body = raw.split("#", 1)[0].rstrip()
        if body.strip():
            lines.append(body)

    if not lines or not lines[0].lstrip().startswith("agents:"):
        raise ValueError(f"{path}: expected top-level 'agents:' key")

    agents: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for line in lines[1:]:
        body = line.lstrip()
        if body.startswith("- "):
            if current:
                agents.append(current)
            current = {}
            body = body[2:].strip()
            if ":" in body:
                k, v = body.split(":", 1)
                current[k.strip()] = _strip_quotes(v.strip())
        elif ":" in body:
            k, v = body.split(":", 1)
            current[k.strip()] = _strip_quotes(v.strip())
    if current:
        agents.append(current)
    return {"agents": agents}


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


# ---------- agent factory ----------

def build_agent(model: str) -> Agent:
    if model == "human":
        return HumanAgent()
    return OpenRouterAgent(model=model)


# ---------- score printers ----------

def print_mm_scores(scores) -> None:
    print()
    print("=" * 70)
    print("MM SCORES")
    print("=" * 70)
    for acct, s in sorted(scores.items()):
        print(f"\n{acct}")
        print(f"  total_pnl         : {s.total_pnl:>14.4f}")
        print(f"  sharpe            : {s.sharpe:>14.4f}")
        print(f"  volume            : {s.volume:>14}")
        print(f"  volume_share      : {s.volume_share:>14.4f}")
        print(f"  pnl_bps           : {s.pnl_bps:>14.4f}")
        print(f"  uptime            : {s.uptime:>14.4f}")
        print(f"  consensus         : {s.consensus:>14.4f}")
        print(f"  markout_1         : {s.markout_1:>14.4f}")
        print(f"  markout_5         : {s.markout_5:>14.4f}")
        print(f"  markout_20        : {s.markout_20:>14.4f}")
        print(f"  avg_abs_position  : {s.avg_abs_position:>14.4f}")
        print(f"  self_cross_count  : {s.self_cross_count:>14}")
        print(f"  self_cross_volume : {s.self_cross_volume:>14}")


def print_hf_scores(scores) -> None:
    print()
    print("=" * 70)
    print("HF SCORES")
    print("=" * 70)
    for acct, s in sorted(scores.items()):
        print(f"\n{acct}")
        print(f"  total_pnl  : {s.total_pnl:>14.4f}")
        print(f"  sharpe     : {s.sharpe:>14.4f}")
        print(f"  markout_1  : {s.markout_1:>14.4f}")
        print(f"  markout_5  : {s.markout_5:>14.4f}")
        print(f"  markout_20 : {s.markout_20:>14.4f}")


# ---------- main ----------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="agents.yaml", help="agents config (YAML)")
    parser.add_argument(
        "--db", default=None,
        help="SQLite path (default: in-memory; pass a path to persist)",
    )
    parser.add_argument(
        "--traces", default="episode_traces.json",
        help="output JSON path for reasoning traces",
    )
    parser.add_argument(
        "--auto", action="store_true", default=False,
        help="skip manual phase-transition pauses (requires --settlement)",
    )
    parser.add_argument(
        "--settlement", type=float, default=None,
        help="settlement value (required when --auto is set)",
    )
    args = parser.parse_args()

    if args.auto and args.settlement is None:
        parser.error("--auto requires --settlement <float>")

    agent_specs = parse_agent_config(args.config)

    # Fail fast if any OpenRouter agent is configured but the key is missing.
    needs_openrouter = any(s["model"] != "human" for s in agent_specs)
    if needs_openrouter and not os.environ.get("OPENROUTER_API_KEY"):
        print(
            "error: OPENROUTER_API_KEY not set in environment "
            "(required for non-human agents in agents.yaml)",
            file=sys.stderr,
        )
        sys.exit(1)

    db = connect(args.db) if args.db else connect(":memory:")

    # Register accounts and build participants.
    participants: List[Participant] = []
    for spec in agent_specs:
        upsert_account(db, Account(
            id=spec["name"],
            name=spec["name"],
            role=spec["role"],
            model=spec["model"],
        ))
        agent = build_agent(spec["model"])
        participants.append(Participant(
            agent=agent,
            account_id=spec["name"],
            role=spec["role"],
        ))

    print(f"Loaded {len(participants)} participants from {args.config}:")
    for p in participants:
        print(f"  {p.account_id:<20} {p.role}  ({p.agent.__class__.__name__})")
    print()

    # Run the episode.
    extra_kwargs = {}
    if args.auto:
        extra_kwargs["input_fn"] = lambda _prompt: ""
        extra_kwargs["settlement_value"] = args.settlement

    result = run_episode(
        contract=CONTRACT,
        info_schedule=INFO_SCHEDULE,
        participants=participants,
        num_cycles=NUM_CYCLES,
        settlement_date=SETTLEMENT_DATE,
        db=db,
        **extra_kwargs,
    )

    # Score (run_episode already set CONTRACT.settlement_value).
    mm_scores = score_mm(result.all_fills, result.cycles, result.positions, CONTRACT)
    hf_scores = score_hf(result.all_fills, result.cycles, result.positions, CONTRACT)
    print_mm_scores(mm_scores)
    print_hf_scores(hf_scores)

    # Dump reasoning traces.
    traces_payload: Dict[str, Any] = {
        "contract": {
            "id": CONTRACT.id,
            "name": CONTRACT.name,
            "description": CONTRACT.description,
            "multiplier": CONTRACT.multiplier,
            "position_limit": CONTRACT.position_limit,
            "settlement_value": result.settlement_value,
            "settlement_date": SETTLEMENT_DATE,
        },
        "num_cycles": NUM_CYCLES,
        "info_schedule": {str(k): v for k, v in INFO_SCHEDULE_RAW.items()},
        "agents": {},
    }
    for spec, p in zip(agent_specs, participants):
        traces_payload["agents"][p.account_id] = {
            "model": spec["model"],
            "role": p.role,
            "traces": getattr(p.agent, "traces", []),
        }

    with open(args.traces, "w") as f:
        json.dump(traces_payload, f, indent=2, default=str)
    print(f"\nReasoning traces -> {args.traces}")


if __name__ == "__main__":
    main()
