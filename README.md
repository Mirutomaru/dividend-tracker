# Dividend Tracker

Profesionální tracker a valuační analyzátor **dividendových akcií**. Sledujete si ručně
vybrané tickery, aplikace stáhne fundamentální data (yfinance), spočítá kompletní sadu
metrik udržitelnosti dividendy, valuace a solvence, přiřadí skóre 0–100 s bezpečnostním
flagem, detekuje **dividendové pasti** i **nákupní zóny** a odhadne férovou cenu pomocí
Gordon Growth Modelu, two-stage DDM (pro vysoký růst) a výnosové metody. REITY se
posuzují jinak — udržitelnost měří FFO/AFFO cash coverage místo zkresleného EPS payoutu.

> ⚠️ Není investiční poradenství. Data pocházejí z veřejného zdroje (Yahoo Finance via
> `yfinance`) a mohou být nepřesné nebo se opozdit. Vždy ověřte kritická čísla ve
> výroční zprávě společnosti.

## Quick Start — na jeden klik

```bash
cd /home/ai/Prace/projekty/dividend-tracker
./start.sh          # první běh sám nainstaluje; otevře web GUI v prohlížeči
```

- Jiný port: `./start.sh --port 9000`
- Řádkové příkazy: `./start.sh analyze KO JNJ`, `./start.sh sync`, `./start.sh scan`
- Detailní návod k použití: [`NAVOD.md`](NAVOD.md)

## Funkce

- Ruční výběr tickerů — jednoduché GUI (Tkinter) i CLI
- Metriky podle specifikace:
  - **A. Dividendy & udržitelnost**: trailing/forward yield, payout ratio (EPS), FCF
    payout ratio, **FFO/AFFO payout (REIT)**, CAGR růstu dividendy (3Y/5Y/10Y), streak
    růstu výplat; historická DPS je **split-adjusted** na aktuální počet akcií
  - **B. Ocenění & valuace**: P/E TTM + forward vs odvětvový medián a 5letý historický
    průměr firmy, P/FCF, EV/EBITDA vs odvětví, FCF yield, aktuální yield vs 5Y průměr
  - **C. Zdraví & solvence**: Net Debt/EBITDA, Interest Coverage, ROIC
- **Scoring 0–100** (udržitelnost 40 + valuace 35 + kvalita/růst 25) s přepočtem vah
  napříč dostupnými daty (chybějící položka netrestá nulou)
- **Dividend Safety Flag**: zelená / žlutá / červená dle pevných prahů (payout < 65 %,
  FCF payout < 70 %, ND/EBITDA < 3.0x, interest coverage > 4.0x). REITY se posuzují
  jinak: EPS payout je ignorován a krytí dividendy měří **FFO/AFFO payout** (> 100 % =
  červená, < 75 % + nízká páka = zelená); strukturovaně záporné FCF se u REITU netrestá,
  když existuje FFO/AFFO krytí. Utility mají uvolněné pravidlo FCF payout.
- **Signály**: `BUY_ZONE_UNDERVALUED` (yield > +15 % nad 5Y průměrem + zelený flag),
  `DIVIDEND_TRAP` (yield > 8 % a FCF payout > 100 % / záporné FCF) — trap omezuje
  skóre na ≤25, červený flag na ≤39
- **Férová cena & nákupní zóna**: Gordon model `P = D1/(r−g)` (výchozí r=8 %, g = 5Y CAGR
  dividendy oříznutý) + výnosová metoda (cena, při které forward DPS dává 5Y průměrný
  výnos); zóna `[0.85·FV, FV]` a verdikt podhodnoceno/férové/přehodnoceno (±10 %).
  Pokud je 5Y CAGR ≥ požadovaná výkonnost (Gordon by divergoval), použije se **two-stage
  DDM** — fáze 1: růst = CAGR oříznutý na [0 %, +15 %] po dobu 5 let, fáze 2: terminální
  růst `min(g1/2, 3 %)`. Pokud ani ten nekonverguje (r ≤ terminální růst), spadne se na
  konzervativní ořez Gordonu.
- Normalizovaný **JSON export** reportu (schéma viz níže)
- Disková cache odvětvových benchmarků (7 dní), retry na provider chyby, strukturované logování
- **Srážková daň podle země původu** — čistý yield (`net_yield_pct`) s vestavěnými sazbami
  a přepsáním přes `divtrack portfolio tax set --country US 15`; po `sync` se v konzoli
  tiskne srovnávací tabulka watchlisty s hrubým → čistým yieldem
- **`divtrack scan`** — filtr watchlisty na akcie se zeleným Safety flagem + signálem
  BUY_ZONE (pozicní tickery / `--file` / trvalá watchlista z DB)

## Struktura projektu (Clean Architecture)

```
src/dividend_tracker/
├── models.py               # Pydantic modely: Snapshot → MetricsReport → Score/FairValue
├── domain/                 # čistá logika, bez sítě a UI (100 % pokryto testy)
│   ├── dividends.py        # agregace plateb, CAGR, streak
│   ├── metrics.py          # výpočet všech metrik A/B/C
│   ├── scoring.py          # skóre 0–100, safety flag, signály
│   └── valuation.py        # Gordon + výnosová metoda, nákupní zóna
├── data/                   # sběr dat + cache-first lokální databáze
│   ├── fetcher.py          # Snapshot sběr (yfinance), retry, cache, benchmarky sektoru
│   ├── peers.py            # peer universe pro odvětvové mediány P/E a EV/EBITDA
│   ├── db.py               # SQLite: stocks, dividends_history, dividends_calendar, quotes, api_usage
│   ├── fmp.py              # FMP klient — denní limit 250 volání s čítačem v DB (api_usage)
│   ├── quotes.py           # aktuální ceny přes yfinance → tabulka quotes (NE FMP!)
│   ├── sync.py             # cache-first synchronizace: TTL 72 h fundamenty / 7 dní dividendy
│   └── watchlist.py        # srovnávací řádky počítané přímo z DB (bez externích volání)
├── pipeline.py             # analyze_ticker() — jediný vstupní bod (CLI i GUI)
├── presentation/           # report schéma, konzolový formát
│   ├── report.py           # normalizované JSON + export
│   └── console.py          # tabulky a detailní bloky
├── gui/app.py              # Tkinter GUI (analýza v pozadí vlákne)
├── web/                    # Web GUI — lehký HTTP server na stdlib, bez závislostí
│   ├── server.py           # JobManager + JSON API (/api/analyze, /api/jobs/<id>)
│   └── static/             # index.html · style.css (Apple-style) · app.js
└── cli.py                  # divtrack analyze / sync / watch / portfolio / alert / export / scan / config / gui / web
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
divtrack analyze KO --refresh           # ignorovat disk cache snapshotů (TTL 1 den)

# cache-first synchronizace dat do lokální DB (FMP fundamenty + yfinance ceny)
divtrack sync KO JNJ MSFT               # explicitní tickery
divtrack sync                           # vše, co je už v DB
divtrack sync --force                   # ignorovat TTL a stáhnout znovu

# sken watchlisty: pouze akcie se zeleným Safety flagem + signálem BUY_ZONE
divtrack scan                           # trvalá watchlista z DB
divtrack scan KO JNJ MSFT               # explicitní tickery
divtrack scan --file watchlist.txt      # tickery ze souboru (jeden na řádek)
```

`sync` respektuje cache-first pravidla (fundamenty 72 h, dividendová historie 7 dní)
a denní limit FMP — čerstvá data se nikdy neopakují. Výstup ukáže počty podle zdroje
(cached/fmp/yfinance), stav FMP čítače a aktualizované ceny; na konci tiskne
**srovnávací tabulku watchlisty** přímo z lokální DB (bez externích volání) se sloupci
cena, **yield hrubý → čistý** (po srážkové dani podle země původu), streak růstu a
příští výplata.

`scan` projde všechny tickery ze zvoleného zdroje (pozicní argumenty > `--file` >
trvalá watchlista z DB) a vypíše jen ty, které mají **zelený Safety flag** i signál
**BUY_ZONE** (yield > +15 % nad 5Y průměrem). Chyba u jednotlivého tickeru sken
nezastaví; až když selže vše, vrací exit code 2.

Výstup `analyze`: srovnávací tabulka + detailní blok pro každý ticker (skóre, flagy,
signály, metriky vs odvětví, férová cena a nákupní zóna). Globální i subkomandový
`--verbose/-v` zapne podrobnější logování (INFO: cache hit/miss, retry).

## Použití — Web GUI

```bash
divtrack web                 # http://127.0.0.1:8742
divtrack web --port 9000     # vlastní port
```

Moderní, čisté webové rozhraní (Apple-style design) bez jakýchkoli externích
závislostí — běží na standardní knihovně Pythonu. Zadejte tickery oddělené mezerou
(nebo čárkou), klikněte „Analyzovat“ a výsledky se postupně vykreslují:

- **Srovnávací tabulka** (až 100 tickerů) — jeden řádek pod druhým, seřaditelné
  sloupce (klik na záhlaví): cena, yield, payout, růst, série, skóre, férová cena,
  nákupní zóna a „do zóny“ (o kolik % musí cena klesnout). Řádky s cenou právě v
  nákupní zóně jsou zvýrazněny zeleně. Klik na řádek přeskočí na detail.
- **Detailní karty** — skóre dividendového zdraví (0–100) s bezpečnostním flagem,
  dividendové metriky, ocenění vs. odvětvový benchmark, finanční zdraví a férová
  cena s nákupní zónou.

- Analýza běží asynchronně v pozadí (thread pool), UI se nezavěšuje.
- Podpora automatického tmavého režimu (`prefers-color-scheme`).
- Server je vázaný na `127.0.0.1` — určen pro lokální použití.

## Datová vrstva — cache-first lokální databáze

Web GUI umí pracovat i **přímo z lokální SQLite databáze** (`~/.cache/dividend-tracker/data.db`,
přesahuje restarty) bez jakéhokoli externího volání:

- **Watchlist tabulka** — srovnání všech tickerů v DB, výchozí řazení **abecedně**,
  klik na záhlaví libovolného sloupce přepíná vzestup/sestup (cena, yield, payout,
  růst 5l, série let, příští výplata…). Metriky se počítají z uložených dat.
- **Synchronizace** (tlačítko „⟳ Sync“ nebo `POST /api/sync`):
  - *Cache-first*: fundamenty se stahují jen při chybě v DB nebo starší než **72 h**,
    dividendová historie starší než **7 dní**; čerstvá data = žádné API volání.
  - *FMP* (fundamenty + dividendy, dávka 1–3 volání na ticker) s denním limitem
    **250 volání** — čítač a expirace se evidují v tabulce `api_usage`; při vyčerpání
    limitu se fundamenty přestávají tahat a čte se jen z DB.
  - *Ceny* nikdy neprocházejí FMP: aktuální ceny jde **yfinance** (bez tvrdého
    denního limitu) do tabulky `quotes`.
- **API**: `GET /api/watchlist` · `POST /api/sync {tickers, force}` ·
  `GET /api/fmp-status` (zbývající volání dnes).

### Konfigurace FMP

```bash
export FMP_API_KEY="..."        # bez klíče běží sync jen přes yfinance fallback
# volitelné: DIVTRACK_DB=/cesta/k/data.db   (výchozí ~/.cache/dividend-tracker/data.db)
```

Datové schéma (minimální, dle zadání): `stocks` (ticker, name, currency, exchange,
last_fundamental_update), `dividends_history` (ex_date, payment_date, record_date,
amount, frequency), `dividends_calendar`, `quotes` (price, change, volume, timestamp)
a `api_usage` (denní čítač volání per provider).

### Noční automatický sync (volitelné)

Doporučený režim: FMP jednou denně v noci do DB + ceny přes yfinance. Po schválení
se nainstaluje do systémového cronu (02:30, log do `~/.cache/dividend-tracker/sync.log`):

```cron
30 2 * * * cd /home/ai/Prace/projekty/dividend-tracker && .venv/bin/python -m dividend_tracker.cli sync >> ~/.cache/dividend-tracker/sync.log 2>&1
```

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
      "ffo_payout_ratio_pct": null, "affo_payout_ratio_pct": null,
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
    "required_return_pct": 8.0, "growth_assumed_pct": 4.9, "ddm_model": "gordon",
    "gordon_price": 112.3, "two_stage_price": null, "yield_based_price": 70.3,
    "fair_value": 91.3,
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
- **Split-adjustment dividend**: yfinance vrací dividendy tak, jak byly vyplaceny — po
  pozdějším splitu jsou historické per-share hodnoty na starém počtu akcií. Každá
  minulá platba se proto škáluje součinem `1/faktor` všech splitů proběhlých PO ní
  (4:1 forward = faktor 4, reverse < 1; NaN/≤0 faktory se ignorují). Bez této úpravy by
  CAGR i srovnání yieldu s aktuální cenou u firem se splity nedávaly smysl. Pokud splity
  nejsou dostupné, historie zůstává neupravená a report hlásí `data_gaps: splits_unadjusted`.
- **FFO/AFFO odhad (REIT)**: FFO ≈ NI + D&A; AFFO ≈ FFO − CapEx (bez rozlišení
  maintenance/growth capex). Hodnoty jsou orientační — reálné FFO z výroční zprávy se
  může lišit o položky jako amortizace přeměrných cen nemovitostí.
- Labely řádků ve výkazech se v provideru občas mění — fetcher zkouší více variant a
  chybějící položky hlásí do `data_gaps` místo tichého selhání.

## Testy

```bash
pytest          # domain logika (metriky, scoring, valuace) — běží offline
```
