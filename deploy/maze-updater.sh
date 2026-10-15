#!/usr/bin/env bash
# Service de mise à jour de Maze, exécuté sur l'hôte par systemd (utilisateur ops).
#
# Deux dossiers, à sens unique (audit H2) :
#   control/  boîte aux lettres partagée en écriture avec le conteneur, qui n'y dépose que sa demande :
#             control/request (« check » ou « update », puis le nom du demandeur). Ce script n'y écrit jamais :
#             il en retire la demande par un renommage (qui ne suit pas les liens symboliques) vers status/,
#             et refuse tout ce qui n'est pas un fichier ordinaire.
#   status/   écrit par ce script seul (dossier suivi par git, donc créé par ops), monté en lecture seule
#             dans le conteneur :
#     state        idle | checking | updating | success | failed | rolledback
#                  (l'interface affiche « interrompue » si l'état reste checking / updating alors que
#                  le verrou status/.lock est libre : service arrêté sans avoir pu écrire, disque plein…)
#     info         CLÉ=valeur (current, available, branch, checked_at, check_message,
#                  started_at, finished_at, message)
#     commits.tsv  commits disponibles : hash<TAB>date<TAB>titre
#     update.log   journal de la dernière mise à jour
# Sans demande (déclenchement par le timer), il fait une simple vérification.

set -uo pipefail

main() {
    local action requested_by
    # repo, control, status et branch restent globales : le piège de sortie (on_exit) en a besoin.
    repo="$(cd "$(dirname "$0")/.." && pwd)"
    control="$repo/control"
    status="$repo/status"
    branch="${MAZE_BRANCH:-main}"
    cd "$repo" || exit 1
    umask 022

    if [ -L "$status" ] || ! mkdir -p "$status"; then
        echo "Dossier $status inutilisable (lien symbolique ou création impossible)." >&2
        exit 1
    fi

    # Une seule exécution à la fois (verrou lu par l'interface pour détecter une opération interrompue).
    exec 9>"$status/.lock"
    flock -n 9 || { echo "Déjà en cours"; exit 0; }
    trap on_exit EXIT
    trap "exit 143" TERM INT HUP

    action="check"
    requested_by="tâche planifiée"
    rejected=""
    adopt_legacy
    take_request

    case "$action" in
        check)  do_check ;;
        update) do_update ;;
    esac
    [ -z "$rejected" ] || set_info message "$rejected"
}

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Déplace l'entrée $1 de control/ vers status/$2 sans jamais suivre de lien : le renommage déplace l'entrée
# elle-même (lien, dossier, tube ou fichier) dans status/, où le conteneur ne peut rien écrire ; on n'y lit
# ensuite qu'un fichier ordinaire. Code 0 : fichier ordinaire prêt à lire ; 1 : rien ; 2 : refusé (supprimé).
take() {
    local src="$control/$1" dest="$status/$2"
    [ -e "$src" ] || [ -L "$src" ] || return 1
    rm -rf -- "$dest"
    # Même système de fichiers obligatoire : sinon mv copierait le contenu (en suivant un lien posé entre-temps).
    if [ "$(stat -c %d -- "$control")" != "$(stat -c %d -- "$status")" ] || ! mv -fT -- "$src" "$dest" 2>/dev/null; then
        rm -rf -- "$src"
        return 2
    fi
    if [ -L "$dest" ] || [ ! -f "$dest" ]; then
        rm -rf -- "$dest"
        return 2
    fi
    return 0
}

# Demande déposée par le conteneur : seuls ses premiers octets sont lus, l'action est choisie dans une liste.
take_request() {
    local first second code=0
    take request .request || code=$?
    [ "$code" -ne 1 ] || return 0
    if [ "$code" -ne 0 ]; then
        rejected="Demande refusée : control/request n'était pas un fichier ordinaire."
        action="none"
        return 0
    fi
    first="$(head -c 512 -- "$status/.request" | sed -n 1p)"
    second="$(head -c 512 -- "$status/.request" | sed -n 2p)"
    rm -f -- "$status/.request"
    case "$first" in
        check|update) action="$first" ;;
        *) action="none"; rejected="Demande inconnue ignorée." ;;
    esac
    # Nom affiché dans l'interface : sans caractères de contrôle, 64 octets au plus.
    second="$(printf '%s' "$second" | tr -d '\000-\037\177' | head -c 64)"
    [ -z "$second" ] || requested_by="$second"
}

# Transition : les versions précédentes de ce script écrivaient leur état dans control/. Au premier passage,
# le dernier résultat y est repris (mêmes précautions que pour la demande, contenu filtré et borné) pour que
# l'interface le garde, puis control/ est vidé de ces fichiers (rm ne suit pas les liens).
adopt_legacy() {
    local name
    if [ ! -e "$status/state" ] && [ ! -e "$status/info" ]; then
        if take state .legacy; then
            case "$(head -c 32 -- "$status/.legacy" | sed -n 1p)" in
                success|failed) head -c 32 -- "$status/.legacy" | sed -n 1p > "$status/state" ;;
            esac
        fi
        if take info .legacy; then
            head -c 65536 -- "$status/.legacy" | tr -d '\000-\011\013-\037\177' \
                | grep -E '^(current|available|branch|checked_at|check_message|started_at|finished_at|message)=' \
                > "$status/info"
        fi
        take update.log .legacy && head -c 1048576 -- "$status/.legacy" > "$status/update.log"
        take commits.tsv .legacy && head -c 65536 -- "$status/.legacy" > "$status/commits.tsv"
        rm -f -- "$status/.legacy"
    fi
    for name in state info commits.tsv update.log fetch.err .lock state.tmp info.tmp commits.tsv.tmp; do
        rm -f -- "$control/$name" 2>/dev/null
    done
    return 0
}

set_state() {
    if printf '%s\n' "$1" > "$status/state.tmp" 2>/dev/null; then
        mv -f "$status/state.tmp" "$status/state"
    else
        rm -f "$status/state.tmp"
        return 1
    fi
}

# Remplace (ou ajoute) une clé du fichier info, de façon atomique. Si l'écriture échoue (disque
# plein), l'ancien fichier est conservé : on ne remplace jamais info par un fichier tronqué.
set_info() {
    local key="$1" value="${2//$'\n'/ }"
    if { grep -v "^$key=" "$status/info" 2>/dev/null; printf '%s=%s\n' "$key" "$value"; } > "$status/info.tmp" 2>/dev/null \
            && grep -q "^$key=" "$status/info.tmp"; then
        mv -f "$status/info.tmp" "$status/info"
    else
        rm -f "$status/info.tmp"
        return 1
    fi
}

# Sortie imprévue pendant une opération : l'état ne doit pas rester « en cours ».
on_exit() {
    case "$(cat "$status/state" 2>/dev/null)" in
        checking) set_state idle ;;
        updating)
            set_info finished_at "$(now)"
            set_info message "La mise à jour s'est arrêtée avant la fin : voir le journal."
            set_state failed ;;
    esac
}

refresh() {
    # Met à jour current / available / commits.tsv sans toucher à l'état.
    if ! git fetch --prune --quiet origin 2>"$status/fetch.err"; then
        set_info check_message "Échec de la vérification : $(tail -n1 "$status/fetch.err")"
        return 1
    fi
    set_info branch "$branch"
    set_info current "$(git rev-parse --short HEAD)"
    set_info available "$(git rev-parse --short "origin/$branch")"
    set_info checked_at "$(now)"
    if git log --format='%h%x09%cI%x09%s' "HEAD..origin/$branch" > "$status/commits.tsv.tmp" 2>/dev/null; then
        mv -f "$status/commits.tsv.tmp" "$status/commits.tsv"
    else
        rm -f "$status/commits.tsv.tmp"
    fi
    # Message tenu à jour à chaque rafraîchissement (vérification ou fin de mise à jour).
    local count
    count=$(wc -l < "$status/commits.tsv" 2>/dev/null || echo 0)
    if [ "$count" -eq 0 ]; then
        set_info check_message "Maze est à jour."
    else
        set_info check_message "$count modification(s) disponible(s)."
    fi
    return 0
}

do_check() {
    local previous
    previous="$(cat "$status/state" 2>/dev/null || echo idle)"
    set_state checking
    refresh || true
    # On conserve le résultat de la dernière mise à jour (success / failed / rolledback) s'il y en a un.
    case "$previous" in success|failed|rolledback) set_state "$previous" ;; *) set_state idle ;; esac
}

# Résultat d'une mise à jour : état et message dans status/, et dans le journal du service (journalctl -u
# maze-updater), seul endroit lisible quand Maze est arrêté.
result() {
    set_info message "$2"
    set_state "$1"
    echo "Résultat ($1) : $2"
    if [ "$1" != success ]; then
        tail -n 25 "$status/update.log" 2>/dev/null
    fi
}

# Codes de sortie de deploy/update.sh : voir son en-tête.
do_update() {
    local code=0
    set_state updating
    set_info started_at "$(now)"
    set_info finished_at ""
    set_info message "Mise à jour lancée par $requested_by…"
    : > "$status/update.log"
    "$repo/deploy/update.sh" "$branch" >> "$status/update.log" 2>&1 || code=$?
    set_info finished_at "$(now)"
    refresh || true
    case "$code" in
        0) result success "Mise à jour terminée (version $(git rev-parse --short HEAD))." ;;
        3) result rolledback "La nouvelle version ne répondait pas : retour automatique à la version précédente ($(git rev-parse --short HEAD)), de nouveau en service. Voir le journal." ;;
        4) result failed "La mise à jour a échoué et le retour automatique à la version précédente n'a pas abouti : Maze est peut-être arrêté. Intervention nécessaire sur le serveur : voir le journal." ;;
        5) result failed "Échec, intervention requise : la nouvelle version avait migré la base, qui n'a pas pu être remise en l'état ; Maze est laissé arrêté (l'ancien code ne connaît pas le nouveau schéma). Marche à suivre à la fin de $status/update.log (docs/fr/guide.md, Mises à jour)." ;;
        *) result failed "La mise à jour a échoué avant le redémarrage : la version précédente est toujours en service. Voir le journal." ;;
    esac
}

main "$@"
