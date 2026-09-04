"""Testy CLI subkomandu ``divtrack scan`` (offline, analýza mockovaná).

Pokrývá: filtr zelený Safety flag + BUY_ZONE, čtení tickerů ze souboru
(--file), fallback na watchlistu z DB, propas --refresh/--no-benchmark a
odolnost vůči chybám jednotlivých tickerů.
"""

from __future__ import annotations

from dividend_tracker.cli import main


def _scan_report(ticker: str, safety: str = "green", signals=()) -> dict:
    """Report v minimální struktuře, kterou zvládnou format_summary_table i block."""
    return {
        "ticker": ticker,
        "company": {"name": f"Firma {ticker}", "sector": "Tech"},
        "price_current": 10.0,
        "metrics": {"dividend": {}, "valuation": {}, "health": {}},
        "score": {
            "total_0_100": 55.0,
            "applicable": True,
            "safety_flag": safety,
            "signals": list(signals),
            "signals_display": [f"• {s}" for s in signals],
        },
        "fair_value": {
            "fair_value": 12.0,
            "verdict": "undervalued",
            "buy_zone_low": 9.0,
            "buy_zone_high": 11.0,
        },
    }


def _mock_analyze(monkeypatch, reports_by_ticker: dict):
    """Nahradí analyze_ticker; vrací report podle tickeru a zaznamená kwargs."""
    from dividend_tracker import cli as cli_mod

    seen: list[dict] = []

    def fake_analyze(ticker, **kw):
        seen.append({"ticker": ticker, "kw": kw})
        return reports_by_ticker[ticker.upper()]

    monkeypatch.setattr(cli_mod, "analyze_ticker", fake_analyze)
    return seen


# ------------------------------------------------------------------ filtr


def test_scan_filters_green_buy_zone(monkeypatch, capsys):
    """Zelený flag + BUY_ZONE → v tabulce; žlutý i zelený bez signálu → ven."""
    from dividend_tracker.domain.scoring import SIGNAL_BUY_ZONE

    reports = {
        "KO": _scan_report("KO", "green", [SIGNAL_BUY_ZONE]),   # match
        "XYZ": _scan_report("XYZ", "yellow"),                    # ne (flag)
        "ABC": _scan_report("ABC", "green"),                     # ne (signál)
    }
    seen = _mock_analyze(monkeypatch, reports)
    rc = main(["scan", "ko", "xyz", "abc"])

    assert rc == 0
    out = capsys.readouterr().out
    assert len(seen) == 3                      # všechny se analyzovaly
    assert "KO" in out                          # match v tabulce
    assert "XYZ" not in out.split("splňuje")[1]  # ne-match mimo výslednou tabulku
    assert "ABC" not in out.split("splňuje")[1]
    assert "Z 3 analyzovaných akcií splňuje zelený flag + BUY_ZONE: 1" in out


def test_scan_no_match_message(monkeypatch, capsys):
    """Žádný match → výslovná zpráva, rc 0 (není to chyba)."""
    reports = {"KO": _scan_report("KO", "yellow")}
    _mock_analyze(monkeypatch, reports)
    rc = main(["scan", "ko"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "nesplňuje" in out


# ------------------------------------------------------------------ --file


def test_scan_reads_file_with_comments_and_dups(monkeypatch, tmp_path):
    """--file: komentáře/prázdné řádky se ignorují, duplikáty deduplikují."""
    f = tmp_path / "watchlist.txt"
    f.write_text("ko\n# kralovna dividend\n\nKO\nabc\n", encoding="utf-8")

    reports = {
        "KO": _scan_report("KO"),
        "ABC": _scan_report("ABC"),
    }
    seen = _mock_analyze(monkeypatch, reports)
    rc = main(["scan", "--file", str(f)])

    assert rc == 0
    tickers_seen = [s["ticker"] for s in seen]
    assert tickers_seen == ["KO", "ABC"]       # dedup + pořadí prvního výskytu


def test_scan_missing_file_error(monkeypatch, tmp_path, capsys):
    """--file na neexistující cestu → rc 2, chyba na stderr."""
    _mock_analyze(monkeypatch, {})
    rc = main(["scan", "--file", str(tmp_path / "neni.txt")])

    assert rc == 2
    err = capsys.readouterr().err
    assert "neexistuje" in err


def test_scan_unreadable_file_error(monkeypatch, tmp_path, capsys):
    """--file na adresář (IsADirectoryError ⊂ OSError) → rc 2, ne traceback."""
    _mock_analyze(monkeypatch, {})
    d = tmp_path / "adresar"
    d.mkdir()
    rc = main(["scan", "--file", str(d)])

    assert rc == 2
    err = capsys.readouterr().err
    assert "Chyba při čtení souboru" in err


def test_scan_empty_file_no_db_fallback(monkeypatch, tmp_path, capsys):
    """--file bez platných tickerů → rc 2; nesmí spadnout na watchlistu z DB."""
    from dividend_tracker.data.db import Database

    db = Database(tmp_path / "scan.db")
    try:
        db.upsert_watchlist("KO")   # v DB je ticker — fallback by ho analyzoval
    finally:
        db.close()

    f = tmp_path / "prazdny.txt"
    f.write_text("# jen komentář\n\n", encoding="utf-8")

    seen = _mock_analyze(monkeypatch, {"KO": _scan_report("KO")})
    rc = main(["scan", "--file", str(f), "--db", str(tmp_path / "scan.db")])

    assert rc == 2
    err = capsys.readouterr().err
    assert "Žádné platné tickery v souboru" in err
    assert seen == []               # nic se nespustilo


def test_scan_invalid_positional_tickers(monkeypatch, tmp_path, capsys):
    """Neplatné pozicní tickery se ignorují (warning); všechny neplatné → rc 2."""
    from dividend_tracker.data.db import Database

    db = Database(tmp_path / "scan.db")
    try:
        db.upsert_watchlist("KO")   # fallback by spustil KO — nesmí
    finally:
        db.close()

    seen = _mock_analyze(monkeypatch, {"KO": _scan_report("KO"), "ABC": _scan_report("ABC")})
    rc = main(["scan", "1BAD!", "abc", "--db", str(tmp_path / "scan.db")])

    assert rc == 0
    err = capsys.readouterr().err
    assert "Ignorován neplatný ticker" in err
    assert [s["ticker"] for s in seen] == ["ABC"]   # jen validní, bez fallbacku

    # všechny neplatné → rc 2 (a opět žádný fallback na watchlistu)
    capsys.readouterr()
    rc = main(["scan", "1BAD!", "--db", str(tmp_path / "scan.db")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Žádné platné tickery" in err


# ------------------------------------------------------------------ fallback na watchlistu


def test_scan_falls_back_to_watchlist_db(monkeypatch, tmp_path):
    """Bez --file i argumentů → tickery z trvalé watchlisty v DB."""
    from dividend_tracker.data.db import Database

    db = Database(tmp_path / "scan.db")
    try:
        db.upsert_watchlist("KO")
        db.upsert_watchlist("ABC")
    finally:
        db.close()

    reports = {
        "KO": _scan_report("KO"),
        "ABC": _scan_report("ABC"),
    }
    seen = _mock_analyze(monkeypatch, reports)
    rc = main(["scan", "--db", str(tmp_path / "scan.db")])

    assert rc == 0
    assert {s["ticker"] for s in seen} == {"KO", "ABC"}


def test_scan_empty_source_error(monkeypatch, tmp_path, capsys):
    """Prázdná DB + žádné tickery → rc 2 a nápověda na stderr."""
    from dividend_tracker.data.db import Database

    db = Database(tmp_path / "empty.db")
    db.close()

    _mock_analyze(monkeypatch, {})
    rc = main(["scan", "--db", str(tmp_path / "empty.db")])

    assert rc == 2
    err = capsys.readouterr().err
    assert "Žádné tickery" in err


# ------------------------------------------------------------------ propas flagů


def test_scan_passes_refresh_and_no_benchmark(monkeypatch):
    """--refresh a --no-benchmark dorazí do analyze_ticker."""
    reports = {"KO": _scan_report("KO")}
    seen = _mock_analyze(monkeypatch, reports)

    rc = main(["scan", "ko", "--refresh", "--no-benchmark"])
    assert rc == 0
    assert seen[0]["kw"].get("refresh") is True
    assert seen[0]["kw"].get("use_benchmark") is False


def test_scan_default_flags(monkeypatch):
    """Bez flagů → refresh=False, use_benchmark=True (výchozí)."""
    reports = {"KO": _scan_report("KO")}
    seen = _mock_analyze(monkeypatch, reports)

    rc = main(["scan", "ko"])
    assert rc == 0
    assert seen[0]["kw"].get("refresh") is False
    assert seen[0]["kw"].get("use_benchmark") is True


# ------------------------------------------------------------------ odolnost vůči chybám


def test_scan_continues_after_fetch_error(monkeypatch, capsys):
    """DataFetchError u jednoho tickeru nezastaví zbytek; chyba se hlásí."""
    from dividend_tracker.data.fetcher import DataFetchError

    def fake_analyze(ticker, **kw):
        if ticker.upper() == "BAD":
            raise DataFetchError("404")
        return _scan_report(ticker)

    from dividend_tracker import cli as cli_mod

    monkeypatch.setattr(cli_mod, "analyze_ticker", fake_analyze)
    rc = main(["scan", "bad", "ko"])

    assert rc == 0
    err = capsys.readouterr().err
    assert "Nedostupná data" in err


def test_scan_all_fail_returns_error(monkeypatch, capsys):
    """Všechny tickery selžou → rc 2 (výsledek je bezobsažný)."""
    from dividend_tracker.data.fetcher import DataFetchError

    def fake_analyze(ticker, **kw):
        raise DataFetchError("404")

    from dividend_tracker import cli as cli_mod

    monkeypatch.setattr(cli_mod, "analyze_ticker", fake_analyze)
    rc = main(["scan", "bad"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "Žádný ticker se nepodařilo analyzovat" in err
