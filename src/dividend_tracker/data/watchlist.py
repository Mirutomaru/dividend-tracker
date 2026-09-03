"""Srovnávací řádky pro watchlist — počítají se PŘÍMO Z LOKÁLNÍ DATABÁZE.

Žádné externí volání: všechny metriky (yield, CAGR, streak, příští výplata)
vycházejí z tabulek ``stocks``, ``quotes`` a ``dividends_history/calendar``,
aby srovnávací tabulka běhala okamžitě a bez ohledu na rate-limity.

Výpočet CAGR/streak reuses doménovou logiku (:mod:`..domain.dividends`), aby se
semantika shodovala s analytickou cestou.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .db import Database

log = logging.getLogger(__name__)


def _build_series(rows: List[Dict[str, Any]]):
    """Sestaví ``pd.Series`` (DatetimeIndex → amount) z řádků dividends_history."""
    import pandas as pd

    if not rows:
        return None
    idx = []
    vals = []
    for r in rows:
        try:
            ts = pd.Timestamp(r["ex_date"])
        except (ValueError, KeyError):
            continue
        amt = r.get("dividend_amount")
        if amt is None or float(amt) <= 0:
            continue
        idx.append(ts)
        vals.append(float(amt))
    if not idx:
        return None
    s = pd.Series(vals, index=pd.DatetimeIndex(idx)).sort_index()
    # deduplikace na datum (pojistka proti duplicitním ex_date)
    return s[~s.index.duplicated(keep="last")]


def _ttm(series) -> float:
    """TTM DPS — součet plateb v posledních 365 dnech od poslední výplaty."""
    if series is None or len(series) == 0:
        return 0.0
    ref = series.index.max()
    start = ref - timedelta(days=365)
    window = series[(series.index >= start) & (series.index <= ref)]
    return round(float(window.sum()), 4)


def _next_ex_date(db: Database, ticker: str) -> Optional[Dict[str, Any]]:
    """Nejbližší budoucí výplata z kalendáře (ex_date > dnes)."""
    today = date.today().isoformat()
    for row in db.get_calendar(ticker):  # seřazeno ex_date ASC
        if row["ex_date"] >= today:
            return {"ex_date": row["ex_date"], "amount": row.get("dividend_amount")}
    return None


def _payout_pct(stock: Dict[str, Any], dps_ttm: float) -> Optional[float]:
    """Payout ratio z uloženého fundamentálního snapshotu (DPS/EPS), pokud je k dispozici."""
    raw = stock.get("fundamentals_json")
    if not raw or dps_ttm <= 0:
        return None
    try:
        snap = json.loads(raw)
        eps = float(snap.get("eps_ttm") or 0)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if eps <= 0:
        return None
    return round(dps_ttm / eps * 100.0, 1)


def build_watchlist_rows(db: Database) -> List[Dict[str, Any]]:
    """Vrátí srovnávací řádky pro všechny tickery v DB (abecedně).

    Každý řádek obsahuje sledované atributy — frontend je může seřadit podle
    libovolného z nich vzestupně/sestupně. Chybějící hodnoty jsou ``None``
    (frontend je vykreslí jako „—“ a při řazení zařadí na konec).
    """
    from ..domain.dividends import aggregate_dividends, cagr, dividend_streak, full_years

    rows: List[Dict[str, Any]] = []
    for stock in db.list_stocks():  # ORDER BY ticker → abecedně
        tk = stock["ticker"]
        quote = db.get_quote(tk) or {}
        price = quote.get("current_price")

        series = _build_series(db.get_dividends(tk))
        annual, counts, dps_ttm_db = aggregate_dividends(series)
        ttm = _ttm(series)  # konzistentní TTM okno (365 dní od poslední výplaty)

        yield_pct: Optional[float] = None
        if price and price > 0 and ttm > 0:
            yield_pct = round(ttm / price * 100.0, 2)

        annual_full = full_years(annual, counts)
        growth_5y = cagr(annual_full, 5)
        streak = dividend_streak(annual_full)

        nxt = _next_ex_date(db, tk)

        # 52tídní statistika z denních snapshotů (price_history) — None, pokud
        # v DB ještě není žádný bod (backfill / první dny provozu).
        stats_52w = db.price_stats_52w(tk) or {}

        rows.append(
            {
                "ticker": tk,
                "name": stock.get("name"),
                "currency": stock.get("currency") or "USD",
                "exchange": stock.get("exchange"),
                "sector": stock.get("sector"),
                "price": price,
                "change": quote.get("change"),
                "volume": quote.get("volume"),
                "quote_age_s": (time.time() - quote["timestamp"]) if quote.get("timestamp") else None,
                "dps_ttm": ttm or dps_ttm_db or 0.0,
                "yield_pct": yield_pct,
                "payout_pct": _payout_pct(stock, ttm),
                "growth_5y_pct": growth_5y,
                "streak_years": streak,
                "next_ex_date": nxt["ex_date"] if nxt else None,
                "next_dividend": nxt["amount"] if nxt else None,
                # 52tídní kontext pro rozhodování „když poklesne cena“:
                "low_52w": stats_52w.get("low_52w"),
                "high_52w": stats_52w.get("high_52w"),
                "drawdown_from_high_pct": stats_52w.get("drawdown_from_high_pct"),
                "percentile_52w": stats_52w.get("percentile_52w"),
            }
        )
    return rows


__all__ = ["build_watchlist_rows", "_ttm", "_next_ex_date"]
