"""Datová vrstva — sběr surových dat společnosti z yfinance + disková cache.

Všechny výjimky z providera se chytají a buď se nahradí ``None`` (doplní se do
``Snapshot.data_gaps``), nebo se zahodí jako :class:`DataFetchError`, pokud je data
moc málo na smysluplnou analýzu. Žádné print() — logování přes standardní logging.
"""

from __future__ import annotations

import json
import logging
import os
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from ..models import Benchmark, Snapshot
from .peers import PEER_UNIVERSE, normalize_sector

log = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("DIVTRACK_CACHE", "~/.cache/dividend-tracker")).expanduser()
PEER_TTL_DAYS = 7


class DataFetchError(RuntimeError):
    """Nedostatek dat pro smysluplnou analýzu tickeru."""


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def _pick(df: Optional[pd.DataFrame], *candidates: str) -> Optional[float]:
    """Vyzvedne nejnovější hodnotu řádku z ročního výkazu.

    Nejprve přesný (case-insensitive) název, pak fuzzy částečný zápis — providery
    občas mění labely ("Interest Expense" vs "Interest Expense - Non Operating").
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    col = df.columns[0]  # yfinance řadí nejnovější rok jako první sloupec
    for cand in candidates:
        for idx in df.index:
            if str(idx).strip().lower() == cand.lower():
                v = _to_float(df.at[idx, col])
                if v is not None:
                    return v
    for cand in candidates:
        needle = cand.lower()
        for idx in df.index:
            if needle in str(idx).lower():
                v = _to_float(df.at[idx, col])
                if v is not None:
                    return v
    return None


def resolve_fcf(fcf_direct: Optional[float], ocf: Optional[float], capex_signed: Optional[float]) -> Optional[float]:
    """Vyřeší FCF: preferuje hodnotu hlášenou direktně providerem, fallback je OCF − CapEx.

    ``capex_signed`` se předává tak, jak ho uvádí CF výkaz (záporné). Záporný FCF
    z libovolného zdroje je platná data — jako „chybějící“ se neinterpretuje.
    """
    if fcf_direct is not None:
        return float(fcf_direct)
    if ocf is not None and capex_signed is not None:
        return float(ocf + capex_signed)  # OCF − |CapEx|; CapEx v výkazu je záporné
    return None


def _retry(fn: Callable[[], Any], attempts: int = 2, delay: float = 1.5) -> Any:
    last_exc: Optional[Exception] = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — provider vrhá různé typy (429, JSON, timeout)
            last_exc = exc
            if i < attempts - 1:
                log.warning("retry %d/%d po chybě: %s", i + 1, attempts - 1, exc)
                time.sleep(delay * (i + 1))
    raise DataFetchError(str(last_exc)) from last_exc


def _year_end_prices(hist: Optional[pd.DataFrame]) -> dict[int, float]:
    """Závěrečná cena za každý kalendářní rok z denní historie."""
    if hist is None or hist.empty or "Close" not in hist.columns:
        return {}
    closes = hist["Close"].dropna()
    if closes.empty:
        return {}
    out = closes.groupby(closes.index.year).last().to_dict()
    return {int(y): round(float(p), 4) for y, p in out.items() if pd.notna(p)}


def build_snapshot(ticker: str) -> Snapshot:
    """Sestaví kompletní :class:`Snapshot` pro ticker (jedno volání na výkaz)."""
    import yfinance as yf

    tk = ticker.strip().upper()
    t = yf.Ticker(tk)
    gaps: list[str] = []

    info = _retry(lambda: dict(t.info or {}))
    if not info:
        raise DataFetchError(f"{tk}: provider nevrátil žádné základní info.")

    price = _to_float(info.get("currentPrice") or info.get("regularMarketPrice"))
    eps_ttm = _to_float(info.get("trailingEps"))

    snap = Snapshot(
        ticker=tk,
        name=info.get("longName") or info.get("shortName"),
        sector=info.get("sector"),
        industry=info.get("industry"),
        currency=info.get("currency") or "USD",
        as_of=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        price=price,
        shares_outstanding=int(info["sharesOutstanding"]) if _to_float(info.get("sharesOutstanding")) else None,
        market_cap=_to_float(info.get("marketCap")),
        enterprise_value=_to_float(info.get("enterpriseValue")),
        eps_ttm=eps_ttm,
        eps_forward=_to_float(info.get("forwardEps")),
        forward_dividend_rate=_to_float(info.get("dividendRate")),
        pe_ttm_reported=_to_float(info.get("trailingPE")),
        pe_forward_reported=_to_float(info.get("forwardPE")),
        ev_ebitda_reported=_to_float(info.get("enterpriseToEbitda")),
    )

    # ---- dividendová historie -----------------------------------------------------
    try:
        div_series = t.dividends
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: chyba při sběru dividend (%s)", tk, exc)
        div_series = None
    from ..domain.dividends import aggregate_dividends, split_adjust_dividends

    # Historické DPS škálujeme na aktuální počet akcií (splity po dané výplatě),
    # jinak by CAGR a yield vs. 5Y průměr u firem se splity nedávaly smysl.
    if div_series is not None and len(div_series):
        try:
            splits = t.splits
            div_series = split_adjust_dividends(div_series, splits)
        except Exception as exc:  # noqa: BLE001 — bez splitů pokračujeme s neupravenou historií
            log.warning("%s: splity nedostupné (%s), dividendní historie není split-adjusted", tk, exc)
            gaps.append("splits_unadjusted")

    annual, counts, dps_ttm = aggregate_dividends(div_series)
    snap.annual_dps = {int(k): v for k, v in annual.items()}
    snap.dividend_payment_counts = {int(k): v for k, v in counts.items()}
    snap.dps_ttm = dps_ttm if dps_ttm else 0.0

    # ---- denní historie (5–6 let) pro year-end ceny ---------------------------------
    try:
        hist = t.history(period="6y", interval="1d")
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: chyba při sběru historii cen (%s)", tk, exc)
        hist = None
    snap.fiscal_year_prices = _year_end_prices(hist)
    if eps_ttm and eps_ttm > 0:
        snap.pe_history_approx = {
            y: round(p / eps_ttm, 2) for y, p in snap.fiscal_year_prices.items() if p and p > 0
        }

    # ---- roční výkazy ---------------------------------------------------------------
    try:
        income_stmt = t.income_stmt
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: chyba při sběru income statement (%s)", tk, exc)
        income_stmt = None
    try:
        balance_sheet = t.balance_sheet
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: chyba při sběru balance sheet (%s)", tk, exc)
        balance_sheet = None
    try:
        cashflow = t.cashflow
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: chyba při sběru cash flow statement (%s)", tk, exc)
        cashflow = None

    snap.ebitda = _pick(income_stmt, "EBITDA")
    snap.ebit = _pick(income_stmt, "Operating Income", "EBIT")
    snap.interest_expense = _pick(income_stmt, "Interest Expense", "Interest Expense - Non Operating")
    snap.net_income = _pick(income_stmt, "Net Income")

    tax_provision = _pick(income_stmt, "Tax Provision", "Provision For Income Tax")
    pretax = _pick(income_stmt, "Pretax Income", "Income Before Tax")
    if tax_provision is not None and pretax:
        snap.tax_rate = round(tax_provision / pretax, 4)

    for label, gap in [
        (snap.ebitda, "ebitda"),
        (snap.ebit, "ebit"),
        (snap.interest_expense, "interest_expense"),
        (snap.net_income, "net_income"),
    ]:
        if label is None and gap not in gaps:
            gaps.append(gap)

    snap.total_debt = _pick(balance_sheet, "Total Debt")
    snap.cash_and_equivalents = _pick(
        balance_sheet, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"
    )
    snap.equity = _pick(balance_sheet, "Stockholders Equity", "Total Stockholder Equity")

    ocf = _pick(cashflow, "Operating Cash Flow", "Net Income From Continuing Operating Activity")
    capex_signed = _pick(cashflow, "Capital Expenditure", "CapEx")

    # FCF: provider ho hlásí někdy direktně (info.freeCashflow), jindy jen OCF/CapEx → fallback.
    fcf_direct = _to_float(info.get("freeCashflow"))
    snap.fcf_ttm = resolve_fcf(fcf_direct, ocf, capex_signed)
    if snap.fcf_ttm is not None:
        snap.fcf_ttm = round(snap.fcf_ttm, 0)
    else:
        gaps.append("fcf")

    # D&A a CapEx — vstupy pro FFO/AFFO odhad u REIT (Snapshot.capex = kladná velikost |CapEx|).
    da = _pick(cashflow, "Depreciation And Amortization", "Depreciation")
    snap.depreciation_amortization = da
    snap.capex = abs(capex_signed) if capex_signed is not None else None

    from ..domain.sectors import is_reit

    if is_reit(snap.sector):  # bez D&A/CapEx nelze REIT payout spočítat — explicitní gap
        if da is None:
            gaps.append("depreciation_amortization")
        if snap.capex is None:
            gaps.append("capex")

    div_paid = _pick(cashflow, "Cash Dividends Paid", "Common Stock Dividend Paid", "Dividends Paid")
    snap.dividends_paid = abs(div_paid) if div_paid is not None else None
    if snap.dividends_paid is None:
        gaps.append("dividends_paid")

    for label, gap in [
        (snap.total_debt, "total_debt"),
        (snap.cash_and_equivalents, "cash"),
        (snap.equity, "equity"),
    ]:
        if label is None and gap not in gaps:
            gaps.append(gap)

    snap.data_gaps = sorted(set(gaps))

    if price is None:
        raise DataFetchError(f"{tk}: aktuální cena nedostupná.")

    return snap


# ------------------- odvětvové benchmarky s cachí -------------------------------

def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{re.sub(r'[^a-z0-9]+', '_', name.lower())}.json"


def get_sector_benchmark(sector: Optional[str], force_refresh: bool = False) -> Optional[Benchmark]:
    """Mediány P/E (TTM) a EV/EBITDA napříč peer universe daného sektoru.

    Výsledek se cache na 7 dní (disk), aby se peers nevytahovali při každém spuštění.
    """
    canonical = normalize_sector(sector)
    if not canonical or canonical not in PEER_UNIVERSE:
        return None

    path = _cache_path(f"benchmark_{canonical}")
    if not force_refresh and path.exists():
        try:
            cached = json.loads(path.read_text())
            age_days = (datetime.now(timezone.utc).timestamp() - cached["ts"]) / 86400.0
            if age_days <= PEER_TTL_DAYS:
                return Benchmark(**cached["data"])
        except Exception:  # noqa: BLE001 — poškozená cache → přegenerovat
            pass

    pe_vals, ev_vals = [], []
    for peer in PEER_UNIVERSE[canonical]:
        try:
            import yfinance as yf

            pinfo = dict(yf.Ticker(peer).info or {})
            pe = _to_float(pinfo.get("trailingPE"))
            ev = _to_float(pinfo.get("enterpriseToEbitda"))
            if pe and pe > 0:
                pe_vals.append(pe)
            if ev and ev > 0:
                ev_vals.append(ev)
        except Exception as exc:  # noqa: BLE001 — jeden mrtvý peer nevrací celý benchmark
            log.warning("benchmark %s: peer %s selhal (%s)", canonical, peer, exc)
        time.sleep(0.35)

    data = Benchmark(
        sector=canonical,
        median_pe_ttm=round(statistics.median(pe_vals), 2) if pe_vals else None,
        median_ev_ebitda=round(statistics.median(ev_vals), 2) if ev_vals else None,
        n_peers=len(pe_vals) or len(ev_vals),
    )
    try:
        path.write_text(json.dumps({"ts": datetime.now(timezone.utc).timestamp(), "data": data.model_dump()}, indent=1))
    except OSError as exc:
        log.warning("nelze zapsat cache benchmarku: %s", exc)

    if not data.n_peers:
        return None
    return data


__all__ = ["DataFetchError", "build_snapshot", "get_sector_benchmark", "resolve_fcf"]
