#!/usr/bin/env bash
# Mise à jour de Maze sur le serveur : récupération du code, reconstruction de l'image, sauvegarde,
# redémarrage et contrôle de santé ; retour automatique à la version précédente si la nouvelle ne répond pas.
#
# Usage : ./deploy/update.sh [branche]     (branche par défaut : main)
#         FORCE=1 ./deploy/update.sh       (reconstruit même si la version en service est déjà la dernière)
#
# Codes de sortie (lus par deploy/maze-updater.sh) :
#   0  mise à jour faite, ou version en service déjà à jour
#   1  échec avant le redémarrage (code, construction, sauvegarde…) : la version précédente est toujours
#      en service, et le code (git) est remis à cette version
#   3  la nouvelle version ne répondait pas : retour automatique à la précédente (image maze:previous,
#      code au commit d'avant, base restaurée depuis la sauvegarde « -maj » si la nouvelle l'avait migrée),
#      de nouveau en service
#   4  échec du retour automatique, ou rien à quoi revenir : intervention nécessaire
#   5  retour automatique interrompu : la nouvelle version avait migré la base et sa restauration a échoué (ou
#      aucune sauvegarde « -maj ») ; Maze est laissé ARRÊTÉ plutôt que relancé sur une base qu'il ne connaît
#      pas : intervention nécessaire (marche à suivre affichée, docs/fr/guide.md « Mises à jour »)
#
# Variables : HEALTH_TIMEOUT (180 s) délai pour répondre après le démarrage, sans compter les migrations,
# elles-mêmes bornées par MIGRATION_TIMEOUT (1200 s) ; MIN_FREE_MB (1500) ; MAZE_GID (1000) ; FORCE.

set -euo pipefail

# Tout le script est dans main() : bash le lit en entier avant de l'exécuter,
# ce qui évite tout effet de bord quand « git merge » ou « git reset » ci-dessous modifient ce fichier.
main() {
cd "$(dirname "$0")/.."

BRANCH="${1:-main}"
log() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

app_ok() {
    docker compose exec -T maze python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" \
        >/dev/null 2>&1
}

# Temps total accordé à ce script, en secondes depuis son lancement (vide : pas de limite). Lancé par le service
# maze-updater (INVOCATION_ID, posé par systemd), systemd l'arrête au bout du TimeoutStartSec de l'unité
# INSTALLÉE (30 min dans deploy/systemd ; lu avec systemctl, la copie installée pouvant différer). Lancé à la
# main : aucune limite. UPDATE_BUDGET l'emporte.
service_budget() {
    if [ -n "${UPDATE_BUDGET:-}" ]; then echo "$UPDATE_BUDGET"; return 0; fi
    [ -n "${INVOCATION_ID:-}" ] || return 0
    local span us
    span=$(systemctl show -P TimeoutStartUSec maze-updater.service 2>/dev/null || true)
    case "$span" in
        infinity) return 0 ;;
        "") echo 1800; return 0 ;;
    esac
    us=$(systemd-analyze timespan "$span" 2>/dev/null | awk '$2 ~ /^[0-9]+$/ {print $2; exit}')
    case "$us" in ''|*[!0-9]*) echo 1800 ;; *) echo $((us / 1000000)) ;; esac
}

# Contrôle de santé (audit M20) : HEALTH_TIMEOUT secondes (180 par défaut) pour répondre. Avec l'argument
# « migrations » (nouvelle version), le temps des migrations ne compte pas : tant que <data>/.migration-en-cours
# existe (posé par db.migrate), le délai repart ; la migration est bornée par MIGRATION_TIMEOUT (1200 s) et, sous
# systemd, s'arrête assez tôt pour laisser ROLLBACK_RESERVE secondes (600) au retour arrière avant que systemd
# n'arrête le service (budget ci-dessus) : sinon il serait tué au milieu de la restauration. Sans l'argument
# (version précédente relancée par le retour arrière, qui n'a rien à migrer), la marque est ignorée : une marque
# périmée, laissée par la nouvelle version arrêtée en pleine migration, ne prolonge pas l'attente. Échec immédiat
# si le conteneur s'arrête ou redémarre en boucle (état restarting, exited ou dead, ou RestartCount non nul).
wait_healthy() {
    local follow="${1:-}" timeout="${HEALTH_TIMEOUT:-180}" interval="${HEALTH_INTERVAL:-2}" migrating="" cid state
    local deadline=$((SECONDS + timeout)) migration_end=$((SECONDS + ${MIGRATION_TIMEOUT:-1200})) limit=""
    if [ -n "$budget" ]; then
        limit=$((budget - ${ROLLBACK_RESERVE:-600}))
        [ "$limit" -ge "$migration_end" ] || migration_end=$limit
    fi
    while :; do
        app_ok && return 0
        cid=$(docker compose ps -a -q maze 2>/dev/null | head -n1)
        state=$(docker inspect -f '{{.State.Status}} {{.RestartCount}}' "$cid" 2>/dev/null || true)
        case "$state" in
            restarting*|exited*|dead*|*" "[1-9]*)
                echo "Le conteneur s'arrête ou redémarre en boucle (état : $state) : échec sans attendre." >&2
                return 1 ;;
        esac
        if [ "$follow" = migrations ] \
                && docker compose exec -T maze sh -c 'test -e "${MAZE_DATA_DIR:-/data}/.migration-en-cours"' >/dev/null 2>&1; then
            if [ "$SECONDS" -ge "$migration_end" ]; then
                if [ -n "$limit" ] && [ "$migration_end" = "$limit" ]; then
                    echo "Migration de la base toujours en cours après $SECONDS s de mise à jour : le reste des $budget s accordés par systemd (TimeoutStartSec) est gardé pour le retour arrière : échec." >&2
                else
                    echo "Migration de la base toujours en cours après ${MIGRATION_TIMEOUT:-1200} s : échec." >&2
                fi
                return 1
            fi
            if [ -z "$migrating" ]; then
                echo "Migration de la base en cours : attente de sa fin (le délai de santé ne court pas pendant ce temps)."
                migrating=1
            fi
            deadline=$((SECONDS + timeout))
        elif [ "$SECONDS" -ge "$deadline" ]; then
            echo "Maze ne répond pas après $timeout s." >&2
            return 1
        fi
        sleep "$interval"
    done
}

# Version de la base (PRAGMA user_version), lue dans le conteneur en service (exec) ou dans un conteneur
# jetable de l'image courante, application arrêtée (run).
SCHEMA_PY="import os, sqlite3; print(sqlite3.connect(os.path.join(os.environ.get('MAZE_DATA_DIR', '/data'), 'maze.db')).execute('PRAGMA user_version').fetchone()[0])"

# Commit complet désigné par une version (hash abrégé de MAZE_VERSION) ; vide si inconnu (« dev »…).
commit_of() {
    case "$1" in ''|*[!0-9a-f]*) return 0 ;; esac
    git rev-parse -q --verify "$1^{commit}" 2>/dev/null || true
}

# Code remis à la version en service (échec avant le redémarrage, ou retour arrière).
restore_code() {
    [ -n "$rollback_to" ] || return 0
    [ "$(git rev-parse HEAD)" = "$rollback_to" ] && return 0
    if git reset -q --keep "$rollback_to"; then
        echo "Code remis au commit $(git rev-parse --short HEAD) (version en service)."
    else
        echo "Impossible de remettre le code au commit $rollback_to (modifications locales ?) : git status" >&2
    fi
}

# Une seule mise à jour à la fois : bouton de l'administration (service maze-updater) et ./deploy/update.sh lancés
# ensemble se marcheraient dessus (étiquette maze:previous posée sur l'image de l'autre, code remis en arrière
# pendant sa construction, restauration croisée). Verrou dans .git (toujours présent, jamais remplacé par git).
lock="$(git rev-parse --git-dir)/maze-update.lock"
exec 8>>"$lock"
if ! flock -n 8; then
    echo "Une autre mise à jour est déjà en cours (verrou $lock) : rien n'est fait." >&2
    exit 1
fi

budget=$(service_budget)

if [ ! -s secrets/maze_password.txt ]; then
    echo "secrets/maze_password.txt absent ou vide : voir docs/fr/guide.md, section Déploiement." >&2
    exit 1
fi

# Espace disque : un build complet demande environ 1 Go (image + cache), plus la sauvegarde.
MIN_FREE_MB=${MIN_FREE_MB:-1500}
docker_root=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
for dir in . "$docker_root"; do
    [ -d "$dir" ] || continue
    free_mb=$(df -Pm "$dir" | awk 'NR==2 {print $4}')
    if [ "${free_mb:-0}" -lt "$MIN_FREE_MB" ]; then
        echo "Espace disque insuffisant sur $(df -P "$dir" | awk 'NR==2 {print $6}') : ${free_mb} Mo libres, ${MIN_FREE_MB} Mo nécessaires." >&2
        echo "Libérer de la place (docker builder prune -af, docker image prune -f) ou agrandir le disque, puis relancer." >&2
        exit 1
    fi
done

# Unités systemd installées différentes de celles du dépôt (copiées à la main, voir docs/fr/guide.md) :
# le signaler.
for unit in deploy/systemd/*; do
    installed="/etc/systemd/system/$(basename "$unit")"
    if [ -f "$installed" ] && ! cmp -s "$unit" "$installed"; then
        echo "Attention : $installed diffère de $unit. Réinstaller : sudo cp $PWD/deploy/systemd/maze-updater.* /etc/systemd/system/ && sudo systemctl daemon-reload" >&2
        break
    fi
done

# Version en service (MAZE_VERSION du conteneur) : c'est elle, et non le code local, qui décide si Maze est
# à jour (une sauvegarde refusée ou un build raté laissent l'ancienne version en service).
running=""
was_running=0
if app_ok; then
    was_running=1
    running=$(docker compose exec -T maze printenv MAZE_VERSION 2>/dev/null | tr -d '\r\n' || true)
fi
service_commit=$(commit_of "$running")
before=$(git rev-parse HEAD)
# Commit auquel remettre le code en cas d'échec : celui de la version en service s'il précède le code local
# (cas d'une mise à jour précédente arrêtée après le git merge), sinon le code local.
rollback_to="$before"
if [ -n "$service_commit" ] && git merge-base --is-ancestor "$service_commit" "$before" 2>/dev/null; then
    rollback_to="$service_commit"
fi

# Toute erreur avant le redémarrage : la version précédente reste en service, le code y est remis.
phase=prepare
trap 'code=$?; trap - EXIT; if [ "$code" -ne 0 ] && [ "$phase" = prepare ]; then restore_code; echo "Échec avant le redémarrage : la version en service (${running:-aucune}) est inchangée." >&2; exit 1; fi; exit "$code"' EXIT

log "Récupération du code (branche $BRANCH)"
git fetch --prune origin
git checkout -q "$BRANCH"
git merge --ff-only "origin/$BRANCH"
after=$(git rev-parse HEAD)

if [ "${FORCE:-0}" != "1" ] && [ -n "$service_commit" ] && [ "$service_commit" = "$after" ]; then
    echo "Déjà à jour ($(git rev-parse --short HEAD)) et en service. FORCE=1 pour reconstruire quand même."
    exit 0
fi
if [ -n "$running" ] && [ "$service_commit" != "$after" ] && [ "$before" = "$after" ]; then
    echo "Le code est à jour ($(git rev-parse --short HEAD)) mais la version en service est $running : reconstruction."
fi
[ "$before" != "$after" ] && git --no-pager log --oneline "$before..$after"

# Image en service, gardée sous l'étiquette maze:previous pour le retour arrière (audit M20).
previous=""
container=$(docker compose ps -q maze 2>/dev/null | head -n1 || true)
if [ "$was_running" = 1 ] && [ -n "$container" ]; then
    previous=$(docker inspect -f '{{.Image}}' "$container" 2>/dev/null || true)
    [ -z "$previous" ] || docker image tag "$previous" maze:previous
fi

log "Construction de l'image"
# Version affichée dans l'interface d'administration.
MAZE_VERSION=$(git rev-parse --short HEAD)
export MAZE_VERSION
docker compose build --pull

# Dossier partagé avec le service maze-updater (doit exister avant le démarrage,
# sinon Docker le crée en root et ni le conteneur ni le service ne peuvent y écrire).
# Groupe : celui de l'utilisateur du conteneur (gid 1000 du Dockerfile, MAZE_GID s'il diffère), et non le
# groupe d'ops : sinon le conteneur ne peut plus y déposer sa demande (bouton « Mettre à jour » en 503).
gid="${MAZE_GID:-1000}"
if [ ! -d control ]; then
    if ! install -d -m 2775 -g "$gid" control 2>/dev/null; then
        install -d -m 2775 control
        echo "Attention : control/ créé sans le groupe $gid du conteneur (ops n'en est pas membre)." >&2
        echo "La mise à jour depuis l'interface restera indisponible jusqu'à : sudo chgrp $gid $PWD/control" >&2
    fi
elif [ "$(stat -c %g control)" != "$gid" ]; then
    echo "Attention : control/ a le groupe $(stat -c %g control) et non le groupe $gid du conteneur :" >&2
    echo "la mise à jour depuis l'interface peut échouer (503). Correction : sudo chgrp $gid $PWD/control" >&2
fi
# État du service maze-updater, écrit par ops seul et monté en lecture seule dans le conteneur (audit H2).
# Normalement créé par git (dossier suivi) ; recréé ici s'il a disparu (sinon Docker le créerait en root).
[ -d status ] || install -d -m 755 status
if [ ! -w status ]; then
    echo "Attention : status/ n'est pas modifiable par $(id -un) : le service maze-updater ne pourra pas publier son état." >&2
    echo "Correction : sudo chown $(id -un): $PWD/status" >&2
fi

archive=""
schema_before=""
if [ "$was_running" = 1 ]; then
    log "Sauvegarde avant mise à jour"
    schema_before=$(docker compose exec -T maze python -c "$SCHEMA_PY" 2>/dev/null | tr -d '\r' || true)
    # Archive « -maj », qui tourne à part des sauvegardes quotidiennes (quota MAZE_BACKUP_KEEP, 3 par défaut),
    # si le Maze en service connaît --suffix ; sinon (version antérieure encore en service), comme avant.
    backup_help=$(docker compose exec -T maze python -m maze.backup --help 2>/dev/null || true)
    case "$backup_help" in
        *--suffix*) archive=$(docker compose exec -T maze python -m maze.backup --suffix maj | tr -d '\r' | tail -n1) ;;
        *) archive=$(docker compose exec -T maze python -m maze.backup --keep 14 | tr -d '\r' | tail -n1) ;;
    esac
    echo "$archive"
    case "$archive" in /*.tar.gz) ;; *) archive="" ;; esac
else
    log "Conteneur absent ou arrêté : pas de sauvegarde préalable"
fi

log "Redémarrage"
phase=switch
if docker compose up -d --remove-orphans; then
    log "Contrôle de santé"
    if wait_healthy migrations; then
        echo "Maze est opérationnel (version $(git rev-parse --short HEAD))."
        # Ménage : anciennes images sans étiquette, cache de build inutilisé depuis 24 h.
        # maze:previous (version d'avant) est gardée pour un retour arrière manuel.
        docker image prune -f >/dev/null || true
        docker builder prune -f --filter until=24h >/dev/null || true
        exit 0
    fi
    echo "La nouvelle version ne répond pas. Derniers journaux :" >&2
else
    echo "Le démarrage de la nouvelle version a échoué. Derniers journaux :" >&2
fi
docker compose logs --tail 50 maze >&2 || true
rollback
}

# Retour automatique à la version d'avant (audit M20) : image maze:previous, code au commit d'avant, et base
# restaurée depuis la sauvegarde « -maj » faite juste avant le redémarrage si la nouvelle version l'a migrée
# (l'ancien code ne connaît pas le nouveau schéma). La base migrée est gardée dans l'archive
# « -avant-restauration » créée par la restauration : rien de ce qui a été écrit entre-temps n'est perdu. Avec une
# image qui la connaît, la restauration est exacte (--exact : comptes, sessions et jetons de l'archive, faite
# quelques minutes avant ; ni sessions fermées ni jetons du connecteur ou de Foundry supprimés).
rollback() {
    set +e
    phase=rollback
    if [ -z "$previous" ]; then
        echo "Aucune version précédente en service avant la mise à jour : pas de retour automatique possible." >&2
        echo "Retour manuel : git checkout $rollback_to && docker compose up -d --build" >&2
        exit 4
    fi
    log "Retour automatique à la version précédente (${running:-inconnue})"
    docker compose stop maze
    restore_code
    docker image tag maze:previous maze:latest
    local db_problem="" schema_now
    schema_now=$(docker compose run --rm --no-deps -T --name "maze-retour-$$" maze python -c "$SCHEMA_PY" 2>/dev/null | tr -d '\r' | tail -n1)
    exact=""
    case "$(docker compose run --rm --no-deps -T --name "maze-retour-$$" maze python -m maze.backup --help 2>/dev/null)" in
        *--exact*) exact="--exact" ;;
    esac
    if [ -n "$schema_before" ] && [ -z "$schema_now" ]; then
        # Version d'avant connue, version actuelle illisible (contre-revue du lot 2) : la nouvelle version a peut-être
        # migré la base ; l'ancien code ne doit pas tourner dessus. Maze reste arrêté, comme une restauration ratée.
        echo "Version de la base illisible après la mise à jour (avant : schéma $schema_before) : peut-être migrée par la nouvelle version." >&2
        left_stopped
    elif [ -z "$schema_before" ] || [ -z "$schema_now" ]; then
        echo "Version de la base inconnue (avant : ${schema_before:-?}, maintenant : ${schema_now:-?}) : base laissée telle quelle." >&2
        db_problem=1
    elif [ "$schema_now" != "$schema_before" ]; then
        if [ -n "$archive" ]; then
            log "Base migrée par la nouvelle version (schéma $schema_before > $schema_now) : restauration de $archive ${exact:-(complète)}"
            if ! docker compose run --rm --no-deps -T --name "maze-retour-$$" maze python -m maze.backup --restore "$archive" $exact; then
                echo "Échec de la restauration de $archive : la base est restée au schéma $schema_now." >&2
                left_stopped
            fi
        else
            echo "Base migrée par la nouvelle version (schéma $schema_before > $schema_now) et aucune sauvegarde d'avant mise à jour." >&2
            left_stopped
        fi
    else
        echo "Base non migrée (schéma $schema_now) : gardée telle quelle."
    fi
    docker compose up -d --no-build --force-recreate --remove-orphans
    if wait_healthy; then
        echo "Version précédente (${running:-inconnue}) de nouveau en service."
        if [ -n "$db_problem" ]; then
            echo "Base à vérifier : voir les messages ci-dessus. Sauvegarde d'avant mise à jour : ${archive:-aucune}." >&2
            exit 4
        fi
        exit 3
    fi
    echo "La version précédente ne répond pas non plus. Derniers journaux :" >&2
    docker compose logs --tail 50 maze >&2
    echo "Intervention nécessaire. Sauvegarde d'avant mise à jour : ${archive:-aucune}." >&2
    exit 4
}

# Base migrée par la nouvelle version (ou dont la version est devenue illisible) et non remise en l'état : l'ancien
# code ne doit pas tourner dessus. Maze reste arrêté (aucune interface) : l'état est dans la sortie, donc dans
# status/update.log, et maze-updater.sh le reporte dans status/ et dans le journal du service
# (journalctl -u maze-updater).
left_stopped() {
    local restore_cmd
    if [ -n "$archive" ]; then
        restore_cmd="docker compose run --rm --no-deps -T maze python -m maze.backup --restore $archive $exact"
    else
        restore_cmd="docker compose run --rm --no-deps -T maze python -m maze.backup --restore /data/backups/ARCHIVE_D_AVANT_LA_MISE_A_JOUR"
    fi
    cat >&2 <<EOF

ÉCHEC, INTERVENTION REQUISE : Maze est laissé ARRÊTÉ.
La base est au schéma ${schema_now:-illisible (avant la mise à jour : $schema_before)} (nouvelle version $(git rev-parse --short "$after" 2>/dev/null || echo "$after")),
que l'ancien code (${running:-inconnu}) ne connaît pas, et elle n'a pas pu être remise en l'état d'avant.
Code et image sont déjà revenus à la version précédente (maze:latest = maze:previous). Au choix, dans $PWD :
  1. Revenir à la version précédente : corriger d'abord la cause de l'échec de la restauration (message
     ci-dessus ; par exemple libérer de la place), puis restaurer à la main la sauvegarde d'avant mise à jour
     et redémarrer :
       $restore_cmd
       docker compose up -d --no-build
     (archives disponibles : docker compose run --rm --no-deps -T maze ls -l /data/backups)
  2. Ou relancer la nouvelle version, sur la base migrée (après avoir compris l'échec, journaux ci-dessus) :
       git reset --keep $after && MAZE_VERSION=$(git rev-parse --short "$after" 2>/dev/null) docker compose up -d --build
EOF
    exit 5
}

main "$@"
