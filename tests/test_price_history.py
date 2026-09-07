"""Offline testy historie cen — snapshoty, 52tídní statistiky, backfill."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dividend_tracker.data.db import Database
from dividend_tracker.data.quotes import (
    backfill_price_history,
    sync_quotes,
)


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "ph_test.db"))
    yield d
    d.close()


def _dates(n: int):
    """Posledních ``n`` dní (nejstarší první)."""
    today = date.today()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


# ------------------------------------------------------------------ snapshoty

def test_record_snapshot_upserts(db):
    d0 = _dates(1)[0]
    db.record_price_snapshot("KO", d0, 60.0)
    db.record_price_snapshot("KO", d0, 62.5)  # stejný den → aktualizace
    hist = db.get_price_history("KO")
    assert len(hist) == 1
    assert hist[0]["close"] == pytest.approx(62.5)


def test_record_snapshot_ignores_none(db):
    d0 = _dates(1)[0]
    db.record_price_snapshot("KO", d0, None)
    assert db.get_price_history("KO") == []


def test_get_price_history_window_and_order(db):
    dates = _dates(5)
    for i, d in enumerate(dates):
        db.record_price_snapshot("KO", d, 10.0 + i)
    hist = db.get_price_history("KO", days=365)
    # nejnovější první
    assert [r["date"] for r in hist] == list(reversed(dates))

    # okno: jen poslední 2 dny
    hist2 = db.get_price_history("KO", days=1)
    assert len(hist2) <= 2


# ------------------------------------------------------------------ 52t statistiky

def test_price_stats_52w(db):
    dates = _dates(6)
    # ceny: 100, 90, 80, 70, 60, 65 (poslední = aktuální)
    prices = [100.0, 90.0, 80.0, 70.0, 60.0, 65.0]
    for d, p in zip(dates, prices):
        db.record_price_snapshot("KO", d, p)

    stats = db.price_stats_52w("KO")
    assert stats["low_52w"] == pytest.approx(60.0)
    assert stats["high_52w"] == pytest.approx(100.0)
    assert stats["current"] == pytest.approx(65.0)
    # drawdown od maxima: (65 - 100) / 100 = -35 %
    assert stats["drawdown_from_high_pct"] == pytest.approx(-35.0)
    # percentil: kolik bodů je NÍŽŠÍ než 65 → jen 60 → 1/6 ≈ 16.7 %
    assert stats["percentile_52w"] == pytest.approx(16.7, abs=0.1)
    assert stats["points"] == 6


def test_price_stats_at_high(db):
    """Cena na maximu → drawdown 0, percentil ~100."""
    dates = _dates(4)
    for d, p in zip(dates, [50.0, 60.0, 70.0, 80.0]):
        db.record_price_snapshot("KO", d, p)
    stats = db.price_stats_52w("KO")
    assert stats["drawdown_from_high_pct"] == pytest.approx(0.0)
    assert stats["percentile_52w"] == pytest.approx(75.0)  # 3 body pod 80


def test_price_stats_empty(db):
    assert db.price_stats_52w("KO") is None


# ------------------------------------------------------------------ sync_quotes

def test_sync_quotes_writes_daily_snapshot(monkeypatch, db):
    """Každý úspěšný quote se zároveň zapíše do price_history (dnešek)."""
    import dividend_tracker.data.quotes as quotes_mod

    monkeypatch.setattr(
        quotes_mod, "fetch_quotes",
        lambda tickers, db=None: {"KO": {"price": 61.0, "change": 0.5, "volume": 1000}},
    )
    stats = sync_quotes(db, ["KO"])
    assert stats == {"updated": 1, "failed": 0}

    today = date.today().isoformat()
    hist = db.get_price_history("KO")
    assert len(hist) == 1 and hist[0]["date"] == today
    assert hist[0]["close"] == pytest.approx(61.0)


def test_sync_quotes_failed_ticker_no_snapshot(monkeypatch, db):
    import dividend_tracker.data.quotes as quotes_mod

    monkeypatch.setattr(quotes_mod, "fetch_quotes", lambda tickers, db=None: {})
    stats = sync_quotes(db, ["KO"])
    assert stats == {"updated": 0, "failed": 1}
    assert db.get_price_history("KO") == []


# ------------------------------------------------------------------ backfill

def test_backfill_writes_history(monkeypatch, db):
    import dividend_tracker.data.quotes as quotes_mod

    dates = _dates(3)
    monkeypatch.setattr(
        quotes_mod, "fetch_price_history",
        lambda tickers, days=365, db=None: {"KO": [(d, 10.0 + i) for i, d in enumerate(dates)]},
    )
    result = backfill_price_history(db, ["KO"])
    assert result == {"points": 3, "tickers_with_data": 1}

    hist = db.get_price_history("KO")
    assert len(hist) == 3
    stats = db.price_stats_52w("KO")
    assert stats is not None and stats["points"] == 3


def test_backfill_empty(monkeypatch, db):
    import dividend_tracker.data.quotes as quotes_mod

    monkeypatch.setattr(quotes_mod, "fetch_price_history", lambda tickers, days=365, db=None: {})
    result = backfill_price_history(db, ["KO"])
    assert result == {"points": 0, "tickers_with_data": 0}


# ------------------------------------------------------------------ watchlist řádky

def test_watchlist_rows_include_52w_fields(db):
    """Watchlist řádek obsahuje nové 52tídní atributy (None bez historie)."""
    from dividend_tracker.data.watchlist import build_watchlist_rows

    db.upsert_stock("KO", name="Coca-Cola", currency="USD")
    db.upsert_quote("KO", current_price=60.0)

    rows = {r["ticker"]: r for r in build_watchlist_rows(db)}
    row = rows["KO"]
    # bez historie cen → None (frontend vykreslí „—“)
    assert row["low_52w"] is None
    assert row["high_52w"] is None
    assert row["drawdown_from_high_pct"] is None
    assert row["percentile_52w"] is None

    # po doplnění historie se hodnoty naplní
    dates = _dates(3)
    for d, p in zip(dates, [70.0, 65.0, 60.0]):
        db.record_price_snapshot("KO", d, p)

    rows = {r["ticker"]: r for r in build_watchlist_rows(db)}
    row = rows["KO"]
    assert row["low_52w"] == pytest.approx(60.0)
    assert row["high_52w"] == pytest.approx(70.0)
    assert row["drawdown_from_high_pct"] == pytest.approx(-14.29, abs=0.01)
