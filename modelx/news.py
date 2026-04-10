"""Live news feed and price data for ModelX contracts.

Pulls headlines from Google News RSS (via feedparser) and price bars from
yfinance. Both are optional — if either dependency is missing or a fetch
fails, the system degrades gracefully (empty headlines / "Price data
unavailable") so the episode can still run.
"""

import math
import os
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

try:
    import feedparser  # type: ignore
except ImportError:
    feedparser = None  # type: ignore

try:
    import yfinance as yf  # type: ignore
except ImportError:
    yf = None  # type: ignore

from .models import Contract


@dataclass
class NewsConfig:
    sources: List[str]  # e.g. ["reuters.com", "cnbc.com", "bloomberg.com"]
    max_headlines_per_cycle: int = 10


# ---------- feed URL ----------

def build_feed_url(search_term: str, sources: List[str], hours_back: float) -> str:
    """Construct a Google News RSS search URL.

    `hours_back` is rounded up to nearest integer hour (minimum 1).
    """
    hours = max(1, math.ceil(hours_back))
    encoded_term = urllib.parse.quote(search_term)
    if sources:
        site_filter = "+(" + "+OR+".join(f"site:{s}" for s in sources) + ")"
    else:
        site_filter = ""
    return (
        f"https://news.google.com/rss/search?"
        f"q={encoded_term}{site_filter}+when:{hours}h"
        f"&hl=en-US&gl=US&ceid=US:en"
    )


# ---------- headlines ----------

def fetch_headlines(
    search_terms: List[str],
    sources: List[str],
    since: datetime,
    max_results: int,
) -> List[Dict]:
    """Fetch and deduplicate headlines from Google News RSS.

    Returns up to `max_results` items as:
        {"title": str, "source": str, "published": datetime, "summary": str}

    Silently returns [] on any error.
    """
    if feedparser is None:
        return []

    now = datetime.now(timezone.utc)
    since_utc = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since
    hours_back = max(1.0, (now - since_utc).total_seconds() / 3600)

    seen_titles: set = set()
    results: List[Dict] = []

    for term in search_terms:
        url = build_feed_url(term, sources, hours_back)
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue

        for entry in getattr(feed, "entries", []):
            # Parse publication time.
            pp = getattr(entry, "published_parsed", None)
            if pp is None:
                continue
            try:
                pub_dt = datetime(*pp[:6], tzinfo=timezone.utc)
            except Exception:
                continue

            if pub_dt <= since_utc:
                continue

            # Extract source: Google News appends " - SourceName" to titles.
            raw_title = getattr(entry, "title", "")
            source = ""
            if hasattr(entry, "source") and hasattr(entry.source, "title"):
                source = entry.source.title
                title = raw_title
            elif " - " in raw_title:
                title, source = raw_title.rsplit(" - ", 1)
            else:
                title = raw_title

            # Deduplicate by normalized title.
            norm = title.strip().lower()
            if norm in seen_titles:
                continue
            seen_titles.add(norm)

            results.append({
                "title": title.strip(),
                "source": source.strip(),
                "published": pub_dt,
                "summary": getattr(entry, "summary", "").strip(),
            })

    # Sort most recent first.
    results.sort(key=lambda r: r["published"], reverse=True)
    return results[:max_results]


# ---------- price series ----------

def fetch_price_series(
    ticker: str,
    period: Optional[str] = None,
    interval: Optional[str] = None,
) -> str:
    """Download recent OHLCV data via yfinance and format as a text table.

    Returns the last PRICE_LOOKBACK data points. On any failure returns a
    fallback string. Parameters default to env vars PRICE_PERIOD,
    PRICE_INTERVAL, and PRICE_LOOKBACK.
    """
    if period is None:
        period = os.environ.get("PRICE_PERIOD", "2d")
    if interval is None:
        interval = os.environ.get("PRICE_INTERVAL", "30m")
    lookback = int(os.environ.get("PRICE_LOOKBACK", "24"))
    try:
        if yf is None:
            return "Price data unavailable"

        df = yf.download(ticker, period=period, interval=interval, progress=False)
        if df.empty:
            return "Price data unavailable"

        # yfinance may return multi-level columns; flatten if needed.
        if hasattr(df.columns, "levels") and len(df.columns.levels) > 1:
            df.columns = df.columns.get_level_values(0)

        df = df.tail(lookback)
        lines: List[str] = []
        for ts, row in df.iterrows():
            ts_str = str(ts)
            o = row.get("Open", row.get("open", 0))
            h = row.get("High", row.get("high", 0))
            l = row.get("Low", row.get("low", 0))
            c = row.get("Close", row.get("close", 0))
            v = row.get("Volume", row.get("volume", 0))
            lines.append(
                f"{ts_str}: O={float(o):.2f} H={float(h):.2f} "
                f"L={float(l):.2f} C={float(c):.2f} V={int(v)}"
            )
        return "\n".join(lines) if lines else "Price data unavailable"
    except Exception:
        return "Price data unavailable"


# ---------- combined payload ----------

def build_info_payload(
    contract: Contract,
    news_config: NewsConfig,
    since: datetime,
) -> str:
    """Build a single text block with price data and headlines for a cycle."""
    parts: List[str] = []

    # Price data (optional).
    if contract.price_ticker:
        price_text = fetch_price_series(contract.price_ticker)
        parts.append(f"=== PRICE DATA ({contract.price_ticker}, 15min bars) ===")
        parts.append(price_text)
        parts.append("")  # blank line separator

    # Headlines.
    since_str = since.strftime("%Y-%m-%d %H:%M UTC")
    parts.append(f"=== HEADLINES (since {since_str}) ===")
    headlines = fetch_headlines(
        contract.search_terms,
        news_config.sources,
        since,
        news_config.max_headlines_per_cycle,
    )
    if headlines:
        for h in headlines:
            pub_str = h["published"].strftime("%H:%M")
            parts.append(f"[{h['source']}] {h['title']} ({pub_str})")
    else:
        parts.append("No new headlines since last cycle.")

    return "\n".join(parts)
