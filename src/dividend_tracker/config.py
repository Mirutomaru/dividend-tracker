"""Konfigurace — načtení ``.env`` a sdílené výchozí cesty (bez nových závislostí).

Priorita hodnot (vyšší přebije nižší):
1. skutečná proměnná prostředí (např. nastavená v shellu)
2. soubor ``.env`` — hledá se v tomto pořadí:
   - ``./.env`` (aktuální pracovní adresář)
   - ``~/.cache/dividend-tracker/.env`` (výchozí, vedle databáze)

Podporované klíče::

    FMP_API_KEY=***        # API klíč Financial Modeling Prep
    DIVTRACK_DB=/cesta/data.db          # cesta k SQLite DB
    DIVTRACK_WATCHLIST=/cesta/watchlist.json  # cesta záložního souboru watchlisty

``.env`` formát: ``KLIC=hodnota``, komentáře ``#``, volitelné uvozovky kolem hodnoty.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Výchozí adresář pro data (DB, zálohy, .env).
DATA_DIR = Path(os.environ.get("DIVTRACK_DATA_DIR", "~/.cache/dividend-tracker")).expanduser()

#: Výchozí cesta souboru ``.env`` (vedle databáze).
DEFAULT_ENV_FILE = DATA_DIR / ".env"


def _parse_env_file(path: Path) -> dict[str, str]:
    """Vykonalý parser ``.env`` — klíč=hodnota, komentáře #, volitelné uvozovky."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        # Odstraní případný inline komentář u neuzavřených hodnot.
        if " #" in value and not (value.startswith('"') or value.startswith("'")):
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


def find_env_files() -> list[Path]:
    """Cesty k existujícím ``.env`` souborům v pořadí hledání."""
    candidates = [Path.cwd() / ".env", DEFAULT_ENV_FILE]
    return [p for p in candidates if p.is_file()]


def load_env(override: bool = False) -> list[Path]:
    """Načte ``.env`` soubory do prostředí (bez přepisování již nastavených proměnných).

    Vrátí seznam načtených souborů. Volá se na startu CLI i web serveru, aby
    konfigurace fungovala bez nutnosti ručně exportovat proměnné.
    """
    loaded: list[Path] = []
    for path in find_env_files():
        for key, value in _parse_env_file(path).items():
            if override or key not in os.environ:
                os.environ[key] = value
        loaded.append(path)
    return loaded


def write_env_value(key: str, value: str, path: Path | None = None) -> Path:
    """Zapíše/aktualizuje klíč v ``.env`` souboru (zachová ostatní řádky a komentáře)."""
    p = Path(path) if path else DEFAULT_ENV_FILE
    lines: list[str] = []
    if p.is_file():
        lines = p.read_text(encoding="utf-8").splitlines()

    replaced = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.partition("=")[0].strip()
            if k == key:
                lines[i] = f"{key}={value}"
                replaced = True
    if not replaced:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{key}={value}")

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def remove_env_value(key: str, path: Path | None = None) -> bool:
    """Odebere klíč z ``.env`` souboru. Vrátí True, pokud byl nalezen."""
    p = Path(path) if path else DEFAULT_ENV_FILE
    if not p.is_file():
        return False
    lines = p.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.partition("=")[0].strip()
            if k == key:
                found = True
                continue
        kept.append(line)
    p.write_text("\n".join(kept).rstrip("\n") + "\n", encoding="utf-8")
    return found


def mask_secret(value: str | None) -> str:
    """Zamaskuje tajnou hodnotu pro zobrazení (ukáže jen první 4 a poslední 2 znaky)."""
    if not value:
        return "(nenastaveno)"
    if len(value) <= 6:
        return "****"
    return f"{value[:4]}…{value[-2:]}"


__all__ = [
    "DATA_DIR",
    "DEFAULT_ENV_FILE",
    "find_env_files",
    "load_env",
    "mask_secret",
    "remove_env_value",
    "write_env_value",
]
