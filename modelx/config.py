"""Config dataclasses and utilities for live market runs.

Provides `GlobalConfig`, `MarketConfig`, and `AgentSpec` dataclasses used by
`run_live.py` and `MarketSupervisor`, plus a YAML loader with a mini-parser
fallback so the package works without PyYAML.
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def parse_settlement_date(raw: str) -> Optional[datetime]:
    """Parse a settlement date/timestamp string into a timezone-aware datetime.

    Handles formats like:
      "2026-04-10 16:00:00T-04:00"  (space before time, T before offset)
      "2026-04-10T16:00:00-04:00"   (standard ISO 8601)
      "2026-04-10 16:00:00-04:00"   (space separator, no T before offset)

    Returns None for unparseable values (e.g. "TBD", plain dates "2025-06-11").
    """
    if not raw or raw.upper() == "TBD":
        return None
    # Normalize: replace space between date and time with T, strip T before tz offset
    normalized = re.sub(
        r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})",
        r"\1T\2",
        raw,
    )
    # Remove a stray T immediately before a +/- timezone offset
    normalized = re.sub(r"T([+-]\d{2})", r"\1", normalized)
    try:
        dt = datetime.fromisoformat(normalized)
        # Only return timezone-aware datetimes (full timestamps).
        # Plain dates like "2025-06-11" parse as naive and are not usable
        # for time-based termination.
        return dt if dt.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


@dataclass
class AgentSpec:
    name: str
    model: str
    role: str  # "MM" or "HF"
    # Optional per-agent output-token budget. When None, OpenRouterAgent
    # falls back to OPENROUTER_MAX_TOKENS env or its default. Reasoning
    # models (nemotron-nano, glm-air, deepseek-r1, …) typically need this
    # raised so they don't burn the whole budget on reasoning tokens and
    # return content=null.
    max_tokens: Optional[int] = None


@dataclass
class MarketConfig:
    id: str
    name: str
    description: str
    settlement_date: str
    multiplier: float
    position_limit: int
    max_size: int
    settlement_datetime: Optional[datetime] = None
    info_schedule: Dict[int, List[str]] = field(default_factory=dict)
    # Live news fields — when search_terms is non-empty, the market runner
    # fetches real-time headlines + price data instead of using info_schedule.
    search_terms: List[str] = field(default_factory=list)
    price_ticker: Optional[str] = None
    news_sources: List[str] = field(default_factory=list)
    max_headlines_per_cycle: int = 10
    news_lookback_phases: int = 2


@dataclass
class GlobalConfig:
    phase_duration_seconds: float
    markets: List[MarketConfig]
    agent_specs: List[AgentSpec]



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
