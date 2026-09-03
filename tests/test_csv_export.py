"""Offline testy CSV exportu — watchlist i analytické reporty."""

from __future__ import annotations

import csv
import io

from dividend_tracker.presentation.csv_export import (
    ANALYSIS_COLUMNS,
    WATCHLIST_COLUMNS,
    analysis_csv_text,
    export_analysis_csv,
    export_watchlist_csv,
    watchlist_csv_text,
)


def _sample_report(ticker: str = "KO") -> dict:
    """Minimalizovaný report ve tvaru build_report() (jen relevantní cesty)."""
    return {
        "schema_version": "1.0",
        "ticker": ticker,
        "company": {"name": "Coca-Cola", "sector": "Consumer Defensive"},
        "currency": "USD",
        "price_current": 62.5,
        "metrics": {
            "dividend": {
                "yield_trailing_pct": 3.1,
                "dps_ttm": 1.94,
                "payout_ratio_eps_pct": 70.0,
                "growth_cagr_5y_pct": 6.2,
                "dividend_streak_years": 62,
            },
            "valuation": {"pe_ttm": 24.1, "fcf_yield_pct": 3.8},
            "health": {"net_debt_to_ebitda": -0.5},
        },
        "score": {
            "total_0_100": 78.0,
            "safety_flag": "safe",
            "signals_display": ["Možné podhodnocení / nákupní zóna"],
        },
        "fair_value": {
            "fair_value": 68.0,
            "buy_zone_low": 57.8,
            "buy_zone_high": 68.0,
            "verdict": "undervalued",
        },
    }


def _parse(text: str) -> list[list[str]]:
    return [row for row in csv.reader(io.StringIO(text)) if row]


# ------------------------------------------------------------------ watchlist

def test_watchlist_csv_header_and_rows():
    rows = [
        {"ticker": "KO", "name": "Coca-Cola", "price": 62.5, "yield_pct": 3.1,
         "net_yield_pct": 2.64, "country": "US"},
        {"ticker": "JNJ", "name": "Johnson & Johnson", "price": 150.0},
    ]
    text = watchlist_csv_text(rows)
    parsed = _parse(text)
    assert parsed[0] == WATCHLIST_COLUMNS
    # KO — kompletní řádek
    ko = dict(zip(WATCHLIST_COLUMNS, parsed[1]))
    assert ko["ticker"] == "KO" and ko["price"] == "62.5"
    assert ko["net_yield_pct"] == "2.64" and ko["country"] == "US"
    # JNJ — chybějící atributy → prázdná pole (ne "None")
    jnj = dict(zip(WATCHLIST_COLUMNS, parsed[2]))
    assert jnj["yield_pct"] == "" and jnj["net_yield_pct"] == ""


def test_watchlist_csv_empty_rows():
    text = watchlist_csv_text([])
    parsed = _parse(text)
    assert len(parsed) == 1 and parsed[0] == WATCHLIST_COLUMNS  # jen hlavička


def test_export_watchlist_file_bom(tmp_path):
    p = export_watchlist_csv([{"ticker": "KO", "price": 62.5}], tmp_path / "out" / "wl.csv")
    assert p.exists() and p.name == "wl.csv"
    raw = p.read_bytes()
    # BOM pro Excel (utf-8-sig)
    assert raw.startswith(b"\xef\xbb\xbf")
    text = p.read_text(encoding="utf-8-sig")
    parsed = _parse(text)
    assert dict(zip(WATCHLIST_COLUMNS, parsed[1]))["ticker"] == "KO"


# ------------------------------------------------------------------ analýza

def test_analysis_csv_header():
    header = [name for name, _ in ANALYSIS_COLUMNS]
    text = analysis_csv_text([_sample_report()])
    parsed = _parse(text)
    assert parsed[0] == header


def test_analysis_csv_values_dug_from_nested_paths():
    text = analysis_csv_text([_sample_report("KO")])
    parsed = _parse(text)
    row = dict(zip(parsed[0], parsed[1]))
    assert row["ticker"] == "KO"
    assert row["name"] == "Coca-Cola"          # company.name
    assert row["sector"] == "Consumer Defensive"  # company.sector
    assert row["price_current"] == "62.5"
    assert row["yield_trailing_pct"] == "3.1"   # metrics.dividend.*
    assert row["pe_ttm"] == "24.1"              # metrics.valuation.*
    assert row["net_debt_to_ebitda"] == "-0.5"  # metrics.health.*
    assert row["score_total_0_100"] == "78"     # float bez zbytečných desetin
    assert row["safety_flag"] == "safe"
    assert row["signals"] == "Možné podhodnocení / nákupní zóna"  # list → "; "
    assert row["fair_value"] == "68"            # fair_value.fair_value
    assert row["buy_zone_low"] == "57.8"
    assert row["verdict"] == "undervalued"


def test_analysis_csv_missing_paths_empty():
    """Chybějící úrovně reportu → prázdná pole, ne výjimka."""
    text = analysis_csv_text([{"ticker": "XYZ"}])
    parsed = _parse(text)
    row = dict(zip(parsed[0], parsed[1]))
    assert row["ticker"] == "XYZ"
    assert row["name"] == "" and row["fair_value"] == "" and row["score_total_0_100"] == ""


def test_export_analysis_file(tmp_path):
    p = export_analysis_csv([_sample_report("KO"), _sample_report("JNJ")], tmp_path / "a.csv")
    assert p.exists()
    parsed = _parse(p.read_text(encoding="utf-8-sig"))
    assert len(parsed) == 3  # hlavička + 2 reporty
