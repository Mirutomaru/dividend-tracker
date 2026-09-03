"""Lokální SQLite databáze — cache-first úložiště akciových a dividendových dat.

Tabulky (viz zadání):
- ``stocks``             — profil společnosti + čas posledního fundamentálního update
- ``dividends_history``  — vyplacené dividendy (ex_date, payment_date, record_date, amount, frequency)
- ``dividends_calendar`` — plánované budoucí výplaty
- ``quotes``             — aktuální ceny z odděleného zdroje (NE FMP), s časovým razítkem
- ``api_usage``          — čítače denních volání externích API + expirace limitu

Všechna data se ukládají okamžitě po stažení, aby se fundamenty nikdy neopakovaly
při každém dotazu. Přístup je přes :class:`Database` (thread-safe, WAL režim).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    ticker                  TEXT PRIMARY KEY,
    name                    TEXT,
    currency                TEXT DEFAULT 'USD',
    exchange                TEXT,
    sector                  TEXT,
    industry                TEXT,
    last_fundamental_update REAL,          -- epoch sekund; NULL = nikdy
    last_dividend_sync      REAL,          -- epoch sekund posledního syncu dividendové historie
    fundamentals_json       TEXT           -- normalizovaný Snapshot (JSON) pro rychlé čtení
);

CREATE TABLE IF NOT EXISTS dividends_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    ex_date         TEXT NOT NULL,          -- ISO YYYY-MM-DD
    payment_date    TEXT,
    record_date     TEXT,
    dividend_amount REAL NOT NULL,
    frequency       TEXT,                   -- monthly/quarterly/semi-annual/annual
    UNIQUE (ticker, ex_date)
);

CREATE TABLE IF NOT EXISTS dividends_calendar (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    ex_date         TEXT NOT NULL,
    payment_date    TEXT,
    record_date     TEXT,
    dividend_amount REAL,
    frequency       TEXT,
    UNIQUE (ticker, ex_date)
);

CREATE TABLE IF NOT EXISTS quotes (
    ticker        TEXT PRIMARY KEY,
    current_price REAL,
    change        REAL,                     -- absolutní změna od předchozího uzavření
    volume        INTEGER,
    timestamp     REAL                      -- epoch sekund sběru
);

CREATE TABLE IF NOT EXISTS api_usage (
    provider      TEXT PRIMARY KEY,         -- např. 'fmp'
    day           TEXT NOT NULL,            -- YYYY-MM-DD (UTC)
    calls_used    INTEGER DEFAULT 0,
    daily_limit   INTEGER,                  -- např. 250 pro FMP; NULL = neomezeno
    expires_at    REAL                      -- epoch sekund expirace denního limitu
);

CREATE INDEX IF NOT EXISTS idx_div_hist_ticker ON dividends_history (ticker, ex_date DESC);
CREATE INDEX IF NOT EXISTS idx_div_cal_ticker  ON dividends_calendar (ticker, ex_date ASC);
"""


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


#: Výchozí cesta lokální databáze — sdílená mezi CLI (``divtrack sync``),
#: web GUI a nočním cronem. Překryje se environment proměnnou ``DIVTRACK_DB``.
DEFAULT_DB_PATH = Path(os.environ.get("DIVTRACK_DB", "~/.cache/dividend-tracker/data.db")).expanduser()


class Database:
    """Lehká obálka nad SQLite s thread-safe přístupem a WAL režimem."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path.parent and not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # Schéma vytvoříme hned (idempotentní).
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Vrátí spojení vázané na aktuální vlákno (SQLite nesdílí conn napříč vlákny)."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self.path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.conn = conn
        try:
            yield self._local.conn
            self._local.conn.commit()
        except Exception:
            self._local.conn.rollback()
            raise

    # ------------------------------------------------------------------ stocks

    def upsert_stock(
        self,
        ticker: str,
        name: Optional[str] = None,
        currency: Optional[str] = None,
        exchange: Optional[str] = None,
        sector: Optional[str] = None,
        industry: Optional[str] = None,
        fundamentals_json: Optional[str] = None,
        mark_fundamental_update: bool = True,
    ) -> None:
        with self._conn() as conn:
            ts = time.time() if mark_fundamental_update else None
            row = conn.execute(
                "SELECT * FROM stocks WHERE ticker=?", (ticker,)
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO stocks
                       (ticker, name, currency, exchange, sector, industry,
                        last_fundamental_update, fundamentals_json)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (ticker, name, currency or "USD", exchange, sector, industry, ts, fundamentals_json),
                )
            else:
                # Neexistující hodnoty nenecháme přepsat NULL — zachováme staré.
                conn.execute(
                    """UPDATE stocks SET
                       name = COALESCE(?, name),
                       currency = COALESCE(?, currency),
                       exchange = COALESCE(?, exchange),
                       sector = COALESCE(?, sector),
                       industry = COALESCE(?, industry),
                       last_fundamental_update = COALESCE(?, last_fundamental_update),
                       fundamentals_json = COALESCE(?, fundamentals_json)
                       WHERE ticker=?""",
                    (name, currency, exchange, sector, industry, ts, fundamentals_json, ticker),
                )

    def mark_dividend_sync(self, ticker: str) -> None:
        """Označí čas posledního syncu dividendové historie pro daný ticker."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE stocks SET last_dividend_sync=? WHERE ticker=?", (time.time(), ticker)
            )

    def get_stock(self, ticker: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM stocks WHERE ticker=?", (ticker,)).fetchone()
            return dict(row) if row else None

    def list_stocks(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM stocks ORDER BY ticker").fetchall()
            return [dict(r) for r in rows]

    # -------------------------------------------------------- dividends_history

    def upsert_dividend(
        self,
        ticker: str,
        ex_date: str,
        dividend_amount: float,
        payment_date: Optional[str] = None,
        record_date: Optional[str] = None,
        frequency: Optional[str] = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO dividends_history
                   (ticker, ex_date, payment_date, record_date, dividend_amount, frequency)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(ticker, ex_date) DO UPDATE SET
                     payment_date = excluded.payment_date,
                     record_date  = excluded.record_date,
                     dividend_amount = excluded.dividend_amount,
                     frequency    = COALESCE(excluded.frequency, dividends_history.frequency)""",
                (ticker, ex_date, payment_date, record_date, dividend_amount, frequency),
            )

    def get_dividends(self, ticker: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM dividends_history WHERE ticker=? ORDER BY ex_date DESC", (ticker,)
            ).fetchall()
            return [dict(r) for r in rows]

    def replace_dividends(self, rows: List[Dict[str, Any]]) -> int:
        """Nahradí kompletní dividendovou historii pro všechny tickery v ``rows``.

        Používá se při batch syncu (FMP vrátí celou historii najednou) — staré
        záznamy daných tickerů se smažou a vloží se nové. Volitelná pole
        (payment_date, record_date, frequency) mohou v řádcích chybět.
        Vrátí počet vložených řádků.
        """
        if not rows:
            return 0
        tickers = sorted({r["ticker"] for r in rows})
        normalized = [
            {
                "ticker": r["ticker"],
                "ex_date": r["ex_date"],
                "payment_date": r.get("payment_date"),
                "record_date": r.get("record_date"),
                "dividend_amount": r["dividend_amount"],
                "frequency": r.get("frequency"),
            }
            for r in rows
        ]
        with self._conn() as conn:
            placeholders = ",".join("?" * len(tickers))
            conn.execute(
                f"DELETE FROM dividends_history WHERE ticker IN ({placeholders})", tickers
            )
            conn.executemany(
                """INSERT INTO dividends_history
                   (ticker, ex_date, payment_date, record_date, dividend_amount, frequency)
                   VALUES (:ticker, :ex_date, :payment_date, :record_date, :dividend_amount, :frequency)""",
                normalized,
            )
        return len(normalized)

    def replace_calendar(self, rows: List[Dict[str, Any]]) -> int:
        """Nahradí plánované výplaty (budoucí ex-dates) pro tickery v ``rows``."""
        if not rows:
            return 0
        tickers = sorted({r["ticker"] for r in rows})
        normalized = [
            {
                "ticker": r["ticker"],
                "ex_date": r["ex_date"],
                "payment_date": r.get("payment_date"),
                "record_date": r.get("record_date"),
                "dividend_amount": r.get("dividend_amount"),
                "frequency": r.get("frequency"),
            }
            for r in rows
        ]
        with self._conn() as conn:
            placeholders = ",".join("?" * len(tickers))
            conn.execute(
                f"DELETE FROM dividends_calendar WHERE ticker IN ({placeholders})", tickers
            )
            conn.executemany(
                """INSERT INTO dividends_calendar
                   (ticker, ex_date, payment_date, record_date, dividend_amount, frequency)
                   VALUES (:ticker, :ex_date, :payment_date, :record_date, :dividend_amount, :frequency)""",
                normalized,
            )
        return len(normalized)

    def upsert_calendar_entry(
        self,
        ticker: str,
        ex_date: str,
        dividend_amount: Optional[float],
        payment_date: Optional[str] = None,
        record_date: Optional[str] = None,
        frequency: Optional[str] = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO dividends_calendar
                   (ticker, ex_date, payment_date, record_date, dividend_amount, frequency)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(ticker, ex_date) DO UPDATE SET
                     payment_date = excluded.payment_date,
                     record_date  = excluded.record_date,
                     dividend_amount = COALESCE(excluded.dividend_amount, dividends_calendar.dividend_amount),
                     frequency    = COALESCE(excluded.frequency, dividends_calendar.frequency)""",
                (ticker, ex_date, payment_date, record_date, dividend_amount, frequency),
            )

    def get_calendar(self, ticker: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            if ticker:
                rows = conn.execute(
                    "SELECT * FROM dividends_calendar WHERE ticker=? ORDER BY ex_date ASC", (ticker,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM dividends_calendar ORDER BY ex_date ASC").fetchall()
            return [dict(r) for r in rows]

    # -------------------------------------------------------------------- quotes

    def upsert_quote(
        self, ticker: str, current_price: Optional[float], change: Optional[float] = None, volume: Optional[int] = None
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO quotes (ticker, current_price, change, volume, timestamp)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(ticker) DO UPDATE SET
                     current_price = excluded.current_price,
                     change        = COALESCE(excluded.change, quotes.change),
                     volume        = COALESCE(excluded.volume, quotes.volume),
                     timestamp     = excluded.timestamp""",
                (ticker, current_price, change, volume, time.time()),
            )

    def get_quote(self, ticker: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM quotes WHERE ticker=?", (ticker,)).fetchone()
            return dict(row) if row else None

    # --------------------------------------------------------------- api_usage

    def record_api_call(self, provider: str, daily_limit: Optional[int] = None) -> Dict[str, Any]:
        """Zapíše jedno volání a vrátí aktuální stav čítače pro daný den."""
        day = _utc_day()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM api_usage WHERE provider=? AND day=?", (provider, day)
            ).fetchone()
            if row is None:
                expires_at = time.time() + 86400.0
                conn.execute(
                    """INSERT INTO api_usage (provider, day, calls_used, daily_limit, expires_at)
                       VALUES (?,?,?,?,?)""",
                    (provider, day, 1, daily_limit, expires_at),
                )
            else:
                new_limit = daily_limit if daily_limit is not None else row["daily_limit"]
                conn.execute(
                    "UPDATE api_usage SET calls_used=calls_used+1, daily_limit=COALESCE(?, daily_limit) WHERE provider=? AND day=?",
                    (new_limit, provider, day),
                )
            return self.get_api_usage(provider)

    def get_api_usage(self, provider: str) -> Dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM api_usage WHERE provider=? AND day=?", (provider, _utc_day())
            ).fetchone()
            if row is None:
                return {
                    "provider": provider,
                    "day": _utc_day(),
                    "calls_used": 0,
                    "daily_limit": None,
                    "expires_at": None,
                    "remaining": None,
                }
            d = dict(row)
        limit = d.get("daily_limit")
        used = d.get("calls_used", 0)
        d["remaining"] = (limit - used) if limit is not None else None
        return d

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


__all__ = ["Database"]
