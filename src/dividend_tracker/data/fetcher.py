"""Datová vrstva — sběr surových dat společnosti z yfinance + disková cache.

Všechny výjimky z providera se chytají a buď se nahradí ``None`` (doplní se do
``Snapshot.data_gaps``), nebo se zahodí jako :class:`DataFetchError`, pokud je data
moc málo na smysluplnou analýzu. Žádné print() — logování přes standardní logging.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import statistics
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ..models import Benchmark, Snapshot
from .peers import PEER_UNIVERSE, normalize_sector

log = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("DIVTRACK_CACHE", "~/.cache/dividend-tracker")).expanduser()
PEER_TTL_DAYS = 7
SNAPSHOT_TTL_DAYS = 1          # disk cache snapshotů — TTL 1 den (divtrack analyze --refresh bypass)
MAX_WORKERS = 3                # paralelní stahování nezávislých endpointů yfinance za ticker


class DataFetchError(RuntimeError):
    """Nedostatek dat pro smysluplnou analýzu tickeru."""


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def _pick(df: pd.DataFrame | None, *candidates: str) -> float | None:
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


def resolve_fcf(fcf_direct: float | None, ocf: float | None, capex_signed: float | None) -> float | None:
    """Vyřeší FCF: preferuje hodnotu hlášenou direktně providerem, fallback je OCF − CapEx.

    ``capex_signed`` se předává tak, jak ho uvádí CF výkaz (záporné). Záporný FCF
    z libovolného zdroje je platná data — jako „chybějící“ se neinterpretuje.
    """
    if fcf_direct is not None:
        return float(fcf_direct)
    if ocf is not None and capex_signed is not None:
        return float(ocf + capex_signed)  # OCF − |CapEx|; CapEx v výkazu je záporné
    return None


def _is_rate_limited(exc: Exception) -> bool:
    """Rozpozná omezení frekvence providera (429 / Too Many Requests) — delší pauza."""
    msg = str(exc).lower()
    if "429" in msg or "too many requests" in msg or "rate limit" in msg:
        return True
    # yfinance vrhá YFRateLimitError; neimportujeme ho tu (vyhnutí tvrdé závislosti)
    cls = type(exc).__name__.lower()
    return "ratelimit" in cls or "rate_limit" in cls


def _retry(fn: Callable[[], Any], attempts: int = 3, delay: float = 1.5) -> Any:
    """Opakuje volání s exponenciálním backoffem + jitterem; rate-limit (429) čeká déle.

    ``delay`` je základ — každý další pokus ho zdvojnásobí (1,5s → 3s → 6s …), navíc se
    přidá náhodný jitter (0–50 %), aby paralelní workeri nedorazili do providera synchronně.
    """
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — provider vrhá různé typy (429, JSON, timeout)
            last_exc = exc
            if i < attempts - 1:
                base = delay * (2 ** i)
                wait = base + random.uniform(0, base / 2)
                if _is_rate_limited(exc):
                    wait *= 2  # rate-limit → dvojnásobná pauza
                log.warning("retry %d/%d po chybě: %s (čekám %.1fs)", i + 1, attempts - 1, exc, wait)
                time.sleep(wait)
    raise DataFetchError(str(last_exc)) from last_exc


def _fetch_independent(tk: str) -> dict[str, Any]:
    """Stáhne 6 vzájemně nezávislých endpointů yfinance paralelně (ThreadPoolExecutor).

    Každý endpoint je izolovaný — selhání jednoho vrátí ``None`` a zaloguje se, nezahyne
    celá dávka. Vrací dict s klíči dividends/splits/history/income_stmt/balance_sheet/cashflow.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    import yfinance as yf

    names = ["dividends", "splits", "history", "income_stmt", "balance_sheet", "cashflow"]

    def _one(name: str) -> Any:
        t = yf.Ticker(tk)  # čerstvá instance na vlákno — žádný sdílený stav mezi workeri
        if name == "dividends":
            return t.dividends
        if name == "splits":
            return t.splits
        if name == "history":
            return t.history(period="6y", interval="1d")
        if name == "income_stmt":
            return t.income_stmt
        if name == "balance_sheet":
            return t.balance_sheet
        return t.cashflow

    out: dict[str, Any] = {k: None for k in names}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_one, n): n for n in names}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                out[name] = fut.result()
            except Exception as exc:  # noqa: BLE001 — jeden mrtvý endpoint nezahyne celá dávka
                log.warning("%s: chyba při sběru %s (%s)", tk, name, exc)
    return out


def _year_end_prices(hist: pd.DataFrame | None) -> dict[int, float]:
    """Závěrečná cena za každý kalendářní rok z denní historie."""
    if hist is None or hist.empty or "Close" not in hist.columns:
        return {}
    closes = hist["Close"].dropna()
    if closes.empty:
        return {}
    out = closes.groupby(closes.index.year).last().to_dict()
    return {int(y): round(float(p), 4) for y, p in out.items() if pd.notna(p)}


def build_snapshot(ticker: str, refresh: bool = False) -> Snapshot:
    """Sestaví kompletní :class:`Snapshot` pro ticker (paralelní stahování endpointů).

    Pokud ``refresh`` je false a v disk cache je čerstvý snapshot (TTL 1 den), vrátí ho bez
    jediného síťového volání. Jinak stáhne data znovu (6 nezávislých endpointů paralelně)
    a výsledek uloží do cache.
    """
    tk = ticker.strip().upper()

    if not refresh:
        cached = load_cached_snapshot(tk)
        if cached is not None:
            return cached

    import yfinance as yf

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

    # ---- 6 nezávislých endpointů paralelně (ThreadPoolExecutor) -------------------
    data = _fetch_independent(tk)
    div_series = data["dividends"]
    splits = data["splits"]
    hist = data["history"]
    income_stmt = data["income_stmt"]
    balance_sheet = data["balance_sheet"]
    cashflow = data["cashflow"]

    from ..domain.dividends import aggregate_dividends, split_adjust_dividends

    # Historické DPS škálujeme na aktuální počet akcií (splity po dané výplatě),
    # jinak by CAGR a yield vs. 5Y průměr u firem se splity nedávaly smysl.
    if div_series is not None and len(div_series):
        try:
            div_series = split_adjust_dividends(div_series, splits)
        except Exception as exc:  # noqa: BLE001 — bez splitů pokračujeme s neupravenou historií
            log.warning("%s: splity nedostupné (%s), dividendní historie není split-adjusted", tk, exc)
            gaps.append("splits_unadjusted")

    annual, counts, dps_ttm = aggregate_dividends(div_series)
    snap.annual_dps = {int(k): v for k, v in annual.items()}
    snap.dividend_payment_counts = {int(k): v for k, v in counts.items()}
    snap.dps_ttm = dps_ttm if dps_ttm else 0.0

    # ---- denní historie (5–6 let) pro year-end ceny ---------------------------------
    snap.fiscal_year_prices = _year_end_prices(hist)
    if eps_ttm and eps_ttm > 0:
        snap.pe_history_approx = {
            y: round(p / eps_ttm, 2) for y, p in snap.fiscal_year_prices.items() if p and p > 0
        }

    # ---- roční výkazy (již stažené paralelně) ---------------------------------------
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

    save_snapshot_cache(snap)  # TTL 1 den — další spuštění bez --refresh čte z disk cache
    return snap


# ------------------- odvětvové benchmarky s cachí -------------------------------

def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{re.sub(r'[^a-z0-9]+', '_', name.lower())}.json"


# ------------------- disk cache snapshotů (TTL 1 den) ---------------------------

def _snapshot_cache_path(ticker: str) -> Path:
    return _cache_path(f"snap_{ticker.strip().upper()}")


def load_cached_snapshot(ticker: str, ttl_days: float = SNAPSHOT_TTL_DAYS) -> Snapshot | None:
    """Vrátí snapshot z disk cache, pokud je čerstvý (mladší než ``ttl_days``).

    Poškozená / stará cache se tichounce ignoruje a vrátí ``None`` — volající pak stáhne
    data znovu. Žádné výjimky ven.
    """
    path = _snapshot_cache_path(ticker)
    if not path.exists():
        return None
    try:
        cached = json.loads(path.read_text())
        age_days = (datetime.now(timezone.utc).timestamp() - cached["ts"]) / 86400.0
        if age_days > ttl_days:
            log.debug("snapshot cache %s je stará (%.1f d) — ignoruji", ticker, age_days)
            return None
        snap = Snapshot(**cached["data"])
        log.info("%s: snapshot z disk cache (věk %.2f d)", ticker, age_days)
        return snap
    except Exception as exc:  # noqa: BLE001 — poškozená cache → přegenerovat
        log.debug("snapshot cache %s nepoužitelná (%s)", ticker, exc)
        return None


def save_snapshot_cache(snap: Snapshot) -> None:
    """Uloží snapshot do disk cache (TTL 1 den). Chyba zápisu jen zaloguje — nebrání analýze."""
    try:
        path = _snapshot_cache_path(snap.ticker)
        payload = {"ts": datetime.now(timezone.utc).timestamp(), "data": snap.model_dump()}
        path.write_text(json.dumps(payload, indent=1))
    except OSError as exc:
        log.warning("nelze zapsat snapshot cache %s: %s", snap.ticker, exc)


def get_sector_benchmark(sector: str | None, force_refresh: bool = False) -> Benchmark | None:
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


__all__ = [
    "DataFetchError",
    "build_snapshot",
    "get_sector_benchmark",
    "load_cached_snapshot",
    "resolve_fcf",
    "save_snapshot_cache",
]
