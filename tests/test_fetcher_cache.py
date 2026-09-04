"""Testy Fáze 4 — disk cache snapshotů (TTL 1 den), refresh bypass, exponenciální backoff.

Vše offline: síťové volání yfinance je nahrazeno monkeypatchem ``_retry`` /
``_fetch_independent``, cache směřuje do ``tmp_path`` přes ``CACHE_DIR``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from tests.conftest import make_snapshot

from dividend_tracker.data import fetcher as f


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Cache vždy do izolovaného adresáře — testy se nedotknou reálné cache."""
    monkeypatch.setattr(f, "CACHE_DIR", tmp_path / "cache")


# ------------------------------------------------------------------ disk cache

def test_save_and_load_roundtrip():
    snap = make_snapshot(ticker="RTST")
    f.save_snapshot_cache(snap)

    loaded = f.load_cached_snapshot("rtst")  # case-insensitive cesta
    assert loaded is not None
    assert loaded.ticker == "RTST"
    assert loaded.price == pytest.approx(60.0)
    assert loaded.dps_ttm == pytest.approx(3.96)
    assert loaded.data_gaps == []


def test_load_missing_returns_none():
    assert f.load_cached_snapshot("NOPE") is None


def test_load_expired_cache_returns_none(monkeypatch):
    """Snapshot starší než TTL (1 den) se ignoruje."""
    snap = make_snapshot(ticker="OLD")
    path = f._snapshot_cache_path("OLD")
    path.parent.mkdir(parents=True, exist_ok=True)
    stale_ts = datetime.now(timezone.utc).timestamp() - 2 * 86400  # 2 dny
    path.write_text(json.dumps({"ts": stale_ts, "data": snap.model_dump()}))

    assert f.load_cached_snapshot("OLD") is None


def test_load_corrupt_cache_returns_none():
    """Poškozená cache → tiché None (volající stáhne data znovu)."""
    path = f._snapshot_cache_path("BAD")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ tohle není JSON ]")

    assert f.load_cached_snapshot("BAD") is None


def test_save_failure_does_not_raise(monkeypatch):
    """Nepoužitelná cache (OSError při zápisu) nesmí rozbít analýzu — jen zaloguje."""
    def _raise(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(f.Path, "write_text", _raise)
    f.save_snapshot_cache(make_snapshot(ticker="OK"))  # nesmí vyhodit výjimku


# ------------------------------------------------- build_snapshot + cache-first

def test_build_snapshot_uses_fresh_cache_without_network(monkeypatch):
    """Čerstvá cache → snapshot se vrátí bez jediného síťového volání."""
    snap = make_snapshot(ticker="CACH")
    f.save_snapshot_cache(snap)

    def _boom(tk):  # kdyby se síťová cesta spustila, test spadne
        raise AssertionError("síťové stahování by nemělo proběhnout")

    monkeypatch.setattr(f, "_fetch_independent", _boom)
    monkeypatch.setattr(f, "_retry", lambda fn: (_ for _ in ()).throw(AssertionError("info fetch")))

    out = f.build_snapshot("cach")  # bez refresh → cache hit
    assert out.ticker == "CACH"
    assert out.price == pytest.approx(60.0)


def test_build_snapshot_refresh_bypasses_cache(monkeypatch):
    """refresh=True ignoruje cache a stáhne data znovu (i když je čerstvá)."""
    stale = make_snapshot(ticker="REFR", price=99.0)  # v cache leží stará cena
    f.save_snapshot_cache(stale)

    fake_info = {"currentPrice": 55.0, "longName": "Refresh Co", "sector": "Consumer Defensive"}
    monkeypatch.setattr(f, "_retry", lambda fn: dict(fake_info))
    monkeypatch.setattr(
        f, "_fetch_independent",
        lambda tk: {k: None for k in ("dividends", "splits", "history",
                                      "income_stmt", "balance_sheet", "cashflow")},
    )

    out = f.build_snapshot("REFR", refresh=True)
    assert out.price == pytest.approx(55.0), "refresh musí ignorovat cache (99.0)"

    # a výsledek se zpět do cache uložil (další spuštění bez refresh dostane 55.0)
    reloaded = f.load_cached_snapshot("REFR")
    assert reloaded is not None and reloaded.price == pytest.approx(55.0)


def test_build_snapshot_saves_to_cache_after_fetch(monkeypatch):
    """Po úspěšném stahování se snapshot uloží do cache (TTL 1 den)."""
    fake_info = {"currentPrice": 42.0, "longName": "Fresh Co", "sector": "Consumer Defensive"}
    monkeypatch.setattr(f, "_retry", lambda fn: dict(fake_info))
    monkeypatch.setattr(
        f, "_fetch_independent",
        lambda tk: {k: None for k in ("dividends", "splits", "history",
                                      "income_stmt", "balance_sheet", "cashflow")},
    )

    out = f.build_snapshot("FRESH")
    assert out.price == pytest.approx(42.0)
    cached = f.load_cached_snapshot("FRESH")
    assert cached is not None and cached.ticker == "FRESH"


# ------------------------------------------------------------------ backoff

def test_retry_exponential_backoff(monkeypatch):
    """Druhá pauza musí být delší než první (exponenciální, ne lineární)."""
    sleeps: list[float] = []
    monkeypatch.setattr(f.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("simulovaná chyba providera")
        return "OK"

    assert f._retry(flaky, attempts=3, delay=1.5) == "OK"
    assert len(sleeps) == 2
    # i=0: base 1.5 → [1.5, 2.25]; i=1: base 3.0 → [3.0, 4.5]
    assert 1.5 <= sleeps[0] <= 2.25
    assert 3.0 <= sleeps[1] <= 4.5
    assert sleeps[1] > sleeps[0], "backoff musí růst exponenciálně"


def test_retry_rate_limit_waits_longer(monkeypatch):
    """429 / rate-limit → dvojnásobná pauza oproti běžné chybě."""
    sleeps: list[float] = []
    monkeypatch.setattr(f.time, "sleep", lambda s: sleeps.append(s))

    def limited():
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    with pytest.raises(f.DataFetchError):
        f._retry(limited, attempts=2, delay=1.5)

    # i=0: base 1.5 × 2 (rate-limit) → [3.0, 4.5] — bez rate-limitu by bylo [1.5, 2.25]
    assert len(sleeps) == 1
    assert sleeps[0] >= 3.0


def test_retry_exhaustion_raises_datafetcherror():
    with pytest.raises(f.DataFetchError):
        f._retry(lambda: 1 / 0, attempts=1, delay=0.0)


@pytest.mark.parametrize(
    "exc, expected",
    [
        (RuntimeError("HTTP Error 429"), True),
        (RuntimeError("Too Many Requests from provider"), True),
        (RuntimeError("rate limit exceeded"), True),
        (type("YFRateLimitError", (), {})(), True),  # yfinance typ podle jména třídy
        (ConnectionError("network timeout"), False),
    ],
)
def test_is_rate_limited(exc, expected):
    assert f._is_rate_limited(exc) is expected
