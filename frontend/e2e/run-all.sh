#!/usr/bin/env bash
# Parcours navigateur de frontend/e2e/ (Playwright), chacun sur une instance neuve de Maze lancée par ce script :
# base vide, dossiers de contrôle et d'état vides (mise-a-jour.mjs), MAZE_VERSION=abc1234, MAZE_DEFAULT_LOCALE=fr (les
# parcours ouvrent des navigateurs en fr-FR et attendent les textes français, sauf langue.mjs et
# interface-anglaise.mjs qui ouvrent aussi un navigateur en en-US) ; dossier de données passé au
# parcours (MAZE_E2E_DATA_DIR, finitions-interface.mjs). Même commande en local,
# en recette et dans l'intégration continue (.github/workflows/ci.yml) : c'est la liste de référence des parcours.
#
# Usage : frontend/e2e/run-all.sh [parcours...]     (noms sans .mjs ; défaut : tous les *.mjs du dossier)
# Variables :
#   MAZE_STATIC_DIR  interface construite (défaut : frontend/dist ; node build.mjs dans frontend/)
#   PYTHON           interpréteur qui a les dépendances du serveur (défaut : python3)
#   MAZE_E2E_PORT    premier port des instances, sur 127.0.0.1 (défaut : 8790) ; en parallèle, ports
#                    MAZE_E2E_PORT à MAZE_E2E_PORT + MAZE_E2E_JOBS - 1
#   MAZE_E2E_JOBS    nombre de parcours lancés en même temps (défaut : nombre de processeurs, 4 au plus) ;
#                    1 : l'un après l'autre sur MAZE_E2E_PORT, sortie au fil de l'eau. En parallèle, chaque parcours
#                    a sa propre instance (port, données, contrôle, état, captures, journaux) et sa sortie est
#                    affichée d'un bloc quand il se termine.
#   NODE_PATH        dossier contenant le module playwright (par exemple « npm root -g ») ; navigateurs installés
#                    par « npx playwright install chromium » (ou PLAYWRIGHT_BROWSERS_PATH)
#   MAZE_E2E_KEEP=1  garde les données, journaux et captures même si tout passe (dossier affiché)
# Code de sortie : 0 si tous les parcours passent, 1 sinon (journal du serveur affiché pour chaque échec).
set -uo pipefail

E2E="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$E2E/../.." && pwd)"
PYTHON="${PYTHON:-python3}"
PORT="${MAZE_E2E_PORT:-8790}"
STATIC="${MAZE_STATIC_DIR:-$REPO/frontend/dist}"
if [ ! -f "$STATIC/index.html" ]; then
    echo "Interface introuvable dans $STATIC : construire d'abord (cd frontend && node build.mjs) ou MAZE_STATIC_DIR." >&2
    exit 1
fi
if [ -n "${MAZE_E2E_JOBS:-}" ]; then
    JOBS="$MAZE_E2E_JOBS"
else
    JOBS="$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
    [ "$JOBS" -gt 4 ] 2>/dev/null && JOBS=4
fi
case "$JOBS" in
    '' | *[!0-9]* | 0) echo "MAZE_E2E_JOBS doit être un entier positif (reçu : $JOBS)." >&2; exit 1 ;;
esac

if [ "$#" -gt 0 ]; then
    names=("$@")
else
    names=()
    for f in "$E2E"/*.mjs; do names+=("$(basename "$f" .mjs)"); done
fi
[ "$JOBS" -gt "${#names[@]}" ] && JOBS="${#names[@]}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/maze-e2e.XXXXXX")"
server=""
stop_server() {
    if [ -n "$server" ]; then
        kill "$server" 2>/dev/null && wait "$server" 2>/dev/null
        server=""
    fi
}

# Un parcours sur une instance neuve au port $2 : sortie du parcours, puis « === nom : OK » ou l'échec avec la fin du
# journal du serveur. Code de retour 0 si le parcours passe. $3 : texte ajouté aux lignes de résultat (durée).
run_one() {
    local name="$1" port="$2" url="http://127.0.0.1:$2" script="$E2E/$1.mjs" run="$WORK/$1" t0="$SECONDS" code
    mkdir -p "$run/data" "$run/control" "$run/status" "$run/shots"
    echo "=== $name"
    if curl -s -o /dev/null "$url/"; then   # sinon le parcours viserait une autre instance
        echo "Port $port déjà occupé : choisir d'autres ports avec MAZE_E2E_PORT." >&2
        return 1
    fi
    (
        cd "$REPO/backend" || exit 1
        # Langue par défaut française : les parcours attendent les textes français (navigateurs en fr-FR ;
        # langue.mjs et interface-anglaise.mjs vérifient l'anglais avec un navigateur en en-US).
        export MAZE_DATA_DIR="$run/data" MAZE_STATIC_DIR="$STATIC" MAZE_CONTROL_DIR="$run/control" \
            MAZE_STATUS_DIR="$run/status" MAZE_VERSION=abc1234 MAZE_DEFAULT_LOCALE=fr PYTHONDONTWRITEBYTECODE=1
        exec "$PYTHON" -m uvicorn maze.app:app_factory --factory --host 127.0.0.1 --port "$port"
    ) >"$run/server.log" 2>&1 &
    server=$!
    echo "$server" >"$run/server.pid"
    up=""
    for _ in $(seq 1 60); do
        if curl -fs -o /dev/null "$url/api/health"; then up=1; break; fi
        kill -0 "$server" 2>/dev/null || break
        sleep 0.5
    done
    if [ -z "$up" ]; then
        echo "Le serveur ne répond pas :" >&2
        tail -40 "$run/server.log" >&2
        stop_server
        return 1
    fi
    MAZE_E2E_CONTROL_DIR="$run/control" MAZE_E2E_STATUS_DIR="$run/status" MAZE_E2E_DATA_DIR="$run/data" \
        MAZE_E2E_SHOTS="$run/shots" \
        MAZE_URL="$url" node "$script" "$url" 2>&1 | tee "$run/e2e.log"
    code="${PIPESTATUS[0]}"
    local took=""
    [ -n "${3:-}" ] && took=" ($((SECONDS - t0)) s)"
    if [ "$code" = 0 ]; then
        echo "=== $name : OK$took"
    else
        echo "=== $name : ÉCHEC$took. Fin du journal du serveur :" >&2
        tail -60 "$run/server.log" >&2
        if [ "${GITHUB_ACTIONS:-}" = true ]; then   # annotation visible sur la page du run
            msg=$( (tail -25 "$run/e2e.log"; echo "--- serveur :"; tail -15 "$run/server.log") | sed 's/%/%25/g' \
                | awk 'BEGIN { ORS = "%0A" } { print }')
            echo "::error title=Parcours $name::$msg"
        fi
    fi
    stop_server
    [ "$code" = 0 ]
}

failed=()
if [ "$JOBS" -le 1 ]; then
    trap stop_server EXIT
    for name in "${names[@]}"; do
        [ -f "$E2E/$name.mjs" ] || { echo "Parcours inconnu : $name" >&2; failed+=("$name"); continue; }
        run_one "$name" "$PORT" || failed+=("$name")
    done
else
    # Emplacements 0 à JOBS-1 : l'emplacement i lance ses parcours au port PORT + i, l'un après l'autre. Sortie de
    # chaque parcours dans $WORK/<parcours>/output.log, affichée d'un bloc à la fin du parcours (ordre d'arrivée).
    echo "Parcours navigateur : ${#names[@]} parcours, $JOBS à la fois (MAZE_E2E_JOBS), ports $PORT à $((PORT + JOBS - 1))."
    slot_pid=() slot_name=()
    for ((s = 0; s < JOBS; s++)); do slot_pid[s]=""; slot_name[s]=""; done
    stop_all() {
        local s f
        for ((s = 0; s < JOBS; s++)); do
            [ -n "${slot_pid[s]}" ] || continue
            kill "${slot_pid[s]}" 2>/dev/null
            f="$WORK/${slot_name[s]}/server.pid"
            [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null
        done
    }
    trap stop_all EXIT
    next=0
    while :; do
        running=0
        for ((s = 0; s < JOBS; s++)); do
            pid="${slot_pid[s]}"
            [ -n "$pid" ] || continue
            if kill -0 "$pid" 2>/dev/null; then running=$((running + 1)); continue; fi
            wait "$pid"
            code=$?
            cat "$WORK/${slot_name[s]}/output.log"
            [ "$code" = 0 ] || failed+=("${slot_name[s]}")
            slot_pid[s]=""
        done
        for ((s = 0; s < JOBS && next < ${#names[@]}; s++)); do
            [ -z "${slot_pid[s]}" ] || continue
            name="${names[next]}"
            next=$((next + 1))
            if [ ! -f "$E2E/$name.mjs" ]; then
                echo "Parcours inconnu : $name" >&2; failed+=("$name"); s=$((s - 1)); continue
            fi
            mkdir -p "$WORK/$name"
            ( trap stop_server EXIT; run_one "$name" $((PORT + s)) duration ) >"$WORK/$name/output.log" 2>&1 &
            slot_pid[s]=$!
            slot_name[s]="$name"
            running=$((running + 1))
        done
        [ "$running" -gt 0 ] || break
        sleep 0.2
    done
fi

if [ "${#failed[@]}" -eq 0 ]; then
    echo "Parcours navigateur : ${#names[@]} sur ${#names[@]} OK."
    if [ "${MAZE_E2E_KEEP:-}" = 1 ]; then echo "Données et captures : $WORK"; else rm -rf "$WORK"; fi
    exit 0
fi
echo "Parcours en échec : ${failed[*]} (données, journaux et captures : $WORK)" >&2
exit 1
