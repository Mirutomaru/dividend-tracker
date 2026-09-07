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

import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    ticker                  TEXT PRIMARY KEY,
    name                    TEXT,
    currency                TEXT DEFAULT 'USD',
    exchange                TEXT,
    country                 TEXT,          -- země původu (pro daňové sazby)
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

-- ---------------------------------------------------------------------------
-- Rozšíření: perzistentní watchlist, portfolio, historie cen, upozornění, nastavení
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS watchlist (
    ticker      TEXT PRIMARY KEY,     -- sledovaný ticker (trvalý seznam)
    added_at    REAL,                 -- epoch sekund přidání
    note        TEXT                  -- volitelná poznámka uživatele
);

CREATE TABLE IF NOT EXISTS portfolio (
    ticker      TEXT PRIMARY KEY,     -- držená pozice
    shares      REAL NOT NULL DEFAULT 0,   -- počet akcií
    avg_cost    REAL,               -- průměrná nákupní cena za akcii
    currency    TEXT DEFAULT 'USD',
    updated_at  REAL                 -- epoch sekund poslední změny pozice
);

CREATE TABLE IF NOT EXISTS portfolio_transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,       -- pozice, ke které transakce patří
    kind        TEXT NOT NULL,       -- buy | sell | dividend
    date_iso    TEXT NOT NULL,       -- ISO datum transakce (YYYY-MM-DD)
    shares      REAL,                -- počet akcií (buy/sell); u dividend NULL
    price       REAL,                -- cena za akcii (buy/sell); u dividend NULL
    amount      REAL,                -- celková částka: buy/sell = shares×price; dividend = vyplacená částka
    created_at  REAL                 -- epoch sekund záznamu
);

CREATE TABLE IF NOT EXISTS price_history (
    ticker      TEXT NOT NULL,
    date        TEXT NOT NULL,       -- ISO YYYY-MM-DD (denní snapshot)
    close       REAL,                -- uzavřecí / aktuální cena
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_price_hist_ticker ON price_history(ticker, date DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    condition       TEXT NOT NULL,   -- below | above | buy_zone
    threshold       REAL,            -- cena pro below/above; u buy_zone se ignoruje
    active          INTEGER DEFAULT 1,
    last_triggered  TEXT,            -- ISO datum posledního vyhodnocení (anti-duplikace)
    created_at      REAL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,          -- např. income_target, tax_rate_us
    value TEXT
);

-- ---------------------------------------------------------------------------
-- Rozšíření: perzistentní analytické reporty (web GUI — kompletní přehled)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS reports (
    ticker      TEXT PRIMARY KEY,     -- poslední hotový report na ticker (upsert)
    report_json TEXT NOT NULL,        -- normalizovaný report (JSON)
    created_at  REAL                  -- epoch sekund generování
);
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
            # Migrace starých DB — přidání sloupce, který neexistuje.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(stocks)").fetchall()}
            if "country" not in cols:
                conn.execute("ALTER TABLE stocks ADD COLUMN country TEXT")

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
        name: str | None = None,
        currency: str | None = None,
        exchange: str | None = None,
        country: str | None = None,
        sector: str | None = None,
        industry: str | None = None,
        fundamentals_json: str | None = None,
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
                       (ticker, name, currency, exchange, country, sector, industry,
                        last_fundamental_update, fundamentals_json)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (ticker, name, currency or "USD", exchange, country, sector, industry, ts, fundamentals_json),
                )
            else:
                # Neexistující hodnoty nenecháme přepsat NULL — zachováme staré.
                conn.execute(
                    """UPDATE stocks SET
                       name = COALESCE(?, name),
                       currency = COALESCE(?, currency),
                       exchange = COALESCE(?, exchange),
                       country = COALESCE(?, country),
                       sector = COALESCE(?, sector),
                       industry = COALESCE(?, industry),
                       last_fundamental_update = COALESCE(?, last_fundamental_update),
                       fundamentals_json = COALESCE(?, fundamentals_json)
                       WHERE ticker=?""",
                    (name, currency, exchange, country, sector, industry, ts, fundamentals_json, ticker),
                )

    def mark_dividend_sync(self, ticker: str) -> None:
        """Označí čas posledního syncu dividendové historie pro daný ticker."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE stocks SET last_dividend_sync=? WHERE ticker=?", (time.time(), ticker)
            )

    def get_stock(self, ticker: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM stocks WHERE ticker=?", (ticker,)).fetchone()
            return dict(row) if row else None

    def list_stocks(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM stocks ORDER BY ticker").fetchall()
            return [dict(r) for r in rows]

    def remove_ticker(self, ticker: str) -> None:
        """Odstraní všechny synchronizované údaje o tickeru (idempotentní).

        Dotkne se tabulek ``stocks``, ``dividends_history``, ``dividends_calendar``,
        ``quotes`` a ``price_history``. Watchlist, portfolio i reporty se nedotýká —
        ty spravuje volající (např. web GUI při odebrání akcie).
        """
        tk = ticker.upper()
        with self._conn() as conn:
            for table in ("stocks", "dividends_history", "dividends_calendar", "quotes", "price_history"):
                conn.execute(f"DELETE FROM {table} WHERE ticker=?", (tk,))

    # -------------------------------------------------------- dividends_history

    def upsert_dividend(
        self,
        ticker: str,
        ex_date: str,
        dividend_amount: float,
        payment_date: str | None = None,
        record_date: str | None = None,
        frequency: str | None = None,
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

    def get_dividends(self, ticker: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM dividends_history WHERE ticker=? ORDER BY ex_date DESC", (ticker,)
            ).fetchall()
            return [dict(r) for r in rows]

    def replace_dividends(self, rows: list[dict[str, Any]]) -> int:
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

    def replace_calendar(self, rows: list[dict[str, Any]]) -> int:
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
        dividend_amount: float | None,
        payment_date: str | None = None,
        record_date: str | None = None,
        frequency: str | None = None,
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

    def get_calendar(self, ticker: str | None = None) -> list[dict[str, Any]]:
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
        self, ticker: str, current_price: float | None, change: float | None = None, volume: int | None = None
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

    def get_quote(self, ticker: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM quotes WHERE ticker=?", (ticker,)).fetchone()
            return dict(row) if row else None

    # --------------------------------------------------------------- api_usage

    def record_api_call(self, provider: str, daily_limit: int | None = None) -> dict[str, Any]:
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

    def get_api_usage(self, provider: str) -> dict[str, Any]:
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

    # ----------------------------------------------------------------- watchlist

    def upsert_watchlist(self, ticker: str, note: str | None = None) -> None:
        """Přidá ticker do trvalé watchlisty (idempotentní)."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO watchlist (ticker, added_at, note) VALUES (?,?,?)
                   ON CONFLICT(ticker) DO UPDATE SET
                     note = COALESCE(excluded.note, watchlist.note)""",
                (ticker.upper(), time.time(), note),
            )

    def remove_from_watchlist(self, ticker: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM watchlist WHERE ticker=?", (ticker.upper(),))

    def list_watchlist(self) -> list[dict[str, Any]]:
        """Trvalá watchlista seřazená abecedně."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM watchlist ORDER BY ticker"
            ).fetchall()
            return [dict(r) for r in rows]

    def is_in_watchlist(self, ticker: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM watchlist WHERE ticker=?", (ticker.upper(),)
            ).fetchone()
            return row is not None

    # ----------------------------------------------------------------- reports

    def save_report(self, report: dict[str, Any]) -> None:
        """Uloží/aktualizuje poslední analytický report pro ticker (upsert)."""
        tk = str(report.get("ticker") or "").upper()
        if not tk:
            return
        stored = dict(report)
        stored["ticker"] = tk  # unifikovaný klíč pro zobrazení i mazání
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO reports (ticker, report_json, created_at) VALUES (?,?,?)
                   ON CONFLICT(ticker) DO UPDATE SET
                     report_json = excluded.report_json,
                     created_at  = excluded.created_at""",
                (tk, json.dumps(stored, ensure_ascii=False), time.time()),
            )

    def list_reports(self) -> list[dict[str, Any]]:
        """Všechny uložené reporty seřazené abecedně podle tickera."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ticker, report_json FROM reports ORDER BY ticker"
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                out.append(json.loads(r["report_json"]))
            except (ValueError, TypeError):
                log.warning("poškozený report pro %s — přeskočeno", r["ticker"])
        return out

    def delete_report(self, ticker: str) -> None:
        """Smaže uložený report tickera (idempotentní)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM reports WHERE ticker=?", (ticker.upper(),))

    # ----------------------------------------------------------------- portfolio

    def upsert_position(
        self,
        ticker: str,
        shares: float,
        avg_cost: float | None = None,
        currency: str | None = None,
    ) -> None:
        """Vloží/aktualizuje drženou pozici. ``shares <= 0`` pozici smaže.

        Pro výpočet TWR/IRR se automaticky zapisují transakce (buy/sell) do
        ``portfolio_transactions`` — nová pozice = buy, snížení podílu = sell,
        zvýšení = buy (cena: avg_cost, jinak aktuální quote).
        """
        tk = ticker.upper()
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        with self._conn() as conn:
            row = conn.execute(
                "SELECT shares FROM portfolio WHERE ticker=?", (tk,)
            ).fetchone()
            old_shares: float | None = float(row["shares"]) if row else None

            if shares is not None and shares <= 0:
                # Kompletní prodej — záznam sell za poslední známou cenou.
                px = (self.get_quote(tk) or {}).get("current_price")
                if old_shares and old_shares > 0 and px:
                    conn.execute(
                        """INSERT INTO portfolio_transactions
                           (ticker, kind, date_iso, shares, price, amount, created_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (tk, "sell", today_iso, old_shares, px, round(old_shares * px, 2), time.time()),
                    )
                conn.execute("DELETE FROM portfolio WHERE ticker=?", (tk,))
                return

            conn.execute(
                """INSERT INTO portfolio (ticker, shares, avg_cost, currency, updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(ticker) DO UPDATE SET
                     shares     = excluded.shares,
                     avg_cost   = COALESCE(excluded.avg_cost, portfolio.avg_cost),
                     currency   = COALESCE(excluded.currency, portfolio.currency),
                     updated_at = excluded.updated_at""",
                (tk, shares, avg_cost, currency or "USD", time.time()),
            )

            # Transakční záznam pro TWR/IRR.
            quote_px = self.get_quote(tk)
            if old_shares is None:
                px = avg_cost or (quote_px or {}).get("current_price")
                if shares > 0 and px:
                    conn.execute(
                        """INSERT INTO portfolio_transactions
                           (ticker, kind, date_iso, shares, price, amount, created_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (tk, "buy", today_iso, shares, px, round(shares * px, 2), time.time()),
                    )
            elif old_shares > 0 and shares < old_shares:
                sold = old_shares - shares
                px = (quote_px or {}).get("current_price") or avg_cost
                if px:
                    conn.execute(
                        """INSERT INTO portfolio_transactions
                           (ticker, kind, date_iso, shares, price, amount, created_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (tk, "sell", today_iso, sold, px, round(sold * px, 2), time.time()),
                    )
            elif shares > old_shares:
                bought = shares - old_shares
                px = avg_cost or (quote_px or {}).get("current_price")
                if px:
                    conn.execute(
                        """INSERT INTO portfolio_transactions
                           (ticker, kind, date_iso, shares, price, amount, created_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (tk, "buy", today_iso, bought, px, round(bought * px, 2), time.time()),
                    )

    def remove_position(self, ticker: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM portfolio WHERE ticker=?", (ticker.upper(),))

    def list_positions(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM portfolio ORDER BY ticker"
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------- portfolio_transactions

    def add_transaction(
        self,
        ticker: str,
        kind: str,
        date_iso: str,
        shares: float | None = None,
        price: float | None = None,
        amount: float | None = None,
    ) -> None:
        """Zapíše transakci portfolia (buy/sell/dividend) pro výpočet TWR/IRR.

        ``amount`` je celková částka cash flow: buy = −shares×price, sell = +shares×price,
        dividend = +vyplacená částka. Sign konvence řeší vrstva nahoře (domain).
        """
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO portfolio_transactions
                   (ticker, kind, date_iso, shares, price, amount, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (ticker.upper(), kind, date_iso, shares, price, amount, time.time()),
            )

    def list_transactions(self, ticker: str | None = None) -> list[dict[str, Any]]:
        """Transakce portfolia seřazené podle data (nejstarší první)."""
        with self._conn() as conn:
            if ticker is not None:
                rows = conn.execute(
                    "SELECT * FROM portfolio_transactions WHERE ticker=? ORDER BY date_iso, id",
                    (ticker.upper(),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM portfolio_transactions ORDER BY date_iso, id"
                ).fetchall()
            return [dict(r) for r in rows]

    def clear_transactions(self, ticker: str | None = None) -> None:
        """Smaže transakce (celé portfolio nebo jeden ticker)."""
        with self._conn() as conn:
            if ticker is not None:
                conn.execute(
                    "DELETE FROM portfolio_transactions WHERE ticker=?", (ticker.upper(),)
                )
            else:
                conn.execute("DELETE FROM portfolio_transactions")

    # ----------------------------------------------------------- price_history

    def record_price_snapshot(self, ticker: str, date_iso: str, close: float | None) -> None:
        """Zapíše (nebo aktualizuje) denní snapshot ceny pro dané datum."""
        if close is None:
            return
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO price_history (ticker, date, close) VALUES (?,?,?)
                   ON CONFLICT(ticker, date) DO UPDATE SET close = excluded.close""",
                (ticker.upper(), date_iso, close),
            )

    def get_price_history(self, ticker: str, days: int = 365) -> list[dict[str, Any]]:
        """Denní snapshoty za posledních ``days`` dní, nejnovější první."""
        cutoff = (datetime.now(timezone.utc).timestamp() - days * 86400.0)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT date, close FROM price_history WHERE ticker=? AND date >= ? ORDER BY date DESC",
                (ticker.upper(), datetime.fromtimestamp(cutoff, tz=timezone.utc).date().isoformat()),
            ).fetchall()
            return [dict(r) for r in rows]

    def price_stats_52w(self, ticker: str) -> dict[str, Any] | None:
        """Statistika za posledních 365 dní: min/max cena, drawdown od maxima, percentil.

        Percentil = podíl dnů, kdy byla cena NÍŽŠÍ než aktuální (0–100).
        Vrátí ``None``, pokud v historii není žádný bod.
        """
        hist = self.get_price_history(ticker, days=365)
        if not hist:
            return None
        closes = [r["close"] for r in hist if r["close"] is not None]
        if not closes:
            return None
        lo, hi = min(closes), max(closes)
        current = closes[0]  # nejnovější
        drawdown_pct = ((current - hi) / hi * 100.0) if hi else None
        below = sum(1 for c in closes if c < current)
        percentile = round(below / len(closes) * 100.0, 1)
        return {
            "ticker": ticker.upper(),
            "low_52w": lo,
            "high_52w": hi,
            "current": current,
            "drawdown_from_high_pct": round(drawdown_pct, 2) if drawdown_pct is not None else None,
            "percentile_52w": percentile,
            "points": len(closes),
        }

    # -------------------------------------------------------------------- alerts

    def add_alert(self, ticker: str, condition: str, threshold: float | None = None) -> int:
        """Přidá upozornění. ``condition`` ∈ {below, above, buy_zone}."""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO alerts (ticker, condition, threshold, active, created_at) VALUES (?,?,?,?,?)",
                (ticker.upper(), condition, threshold, 1, time.time()),
            )
            return int(cur.lastrowid or 0)

    def list_alerts(self, active_only: bool = True) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM alerts WHERE active=1 ORDER BY ticker"
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM alerts ORDER BY ticker").fetchall()
            return [dict(r) for r in rows]

    def set_alert_active(self, alert_id: int, active: bool) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE alerts SET active=? WHERE id=?", (1 if active else 0, alert_id)
            )

    def remove_alert(self, alert_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM alerts WHERE id=?", (alert_id,))

    def mark_alert_triggered(self, alert_id: int, date_iso: str) -> None:
        """Označí datum posledního vyhodnocení (zabrání opakovanému upozornění)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE alerts SET last_triggered=? WHERE id=?", (date_iso, alert_id)
            )

    # ------------------------------------------------------------------ settings

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def list_settings(self) -> dict[str, str]:
        """Všechna nastavení jako slovník ``{key: value}``."""
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {r["key"]: r["value"] for r in rows}

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO settings (key, value) VALUES (?,?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, value),
            )

    def delete_setting(self, key: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM settings WHERE key=?", (key,))

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


__all__ = ["Database"]
