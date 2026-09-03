"""Normalizované JSON schéma reportu (specifikace bod 3 — výstupní požadavky).

Struktura: kompletní tabulka metrik srovaná proti oborovým benchmarkům, celkové
skóre dividendového zdraví (0–100) a odhad férové nákupní ceny / zóny.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models import Benchmark, FairValueResult, MetricsReport, ScoreResult, Snapshot

SCHEMA_VERSION = "1.0"

SIGNAL_DISPLAY: Dict[str, str] = {
    "BUY_ZONE_UNDERVALUED": "Možné podhodnocení / nákupní zóna",
    "DIVIDEND_TRAP": "⚠ DIVIDENDOVÁ PAST (dividend trap)",
    "NO_DIVIDENDS": "Společnost nevyplácí dividendu — model se nepoužil.",
}

VERDICT_DISPLAY: Dict[str, str] = {
    "undervalued": "Podhodnoceno (cena < 90 % FV)",
    "fair": "Férově oceněno (±10 % kolem FV)",
    "overvalued": "Přehodnoceno (cena > 110 % FV)",
    "unknown": "Nedostatek dat pro úsudek",
}


def _own_5y_median_pe(snap: Snapshot) -> Optional[float]:
    vals = [v for v in snap.pe_history_approx.values() if v and v > 0]
    return round(statistics.median(vals), 2) if len(vals) >= 3 else None


def build_report(
    ticker: str,
    snap: Snapshot,
    metrics: MetricsReport,
    score: ScoreResult,
    fair_value: FairValueResult,
    benchmark: Optional[Benchmark] = None,
) -> dict:
    """Se skládá finalizovaný report (plain dict → serializovatelné JSON)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "ticker": snap.ticker,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "company": {"name": snap.name, "sector": snap.sector, "industry": snap.industry},
        "currency": snap.currency,
        "price_current": snap.price,
        "metrics": {
            "dividend": {
                "yield_trailing_pct": metrics.yield_trailing_pct,
                "yield_forward_pct": metrics.yield_forward_pct,
                "dps_ttm": snap.dps_ttm,
                "payout_ratio_eps_pct": metrics.payout_eps_pct,
                "fcf_payout_ratio_pct": metrics.fcf_payout_pct,
                "growth_cagr_3y_pct": metrics.cagr_3y_pct,
                "growth_cagr_5y_pct": metrics.cagr_5y_pct,
                "growth_cagr_10y_pct": metrics.cagr_10y_pct,
                "dividend_streak_years": metrics.streak_years,
            },
            "valuation": {
                "pe_ttm": metrics.pe_ttm,
                "pe_forward": metrics.pe_forward,
                "sector_median_pe_ttm": benchmark.median_pe_ttm if benchmark else None,
                "own_5y_median_pe_approx": _own_5y_median_pe(snap),
                "p_fcf": metrics.p_fcf,
                "ev_ebitda": metrics.ev_ebitda,
                "sector_median_ev_ebitda": benchmark.median_ev_ebitda if benchmark else None,
                "fcf_yield_pct": metrics.fcf_yield_pct,
                "dividend_yield_5y_avg_pct": metrics.yield_5y_avg_pct,
                "yield_vs_5y_avg_deviation_pct": metrics.yield_vs_5y_deviation_pct,
            },
            "health": {
                "net_debt_to_ebitda": metrics.net_debt_to_ebitda,
                "interest_coverage": metrics.interest_coverage,
                "roic_pct": metrics.roic_pct,
            },
        },
        "benchmark_sector": benchmark.model_dump() if benchmark else None,
        "score": {
            "applicable": score.applicable,
            "total_0_100": score.total,
            "safety_flag": score.safety_flag,
            "signals": score.signals,
            "signals_display": [SIGNAL_DISPLAY.get(s, s) for s in score.signals],
            "breakdown": score.breakdown.model_dump(),
            "reasons": score.reasons,
        },
        "fair_value": {
            **fair_value.model_dump(),
            "verdict_display": VERDICT_DISPLAY.get(fair_value.verdict, fair_value.verdict),
        },
        "notes": metrics.notes,
        "data_gaps": snap.data_gaps,
    }


def export_json(reports: List[Dict[str, Any]], path: str | Path) -> Path:
    """Zapíše reporty do souboru (list reportů, jeden JSON). Vrátí absolutní cestu."""
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reports": reports,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return p
