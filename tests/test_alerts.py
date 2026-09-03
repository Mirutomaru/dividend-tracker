"""Offline testy upozornění — vyhodnocení pravidel proti cenám v DB."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dividend_tracker.data.alerts import add_alert, evaluate_alerts
from dividend_tracker.data.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "alerts_test.db"))
    yield d
    d.close()


def _seed_dividends(db: Database, ticker: str, quarterly: float = 0.62) -> None:
    """4 kvartální platby v posledních ~12 měsících → TTM DPS = 4×quarterly."""
    today = date.today()
    rows = []
    for i in range(4):
        ex = (today - timedelta(days=90 * i)).isoformat()
        rows.append({"ticker": ticker, "ex_date": ex, "dividend_amount": quarterly})
    db.replace_dividends(rows)


# ------------------------------------------------------------------ validace

def test_add_alert_requires_threshold(db):
    with pytest.raises(ValueError):
        add_alert(db, "KO", "below")  # bez prahové hodnoty
    with pytest.raises(ValueError):
        add_alert(db, "KO", "above", threshold=-5)


def test_add_alert_rejects_unknown_condition(db):
    with pytest.raises(ValueError):
        add_alert(db, "KO", "sideways", threshold=10)


def test_buy_zone_needs_no_threshold(db):
    aid = add_alert(db, "KO", "buy_zone")
    assert aid >= 1
    rows = db.list_alerts()
    assert len(rows) == 1 and rows[0]["condition"] == "buy_zone"


# ------------------------------------------------------------------ vyhodnocení

def test_below_triggers_when_price_at_or_under_threshold(db):
    _seed_dividends(db, "KO")
    db.upsert_quote("KO", current_price=64.9)
    add_alert(db, "KO", "below", threshold=65.0)

    triggered = evaluate_alerts(db)
    assert len(triggered) == 1
    t = triggered[0]
    assert t["ticker"] == "KO" and t["condition"] == "below"
    assert t["price"] == pytest.approx(64.9)
    # označeno jako vyhodnocené dnes → anti-duplikace
    assert db.list_alerts()[0]["last_triggered"] == date.today().isoformat()


def test_below_does_not_trigger_above_threshold(db):
    _seed_dividends(db, "KO")
    db.upsert_quote("KO", current_price=70.0)
    add_alert(db, "KO", "below", threshold=65.0)

    assert evaluate_alerts(db) == []


def test_above_triggers_when_price_at_or_over_threshold(db):
    _seed_dividends(db, "JNJ")
    db.upsert_quote("JNJ", current_price=160.0)
    add_alert(db, "JNJ", "above", threshold=158.0)

    triggered = evaluate_alerts(db)
    assert len(triggered) == 1 and triggered[0]["condition"] == "above"


def test_buy_zone_triggers_at_fair_value(db):
    """FV = DPS_TTM / r; DPS 2.48 @ r=8 % → FV 31.00."""
    _seed_dividends(db, "KO", quarterly=0.62)  # TTM = 2.48
    db.upsert_quote("KO", current_price=30.5)
    add_alert(db, "KO", "buy_zone")

    triggered = evaluate_alerts(db)
    assert len(triggered) == 1
    assert "férová" in triggered[0]["detail"]


def test_buy_zone_ignores_price_above_fair_value(db):
    _seed_dividends(db, "KO", quarterly=0.62)  # FV = 31.00
    db.upsert_quote("KO", current_price=45.0)
    add_alert(db, "KO", "buy_zone")

    assert evaluate_alerts(db) == []


def test_buy_zone_without_dps_is_skipped(db):
    """Bez dividendové historie se férová cena nepočítá — pravidlo se přeskočí."""
    db.upsert_quote("KO", current_price=30.0)
    add_alert(db, "KO", "buy_zone")

    assert evaluate_alerts(db) == []


def test_no_quote_in_db_is_skipped(db):
    _seed_dividends(db, "KO")  # dividendy jsou, ale žádná cena v quotes
    add_alert(db, "KO", "below", threshold=65.0)

    assert evaluate_alerts(db) == []


def test_no_duplicate_trigger_same_day(db):
    """Druhé vyhodnocení ve stejný den se už neopakuje (anti-duplikace)."""
    _seed_dividends(db, "KO")
    db.upsert_quote("KO", current_price=60.0)
    add_alert(db, "KO", "below", threshold=65.0)

    first = evaluate_alerts(db)
    second = evaluate_alerts(db)
    assert len(first) == 1 and second == []


def test_inactive_alert_is_ignored(db):
    _seed_dividends(db, "KO")
    db.upsert_quote("KO", current_price=60.0)
    aid = add_alert(db, "KO", "below", threshold=65.0)
    db.set_alert_active(aid, False)

    assert evaluate_alerts(db) == []


def test_multiple_alerts_evaluated_independently(db):
    _seed_dividends(db, "KO")
    _seed_dividends(db, "JNJ", quarterly=1.0)  # TTM = 4.0 → FV @8 % = 50.0
    db.upsert_quote("KO", current_price=60.0)   # pod 65 → below hit
    db.upsert_quote("JNJ", current_price=90.0)  # nad 50, ale above prah 100 → miss

    add_alert(db, "KO", "below", threshold=65.0)
    add_alert(db, "JNJ", "above", threshold=100.0)

    triggered = evaluate_alerts(db)
    assert [t["ticker"] for t in triggered] == ["KO"]


def test_required_return_setting_respected(db):
    """Nastavení required_return_pct mění férovou cenu buy_zone."""
    _seed_dividends(db, "KO", quarterly=0.62)  # TTM = 2.48
    db.set_setting("required_return_pct", "10")  # FV = 2.48/0.10 = 24.80
    db.upsert_quote("KO", current_price=25.0)  # nad 24.80 → miss

    add_alert(db, "KO", "buy_zone")
    assert evaluate_alerts(db) == []
