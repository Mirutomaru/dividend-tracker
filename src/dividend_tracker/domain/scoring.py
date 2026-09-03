"""Scoring model — celkové skóre dividendového zdraví (0–100), safety flag, signály.

Váhy bloků: udržitelnost 40, valuace 35, kvalita/růst 25. Uvnitř bloku se váhy
přepočítávají jen napříč DOSTUPNÝMI položkami (chybějící data nesmí tichou nulou
snížit skóre). Red safety flag omezuje skóre na ≤39, dividend trap na ≤25.
"""

from __future__ import annotations

import statistics
from typing import List, Optional, Tuple

from ..models import Benchmark, MetricsReport, ScoreBreakdown, ScoreResult, Snapshot

# REIT a utility mají odlišné payout pravidlo (spec: FCF payout > 85 % se jim nepočítá)
REIT_LIKE_SECTORS = {"Real Estate", "Utilities"}

SIGNAL_BUY_ZONE = "BUY_ZONE_UNDERVALUED"   # yield > 15 % nad 5Y průměrem + zelený safety flag
SIGNAL_DIVIDEND_TRAP = "DIVIDEND_TRAP"     # yield > 8 % a FCF payout > 100 % (nebo záporné FCF)
SIGNAL_NO_DIVIDEND = "NO_DIVIDENDS"

BLOCK_WEIGHTS = {"sustainability": 40, "valuation": 35, "quality_growth": 25}


def _clip01(x: Optional[float]) -> Optional[float]:
    return None if x is None else max(0.0, min(1.0, float(x)))


def _lower_is_better(value: Optional[float], best: float, worst: float) -> Optional[float]:
    """Lineární rampa 1→0 pro „nižší je lepší“ (1 při <=best, 0 při >=worst)."""
    if value is None:
        return None
    if worst == best:
        return 1.0
    return _clip01((worst - float(value)) / (worst - best))


def _higher_is_better(value: Optional[float], worst: float, best: float) -> Optional[float]:
    """Lineární rampa 0→1 pro „vyšší je lepší“."""
    if value is None:
        return None
    if best == worst:
        return 1.0
    return _clip01((float(value) - worst) / (best - worst))


def _block_score(items: List[Tuple[str, float, Optional[float]]]) -> Optional[float]:
    """Skóre bloku 0–100; None, pokud není žádná dostupná položka."""
    avail = [(w, s) for _, w, s in items if s is not None]
    total_w = sum(w for w, _ in avail)
    if total_w == 0:
        return None
    return round(sum(w * s for w, s in avail) / total_w * 100.0, 1)


def compute_safety_flag(snap: Snapshot, m: MetricsReport) -> Tuple[Optional[str], List[str]]:
    """Dividend Safety Flag dle specifikace (zelená/žlutá/červená)."""
    reasons: List[str] = []
    red = False

    if snap.fcf_ttm is not None and snap.fcf_ttm < 0:
        red, reasons = True, ["Záporné FCF — výplata není hotovostně kryta."]
    payout_ok = m.payout_eps_pct is not None and m.payout_eps_pct < 65.0
    fcf_p = m.fcf_payout_pct
    if fcf_p is not None and fcf_p > 85.0 and (snap.sector or "") not in REIT_LIKE_SECTORS:
        red = True
        reasons.append(f"FCF payout ratio {fcf_p:.1f} % > 85 %.")
    elif snap.fcf_ttm is not None and snap.fcf_ttm <= 0 and (snap.dividends_paid or 0) > 0:
        red = True
        reasons.append("Dividendy placeny ze záporného FCF (ekvivalent payoutu > 100 %).")
    if m.net_debt_to_ebitda is not None and m.net_debt_to_ebitda > 4.5:
        red, reasons = True, [*reasons, f"Net Debt/EBITDA {m.net_debt_to_ebitda:.2f}x > 4.5x."]

    if red:
        return "red", reasons

    green_ok = (
        payout_ok
        and m.fcf_payout_pct is not None
        and m.fcf_payout_pct < 70.0
        and m.net_debt_to_ebitda is not None
        and m.net_debt_to_ebitda < 3.0
        and m.interest_coverage is not None
        and m.interest_coverage > 4.0
    )
    if green_ok:
        return "green", ["Payout < 65 %; FCF payout < 70 %; Net Debt/EBITDA < 3.0x; Interest Coverage > 4.0x."]

    reasons.append("Nejsou splněna všechna kritéria zeleného stavu (nebo chybí data).")
    return "yellow", reasons


def _detect_signals(snap: Snapshot, m: MetricsReport, safety: Optional[str]) -> List[str]:
    signals: List[str] = []
    trap_fcf_breach = (snap.fcf_ttm is not None and snap.fcf_ttm < 0) or bool(
        m.fcf_payout_pct is not None and m.fcf_payout_pct > 100.0
    )
    if (m.yield_trailing_pct or 0) > 8.0 and trap_fcf_breach:
        signals.append(SIGNAL_DIVIDEND_TRAP)
    if safety == "green" and (m.yield_vs_5y_deviation_pct or -999.0) > 15.0:
        signals.append(SIGNAL_BUY_ZONE)
    return signals


def compute_score(
    snap: Snapshot, m: MetricsReport, bench: Optional[Benchmark] = None
) -> ScoreResult:
    """Celkové skóre (0–100) + flagy a signály. Viz modul docstring pro metodologii."""
    # Firma nevyplácí dividendu → model se nepoužije.
    if not snap.dps_ttm or snap.dps_ttm <= 0:
        return ScoreResult(
            applicable=False, total=None, safety_flag=None, signals=[SIGNAL_NO_DIVIDEND]
        )

    # ---------- bloky (položka: název, váha, skóre 0..1 nebo None=nedostupná) ----
    cagr5 = m.cagr_5y_pct or 0.0
    growth_streak_pts = min(max(cagr5, 0.0) / 5.0, 1.0) * 3.0 + min(m.streak_years / 10.0, 1.0) * 3.0

    fcf_payout_score: Optional[float]
    if snap.fcf_ttm is not None and snap.fcf_ttm <= 0:
        fcf_payout_score = 0.0  # záporné FCF se trestá, nezapočítává se jako "chybí"
    else:
        fcf_payout_score = _lower_is_better(m.fcf_payout_pct, 70.0, 110.0)

    sustainability = [
        ("payout_eps", 10, _lower_is_better(m.payout_eps_pct, 65.0, 90.0)),
        ("fcf_payout", 10, fcf_payout_score),
        ("net_debt_ebitda", 8, _lower_is_better(m.net_debt_to_ebitda, 1.5, 4.5)),
        ("interest_coverage", 6, _higher_is_better(m.interest_coverage, 2.0, 6.0)),
        ("growth_streak", 6, (growth_streak_pts / 6.0) if (m.cagr_5y_pct is not None or m.streak_years > 0) else None),
    ]

    # P/E vs referenční hodnoty (odvětvový medián + aprox. 5letý medián firmy); bereme nepříznivější
    pe_refs = []
    if bench and (bench.median_pe_ttm or 0) > 0:
        pe_refs.append(bench.median_pe_ttm)
    own_hist = [v for v in snap.pe_history_approx.values() if v and v > 0]
    if len(own_hist) >= 3:
        pe_refs.append(statistics.median(own_hist))
    pe_score = None
    if m.pe_ttm is not None and pe_refs:
        ratios = [m.pe_ttm / ref for ref in pe_refs if ref > 0]
        if ratios:
            pe_score = _lower_is_better(max(ratios), 0.8, 1.3)

    ev_ratio = None
    if m.ev_ebitda is not None and bench and (bench.median_ev_ebitda or 0) > 0:
        ev_ratio = m.ev_ebitda / bench.median_ev_ebitda

    valuation = [
        ("yield_vs_5y_avg", 8, _higher_is_better(m.yield_vs_5y_deviation_pct, -20.0, 20.0)),
        ("pe_vs_refs", 7, pe_score),
        ("p_fcf_abs", 6, _lower_is_better(m.p_fcf, 10.0, 25.0)),
        ("ev_ebitda_vs_sector", 7, _lower_is_better(ev_ratio, 0.9, 1.4) if ev_ratio is not None else None),
        ("fcf_yield", 7, _higher_is_better(m.fcf_yield_pct, 3.0, 10.0)),
    ]

    fcf_health: Optional[float] = None
    if snap.fcf_ttm is not None:
        if snap.fcf_ttm <= 0:
            fcf_health = 0.0
        else:
            pts = 5.0 + (3.0 if m.fcf_payout_pct is not None and m.fcf_payout_pct <= 70.0 else 0.0)
            fcf_health = pts / 8.0

    div_growth_q: Optional[float] = None
    if m.cagr_5y_pct is not None:
        c = m.cagr_5y_pct
        div_growth_q = 1.0 if c >= 5 else 0.6 if c >= 2 else 0.2 if c >= 0 else 0.0

    quality = [
        ("roic", 12, _higher_is_better(m.roic_pct, 5.0, 15.0)),
        ("fcf_health", 8, fcf_health),
        ("div_growth_quality", 5, div_growth_q),
    ]

    block_scores = {
        "sustainability": _block_score(sustainability),
        "valuation": _block_score(valuation),
        "quality_growth": _block_score(quality),
    }

    avail_blocks = [(BLOCK_WEIGHTS[k], v) for k, v in block_scores.items() if v is not None]
    total_w = sum(w for w, _ in avail_blocks)
    total = round(sum(w * s for w, s in avail_blocks) / total_w, 1) if total_w else None

    safety, reasons = compute_safety_flag(snap, m)
    signals = _detect_signals(snap, m, safety)

    # Omezení skóre podle rizikových stavů (specifikace logiky hodnocení).
    cap_reasons: List[str] = []
    if total is not None:
        if safety == "red":
            total = min(total, 39.0)
            cap_reasons.append("Skóre omezeno na ≤39 (červený safety flag).")
        if SIGNAL_DIVIDEND_TRAP in signals:
            total = min(total, 25.0)
            cap_reasons.append("Skóre omezeno na ≤25 (dividend trap signál).")

    return ScoreResult(
        applicable=True,
        total=total,
        safety_flag=safety,  # type: ignore[arg-type]
        signals=signals,
        breakdown=ScoreBreakdown(
            sustainability_pct=block_scores["sustainability"],
            valuation_pct=block_scores["valuation"],
            quality_growth_pct=block_scores["quality_growth"],
        ),
        reasons=[*reasons, *cap_reasons],
    )
