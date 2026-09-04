"""Testy konzolové tabulky watchlisty (hrubý → čistý yield po srážkové dani).

Pokrývá prezentační funkci ``format_watchlist_table`` i napojení do CLI
(``divtrack sync`` na konci tiskne srovnávací tabulku z lokální DB).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dividend_tracker.cli import main
from dividend_tracker.presentation.console import format_watchlist_table


def _row(**kw):
    """Výchozí řádek ve tvaru ``build_watchlist_rows()`` + přepsání klíčů."""
    base = {
        "ticker": "KO",
        "name": "Coca-Cola",
        "currency": "USD",
        "price": 60.0,
        "yield_pct": 6.67,
        "net_yield_pct": 5.67,
        "streak_years": 3,
        "next_ex_date": "2026-10-01",
    }
    base.update(kw)
    return base


# ------------------------------------------------------------------ formát

def test_empty_rows():
    assert format_watchlist_table([]) == "(watchlista je prázdná)"


def test_us_stock_shows_gross_and_net():
    """US (sazba 15 %) → sloupec YIELD ukáže hrubý i čistý yield."""
    out = format_watchlist_table([_row()])
    assert "6.67% → 5.67%" in out


def test_zero_rate_shows_gross_only():
    """GB (sazba 0 %) — net == hrubý, duplicitní hodnota se neopakuje."""
    out = format_watchlist_table([_row(ticker="AZN", yield_pct=9.6, net_yield_pct=9.6)])
    line = [ln for ln in out.splitlines() if ln.startswith("AZN")][0]
    assert "9.60%" in line
    # data řádek bez šipky (hlavička ji má vždy)
    assert "→" not in line


def test_unknown_country_shows_gross_only():
    """Neznámá země → net == hrubý (konzervativní odhad)."""
    out = format_watchlist_table([_row(ticker="XYZ", yield_pct=4.0, net_yield_pct=4.0)])
    line = [ln for ln in out.splitlines() if ln.startswith("XYZ")][0]
    assert "4.00%" in line
    assert "→" not in line


def test_missing_values_render_dash():
    """Chybějící cena/yield/streak → pomlčky, tabulka nepadne."""
    out = format_watchlist_table(
        [_row(ticker="NEW", price=None, yield_pct=None, net_yield_pct=None, streak_years=0)]
    )
    assert "NEW" in out
    # None cena a None yield → pomlčky
    lines = [ln for ln in out.splitlines() if ln.startswith("NEW")]
    assert "—" in lines[0]


def test_streak_and_next_ex_date():
    out = format_watchlist_table([_row(streak_years=12, next_ex_date="2026-12-15")])
    line = [ln for ln in out.splitlines() if ln.startswith("KO")][0]
    assert "12" in line
    assert "2026-12-15" in line


def test_header_present():
    out = format_watchlist_table([_row()])
    first = out.splitlines()[0]
    assert "TICKER" in first and "YIELD (hrubý → čistý)" in first


# ------------------------------------------------------------------ CLI sync

@pytest.fixture
def db(tmp_path):
    """DB s jednou US akcí — TTM DPS 4,0 / cena 60 → hrubý 6,67 %, net 5,67 %."""
    from dividend_tracker.data.db import Database

    d = Database(str(tmp_path / "wl_table.db"))
    today = date.today()
    dates = [(today - timedelta(days=30 * i)).isoformat() for i in range(1, 5)]
    d.upsert_stock("KO", name="Coca-Cola", currency="USD", country="US")
    d.upsert_quote("KO", current_price=60.0)
    d.replace_dividends(
        [{"ticker": "KO", "ex_date": dt, "dividend_amount": 1.0} for dt in dates]
    )
    yield d
    d.close()


def test_sync_prints_watchlist_table(db, monkeypatch, capsys):
    """Po ``divtrack sync`` se vytiskne tabulka s hrubým i čistým yieldem."""
    from dividend_tracker import data as data_pkg

    def fake_sync_all(d_, tickers, force=False):
        return {"results": {}, "counts": {}, "fmp_usage": {}, "quotes": {}}

    monkeypatch.setattr(data_pkg.sync, "sync_all", fake_sync_all)

    rc = main(["sync", "--db", str(db.path)])
    assert rc == 0
    out = capsys.readouterr().out
    # tabulka se vytiskla a obsahuje oba yielde (US −15 %)
    assert "YIELD (hrubý → čistý)" in out
    assert "6.67% → 5.67%" in out


def test_sync_empty_db_no_table(monkeypatch, tmp_path, capsys):
    """DB bez akcií → sync projde, ale srovnávací tabulka se netiskne."""
    from dividend_tracker import data as data_pkg

    def fake_sync_all(d_, tickers, force=False):
        return {"results": {}, "counts": {}, "fmp_usage": {}, "quotes": {}}

    monkeypatch.setattr(data_pkg.sync, "sync_all", fake_sync_all)

    rc = main(["sync", "KO", "--db", str(tmp_path / "empty.db")])
    assert rc == 0
    out = capsys.readouterr().out
    # žádné akcie v DB → srovnávací tabulka se netiskne
    assert "YIELD (hrubý → čistý)" not in out
