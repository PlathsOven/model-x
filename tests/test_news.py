"""News module tests. Run: python3 tests/test_news.py

All external calls (feedparser, yfinance) are mocked — no real HTTP requests.
"""

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modelx.models import Contract
from modelx.news import (
    NewsConfig,
    build_feed_url,
    build_info_payload,
    fetch_headlines,
    fetch_price_series,
)


# ---------- helpers ----------

def _contract(**overrides) -> Contract:
    base = dict(
        id="sp500",
        name="S&P 500",
        description="S&P 500 close",
        multiplier=0.01,
        position_limit=100,
        search_terms=["S&P 500", "US stock market"],
        price_ticker="^GSPC",
    )
    base.update(overrides)
    return Contract(**base)


def _make_entry(title, source_name, published_parsed, summary=""):
    """Build a feedparser-like entry."""
    entry = SimpleNamespace(
        title=title,
        published_parsed=published_parsed,
        summary=summary,
    )
    if source_name:
        entry.source = SimpleNamespace(title=source_name)
    return entry


def _make_feed(entries):
    return SimpleNamespace(entries=entries)


# ---------- build_feed_url tests ----------

def test_build_feed_url_basic():
    url = build_feed_url("S&P 500", ["reuters.com", "cnbc.com"], 2.0)
    assert "q=S%26P%20500" in url
    assert "site:reuters.com" in url
    assert "site:cnbc.com" in url
    assert "+OR+" in url
    assert "when:2h" in url
    assert "hl=en-US" in url


def test_build_feed_url_rounds_up_hours():
    url = build_feed_url("test", [], 1.1)
    assert "when:2h" in url


def test_build_feed_url_minimum_one_hour():
    url = build_feed_url("test", [], 0.3)
    assert "when:1h" in url


def test_build_feed_url_no_sources():
    url = build_feed_url("test", [], 1.0)
    # No site filter in URL.
    assert "site:" not in url
    assert "when:1h" in url


# ---------- fetch_headlines tests ----------

def test_fetch_headlines_filters_by_time():
    since = datetime(2026, 4, 9, 10, 0, 0, tzinfo=timezone.utc)
    old_entry = _make_entry(
        "Old headline - Reuters",
        None,
        (2026, 4, 9, 9, 0, 0, 0, 0, 0),  # before since
    )
    new_entry = _make_entry(
        "New headline",
        "CNBC",
        (2026, 4, 9, 11, 0, 0, 0, 0, 0),  # after since
    )

    with patch("modelx.news.feedparser") as mock_fp:
        mock_fp.parse.return_value = _make_feed([old_entry, new_entry])
        results = fetch_headlines(["test"], ["cnbc.com"], since, 10)

    assert len(results) == 1
    assert results[0]["title"] == "New headline"
    assert results[0]["source"] == "CNBC"


def test_fetch_headlines_deduplicates():
    since = datetime(2026, 4, 9, 10, 0, 0, tzinfo=timezone.utc)
    entry1 = _make_entry(
        "Breaking news",
        "Reuters",
        (2026, 4, 9, 11, 0, 0, 0, 0, 0),
    )
    entry2 = _make_entry(
        "Breaking news",  # same title
        "CNBC",
        (2026, 4, 9, 11, 30, 0, 0, 0, 0),
    )

    with patch("modelx.news.feedparser") as mock_fp:
        # Two search terms, each returns the same headline.
        mock_fp.parse.side_effect = [
            _make_feed([entry1]),
            _make_feed([entry2]),
        ]
        results = fetch_headlines(["term1", "term2"], [], since, 10)

    assert len(results) == 1


def test_fetch_headlines_sorts_recent_first():
    since = datetime(2026, 4, 9, 10, 0, 0, tzinfo=timezone.utc)
    earlier = _make_entry("Earlier", "A", (2026, 4, 9, 11, 0, 0, 0, 0, 0))
    later = _make_entry("Later", "B", (2026, 4, 9, 12, 0, 0, 0, 0, 0))

    with patch("modelx.news.feedparser") as mock_fp:
        mock_fp.parse.return_value = _make_feed([earlier, later])
        results = fetch_headlines(["test"], [], since, 10)

    assert results[0]["title"] == "Later"
    assert results[1]["title"] == "Earlier"


def test_fetch_headlines_max_results():
    since = datetime(2026, 4, 9, 10, 0, 0, tzinfo=timezone.utc)
    entries = [
        _make_entry(f"Headline {i}", "Src", (2026, 4, 9, 11, i, 0, 0, 0, 0))
        for i in range(5)
    ]

    with patch("modelx.news.feedparser") as mock_fp:
        mock_fp.parse.return_value = _make_feed(entries)
        results = fetch_headlines(["test"], [], since, 3)

    assert len(results) == 3


def test_fetch_headlines_error_returns_empty():
    since = datetime(2026, 4, 9, 10, 0, 0, tzinfo=timezone.utc)

    with patch("modelx.news.feedparser") as mock_fp:
        mock_fp.parse.side_effect = Exception("network error")
        results = fetch_headlines(["test"], [], since, 10)

    assert results == []


def test_fetch_headlines_source_from_title_dash():
    """When entry has no .source attr, extract from 'Title - Source' format."""
    since = datetime(2026, 4, 9, 10, 0, 0, tzinfo=timezone.utc)
    entry = SimpleNamespace(
        title="Markets rally - Bloomberg",
        published_parsed=(2026, 4, 9, 11, 0, 0, 0, 0, 0),
        summary="",
    )
    # No .source attribute.

    with patch("modelx.news.feedparser") as mock_fp:
        mock_fp.parse.return_value = _make_feed([entry])
        results = fetch_headlines(["test"], [], since, 10)

    assert results[0]["title"] == "Markets rally"
    assert results[0]["source"] == "Bloomberg"


# ---------- fetch_price_series tests ----------

def test_fetch_price_series_formats_table():
    import pandas as pd

    index = pd.date_range("2026-04-09 10:00", periods=3, freq="15min")
    df = pd.DataFrame({
        "Open": [5500.0, 5501.0, 5502.0],
        "High": [5505.0, 5506.0, 5507.0],
        "Low": [5499.0, 5500.0, 5501.0],
        "Close": [5503.0, 5504.0, 5505.0],
        "Volume": [1000, 2000, 3000],
    }, index=index)

    with patch("modelx.news.yf") as mock_yf:
        mock_yf.download.return_value = df
        result = fetch_price_series("^GSPC")

    assert "O=5500.00" in result
    assert "V=1000" in result
    lines = result.strip().split("\n")
    assert len(lines) == 3


def test_fetch_price_series_error_returns_fallback():
    with patch("modelx.news.yf") as mock_yf:
        mock_yf.download.side_effect = Exception("API error")
        result = fetch_price_series("^GSPC")

    assert result == "Price data unavailable"


def test_fetch_price_series_empty_df():
    import pandas as pd

    with patch("modelx.news.yf") as mock_yf:
        mock_yf.download.return_value = pd.DataFrame()
        result = fetch_price_series("^GSPC")

    assert result == "Price data unavailable"


# ---------- build_info_payload tests ----------

def test_build_info_payload_with_both():
    since = datetime(2026, 4, 9, 10, 0, 0, tzinfo=timezone.utc)
    contract = _contract()
    config = NewsConfig(sources=["reuters.com"], max_headlines_per_cycle=5)

    with patch("modelx.news.fetch_price_series", return_value="mock price data"), \
         patch("modelx.news.fetch_headlines", return_value=[
             {"title": "Stocks up", "source": "Reuters", "published": since, "summary": ""},
         ]):
        result = build_info_payload(contract, config, since)

    assert "=== PRICE DATA (^GSPC, 15min bars) ===" in result
    assert "mock price data" in result
    assert "=== HEADLINES" in result
    assert "[Reuters] Stocks up" in result


def test_build_info_payload_no_ticker():
    since = datetime(2026, 4, 9, 10, 0, 0, tzinfo=timezone.utc)
    contract = _contract(price_ticker=None)
    config = NewsConfig(sources=["reuters.com"], max_headlines_per_cycle=5)

    with patch("modelx.news.fetch_headlines", return_value=[
        {"title": "Stocks up", "source": "Reuters", "published": since, "summary": ""},
    ]):
        result = build_info_payload(contract, config, since)

    assert "PRICE DATA" not in result
    assert "=== HEADLINES" in result
    assert "[Reuters] Stocks up" in result


def test_build_info_payload_no_headlines():
    since = datetime(2026, 4, 9, 10, 0, 0, tzinfo=timezone.utc)
    contract = _contract()
    config = NewsConfig(sources=["reuters.com"], max_headlines_per_cycle=5)

    with patch("modelx.news.fetch_price_series", return_value="mock price data"), \
         patch("modelx.news.fetch_headlines", return_value=[]):
        result = build_info_payload(contract, config, since)

    assert "No new headlines since last cycle." in result


# ---------- runner ----------

TESTS = [
    test_build_feed_url_basic,
    test_build_feed_url_rounds_up_hours,
    test_build_feed_url_minimum_one_hour,
    test_build_feed_url_no_sources,
    test_fetch_headlines_filters_by_time,
    test_fetch_headlines_deduplicates,
    test_fetch_headlines_sorts_recent_first,
    test_fetch_headlines_max_results,
    test_fetch_headlines_error_returns_empty,
    test_fetch_headlines_source_from_title_dash,
    test_fetch_price_series_formats_table,
    test_fetch_price_series_error_returns_fallback,
    test_fetch_price_series_empty_df,
    test_build_info_payload_with_both,
    test_build_info_payload_no_ticker,
    test_build_info_payload_no_headlines,
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
