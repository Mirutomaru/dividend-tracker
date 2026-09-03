"""Testy lokální databáze + FMP klienta (offline, bez sítě)."""

from __future__ import annotations

import time

import pytest

from dividend_tracker.data.db import Database
from dividend_tracker.data.fmp import DAILY_LIMIT, FMPCacheClient, FMPLimitExceeded


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


# ---------------------------------------------------------------------- stocks

def test_upsert_and_get_stock(db):
    db.upsert_stock("KO", name="Coca-Cola", currency="USD", exchange="NYSE", sector="Consumer Defensive")
    row = db.get_stock("KO")
    assert row is not None
    assert row["name"] == "Coca-Cola"
    assert row["currency"] == "USD"
    assert row["last_fundamental_update"] is not None


def test_upsert_preserves_existing_on_none(db):
    db.upsert_stock("KO", name="Coca-Cola", sector="Consumer Defensive")
    # Druhý upsert bez jména nesmí smazat staré.
    db.upsert_stock("KO", currency="EUR")
    row = db.get_stock("KO")
    assert row["name"] == "Coca-Cola"  # zachováno
    assert row["currency"] == "EUR"     # aktualizováno


def test_mark_dividend_sync(db):
    db.upsert_stock("JNJ", name="Johnson & Johnson")
    before = time.time() - 10
    db.mark_dividend_sync("JNJ")
    row = db.get_stock("JNJ")
    assert row["last_dividend_sync"] is not None and row["last_dividend_sync"] >= before


# ---------------------------------------------------------- dividends_history

def test_replace_dividends_roundtrip(db):
    rows = [
        {"ticker": "KO", "ex_date": "2026-01-15", "payment_date": "2026-02-01",
         "record_date": None, "dividend_amount": 0.49, "frequency": "quarterly"},
        {"ticker": "KO", "ex_date": "2026-04-15", "payment_date": "2026-05-01",
         "record_date": None, "dividend_amount": 0.49, "frequency": "quarterly"},
    ]
    n = db.replace_dividends(rows)
    assert n == 2
    got = db.get_dividends("KO")
    assert len(got) == 2
    # seřazeno ex_date DESC → nejnovější první
    assert got[0]["ex_date"] == "2026-04-15"


def test_replace_dividends_replaces_old(db):
    db.replace_dividends([{"ticker": "KO", "ex_date": "2020-01-01", "dividend_amount": 0.4}])
    # Nová dávka smaže staré záznamy daného tickera.
    db.replace_dividends([{"ticker": "KO", "ex_date": "2026-01-01", "dividend_amount": 0.5}])
    got = db.get_dividends("KO")
    assert len(got) == 1 and got[0]["ex_date"] == "2026-01-01"


def test_replace_calendar(db):
    n = db.replace_calendar([{"ticker": "JNJ", "ex_date": "2027-01-10", "dividend_amount": 1.3}])
    assert n == 1
    cal = db.get_calendar("JNJ")
    assert len(cal) == 1 and cal[0]["ex_date"] == "2027-01-10"


# ---------------------------------------------------------------------- quotes

def test_upsert_quote(db):
    db.upsert_quote("MSFT", current_price=450.0, change=-2.5, volume=1_000_000)
    q = db.get_quote("MSFT")
    assert q["current_price"] == 450.0
    assert q["change"] == -2.5
    # Druhý upsert aktualizuje cenu i timestamp.
    t1 = q["timestamp"]
    time.sleep(0.01)
    db.upsert_quote("MSFT", current_price=455.0)
    q2 = db.get_quote("MSFT")
    assert q2["current_price"] == 455.0
    assert q2["timestamp"] >= t1


# ------------------------------------------------------------------- api_usage

def test_api_usage_counter(db):
    u = db.record_api_call("fmp", daily_limit=DAILY_LIMIT)
    assert u["calls_used"] == 1
    assert u["remaining"] == DAILY_LIMIT - 1
    for _ in range(3):
        u = db.record_api_call("fmp", daily_limit=DAILY_LIMIT)
    assert u["calls_used"] == 4
    assert u["remaining"] == DAILY_LIMIT - 4


def test_get_api_usage_empty(db):
    u = db.get_api_usage("fmp")
    assert u["calls_used"] == 0
    assert u["daily_limit"] is None
    assert u["remaining"] is None


# ------------------------------------------------------------------------ FMP

@pytest.fixture
def fmp_db(tmp_path, monkeypatch):
    d = Database(tmp_path / "fmp.db")
    monkeypatch.setenv("FMP_API_KEY", "test-key-123")
    yield d
    d.close()


def test_fmp_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    client = FMPCacheClient(Database(tmp_path / "x.db"))
    assert client.configured is False
    assert client.can_call() is False


def test_fmp_can_call_and_limit(fmp_db, monkeypatch):
    """Po DAILY_LIMIT voláních se další zablokuje (can_call → False)."""
    import dividend_tracker.data.fmp as fmp_mod

    calls = {"n": 0}

    def fake_get_json(url, timeout=15.0):
        calls["n"] += 1
        return []  # prázdná odpověď stačí — počítáme jen volání

    monkeypatch.setattr(fmp_mod, "_get_json", fake_get_json)
    client = FMPCacheClient(fmp_db)

    for i in range(DAILY_LIMIT):
        assert client.can_call() is True, f"volání {i + 1} mělo být povolené"
        client.get_dividend_history("KO")

    # Limit vyčerpán — další volání musí selhat.
    assert client.can_call() is False
    with pytest.raises(FMPLimitExceeded):
        client.get_dividend_history("KO")


def test_fmp_usage_reflects_calls(fmp_db, monkeypatch):
    import dividend_tracker.data.fmp as fmp_mod

    monkeypatch.setattr(fmp_mod, "_get_json", lambda url, timeout=15.0: [])
    client = FMPCacheClient(fmp_db)
    client.get_dividend_history("KO")
    u = client.usage()
    assert u["calls_used"] == 1
    assert u["daily_limit"] == DAILY_LIMIT
