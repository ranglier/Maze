#!/usr/bin/env bash
# Mise à jour de Maze sur le serveur : sauvegarde, récupération du code,
# reconstruction de l'image, redémarrage et contrôle de santé.
#
# Usage : ./deploy/update.sh [branche]     (branche par défaut : main)
#         FORCE=1 ./deploy/update.sh       (reconstruit même sans nouveau commit)

set -euo pipefail

# Tout le script est dans main() : bash le lit en entier avant de l'exécuter,
# ce qui évite tout effet de bord quand le « git merge » ci-dessous modifie ce fichier.
main() {
cd "$(dirname "$0")/.."

BRANCH="${1:-main}"
log() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

app_ok() {
    docker compose exec -T maze python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" \
        >/dev/null 2>&1
}

if [ ! -s secrets/maze_password.txt ]; then
    echo "secrets/maze_password.txt absent ou vide : voir la section Déploiement du README." >&2
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

log "Récupération du code (branche $BRANCH)"
before=$(git rev-parse --short HEAD)
git fetch --prune origin
git checkout -q "$BRANCH"
git merge --ff-only "origin/$BRANCH"
after=$(git rev-parse --short HEAD)

if [ "$before" = "$after" ] && [ "${FORCE:-0}" != "1" ] && app_ok; then
    echo "Déjà à jour ($after) et en service. FORCE=1 pour reconstruire quand même."
    exit 0
fi
[ "$before" != "$after" ] && git --no-pager log --oneline "$before..$after"

if app_ok; then
    log "Sauvegarde avant mise à jour"
    docker compose exec -T maze python -m maze.backup --keep 14
else
    log "Conteneur absent ou arrêté : pas de sauvegarde préalable"
fi

log "Construction de l'image"
# Version affichée dans l'interface d'administration.
MAZE_VERSION=$(git rev-parse --short HEAD)
export MAZE_VERSION
docker compose build --pull

# Dossier partagé avec le service maze-updater (doit exister avant le démarrage,
# sinon Docker le crée en root et ni le conteneur ni le service ne peuvent y écrire).
[ -d control ] || install -d -m 2775 control

log "Redémarrage"
docker compose up -d --remove-orphans

log "Contrôle de santé"
for _ in $(seq 1 30); do
    if app_ok; then
        echo "Maze est opérationnel (version $after)."
        # Ménage : anciennes images sans étiquette, cache de build inutilisé depuis 24 h.
        docker image prune -f >/dev/null || true
        docker builder prune -f --filter until=24h >/dev/null || true
        exit 0
    fi
    sleep 2
done

echo "Maze ne répond pas après 60 s. Derniers journaux :" >&2
docker compose logs --tail 50 maze >&2
echo "Retour à la version précédente : git checkout $before && docker compose up -d --build" >&2
exit 1
}

main "$@"
