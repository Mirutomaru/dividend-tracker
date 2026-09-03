"""Testy CLI subpříkazu ``divtrack export`` (offline — analýza je mockovaná)."""

from __future__ import annotations

import csv
import io

from dividend_tracker.cli import main


def _seed_db(path) -> None:
    from dividend_tracker.data.db import Database

    db = Database(path)
    db.upsert_stock("KO", name="Coca-Cola", currency="USD", country="US")
    db.upsert_quote("KO", current_price=62.5)
    db.close()


def _read_csv(path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8-sig")
    return [row for row in csv.reader(io.StringIO(text)) if row]


# ------------------------------------------------------------------ watchlist

def test_export_watchlist_writes_csv(tmp_path, capsys):
    db_path = tmp_path / "wl.db"
    _seed_db(db_path)
    out = tmp_path / "out.csv"

    rc = main(["export", "watchlist", "--db", str(db_path), "--file", str(out)])
    assert rc == 0
    assert out.exists()
    rows = _read_csv(out)
    # hlavička + 1 řádek KO
    assert len(rows) == 2
    header, data = rows[0], dict(zip(rows[0], rows[1]))
    assert "ticker" in header and "net_yield_pct" in header
    assert data["ticker"] == "KO" and data["price"] == "62.5"
    out_text = capsys.readouterr().out
    assert "Exportováno 1 řádků" in out_text


def test_export_watchlist_empty_db_returns_error(tmp_path, capsys):
    db_path = tmp_path / "empty.db"
    from dividend_tracker.data.db import Database

    Database(db_path).close()  # vytvoří prázdnou DB

    rc = main(["export", "watchlist", "--db", str(db_path), "--file", str(tmp_path / "o.csv")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "prázdná" in err


# ------------------------------------------------------------------ analysis

def test_export_analysis_writes_csv(monkeypatch, tmp_path, capsys):
    from dividend_tracker import cli as cli_mod

    def fake_analyze(ticker, **kw):
        return {
            "ticker": ticker,
            "company": {"name": f"Firma {ticker}", "sector": "Tech"},
            "price_current": 10.0,
            "score": {"total_0_100": 55.0},
            "fair_value": {"fair_value": 12.0, "verdict": "undervalued"},
        }

    monkeypatch.setattr(cli_mod, "analyze_ticker", fake_analyze)
    out = tmp_path / "analysis.csv"

    rc = main(["export", "analysis", "ko", "jnj", "--file", str(out)])
    assert rc == 0
    rows = _read_csv(out)
    # hlavička + 2 reporty
    assert len(rows) == 3
    header = rows[0]
    assert "ticker" in header and "fair_value" in header
    tickers = {dict(zip(header, r))["ticker"] for r in rows[1:]}
    assert tickers == {"KO", "JNJ"}


def test_export_analysis_no_tickers_returns_error(tmp_path, capsys):
    rc = main(["export", "analysis", "--file", str(tmp_path / "o.csv")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Zadejte tickery" in err
