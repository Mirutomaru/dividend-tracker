"""Offline testy perzistentní watchlisty (tabulka + souborová záloha)."""

from __future__ import annotations

import json

import pytest

from dividend_tracker.data.db import Database
from dividend_tracker.data.watchlist_store import (
    auto_add_to_watchlist,
    export_watchlist_file,
    import_watchlist_file,
)


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "wl.db")
    yield d
    d.close()


@pytest.fixture()
def wl_file(tmp_path):
    return tmp_path / "watchlist.json"


class TestWatchlistTable:
    def test_upsert_idempotent(self, db):
        db.upsert_watchlist("ko", note="první")
        db.upsert_watchlist("KO")  # bez note — původní se neztratí
        rows = db.list_watchlist()
        assert len(rows) == 1
        assert rows[0]["ticker"] == "KO"
        assert rows[0]["note"] == "první"

    def test_note_update(self, db):
        db.upsert_watchlist("jnj", note="stará")
        db.upsert_watchlist("JNJ", note="nová")
        assert db.list_watchlist()[0]["note"] == "nová"

    def test_remove_and_is_in(self, db):
        db.upsert_watchlist("msft")
        assert db.is_in_watchlist("MSFT")
        db.remove_from_watchlist("msft")
        assert not db.is_in_watchlist("MSFT")


class TestFileBackup:
    def test_export_creates_readable_file(self, db, wl_file):
        db.upsert_watchlist("ko", note="kralovna")
        path = export_watchlist_file(db, wl_file)
        assert path == wl_file and wl_file.exists()
        payload = json.loads(wl_file.read_text(encoding="utf-8"))
        assert payload["tickers"][0]["ticker"] == "KO"
        assert payload["updated_at"] > 0

    def test_import_restores_into_db(self, db, wl_file):
        # Soubor existuje, DB je prázdná → obnova.
        wl_file.write_text(
            json.dumps({"tickers": [{"ticker": "KO"}, {"ticker": "JNJ", "note": "x"}]}),
            encoding="utf-8",
        )
        added = import_watchlist_file(db, wl_file)
        assert added == 2
        tickers = {r["ticker"] for r in db.list_watchlist()}
        assert tickers == {"KO", "JNJ"}

    def test_import_is_merge_not_replace(self, db, wl_file):
        db.upsert_watchlist("existing")
        wl_file.write_text(json.dumps({"tickers": [{"ticker": "NEW"}, {"ticker": "EXISTING"}]}), encoding="utf-8")
        added = import_watchlist_file(db, wl_file)
        assert added == 1  # EXISTING už je v DB
        assert {r["ticker"] for r in db.list_watchlist()} == {"EXISTING", "NEW"}

    def test_import_missing_file_returns_zero(self, db, tmp_path):
        assert import_watchlist_file(db, tmp_path / "chybi.json") == 0

    def test_import_corrupt_file_returns_zero(self, db, wl_file):
        wl_file.write_text("{neplatné json", encoding="utf-8")
        assert import_watchlist_file(db, wl_file) == 0


class TestAutoAdd:
    def test_auto_add_and_backup(self, db, tmp_path, monkeypatch):
        # Přesměruj výchozí cestu zálohy do tmp.
        import dividend_tracker.data.watchlist_store as store

        monkeypatch.setattr(store, "DEFAULT_WATCHLIST_FILE", tmp_path / "auto.json")
        added = auto_add_to_watchlist(db, ["ko", "JNJ", "ko"])  # duplicita ignorována
        assert added == 2
        assert (tmp_path / "auto.json").exists()

    def test_auto_add_idempotent(self, db, tmp_path, monkeypatch):
        import dividend_tracker.data.watchlist_store as store

        monkeypatch.setattr(store, "DEFAULT_WATCHLIST_FILE", tmp_path / "auto.json")
        auto_add_to_watchlist(db, ["ko"])
        assert auto_add_to_watchlist(db, ["KO"]) == 0


class TestSyncAutoAdds:
    def test_sync_all_populates_persistent_watchlist(self, db, monkeypatch):
        """sync_all automaticky přidá tickery do trvalé watchlisty."""
        import dividend_tracker.data.sync as sync_mod

        captured = {}

        def fake_fundamentals(d, tickers, force=False):
            return {"counts": {}, "tickers": list(tickers)}

        def fake_quotes(d, tickers):
            captured["quotes"] = list(tickers)
            return {t: 10.0 for t in tickers}

        monkeypatch.setattr(sync_mod, "sync_fundamentals", fake_fundamentals)
        import dividend_tracker.data.quotes as quotes_mod

        monkeypatch.setattr(quotes_mod, "fetch_quotes", lambda ts: {t: {"price": 10.0} for t in ts})

        summary = sync_mod.sync_all(db, ["KO", "JNJ"])
        assert db.is_in_watchlist("KO") and db.is_in_watchlist("JNJ")
        assert set(summary["tickers"]) == {"KO", "JNJ"}
