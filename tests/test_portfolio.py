"""Offline testy portfolia — pozice, roční příjem, kalkulačka cílového příjmů."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dividend_tracker.data.db import Database
from dividend_tracker.data.portfolio import (
    build_income_target_report,
    build_portfolio_report,
)


def _quarterly_dates(n: int = 4):
    """Posledních ``n`` kvartálních ex-date (30/120/210/300 dní zpět)."""
    today = date.today()
    return [(today - timedelta(days=30 * i)).isoformat() for i in range(1, n + 1)]


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "pf_test.db"))
    # KO: 100 akcií @ 50 $, cena 60 $, DPS TTM 4.0 (4× 1.00)
    d.upsert_stock("KO", name="Coca-Cola", currency="USD")
    d.upsert_quote("KO", current_price=60.0)
    d.replace_dividends(
        [{"ticker": "KO", "ex_date": dt, "dividend_amount": 1.0} for dt in _quarterly_dates()]
    )
    d.upsert_position("KO", shares=100, avg_cost=50.0)

    # JNJ: 50 akcií @ 150 $, cena 160 $, DPS TTM 5.0 (4× 1.25)
    d.upsert_stock("JNJ", name="Johnson & Johnson", currency="USD")
    d.upsert_quote("JNJ", current_price=160.0)
    d.replace_dividends(
        [{"ticker": "JNJ", "ex_date": dt, "dividend_amount": 1.25} for dt in _quarterly_dates()]
    )
    d.upsert_position("JNJ", shares=50, avg_cost=150.0)

    yield d
    d.close()


def test_portfolio_positions(db):
    report = build_portfolio_report(db)
    assert report["n_positions"] == 2
    by_tk = {p["ticker"]: p for p in report["positions"]}

    ko = by_tk["KO"]
    assert ko["market_value"] == pytest.approx(6000.0)   # 100 × 60
    assert ko["cost_basis"] == pytest.approx(5000.0)     # 100 × 50
    assert ko["unrealized_pl_pct"] == pytest.approx(20.0)
    assert ko["dps_ttm"] == pytest.approx(4.0)
    assert ko["annual_income"] == pytest.approx(400.0)   # 100 × 4.0
    assert ko["yield_on_cost_pct"] == pytest.approx(8.0)  # 4.0 / 50

    jnj = by_tk["JNJ"]
    assert jnj["market_value"] == pytest.approx(8000.0)
    assert jnj["annual_income"] == pytest.approx(250.0)   # 50 × 5.0


def test_portfolio_totals(db):
    report = build_portfolio_report(db)
    assert report["total_market_value"] == pytest.approx(14000.0)
    assert report["annual_income"] == pytest.approx(650.0)
    # yield = 650 / 14000
    assert report["portfolio_yield_pct"] == pytest.approx(4.64, abs=0.01)
    # weighted YoC = 650 / (5000 + 7500)
    assert report["weighted_yield_on_cost_pct"] == pytest.approx(5.2, abs=0.01)
    assert report["income_by_currency"] == {"USD": 650.0}


def test_empty_portfolio(tmp_path):
    d = Database(str(tmp_path / "empty.db"))
    try:
        report = build_portfolio_report(d)
        assert report["n_positions"] == 0
        assert report["positions"] == []
        assert report["total_market_value"] is None
        assert report["annual_income"] == 0.0

        target = build_income_target_report(d)
        assert target == {"configured": False}
    finally:
        d.close()


def test_position_without_price_or_dividends(tmp_path):
    """Pozice bez ceny a bez dividend — hodnota None, příjem 0."""
    d = Database(str(tmp_path / "noquote.db"))
    try:
        d.upsert_stock("XYZ", name="No Data Co")
        d.upsert_position("XYZ", shares=10, avg_cost=25.0)
        report = build_portfolio_report(d)
        pos = report["positions"][0]
        assert pos["price"] is None
        assert pos["market_value"] is None
        assert pos["annual_income"] == 0.0
        assert report["total_market_value"] is None
    finally:
        d.close()


def test_upsert_position_updates_and_removes(db):
    db.upsert_position("KO", shares=150, avg_cost=48.0)
    pos = {p["ticker"]: p for p in build_portfolio_report(db)["positions"]}["KO"]
    assert pos["shares"] == 150
    assert pos["avg_cost"] == pytest.approx(48.0)

    # shares <= 0 smaže pozici
    db.upsert_position("JNJ", shares=0)
    tickers = {p["ticker"] for p in build_portfolio_report(db)["positions"]}
    assert "JNJ" not in tickers


def test_income_target_progress(db):
    db.set_setting("income_target", "1000")
    target = build_income_target_report(db)

    assert target["configured"] is True
    assert target["target_annual_income"] == pytest.approx(1000.0)
    assert target["current_annual_income"] == pytest.approx(650.0)
    assert target["remaining_income"] == pytest.approx(350.0)
    assert target["progress_pct"] == pytest.approx(65.0, abs=0.1)

    # reálný yield portfolia (4.64 %) se použije pro potřebný kapitál
    assert target["effective_yield_pct_used"] == pytest.approx(4.64, abs=0.01)
    expected_capital = 1000 / (target["effective_yield_pct_used"] / 100.0)
    assert target["required_capital"] == pytest.approx(expected_capital, rel=1e-3)


def test_income_target_uses_assumed_yield_when_no_portfolio(tmp_path):
    """Bez pozic se použije nastavený/odhadovaný yield (výchozí 4 %)."""
    d = Database(str(tmp_path / "target_only.db"))
    try:
        d.set_setting("income_target", "6000")
        target = build_income_target_report(d)
        assert target["configured"] is True
        assert target["current_annual_income"] == 0.0
        assert target["progress_pct"] == pytest.approx(0.0)
        assert target["effective_yield_pct_used"] == pytest.approx(4.0)
        assert target["required_capital"] == pytest.approx(150000.0)

        # explicitní odhad yieldu přebije výchozí 4 %
        d.set_setting("target_yield_pct", "3")
        target = build_income_target_report(d)
        assert target["effective_yield_pct_used"] == pytest.approx(3.0)
        assert target["required_capital"] == pytest.approx(200000.0)
    finally:
        d.close()


def test_income_target_invalid_value(tmp_path):
    """Nečíselné nastavení → configured False (bez výjimky)."""
    d = Database(str(tmp_path / "bad.db"))
    try:
        d.set_setting("income_target", "šest tisíc")
        assert build_income_target_report(d) == {"configured": False}
    finally:
        d.close()


# ------------------------------ TWR/IRR end-to-end (DB) ------------------------

def test_portfolio_returns_empty(tmp_path):
    """Bez transakční historie → twr_pct i irr_pct None."""
    d = Database(str(tmp_path / "ret_empty.db"))
    try:
        report = build_portfolio_report(d)
        assert report["twr_pct"] is None
        assert report["irr_pct"] is None
    finally:
        d.close()


def test_portfolio_returns_single_buy_gain(tmp_path):
    """Nákup před rokem za 50, aktuální cena 60 → TWR ≈ +20 %, IRR ≈ +20 % p.a."""
    from datetime import date, timedelta

    d = Database(str(tmp_path / "ret.db"))
    try:
        buy_date = (date.today() - timedelta(days=365)).isoformat()
        d.upsert_stock("KO", name="Coca-Cola")
        d.upsert_quote("KO", current_price=60.0)
        d.record_price_snapshot("KO", buy_date, 50.0)
        # Nákup 100 akcií za 50 $ (buy = záporný cash flow).
        d.add_transaction("KO", "buy", buy_date, shares=100, price=50.0, amount=5000.0)

        report = build_portfolio_report(d)
        assert report["twr_pct"] == pytest.approx(20.0, abs=0.5)
        assert report["irr_pct"] == pytest.approx(20.0, abs=0.5)
    finally:
        d.close()


def test_portfolio_returns_upsert_auto_transaction(tmp_path):
    """upsert_position automaticky zapíše buy transakci; jednodenní nákup → TWR/IRR None."""
    from datetime import date

    d = Database(str(tmp_path / "ret_auto.db"))
    try:
        # Pozice bez explicitní transakce — upsert_position ji doplní (buy za avg_cost).
        d.upsert_stock("KO", name="Coca-Cola")
        d.upsert_quote("KO", current_price=60.0)
        d.record_price_snapshot("KO", date.today().isoformat(), 50.0)
        d.upsert_position("KO", shares=100, avg_cost=50.0)

        txns = d.list_transactions()
        assert len(txns) == 1 and txns[0]["kind"] == "buy"
        # buy cash flow je záporný (peníze odcházejí z portfolia).
        assert txns[0]["amount"] == pytest.approx(5000.0)

        report = build_portfolio_report(d)
        # Nákup i hodnocení ve stejný den → jen 1 datum, TWR/IRR ještě nedefinovatelné.
        assert report["twr_pct"] is None
        assert report["irr_pct"] is None
    finally:
        d.close()


def test_portfolio_returns_with_dividend(tmp_path):
    """Nákup + dividendový cash flow — TWR zohlední výplatu, IRR se řeší bisekcí."""
    from datetime import date, timedelta

    d = Database(str(tmp_path / "ret_div.db"))
    try:
        buy_date = (date.today() - timedelta(days=365)).isoformat()
        div_date = (date.today() - timedelta(days=180)).isoformat()
        d.upsert_stock("KO", name="Coca-Cola")
        d.upsert_quote("KO", current_price=62.0)
        d.record_price_snapshot("KO", buy_date, 50.0)
        d.record_price_snapshot("KO", div_date, 58.0)
        d.add_transaction("KO", "buy", buy_date, shares=100, price=50.0, amount=5000.0)
        # Dividenda 2 $/akcii na ex-date (držíme 100 akcií → +200 cash flow).
        d.upsert_dividend("KO", div_date, dividend_amount=2.0)

        report = build_portfolio_report(d)
        assert report["twr_pct"] is not None
        assert report["irr_pct"] is not None
    finally:
        d.close()
