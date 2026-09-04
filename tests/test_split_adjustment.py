"""Detailní testy split_adjust_dividends (domain/dividends.py).

Konvence faktoru: 4:1 forward split = 4.0, reverse split < 1. Historická platba se
škáluje na aktuální počet akcií součinem 1/faktor všech splitů PO dané výplatě.
Neplatné hodnoty (NaN, <= 0) se ignorují.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dividend_tracker.domain.dividends import aggregate_dividends, split_adjust_dividends


def _divs(*pairs: tuple[str, float]) -> pd.Series:
    """Pomocná konstrukce série plateb z (datum, DPS) párů."""
    return pd.Series([v for _, v in pairs], index=pd.to_datetime([d for d, _ in pairs]))


# ---------- Forward split (> 1) ---------------------------------------------------


def test_forward_split_scales_history_down():
    """4:1 split po výplatě → historická platba /4; platby po splitu beze změny."""
    divs = _divs(("2023-06-15", 1.00), ("2024-06-15", 1.25))
    splits = pd.Series({pd.Timestamp("2024-01-02"): 4.0})
    out = split_adjust_dividends(divs, splits)
    assert list(out) == pytest.approx([0.25, 1.25])


def test_split_before_all_payments_is_noop():
    """Split před první výplatou neovlivní žádnou platbu (žádný split 'po' ní)."""
    divs = _divs(("2024-06-15", 1.00), ("2025-06-15", 1.25))
    splits = pd.Series({pd.Timestamp("2023-01-02"): 4.0})
    out = split_adjust_dividends(divs, splits)
    assert list(out) == pytest.approx([1.00, 1.25])


def test_split_same_day_as_payment_not_applied():
    """Hranice: split ve stejný den jako výplata se NEaplikuje (po = striktně později)."""
    divs = _divs(("2024-01-02", 1.00))
    splits = pd.Series({pd.Timestamp("2024-01-02"): 2.0})
    out = split_adjust_dividends(divs, splits)
    assert list(out) == pytest.approx([1.00])


# ---------- Reverse split (< 1) ---------------------------------------------------


def test_reverse_split_scales_history_up():
    """Reverse split 1:4 (faktor 0.25) po výplatě → historická platba ×4."""
    divs = _divs(("2023-06-15", 4.00), ("2024-06-15", 1.00))
    splits = pd.Series({pd.Timestamp("2024-01-02"): 0.25})
    out = split_adjust_dividends(divs, splits)
    assert list(out) == pytest.approx([16.0, 1.0])


# ---------- Kombinace a hranice ---------------------------------------------------


def test_multiple_splits_compound():
    """Dva splity po výplatě se násobí: /2/4 = /8."""
    divs = _divs(("2022-06-15", 1.00), ("2024-06-15", 2.00))
    splits = pd.Series(
        {pd.Timestamp("2023-01-02"): 2.0, pd.Timestamp("2024-01-02"): 4.0}
    )
    out = split_adjust_dividends(divs, splits)
    # první platba (2022): oba splity po ní → /2/4 = 1/8; druhá (2024-06): žádný split
    # po ní (oba proběhly dříve) → beze změny
    assert list(out) == pytest.approx([0.125, 2.0])


def test_split_between_two_payments_affects_only_earlier():
    """Split mezi dvěma výplatami upraví jen tu starší."""
    divs = _divs(("2023-06-15", 1.00), ("2024-06-15", 1.50))
    splits = pd.Series({pd.Timestamp("2024-01-02"): 2.0})
    out = split_adjust_dividends(divs, splits)
    assert list(out) == pytest.approx([0.5, 1.5])


# ---------- Neplatné vstupy a robustnost ------------------------------------------


def test_invalid_split_factors_ignored():
    """NaN, 0, záporné i None faktory se ignorují — výstup beze změny."""
    divs = _divs(("2023-06-15", 1.00), ("2024-06-15", 1.25))
    splits = pd.Series(
        {
            pd.Timestamp("2023-01-02"): np.nan,
            pd.Timestamp("2023-06-01"): 0.0,
            pd.Timestamp("2023-09-01"): -1.0,
            pd.Timestamp("2024-01-01"): None,
        }
    )
    out = split_adjust_dividends(divs, splits)
    assert list(out) == pytest.approx([1.00, 1.25])


def test_mixed_valid_and_invalid_factors():
    """Platný faktor mezi neplatnými se aplikuje normálně."""
    divs = _divs(("2023-06-15", 1.00), ("2024-06-15", 1.25))
    splits = pd.Series(
        {pd.Timestamp("2023-01-02"): np.nan, pd.Timestamp("2024-01-02"): 4.0}
    )
    out = split_adjust_dividends(divs, splits)
    assert list(out) == pytest.approx([0.25, 1.25])


def test_dataframe_input_with_stock_splits_column():
    """DataFrame se sloupcem 'Stock Splits' (tvary yfinance) — funguje."""
    divs = _divs(("2023-06-15", 1.00), ("2024-06-15", 1.25))
    df = pd.DataFrame({"Stock Splits": {pd.Timestamp("2024-01-02"): 4.0}})
    out = split_adjust_dividends(divs, df)
    assert list(out) == pytest.approx([0.25, 1.25])


def test_dataframe_input_falls_back_to_first_column():
    """DataFrame bez sloupce 'Stock Splits' → použije se první sloupec."""
    divs = _divs(("2023-06-15", 1.00), ("2024-06-15", 1.25))
    df = pd.DataFrame({"Split": {pd.Timestamp("2024-01-02"): 4.0}})
    out = split_adjust_dividends(divs, df)
    assert list(out) == pytest.approx([0.25, 1.25])


def test_none_and_empty_inputs():
    """None → prázdná float série; prázdná série se vrátí (kopie)."""
    out = split_adjust_dividends(None)
    assert isinstance(out, pd.Series) and len(out) == 0
    empty = pd.Series(dtype=float)
    out2 = split_adjust_dividends(empty)
    assert len(out2) == 0


def test_no_splits_argument_is_identity():
    """Bez splits (None) se vrátí kopie beze změny."""
    divs = _divs(("2023-06-15", 1.00), ("2024-06-15", 1.25))
    out = split_adjust_dividends(divs)
    assert list(out) == pytest.approx([1.00, 1.25])


def test_incomparable_index_types_ignored_not_crash():
    """Nesrovnatelné typy indexů (string vs Timestamp) → ignorovat, ne crashnout."""
    divs = _divs(("2023-06-15", 1.00), ("2024-06-15", 1.25))
    splits = pd.Series({"2024-01-02": 4.0})  # string index
    out = split_adjust_dividends(divs, splits)
    assert list(out) == pytest.approx([1.00, 1.25])


def test_input_series_not_mutated():
    """Vstupní série se nemění (vrací se kopie)."""
    divs = _divs(("2023-06-15", 1.00), ("2024-06-15", 1.25))
    splits = pd.Series({pd.Timestamp("2024-01-02"): 4.0})
    split_adjust_dividends(divs, splits)
    assert list(divs) == pytest.approx([1.00, 1.25])


# ---------- Integrace s agregací ---------------------------------------------------


def test_split_adjusted_history_feeds_aggregate():
    """End-to-end: po úpravě o split dává agregace konzistentní DPS za rok."""
    divs = _divs(("2023-06-15", 4.00), ("2024-06-15", 1.00))
    splits = pd.Series({pd.Timestamp("2024-01-02"): 0.25})  # reverse 1:4
    adjusted = split_adjust_dividends(divs, splits)
    annual, counts, ttm = aggregate_dividends(adjusted)
    assert annual == {2023: 16.0, 2024: 1.0}
    assert counts == {2023: 1, 2024: 1}
