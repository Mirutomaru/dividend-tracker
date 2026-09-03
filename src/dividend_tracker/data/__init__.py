"""Datová vrstva — sběr dat z providera (yfinance), cache, odvětvové benchmarky."""

from .fetcher import DataFetchError, build_snapshot, get_sector_benchmark

__all__ = ["DataFetchError", "build_snapshot", "get_sector_benchmark"]
