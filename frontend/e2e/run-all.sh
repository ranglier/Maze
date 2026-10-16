#!/usr/bin/env bash
# Parcours navigateur de frontend/e2e/ (Playwright), chacun sur une instance neuve de Maze lancée par ce script :
# base vide, dossiers de contrôle et d'état vides (mise-a-jour.mjs), MAZE_VERSION=abc1234, MAZE_DEFAULT_LOCALE=fr (les
# parcours ouvrent des navigateurs en fr-FR et attendent les textes français) ; dossier de données passé au
# parcours (MAZE_E2E_DATA_DIR, finitions-interface.mjs). Même commande en local,
# en recette et dans l'intégration continue (.github/workflows/ci.yml) : c'est la liste de référence des parcours.
#
# Usage : frontend/e2e/run-all.sh [parcours...]     (noms sans .mjs ; défaut : tous les *.mjs du dossier)
# Variables :
#   MAZE_STATIC_DIR  interface construite (défaut : frontend/dist ; node build.mjs dans frontend/)
#   PYTHON           interpréteur qui a les dépendances du serveur (défaut : python3)
#   MAZE_E2E_PORT    port des instances, sur 127.0.0.1 (défaut : 8790)
#   NODE_PATH        dossier contenant le module playwright (par exemple « npm root -g ») ; navigateurs installés
#                    par « npx playwright install chromium » (ou PLAYWRIGHT_BROWSERS_PATH)
#   MAZE_E2E_KEEP=1  garde les données, journaux et captures même si tout passe (dossier affiché)
# Code de sortie : 0 si tous les parcours passent, 1 sinon (journal du serveur affiché pour chaque échec).
set -uo pipefail

E2E="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$E2E/../.." && pwd)"
PYTHON="${PYTHON:-python3}"
PORT="${MAZE_E2E_PORT:-8790}"
URL="http://127.0.0.1:$PORT"
STATIC="${MAZE_STATIC_DIR:-$REPO/frontend/dist}"
if [ ! -f "$STATIC/index.html" ]; then
    echo "Interface introuvable dans $STATIC : construire d'abord (cd frontend && node build.mjs) ou MAZE_STATIC_DIR." >&2
    exit 1
fi

if [ "$#" -gt 0 ]; then
    names=("$@")
else
    names=()
    for f in "$E2E"/*.mjs; do names+=("$(basename "$f" .mjs)"); done
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/maze-e2e.XXXXXX")"
server=""
stop_server() {
    if [ -n "$server" ]; then
        kill "$server" 2>/dev/null && wait "$server" 2>/dev/null
        server=""
    fi
}
trap stop_server EXIT

failed=()
for name in "${names[@]}"; do
    script="$E2E/$name.mjs"
    [ -f "$script" ] || { echo "Parcours inconnu : $name" >&2; failed+=("$name"); continue; }
    run="$WORK/$name"
    mkdir -p "$run/data" "$run/control" "$run/status" "$run/shots"
    echo "=== $name"
    (
        cd "$REPO/backend" || exit 1
        # Langue par défaut française : les parcours attendent les textes français (navigateurs en fr-FR ;
        # langue.mjs vérifie l'anglais avec un navigateur en en-US).
        export MAZE_DATA_DIR="$run/data" MAZE_STATIC_DIR="$STATIC" MAZE_CONTROL_DIR="$run/control" \
            MAZE_STATUS_DIR="$run/status" MAZE_VERSION=abc1234 MAZE_DEFAULT_LOCALE=fr PYTHONDONTWRITEBYTECODE=1
        exec "$PYTHON" -m uvicorn maze.app:app_factory --factory --host 127.0.0.1 --port "$PORT"
    ) >"$run/server.log" 2>&1 &
    server=$!
    up=""
    for _ in $(seq 1 60); do
        if curl -fs -o /dev/null "$URL/api/health"; then up=1; break; fi
        kill -0 "$server" 2>/dev/null || break
        sleep 0.5
    done
    if [ -z "$up" ]; then
        echo "Le serveur ne répond pas :" >&2
        tail -40 "$run/server.log" >&2
        failed+=("$name")
        stop_server
        continue
    fi
    MAZE_E2E_CONTROL_DIR="$run/control" MAZE_E2E_STATUS_DIR="$run/status" MAZE_E2E_DATA_DIR="$run/data" \
        MAZE_E2E_SHOTS="$run/shots" \
        MAZE_URL="$URL" node "$script" "$URL" 2>&1 | tee "$run/e2e.log"
    if [ "${PIPESTATUS[0]}" = 0 ]; then
        echo "=== $name : OK"
    else
        echo "=== $name : ÉCHEC. Fin du journal du serveur :" >&2
        tail -60 "$run/server.log" >&2
        failed+=("$name")
        if [ "${GITHUB_ACTIONS:-}" = true ]; then   # annotation visible sur la page du run
            msg=$( (tail -25 "$run/e2e.log"; echo "--- serveur :"; tail -15 "$run/server.log") | sed 's/%/%25/g' \
                | awk 'BEGIN { ORS = "%0A" } { print }')
            echo "::error title=Parcours $name::$msg"
        fi
    fi
    stop_server
done

if [ "${#failed[@]}" -eq 0 ]; then
    echo "Parcours navigateur : ${#names[@]} sur ${#names[@]} OK."
    if [ "${MAZE_E2E_KEEP:-}" = 1 ]; then echo "Données et captures : $WORK"; else rm -rf "$WORK"; fi
    exit 0
fi
echo "Parcours en échec : ${failed[*]} (données, journaux et captures : $WORK)" >&2
exit 1
