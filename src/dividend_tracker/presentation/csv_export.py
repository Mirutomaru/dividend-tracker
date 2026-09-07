"""CSV export — watchlist a analytické reporty do souboru.

Vše běží offline na standardní knihovně ``csv``:
- **watchlist** — ploché řádky z ``build_watchlist_rows()`` (všechny sledované
  atributy, včetně 52tídních statistik a čistého yieldu po zdanění)
- **analýza** — vybrané metriky z hloubkových reportů (skóre, férová cena,
  nákupní zóna, signály)

Soubory se zapisují s BOM (``utf-8-sig``), aby se správně otevřely v Excelu.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

# Sloupce watchlist CSV — pořadí = sloupce ve souboru.
WATCHLIST_COLUMNS: list[str] = [
    "ticker", "name", "currency", "exchange", "country", "sector",
    "price", "change", "volume",
    "dps_ttm", "yield_pct", "net_yield_pct", "payout_pct",
    "growth_5y_pct", "streak_years",
    "next_ex_date", "next_dividend",
    "low_52w", "high_52w", "drawdown_from_high_pct", "percentile_52w",
]

# Sloupce analytického CSV — (názov, cesta do reportu).
ANALYSIS_COLUMNS: list[tuple[str, tuple[str, ...]]] = [
    ("ticker", ("ticker",)),
    ("name", ("company", "name")),
    ("sector", ("company", "sector")),
    ("currency", ("currency",)),
    ("price_current", ("price_current",)),
    ("yield_trailing_pct", ("metrics", "dividend", "yield_trailing_pct")),
    ("dps_ttm", ("metrics", "dividend", "dps_ttm")),
    ("payout_ratio_eps_pct", ("metrics", "dividend", "payout_ratio_eps_pct")),
    ("growth_cagr_5y_pct", ("metrics", "dividend", "growth_cagr_5y_pct")),
    ("dividend_streak_years", ("metrics", "dividend", "dividend_streak_years")),
    ("pe_ttm", ("metrics", "valuation", "pe_ttm")),
    ("fcf_yield_pct", ("metrics", "valuation", "fcf_yield_pct")),
    ("net_debt_to_ebitda", ("metrics", "health", "net_debt_to_ebitda")),
    ("score_total_0_100", ("score", "total_0_100")),
    ("safety_flag", ("score", "safety_flag")),
    ("signals", ("score", "signals_display")),
    ("fair_value", ("fair_value", "fair_value")),
    ("buy_zone_low", ("fair_value", "buy_zone_low")),
    ("buy_zone_high", ("fair_value", "buy_zone_high")),
    ("verdict", ("fair_value", "verdict")),
]


def _dig(report: dict[str, Any], path: tuple[str, ...]) -> Any:
    """Vydoluje hodnotu podle cesty; chybějící úroveň → ``None``."""
    cur: Any = report
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


def _fmt(value: Any) -> str:
    """Formátuje hodnotu pro CSV (None → prázdné pole, list → „; “)."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    if isinstance(value, float):
        # odstraň zbytečná desetinná místa (6.0 → 6), ale zachovej reálné
        return f"{value:.10g}"
    return str(value)


def _csv_text(header: list[str], rows: list[list[Any]]) -> str:
    """CSV jako text (pro web endpoint — bez souboru na disku)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def watchlist_csv_text(rows: list[dict[str, Any]]) -> str:
    """Watchlist řádky jako CSV text."""
    data = [[_fmt(row.get(col)) for col in WATCHLIST_COLUMNS] for row in rows]
    return _csv_text(WATCHLIST_COLUMNS, data)


def analysis_csv_text(reports: list[dict[str, Any]]) -> str:
    """Analytické reporty jako CSV text (vybrané metriky)."""
    header = [name for name, _ in ANALYSIS_COLUMNS]
    data = [[_fmt(_dig(rep, path_)) for _, path_ in ANALYSIS_COLUMNS] for rep in reports]
    return _csv_text(header, data)


def export_watchlist_csv(
    rows: list[dict[str, Any]], path: str | Path
) -> Path:
    """Exportuje watchlist řádky do CSV. Vrátí absolutní cestu."""
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(watchlist_csv_text(rows), encoding="utf-8-sig")
    return p


def export_analysis_csv(
    reports: list[dict[str, Any]], path: str | Path
) -> Path:
    """Exportuje analytické reporty do CSV (vybrané metriky). Vrátí cestu."""
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(analysis_csv_text(reports), encoding="utf-8-sig")
    return p


__all__ = [
    "ANALYSIS_COLUMNS",
    "WATCHLIST_COLUMNS",
    "analysis_csv_text",
    "export_analysis_csv",
    "export_watchlist_csv",
    "watchlist_csv_text",
]
