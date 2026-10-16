"""Sauvegarde à chaud et restauration : base + médias dans une archive.

Usage (dans le conteneur) :
  python -m maze.backup [--keep 14]          archive dans $MAZE_DATA_DIR/backups/maze-AAAAMMJJ-HHMMSS.tar.gz
  python -m maze.backup --suffix maj         archive maze-AAAAMMJJ-HHMMSS-maj.tar.gz (rotation à part)
  python -m maze.backup --restore ARCHIVE    restaure (après une sauvegarde de l'état actuel)

Rotation : chaque nature d'archive (suffixe du nom) tourne séparément, sur la date et le numéro du nom. Archives
gardées par nature (par défaut, réglables par MAZE_BACKUP_KEEP, ex. « quotidienne=30,maj=5 » ; --keep l'emporte) :
  (sans suffixe)        quotidienne : sauvegarde quotidienne (timer maze-backup), 14
  -manuelle             bouton « Sauvegarder maintenant » / « Exporter » de l'administration : 3
  -maj                  avant chaque mise à jour (deploy/update.sh) : 3
  -avant-restauration   état d'avant chaque restauration : 3, jamais l'archive en cours de restauration
  -import               archives importées dans l'administration : 2
Une sauvegarde ou une restauration est refusée (SpaceError) si le volume n'a pas deux fois la taille de la dernière
archive plus SPACE_MARGIN de libre.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import db
from .i18n import msg
from .mediafiles import FILE_NAME, is_temp
from .config import Settings, load_settings

ARCHIVE_PATTERN = "maze-*.tar.gz"
# Archive en cours d'écriture : maze-AAAAMMJJ-HHMMSS[...].tar.part, hors du motif des archives (ni listée, ni
# restaurable, ni comptée dans la rotation) ; renommée en .tar.gz seulement une fois complète.
PART_PATTERN = "maze-*.tar.part"
PART_MIN_AGE = 60   # secondes : un .part plus récent peut être en train de naître (verrou pas encore pris)
# maze-AAAAMMJJ-HHMMSS[-import|-avant-restauration|-manuelle|-maj][-2].tar.gz
ARCHIVE_NAME = re.compile(r"^maze-\d{8}-\d{6}(?:-[a-z0-9-]{1,40})?\.tar\.gz$")
# Date, nature (lettres et tirets, vide pour les quotidiennes) et numéro (2e archive dans la même seconde).
ARCHIVE_PARTS = re.compile(r"^maze-(\d{8}-\d{6})(?:-([a-z]+(?:-[a-z]+)*?))?(?:-(\d+))?\.tar\.gz$")
NATURE = re.compile(r"^[a-z]+(?:-[a-z]+)*$")
log = logging.getLogger("maze")
# Archives gardées par nature, par défaut (MAZE_BACKUP_KEEP les remplace, nature par nature). Chaque archive est une
# copie complète de la base et des images : hors des quotidiennes (l'historique), on garde peu. 3 avant mise à jour
# suffisent à revenir sur les dernières (update.sh les fait en série) ; 3 manuelles, pour les instantanés faits à
# la main ; 3 avant restauration, pour annuler la dernière restauration ou revenir sur deux essais successifs ;
# 2 importées, puisqu'une archive importée l'est pour être restaurée aussitôt et que l'original existe ailleurs.
# Total par défaut : 25 archives (14 + 3 + 3 + 3 + 2).
DEFAULT_KEEP = {"quotidienne": 14, "manuelle": 3, "maj": 3, "avant-restauration": 3, "import": 2}
# Dernière restauration réussie (dans <data>/backups/) : nom de son archive de sécurité et comptes qu'elle a
# retirés (présents avant, absents de la base restaurée). Restaurer ensuite exactement cette archive de sécurité
# annule la restauration : ces comptes-là reviennent tels qu'ils étaient, sans être désactivés.
LAST_RESTORE = ".derniere-restauration"
# Marge en plus de deux fois la dernière archive (base temporaire, journal SQLite, extraction).
SPACE_MARGIN = 200 * 1024 * 1024
_restore_lock = threading.Lock()

# Restauration en cours (audit M7, DATA-4) : fichier verrouillé (flock) pendant toute la restauration, depuis
# l'administration comme en ligne de commande, dans un autre processus ou un conteneur jetable qui partage le
# volume. Le serveur (collab.Hub) refuse alors les écritures temps réel et ferme les salles d'édition ; les
# navigateurs se reconnectent après, voient la nouvelle époque et rechargent la fiche restaurée. Un fichier
# laissé par une restauration interrompue (verrou libre) est ignoré.
RESTORE_MARKER = ".restauration-en-cours"
# En ligne de commande : délai laissé aux écritures temps réel commencées avant le fichier (quelques ms).
RESTORE_GRACE = 1.0


@contextmanager
def restore_marker(data_dir: Path) -> Iterator[None]:
    """Verrou de RESTORE_MARKER le temps du bloc. Déjà tenu par une autre restauration (autre processus : le
    verrou _restore_lock ne vaut que dans celui-ci) : ArchiveError tout de suite. Attendre ce verrou ferait
    suivre une seconde restauration sur un fichier que la première supprime en finissant : le serveur ne verrait
    plus de restauration en cours pendant la seconde."""
    path = data_dir / RESTORE_MARKER
    deadline = time.monotonic() + 1.0
    while True:
        handle = open(path, "a", encoding="utf-8")  # "a" : ne vide pas le fichier d'une restauration en cours
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            # restore_in_progress (serveur) tient un instant un verrou partagé à chaque trame temps réel : on
            # réessaie pendant une seconde avant de conclure qu'une autre restauration le tient.
            if time.monotonic() < deadline:
                time.sleep(0.02)
                continue
            raise ArchiveError("Une restauration est déjà en cours") from None
        try:
            same = os.stat(path).st_ino == os.fstat(handle.fileno()).st_ino
        except FileNotFoundError:
            same = False
        if same:
            break
        handle.close()  # fichier supprimé par une restauration qui vient de finir : verrou à reprendre
    with handle:
        handle.truncate(0)
        handle.write(f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}\n")
        handle.flush()
        try:
            yield
        finally:
            path.unlink(missing_ok=True)


def restore_in_progress(data_dir: Path) -> bool:
    """Vrai si une restauration tient le verrou de RESTORE_MARKER (appelé à chaque trame temps réel)."""
    try:
        handle = open(data_dir / RESTORE_MARKER, encoding="utf-8")
    except OSError:
        return False
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False
# Seuls éléments acceptés sous media/ : les dossiers media/ et media/<campagne>/, et les fichiers que Maze
# produit, media/<campagne>/<nom>.<webp|jpg> (liste unique : mediafiles ; jamais de HTML ni de script).
MEDIA_DIR = re.compile(r"^media(?:/\d+)?/?$")
MEDIA_FILE = re.compile(rf"^media/\d+/{FILE_NAME}$")


class ArchiveError(Exception):
    """Archive illisible ou qui n'est pas une sauvegarde Maze."""


class SpaceError(ArchiveError):
    """Pas assez de place sur le volume pour une sauvegarde ou une restauration (507 dans l'API)."""


@dataclass(frozen=True)
class RestoreResult:
    safety: Path                                        # archive « -avant-restauration »
    disabled: list[str] = field(default_factory=list)   # comptes de l'archive restaurés désactivés


def new_archive_path(target_dir: Path, suffix: str = "") -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"maze-{stamp}{'-' + suffix if suffix else ''}"
    # Deux sauvegardes dans la même seconde : numéro suivant le plus grand déjà pris (archive ou .part), jamais un
    # numéro libéré par la rotation, qui rangerait la nouvelle archive avant les autres (et la ferait supprimer).
    taken = re.compile(rf"^{re.escape(base)}(?:-(\d+))?\.tar\.(?:gz|part)$")
    numbers = [int(m.group(1) or 1) for p in target_dir.glob(f"{base}*") if (m := taken.match(p.name))]
    if not numbers:
        return target_dir / f"{base}.tar.gz"
    return target_dir / f"{base}-{max(numbers) + 1}.tar.gz"


def remove_abandoned_parts(target_dir: Path) -> None:
    """Retire les archives inachevées laissées par une sauvegarde interrompue (arrêt brutal, conteneur tué).

    Une sauvegarde en cours tient un verrou (flock) sur son .part pendant toute l'écriture, y compris dans un
    autre processus (python -m maze.backup du timer quotidien pendant que le serveur sauvegarde) : seul un .part dont le
    verrou est libre, et assez ancien pour ne pas être en train de naître, est supprimé."""
    for part in target_dir.glob(PART_PATTERN):
        try:
            if time.time() - part.stat().st_mtime < PART_MIN_AGE:
                continue
            with part.open("rb") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                part.unlink()
        except OSError:  # verrou tenu (sauvegarde en cours) ou fichier déjà retiré
            continue


def _add_media(tar: tarfile.TarFile, media_dir: Path) -> None:
    """Ajoute media/ fichier par fichier : une image supprimée pendant le parcours (MJ qui retire une image ou
    remplace un fond de carte) est sautée au lieu de faire échouer toute la sauvegarde."""
    tar.add(media_dir, arcname="media", recursive=False)
    for path in sorted(media_dir.rglob("*")):
        if is_temp(path.name):
            continue   # image en cours d'écriture (imaging._save) : pas encore une image de Maze
        try:
            tar.add(path, arcname=f"media/{path.relative_to(media_dir).as_posix()}", recursive=False)
        except FileNotFoundError:
            continue


def archive_nature(name: str) -> str | None:
    """Nature d'une archive d'après son nom ("" : quotidienne) ; None si le nom n'est pas celui d'une archive."""
    match = ARCHIVE_PARTS.match(name)
    return (match.group(2) or "") if match else None


def sort_key(path: Path) -> tuple[str, int, str]:
    """Ordre chronologique : date du nom, puis numéro (« -2 » après l'archive sans numéro de la même seconde)."""
    match = ARCHIVE_PARTS.match(path.name)
    if not match:
        return ("", 0, path.name)
    return (match.group(1), int(match.group(3) or 1), path.name)


def keep_counts() -> dict[str, int]:
    """Archives gardées par nature : DEFAULT_KEEP, remplacés par MAZE_BACKUP_KEEP (« nature=nombre » séparés par des
    virgules, « quotidienne » pour les archives sans suffixe, 0 = toutes) ; un élément invalide est ignoré et
    signalé dans le journal."""
    counts = dict(DEFAULT_KEEP)
    for item in (p.strip() for p in os.environ.get("MAZE_BACKUP_KEEP", "").split(",")):
        if not item:
            continue
        name, sep, value = (x.strip() for x in item.partition("="))
        if sep and NATURE.match(name) and value.isdigit():
            counts[name] = int(value)
        else:
            log.warning("MAZE_BACKUP_KEEP : « %s » ignoré (attendu nature=nombre, ex. maj=3)", item)
    return counts


def keep_for(nature: str) -> int:
    """Archives gardées pour cette nature ("" : quotidienne) ; 0 (toutes) pour une nature sans quota."""
    return keep_counts().get(nature or "quotidienne", 0)


def free_space(path: Path) -> int:
    return shutil.disk_usage(path).free


def latest_size(cfg: Settings) -> int:
    """Taille de la dernière archive ; sans archive, celle de la base et des images (estimation)."""
    archives = sorted((cfg.data_dir / "backups").glob(ARCHIVE_PATTERN), key=sort_key)
    if archives:
        return archives[-1].stat().st_size
    size = cfg.db_path.stat().st_size if cfg.db_path.exists() else 0
    if cfg.media_dir.exists():
        size += sum(p.stat().st_size for p in cfg.media_dir.rglob("*") if p.is_file())
    return size


def space_needed(cfg: Settings, extra: int = 0) -> int:
    return 2 * max(latest_size(cfg), extra) + SPACE_MARGIN


# Refus faute de place, renvoyé à l'administration (traduit à la réponse, maze.i18n) : une phrase par opération.
SPACE_MESSAGES = {
    "sauvegarde": "Espace disque insuffisant pour une sauvegarde : {free} Mo libres sur le volume, {needed} Mo nécessaires (deux fois la taille de la dernière archive, plus {margin} Mo). Supprimez d'anciennes archives (Administration > Sauvegardes) ou agrandissez le volume.",  # noqa: E501
    "restauration": "Espace disque insuffisant pour une restauration : {free} Mo libres sur le volume, {needed} Mo nécessaires (deux fois la taille de la dernière archive, plus {margin} Mo). Supprimez d'anciennes archives (Administration > Sauvegardes) ou agrandissez le volume.",  # noqa: E501
}


def check_space(cfg: Settings, target_dir: Path, extra: int = 0, action: str = "sauvegarde") -> None:
    """Refuse (SpaceError) si le volume n'a pas deux fois la taille de la dernière archive (ou de `extra`, l'archive
    à restaurer) plus SPACE_MARGIN de libre : une archive à moitié écrite sur un disque plein ne sert à rien, et une
    restauration écrit une archive de sécurité puis extrait l'archive restaurée."""
    free, needed = free_space(target_dir), space_needed(cfg, extra)
    if free < needed:
        mb = 1024 * 1024
        raise SpaceError(msg(SPACE_MESSAGES[action], free=free // mb, needed=needed // mb, margin=SPACE_MARGIN // mb))


def rotate(target_dir: Path, nature: str, keep: int, protect: tuple[Path, ...] = ()) -> None:
    """Ne garde que les `keep` archives les plus récentes de cette nature (0 : toutes). Les autres natures, les
    noms inconnus et les archives de `protect` ne sont jamais supprimés."""
    if keep <= 0:
        return
    same = sorted((p for p in target_dir.glob(ARCHIVE_PATTERN) if archive_nature(p.name) == nature), key=sort_key)
    kept = {p.name for p in protect}
    for old in same[:-keep]:
        if old.name not in kept:
            old.unlink(missing_ok=True)


def backup(keep: int | None, cfg: Settings | None = None, suffix: str = "") -> Path:
    """Nouvelle archive de nature `suffix` ("" : quotidienne), puis rotation de cette nature seule (`keep` archives,
    None : quota de la nature, keep_for)."""
    if suffix and not NATURE.match(suffix):
        raise ValueError(f"Nature d'archive invalide : {suffix}")
    cfg = cfg or load_settings()
    target_dir = cfg.data_dir / "backups"
    target_dir.mkdir(exist_ok=True)
    remove_abandoned_parts(target_dir)
    check_space(cfg, target_dir)

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "maze.db"
        src = sqlite3.connect(cfg.db_path)
        dst = sqlite3.connect(snapshot)
        with dst:
            src.backup(dst)  # API de sauvegarde SQLite : cohérente même en cours d'écriture
        src.close()
        dst.close()
        # Écrite sous un nom hors du motif des archives, puis renommée une fois complète : un échec (disque plein,
        # erreur d'E/S) ne laisse jamais d'archive tronquée parmi les sauvegardes. Le nom est réservé par la
        # création exclusive du .part : une autre sauvegarde de même nature dans la même seconde prend le suivant.
        while True:
            archive = new_archive_path(target_dir, suffix)
            partial = archive.with_suffix(".part")
            try:
                fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            except FileExistsError:
                continue
            if not archive.exists():  # .part libre : la sauvegarde qui le tenait a peut-être déjà renommé le sien
                break
            os.close(fd)
            partial.unlink()
        with os.fdopen(fd, "wb") as out:
            try:
                fcntl.flock(out, fcntl.LOCK_EX)  # « en cours » pour remove_abandoned_parts d'un autre processus
                with tarfile.open(str(archive), "w:gz", fileobj=out) as tar:
                    tar.add(snapshot, arcname="maze.db")
                    if cfg.media_dir.exists():
                        _add_media(tar, cfg.media_dir)
                out.flush()
                os.fsync(out.fileno())
                os.replace(partial, archive)
            except BaseException:
                partial.unlink(missing_ok=True)
                raise

    rotate(target_dir, suffix, keep_for(suffix) if keep is None else keep)
    return archive


# --- Restauration ------------------------------------------------------------------------

def _check_members(tar: tarfile.TarFile) -> None:
    has_db = False
    for member in tar:
        name = member.name.removeprefix("./")
        parts = Path(name).parts
        if member.name.startswith("/") or ".." in parts or not (member.isfile() or member.isdir()):
            raise ArchiveError(msg("Élément refusé dans l'archive : {name}", name=member.name))
        if name == "maze.db" and member.isfile():
            has_db = True
        elif not (MEDIA_FILE if member.isfile() else MEDIA_DIR).match(name):
            raise ArchiveError(msg("Élément inattendu dans l'archive : {name}", name=member.name))
    if not has_db:
        raise ArchiveError("L'archive ne contient pas de base Maze (maze.db)")


def _check_database(path: Path) -> dict[str, Any]:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ArchiveError("La base de l'archive est endommagée")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not {"campaigns", "npcs", "factions"} <= tables:
                raise ArchiveError("Ce fichier n'est pas une sauvegarde Maze")
            campaigns = conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] if "users" in tables else 0
            # Base plus récente que ce code, ou migrée par une autre branche (audit M22) : migrate la refuserait.
            problem = db.schema_problem(conn)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise ArchiveError(msg("Base illisible : {error}", error=str(exc))) from exc
    if problem:
        raise ArchiveError(msg("Sauvegarde issue d'une version plus récente de Maze (ou d'une autre branche) : mettez Maze à jour d'abord. Détail : {problem}",  # noqa: E501
                               problem=problem))
    return {"schema": version, "campaigns": campaigns, "users": users}


def inspect_archive(archive: Path) -> dict[str, Any]:
    """Vérifie qu'une archive est une sauvegarde Maze exploitable et en résume le contenu."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            _check_members(tar)
            with tempfile.TemporaryDirectory() as tmp:
                tar.extract("maze.db" if "maze.db" in tar.getnames() else "./maze.db", tmp, filter="data")
                return _check_database(Path(tmp) / "maze.db")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ArchiveError(msg("Archive illisible : {error}", error=str(exc))) from exc


def _merge_accounts(new_db: Path, current_db: Path, restorer: str | None = None,
                    keep_archive_accounts: bool = False,
                    undone: frozenset[tuple[int, str]] = frozenset()) -> tuple[list[str], list[dict[str, Any]]]:
    """Comptes de la base restaurée : ceux de l'archive, sans faire revivre ce qui a été révoqué depuis.

    - Sauvegarde antérieure aux comptes : les comptes actuels sont repris. Base en service absente (restauration
      en ligne de commande sur un volume neuf) : les comptes de l'archive, tels quels.
    - Compte présent des deux côtés (même id et même identifiant) : mot de passe, état (actif ou désactivé), rôle
      et clé de session sont ceux de la base en service. Un mot de passe changé, un compte désactivé ou rétrogradé
      après la date de l'archive le restent (réponse à une compromission : on change le mot de passe, on restaure
      la veille, l'intrus ne retrouve pas son accès).
    - Compte de l'archive sans équivalent dans la base en service (supprimé depuis, y compris quand son id a été
      repris par un compte créé depuis) : restauré désactivé, et renvoyé dans la liste ; un administrateur le
      réactive au besoin. Sauf `keep_archive_accounts` (ligne de commande, --keep-archive-accounts : reprise
      après sinistre sur une instance neuve, dont le seul compte est celui du démarrage), et sauf les comptes
      d'`undone` (id, username) : ceux que la restauration annulée avait retirés (LAST_RESTORE).
    - `restorer` (administrateur qui restaure depuis l'interface) : garde son accès même s'il n'existe pas dans
      l'archive (ajouté) ou s'il y est sous un autre id (le compte de même identifiant reprend son mot de passe et
      le rôle d'administrateur). En ligne de commande, personne n'est ajouté.
    - Puis toutes les sessions sont fermées : session_epoch + 1 pour tous, ce qui change aussi leur clé de session
      (déclencheur de la migration 007) ; l'interface rouvre aussitôt celle de l'administrateur qui restaure.
    - Sans administrateur actif à la fin, la restauration est refusée (l'écran de création du premier compte
      serait ouvert à tous).
    Renvoie les identifiants des comptes restaurés désactivés, et les comptes de la base en service absents de la
    base restaurée ({id, username}, pour LAST_RESTORE).
    """
    conn = sqlite3.connect(new_db)
    try:
        conn.execute("ATTACH DATABASE ? AS actuelle", (str(current_db),))
        # Base en service absente ou vide (volume neuf, base endommagée retirée avant de restaurer en ligne de
        # commande) : aucun compte à reprendre, ceux de l'archive restent tels quels.
        has_current = conn.execute("SELECT 1 FROM actuelle.sqlite_master WHERE type = 'table' AND name = 'users'"
                                   ).fetchone() is not None
        disabled: list[str] = []
        removed: list[dict[str, Any]] = []
        conn.execute("CREATE TEMP TABLE annules (id INTEGER, username TEXT)")
        conn.executemany("INSERT INTO temp.annules VALUES (?, ?)", sorted(undone))
        with conn:
            if has_current:
                if conn.execute("SELECT COUNT(*) FROM main.users").fetchone()[0] == 0:
                    columns = ", ".join(r[1] for r in conn.execute("PRAGMA main.table_info(users)"))
                    conn.execute(f"INSERT INTO main.users ({columns}) SELECT {columns} FROM actuelle.users")
                conn.execute("""UPDATE main.users
                                   SET password_hash = c.password_hash, active = c.active, role = c.role,
                                       session_key = c.session_key
                                  FROM actuelle.users AS c
                                 WHERE c.id = users.id AND c.username = users.username""")
                orphan = """active = 1 AND NOT EXISTS (SELECT 1 FROM actuelle.users AS c
                                                     WHERE c.id = users.id AND c.username = users.username)
                            AND NOT EXISTS (SELECT 1 FROM temp.annules AS a
                                             WHERE a.id = users.id AND a.username = users.username)"""
                if not keep_archive_accounts and conn.execute("SELECT 1 FROM actuelle.users").fetchone():
                    disabled = [r[0] for r in conn.execute(
                        f"SELECT username FROM main.users WHERE {orphan} ORDER BY username COLLATE NOCASE")]
                    conn.execute(f"UPDATE main.users SET active = 0 WHERE {orphan}")
                current = conn.execute("SELECT id, username, display_name, password_hash FROM actuelle.users "
                                       "WHERE username = ? AND role = 'admin' AND active = 1",
                                       (restorer,)).fetchone() if restorer else None
                if current is not None:
                    same = conn.execute("SELECT id FROM main.users WHERE username = ?", (current[1],)).fetchone()
                    if same is not None:
                        conn.execute("UPDATE main.users SET password_hash = ?, display_name = ?, role = 'admin', "
                                     "active = 1 WHERE id = ?", (current[3], current[2], same[0]))
                        disabled = [name for name in disabled if name.lower() != current[1].lower()]
                    else:
                        free = not conn.execute("SELECT 1 FROM main.users WHERE id = ?", (current[0],)).fetchone()
                        conn.execute("INSERT INTO main.users (id, username, display_name, password_hash, role, active) "
                                     "VALUES (?, ?, ?, ?, 'admin', 1)",
                                     (current[0] if free else None, current[1], current[2], current[3]))
                removed = [{"id": r[0], "username": r[1]} for r in conn.execute(
                    """SELECT id, username FROM actuelle.users AS c WHERE NOT EXISTS (
                           SELECT 1 FROM main.users AS u WHERE u.id = c.id AND u.username = c.username)
                       ORDER BY id""")]
            conn.execute("UPDATE main.users SET session_epoch = session_epoch + 1")
        conn.execute("DETACH DATABASE actuelle")
        if not conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1").fetchone()[0]:
            if disabled:
                raise ArchiveError(msg("La sauvegarde ne contient aucun administrateur actif (états actuels des comptes repris) ; comptes de l'archive absents de la base en service, donc restaurés désactivés : {accounts} (reprise après sinistre sur une instance neuve, en ligne de commande : --keep-archive-accounts) : restauration refusée",  # noqa: E501
                                       accounts=", ".join(disabled)))
            raise ArchiveError("La sauvegarde ne contient aucun administrateur actif (états actuels des comptes repris) : restauration refusée")  # noqa: E501
    finally:
        conn.close()
    return disabled, removed


def _undone_accounts(archive: Path, target_dir: Path) -> frozenset[tuple[int, str]]:
    """Comptes à garder actifs si `archive` est exactement l'archive de sécurité de la dernière restauration."""
    try:
        state = json.loads((target_dir / LAST_RESTORE).read_text(encoding="utf-8"))
        if archive.resolve() != (target_dir / str(state["safety"])).resolve():
            return frozenset()
        return frozenset((int(a["id"]), str(a["username"])) for a in state["removed"])
    except (OSError, ValueError, KeyError, TypeError):
        return frozenset()


def _write_last_restore(target_dir: Path, safety: Path, removed: list[dict[str, Any]]) -> None:
    """LAST_RESTORE, écrit de façon atomique (fichier temporaire puis renommage)."""
    tmp = target_dir / f"{LAST_RESTORE}.{os.getpid()}.tmp"
    try:
        tmp.write_text(json.dumps({"safety": safety.name, "removed": removed}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target_dir / LAST_RESTORE)
    finally:
        tmp.unlink(missing_ok=True)


def _keep_current_access(new_db: Path, current_db: Path, keep_archive_accounts: bool = False) -> None:
    """Réglages d'accès de l'instance : ceux de la base en service, comme les comptes (_merge_accounts).

    - Connecteur Claude (mcp_settings) : identifiant et empreinte du secret du client OAuth, accès activé ou suspendu.
      Un secret renouvelé ou un accès suspendu depuis la date de l'archive le restent.
    - Campagnes ouvertes à Claude (mcp_grants) : droits actuels, pour les campagnes présentes des deux côtés sous le
      même numéro et le même nom (un numéro repris par une autre campagne n'hérite de rien) ; une campagne de
      l'archive absente aujourd'hui revient fermée à Claude.
    - Journal des actions de Claude (mcp_log) : celui de la base en service (trace utile après un incident).
    - Adresses connues des comptes (login_addresses, limitation des essais de mot de passe, migration 029) : celles de
      la base en service, pour les comptes présents des deux côtés (même id et même identifiant) ; une archive
      n'en ramène pas (ni une adresse oubliée depuis, ni celles d'un compte supprimé).
    Seulement si la base en service a des comptes et sans `keep_archive_accounts` (reprise après sinistre : les
    réglages de l'archive sont gardés). Les jetons (MCP, Foundry) sont supprimés à part ; la clé secrète de
    l'instance (cookies, identifiant d'instance de Foundry) vit dans <data>/.secret_key ou MAZE_SECRET_KEY, hors de
    la base : jamais dans une archive."""
    if keep_archive_accounts:
        return
    conn = sqlite3.connect(new_db)
    try:
        conn.execute("ATTACH DATABASE ? AS actuelle", (str(current_db),))
        tables = {r[0] for r in conn.execute("SELECT name FROM actuelle.sqlite_master WHERE type = 'table'")}
        if {"users", "campaigns", "mcp_settings", "mcp_grants", "mcp_log"} <= tables \
                and conn.execute("SELECT 1 FROM actuelle.users").fetchone():
            with conn:
                for table in ("mcp_settings", "mcp_log"):
                    columns = ", ".join(r[1] for r in conn.execute(f"PRAGMA main.table_info({table})"))
                    conn.execute(f"DELETE FROM main.{table}")
                    conn.execute(f"INSERT INTO main.{table} ({columns}) SELECT {columns} FROM actuelle.{table}")
                conn.execute("DELETE FROM main.mcp_grants")
                conn.execute("""INSERT INTO main.mcp_grants (campaign_id, mode)
                                SELECT g.campaign_id, g.mode FROM actuelle.mcp_grants AS g
                                  JOIN actuelle.campaigns AS a ON a.id = g.campaign_id
                                  JOIN main.campaigns AS m ON m.id = g.campaign_id AND m.name = a.name""")
                if "login_addresses" in tables:
                    conn.execute("DELETE FROM main.login_addresses")
                    conn.execute("""INSERT INTO main.login_addresses (user_id, address, last_login_at)
                                    SELECT l.user_id, l.address, l.last_login_at FROM actuelle.login_addresses AS l
                                      JOIN actuelle.users AS a ON a.id = l.user_id
                                      JOIN main.users AS m ON m.id = l.user_id AND m.username = a.username""")
        conn.execute("DETACH DATABASE actuelle")
    finally:
        conn.close()


def _copy_database(source: Path, target: Path) -> None:
    """Recopie une base dans la base en service avec l'API de sauvegarde SQLite (tout ou rien)."""
    src, dst = sqlite3.connect(source), sqlite3.connect(target, timeout=30)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()


class _MediaStuck(Exception):
    """Images non remplacées, et l'ancien dossier n'a pas pu reprendre sa place : il est resté sous `old_media`."""

    def __init__(self, cause: OSError, error: OSError, old_media: Path):
        super().__init__(str(error))
        self.cause, self.error, self.old_media = cause, error, old_media


REPLACED_MEDIA = ".media-remplace-*"


def replaced_media(data_dir: Path) -> list[dict[str, Any]]:
    """Dossiers d'images d'avant une restauration interrompue (_swap_media) : jamais supprimés automatiquement
    (audit PERF-15), peut-être la seule copie des images si media/ est absent ; signalés au démarrage et dans
    Administration > Sauvegardes."""
    out = []
    for path in sorted(data_dir.glob(REPLACED_MEDIA)):
        if not path.is_dir() or path.is_symlink():
            continue
        files = [f for f in path.rglob("*") if f.is_file()]
        out.append({"name": path.name, "path": str(path), "files": len(files),
                    "size": sum(f.stat().st_size for f in files),
                    "modified": datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "media_missing": media_empty(data_dir)})
    return out


RESTORE_WORK = ".restauration-*"
# Tables comparées pour reconnaître la base d'une restauration (nombre de lignes et plus grand rowid).
_FINGERPRINT_TABLES = ("campaigns", "npcs", "factions", "places", "place_kinds", "images", "document_images",
                       "library_images", "maps", "map_pins")


def _db_fingerprint(path: Path) -> tuple | None:
    """Empreinte d'une base (lecture seule) : version du schéma, (id, live_epoch, revision) de chaque fiche rédigée,
    nombre de lignes et plus grand rowid des tables qui citent des images. None si illisible (base absente,
    restauration arrêtée avant sa migration…)."""
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return (conn.execute("PRAGMA user_version").fetchone()[0],
                    tuple(conn.execute("SELECT id, live_epoch, revision FROM documents ORDER BY id").fetchall()),
                    *(conn.execute(f"SELECT COUNT(*), MAX(rowid) FROM {t}").fetchone() for t in _FINGERPRINT_TABLES))
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def media_empty(data_dir: Path) -> bool:
    """media/ absent ou sans aucun fichier. load_settings recrée un media/ vide à chaque démarrage : « absent » seul
    ne se verrait jamais dans un Maze en marche."""
    media = data_dir / "media"
    return not media.is_dir() or next((f for f in media.rglob("*") if f.is_file()), None) is None


def _interrupted_works(data_dir: Path) -> list[Path]:
    """Dossiers de travail de restauration (.restauration-*) qui contiennent encore les images de l'archive."""
    return [w for w in sorted(data_dir.glob(RESTORE_WORK))
            if w.name != RESTORE_MARKER and w.is_dir() and not w.is_symlink() and (w / "media").is_dir()
            and not (w / "media").is_symlink()]


def _free_replaced_name(data_dir: Path) -> Path:
    base = data_dir / f".media-remplace-{int(time.time())}"
    path, n = base, 2
    while path.exists():
        path, n = base.with_name(f"{base.name}-{n}"), n + 1
    return path


def settle_interrupted_restores(data_dir: Path) -> set[str]:
    """Restauration arrêtée net pendant le remplacement des images (audit PERF-15, contre-revue du lot 6) ; appelé au
    démarrage, verrou du dossier de données pris et aucune restauration en cours. Renvoie les noms des dossiers
    .restauration-* à garder (les autres peuvent être supprimés).

    _restore recopie d'abord la base de l'archive (dossier de travail, maze.db) dans la base en service, puis remplace
    media/ par les images de l'archive (dossier de travail, media/) par deux renommages. Arrêté entre la copie et le
    second renommage, Maze repartait avec la base restaurée et les images d'avant (media/ intact ou absent, images
    d'avant dans .media-remplace-*), et le dossier de travail, seul à avoir les images restaurées, était supprimé.

    Critère : la base en service est celle de la restauration si son empreinte (_db_fingerprint) est celle de la base
    du dossier de travail. La copie (API de sauvegarde de SQLite, tout ou rien) les rend identiques, et rien ne les
    écrit ensuite (écritures refusées pendant la restauration, ménage fait avant les migrations) ; avant la copie, elles
    diffèrent : _restore pose sur chaque fiche de l'archive une époque (live_epoch) à l'heure de la restauration, et une
    copie interrompue est annulée par SQLite. Si les deux bases sont identiques, les images du dossier de travail sont
    par construction celles de la base en service : elles sont remises en place (media/ d'avant, s'il existe, gardé dans
    un .media-remplace-*, signalé et jamais supprimé). Sinon (base d'avant, ou remise en état après un échec), le dossier
    n'est gardé que si media/ manque ou est vide (media_empty), avec une alerte (Administration > Sauvegardes, interrupted_restores) : rien n'est
    remis en place automatiquement. L'archive « -avant-restauration » n'entre pas dans le critère : elle ne dit pas
    quelles images vont avec la base, et rien n'est supprimé ici."""
    keep: set[str] = set()
    works = _interrupted_works(data_dir)
    if not works:
        return keep
    live = _db_fingerprint(data_dir / "maze.db")
    media = data_dir / "media"
    for work in works:
        if live is not None and _db_fingerprint(work / "maze.db") == live:
            moved = None
            try:
                if media_empty(data_dir):
                    shutil.rmtree(media, ignore_errors=True)   # vide (recréé par load_settings) : rien à garder
                else:
                    moved = _free_replaced_name(data_dir)
                    media.rename(moved)
                (work / "media").rename(media)
            except OSError as exc:
                if moved is not None and not media.exists():
                    moved.rename(media)
                media.mkdir(exist_ok=True)
                keep.add(work.name)
                log.error("Restauration interrompue : images de la sauvegarde restaurée non remises en place (%s), "
                          "gardées dans %s (docs/fr/guide.md, Restauration)", exc, work / "media")
                continue
            log.warning("Restauration interrompue pendant le remplacement des images : base restaurée, ses images "
                        "remises en place depuis %s%s", work / "media",
                        f" ; images d'avant gardées dans {moved}" if moved else "")
        elif media_empty(data_dir):
            keep.add(work.name)
            log.warning("Restauration interrompue : media/ absent ou vide, et la base en service n'est pas celle de la "
                        "sauvegarde dont les images sont gardées dans %s : rien n'est remis en place "
                        "(docs/fr/guide.md, Restauration)", work / "media")
    return keep


def interrupted_restores(data_dir: Path) -> list[dict[str, Any]]:
    """Dossiers .restauration-* gardés au démarrage (settle_interrupted_restores), pour l'alerte d'Administration >
    Sauvegardes ; rien pendant une restauration (son dossier de travail est alors normal)."""
    if restore_in_progress(data_dir):
        return []
    out = []
    for work in _interrupted_works(data_dir):
        files = [f for f in (work / "media").rglob("*") if f.is_file()]
        out.append({"name": work.name, "path": str(work), "files": len(files),
                    "size": sum(f.stat().st_size for f in files),
                    "modified": datetime.fromtimestamp(work.stat().st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "media_missing": media_empty(data_dir)})
    return out


def _swap_media(cfg: Settings, new_media: Path) -> Path | None:
    """Remplace media/ par `new_media` ; renvoie l'ancien dossier, à supprimer ensuite (None s'il n'y en avait
    pas). En cas d'échec, l'ancien dossier est remis en place avant de propager l'erreur ; s'il a été recréé entre
    les deux renommages (envoi d'image concurrent), les fichiers reçus entre-temps le rejoignent."""
    old_media = cfg.data_dir / f".media-remplace-{int(time.time())}"
    had_media = cfg.media_dir.exists()
    if had_media:
        cfg.media_dir.rename(old_media)
    try:
        new_media.rename(cfg.media_dir)
    except OSError as cause:
        if had_media:
            try:
                if cfg.media_dir.exists():
                    for path in sorted(p for p in cfg.media_dir.rglob("*") if p.is_file()):
                        dest = old_media / path.relative_to(cfg.media_dir)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(path, dest)
                    shutil.rmtree(cfg.media_dir)
                old_media.rename(cfg.media_dir)
            except OSError as error:
                raise _MediaStuck(cause, error, old_media) from cause
        raise
    return old_media if had_media else None


def restore(archive: Path, cfg: Settings | None = None, restorer: str | None = None,
            keep_archive_accounts: bool = False, exact: bool = False, grace: float = 0) -> RestoreResult:
    """Remplace la base et les images par celles de l'archive.

    L'état actuel est d'abord sauvegardé (archive « -avant-restauration », renvoyée). La base de
    l'archive est mise au schéma actuel, ses comptes fusionnés avec les comptes actuels (_merge_accounts ;
    `restorer` : identifiant de l'administrateur qui restaure depuis l'interface, None en ligne de commande),
    puis elle est recopiée dans la base en service avec l'API de sauvegarde SQLite ; le dossier des images
    est remplacé en bloc.

    `exact` (ligne de commande seulement, --exact ; retour automatique de deploy/update.sh, audit M20) : la base
    est remise telle qu'archivée : ni fusion des comptes, ni reprise des réglages du connecteur, ni suppression
    des jetons MCP et Foundry, sessions gardées ; seul `live_epoch` avance (les éditeurs ouverts rechargent). À
    réserver à une archive de quelques minutes faite par la même instance (sauvegarde « -maj » d'avant une mise à
    jour ratée) : avec une archive plus ancienne, les mots de passe, comptes, sessions et jetons révoqués depuis
    reviendraient.

    Pendant toute l'opération, archive de sécurité comprise, RESTORE_MARKER est verrouillé : un serveur en marche
    n'écrit plus rien venant des éditeurs temps réel (audit M7). `grace` : attente après la pose du verrou, pour
    les écritures commencées juste avant (ligne de commande ; l'administration ferme d'abord les salles).
    """
    cfg = cfg or load_settings()
    if not _restore_lock.acquire(blocking=False):
        raise ArchiveError("Une restauration est déjà en cours")
    try:
        with restore_marker(cfg.data_dir):
            if grace:
                time.sleep(grace)
            return _restore(archive, cfg, restorer, keep_archive_accounts, exact)
    finally:
        _restore_lock.release()


def _keep_sequences(conn: sqlite3.Connection, current_db: Path) -> None:
    """Compteurs des tables en AUTOINCREMENT : au moins ceux de la base remplacée (audit M5 = DATA-8, contre-revue).
    Sans cela, une archive plus ancienne ramène son compteur et redonne les numéros des fiches créées depuis : un
    navigateur resté hors ligne sur l'une d'elles, des mentions ou un module Foundry les rattacheraient à la suivante.
    Appliqué aussi avec --exact : les données restent celles de l'archive, seul ce compteur interne avance (les fiches
    créées par la version ratée, gardées dans l'archive « -avant-restauration », ne voient pas leur numéro repris)."""
    if not current_db.exists():
        return
    try:
        cur = sqlite3.connect(f"file:{current_db}?mode=ro", uri=True)
        try:
            current = dict(cur.execute("SELECT name, seq FROM sqlite_sequence").fetchall())
        finally:
            cur.close()
    except sqlite3.DatabaseError:
        return  # base remplacée illisible ou sans compteur : rien à reporter
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND sql LIKE '%AUTOINCREMENT%'")}
    for name, seq in current.items():
        if name not in tables or not isinstance(seq, int):
            continue
        row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (name,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (name, seq))
        elif row[0] < seq:
            conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (seq, name))


def _restore(archive: Path, cfg: Settings, restorer: str | None, keep_archive_accounts: bool,
             exact: bool) -> RestoreResult:
    inspect_archive(archive)
    (cfg.data_dir / "backups").mkdir(exist_ok=True)
    check_space(cfg, cfg.data_dir / "backups", archive.stat().st_size, "restauration")
    safety = backup(0, cfg, suffix="avant-restauration")  # 0 : aucune rotation pendant l'opération
    work = Path(tempfile.mkdtemp(prefix=".restauration-", dir=cfg.data_dir))
    try:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(work, filter="data")
        new_db = work / "maze.db"
        db.migrate(new_db)
        disabled: list[str] = []
        removed: list[dict[str, Any]] = []
        if not exact:
            disabled, removed = _merge_accounts(new_db, cfg.db_path, restorer, keep_archive_accounts,
                                                _undone_accounts(archive, safety.parent))
            _keep_current_access(new_db, cfg.db_path, keep_archive_accounts)
        conn = sqlite3.connect(new_db)
        try:
            if exact and not conn.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1").fetchone()[0]:
                raise ArchiveError("La sauvegarde ne contient aucun administrateur actif : restauration refusée")
            # Les éditeurs ouverts ne doivent pas réinjecter leur copie : nouvel « epoch » partout.
            with conn:
                conn.execute("UPDATE documents SET live_epoch = ?", (int(time.time()),))
                _keep_sequences(conn, cfg.db_path)
                if not exact:
                    # Jetons du connecteur Claude : jamais ressuscités par une sauvegarde (reconnexion).
                    conn.execute("DELETE FROM mcp_tokens")
                    # Jetons de Foundry : de même (un jeton révoqué depuis la sauvegarde resterait sinon valable).
                    conn.execute("DELETE FROM foundry_tokens")
                    conn.execute("DELETE FROM foundry_grants")  # clés étrangères inactives ici
        finally:
            conn.close()
        _copy_database(new_db, cfg.db_path)
        new_media = work / "media"
        new_media.mkdir(exist_ok=True)
        try:
            old_media = _swap_media(cfg, new_media)
        except (OSError, _MediaStuck) as exc:
            # Images non remplacées (anciennes remises en place) : la base d'avant revient aussi, depuis
            # l'archive de sécurité (maze.db en est le premier membre), pour ne pas servir une base
            # restaurée sans ses images.
            with tarfile.open(safety, "r:gz") as tar:
                tar.extract("maze.db", work / "avant", filter="data")
            _copy_database(work / "avant" / "maze.db", cfg.db_path)
            if isinstance(exc, _MediaStuck):
                raise ArchiveError(msg(
                    "Remplacement des images impossible ({cause}), et l'ancien dossier des images n'a pas pu reprendre sa place ({error}) : restauration annulée, base remise en l'état, mais les images d'avant sont dans {old} et non dans {media}. Arrêter Maze, mettre de côté {media} s'il existe, renommer {old} en {media}, puis redémarrer.",  # noqa: E501
                    cause=str(exc.cause), error=str(exc.error), old=str(exc.old_media), media=str(cfg.media_dir))) from exc
            raise ArchiveError(msg("Remplacement des images impossible ({error}) : restauration annulée, base et images remises en l'état",  # noqa: E501
                                   error=str(exc))) from exc
        if old_media is not None:
            shutil.rmtree(old_media, ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _write_last_restore(safety.parent, safety, removed)
    rotate(safety.parent, "avant-restauration", keep_for("avant-restauration"), protect=(archive,))
    return RestoreResult(safety, disabled)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sauvegarde de Maze")
    parser.add_argument("--keep", type=int, default=None,
                        help="archives de la même nature conservées (0 = toutes ; par défaut : MAZE_BACKUP_KEEP, "
                             "sinon 14 quotidiennes, 3 maj, 3 manuelle…)")

    def nature(value: str) -> str:
        if value and not NATURE.match(value):
            raise argparse.ArgumentTypeError("lettres minuscules et tirets seulement (ex. maj)")
        return value
    parser.add_argument("--suffix", type=nature, default="",
                        help="nature de l'archive, ajoutée au nom (ex. maj) ; chaque nature tourne à part")
    parser.add_argument("--restore", metavar="ARCHIVE",
                        help="restaure cette archive (à faire Maze arrêté de préférence ; comptes : voir _merge_accounts)")
    parser.add_argument("--keep-archive-accounts", action="store_true",
                        help="avec --restore : reprend tels quels les comptes de l'archive absents de la base en "
                             "service, et les réglages du connecteur Claude de l'archive (reprise après sinistre "
                             "sur une instance neuve), au lieu de les désactiver")
    parser.add_argument("--exact", action="store_true",
                        help="avec --restore : base remise telle qu'archivée (comptes, sessions, jetons MCP et "
                             "Foundry, réglages du connecteur), sans fusion ni révocation. Réservé au retour "
                             "automatique d'une mise à jour ratée avec la sauvegarde -maj faite quelques minutes "
                             "avant ; jamais avec une archive plus ancienne (accès révoqués depuis rétablis)")
    args = parser.parse_args()
    if args.exact and (not args.restore or args.keep_archive_accounts):
        parser.error("--exact s'utilise avec --restore, sans --keep-archive-accounts")
    if args.restore:
        try:
            # Maze peut tourner pendant ce temps (même conteneur ou autre processus) : ses éditeurs temps réel
            # n'écrivent plus rien pendant la restauration (RESTORE_MARKER), puis rechargent la fiche restaurée.
            result = restore(Path(args.restore), keep_archive_accounts=args.keep_archive_accounts, exact=args.exact,
                             grace=RESTORE_GRACE)
        except ArchiveError as exc:
            parser.exit(1, f"Restauration refusée : {exc}\n")
        print("État précédent sauvegardé dans", result.safety)
        print("Base remise telle qu'archivée : comptes, sessions et jetons de l'archive." if args.exact
              else "Toutes les sessions sont fermées : chacun doit se reconnecter.")
        if result.disabled:
            print("Comptes de l'archive absents de la base en service, restaurés désactivés :",
                  ", ".join(result.disabled))
    else:
        try:
            print(backup(args.keep, suffix=args.suffix))
        except ArchiveError as exc:
            parser.exit(1, f"Sauvegarde refusée : {exc}\n")
