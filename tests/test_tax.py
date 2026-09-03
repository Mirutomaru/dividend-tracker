"""Offline testy zdanění dividend — sazby, net yield, přepsání v DB."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dividend_tracker.data.db import Database
from dividend_tracker.data.portfolio import build_portfolio_report
from dividend_tracker.data.watchlist import build_watchlist_rows
from dividend_tracker.domain.tax import (
    WITHHOLDING_RATES,
    net_amount,
    net_yield_pct,
    withholding_rate,
)


# ------------------------------------------------------------------ sazby

def test_builtin_rates():
    assert withholding_rate("US") == 15.0
    assert withholding_rate("GB") == 0.0
    # neznámá země → None (net = hrubý, konzervativní odhad)
    assert withholding_rate("XX") is None
    assert withholding_rate(None) is None


def test_normalize_country():
    from dividend_tracker.domain.tax import _normalize_country

    assert _normalize_country("us") == "US"
    assert _normalize_country("United States") == "US"
    assert _normalize_country("  canada ") == "CA"
    assert _normalize_country(None) is None


def test_override_from_db_wins():
    # přepsání v DB má přednost před vestavěnou sazbou
    assert withholding_rate("US", {"withholding_US": "10"}) == 10.0
    # neplatné přepsání se ignoruje → vestavěná sazba
    assert withholding_rate("US", {"withholding_US": "špatně"}) == 15.0


# ------------------------------------------------------------------ výpočty

def test_net_yield_pct():
    # US: 4 % hrubý → 3,4 % čistý (sazba 15 %)
    assert net_yield_pct(4.0, "US") == pytest.approx(3.4)
    # GB: sazba 0 % → bez změny
    assert net_yield_pct(4.0, "GB") == pytest.approx(4.0)
    # neznámá země → bez sražení
    assert net_yield_pct(4.0, None) == pytest.approx(4.0)
    # None vstoupí → None ven
    assert net_yield_pct(None, "US") is None


def test_net_amount():
    assert net_amount(100.0, "US") == pytest.approx(85.0)  # −15 %
    assert net_amount(100.0, "GB") == pytest.approx(100.0)
    assert net_amount(None, "US") is None


def test_rates_table_sane():
    for code, rate in WITHHOLDING_RATES.items():
        assert 0.0 <= rate <= 100.0, f"sazba pro {code} mimo rozsah"


# ------------------------------------------------------------------ watchlist

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "tax_test.db"))
    today = date.today()
    dates = [(today - timedelta(days=30 * i)).isoformat() for i in range(1, 5)]

    # US akcie — sazba 15 %
    d.upsert_stock("KO", name="Coca-Cola", currency="USD", country="US")
    d.upsert_quote("KO", current_price=60.0)
    d.replace_dividends(
        [{"ticker": "KO", "ex_date": dt, "dividend_amount": 1.0} for dt in dates]
    )

    # UK akcie — sazba 0 %
    d.upsert_stock("AZN", name="AstraZeneca", currency="USD", country="GB")
    d.upsert_quote("AZN", current_price=50.0)
    d.replace_dividends(
        [{"ticker": "AZN", "ex_date": dt, "dividend_amount": 1.2} for dt in dates]
    )

    # akcie bez země — net = hrubý
    d.upsert_stock("XYZ", name="Unknown Co", currency="USD")
    d.upsert_quote("XYZ", current_price=40.0)
    d.replace_dividends(
        [{"ticker": "XYZ", "ex_date": dt, "dividend_amount": 1.6} for dt in dates]
    )

    yield d
    d.close()


def test_watchlist_net_yield(db):
    rows = {r["ticker"]: r for r in build_watchlist_rows(db)}

    # KO: TTM DPS 4,0 / cena 60 → hrubý 6,67 %; US −15 % → 5,67 %
    ko = rows["KO"]
    assert ko["yield_pct"] == pytest.approx(6.67, abs=0.01)
    assert ko["net_yield_pct"] == pytest.approx(5.67, abs=0.02)

    # AZN: TTM 4,8 / 50 → 9,6 %; GB bez daně → stejné
    azn = rows["AZN"]
    assert azn["yield_pct"] == pytest.approx(9.6, abs=0.01)
    assert azn["net_yield_pct"] == pytest.approx(azn["yield_pct"])

    # XYZ: bez země → net = hrubý
    xyz = rows["XYZ"]
    assert xyz["country"] is None
    assert xyz["net_yield_pct"] == pytest.approx(xyz["yield_pct"])


def test_watchlist_net_yield_override(db):
    """Přepsání sazby v DB se projeví v watchlistu."""
    db.set_setting("withholding_US", "0")  # např. držba přes daňový účet
    rows = {r["ticker"]: r for r in build_watchlist_rows(db)}
    assert rows["KO"]["net_yield_pct"] == pytest.approx(rows["KO"]["yield_pct"])


# ------------------------------------------------------------------ portfolio

def test_portfolio_net_income(db):
    db.upsert_position("KO", shares=100, avg_cost=50.0)   # příjem 400 → net 340 (US 15 %)
    db.upsert_position("AZN", shares=50, avg_cost=40.0)   # příjem 240 → net 240 (GB 0 %)

    report = build_portfolio_report(db)
    by_tk = {p["ticker"]: p for p in report["positions"]}

    ko = by_tk["KO"]
    assert ko["annual_income"] == pytest.approx(400.0)
    assert ko["withholding_rate_pct"] == 15.0
    assert ko["net_annual_income"] == pytest.approx(340.0)

    azn = by_tk["AZN"]
    assert azn["annual_income"] == pytest.approx(240.0)
    assert azn["withholding_rate_pct"] == 0.0
    assert azn["net_annual_income"] == pytest.approx(240.0)

    # součty: hrubý 640, čistý 580
    assert report["annual_income"] == pytest.approx(640.0)
    assert report["net_annual_income"] == pytest.approx(580.0)
    # net portfolio yield = 580 / (100×60 + 50×50) = 580 / 8500
    assert report["net_portfolio_yield_pct"] == pytest.approx(6.82, abs=0.01)


def test_portfolio_net_income_override(db):
    db.upsert_position("KO", shares=100, avg_cost=50.0)
    db.set_setting("withholding_US", "0")

    report = build_portfolio_report(db)
    ko = {p["ticker"]: p for p in report["positions"]}["KO"]
    assert ko["net_annual_income"] == pytest.approx(ko["annual_income"])


def test_portfolio_position_without_country(db):
    """Pozice bez země → sazba None, net = hrubý."""
    db.upsert_position("XYZ", shares=10, avg_cost=30.0)

    report = build_portfolio_report(db)
    xyz = {p["ticker"]: p for p in report["positions"]}["XYZ"]
    assert xyz["withholding_rate_pct"] is None
    assert xyz["net_annual_income"] == pytest.approx(xyz["annual_income"])


# ------------------------------------------------------------------ migrace

def test_old_db_migration_adds_country(tmp_path):
    """Stará DB bez sloupce country se při otevření upgraduje."""
    import sqlite3

    path = str(tmp_path / "old.db")
    # vytvoř „starou“ tabulku stocks bez sloupce country
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE stocks (
             ticker TEXT PRIMARY KEY, name TEXT, currency TEXT DEFAULT 'USD',
             exchange TEXT, sector TEXT, industry TEXT,
             last_fundamental_update REAL, fundamentals_json TEXT)"""
    )
    conn.commit()
    conn.close()

    d = Database(path)  # musí upgradovat bez výjimky
    try:
        with d._conn() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(stocks)").fetchall()}
        assert "country" in cols
        # upsert s country funguje i po migraci
        d.upsert_stock("KO", name="Coca-Cola", country="US")
        stock = d.get_stock("KO")
        assert stock is not None and stock["country"] == "US"
    finally:
        d.close()
