#!/usr/bin/env bash
# Service de mise à jour de Maze, exécuté sur l'hôte par systemd (utilisateur ops).
#
# L'interface d'administration dépose une demande dans control/request (« check » ou « update »).
# Ce script la traite et publie le résultat dans control/ :
#   state        idle | checking | updating | success | failed
#                (l'interface affiche « interrompue » si l'état reste checking / updating alors que
#                le verrou control/.lock est libre : service arrêté sans avoir pu écrire, disque plein…)
#   info         CLÉ=valeur (current, available, branch, checked_at, check_message,
#                started_at, finished_at, message)
#   commits.tsv  commits disponibles : hash<TAB>date<TAB>titre
#   update.log   journal de la dernière mise à jour
# Sans demande (déclenchement par le timer), il fait une simple vérification.

set -uo pipefail

main() {
    local action requested_by
    # repo, control et branch restent globales : le piège de sortie (on_exit) en a besoin.
    repo="$(cd "$(dirname "$0")/.." && pwd)"
    control="$repo/control"
    branch="${MAZE_BRANCH:-main}"
    cd "$repo" || exit 1
    umask 002

    # Une seule exécution à la fois.
    exec 9>"$control/.lock"
    flock -n 9 || { echo "Déjà en cours"; exit 0; }
    trap on_exit EXIT
    trap "exit 143" TERM INT HUP

    action="check"
    requested_by="tâche planifiée"
    if [ -f "$control/request" ]; then
        action="$(sed -n 1p "$control/request")"
        requested_by="$(sed -n 2p "$control/request")"
        rm -f "$control/request"
    fi

    case "$action" in
        check)  do_check ;;
        update) do_update ;;
        *)      set_info message "Demande inconnue : $action" ;;
    esac
}

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

set_state() {
    if printf '%s\n' "$1" > "$control/state.tmp" 2>/dev/null; then
        mv -f "$control/state.tmp" "$control/state"
    else
        rm -f "$control/state.tmp"
        return 1
    fi
}

# Remplace (ou ajoute) une clé du fichier info, de façon atomique. Si l'écriture échoue (disque
# plein), l'ancien fichier est conservé : on ne remplace jamais info par un fichier tronqué.
set_info() {
    local key="$1" value="${2//$'\n'/ }"
    if { grep -v "^$key=" "$control/info" 2>/dev/null; printf '%s=%s\n' "$key" "$value"; } > "$control/info.tmp" 2>/dev/null \
            && grep -q "^$key=" "$control/info.tmp"; then
        mv -f "$control/info.tmp" "$control/info"
    else
        rm -f "$control/info.tmp"
        return 1
    fi
}

# Sortie imprévue pendant une opération : l'état ne doit pas rester « en cours ».
on_exit() {
    case "$(cat "$control/state" 2>/dev/null)" in
        checking) set_state idle ;;
        updating)
            set_info finished_at "$(now)"
            set_info message "La mise à jour s'est arrêtée avant la fin : voir le journal."
            set_state failed ;;
    esac
}

refresh() {
    # Met à jour current / available / commits.tsv sans toucher à l'état.
    if ! git fetch --prune --quiet origin 2>"$control/fetch.err"; then
        set_info check_message "Échec de la vérification : $(tail -n1 "$control/fetch.err")"
        return 1
    fi
    set_info branch "$branch"
    set_info current "$(git rev-parse --short HEAD)"
    set_info available "$(git rev-parse --short "origin/$branch")"
    set_info checked_at "$(now)"
    if git log --format='%h%x09%cI%x09%s' "HEAD..origin/$branch" > "$control/commits.tsv.tmp" 2>/dev/null; then
        mv -f "$control/commits.tsv.tmp" "$control/commits.tsv"
    else
        rm -f "$control/commits.tsv.tmp"
    fi
    # Message tenu à jour à chaque rafraîchissement (vérification ou fin de mise à jour).
    local count
    count=$(wc -l < "$control/commits.tsv" 2>/dev/null || echo 0)
    if [ "$count" -eq 0 ]; then
        set_info check_message "Maze est à jour."
    else
        set_info check_message "$count modification(s) disponible(s)."
    fi
    return 0
}

do_check() {
    local previous
    previous="$(cat "$control/state" 2>/dev/null || echo idle)"
    set_state checking
    refresh || true
    # On conserve le résultat de la dernière mise à jour (success / failed) s'il y en a un.
    case "$previous" in success|failed) set_state "$previous" ;; *) set_state idle ;; esac
}

do_update() {
    set_state updating
    set_info started_at "$(now)"
    set_info message "Mise à jour lancée par $requested_by…"
    : > "$control/update.log"
    if "$repo/deploy/update.sh" "$branch" >> "$control/update.log" 2>&1; then
        set_info finished_at "$(now)"
        refresh || true
        set_info message "Mise à jour terminée (version $(git rev-parse --short HEAD))."
        set_state success
    else
        set_info finished_at "$(now)"
        refresh || true
        set_info message "La mise à jour a échoué : voir le journal."
        set_state failed
    fi
}

main "$@"
