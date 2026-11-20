"""Corbeille (migration 034) : un PNJ, une faction, un lieu, une fiche rédigée (avec ses sous-pages), un événement de
chronologie ou une carte interactive supprimé y reste TRASH_DAYS jours, d'où il se restaure tel qu'il était, avec ce
qui en dépendait. Interface et connecteur Claude passent par les mêmes routes de suppression ; une campagne supprimée
emporte sa corbeille (cascade), sans y passer elle-même.

Capture (put) : pendant la transaction de suppression, des déclencheurs TEMP (propres à la connexion, retirés à la fin)
recopient, avant toute modification, chaque ligne supprimée ou modifiée de toutes les tables de la base (sauf
campaigns, la corbeille elle-même, schema_migrations et l'index de recherche, reconstruit par ses déclencheurs). On
obtient ainsi exactement ce que la suppression a fait, quelle qu'en soit la cause : cascades ON DELETE CASCADE, mises à
NULL (ON DELETE SET NULL), nettoyage polymorphe des déclencheurs (relations, galerie, liens de lieux, mentions, carte
des scénarios, mentions de la chronologie) et effets du code de la route (sous-lieux remontés d'un niveau, illustration
rattachée à une autre fiche qui l'affiche). Une table ajoutée plus tard est prise en compte sans rien changer ici.
Ligne de `trash` : lignes supprimées par table (valeurs d'avant la transaction, tables dans l'ordre d'insertion :
parents d'abord), lignes modifiées (colonne : ancienne et nouvelle valeur), fichiers des lignes supprimées (galerie,
illustrations, fond de carte et ses aperçus), déplacés sous media/<campagne>/.trash/ après validation.

Restauration (restore) : lignes réinsérées avec leurs identifiants d'origine, parents d'abord, clés étrangères
vérifiées à la validation (defer_foreign_keys) ; les déclencheurs d'insertion reconstruisent l'index de recherche.
Une ligne qui renvoie à un élément disparu entre-temps suit la règle de la base : ON DELETE CASCADE ou référence
polymorphe (relation vers un PNJ supprimé depuis…) : ligne laissée de côté ; ON DELETE SET NULL : colonne à NULL
(lieu dont le parent a été supprimé : rendu à la racine ; carte dont le lieu a disparu : sans lieu). L'objet lui-même
laissé de côté (sous-page dont la fiche parente a été supprimée) : refus 409 qui dit quoi restaurer d'abord.
Identifiant repris entre-temps : nouvel identifiant pour une table sans AUTOINCREMENT (appartenances, relations,
versions, commentaires des fiches…, références recopiées en conséquence) ; refus 409 pour une table AUTOINCREMENT
(jamais réutilisés : ne devrait pas arriver). Colonnes modifiées par la suppression : remises à l'ancienne valeur si
elles ont toujours la valeur laissée par la suppression (sous-lieu rendu à son lieu s'il n'a pas été déplacé depuis).
Fiches rédigées : journal temps réel (document_updates) et content_seq restaurés tels quels, époque suivante
(live_epoch + 1) : un navigateur resté sur l'ancienne recharge la fiche au lieu d'y verser ses modifications.

Purge : éléments de plus de TRASH_DAYS jours supprimés définitivement (lignes et fichiers), au démarrage puis chaque
jour (app.py) ; suppression définitive d'un élément ou de toute la corbeille d'une campagne (routes ci-dessous).
La corbeille n'est pas exportée avec la campagne (transfer.py) ; la sauvegarde la contient (base et media/.trash).

Fichiers : moving() (verrou du processus) est tenu par chaque suppression, de la capture à la fin des déplacements
vers .trash, par la restauration, de la validation à la remise en place des fichiers, et par la purge, de la lecture
des fichiers à garder à la fin du parcours de .trash : la purge ne voit jamais un fichier déplacé mais pas encore
cité, ou plus cité mais pas encore remis en place. Un fichier déplacé vers .trash prend la date du déplacement :
l'âge des fichiers sans élément (STRAY_AGE) se compte depuis l'entrée dans la corbeille.

Payload (BLOB) : PAYLOAD_MAGIC, puis zlib de « longueur de l'en-tête (4 octets) + en-tête JSON + valeurs binaires
brutes à la suite » ; dans l'en-tête, une valeur binaire (versions compressées, journal temps réel) vaut
{"bin": rang} et "bins" donne leurs longueurs : pas de base64. Au-delà de MAX_PAYLOAD octets (ou de MAX_RAW octets
avant compression), la suppression est refusée (413) et annulée.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import struct
import threading
import time
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, db, i18n
from .api import path_id, scoped, settings, threaded, touch_campaign, tx
from .i18n import msg, msgn
from .mediafiles import TRASH_DIR

log = logging.getLogger("maze")

TRASH_DAYS = 30
PURGE_EVERY = 24 * 3600          # secondes entre deux purges automatiques (app.py)
STRAY_AGE = 24 * 3600            # fichier de .trash sans élément de corbeille : retiré après ce délai
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
KINDS = {"npc": "npcs", "faction": "factions", "place": "places", "document": "documents",
         "event": "timeline_events", "map": "maps"}
# Nom de la sorte d'élément dans un message (« PNJ introuvable »…), comme les routes de suppression.
WHAT = "Élément de la corbeille"
# Tables jamais capturées : campagne (date de modification), corbeille, migrations ; l'index de recherche (table
# virtuelle et ses tables internes) est reconstruit par les déclencheurs d'insertion.
SKIP_TABLES = {"campaigns", "trash", "schema_migrations"}
# Références polymorphes (couple sorte / identifiant, sans clé étrangère), nettoyées par les déclencheurs de
# suppression des fiches (migrations 001, 004, 010, 011, 012, 032) : traitées comme ON DELETE CASCADE.
POLY_REFS: dict[str, tuple[tuple[str, str], ...]] = {
    "relations": (("source_kind", "source_id"), ("target_kind", "target_id")),
    "images": (("owner_kind", "owner_id"),),
    "place_links": (("entity_kind", "entity_id"),),
    "document_mentions": (("target_kind", "target_id"),),
    "scenario_nodes": (("entity_kind", "entity_id"),),
    "timeline_mentions": (("target_kind", "target_id"),),
}
POLY_TABLES = {"npc": "npcs", "faction": "factions", "place": "places", "doc": "documents"}
# Colonnes qui nomment un fichier de media/ (chemin relatif « <campagne>/<nom> »).
FILE_COLUMNS = {"images": ("file", "thumb"), "document_images": ("file", "thumb"),
                "library_images": ("file", "thumb"), "maps": ("file", "thumb", "preview"),
                "place_kinds": ("image",)}
DOC_IMAGE = re.compile(rb'data-doc-image="(\d+)"')
PAYLOAD_MAGIC = b"MZT1"
MAX_PAYLOAD = 200 * 1024 * 1024   # octets compressés d'un élément de corbeille, au plus
MAX_RAW = 1024 * 1024 * 1024      # octets capturés (texte et binaire) avant compression, au plus

_files_lock = threading.Lock()
_held = threading.local()


@contextmanager
def moving() -> Iterator[None]:
    """Verrou des fichiers de la corbeille (voir l'en-tête) : à prendre AVANT la transaction (même ordre partout :
    ce verrou, puis celui d'écriture de SQLite), et à garder jusqu'à la fin des déplacements de fichiers."""
    with _files_lock:
        yield


@contextmanager
def deleting(request: Request) -> Iterator[sqlite3.Connection]:
    """Transaction d'une route de suppression, sous moving() : après validation, les fichiers des éléments mis à la
    corbeille par put() y sont déplacés (stash_files) avant que le verrou ne soit rendu."""
    with moving():
        _held.pending = []
        try:
            with tx(request) as conn:
                yield conn
            for t in _held.pending:
                stash_files(settings(request).media_dir, t.campaign_id, t.files)
        finally:
            _held.pending = None


# --- Schéma ---------------------------------------------------------------------------------------

@dataclass
class Table:
    name: str
    cols: list[str]
    pk: str | None                 # colonne INTEGER PRIMARY KEY (alias de rowid), sinon None
    autoinc: bool
    fks: list[tuple[str, str, str, str]] = field(default_factory=list)   # (colonne, table, colonne visée, ON DELETE)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def schema(conn: sqlite3.Connection) -> dict[str, Table]:
    """Tables capturées, dans l'ordre des dépendances (une table après celles que ses clés étrangères visent)."""
    rows = conn.execute("SELECT name, sql FROM main.sqlite_master WHERE type = 'table'").fetchall()
    virtual = [r[0] for r in rows if (r[1] or "").upper().startswith("CREATE VIRTUAL")]
    tables: dict[str, Table] = {}
    for name, sql in rows:
        if (name.startswith("sqlite_") or name in SKIP_TABLES or name in virtual
                or any(name.startswith(v + "_") for v in virtual)):
            continue
        info = conn.execute(f"PRAGMA main.table_info({_q(name)})").fetchall()
        pks = [r for r in info if r["pk"]]
        pk = pks[0]["name"] if len(pks) == 1 and pks[0]["type"].upper() == "INTEGER" else None
        fks = [(r["from"], r["table"], r["to"] or "id", r["on_delete"].upper())
               for r in conn.execute(f"PRAGMA main.foreign_key_list({_q(name)})")]
        tables[name] = Table(name, [r["name"] for r in info], pk, "AUTOINCREMENT" in (sql or "").upper(), fks)
    ordered: dict[str, Table] = {}
    pending = dict(sorted(tables.items()))
    while pending:
        ready = [n for n, t in pending.items()
                 if all(ref == n or ref not in pending for _, ref, _, _ in t.fks)] or [next(iter(pending))]
        for n in ready:
            ordered[n] = pending.pop(n)
    return ordered


class _Bins:
    """Valeurs binaires d'un payload, rangées à part (format : voir l'en-tête du module)."""

    def __init__(self) -> None:
        self.items: list[bytes] = []

    def encode(self, value: Any) -> Any:
        if isinstance(value, (bytes, bytearray, memoryview)):
            self.items.append(bytes(value))
            return {"bin": len(self.items) - 1}
        return value


def dump_payload(payload: dict[str, Any], bins: _Bins) -> bytes:
    head = json.dumps({**payload, "bins": [len(b) for b in bins.items]}, ensure_ascii=False,
                      separators=(",", ":")).encode()
    packer = zlib.compressobj(6)
    parts = [PAYLOAD_MAGIC, packer.compress(struct.pack(">I", len(head))), packer.compress(head)]
    parts += [packer.compress(b) for b in bins.items]
    parts.append(packer.flush())
    return b"".join(parts)


def load_payload(raw: bytes | str | None) -> dict[str, Any]:
    """Payload décodé : valeurs binaires rendues en bytes, dans les lignes et les colonnes modifiées."""
    if not raw:
        return {}
    data = zlib.decompress(bytes(raw)[len(PAYLOAD_MAGIC):])
    size = struct.unpack(">I", data[:4])[0]
    payload = json.loads(data[4:4 + size])
    bins, pos = [], 4 + size
    for n in payload.pop("bins", []):
        bins.append(data[pos:pos + n])
        pos += n

    def dec(value: Any) -> Any:
        return bins[value["bin"]] if isinstance(value, dict) else value
    for table in payload.get("tables", []):
        table["rows"] = [[dec(v) for v in r] for r in table["rows"]]
    for effect in payload.get("updates", []):
        effect["set"] = {c: [dec(o), dec(n)] for c, (o, n) in effect["set"].items()}
    return payload


def _raw_size(deleted: dict[str, list[list[Any]]]) -> int:
    return sum(len(v) for rows in deleted.values() for r in rows for v in r if isinstance(v, (str, bytes)))


def now_text(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) + delta).strftime(TIME_FORMAT)


def expires_at(deleted_at: str) -> str:
    return (datetime.strptime(deleted_at, TIME_FORMAT) + timedelta(days=TRASH_DAYS)).strftime(TIME_FORMAT)


def cutoff() -> str:
    """Date avant laquelle un élément a dépassé son séjour dans la corbeille."""
    return now_text(-timedelta(days=TRASH_DAYS))


# --- Capture d'une suppression --------------------------------------------------------------------

@dataclass
class Trashed:
    trash_id: int
    campaign_id: int
    files: list[str]


class _Capture:
    """Déclencheurs TEMP sur toutes les tables capturées : la ligne d'avant chaque DELETE ou UPDATE est recopiée
    dans une table TEMP (sans type : valeurs gardées telles quelles, BLOB compris). Les déclencheurs de SQLite
    s'exécutent aussi pour les cascades et les mises à NULL des clés étrangères, et pour les suppressions faites par
    d'autres déclencheurs."""

    def __init__(self, conn: sqlite3.Connection, tables: dict[str, Table]):
        self.conn, self.tables = conn, tables

    @staticmethod
    def _cap(t: Table) -> str:
        return _q(f"_trash_cap_{t.name}")

    def start(self) -> None:
        # Corps des déclencheurs : nom de la table TEMP sans « temp. » (refusé par SQLite avant 3.4x : « qualified
        # table names are not allowed… within triggers ») ; le préfixe _trash_cap_ évite toute homonymie avec main.
        for t in self.tables.values():
            cols = ", ".join(_q(c) for c in t.cols)
            old = ", ".join("OLD." + _q(c) for c in t.cols)
            self.conn.execute(f"CREATE TEMP TABLE {self._cap(t)} (_rid, {cols})")
            for event in ("DELETE", "UPDATE"):
                self.conn.execute(
                    f"CREATE TEMP TRIGGER {_q(f'_trash_{event[0]}_{t.name}')} AFTER {event} ON main.{_q(t.name)} "
                    f"BEGIN INSERT INTO {self._cap(t)} VALUES (OLD.rowid, {old}); END")

    def stop(self) -> None:
        for t in self.tables.values():
            for event in ("DELETE", "UPDATE"):
                self.conn.execute(f"DROP TRIGGER IF EXISTS temp.{_q(f'_trash_{event[0]}_{t.name}')}")
            self.conn.execute(f"DROP TABLE IF EXISTS temp.{self._cap(t)}")

    def collect(self, bins: _Bins) -> tuple[dict[str, list[list[Any]]], list[dict[str, Any]]]:
        """(lignes supprimées par table, valeurs d'avant la transaction ; lignes modifiées : table, rowid, colonnes
        changées avec [ancienne, nouvelle] valeur)."""
        deleted: dict[str, list[list[Any]]] = {}
        updated: list[dict[str, Any]] = []
        for t in self.tables.values():
            cap = self._cap(t)
            cols = ", ".join(_q(c) for c in t.cols)
            firsts = self.conn.execute(
                f"SELECT * FROM temp.{cap} WHERE rowid IN (SELECT MIN(rowid) FROM temp.{cap} GROUP BY _rid) "
                f"ORDER BY rowid").fetchall()
            for row in firsts:
                rid, old = row[0], list(row[1:])
                now = self.conn.execute(f"SELECT {cols} FROM main.{_q(t.name)} WHERE rowid = ?", (rid,)).fetchone()
                if now is None:
                    deleted.setdefault(t.name, []).append(old)
                    continue
                changed = {c: [bins.encode(o), bins.encode(n)] for c, o, n in zip(t.cols, old, tuple(now), strict=True) if o != n}
                if changed:
                    updated.append({"t": t.name, "rid": rid, "set": changed})
        return deleted, updated


def _counts(kind: str, deleted: dict[str, list[list[Any]]], updated: list[dict[str, Any]]) -> dict[str, int]:
    """Effectifs du résumé affiché dans la corbeille (« 3 sous-pages, 2 images »)."""
    n = {t: len(rows) for t, rows in deleted.items()}
    if kind == "document":
        return {"subpages": n.get("documents", 1) - 1, "images": n.get("document_images", 0),
                "comments": n.get("comments", 0)}
    if kind in ("npc", "faction", "place"):
        out = {"images": n.get("images", 0), "relations": n.get("relations", 0)}
        if kind == "place":
            out["subplaces"] = sum(1 for u in updated if u["t"] == "places" and "parent_id" in u["set"])
        return out
    if kind == "map":
        return {"pins": n.get("map_pins", 0)}
    return {}


def put(conn: sqlite3.Connection, request: Request, kind: str, row: dict[str, Any],
        remove: Callable[[], None]) -> Trashed:
    """Exécute remove() (le code de suppression de la route, dans sa transaction) en capturant tout ce qu'elle
    supprime ou modifie, et range le tout dans la corbeille. Fichiers déplacés après validation, à la sortie de
    deleting() (à appeler dans ce bloc). Élément trop volumineux (MAX_PAYLOAD) : HTTPException 413, la transaction
    de la route est annulée."""
    pending = getattr(_held, "pending", None)
    if pending is None:
        raise RuntimeError("trash.put hors de trash.deleting()")
    tables = schema(conn)
    capture = _Capture(conn, tables)
    bins = _Bins()
    capture.start()
    try:
        remove()
        deleted, updated = capture.collect(bins)
    finally:
        capture.stop()
    name = row.get("name") if kind not in ("document", "event") else row.get("title")
    if _raw_size(deleted) > MAX_RAW:
        _too_large(name, _raw_size(deleted))
    payload = {"v": 1, "tables": [{"name": t, "cols": tables[t].cols,
                                    "rows": [[bins.encode(v) for v in r] for r in deleted[t]]}
                                   for t in tables if t in deleted],
               "updates": updated}
    packed = dump_payload(payload, bins)
    if len(packed) > MAX_PAYLOAD:
        _too_large(name, len(packed))
    files: list[str] = []
    for tname, columns in FILE_COLUMNS.items():
        if tname in deleted:
            idx = [tables[tname].cols.index(c) for c in columns if c in tables[tname].cols]
            files += [r[i] for r in deleted[tname] for i in idx if isinstance(r[i], str) and r[i]]
    user = auth.current_user(request)
    via = "claude" if user.get("mcp_scope") is not None else "interface"
    trash_id = db.insert(conn, "trash", {
        "campaign_id": row["campaign_id"], "kind": kind, "object_id": row["id"], "name": name or "",
        "deleted_by": user.get("id"), "via": via, "deleted_at": now_text(),
        "payload": packed,
        "files": json.dumps(files), "counts": json.dumps(_counts(kind, deleted, updated)),
    })
    pending.append(Trashed(trash_id, row["campaign_id"], files))
    return pending[-1]


def _too_large(name: str | None, size: int) -> None:
    raise HTTPException(413, msg("« {name} » est trop volumineux pour la corbeille ({size} Mo, {max} Mo au plus) : rien n'a été supprimé. Supprimez d'abord des versions de son historique.",  # noqa: E501
                                 name=name or "", size=-(-size // (1024 * 1024)), max=MAX_PAYLOAD // (1024 * 1024)))


# --- Fichiers -------------------------------------------------------------------------------------

def _inside(media_dir: Path, rel: str) -> Path | None:
    path = (media_dir / rel).resolve()
    return path if media_dir.resolve() in path.parents else None


def _trash_path(media_dir: Path, campaign_id: int, rel: str) -> Path:
    return media_dir / str(campaign_id) / TRASH_DIR / Path(rel).name


def stash_files(media_dir: Path, campaign_id: int, files: list[str]) -> None:
    """Fichiers d'un élément mis à la corbeille : déplacés (renommage atomique, même volume) sous .trash/."""
    for rel in files:
        src = _inside(media_dir, rel)
        if src is None or not src.is_file():
            continue
        dest = _trash_path(media_dir, campaign_id, rel)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dest)
            os.utime(dest)   # âge (STRAY_AGE) compté depuis l'entrée dans la corbeille
        except OSError as exc:
            log.warning("Corbeille : fichier %s non déplacé (%s)", rel, exc)


def unstash_files(media_dir: Path, campaign_id: int, files: list[str]) -> None:
    """Fichiers remis en place à la restauration (laissés où ils sont s'ils n'ont jamais été déplacés)."""
    for rel in files:
        dest = _inside(media_dir, rel)
        src = _trash_path(media_dir, campaign_id, rel)
        if dest is None or not src.is_file():
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dest)
        except OSError as exc:
            log.warning("Corbeille : fichier %s non remis en place (%s)", rel, exc)


def drop_files(media_dir: Path, campaign_id: int, files: list[str]) -> None:
    for rel in files:
        _trash_path(media_dir, campaign_id, rel).unlink(missing_ok=True)
    try:
        (media_dir / str(campaign_id) / TRASH_DIR).rmdir()   # seulement s'il est vide
    except OSError:
        pass


def stash(request: Request, trashed: Trashed) -> JSONResponse:
    """Réponse d'une route de suppression ({"trash_id"}) ; fichiers déjà déplacés à la sortie de deleting()."""
    return JSONResponse({"trash_id": trashed.trash_id})


# --- Restauration ---------------------------------------------------------------------------------

class RestoreRefused(Exception):
    """Restauration impossible (message pour le MJ, 409)."""


@dataclass
class Restored:
    kind: str
    object_id: int
    campaign_id: int
    files_back: list[str]
    files_lost: list[str]
    documents: list[int]


def _trashed_parent(conn: sqlite3.Connection, campaign_id: int, doc_id: int) -> str | None:
    """Titre de la fiche rédigée doc_id si elle est elle-même dans la corbeille de la campagne."""
    for name, payload in conn.execute("SELECT name, payload FROM trash WHERE campaign_id = ? AND kind = 'document' "
                                      "ORDER BY id DESC", (campaign_id,)):
        for table in load_payload(payload).get("tables", []):
            if table["name"] == "documents" and "id" in table["cols"]:
                i = table["cols"].index("id")
                if any(r[i] == doc_id for r in table["rows"]):
                    return name
    return None


def restore(conn: sqlite3.Connection, item: dict[str, Any]) -> Restored:
    tables = schema(conn)
    payload = load_payload(item["payload"])
    root_table = KINDS[item["kind"]]
    # Lignes par table, dans l'ordre des dépendances actuel ; colonnes disparues depuis ignorées (migration).
    rows: dict[str, list[dict[str, Any]]] = {}
    for block in payload.get("tables", []):
        t = tables.get(block["name"])
        if t is None:
            continue
        rows[t.name] = [{c: v for c, v in zip(block["cols"], r, strict=True) if c in t.cols} for r in block["rows"]]
    rows = {name: rows[name] for name in tables if name in rows}
    skipped: dict[str, set[int]] = {name: set() for name in rows}
    in_payload: dict[tuple[str, str], set[Any]] = {}
    in_base: dict[tuple[str, str, Any], bool] = {}

    def alive(table: str, col: str, value: Any) -> bool:
        """L'élément visé existe : parmi les lignes restaurées (non écartées), ou dans la base."""
        key = (table, col)
        if key not in in_payload:
            in_payload[key] = {r.get(col) for i, r in enumerate(rows.get(table, [])) if i not in skipped[table]}
        if value in in_payload[key]:
            return True
        if table not in tables and table != "campaigns":
            return False
        if (table, col, value) not in in_base:
            in_base[(table, col, value)] = conn.execute(
                f"SELECT 1 FROM main.{_q(table)} WHERE {_q(col)} = ? LIMIT 1", (value,)).fetchone() is not None
        return in_base[(table, col, value)]

    # Références vers des éléments disparus entre-temps : règle de la base (CASCADE : ligne écartée ; SET NULL).
    changed = True
    while changed:
        changed = False
        for name, table_rows in rows.items():
            t = tables[name]
            for i, r in enumerate(table_rows):
                if i in skipped[name]:
                    continue
                gone = False
                for col, ref, ref_col, action in t.fks:
                    value = r.get(col)
                    if value is None or alive(ref, ref_col, value):
                        continue
                    if action == "SET NULL":
                        r[col] = None
                    else:
                        gone = True
                for kind_col, id_col in POLY_REFS.get(name, ()):
                    target = POLY_TABLES.get(r.get(kind_col))
                    if target and r.get(id_col) is not None and not alive(target, "id", r[id_col]):
                        gone = True
                if gone:
                    skipped[name].add(i)
                    in_payload.clear()
                    changed = True

    label = item["name"]
    root = next((i for i, r in enumerate(rows.get(root_table, [])) if r.get("id") == item["object_id"]), None)
    if root is None:
        raise RestoreRefused(msg("« {name} » ne peut pas être restauré : élément de corbeille incomplet.", name=label))
    if conn.execute(f"SELECT 1 FROM {root_table} WHERE id = ?", (item["object_id"],)).fetchone():
        raise RestoreRefused(msg("« {name} » ne peut pas être restauré : son numéro est déjà pris par un autre élément.",
                                 name=label))
    if root in skipped[root_table]:
        parent = rows[root_table][root].get("parent_id") if root_table == "documents" else None
        if parent is not None and not alive("documents", "id", parent):
            title = _trashed_parent(conn, item["campaign_id"], parent)
            if title is not None:
                raise RestoreRefused(msg("« {name} » ne peut pas être restauré : sa fiche parente a été supprimée. Restaurez d'abord « {parent} » depuis la corbeille.",  # noqa: E501
                                         name=label, parent=title))
            raise RestoreRefused(msg("« {name} » ne peut pas être restauré : sa fiche parente n'existe plus.", name=label))
        raise RestoreRefused(msg("« {name} » ne peut pas être restauré : un élément dont il dépend a été supprimé entre-temps.",  # noqa: E501
                                 name=label))

    # Insertion, parents d'abord ; clés étrangères vérifiées à la validation.
    conn.execute("PRAGMA defer_foreign_keys = ON")
    remap: dict[tuple[str, Any], int] = {}
    restored_docs: list[int] = []
    for name, table_rows in rows.items():
        t = tables[name]
        keep = [r for i, r in enumerate(table_rows) if i not in skipped[name]]
        self_fk = [col for col, ref, _, _ in t.fks if ref == name]
        if self_fk and t.pk:   # parents avant enfants dans une même table (sous-pages, réponses)
            ids = {r.get(t.pk) for r in keep}
            done: set[Any] = set()
            ordered: list[dict[str, Any]] = []
            while keep:
                ready = [r for r in keep if all(r.get(c) not in ids or r.get(c) in done or r.get(c) == r.get(t.pk)
                                                for c in self_fk)] or keep[:1]
                for r in ready:
                    keep.remove(r)
                    ordered.append(r)
                    done.add(r.get(t.pk))
            keep = ordered
        for r in keep:
            values = dict(r)
            for col, ref, ref_col, _ in t.fks:
                if (ref, values.get(col)) in remap and ref in tables and ref_col == tables[ref].pk:
                    values[col] = remap[(ref, values[col])]
            if name == "documents" and "live_epoch" in values:
                values["live_epoch"] = (values["live_epoch"] or 0) + 1
                restored_docs.append(values["id"])
            old_id = values.get(t.pk) if t.pk else None
            verb = "INSERT"
            if t.pk is None:
                verb = "INSERT OR IGNORE"   # table de liens (clé composée) : ligne déjà recréée, gardée
            elif conn.execute(f"SELECT 1 FROM main.{_q(name)} WHERE {_q(t.pk)} = ?", (old_id,)).fetchone():
                if t.autoinc:
                    raise RestoreRefused(msg("« {name} » ne peut pas être restauré : son numéro est déjà pris par un autre élément.",  # noqa: E501
                                             name=label))
                values.pop(t.pk)   # identifiant repris entre-temps (table sans AUTOINCREMENT) : nouveau numéro
            cols = list(values)
            cur = conn.execute(f"{verb} INTO main.{_q(name)} ({', '.join(_q(c) for c in cols)}) "
                               f"VALUES ({', '.join('?' * len(cols))})", [values[c] for c in cols])
            if t.pk and old_id is not None and t.pk not in values:
                remap[(name, old_id)] = cur.lastrowid

    # Colonnes modifiées par la suppression : rendues si personne n'y a touché depuis.
    for effect in payload.get("updates", []):
        t = tables.get(effect["t"])
        if t is None:
            continue
        for col, (old, new) in effect["set"].items():
            if col not in t.cols:
                continue
            target = next(((ref, ref_col) for c, ref, ref_col, _ in t.fks if c == col), None)
            if old is not None and target and not alive(*target, old):
                continue
            conn.execute(f"UPDATE main.{_q(t.name)} SET {_q(col)} = ? WHERE rowid = ? AND {_q(col)} IS ?",
                         (old, effect["rid"], new))

    files = json.loads(item["files"])
    lost: set[str] = set()
    for name, columns in FILE_COLUMNS.items():
        for i in skipped.get(name, ()):
            lost.update(rows[name][i].get(c) or "" for c in columns)
    lost.discard("")
    return Restored(item["kind"], item["object_id"], item["campaign_id"], [f for f in files if f not in lost],
                    [f for f in files if f in lost], restored_docs)


# --- Purge ----------------------------------------------------------------------------------------

def purge_expired(db_path: Path, media_dir: Path) -> int:
    """Supprime définitivement les éléments de plus de TRASH_DAYS jours (lignes, puis fichiers), et les fichiers de
    .trash qu'aucun élément ne cite plus (déplacement interrompu), passé STRAY_AGE. Renvoie le nombre d'éléments.
    Sous moving() de bout en bout : aucune suppression ni restauration ne déplace de fichier pendant ce temps."""
    with moving():
        with db.transaction(db_path) as conn:
            expired = conn.execute("SELECT id, campaign_id, files FROM trash WHERE deleted_at < ?",
                                   (cutoff(),)).fetchall()
            conn.executemany("DELETE FROM trash WHERE id = ?", [(r["id"],) for r in expired])
            kept = {Path(f).name for (files,) in conn.execute("SELECT files FROM trash") for f in json.loads(files)}
        for r in expired:
            drop_files(media_dir, r["campaign_id"], json.loads(r["files"]))
        for folder in media_dir.glob(f"*/{TRASH_DIR}"):
            for path in folder.iterdir():
                try:
                    if path.is_file() and path.name not in kept and time.time() - path.stat().st_mtime > STRAY_AGE:
                        path.unlink()
                except OSError:
                    continue
    if expired:
        log.info("Corbeille : %s élément(s) de plus de %s jours supprimé(s)", len(expired), TRASH_DAYS)
    return len(expired)


def document_image_refs(conn: sqlite3.Connection, campaign_id: int) -> set[int]:
    """Illustrations citées par les fiches rédigées de la corbeille (texte, versions, journal temps réel) : gardées
    par le ménage d'Administration > Images, pour qu'une fiche restaurée retrouve ses images."""
    found: set[int] = set()
    for (payload,) in conn.execute("SELECT payload FROM trash WHERE campaign_id = ? AND kind = 'document'",
                                   (campaign_id,)):
        for table in load_payload(payload).get("tables", []):
            column = {"documents": "content", "document_versions": "content"}.get(table["name"])
            if column is None or column not in table["cols"]:
                continue
            i = table["cols"].index(column)
            for r in table["rows"]:
                value = r[i]
                if isinstance(value, bytes):
                    try:
                        value = zlib.decompress(value)
                    except zlib.error:
                        pass
                else:
                    value = (value or "").encode()
                found.update(int(m) for m in DOC_IMAGE.findall(value))
    return found


# --- Routes ---------------------------------------------------------------------------------------

def _summary(counts: dict[str, int], locale: str) -> str:
    texts = {
        "subpages": ("{n} sous-page", "{n} sous-pages"),
        "subplaces": ("{n} sous-lieu", "{n} sous-lieux"),
        "images": ("{n} image", "{n} images"),
        "relations": ("{n} lien", "{n} liens"),
        "comments": ("{n} commentaire", "{n} commentaires"),
        "pins": ("{n} épingle", "{n} épingles"),
    }
    return ", ".join(i18n.translate(msgn(*texts[key], counts[key]), locale)
                     for key in texts if counts.get(key))


@threaded
def list_trash(request: Request) -> Response:
    cid = path_id(request)
    locale = i18n.request_locale(request.scope)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        rows = conn.execute(
            """SELECT t.id, t.kind, t.object_id, t.name, t.deleted_at, t.via, t.counts,
                      COALESCE(NULLIF(u.display_name, ''), u.username, '') AS deleted_by_name
                 FROM trash t LEFT JOIN users u ON u.id = t.deleted_by
                WHERE t.campaign_id = ? AND t.deleted_at >= ? ORDER BY t.deleted_at DESC, t.id DESC""",
            (cid, cutoff())).fetchall()
    return JSONResponse({"items": [{
        "id": r["id"], "kind": r["kind"], "object_id": r["object_id"], "name": r["name"],
        "deleted_at": r["deleted_at"], "expires_at": expires_at(r["deleted_at"]),
        "deleted_by_name": r["deleted_by_name"], "via": r["via"],
        "summary": _summary(json.loads(r["counts"] or "{}"), locale),
    } for r in rows]})


def _item(conn: sqlite3.Connection, request: Request) -> dict[str, Any]:
    item = scoped(conn, request, "trash", path_id(request), WHAT)
    if item["deleted_at"] < cutoff():   # dépassé : en attente de la purge du jour
        raise HTTPException(404, msg("{what} introuvable", what=msg(WHAT)))
    return item


@threaded
def restore_item(request: Request) -> Response:
    with moving():   # de la validation à la remise en place des fichiers : la purge attend
        try:
            with tx(request) as conn:
                item = _item(conn, request)
                done = restore(conn, item)
                conn.execute("DELETE FROM trash WHERE id = ?", (item["id"],))
                touch_campaign(conn, item["campaign_id"])
        except RestoreRefused as exc:
            raise HTTPException(409, i18n.message(exc)) from None
        except sqlite3.IntegrityError:
            # Clé étrangère encore cassée à la validation (cas non prévu par la vérification) : rien n'est restauré.
            log.warning("Corbeille : restauration de l'élément %s refusée par la base", path_id(request),
                        exc_info=True)
            raise HTTPException(409, "Restauration impossible : des éléments liés ont changé entre-temps. Rien n'a été restauré.") from None  # noqa: E501
        media_dir = settings(request).media_dir
        unstash_files(media_dir, done.campaign_id, done.files_back)
        drop_files(media_dir, done.campaign_id, done.files_lost)
    hub = getattr(request.app.state, "hub", None)
    if hub is not None and done.documents:
        hub.close_documents(done.documents, "reset")
    return JSONResponse({"kind": done.kind, "object_id": done.object_id})


@threaded
def delete_item(request: Request) -> Response:
    with moving():
        with tx(request) as conn:
            item = scoped(conn, request, "trash", path_id(request), WHAT)
            conn.execute("DELETE FROM trash WHERE id = ?", (item["id"],))
        drop_files(settings(request).media_dir, item["campaign_id"], json.loads(item["files"]))
    return Response(status_code=204)


@threaded
def empty_trash(request: Request) -> Response:
    cid = path_id(request)
    with moving():
        with tx(request) as conn:
            scoped(conn, request, "campaigns", cid, "Campagne")
            files = [f for (files,) in conn.execute("SELECT files FROM trash WHERE campaign_id = ?", (cid,))
                     for f in json.loads(files)]
            conn.execute("DELETE FROM trash WHERE campaign_id = ?", (cid,))
        drop_files(settings(request).media_dir, cid, files)
    return Response(status_code=204)


routes = [
    Route("/api/campaigns/{id:int}/trash", list_trash),
    Route("/api/campaigns/{id:int}/trash", empty_trash, methods=["DELETE"]),
    Route("/api/trash/{id:int}/restore", restore_item, methods=["POST"]),
    Route("/api/trash/{id:int}", delete_item, methods=["DELETE"]),
]
