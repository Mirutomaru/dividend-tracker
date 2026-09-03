"""Aktuální tržní ceny — oddělený zdroj (NE FMP), ukládaný do tabulky ``quotes``.

Zdroj: yfinance (Yahoo Finance) bez tvrdého denního limitu, vhodné pro častý refresh.
Ceny se stahují v dávce (jedno batch volání pro všechny tickery) a okamžitě zapisují
do DB, aby srovnávací tabulka mohla běžet čistě z lokálních dat.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .db import Database

log = logging.getLogger(__name__)


def fetch_quotes(tickers: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
    """Stáhne aktuální ceny pro dávku tickerů (jedno batch volání yfinance).

    Vrátí ``{ticker: {"price": float|None, "change": float|None, "volume": int|None}}``.
    Chyba u jednoho tickera nezahodí celou dávku — chybějící se prostě nevrátí.
    """
    import yfinance as yf

    out: Dict[str, Dict[str, Optional[float]]] = {}
    if not tickers:
        return out

    try:
        data = yf.download(
            tickers, period="5d", interval="1d", group_by="ticker", progress=False, auto_adjust=False
        )
    except Exception as exc:  # noqa: BLE001 — provider vrhá různé typy (timeout, 429)
        log.warning("batch download cen selhal (%s), zkusím po jednotlivých tickerech", exc)
        data = None

    if data is not None and not data.empty:
        for tk in tickers:
            try:
                sub = data[tk].dropna(how="all") if tk in getattr(data, "columns", []) else None
                if sub is None or sub.empty or "Close" not in sub.columns:
                    continue
                closes = sub["Close"].dropna()
                if closes.empty:
                    continue
                price = float(closes.iloc[-1])
                change = float(closes.iloc[-1] - closes.iloc[-2]) if len(closes) >= 2 else None
                vol = sub["Volume"].dropna()
                volume = int(vol.iloc[-1]) if not vol.empty and vol.iloc[-1] > 0 else None
                out[tk] = {"price": price, "change": change, "volume": volume}
            except (KeyError, IndexError, TypeError) as exc:
                log.warning("cena pro %s se nepodařilo vyčíst z batch dat: %s", tk, exc)

    # Fallback po jednotlivých tickerech pro ty, co v batchu chybí.
    for tk in tickers:
        if tk in out:
            continue
        try:
            fi = yf.Ticker(tk).fast_info
            price = float(fi["last_price"]) if fi.get("last_price") else None
            prev = fi.get("previous_close")
            change = float(price - prev) if (price is not None and prev) else None
            volume = int(fi["last_volume"]) if fi.get("last_volume") else None
            if price is not None:
                out[tk] = {"price": price, "change": change, "volume": volume}
        except Exception as exc:  # noqa: BLE001
            log.warning("cena pro %s nedostupná ani přes fast_info: %s", tk, exc)

    return out


def sync_quotes(db: Database, tickers: List[str]) -> Dict[str, int]:
    """Aktualizuje tabulku ``quotes`` a vrátí souhrn (updated / failed)."""
    quotes = fetch_quotes(tickers)
    updated = 0
    for tk in tickers:
        q = quotes.get(tk)
        if q is None or q["price"] is None:
            continue
        db.upsert_quote(
            tk, q["price"], change=q["change"], volume=int(q["volume"]) if q["volume"] else None
        )
        updated += 1
    return {"updated": updated, "failed": len(tickers) - updated}


__all__ = ["fetch_quotes", "sync_quotes"]
