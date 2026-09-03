# Dividend Tracker

Profesionální tracker a valuační analyzátor **dividendových akcií**. Sledujete si ručně
vybrané tickery, aplikace stáhne fundamentální data (yfinance), spočítá kompletní sadu
metrik udržitelnosti dividendy, valuace a solvence, přiřadí skóre 0–100 s bezpečnostním
flagem, detekuje **dividendové pasti** i **nákupní zóny** a odhadne férovou cenu pomocí
Gordon Growth Modelu + výnosové metody.

> ⚠️ Není investiční poradenství. Data pocházejí z veřejného zdroje (Yahoo Finance via
> `yfinance`) a mohou být nepřesné nebo se opozdit. Vždy ověřte kritická čísla ve
> výroční zprávě společnosti.

## Funkce

- Ruční výběr tickerů — jednoduché GUI (Tkinter) i CLI
- Metriky podle specifikace:
  - **A. Dividendy & udržitelnost**: trailing/forward yield, payout ratio (EPS), FCF
    payout ratio, CAGR růstu dividendy (3Y/5Y/10Y), streak růstu výplat
  - **B. Ocenění & valuace**: P/E TTM + forward vs odvětvový medián a 5letý historický
    průměr firmy, P/FCF, EV/EBITDA vs odvětví, FCF yield, aktuální yield vs 5Y průměr
  - **C. Zdraví & solvence**: Net Debt/EBITDA, Interest Coverage, ROIC
- **Scoring 0–100** (udržitelnost 40 + valuace 35 + kvalita/růst 25) s přepočtem vah
  napříč dostupnými daty (chybějící položka netrestá nulou)
- **Dividend Safety Flag**: zelená / žlutá / červená dle pevných prahů (payout < 65 %,
  FCF payout < 70 %, ND/EBITDA < 3.0x, interest coverage > 4.0x; REIT/utility má
  uvolněné pravidlo FCF payout)
- **Signály**: `BUY_ZONE_UNDERVALUED` (yield > +15 % nad 5Y průměrem + zelený flag),
  `DIVIDEND_TRAP` (yield > 8 % a FCF payout > 100 % / záporné FCF) — trap omezuje
  skóre na ≤25, červený flag na ≤39
- **Férová cena & nákupní zóna**: Gordon model `P = D1/(r−g)` (výchozí r=8 %, g = 5Y CAGR
  dividendy oříznutý) + výnosová metoda (cena, při které forward DPS dává 5Y průměrný
  výnos); zóna `[0.85·FV, FV]` a verdikt podhodnoceno/férové/přehodnoceno (±10 %)
- Normalizovaný **JSON export** reportu (schéma viz níže)
- Disková cache odvětvových benchmarků (7 dní), retry na provider chyby, strukturované logování

## Struktura projektu (Clean Architecture)

```
src/dividend_tracker/
├── models.py               # Pydantic modely: Snapshot → MetricsReport → Score/FairValue
├── domain/                 # čistá logika, bez sítě a UI (100 % pokryto testy)
│   ├── dividends.py        # agregace plateb, CAGR, streak
│   ├── metrics.py          # výpočet všech metrik A/B/C
│   ├── scoring.py          # skóre 0–100, safety flag, signály
│   └── valuation.py        # Gordon + výnosová metoda, nákupní zóna
├── data/                   # adapter na yfinance (jediné místo kontaktu se světem)
│   ├── fetcher.py          # Snapshot sběr, retry, cache, benchmarky sektoru
│   └── peers.py            # peer universe pro odvětvové mediány P/E a EV/EBITDA
├── pipeline.py             # analyze_ticker() — jediný vstupní bod (CLI i GUI)
├── presentation/           # report schéma, konzolový formát
│   ├── report.py           # normalizované JSON + export
│   └── console.py          # tabulky a detailní bloky
├── gui/app.py              # Tkinter GUI (analýza v pozadí vlákne)
└── cli.py                  # divtrack analyze / divtrack gui
```

## Instalace

```bash
cd dividend-tracker
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# GUI vyžaduje Tk (Debian/Ubuntu): sudo apt install python3-tk
```

## Použití — GUI

```bash
divtrack-gui          # nebo: python -m dividend_tracker.gui.app
```

V horní liště zadejte ticker (např. `KO`), Enter / „Přidat a analyzovat“. Vlevo roste
seznam sledovaných tickerů, vpravo srovnávací tabulka (cena, yield, FCF payout, skóre,
safety flag, nákupní zóna, signál) a detailní report kliknutého řádku. „Export JSON…"
uloží všechny reporty do souboru. Analýza běží v pozadí — UI se nemrzí.

## Použití — CLI

```bash
divtrack analyze KO JNJ MSFT            # zkratka bez podpříkazu funguje taky
divtrack analyze KO --json report.json  # export do JSON
divtrack analyze KO --required-return 9.5   # vlastní požadovaná výkonnost pro Gordon model
```

Výstup: srovnávací tabulka + detailní blok pro každý ticker (skóre, flagy, signály,
metriky vs odvětví, férová cena a nákupní zóna).

## JSON schéma reportu (výběr)

```jsonc
{
  "schema_version": "1.0",
  "ticker": "KO",
  "company": {"name": "...", "sector": "Consumer Defensive", "industry": "..."},
  "price_current": 63.4,
  "metrics": {
    "dividend": {
      "yield_trailing_pct": 7.1, "yield_forward_pct": 7.2, "dps_ttm": 4.5,
      "payout_ratio_eps_pct": 52.0, "fcf_payout_ratio_pct": 61.0,
      "growth_cagr_3y_pct": 4.8, "growth_cagr_5y_pct": 4.9, "growth_cagr_10y_pct": 5.0,
      "dividend_streak_years": 27
    },
    "valuation": {
      "pe_ttm": 23.1, "sector_median_pe_ttm": 24.0, "own_5y_median_pe_approx": 26.4,
      "p_fcf": 18.2, "ev_ebitda": 9.8, "sector_median_ev_ebitda": 12.0,
      "fcf_yield_pct": 5.5, "dividend_yield_5y_avg_pct": 6.4,
      "yield_vs_5y_avg_deviation_pct": 10.9
    },
    "health": {"net_debt_to_ebitda": 2.4, "interest_coverage": 8.7, "roic_pct": 12.3}
  },
  "score": {
    "applicable": true, "total_0_100": 78.5, "safety_flag": "yellow",
    "signals": [], "breakdown": {"sustainability_pct": 84.0, "valuation_pct": 72.0,
    "quality_growth_pct": 79.0}, "reasons": ["..."]
  },
  "fair_value": {
    "required_return_pct": 8.0, "growth_assumed_pct": 4.9,
    "gordon_price": 112.3, "yield_based_price": 70.3, "fair_value": 91.3,
    "buy_zone_low": 77.6, "buy_zone_high": 91.3,
    "verdict": "undervalued", "verdict_display": "Podhodnoceno (cena < 90 % FV)"
  },
  "data_gaps": ["interest_expense"]
}
```

## Metodologické poznámky a omezení

- **Fiskální rok ≈ kalendářní rok** data výplaty dividend; neúplný aktuální rok se
  vynechává ze streaku/CAGR (aby nedal falešný pokles).
- **P/E historie firmy** je aproximace (cena za rok / aktuální EPS_TTM) — reálná P/E
  historie by potřebovala historické zisku; hodnota slouží jen jako referenční bod.
- Ovětvové benchmarky = mediány přes likvidní US large-cap peer universe
  (`data/peers.py`, ~8–10 tickerů na sektor), cache 7 dní.
- `FCF payout` používá roční „dividends paid“ z CF výkazu; u firem s nepravidelným
  cashflow může mírně kolísat mezi lety.
- Labely řádků ve výkazech se v provideru občas mění — fetcher zkouší více variant a
  chybějící položky hlásí do `data_gaps` místo tichého selhání.

## Testy

```bash
pytest          # domain logika (metriky, scoring, valuace) — běží offline
```
