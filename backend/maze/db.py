"""Accès SQLite : connexions, transactions et migrations SQL versionnées."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import re
import sqlite3
import weakref
from html.parser import HTMLParser
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar
from collections.abc import Callable, Iterator

import anyio
import anyio.to_thread

log = logging.getLogger("maze")
T = TypeVar("T")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
JSON_COLUMNS = {"tags"}
BOOL_COLUMNS = {"directed", "secret", "is_leader"}


class _TextExtractor(HTMLParser):
    """Texte brut d'un fragment HTML (fiches rédigées), blocs séparés par des espaces."""

    BLOCKS = {"p", "br", "li", "h1", "h2", "h3", "blockquote", "div", "hr", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.BLOCKS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCKS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_text(html: str | None) -> str:
    """Utilisée par SQLite (déclencheurs de l'index de recherche) : voir migration 008."""
    if not html:
        return ""
    try:
        parser = _TextExtractor()
        parser.feed(html)
        parser.close()
        text = "".join(parser.parts)
    except Exception:  # noqa: BLE001 - l'enregistrement d'une fiche ne doit jamais échouer pour l'index
        text = re.sub(r"<[^>]*>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


# Attente d'un verrou d'écriture : connexions des fils de travail (run_db), qui peuvent attendre sans rien figer.
BUSY_TIMEOUT = 15.0
# Connexions encore ouvertes dans la boucle d'événements (session de chaque requête, temps réel) : lectures en WAL,
# qui n'attendent pas les écritures ; au pire, une attente courte puis une erreur « réessayez » (503) plutôt qu'un
# gel de tout le serveur (audit M15 = PERF-1).
LOOP_TIMEOUT = 0.25
# Fils de travail réservés à SQLite (run_db) : routes, temps réel. Pools à part de celui d'anyio (fichiers statiques,
# images, mots de passe), et séparés entre écritures et lectures (contre-revue du lot 4) : des enregistrements qui
# attendent tous un verrou d'écriture n'occupent que les fils d'écriture, les lectures (instantané, sans attente)
# gardent les leurs. Tailles réglables : MAZE_DB_WRITE_THREADS (5), MAZE_DB_READ_THREADS (11).
DB_WRITE_THREADS = 5
DB_READ_THREADS = 11
_limiters: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _threads(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        if value < 1:
            raise ValueError
    except ValueError:
        log.warning("%s=%r invalide (entier au moins égal à 1) : %s par défaut", name, raw, default)
        return default
    return value
BUSY_MESSAGE = "Base de données occupée : réessayez dans un instant"


def is_busy(exc: BaseException) -> bool:
    """Verrou de SQLite toujours pris à la fin de l'attente (« database is locked »)."""
    return isinstance(exc, sqlite3.OperationalError) and ("locked" in str(exc) or "busy" in str(exc))


def _limiter(write: bool) -> anyio.CapacityLimiter:
    loop = asyncio.get_running_loop()   # des pools par boucle (les tests lancent plusieurs serveurs)
    pools = _limiters.get(loop)
    if pools is None:
        pools = _limiters[loop] = {
            True: anyio.CapacityLimiter(_threads("MAZE_DB_WRITE_THREADS", DB_WRITE_THREADS)),
            False: anyio.CapacityLimiter(_threads("MAZE_DB_READ_THREADS", DB_READ_THREADS)),
        }
    return pools[write]


async def run_db(fn: Callable[..., T], *args: Any, write: bool = True, **kwargs: Any) -> T:
    """Exécute fn (travail SQLite) dans un fil de travail réservé, hors de la boucle d'événements (audit M15 =
    PERF-1) : un verrou d'écriture tenu longtemps (import, suppression d'une campagne, restauration) ne fige plus
    ni /api/health, ni les autres requêtes, ni le temps réel. fn ne doit pas toucher aux objets de la boucle (salles
    du temps réel : passer par collab.Hub, dont les méthodes publiques reviennent dans la boucle). write=False : fils
    des lectures (transactions en lecture seule, qui n'attendent pas le verrou d'écriture)."""
    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs), limiter=_limiter(write))


def connect(db_path: Path, timeout: float | None = None) -> sqlite3.Connection:
    # isolation_level=None : aucune transaction implicite de sqlite3 (mode historique : ouverte à la première
    # écriture seulement, les lectures d'avant hors transaction, audit DATA-15). Transactions explicites : transaction().
    timeout = BUSY_TIMEOUT if timeout is None else timeout
    conn = sqlite3.connect(db_path, timeout=timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # Indispensable aux déclencheurs de l'index de recherche (texte brut des fiches rédigées).
    conn.create_function("html_text", 1, html_text, deterministic=True)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    return conn


@contextmanager
def transaction(db_path: Path, write: bool = True, timeout: float | None = None) -> Iterator[sqlite3.Connection]:
    """Transaction explicite (audit M15 = PERF-1, réserve de la contre-revue X3 ; DATA-15).

    write=True (défaut) : BEGIN IMMEDIATE, verrou d'écriture pris dès le début. Un bloc qui vérifie puis écrit
    (« aucun compte, donc je crée l'administrateur ») reste atomique quand plusieurs fils l'exécutent en même temps :
    le second attend la fin du premier puis voit son écriture. write=False : BEGIN simple, pour les blocs qui ne
    font que lire : instantané cohérent de toute la base (WAL), sans attendre ni gêner les écritures. Ne jamais
    écrire dans un bloc write=False (l'écriture pourrait échouer en SQLITE_BUSY si un autre a écrit entre-temps)."""
    conn = connect(db_path, timeout)
    try:
        if not write:
            conn.execute("PRAGMA query_only = ON")   # une écriture par erreur échoue tout de suite, en clair
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# Présent à côté de la base pendant que des migrations tournent : deploy/update.sh attend alors la fin au lieu
# de juger la nouvelle version morte (contrôle de santé, audit M20). Retiré à la fin, même en cas d'échec.
MIGRATING_MARKER = ".migration-en-cours"
MIGRATION_NAME = re.compile(r"(\d+)_[^/]*\.sql")
# Migrations appliquées, par nom (audit M22 = QUAL-2, DATA-14). PRAGMA user_version reste tenu à jour (numéro de la
# dernière) : deploy/update.sh le lit pour décider d'une restauration au retour arrière, et les versions d'avant
# cette table ne connaissent que lui.
SCHEMA_TABLE = """CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    checksum   TEXT NOT NULL DEFAULT '',
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)"""
RESTORE_HINT = ("restaurez la sauvegarde d'avant la mise à jour (archive « -maj » de <data>/backups, "
                "python -m maze.backup --restore) ou relancez la version de Maze qui a migré cette base")


class MigrationError(RuntimeError):
    """Migrations du code incohérentes, ou base que ce code ne sait pas ouvrir : refus de démarrer."""


def migration_files(directory: Path | None = None) -> list[tuple[int, str, Path]]:
    """Fichiers de migration (numéro, nom, chemin), triés. Refuse deux fichiers de même numéro et un trou dans la
    numérotation (001, 002, … sans manque) : deux branches qui ont chacune ajouté leur migration."""
    found: dict[int, list[Path]] = {}
    for path in (directory or MIGRATIONS_DIR).glob("*.sql"):
        match = MIGRATION_NAME.fullmatch(path.name)
        if not match:
            raise MigrationError(f"Fichier de migration mal nommé : {path.name} (attendu : 026_description.sql)")
        found.setdefault(int(match.group(1)), []).append(path)
    doubles = {n: sorted(p.name for p in paths) for n, paths in found.items() if len(paths) > 1}
    if doubles:
        detail = " ; ".join(", ".join(names) for _, names in sorted(doubles.items()))
        raise MigrationError(f"Migrations de même numéro : {detail}. Renumérotez la plus récente (numéro suivant "
                             "le plus haut existant) avant de démarrer.")
    numbers = sorted(found)
    missing = sorted(set(range(1, (numbers[-1] if numbers else 0) + 1)) - set(numbers))
    if missing:
        shown = ", ".join(f"{n:03d}" for n in missing[:10]) + (", …" if len(missing) > 10 else "")
        raise MigrationError(f"Numérotation des migrations avec un trou : {shown} manquant(s).")
    return [(n, found[n][0].name, found[n][0]) for n in numbers]


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def applied_migrations(conn: sqlite3.Connection) -> dict[int, str] | None:
    """Migrations enregistrées dans la base (numéro > nom), ou None si la table n'existe pas encore."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'").fetchone():
        return None
    return {r[0]: r[1] for r in conn.execute("SELECT version, name FROM schema_migrations")}


def schema_problem(conn: sqlite3.Connection, files: list[tuple[int, str, Path]] | None = None) -> str | None:
    """Raison pour laquelle ce code ne doit pas ouvrir cette base (plus récente que lui, ou migrée par une autre
    branche), ou None. Utilisée au démarrage (migrate) et avant une restauration (backup)."""
    files = migration_files() if files is None else files
    known = {n: name for n, name, _ in files}
    latest = max(known, default=0)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    applied = applied_migrations(conn)
    if applied is None:
        if version > latest:
            return (f"base au schéma {version}, plus récent que ce code (dernière migration connue : {latest:03d}) : "
                    f"{RESTORE_HINT}")
        return None
    unknown = sorted(name for n, name in applied.items() if known.get(n) != name)
    if unknown:
        return (f"la base contient des migrations inconnues de ce code ({', '.join(unknown)}) : base issue d'une "
                f"version plus récente de Maze ou d'une autre branche ; {RESTORE_HINT}")
    if version > max(applied, default=0):
        return (f"base au schéma {version} alors que la dernière migration enregistrée est "
                f"{max(applied, default=0):03d} : une version de Maze qui n'enregistre pas ses migrations l'a "
                f"migrée ; {RESTORE_HINT}")
    return None


def migrate(db_path: Path) -> None:
    files = migration_files()
    conn = connect(db_path)
    marker = db_path.parent / MIGRATING_MARKER
    pending: list[tuple[int, str, Path]] = []
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        problem = schema_problem(conn, files)
        if problem:
            raise MigrationError(f"Démarrage refusé, {problem}.")
        applied = applied_migrations(conn)
        if applied is None:
            # Reprise d'une base d'avant cette table : migrations jusqu'à user_version enregistrées sous les noms
            # actuels (aucune autre trace de ce qui a été appliqué).
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.execute("BEGIN")
            conn.execute(SCHEMA_TABLE)
            conn.executemany("INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, ?)",
                             [(n, name, _checksum(path)) for n, name, path in files if n <= current])
            conn.execute("COMMIT")
            applied = {n: name for n, name, _ in files if n <= current}
        else:
            sums = dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())
            changed = [name for n, name, path in files if sums.get(n) not in (None, "", _checksum(path))]
            if changed:
                log.warning("Migrations déjà appliquées modifiées depuis : %s (sans effet sur cette base)",
                            ", ".join(changed))
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            if applied and current < max(applied):
                log.warning("PRAGMA user_version (%s) en retard sur schema_migrations (%s) : remis à jour",
                            current, max(applied))
                conn.execute(f"PRAGMA user_version = {max(applied)}")
        pending = [(n, name, path) for n, name, path in files if n not in applied]
        if pending:
            marker.write_text(f"{len(pending)} migration(s) depuis le schéma {max(applied, default=0)}\n",
                              encoding="utf-8")
        else:
            marker.unlink(missing_ok=True)  # laissé par un arrêt brutal
        for version, name, path in pending:
            log.info("Migration %s", name)
            sql = path.read_text(encoding="utf-8")
            # Clés étrangères suspendues le temps d'une migration (procédure SQLite pour reconstruire
            # une table : sinon, supprimer l'ancienne table mettrait à NULL les références vers elle),
            # puis vérifiées avant de valider.
            conn.execute("PRAGMA foreign_keys = OFF")
            before = {tuple(r) for r in conn.execute("PRAGMA foreign_key_check")}
            try:
                conn.executescript(f"BEGIN;\n{sql}\n;PRAGMA user_version = {version};")
                conn.execute("INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, ?)",
                             (version, name, _checksum(path)))
                # Seules comptent les incohérences apparues pendant la migration.
                broken = [tuple(r) for r in conn.execute("PRAGMA foreign_key_check") if tuple(r) not in before]
                if broken:
                    raise sqlite3.IntegrityError(f"Migration {name} : références incohérentes {broken[:5]}")
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()
        if pending:
            marker.unlink(missing_ok=True)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key in data.keys() & JSON_COLUMNS:
        data[key] = json.loads(data[key] or "[]")
    for key in data.keys() & BOOL_COLUMNS:
        data[key] = bool(data[key])
    return data


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(r) for r in rows]  # type: ignore[misc]


def _encode(key: str, value: Any) -> Any:
    if key in JSON_COLUMNS:
        return json.dumps(value, ensure_ascii=False)
    if key in BOOL_COLUMNS:
        return int(bool(value))
    return value


# Les noms de colonnes proviennent toujours des champs des modèles Pydantic (liste blanche),
# jamais directement de la requête : la construction dynamique du SQL est donc sûre.

def insert(conn: sqlite3.Connection, table: str, data: dict[str, Any]) -> int:
    cols = list(data.keys())
    placeholders = ", ".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
        [_encode(c, data[c]) for c in cols],
    )
    return int(cur.lastrowid)


def update(conn: sqlite3.Connection, table: str, row_id: int, data: dict[str, Any],
           touch: bool = True) -> None:
    if not data:
        return
    assignments = [f"{c} = ?" for c in data]
    if touch:
        assignments.append("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
    conn.execute(
        f"UPDATE {table} SET {', '.join(assignments)} WHERE id = ?",
        [_encode(c, v) for c, v in data.items()] + [row_id],
    )
