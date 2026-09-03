"""Pomocné funkce pro klasifikaci sektorů (GICS názvy z yfinance info).

Sektorová znalost je soustředěna zde, aby ji sdílely metriky i scoring
bez vzájemných závislostí.
"""

from __future__ import annotations

from typing import Optional

#: Sektory, kde klasická FCF pravidla (payout > 85 % = červená) neplatí —
#: REITY mají strukturovaně zápornou/nízkou FCF kvůli D&A, utility jsou kapitálově intenzivní.
REIT_LIKE_SECTORS = {"Real Estate", "Utilities"}


def is_reit(sector: Optional[str]) -> bool:
    """Přísní REIT test — pro FFO/AFFO logiku a ignorování EPS payout ratio."""
    return (sector or "").strip().lower() == "real estate"


def is_reit_like(sector: Optional[str]) -> bool:
    """Uvolněný test sektorů, kde neplatí klasická FCF pravidla (REIT + utility).

    Normalizuje velikost písmen a mezery konzistentně s :func:`is_reit`, aby se
    stejný sektor nepsuzoval rozdílně v různých částech scoringu.
    """
    return (sector or "").strip().lower() in {s.lower() for s in REIT_LIKE_SECTORS}
