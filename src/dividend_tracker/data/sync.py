"""Cache-first synchronizace dat do lokální databáze.

Pravidla (viz zadání):
1. **Před voláním externího API se vždy čte DB.** Fundamenty se stahují jen,
   pokud chybí nebo jsou starší než :data:`FUNDAMENTAL_TTL` (72 h); dividendová
   historie má vlastní delší TTL :data:`DIVIDEND_TTL` (7 dní).
2. **FMP** je primární zdroj fundamentů s denním limitem 250 volání — při
   vyčerpání se aktualizace zastaví a čte se pouze z DB (stale data jsou
   raději než vyhozený limit).
3. **Fallback** bez FMP klíče / při chybě: yfinance (info + dividendy), což je
   i zdroj aktuálních cen (:mod:`dividend_tracker.data.quotes`).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from .db import Database
from .fmp import FMPCacheClient, FMPError, FMPLimitExceeded

log = logging.getLogger(__name__)

#: Fundamentální data (profil, výkazy) — stahovat po 72 hodinách.
FUNDAMENTAL_TTL = 72 * 3600
#: Dividendová historie — stahovat po 7 dnech.
DIVIDEND_TTL = 7 * 86400


def _parse_date(value: Any) -> Optional[str]:
    """Normalizuje datum z providera (ISO string / epoch ms / datetime) na ISO ``YYYY-MM-DD``."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):  # epoch milisekundy (FMP)
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).date().isoformat()
        if isinstance(value, str):
            s = value.strip()[:10]
            date.fromisoformat(s)  # validace — hodí ValueError při šmeřdě
            return s
    except (ValueError, OSError, OverflowError):
        return None
    return None


def _to_amount(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f <= 0:  # NaN nebo neplatná platba
        return None
    return round(f, 6)


def _dividend_rows_from_fmp(ticker: str, hist: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalizuje FMP ``historical-dividend`` na řádky pro DB."""
    rows: List[Dict[str, Any]] = []
    for item in hist:
        ex_date = _parse_date(item.get("date") or item.get("exDate"))
        amount = _to_amount(item.get("amount"))
        if not ex_date or amount is None:
            continue
        rows.append(
            {
                "ticker": ticker,
                "ex_date": ex_date,
                "payment_date": _parse_date(item.get("paymentDate")),
                "record_date": _parse_date(item.get("recordDate") or item.get("periodOfRecord")),
                "dividend_amount": amount,
                "frequency": None,
            }
        )
    return rows


def _calendar_rows_from_fmp(ticker: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalizuje FMP ``ex-dividend-date`` (budoucí výplaty) na řádky pro DB."""
    today = date.today().isoformat()
    rows: List[Dict[str, Any]] = []
    for item in items:
        ex_date = _parse_date(item.get("exDividendDate") or item.get("date"))
        amount = _to_amount(item.get("amount"))
        if not ex_date or ex_date < today or amount is None:
            continue  # jen budoucí výplaty; minulé patří do historie
        rows.append(
            {
                "ticker": ticker,
                "ex_date": ex_date,
                "payment_date": _parse_date(item.get("payDividendDate") or item.get("paymentDate")),
                "record_date": _parse_date(item.get("recordDate")),
                "dividend_amount": amount,
            }
        )
    return rows


def sync_via_fmp(fmp: FMPCacheClient, db: Database, ticker: str) -> None:
    """Stáhne dividendovou historii + plánované výplaty v dávce (2 volání na ticker).

    Profil společnosti se stahuje jen při prvním syncu tickera (3. volání),
    aby se limit šetřil — viz zadání „stáhni najednou v dávce“.
    """
    tk = ticker.upper()
    hist_rows = _dividend_rows_from_fmp(tk, fmp.get_dividend_history(tk))
    cal_rows = _calendar_rows_from_fmp(tk, fmp.get_dividend_calendar(tk))

    stock = db.get_stock(tk)
    if stock is None:  # první sync — doplnit profil (1 volání navíc)
        prof = fmp.get_profile(tk) or {}
        # FMP „industry“ je v praxi sektorová kategorie („Consumer Cyclical“) —
        # mapujeme ji do sector, aby doménová vrstva (REIT detekce apod.) fungovala.
        db.upsert_stock(
            tk,
            name=prof.get("name"),
            currency=prof.get("currency") or "USD",
            exchange=prof.get("exchangeShortName") or prof.get("exchange"),
            country=prof.get("country"),
            sector=prof.get("industry"),
            industry=None,  # FMP v profilu nemá samostatný pododvětvový údaj
        )

    db.replace_dividends(hist_rows)
    db.replace_calendar(cal_rows)
    db.mark_dividend_sync(tk)


def sync_via_yfinance(db: Database, ticker: str) -> None:
    """Fallback bez FMP: yfinance info + dividendová historie (bez tvrdého limitu)."""
    import yfinance as yf

    tk = ticker.upper()
    t = yf.Ticker(tk)
    info = dict(t.info or {})
    if not info:
        raise DataFetchErrorFallback(f"{tk}: provider nevrátil žádné základní info.")

    div_series = None
    try:
        div_series = t.dividends
    except Exception as exc:  # noqa: BLE001 — bez historie pokračujeme jen s profilem
        log.warning("%s: dividendová historie nedostupná (%s)", tk, exc)

    rows: List[Dict[str, Any]] = []
    if div_series is not None and len(div_series):
        for ts, v in div_series.items():
            amount = _to_amount(v)
            if amount is None:
                continue
            ex_date = (ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])).isoformat()
            rows.append(
                {
                    "ticker": tk,
                    "ex_date": ex_date,
                    "payment_date": None,  # yfinance neuvádí payment/record date
                    "record_date": None,
                    "dividend_amount": amount,
                    "frequency": None,
                }
            )

    db.upsert_stock(
        tk,
        name=info.get("longName") or info.get("shortName"),
        currency=info.get("currency") or "USD",
        exchange=info.get("fullExchangeName") or info.get("exchange"),
        country=info.get("country"),
        sector=info.get("sector"),
        industry=info.get("industry"),
    )
    db.replace_dividends(rows)
    db.mark_dividend_sync(tk)


class DataFetchErrorFallback(RuntimeError):
    """Nedostatek dat z fallback zdroje (yfinance)."""


def _is_fresh(ts: Optional[float], ttl: float, now: float) -> bool:
    return ts is not None and (now - ts) < ttl


def sync_fundamentals(
    db: Database, tickers: List[str], force: bool = False
) -> Dict[str, Any]:
    """Cache-first synchronizace fundamentů pro dávku tickerů.

    Vrátí souhrn::

        {"results": {ticker: "cached"|"fmp"|"yfinance"|"stale_cache"|"error"},
         "counts": {...}, "fmp_usage": {...}}
    """
    now = time.time()
    fmp = FMPCacheClient(db)
    results: Dict[str, str] = {}

    for tk in tickers:
        tk = tk.upper()
        stock = db.get_stock(tk)
        fund_fresh = not force and _is_fresh(
            (stock or {}).get("last_fundamental_update"), FUNDAMENTAL_TTL, now
        )
        div_fresh = not force and _is_fresh((stock or {}).get("last_dividend_sync"), DIVIDEND_TTL, now)

        if fund_fresh and div_fresh:
            results[tk] = "cached"  # cache-first: žádné externí volání
            continue

        try:
            if not fmp.configured:
                raise FMPError("FMP API klíč není nastaven (FMP_API_KEY).")
            sync_via_fmp(fmp, db, tk)
            results[tk] = "fmp"
        except FMPLimitExceeded as exc:
            log.warning("FMP limit vyčerpán — %s zůstává v cache (%s)", tk, exc)
            results[tk] = "stale_cache" if stock else "error"
        except FMPError as exc:
            log.warning("FMP selhalo pro %s (%s), fallback na yfinance", tk, exc)
            try:
                sync_via_yfinance(db, tk)
                results[tk] = "yfinance"
            except Exception as exc2:  # noqa: BLE001 — provider vrhá různé typy
                log.error("fallback yfinance selhal pro %s: %s", tk, exc2)
                results[tk] = "stale_cache" if stock else "error"

    counts: Dict[str, int] = {}
    for status in results.values():
        counts[status] = counts.get(status, 0) + 1
    return {"results": results, "counts": counts, "fmp_usage": fmp.usage()}


def sync_all(db: Database, tickers: List[str], force: bool = False) -> Dict[str, Any]:
    """Kompletní synchronizace: fundamenty (cache-first) + aktuální ceny do ``quotes``.

    Všechny tickery se zároveň automaticky přidají do **trvalé watchlisty** a ta se
    zálohuje do souboru, aby seznam akcií nikdy nebyl ztracen. Po aktualizaci cen se
    vyhodnotí aktivní **upozornění** (max. 1× denně na pravidlo) — vyhodnocená se
    vrátí v ``summary["alerts_triggered"]`` a logují se, aby je mohl zobrazit i cron.
    """
    from .watchlist_store import auto_add_to_watchlist

    added = auto_add_to_watchlist(db, tickers)
    if added:
        log.info("watchlist rozšířena o %s ticker(ů)", added)

    fund = sync_fundamentals(db, tickers, force=force)

    from .quotes import sync_quotes  # lokální import — quotes táhne yfinance

    quote_stats: Dict[str, int] = {}
    try:
        quote_stats = sync_quotes(db, tickers)
    except Exception as exc:  # noqa: BLE001 — ceny jsou doplňkové, nesmí zabít fundamenty
        log.error("synchronizace cen selhala: %s", exc)

    from .alerts import evaluate_alerts  # lokální import — kontrola proti cenám v DB

    triggered: List[Dict[str, Any]] = []
    try:
        triggered = evaluate_alerts(db)
        for item in triggered:
            log.info("ALERT %s", item["message"])
    except Exception as exc:  # noqa: BLE001 — upozornění nesmí zabít sync
        log.error("vyhodnocení upozornění selhalo: %s", exc)

    return {**fund, "quotes": quote_stats, "alerts_triggered": triggered}


__all__ = [
    "FUNDAMENTAL_TTL",
    "DIVIDEND_TTL",
    "sync_fundamentals",
    "sync_all",
    "sync_via_fmp",
    "sync_via_yfinance",
]
