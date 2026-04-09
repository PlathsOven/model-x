"""Config loader for live multi-market runs.

Parses `markets.yaml` (market definitions + global phase duration) and
`agents.yaml` (agent registry) into typed `GlobalConfig` / `MarketConfig` /
`AgentSpec` dataclasses for `run_markets.py` and `MarketSupervisor`.

PyYAML is preferred but optional — there's a tiny fallback parser for the
documented format so the rest of the package keeps working without the dep.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentSpec:
    name: str
    model: str
    role: str  # "MM" or "HF"


@dataclass
class MarketConfig:
    id: str
    name: str
    description: str
    settlement_date: str
    multiplier: float
    position_limit: int
    num_cycles: int
    max_size: int
    info_schedule: Dict[int, List[str]] = field(default_factory=dict)


@dataclass
class GlobalConfig:
    phase_duration_seconds: float
    markets: List[MarketConfig]
    agent_specs: List[AgentSpec]


# ---------- public API ----------

def load_config(markets_path: str, agents_path: str) -> GlobalConfig:
    """Load and validate the full live-run config.

    `markets_path` is a YAML file with `phase_duration_seconds` and `markets:`.
    `agents_path` is the existing agents.yaml — every agent in it participates
    in every market. Raises ValueError on missing/invalid fields.
    """
    if not os.path.exists(markets_path):
        raise FileNotFoundError(f"markets config not found at {markets_path}")
    if not os.path.exists(agents_path):
        raise FileNotFoundError(f"agents config not found at {agents_path}")

    markets_data = _load_yaml(markets_path)
    agents_data = _load_yaml(agents_path)

    phase = markets_data.get("phase_duration_seconds")
    if phase is None:
        raise ValueError(
            f"{markets_path}: missing top-level 'phase_duration_seconds'"
        )
    try:
        phase_seconds = float(phase)
    except (TypeError, ValueError):
        raise ValueError(
            f"{markets_path}: phase_duration_seconds must be a number, got {phase!r}"
        )
    if phase_seconds <= 0:
        raise ValueError(
            f"{markets_path}: phase_duration_seconds must be > 0, got {phase_seconds}"
        )

    markets = _parse_markets(markets_data.get("markets") or [], markets_path)
    if not markets:
        raise ValueError(f"{markets_path}: no markets defined under 'markets:' key")

    agent_specs = _parse_agents(agents_data.get("agents") or [], agents_path)
    if not agent_specs:
        raise ValueError(f"{agents_path}: no agents defined under 'agents:' key")
    # Live markets cannot use the blocking HumanAgent — it would freeze the
    # asyncio event loop forever.
    for spec in agent_specs:
        if spec.model == "human":
            raise ValueError(
                f"{agents_path}: agent {spec.name!r} uses model='human' which is "
                f"incompatible with live multi-market runs (would block the event loop). "
                f"Use run_demo.py for human-in-the-loop episodes."
            )

    return GlobalConfig(
        phase_duration_seconds=phase_seconds,
        markets=markets,
        agent_specs=agent_specs,
    )


# ---------- parsing helpers ----------

def _parse_markets(raw: Any, path: str) -> List[MarketConfig]:
    if not isinstance(raw, list):
        raise ValueError(f"{path}: 'markets:' must be a list")
    out: List[MarketConfig] = []
    seen_ids: set = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: markets[{i}] must be a mapping")
        for key in ("id", "name", "description", "settlement_date",
                    "num_cycles"):
            if key not in item:
                raise ValueError(
                    f"{path}: markets[{i}] missing required key {key!r}"
                )
        market_id = str(item["id"])
        if market_id in seen_ids:
            raise ValueError(f"{path}: duplicate market id {market_id!r}")
        seen_ids.add(market_id)

        info_schedule = _parse_info_schedule(
            item.get("info_schedule") or {}, path, market_id,
        )

        out.append(MarketConfig(
            id=market_id,
            name=str(item["name"]),
            description=str(item["description"]),
            settlement_date=str(item["settlement_date"]),
            multiplier=float(item.get("multiplier", 1.0)),
            position_limit=int(item.get("position_limit", 100)),
            num_cycles=int(item["num_cycles"]),
            max_size=int(item.get("max_size", 50)),
            info_schedule=info_schedule,
        ))
    return out


def _parse_info_schedule(
    raw: Any,
    path: str,
    market_id: str,
) -> Dict[int, List[str]]:
    """Normalize the info_schedule mapping. Each value may be a string or list."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"{path}: market {market_id!r} info_schedule must be a mapping"
        )
    out: Dict[int, List[str]] = {}
    for k, v in raw.items():
        try:
            cycle_idx = int(k)
        except (TypeError, ValueError):
            raise ValueError(
                f"{path}: market {market_id!r} info_schedule key "
                f"{k!r} is not an integer"
            )
        if isinstance(v, list):
            out[cycle_idx] = [str(x) for x in v]
        else:
            out[cycle_idx] = [str(v)]
    return out


def _parse_agents(raw: Any, path: str) -> List[AgentSpec]:
    if not isinstance(raw, list):
        raise ValueError(f"{path}: 'agents:' must be a list")
    out: List[AgentSpec] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: agents[{i}] must be a mapping")
        for key in ("name", "model", "role"):
            if key not in item:
                raise ValueError(
                    f"{path}: agents[{i}] missing required key {key!r}"
                )
        role = str(item["role"])
        if role not in ("MM", "HF"):
            raise ValueError(
                f"{path}: agent {item['name']!r} has invalid role {role!r} "
                f"(expected 'MM' or 'HF')"
            )
        out.append(AgentSpec(
            name=str(item["name"]),
            model=str(item["model"]),
            role=role,
        ))
    return out


# ---------- YAML loader (PyYAML preferred, fallback parser otherwise) ----------

def _load_yaml(path: str) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: top level must be a mapping")
        return data
    except ImportError:
        return _mini_yaml(path)


def _mini_yaml(path: str) -> Dict[str, Any]:
    """Minimal YAML parser for the markets/agents config format.

    Supports:
        top_key: value
        top_key:
          - key1: value1
            key2: value2
            nested:
              key3: value3
              key4:
                - "list item"
        # comments
    Indentation is 2 spaces. Quoted values are unquoted. Numbers stay as
    strings — the typed parser above coerces them.
    """
    with open(path) as f:
        text = f.read()

    lines: List[tuple] = []  # (indent, content)
    for raw in text.splitlines():
        # Strip trailing comments only when not inside a quoted value.
        body = _strip_trailing_comment(raw).rstrip()
        if not body.strip():
            continue
        indent = len(body) - len(body.lstrip())
        lines.append((indent, body[indent:]))

    pos = [0]

    def parse_block(min_indent: int) -> Any:
        """Parse a block whose entries are at exactly `min_indent` columns.

        Returns either a dict or a list depending on the first sibling.
        """
        if pos[0] >= len(lines):
            return None
        first_indent, first_body = lines[pos[0]]
        if first_indent < min_indent:
            return None
        if first_body.startswith("- "):
            return parse_list(min_indent)
        return parse_dict(min_indent)

    def parse_dict(indent: int) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        while pos[0] < len(lines):
            cur_indent, body = lines[pos[0]]
            if cur_indent < indent:
                break
            if cur_indent > indent:
                # Continuation belonging to a previous list item / dict — bail
                # so the caller can pick it up. (Shouldn't happen with valid input.)
                break
            if body.startswith("- "):
                # Reached a sibling list — caller should have entered parse_list.
                break
            if ":" not in body:
                raise ValueError(
                    f"{path}: line {pos[0]+1}: expected 'key: value', got {body!r}"
                )
            key, _, value = body.partition(":")
            key = key.strip()
            value = value.strip()
            pos[0] += 1
            if value:
                out[key] = _strip_quotes(value)
            else:
                # Nested block — descend at deeper indent.
                child = parse_block(indent + 2)
                out[key] = child if child is not None else {}
        return out

    def parse_list(indent: int) -> List[Any]:
        out: List[Any] = []
        while pos[0] < len(lines):
            cur_indent, body = lines[pos[0]]
            if cur_indent < indent or not body.startswith("- "):
                break
            inner = body[2:].strip()
            pos[0] += 1
            if not inner:
                # Empty list marker — descend.
                child = parse_block(indent + 2)
                out.append(child if child is not None else {})
                continue
            if ":" in inner and not (inner.startswith('"') or inner.startswith("'")):
                # First key of a dict-style list item: "- key: value".
                # Re-inject as if it were at indent+2 so parse_dict picks it up,
                # then continue collecting siblings at the same indent.
                k, _, v = inner.partition(":")
                k = k.strip()
                v = v.strip()
                item: Dict[str, Any] = {}
                if v:
                    item[k] = _strip_quotes(v)
                else:
                    child = parse_block(indent + 4)
                    item[k] = child if child is not None else {}
                # Collect remaining sibling keys belonging to this list item.
                while pos[0] < len(lines):
                    nxt_indent, nxt_body = lines[pos[0]]
                    if nxt_indent != indent + 2 or nxt_body.startswith("- "):
                        break
                    if ":" not in nxt_body:
                        break
                    nk, _, nv = nxt_body.partition(":")
                    nk = nk.strip()
                    nv = nv.strip()
                    pos[0] += 1
                    if nv:
                        item[nk] = _strip_quotes(nv)
                    else:
                        child = parse_block(indent + 4)
                        item[nk] = child if child is not None else {}
                out.append(item)
            else:
                out.append(_strip_quotes(inner))
        return out

    return parse_dict(0) if lines else {}


def _strip_trailing_comment(line: str) -> str:
    """Remove `# ...` from a line, but not when inside quotes."""
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
    return line


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s
