"""Testy Fáze 4 — CLI flagy ``--refresh`` a ``--verbose`` (offline, analýza mockovaná)."""

from __future__ import annotations

import logging

import pytest

from dividend_tracker.cli import main


def _fake_report(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "company": {"name": f"Firma {ticker}", "sector": "Tech"},
        "price_current": 10.0,
        # metrics sub-dicty musí existovat (format_ticker_block je indexuje); vnitřní klíče
        # se čtou přes .get() → prázdné dicty stačí pro test wiringu flagů.
        "metrics": {"dividend": {}, "valuation": {}, "health": {}},
        "score": {"total_0_100": 55.0, "applicable": False},
        "fair_value": {
            "fair_value": 12.0,
            "verdict": "undervalued",
            "buy_zone_low": 9.0,
            "buy_zone_high": 11.0,
        },
    }


@pytest.fixture
def record_basic_config(monkeypatch):
    """Zachytí volání ``logging.basicConfig`` (level, format) místo reálného nastavení.

    Reálné basicConfig je no-op, pokud root logger už má handler — a pytest obaluje call
    fázi log-capture handlerem, takže by level nikdy nedosáhl. Mockování je deterministické.
    """
    calls: list[dict] = []

    def _record(level=None, format=None, **kw):  # noqa: A002 — podpis basicConfig
        calls.append({"level": level, "format": format})

    monkeypatch.setattr(logging, "basicConfig", _record)
    return calls


# ------------------------------------------------------------------ --refresh

def test_analyze_passes_refresh_to_pipeline(monkeypatch):
    """``divtrack analyze KO --refresh`` → refresh=True dorazí do analyze_ticker."""
    from dividend_tracker import cli as cli_mod

    seen: list[dict] = []

    def fake_analyze(ticker, **kw):
        seen.append(kw)
        return _fake_report(ticker)

    monkeypatch.setattr(cli_mod, "analyze_ticker", fake_analyze)
    rc = main(["analyze", "ko", "--refresh"])
    assert rc == 0
    assert len(seen) == 1
    assert seen[0].get("refresh") is True


def test_analyze_default_refresh_false(monkeypatch):
    """Bez flagu → refresh=False (výchozí chování se nepromění)."""
    from dividend_tracker import cli as cli_mod

    seen: list[dict] = []

    def fake_analyze(ticker, **kw):
        seen.append(kw)
        return _fake_report(ticker)

    monkeypatch.setattr(cli_mod, "analyze_ticker", fake_analyze)
    rc = main(["analyze", "ko"])
    assert rc == 0
    assert seen[0].get("refresh") is False


def test_export_analysis_passes_refresh(monkeypatch, tmp_path):
    """``divtrack export analysis KO --refresh`` → refresh=True i v exportu."""
    from dividend_tracker import cli as cli_mod

    seen: list[dict] = []

    def fake_analyze(ticker, **kw):
        seen.append(kw)
        return _fake_report(ticker)

    monkeypatch.setattr(cli_mod, "analyze_ticker", fake_analyze)
    rc = main(["export", "analysis", "ko", "--refresh", "--file", str(tmp_path / "a.csv")])
    assert rc == 0
    assert seen[0].get("refresh") is True


# ------------------------------------------------------------------ --verbose

def test_verbose_flag_sets_info_level(monkeypatch, record_basic_config):
    """``divtrack analyze KO --verbose`` → basicConfig(level=INFO)."""
    from dividend_tracker import cli as cli_mod

    monkeypatch.setattr(cli_mod, "analyze_ticker", lambda t, **kw: _fake_report(t))
    rc = main(["analyze", "ko", "--verbose"])
    assert rc == 0
    assert record_basic_config[0]["level"] == logging.INFO


def test_default_level_is_warning(monkeypatch, record_basic_config):
    """Bez --verbose → basicConfig(level=WARNING) (výchozí)."""
    from dividend_tracker import cli as cli_mod

    monkeypatch.setattr(cli_mod, "analyze_ticker", lambda t, **kw: _fake_report(t))
    rc = main(["analyze", "ko"])
    assert rc == 0
    assert record_basic_config[0]["level"] == logging.WARNING


def test_global_verbose_before_subcommand(monkeypatch, record_basic_config):
    """Globální pozice flagu: ``divtrack --verbose analyze KO`` → INFO."""
    from dividend_tracker import cli as cli_mod

    monkeypatch.setattr(cli_mod, "analyze_ticker", lambda t, **kw: _fake_report(t))
    rc = main(["--verbose", "analyze", "ko"])
    assert rc == 0
    assert record_basic_config[0]["level"] == logging.INFO


def test_short_v_flag(monkeypatch, record_basic_config):
    """Zkratka ``-v`` funguje za subkomandem → INFO."""
    from dividend_tracker import cli as cli_mod

    monkeypatch.setattr(cli_mod, "analyze_ticker", lambda t, **kw: _fake_report(t))
    rc = main(["analyze", "ko", "-v"])
    assert rc == 0
    assert record_basic_config[0]["level"] == logging.INFO
