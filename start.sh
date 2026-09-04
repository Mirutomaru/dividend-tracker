#!/usr/bin/env bash
# Dividend Tracker — spuštění na jeden klik.
#   ./start.sh                 → web GUI v prohlížeči (http://127.0.0.1:8742)
#   ./start.sh --port 9000     → jiný port
#   ./start.sh <divtrack ...>  → cokoliv dalšího se předá řádkovému nástroji,
#                                např. ./start.sh analyze KO JNJ nebo ./start.sh sync
set -euo pipefail
cd "$(dirname "$0")"

# První spuštění? Nainstalovat vše automaticky (idempotentní).
if [ ! -x .venv/bin/divtrack ]; then
    echo "První spuštění — instaluji aplikaci..."
    ./install.sh
fi

HOST=127.0.0.1
PORT=8742
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        --host) HOST="$2"; shift 2 ;;
        *) ARGS+=("$1"); shift ;;
    esac
done

if [ ${#ARGS[@]} -eq 0 ]; then
    # ------------------------------------------------ web GUI režim
    .venv/bin/divtrack web --host "$HOST" --port "$PORT" &
    SERVER_PID=$!
    trap 'kill $SERVER_PID 2>/dev/null || true' EXIT INT TERM

    READY=0
    for _ in $(seq 1 40); do   # max ~20 s čekání na start serveru
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            echo "CHYBA: web server skončil při startu (port $PORT je obsazený?)" >&2
            exit 1
        fi
        if .venv/bin/python -c \
            "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://$HOST:$PORT/', timeout=1).status==200 else 1)" \
            >/dev/null 2>&1; then
            READY=1
            break
        fi
        sleep 0.5
    done

    echo ""
    if [ "$READY" -eq 1 ]; then
        echo "✓ Dividend Tracker běží: http://$HOST:$PORT"
    else
        echo "⚠ Server se nespustil včas — zkus jiný port: ./start.sh --port 9000" >&2
    fi
    echo "  Ukončení: Ctrl+C"
    echo ""

    # Otevřít prohlížeč (pokud je grafické prostředí dostupné)
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "http://$HOST:$PORT/" >/dev/null 2>&1 || true
    elif command -v open >/dev/null 2>&1; then
        open "http://$HOST:$PORT/" >/dev/null 2>&1 || true
    fi

    wait "$SERVER_PID"
else
    # ------------------------------------------------ CLI režim (předání divtrack)
    exec .venv/bin/divtrack "${ARGS[@]}"
fi
