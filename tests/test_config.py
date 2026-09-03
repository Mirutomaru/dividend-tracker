"""Offline testy konfigurace — .env parser, načtení, zápis, maskování."""

from __future__ import annotations

import os

import pytest

from dividend_tracker.config import (
    _parse_env_file,
    load_env,
    mask_secret,
    remove_env_value,
    write_env_value,
)


class TestParseEnvFile:
    def test_basic_pairs(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("FMP_API_KEY=abc123\nDIVTRACK_DB=/tmp/x.db\n", encoding="utf-8")
        vals = _parse_env_file(p)
        assert vals == {"FMP_API_KEY": "abc123", "DIVTRACK_DB": "/tmp/x.db"}

    def test_quotes_and_comments(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text(
            "# komentář\n"
            'KEY="quoted value"\n'
            "OTHER='single'\n"
            "INLINE=abc # inline comment\n"
            "\n",
            encoding="utf-8",
        )
        vals = _parse_env_file(p)
        assert vals["KEY"] == "quoted value"
        assert vals["OTHER"] == "single"
        assert vals["INLINE"] == "abc"

    def test_missing_file(self, tmp_path):
        assert _parse_env_file(tmp_path / "chybi.env") == {}


class TestLoadEnv:
    def test_loads_into_environ_without_overriding(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("DIVTRACK_TEST_KEY=z_v_souboru\n", encoding="utf-8")
        # Přesměruj hledání na náš soubor.
        import dividend_tracker.config as cfg

        monkeypatch.setattr(cfg, "find_env_files", lambda: [env])
        monkeypatch.delenv("DIVTRACK_TEST_KEY", raising=False)
        loaded = load_env()
        assert len(loaded) == 1
        assert os.environ["DIVTRACK_TEST_KEY"] == "z_v_souboru"

        # Existující proměnná se nepřepíše.
        monkeypatch.setenv("DIVTRACK_TEST_KEY", "z_prostredi")
        load_env()
        assert os.environ["DIVTRACK_TEST_KEY"] == "z_prostredi"


class TestWriteRemove:
    def test_write_new_and_update(self, tmp_path):
        p = tmp_path / ".env"
        write_env_value("FMP_API_KEY", "klíč1234567890", p)
        text = p.read_text(encoding="utf-8")
        assert "FMP_API_KEY=klíč1234567890" in text

        # Aktualizace existujícího klíče — bez duplikátu.
        write_env_value("FMP_API_KEY", "nový_klíč", p)
        text = p.read_text(encoding="utf-8")
        assert text.count("FMP_API_KEY=") == 1
        assert "FMP_API_KEY=nový_klíč" in text

    def test_write_preserves_other_lines(self, tmp_path):
        p = tmp_path / ".env"
        write_env_value("A", "1", p)
        write_env_value("B", "2", p)
        vals = _parse_env_file(p)
        assert vals == {"A": "1", "B": "2"}

    def test_remove(self, tmp_path):
        p = tmp_path / ".env"
        write_env_value("A", "1", p)
        write_env_value("B", "2", p)
        assert remove_env_value("A", p) is True
        vals = _parse_env_file(p)
        assert vals == {"B": "2"}
        assert remove_env_value("CHYBI", p) is False


class TestMaskSecret:
    def test_masking(self):
        assert mask_secret(None) == "(nenastaveno)"
        assert mask_secret("") == "(nenastaveno)"
        assert mask_secret("abc") == "****"  # krátké → celé zamaskované
        m = mask_secret("abcd1234efgh")
        assert m.startswith("abcd…") and m.endswith("gh")
