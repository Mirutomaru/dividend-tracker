"""Daňové sazby na dividendy — čistý (net) yield po zdanění.

Dividendy podléhají zadržovací dani ve zemi původu emitenta. Pro rezidenta ČR
se uplatňuje smluvní sazba (typicky 15 %), u US akcií po předložení formuláře
W-8BEN. Sazby jsou **odhad** — pro přesné daňové poradenství se vždy poraďte
s daňovým poradcem; hodnoty lze kdykoli přepsat nastavením v DB
(``withholding_<ZEME>``, např. ``withholding_US = 15``).

Vše běží offline — žádné externí volání, jen země z lokální DB + tabulka sazeb.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

#: Smluvní sazby zadržovací daně pro rezidenta ČR (odhad, %).
#: Země v tabulce se normalizují na uppercase ISO-2 kód.
WITHHOLDING_RATES: dict[str, float] = {
    "US": 15.0,   # US–CZ/EU smlouva (W-8BEN)
    "CA": 15.0,   # CZ–Kanada
    "GB": 0.0,    # UK od 2016 neuplatňuje zadržovací daň na dividendy
    "DE": 26.0,   # CZ–Německo (dle zadání; při splnění podmínek smlouvy lze přepsat na 15 %)
    "FR": 12.8,   # CZ–Francie
    "NL": 15.0,   # CZ–Nizozemsko
    "IE": 15.0,   # CZ–Irsko
}


def _normalize_country(country: str | None) -> str | None:
    """Normalizuje zemi na uppercase ISO-2 kód (``"United States" → "US"``)."""
    if not country:
        return None
    c = str(country).strip().upper()
    # plné názvy zemí → ISO-2 (nejčastější v datech FMP/yfinance)
    full_names = {
        "UNITED STATES": "US",
        "USA": "US",
        "CANADA": "CA",
        "GREAT BRITAIN": "GB",
        "UK": "GB",
        "GERMANY": "DE",
        "FRANCE": "FR",
        "NETHERLANDS": "NL",
        "IRELAND": "IE",
    }
    return full_names.get(c, c)


def withholding_rate(
    country: str | None,
    overrides: dict[str, Any] | None = None,
) -> float | None:
    """Vrátí sazbu zadržovací daně (%) pro danou zemi.

    Priorita: přepsání v nastavení DB (``withholding_<ZEME>``) > vestavěná
    tabulka sazeb > ``None`` (sazba neznámá — net yield = hrubý yield).
    """
    code = _normalize_country(country)
    if not code:
        return None
    # přepsání z nastavení DB mají přednost
    if overrides:
        raw = overrides.get(f"withholding_{code}")
        if raw is not None:
            try:
                rate = float(raw)
                if 0.0 <= rate <= 100.0:
                    return round(rate, 2)
            except (TypeError, ValueError):
                log.warning("nastavení withholding_%s není číslo: %r", code, raw)
    return WITHHOLDING_RATES.get(code)


def net_yield_pct(
    gross_yield_pct: float | None,
    country: str | None,
    overrides: dict[str, Any] | None = None,
) -> float | None:
    """Čistý (net) yield po zdanění.

    ``None`` vstoupí → ``None`` ven (yield se nedá spočítat). Neznámá sazba
    znamená žádné sražení — net = hrubý (konzervativní odhad, frontend to
    označí).
    """
    if gross_yield_pct is None:
        return None
    rate = withholding_rate(country, overrides)
    if rate is None or rate <= 0.0:
        return round(float(gross_yield_pct), 2)
    return round(float(gross_yield_pct) * (1.0 - rate / 100.0), 2)


def net_amount(
    gross_amount: float | None,
    country: str | None,
    overrides: dict[str, Any] | None = None,
) -> float | None:
    """Čistá částka po zdanění (např. roční příjem pozice)."""
    if gross_amount is None:
        return None
    rate = withholding_rate(country, overrides)
    if rate is None or rate <= 0.0:
        return round(float(gross_amount), 2)
    return round(float(gross_amount) * (1.0 - rate / 100.0), 2)


__all__ = [
    "WITHHOLDING_RATES",
    "net_amount",
    "net_yield_pct",
    "withholding_rate",
]
