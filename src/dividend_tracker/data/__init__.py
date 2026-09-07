"""Datová vrstva — sběr dat z providerů, cache-first SQLite úložiště, synchronizace.

- :mod:`fetcher`  — yfinance snapshoty (původní analytická cesta)
- :mod:`db`       — lokální SQLite databáze (stocks, dividends_history, calendar, quotes, api_usage)
- :mod:`fmp`      — FMP klient s denním čítačem volání (limit 250/den)
- :mod:`tiingo`   — Tiingo klient s denním čítačem volání (limit 10 000/den)
- :mod:`quotes`   — aktuální ceny a historie cen: primárně Tiingo, fallback yfinance → tabulky ``quotes``/``price_history``
- :mod:`sync`     — cache-first orchestrace (TTL 72 h fundamenty / 7 dní dividendy, provider chain FMP → Tiingo → yfinance)
"""

from .alerts import VALID_CONDITIONS, add_alert, evaluate_alerts
from .db import Database
from .fetcher import (
    DataFetchError,
    build_snapshot,
    get_sector_benchmark,
    load_cached_snapshot,
    save_snapshot_cache,
)
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
    "VALID_CONDITIONS",
    "add_alert",
    "auto_add_to_watchlist",
    "build_income_target_report",
    "build_portfolio_report",
    "build_snapshot",
    "build_watchlist_rows",
    "evaluate_alerts",
    "export_watchlist_file",
    "fetch_quotes",
    "get_api_key",
    "get_sector_benchmark",
    "import_watchlist_file",
    "load_cached_snapshot",
    "save_snapshot_cache",
    "sync_all",
    "sync_fundamentals",
    "sync_quotes",
]
