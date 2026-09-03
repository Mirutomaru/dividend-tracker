"""Výpočty dividend — agregace plateb, CAGR, streak. Čisté funkce (testovatelné bez sítě)."""

from __future__ import annotations

import pandas as pd
from typing import Dict, Optional, Tuple


def split_adjust_dividends(
    divs: Optional[pd.Series],
    splits: Optional["pd.Series | pd.DataFrame"] = None,
) -> pd.Series:
    """Upraví surovou historii dividend o akciové splity (srovnatelný DPS na aktuální počet akcií).

    yfinance vrací dividendy tak, jak byly vyplaceny — po pozdějším 4:1 splitu jsou
    historické per-share hodnoty na starém počtu akcií. Pro CAGR i srovnání yieldu
    s aktuální cenou se každá minulá platba škáluje na AKTUÁLNÍ počet akcií:
    vynásobí se součinem ``1/faktor`` všech splitů, které proběhly PO dané výplatě.

    Konvence faktoru (yfinance ``splits``, ověřeno ve zdrojovém kódu 1.x):
    4:1 forward split = 4.0, reverse split < 1. Neplatné hodnoty (NaN, <= 0) se ignorují.
    """
    if divs is None or len(divs) == 0:
        return pd.Series(dtype=float) if divs is None else divs.copy()

    out = divs.astype(float).copy()

    # Normalizace splits na (datum, faktor) — Series i DataFrame se sloupcem "Stock Splits"
    pairs: list[tuple] = []
    if splits is not None and len(splits):
        s = splits
        if isinstance(s, pd.DataFrame):
            col = "Stock Splits" if "Stock Splits" in s.columns else s.columns[0]
            s = s[col]
        for ts, f in s.items():
            try:
                fv = float(f)
            except (TypeError, ValueError):
                continue
            if fv != fv or fv <= 0.0:  # NaN nebo neplatný faktor
                continue
            pairs.append((ts, fv))

    for i, ts in enumerate(out.index):
        prod = 1.0
        for split_ts, factor in pairs:
            try:
                after = split_ts > ts
            except TypeError:  # nesrovnatelné typy indexů — raději ignorovat než crashnout
                continue
            if after:
                prod /= factor
        out.iloc[i] = out.iloc[i] * prod

    return out


def aggregate_dividends(series: "pd.Series | None") -> Tuple[Dict[int, float], Dict[int, int], Optional[float]]:
    """Zahrubí surovou sérii plateb dividend (DatetimeIndex, hodnota = DPS za jednu výplatu).

    Vrátí ``(dps_per_fiscal_year, payment_counts, dps_ttm)``.

    Poznámky k aproximacím:
    - fiskální rok ≈ kalendářní rok data výplaty (pro kvartální plátce přesné),
    - TTM okno = 365 dnů zpět od POSLEDNÍ záznamu v sérii (nikoli "dnes"), aby starší
      série nedávala zavádějící nulu.
    """
    annual: Dict[int, float] = {}
    counts: Dict[int, int] = {}
    if series is None or len(series) == 0:
        return {}, {}, 0.0

    vals = series.dropna()
    if len(vals) == 0:
        return {}, {}, 0.0

    ref = vals.index.max()
    window_start = ref - pd.Timedelta(days=365)
    ttm = 0.0
    for ts, v in vals.items():
        v = float(v)
        if v <= 0:  # záporné "plateb" jsou korekce/revize — ignorujeme
            continue
        y = int(ts.year)
        annual[y] = round(annual.get(y, 0.0) + v, 6)
        counts[y] = counts.get(y, 0) + 1
        if window_start <= ts <= ref:
            ttm += v

    return annual, counts, round(ttm, 4)


def full_years(annual_dps: Dict[int, float], payment_counts: Dict[int, int]) -> Dict[int, float]:
    """Roky s "plným" rokem plateb (typický počet výplat).

    Nejnovější rok může být nekompletní (firma tečálně platí) — ten se ze streaku/CAGR
    vynechává, aby nedával falešný pokles. Typ = nejčastější počet plateb za rok.
    """
    if not payment_counts:
        return dict(annual_dps)
    # mode počtu plateb (při remě vyšší počet — konzervativněji)
    freq: Dict[int, int] = {}
    for c in payment_counts.values():
        freq[c] = freq.get(c, 0) + 1
    typical = max(sorted(freq), key=lambda c: (freq[c], c))
    return {y: v for y, v in annual_dps.items() if payment_counts.get(y, 0) >= typical}


def cagr(annual_full: Dict[int, float], years: int) -> Optional[float]:
    """CAGR dividendy za ``years`` let mezi NEJNOVĚJŠÍM plným rokem a rokem o ``years`` dřív.

    Vrací procenta (např. 14.9 == +14,9 %/rok) nebo None, pokud data nestačí.
    """
    if years <= 0 or not annual_full:
        return None
    ys = sorted(k for k, v in annual_full.items() if v > 0)
    if len(ys) < 2:
        return None
    end_y = ys[-1]
    start_y = end_y - years
    if start_y not in annual_full or annual_full[start_y] <= 0:
        return None
    start_v, end_v = annual_full[start_y], annual_full[end_y]
    n = end_y - start_y
    if n <= 0 or end_v < 0:
        return None
    return round(((end_v / start_v) ** (1.0 / n) - 1.0) * 100.0, 2)


def dividend_streak(annual_full: Dict[int, float]) -> int:
    """Počet po sobě jdoucích let růstu výplaty (každý rok vyšší než předchozí).

    Počítá se od nejnovějšího plného roku zpět; rok s poklesem nebo nulou streak
    přeruší (vrátí 0). Pár „rok > předchozí rok“ = 1 bod.
    """
    ys = sorted(k for k, v in annual_full.items() if v > 0)
    if not ys or len(ys) < 2:
        return 0
    streak = 0
    for i in range(len(ys) - 1, 0, -1):
        cur, prev = annual_full[ys[i]], annual_full[ys[i - 1]]
        if prev > 0 and cur > prev:
            streak += 1
        else:
            break
    return streak
