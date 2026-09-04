#!/usr/bin/env bash
# Dividend Tracker — instalace (jednorázová, idempotentní)
# Vytvoří virtuální prostředí a nainstaluje aplikaci. Bez dev nástrojů.
set -euo pipefail
cd "$(dirname "$0")"

echo "=== Dividend Tracker — instalace ==="

# 1) Python 3.10+
if ! command -v python3 >/dev/null 2>&1; then
    echo "CHYBA: není nainstalovaný python3." >&2
    echo "Debian/Ubuntu: sudo apt install python3 python3-venv" >&2
    exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "CHYBA: potřeba Python 3.10 nebo novější (nalezl: $(python3 --version 2>&1))." >&2
    exit 1
fi

# 2) Virtuální prostředí
if [ ! -d .venv ]; then
    echo "[1/3] Vytvářím virtuální prostředí (.venv)..."
    python3 -m venv .venv
else
    echo "[1/3] Virtuální prostředí už existuje — ponechávám."
fi

# 3) Instalace balíčku (bez [dev] — pro běžné použití stačí runtime závislosti)
echo "[2/3] Instaluji aplikaci a závislosti (yfinance, pandas, pydantic)..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e .

# 4) Kontrola Tkinteru (volitelné — jen pro desktop GUI divtrack-gui)
echo "[3/3] Kontroluji volitelné závislosti..."
if ! .venv/bin/python -c "import tkinter" >/dev/null 2>&1; then
    echo "      Pozn.: chybí Tkinter → desktop GUI (divtrack-gui) nebude fungovat."
    echo "             Webové GUI a CLI fungují plně. Oprava: sudo apt install python3-tk"
fi

echo ""
echo "✓ Instalace hotova!"
echo ""
echo "  Spuštění aplikace (web GUI v prohlížeči):   ./start.sh"
echo "  Řádkový nástroj:                            .venv/bin/divtrack --help"
echo ""
