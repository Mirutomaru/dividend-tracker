"""Datová vrstva — sběr dat z providerů, cache-first SQLite úložiště, synchronizace.

- :mod:`fetcher`  — yfinance snapshoty (původní analytická cesta)
- :mod:`db`       — lokální SQLite databáze (stocks, dividends_history, calendar, quotes, api_usage)
- :mod:`fmp`      — FMP klient s denním čítačem volání (limit 250/den)
- :mod:`quotes`   — aktuální ceny z odděleného zdroje (yfinance batch) → tabulka ``quotes``
- :mod:`sync`     — cache-first orchestrace (TTL 72 h fundamenty / 7 dní dividendy, fallback yfinance)
"""

from .db import Database
from .fetcher import DataFetchError, build_snapshot, get_sector_benchmark
from .fmp import DAILY_LIMIT, FMPCacheClient, FMPError, FMPLimitExceeded, get_api_key
from .portfolio import build_income_target_report, build_portfolio_report
from .quotes import fetch_quotes, sync_quotes
from .sync import (
    DIVIDEND_TTL,
    FUNDAMENTAL_TTL,
    sync_all,
    sync_fundamentals,
)
from .watchlist import build_watchlist_rows
from .watchlist_store import (
    DEFAULT_WATCHLIST_FILE,
    auto_add_to_watchlist,
    export_watchlist_file,
    import_watchlist_file,
)

__all__ = [
    "DataFetchError",
    "Database",
    "DAILY_LIMIT",
    "DEFAULT_WATCHLIST_FILE",
    "DIVIDEND_TTL",
    "FMPCacheClient",
    "FMPError",
    "FMPLimitExceeded",
    "FUNDAMENTAL_TTL",
    "auto_add_to_watchlist",
    "build_snapshot",
    "build_watchlist_rows",
    "export_watchlist_file",
    "fetch_quotes",
    "get_api_key",
    "get_sector_benchmark",
    "import_watchlist_file",
    "sync_all",
    "sync_fundamentals",
    "sync_quotes",
]
