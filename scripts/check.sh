#!/usr/bin/env bash
# Contrôles de Maze, mêmes commandes en local, en recette et dans l'intégration continue.
#
# Usage :
#   scripts/check.sh quick [réf] [--e2e parcours,...]
#       Contrôles ciblés selon les fichiers modifiés par rapport à réf (défaut : origin/main ; comparaison depuis
#       leur ancêtre commun, fichiers modifiés non commités et nouveaux fichiers non suivis compris). Pendant le
#       développement. --e2e ajoute des parcours navigateur (noms sans .mjs, séparés par des virgules).
#   scripts/check.sh full
#       Tout, avant la demande de fusion : ruff (commande de la CI), suite du serveur (modules en parallèle),
#       construction de l'interface, tests node avec MAZE_TEST_BROWSER=1, tous les parcours navigateur en parallèle.
#
# Règles de quick (chaque fichier modifié ajoute des contrôles ; la liste et la raison sont affichées) :
#   backend/maze/X.py              tests/test_X.py s'il existe, les tests qui importent maze.X (recherche simple :
#                                  « maze.X » ou « from maze import ... X » sur une ligne), ruff sur le fichier
#   backend/maze/foundry*          test_foundry et frontend/test/foundry-module.test.mjs (module Foundry compris)
#   backend/maze/migrations/*      test_migrations (numérotation, montée de version des bases)
#   backend/maze/i18n/**, frontend/src/i18n/**
#                                  test_i18n et frontend/test/i18n.test.mjs
#   backend/tests/test_X.py        ce module, les modules de test qui l'importent (tests.test_X), ruff
#   backend/tests/fixtures/D/...   les modules de test qui citent D
#   backend/requirements.txt       toute la suite du serveur
#   frontend/src/**, frontend/{build,i18n-catalogs,schema-fingerprint}.mjs, index.html, public/**, package*.json
#                                  construction de l'interface et tous les tests node (rapides)
#   frontend/test/**               tous les tests node
#   frontend/e2e/X.mjs             construction et parcours X
#   deploy/**, docker-compose.yml, Dockerfile
#                                  test_deploy
#   README.md, docs/**             test_docs (variables du tableau Configuration)
#   *.sh                           bash -n (syntaxe)
#   autre fichier                  rien (signalé)
#
# Variables (mêmes conventions que frontend/e2e/run-all.sh) :
#   PYTHON           interpréteur qui a les dépendances du serveur (défaut : python3)
#   RUFF             commande ruff (défaut : ruff ; la CI épingle sa version)
#   NODE_PATH        dossier du module playwright (npm root -g) pour les tests node dans Chromium et les parcours
#   MAZE_NODE_PATHS  dossiers node_modules supplémentaires pour esbuild (défaut : frontend/node_modules)
#   MAZE_STATIC_DIR  interface construite (défaut : frontend/dist)
#   MAZE_BUILD_CMD   commande de construction de l'interface (défaut : node build.mjs dans frontend/, qui écrit
#                    frontend/dist) ; si elle écrit ailleurs, MAZE_STATIC_DIR doit désigner ce dossier
#   MAZE_TEST_JOBS   modules de test du serveur lancés en même temps (défaut : deux par processeur, 4 au plus ;
#                    1 : l'un après l'autre)
#   MAZE_E2E_JOBS, MAZE_E2E_PORT, MAZE_E2E_KEEP   transmises à frontend/e2e/run-all.sh
# Code de sortie : 0 si tout passe, 1 au moindre échec (résumé final), 2 si l'usage est incorrect.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
RUFF="${RUFF:-ruff}"
STATIC="${MAZE_STATIC_DIR:-$REPO/frontend/dist}"
export MAZE_STATIC_DIR="$STATIC" PYTHONDONTWRITEBYTECODE=1
RUFF_OPTS=(check --isolated --select F,E9,B --line-length 120)   # identique au job « Serveur » de la CI

default_jobs() {
    local n
    n="$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
    [ "$n" -gt 4 ] 2>/dev/null && n=4
    echo "$n"
}
# Tests du serveur surtout en attente (serveurs de test, délais, sous-processus) : deux modules par processeur,
# 4 au plus ; le plus long module (test_backup, une minute environ) fixe de toute façon la durée minimale.
TEST_JOBS="${MAZE_TEST_JOBS:-$(( $(default_jobs) * 2 > 4 ? 4 : $(default_jobs) * 2 ))}"
case "$TEST_JOBS" in
    '' | *[!0-9]* | 0) echo "MAZE_TEST_JOBS doit être un entier positif (reçu : $TEST_JOBS)." >&2; exit 2 ;;
esac

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

# --- Étapes et résumé ---------------------------------------------------------------------------------------------
summary=()
status=0
step() {   # step "libellé" commande... : lance, chronomètre et note le résultat
    local label="$1" t0="$SECONDS" code
    shift
    echo
    echo "##### $label"
    "$@"
    code=$?
    if [ "$code" = 0 ]; then
        summary+=("OK      $label ($((SECONDS - t0)) s)")
    else
        summary+=("ÉCHEC   $label ($((SECONDS - t0)) s)")
        status=1
    fi
    return "$code"
}
skip() { summary+=("SAUTÉ   $1"); }
finish() {
    echo
    echo "##### Résumé ($SECONDS s)"
    printf '  %s\n' "${summary[@]}"
    if [ "$status" = 0 ]; then echo "Tout est vert."; else echo "Au moins un contrôle en échec." >&2; fi
    exit "$status"
}

# --- Contrôles ----------------------------------------------------------------------------------------------------
ruff_check() { (cd "$REPO/backend" && "$RUFF" "${RUFF_OPTS[@]}" "$@"); }

build_frontend() {
    if [ -n "${MAZE_BUILD_CMD:-}" ]; then
        (cd "$REPO/frontend" && eval "$MAZE_BUILD_CMD")
    else
        (cd "$REPO/frontend" && node build.mjs)
    fi || return 1
    [ -f "$STATIC/index.html" ] || { echo "Interface construite introuvable dans $STATIC (MAZE_STATIC_DIR)." >&2; return 1; }
}

node_tests() {   # node_tests [fichiers relatifs à frontend/] ; sans argument : tous
    local files=("$@")
    [ "${#files[@]}" -gt 0 ] || files=(test/*.test.mjs)
    (cd "$REPO/frontend" && node --test "${files[@]}")
}

# Modules de test du serveur (noms test_X), chacun dans son propre processus (unittest discover -p test_X.py, comme
# la suite complète), MAZE_TEST_JOBS à la fois, les plus gros fichiers d'abord. Les tests prennent des ports libres
# (port 0) et des dossiers temporaires uniques : pas de conflit entre processus. Sortie d'un module affichée d'un bloc
# (en entier s'il échoue, sa dernière ligne sinon).
server_tests() {
    local mods=("$@") logs pid code s next=0 running total=0 failed=() m n
    if [ "${#mods[@]}" -eq 0 ]; then
        mapfile -t mods < <(cd "$REPO/backend/tests" && ls -S test_*.py | sed 's/\.py$//')
    else
        mapfile -t mods < <(cd "$REPO/backend/tests" && ls -S "${mods[@]/%/.py}" | sed 's/\.py$//')
    fi
    logs="$(mktemp -d "${TMPDIR:-/tmp}/maze-unittest.XXXXXX")"
    local jobs="$TEST_JOBS"
    [ "$jobs" -gt "${#mods[@]}" ] && jobs="${#mods[@]}"
    echo "Modules : ${mods[*]} ($jobs à la fois, MAZE_TEST_JOBS)"
    local slot_pid=() slot_mod=()
    for ((s = 0; s < jobs; s++)); do slot_pid[s]=""; slot_mod[s]=""; done
    while :; do
        running=0
        for ((s = 0; s < jobs; s++)); do
            pid="${slot_pid[s]}"
            [ -n "$pid" ] || continue
            if kill -0 "$pid" 2>/dev/null; then running=$((running + 1)); continue; fi
            wait "$pid"
            code=$?
            m="${slot_mod[s]}"
            n="$(sed -n 's/^Ran \([0-9]*\) tests\{0,1\} in .*/\1/p' "$logs/$m.log")"
            total=$((total + ${n:-0}))
            if [ "$code" = 0 ]; then
                echo "--- $m : OK, ${n:-?} tests, $(sed -n 's/^Ran [0-9]* tests\{0,1\} in \(.*\)/\1/p' "$logs/$m.log")"
            else
                echo "--- $m : ÉCHEC"
                cat "$logs/$m.log"
                failed+=("$m")
            fi
            slot_pid[s]=""
        done
        for ((s = 0; s < jobs && next < ${#mods[@]}; s++)); do
            [ -z "${slot_pid[s]}" ] || continue
            m="${mods[next]}"
            next=$((next + 1))
            (cd "$REPO/backend" && exec "$PYTHON" -m unittest discover -s tests -p "$m.py") >"$logs/$m.log" 2>&1 &
            slot_pid[s]=$!
            slot_mod[s]="$m"
            running=$((running + 1))
        done
        [ "$running" -gt 0 ] || break
        sleep 0.2
    done
    if [ "${#failed[@]}" -gt 0 ]; then
        echo "Serveur : $total tests, modules en échec : ${failed[*]} (journaux : $logs)" >&2
        return 1
    fi
    echo "Serveur : $total tests dans ${#mods[@]} modules, OK."
    rm -rf "$logs"
}

node_tests_browser() { (export MAZE_TEST_BROWSER=1; node_tests); }   # Playwright absent : erreur, pas tests sautés
e2e() { "$REPO/frontend/e2e/run-all.sh" "$@"; }

# --- full ---------------------------------------------------------------------------------------------------------
full() {
    step "Ruff" ruff_check maze tests
    step "Tests du serveur" server_tests
    if step "Construction de l'interface" build_frontend; then
        step "Tests node (MAZE_TEST_BROWSER=1)" node_tests_browser
        step "Parcours navigateur" e2e
    else
        skip "Tests node (construction en échec)"
        skip "Parcours navigateur (construction en échec)"
    fi
    finish
}

# --- quick --------------------------------------------------------------------------------------------------------
declare -A SRV=() NODE_FILES=() E2E_SET=()
NODE_ALL="" BUILD="" SRV_ALL=""
RUFF_FILES=() SH_FILES=() reasons=() ignored=()

add_srv() {   # add_srv test_X "raison" : seulement si backend/tests/test_X.py existe
    [ -f "$REPO/backend/tests/$1.py" ] || return 1
    SRV[$1]=1
    reasons+=("$2 -> $1")
}
importers() {   # importers motif-de-module (maze.X ou tests.test_X) : modules de test qui le citent
    local mod="$1" pkg="${1%%.*}" name="${1#*.}"
    (cd "$REPO/backend/tests" && grep -lE "\\b${pkg}\\.${name}\\b|from ${pkg} import [A-Za-z0-9_, ]*\\b${name}\\b" test_*.py \
        2>/dev/null | sed 's/\.py$//')
}

select_for() {
    local f="$1" stem t
    case "$f" in
        backend/maze/migrations/*)
            add_srv test_migrations "$f (migration)" ;;
        backend/maze/i18n/*)
            add_srv test_i18n "$f (catalogue)"
            NODE_FILES[test/i18n.test.mjs]=1; reasons+=("$f (catalogue) -> frontend/test/i18n.test.mjs") ;;
    esac
    case "$f" in
        backend/maze/foundry*)
            add_srv test_foundry "$f (Foundry)"
            NODE_FILES[test/foundry-module.test.mjs]=1; reasons+=("$f (Foundry) -> frontend/test/foundry-module.test.mjs") ;;
    esac
    case "$f" in
        backend/maze/*.py)
            stem="$(basename "$f" .py)"
            [ -f "$REPO/$f" ] && RUFF_FILES+=("${f#backend/}")
            add_srv "test_$stem" "$f (test du même nom)"
            for t in $(importers "maze.$stem"); do add_srv "$t" "$f (importé par le test)"; done ;;
        backend/tests/fixtures/*)
            stem="${f#backend/tests/fixtures/}"; stem="${stem%%/*}"
            for t in $(cd "$REPO/backend/tests" && grep -lF "$stem" test_*.py | sed 's/\.py$//'); do
                add_srv "$t" "$f (donnée de test citée)"
            done ;;
        backend/tests/*.py)
            stem="$(basename "$f" .py)"
            [ -f "$REPO/$f" ] && RUFF_FILES+=("${f#backend/}")
            add_srv "$stem" "$f (module de test)"
            for t in $(importers "tests.$stem"); do [ "$t" = "$stem" ] || add_srv "$t" "$f (importé par le test)"; done ;;
        backend/requirements.txt)
            SRV_ALL=1; reasons+=("$f -> toute la suite du serveur") ;;
        frontend/src/i18n/*)
            add_srv test_i18n "$f (catalogue)"
            BUILD=1; NODE_ALL=1; reasons+=("$f -> construction, tous les tests node") ;;
        frontend/src/* | frontend/build.mjs | frontend/i18n-catalogs.mjs | frontend/schema-fingerprint.mjs \
            | frontend/index.html | frontend/public/* | frontend/package.json | frontend/package-lock.json)
            BUILD=1; NODE_ALL=1; reasons+=("$f -> construction, tous les tests node") ;;
        frontend/test/*)
            NODE_ALL=1; reasons+=("$f -> tous les tests node") ;;
        frontend/e2e/*.mjs)
            stem="$(basename "$f" .mjs)"
            if [ -f "$REPO/$f" ]; then BUILD=1; E2E_SET[$stem]=1; reasons+=("$f -> construction, parcours $stem"); fi ;;
        deploy/* | docker-compose.yml | Dockerfile)
            add_srv test_deploy "$f (déploiement)" ;;
        README.md | docs/*)
            add_srv test_docs "$f (documentation)" ;;
        backend/maze/*) ;;   # déjà traité ci-dessus (migrations, catalogues, module Foundry)
        *.sh) ;;
        *) ignored+=("$f") ;;
    esac
    case "$f" in
        *.sh) [ -f "$REPO/$f" ] && { SH_FILES+=("$f"); reasons+=("$f -> bash -n"); } ;;
    esac
}

quick() {
    local ref="origin/main" extra="" base f files
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --e2e) [ "$#" -ge 2 ] || usage; extra="$2"; shift 2 ;;
            --e2e=*) extra="${1#--e2e=}"; shift ;;
            -*) usage ;;
            *) ref="$1"; shift ;;
        esac
    done
    cd "$REPO" || exit 1
    base="$(git merge-base "$ref" HEAD 2>/dev/null)" || base="$(git rev-parse --verify -q "$ref^{commit}")" \
        || { echo "Référence inconnue : $ref" >&2; exit 2; }
    mapfile -t files < <({ git diff --name-only "$base"; git ls-files --others --exclude-standard; } | sort -u)
    for f in "${files[@]}"; do select_for "$f"; done
    if [ -n "$extra" ]; then
        local IFS=,
        for f in $extra; do [ -n "$f" ] && { E2E_SET[$f]=1; BUILD=1; reasons+=("--e2e -> parcours $f"); }; done
        unset IFS
    fi

    echo "Fichiers modifiés par rapport à $ref (base ${base:0:10}, non commités compris) : ${#files[@]}"
    [ "${#reasons[@]}" -gt 0 ] && printf '  %s\n' "${reasons[@]}"
    [ "${#ignored[@]}" -gt 0 ] && printf '  %s -> aucun contrôle\n' "${ignored[@]}"

    [ "${#RUFF_FILES[@]}" -gt 0 ] && step "Ruff (${#RUFF_FILES[@]} fichiers)" ruff_check "${RUFF_FILES[@]}"
    [ "${#SH_FILES[@]}" -gt 0 ] && step "Syntaxe des scripts shell" bash -c 'for f; do bash -n "$f" || exit 1; done' _ "${SH_FILES[@]}"
    if [ -n "$SRV_ALL" ]; then
        step "Tests du serveur (tous)" server_tests
    elif [ "${#SRV[@]}" -gt 0 ]; then
        mapfile -t mods < <(printf '%s\n' "${!SRV[@]}" | sort)
        step "Tests du serveur (${mods[*]})" server_tests "${mods[@]}"
    fi
    local built=1
    if [ -n "$BUILD" ]; then step "Construction de l'interface" build_frontend || built=""; fi
    if [ -n "$NODE_ALL" ]; then
        step "Tests node (tous)" node_tests
    elif [ "${#NODE_FILES[@]}" -gt 0 ]; then
        mapfile -t nf < <(printf '%s\n' "${!NODE_FILES[@]}" | sort)
        step "Tests node (${nf[*]})" node_tests "${nf[@]}"
    fi
    if [ "${#E2E_SET[@]}" -gt 0 ]; then
        mapfile -t names < <(printf '%s\n' "${!E2E_SET[@]}" | sort)
        if [ -n "$built" ]; then step "Parcours navigateur (${names[*]})" e2e "${names[@]}"
        else skip "Parcours navigateur (construction en échec)"; fi
    fi
    [ "${#summary[@]}" -gt 0 ] || echo "Rien à contrôler."
    finish
}

case "${1:-}" in
    quick) shift; quick "$@" ;;
    full) shift; [ "$#" -eq 0 ] || usage; full ;;
    *) usage ;;
esac
