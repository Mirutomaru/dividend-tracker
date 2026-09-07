"""Aktuální tržní ceny a historie cen — primárně **Tiingo**, fallback yfinance.

Ceny se ukládají do tabulky ``quotes`` (aktuální stav) a ``price_history``
(denní snapshoty pro 52tídní statistiky). Zdrojová strategie:

- **Tiingo** (``TIINGO_API_KEY``, limit 10 000 volání/den) — primární zdroj.
  Jedno volání na ticker; denní čítač se eviduje v ``api_usage`` a při
  vyčerpání se zbytek dávky přepne na yfinance.
- **yfinance** (Yahoo Finance, měkký limit ~2000 req/hod) — fallback pro
  tickery, které Tiingo nedodal (chyba, neznámý symbol, vyčerpaný limit).

Bez ``db`` (či bez nakonfigurovaného Tiingo) se chová jako dříve: čistě
yfinance batch. Volání Tiingo nikdy nezahodí celou dávku — chyba u jednoho
tickera jen přepne tenhle ticker na fallback.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from .db import Database
from .tiingo import TiingoCacheClient, TiingoError, recent_window

log = logging.getLogger(__name__)


def _quotes_from_tiingo(client: TiingoCacheClient, tickers: list[str]) -> dict[str, dict[str, float | None]]:
    """Aktuální ceny z posledních ~10 dní (1 volání na ticker)."""
    start, end = recent_window(days=10)
    out: dict[str, dict[str, float | None]] = {}
    for tk in tickers:
        try:
            bars = client.get_price_history(tk.strip().upper(), start_date=start, end_date=end)
        except TiingoError as exc:
            log.warning("Tiingo cena pro %s nedostupná (%s), zkusím yfinance", tk, exc)
            continue
        closes = [b for b in bars if isinstance(b.get("close"), (int, float))]
        if not closes:
            continue
        last = closes[-1]
        price = float(last["close"])
        change = float(last["close"] - closes[-2]["close"]) if len(closes) >= 2 else None
        volume = int(last["volume"]) if isinstance(last.get("volume"), (int, float)) and last["volume"] > 0 else None
        out[tk] = {"price": price, "change": change, "volume": volume}
    return out


def _history_from_tiingo(client: TiingoCacheClient, tickers: list[str], days: int) -> dict[str, list[tuple]]:
    """Denní uzavírací ceny za posledních ``days`` dní (1 volání na ticker).

    Používá ``adjClose`` (upraveno o dividenda/splity), aby odpovídalo
    yfinance ``auto_adjust=True``; chybí-li, použije se neupravené ``close``.
    """
    end = date.today()
    start = end - timedelta(days=days)
    out: dict[str, list[tuple]] = {}
    for tk in tickers:
        try:
            bars = client.get_price_history(
                tk.strip().upper(), start_date=start.isoformat(), end_date=end.isoformat()
            )
        except TiingoError as exc:
            log.warning("Tiingo historie pro %s nedostupná (%s), zkusím yfinance", tk, exc)
            continue
        points: list[tuple] = []
        for b in bars:
            d = str(b.get("date", ""))[:10]
            adj = b.get("adjClose")
            close = adj if isinstance(adj, (int, float)) else b.get("close")
            if not d or not isinstance(close, (int, float)):
                continue
            points.append((d, float(close)))
        if points:
            out[tk] = points
    return out


def fetch_quotes(
    tickers: list[str], db: Database | None = None
) -> dict[str, dict[str, float | None]]:
    """Stáhne aktuální ceny pro dávku tickerů.

    Vrátí ``{ticker: {"price": float|None, "change": float|None, "volume": int|None}}``.
    S ``db`` a nakonfigurovaným Tiingo jde primárně přes Tiingo; chybějící
    tickery doplní yfinance (batch + fast_info). Chyba u jednoho tickera
    nezahodí celou dávku — chybějící se prostě nevrátí.
    """
    out: dict[str, dict[str, float | None]] = {}
    if not tickers:
        return out

    client = TiingoCacheClient(db) if db is not None else None
    if client is not None and client.configured:
        out.update(_quotes_from_tiingo(client, tickers))

    missing = [tk for tk in tickers if tk not in out]
    if missing:
        out.update(_fetch_quotes_yfinance(missing))
    return out


def _fetch_quotes_yfinance(tickers: list[str]) -> dict[str, dict[str, float | None]]:
    """Původní cesta přes yfinance (batch download + fast_info fallback)."""
    import yfinance as yf

    out: dict[str, dict[str, float | None]] = {}
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
            last_price = float(fi["last_price"]) if fi.get("last_price") else None
            prev = fi.get("previous_close")
            change = float(last_price - prev) if (last_price is not None and prev) else None
            volume = int(fi["last_volume"]) if fi.get("last_volume") else None
            if last_price is not None:
                out[tk] = {"price": last_price, "change": change, "volume": volume}
        except Exception as exc:  # noqa: BLE001
            log.warning("cena pro %s nedostupná ani přes fast_info: %s", tk, exc)

    return out


def sync_quotes(db: Database, tickers: list[str]) -> dict[str, int]:
    """Aktualizuje tabulku ``quotes`` a zároveň zapíše **denní snapshot** do ``price_history``.

    Snapshot je idempotentní (ON CONFLICT na ticker+date) — opakovaný sync ve stejný
    den jen aktualizuje hodnotu. Z historie se počítá 52tídní rozsah, drawdown a
    percentil ceny (viz :meth:`Database.price_stats_52w`).
    """
    quotes = fetch_quotes(tickers, db=db)
    updated = 0
    today = date.today().isoformat()
    for tk in tickers:
        q = quotes.get(tk)
        if q is None or q["price"] is None:
            continue
        db.upsert_quote(
            tk, q["price"], change=q["change"], volume=int(q["volume"]) if q["volume"] else None
        )
        db.record_price_snapshot(tk, today, q["price"])
        updated += 1
    return {"updated": updated, "failed": len(tickers) - updated}


def fetch_price_history(
    tickers: list[str], days: int = 365, db: Database | None = None
) -> dict[str, list[tuple]]:
    """Stáhne denní uzavírací ceny za posledních ``days`` dní.

    Vrátí ``{ticker: [(date_iso, close), ...]}`` — nejstarší první. S ``db`` a
    nakonfigurovaným Tiingo jde primárně přes Tiingo (1 volání na ticker);
    chybějící tickery doplní yfinance batch. Chyba u jednoho nezahodí dávku.
    """
    out: dict[str, list[tuple]] = {}
    if not tickers:
        return out

    client = TiingoCacheClient(db) if db is not None else None
    if client is not None and client.configured:
        out.update(_history_from_tiingo(client, tickers, days))

    missing = [tk for tk in tickers if tk not in out]
    if missing:
        out.update(_fetch_price_history_yfinance(missing, days=days))
    return out


def _fetch_price_history_yfinance(tickers: list[str], days: int) -> dict[str, list[tuple]]:
    """Původní cesta přes yfinance (batch download + jednotlivý fallback)."""
    import yfinance as yf

    out: dict[str, list[tuple]] = {}
    if not tickers:
        return out

    period = "1y" if days <= 365 else f"{max(2, days // 365)}y"
    try:
        data = yf.download(
            tickers, period=period, interval="1d", group_by="ticker", progress=False, auto_adjust=True
        )
    except Exception as exc:  # noqa: BLE001 — provider vrhá různé typy (timeout, 429)
        log.warning("batch download historie cen selhal (%s), zkusím po jednotlivých tickerech", exc)
        data = None

    if data is not None and not data.empty:
        for tk in tickers:
            try:
                sub = data[tk].dropna(how="all") if tk in getattr(data, "columns", []) else None
                if sub is None or sub.empty or "Close" not in sub.columns:
                    continue
                closes = sub["Close"].dropna()
                out[tk] = [(ts.date().isoformat(), float(v)) for ts, v in closes.items()]
            except (KeyError, IndexError, TypeError) as exc:
                log.warning("historie cen pro %s se nepodařilo vyčíst z batch dat: %s", tk, exc)

    # Fallback po jednotlivých tickerech.
    for tk in tickers:
        if tk in out:
            continue
        try:
            hist = yf.Ticker(tk).history(period=period, interval="1d", auto_adjust=True)
            if hist is not None and len(hist):
                closes = hist["Close"].dropna()
                out[tk] = [(ts.date().isoformat(), float(v)) for ts, v in closes.items()]
        except Exception as exc:  # noqa: BLE001
            log.warning("historie cen pro %s nedostupná ani jednotlivě: %s", tk, exc)

    return out


def backfill_price_history(db: Database, tickers: list[str], days: int = 365) -> dict[str, int]:
    """Doplní historii cen do ``price_history`` (pro 52tídní statistiky).

    Používá se při prvním spuštění / po dlouhé pauze — bez backfillu by percentil
    a drawdown vyžadovaly rok denních snapshotů. Vrátí počet zapísaných bodů.
    """
    hist = fetch_price_history(tickers, days=days, db=db)
    total = 0
    for tk in tickers:
        points = hist.get(tk) or []
        for date_iso, close in points:
            db.record_price_snapshot(tk, date_iso, close)
        if points:
            total += len(points)
    return {"points": total, "tickers_with_data": len(hist)}


__all__ = ["fetch_quotes", "sync_quotes", "fetch_price_history", "backfill_price_history"]
