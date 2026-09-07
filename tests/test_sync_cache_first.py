"""Testy cache-first synchronizace + srovnávacích řádků (offline, mockovaní providery)."""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest

from dividend_tracker.data.db import Database
from dividend_tracker.data.fmp import DAILY_LIMIT
from dividend_tracker.data.sync import DIVIDEND_TTL, FUNDAMENTAL_TTL, sync_fundamentals
from dividend_tracker.data.watchlist import build_watchlist_rows


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "sync.db")
    yield d
    d.close()


# ------------------------------------------------------------------ cache-first

def test_fresh_data_stays_cached(db, monkeypatch):
    """Čerstvá data (pod TTL) → žádné externí volání, status 'cached'."""
    import dividend_tracker.data.fmp as fmp_mod
    import dividend_tracker.data.sync as sync_mod

    calls = {"fmp": 0, "yf": 0}
    monkeypatch.setattr(fmp_mod, "_get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("FMP by nemělo být voláno")))
    monkeypatch.setattr(sync_mod, "sync_via_yfinance", lambda db_, tk: calls.__setitem__("yf", calls["yf"] + 1))

    # Ticker s čerstvými timestampy.
    now = time.time()
    db.upsert_stock("KO", name="Coca-Cola")
    with db._conn() as conn:  # noqa: SLF001 — test přímě manipuluje s TTL
        conn.execute(
            "UPDATE stocks SET last_fundamental_update=?, last_dividend_sync=? WHERE ticker='KO'",
            (now - 3600, now - 3600),
        )

    result = sync_fundamentals(db, ["KO"])
    assert result["results"]["KO"] == "cached"
    assert calls["fmp"] == 0 and calls["yf"] == 0


def test_stale_data_triggers_fetch(db, monkeypatch):
    """Stará data (nad TTL) → stáhne se znovu (mock FMP)."""
    import dividend_tracker.data.fmp as fmp_mod

    hist_calls = {"n": 0}

    def fake_get_json(url, timeout=15.0):
        if "historical-dividend" in url:
            hist_calls["n"] += 1
            return [{"date": (date.today() - timedelta(days=30)).isoformat(), "amount": 0.49}]
        return []

    monkeypatch.setattr(fmp_mod, "_get_json", fake_get_json)
    monkeypatch.setenv("FMP_API_KEY", "test-key")

    # Ticker bez historie → starý (NULL timestamp).
    db.upsert_stock("KO", name="Coca-Cola")
    with db._conn() as conn:  # noqa: SLF001 — vynutit stale stav
        conn.execute(
            "UPDATE stocks SET last_fundamental_update=?, last_dividend_sync=? WHERE ticker='KO'",
            (time.time() - FUNDAMENTAL_TTL - 1, time.time() - DIVIDEND_TTL - 1),
        )

    result = sync_fundamentals(db, ["KO"])
    assert result["results"]["KO"] == "fmp"
    assert hist_calls["n"] >= 1
    # Data se okamžitě uložila do DB.
    assert len(db.get_dividends("KO")) == 1


def test_fmp_limit_exceeded_falls_to_yfinance(db, monkeypatch):
    """Vyčerpání FMP limitu + Tiingo nenakonfigurovaný → fallback na yfinance."""
    import dividend_tracker.data.fmp as fmp_mod
    import dividend_tracker.data.sync as sync_mod

    # Předvyplnit čítač na hranici limitu.
    for _ in range(DAILY_LIMIT):
        db.record_api_call("fmp", daily_limit=DAILY_LIMIT)

    monkeypatch.setenv("FMP_API_KEY", "test-key")
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    monkeypatch.setattr(fmp_mod, "_get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nesmí být voláno")))

    called = []
    monkeypatch.setattr(
        sync_mod, "sync_via_yfinance", lambda db_, tk: called.append(tk) or db_.mark_dividend_sync(tk)
    )

    db.upsert_stock("KO", name="Coca-Cola")  # NULL timestamps → stale, ale limit je pryč
    result = sync_fundamentals(db, ["KO"])
    assert result["results"]["KO"] == "yfinance"
    assert called == ["KO"]


def test_all_providers_fail_keeps_stale_cache(db, monkeypatch):
    """FMP limit + Tiingo chyba + yfinance chyba → ticker zůstává v cache (stale_cache)."""
    import dividend_tracker.data.fmp as fmp_mod
    import dividend_tracker.data.sync as sync_mod

    for _ in range(DAILY_LIMIT):
        db.record_api_call("fmp", daily_limit=DAILY_LIMIT)

    monkeypatch.setenv("FMP_API_KEY", "test-key")
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    monkeypatch.setattr(fmp_mod, "_get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nesmí být voláno")))

    def yf_fail(db_, tk):
        raise RuntimeError("yfinance down")

    monkeypatch.setattr(sync_mod, "sync_via_yfinance", yf_fail)

    db.upsert_stock("KO", name="Coca-Cola")  # NULL timestamps → stale
    result = sync_fundamentals(db, ["KO"])
    assert result["results"]["KO"] == "stale_cache"


def test_no_api_key_uses_yfinance_fallback(db, monkeypatch):
    """Bez API klíčů (FMP i Tiingo) → fallback na yfinance (mockovaný)."""
    import dividend_tracker.data.sync as sync_mod

    called = []
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)  # testy CLI/web načtou reálný .env
    monkeypatch.setattr(
        sync_mod, "sync_via_yfinance", lambda db_, tk: called.append(tk) or db_.mark_dividend_sync(tk)
    )

    db.upsert_stock("JNJ", name="Johnson & Johnson")
    result = sync_fundamentals(db, ["JNJ"])
    assert result["results"]["JNJ"] == "yfinance"
    assert called == ["JNJ"]


def test_force_bypasses_cache(db, monkeypatch):
    """force=True ignoruje čerstvou cache a stáhne znovu."""
    import dividend_tracker.data.fmp as fmp_mod

    monkeypatch.setattr(fmp_mod, "_get_json", lambda *a, **k: [])
    monkeypatch.setenv("FMP_API_KEY", "test-key")

    now = time.time()
    db.upsert_stock("KO", name="Coca-Cola")
    with db._conn() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE stocks SET last_fundamental_update=?, last_dividend_sync=? WHERE ticker='KO'",
            (now - 60, now - 60),
        )

    result = sync_fundamentals(db, ["KO"], force=True)
    assert result["results"]["KO"] == "fmp"


# -------------------------------------------------------------------- watchlist

def _seed_watchlist_db(db):
    """Naplní DB dvěma tickerů s odlišnými atributy pro test řazení.

    KO — 7 let kvartálních plateb s ročním růstem ~6 % (dividend grower).
    JNJ — 7 let konstantních plateb (žádný růst), vyšší yield díky nižší ceně.
    """
    today = date.today()

    def seed(ticker, name, sector, base_amount, annual_growth):
        db.upsert_stock(ticker, name=name, currency="USD", sector=sector)
        n_years = 7
        for y in range(n_years):  # y=0 nejstarší rok → y=6 nejnovější
            for q in range(4):    # 4 kvartální výplaty za rok
                days_ago = (n_years - 1 - y) * 365 + q * 91 + 25
                ex = today - timedelta(days=days_ago)
                amt = round(base_amount * ((1.0 + annual_growth) ** y), 4)
                db.upsert_dividend(ticker, ex.isoformat(), amt, frequency="quarterly")

    seed("KO", "Coca-Cola", "Consumer Defensive", 0.42, 0.06)
    db.upsert_quote("KO", current_price=70.0, change=-0.5, volume=1_000_000)
    db.upsert_calendar_entry("KO", (today + timedelta(days=20)).isoformat(), 0.49)

    seed("JNJ", "Johnson & Johnson", "Healthcare", 1.00, 0.0)
    db.upsert_quote("JNJ", current_price=130.0, change=1.2, volume=2_000_000)


def test_watchlist_rows_computed_from_db(db):
    _seed_watchlist_db(db)
    rows = build_watchlist_rows(db)

    # Abecední pořadí (ORDER BY ticker).
    assert [r["ticker"] for r in rows] == ["JNJ", "KO"]

    by_ticker = {r["ticker"]: r for r in rows}
    ko, jnj = by_ticker["KO"], by_ticker["JNJ"]

    # Yield = TTM DPS / cena (KO ~4.2 %, JNJ ~3.9 %).
    assert ko["yield_pct"] is not None and 4 < ko["yield_pct"] < 5
    assert jnj["yield_pct"] is not None and 3 < jnj["yield_pct"] < 4

    # CAGR: KO roste (~6 %/rok), JNJ konstantní (0).
    assert ko["growth_5y_pct"] is not None and ko["growth_5y_pct"] > 3
    assert jnj["growth_5y_pct"] in (None, 0) or abs(jnj["growth_5y_pct"]) < 1

    # Streak: KO má růst po sobě jdoucích let, JNJ ne.
    assert ko["streak_years"] >= 2
    assert jnj["streak_years"] == 0

    # Příští výplata jen u KO (jen tam je v kalendáři).
    assert ko["next_ex_date"] is not None and ko["next_dividend"] == 0.49
    assert jnj["next_ex_date"] is None


def test_watchlist_empty_db(db):
    assert build_watchlist_rows(db) == []
