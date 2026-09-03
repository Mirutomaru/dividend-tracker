"""Odvětvové peer universe — likvidní US large-cap akcie pro výpočet odvětvových mediánů.

Sektory odpovídají GICS názvům, jak je vrací yfinance ``info["sector"]``.
Mediány se počítají jen z P/E (TTM) a EV/EBITDA — obě jsou dostupné bez dalších
výkazových volání (levný batch přes ``Ticker.info``), s diskovou cachí na 7 dní.
"""

from __future__ import annotations

PEER_UNIVERSE: dict[str, list[str]] = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "CSCO", "QCOM", "INTC"],
    "Healthcare": ["JNJ", "PFE", "MRK", "UNH", "LLY", "ABBV", "AMGN", "TMO", "BMY", "CI"],
    "Financial Services": ["JPM", "BAC", "WFC", "GS", "V", "MA", "BLK", "AXP", "SCHW", "MET"],
    "Consumer Defensive": ["PG", "KO", "PEP", "CL", "MO", "KMB", "KR", "HSY"],
    "Consumer Cyclical": ["WMT", "COST", "MCD", "HD", "NKE", "SBUX", "TGT", "LOW"],
    "Industrials": ["CAT", "BA", "GE", "HON", "RTX", "UNP", "UPS", "LMT", "CMI", "PH"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "ECL", "MPC", "OXY", "VLO"],
    "Utilities": ["NEE", "DU", "ED", "DUK", "WEC", "AEP", "SRE", "SO", "EXC"],
    "Real Estate": ["PLD", "AMT", "O", "RE", "SPG", "EQIX", "WPC", "CCI"],
    "Communication Services": ["GOOGL", "CMCSA", "T", "DIS", "NFLX", "META", "VZ", "TMUS"],
    "Basic Materials": ["LIN", "APD", "FCX", "NEM", "DOW", "SHW", "ECL"],
}


def normalize_sector(sector: str | None) -> str | None:
    """Normalizace názvu sektoru pro lookup (velikost písmen, čárky)."""
    if not sector:
        return None
    key = sector.strip().lower()
    for known in PEER_UNIVERSE:
        if known.lower() == key:
            return known
    # obecnější fuzzy fallback (např. "Financials" vs "Financial Services")
    for known in PEER_UNIVERSE:
        if known.split()[0].lower() == key or key in known.lower():
            return known
    return None
