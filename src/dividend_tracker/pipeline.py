"""Orchestrace analýzy — jediný vstupní bod pro CLI i GUI."""

from __future__ import annotations

import logging
from typing import Optional

from .data.fetcher import build_snapshot, get_sector_benchmark
from .domain.metrics import compute_metrics
from .domain.scoring import compute_score
from .domain.valuation import compute_fair_value
from .models import Benchmark, MetricsReport, ScoreResult, Snapshot
from .presentation.report import build_report

log = logging.getLogger(__name__)


def analyze_ticker(
    ticker: str,
    required_return_pct: float = 8.0,
    use_benchmark: bool = True,
    refresh: bool = False,
) -> dict:
    """Kompletní analýza jednoho tickera → normalizovaný report (dict pro JSON).

    ``refresh=True`` bypassne disk cache snapshotů (TTL 1 den) a stáhne data znovu.
    Zvedá :class:`DataFetchError`, pokud provider nevrátil dostatek dat.
    """
    snap: Snapshot = build_snapshot(ticker, refresh=refresh)
    bench: Optional[Benchmark] = get_sector_benchmark(snap.sector) if use_benchmark else None
    metrics: MetricsReport = compute_metrics(snap)
    score: ScoreResult = compute_score(snap, metrics, bench)
    fair_value = compute_fair_value(snap, metrics, required_return_pct)
    return build_report(ticker, snap, metrics, score, fair_value, bench)
