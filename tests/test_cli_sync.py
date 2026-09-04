"""Testy CLI subpříkazu ``divtrack sync`` (offline — synchronizace je mockovaná)."""

from __future__ import annotations

from dividend_tracker.cli import main


def test_sync_empty_db_returns_error(tmp_path, capsys):
    """Prázdná DB a žádné tickery → exit 2 + nápověda."""
    rc = main(["sync", "--db", str(tmp_path / "empty.db")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Databáze je prázdná" in err


def test_sync_with_tickers_calls_sync_all(monkeypatch, tmp_path, capsys):
    """divtrack sync KO JNJ → volá sync_all s normalizovanými tickerů."""
    from dividend_tracker import data as data_pkg

    calls = {}

    def fake_sync_all(db_, tickers, force=False):
        calls["tickers"] = tickers
        calls["force"] = force
        return {
            "results": {t: "cached" for t in tickers},
            "counts": {"cached": len(tickers)},
            "fmp_usage": {"calls_used": 0, "daily_limit": None, "remaining": None},
            "quotes": {},
        }

    monkeypatch.setattr(data_pkg.sync, "sync_all", fake_sync_all)

    rc = main(["sync", "ko", "jnj", "--db", str(tmp_path / "x.db")])
    assert rc == 0
    assert calls["tickers"] == ["KO", "JNJ"]
    assert calls["force"] is False
    out = capsys.readouterr().out
    assert "Synchronizuji 2 ticker" in out
    assert "cached: 2" in out


def test_sync_force_flag(monkeypatch, tmp_path):
    """--force se propíše do sync_all."""
    from dividend_tracker import data as data_pkg

    calls = {}

    def fake_sync_all(db_, tickers, force=False):
        calls["force"] = force
        return {"results": {}, "counts": {}, "fmp_usage": {}, "quotes": {}}

    monkeypatch.setattr(data_pkg.sync, "sync_all", fake_sync_all)
    rc = main(["sync", "KO", "--force", "--db", str(tmp_path / "x.db")])
    assert rc == 0
    assert calls["force"] is True


def test_sync_without_args_uses_db_tickers(monkeypatch, tmp_path):
    """Bez argumentů se syncne vše, co je v DB (list_stocks)."""
    from dividend_tracker import data as data_pkg

    db_path = tmp_path / "x.db"
    # naplní DB dvěma tickerů
    from dividend_tracker.data.db import Database

    db = Database(db_path)
    db.upsert_stock("KO", name="Coca-Cola")
    db.upsert_stock("JNJ", name="Johnson & Johnson")
    db.close()

    calls = {}

    def fake_sync_all(db_, tickers, force=False):
        calls["tickers"] = tickers
        return {"results": {}, "counts": {}, "fmp_usage": {}, "quotes": {}}

    monkeypatch.setattr(data_pkg.sync, "sync_all", fake_sync_all)
    rc = main(["sync", "--db", str(db_path)])
    assert rc == 0
    assert calls["tickers"] == ["JNJ", "KO"]  # abecedně


def test_preprocess_does_not_wrap_sync():
    """Zkratka bez podpříkazu nesmí zabalit 'sync' do analyze."""
    from dividend_tracker.cli import _preprocess

    assert _preprocess(["sync", "KO"]) == ["sync", "KO"]
    assert _preprocess(["AAPL"]) == ["analyze", "AAPL"]
