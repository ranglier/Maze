"""Export et import d'une campagne seule (archive .zip portable d'une instance de Maze à l'autre).

Contenu de l'archive :
  campaign.json   données de la campagne (PNJ, factions, liens, natures, fiches rédigées…)
  media/*.webp    images des PNJ, factions et fiches, et bibliothèque d'images (.jpg : grands fonds de cartes)

À l'import, tout reçoit de nouveaux identifiants : les références (appartenances, liens, images,
mentions @ et images dans le texte des fiches) sont réécrites. L'historique d'édition en temps réel
n'est pas exporté : les fiches repartent de leur dernier texte enregistré.
"""

from __future__ import annotations

import collections
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from . import auth, db, defaults, entries, i18n, imaging, places
from .i18n import msg
from .admin import MAX_IMPORT_MB
from .api import in_tx, path_id, scoped, settings, threaded, tx
from .documents import MAX_DEPTH, sync_mentions
from .entry_history import COMMENT_FIELDS
from .mediafiles import FILE_NAME, FILE_NAME_RE, PIL_FORMATS

FORMAT = "maze-campaign"
VERSION = 1
# Bombe de décompression (audit M16) : campaign.json décodé d'un bloc prend environ 9 fois sa taille en mémoire
# (25 Mio : la limite que l'audit permettait). Une campagne de 1000 PNJ en fait 1,3 Mo ; l'export signale une
# campagne dont le campaign.json dépasserait MAX_JSON (export_check). Rapport décompressé / compressé : 4 pour une vraie campagne, 40 pour
# 2000 PNJ vides ; refusé au-delà de MAX_RATIO (contrôlé à partir de RATIO_FROM). Nombre d'objets JSON borné.
MAX_JSON = 25 * 1024 * 1024
MAX_RATIO = 100
RATIO_FROM = 1024 * 1024
MAX_OBJECTS = 500_000
MAX_IMAGE = 50 * 1024 * 1024
MAX_EVENTS = 5000            # événements de chronologie importés par campagne (comme timeline.MAX_EVENTS)
MAX_EVENT_TEXT = 200_000     # description d'un événement (comme un champ long de fiche)
MEDIA_NAME = FILE_NAME_RE  # extensions des fichiers produits par Maze (mediafiles)
MENTION = re.compile(r'data-mention="(npc|faction|place|doc):(\d+)"')
DOC_IMAGE = re.compile(r'data-doc-image="(\d+)"')
MEDIA_URL = re.compile(rf"/media/\d+/({FILE_NAME})")
COMMENT = re.compile(r'data-comment="(\d+)"')
TABLES = ["relation_types", "place_kinds", "factions", "npcs", "memberships", "relations", "images",
          "documents", "document_images", "library_images", "comments", "places", "place_links", "scenario_nodes",
          "maps", "map_pins", "entry_comments", "timeline_events"]


class TransferError(Exception):
    """Archive de campagne invalide."""


def _rows(conn: sqlite3.Connection, sql: str, params: tuple) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# --- Export ----------------------------------------------------------------------------------

# Sections de campaign.json après « campaign » : (clé, requête des lignes de la campagne, paramètre ?).
SECTIONS: tuple[tuple[str, str], ...] = (
    ("relation_types", "SELECT * FROM relation_types WHERE campaign_id = ?"),
    ("place_kinds", "SELECT * FROM place_kinds WHERE campaign_id = ? ORDER BY sort_order, id"),
    ("factions", "SELECT * FROM factions WHERE campaign_id = ?"),
    ("npcs", "SELECT * FROM npcs WHERE campaign_id = ?"),
    # Membres PNJ et sous-factions (member_faction_id, migration 031) : campagne de la faction.
    ("memberships", "SELECT m.* FROM memberships m JOIN factions f ON f.id = m.faction_id WHERE f.campaign_id = ? "
                    "ORDER BY m.id"),
    ("relations", "SELECT * FROM relations WHERE campaign_id = ?"),
    ("images", "SELECT * FROM images WHERE campaign_id = ?"),
    ("documents", "SELECT * FROM documents WHERE campaign_id = ? ORDER BY id"),
    ("document_images", "SELECT * FROM document_images WHERE campaign_id = ?"),
    ("library_images", "SELECT * FROM library_images WHERE campaign_id = ?"),
    ("comments", "SELECT c.* FROM comments c JOIN documents d ON d.id = c.document_id WHERE d.campaign_id = ? "
                 "ORDER BY c.id"),
    ("places", "SELECT * FROM places WHERE campaign_id = ? ORDER BY id"),
    ("place_links", "SELECT l.* FROM place_links l JOIN places p ON p.id = l.place_id WHERE p.campaign_id = ?"),
    ("scenario_nodes", "SELECT s.* FROM scenario_nodes s JOIN documents d ON d.id = s.document_id "
                       "WHERE d.campaign_id = ?"),
    ("maps", "SELECT * FROM maps WHERE campaign_id = ? ORDER BY sort_order, id"),
    ("map_pins", "SELECT p.* FROM map_pins p JOIN maps m ON m.id = p.map_id WHERE m.campaign_id = ?"),
    # Commentaires des PNJ, factions et lieux (l'historique de leurs versions n'est pas exporté).
    ("entry_comments", "SELECT * FROM entry_comments WHERE campaign_id = ? ORDER BY id"),
    # Chronologie (migration 032), dans son ordre ; timeline_mentions est reconstruite à l'import.
    ("timeline_events", "SELECT * FROM timeline_events WHERE campaign_id = ? ORDER BY sort_order, id"),
)


def _export_data(db_path: Path, campaign_id: int, version: str) -> dict[str, Any]:
    """Contenu de campaign.json."""
    # Une seule transaction de lecture : instantané cohérent de toutes les tables (audit DATA-15).
    with db.transaction(db_path, write=False) as conn:
        campaign = _rows(conn, "SELECT * FROM campaigns WHERE id = ?", (campaign_id,))[0]
        data: dict[str, Any] = {
            "format": FORMAT, "version": VERSION,
            "schema": conn.execute("PRAGMA user_version").fetchone()[0],
            "maze_version": version,
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "campaign": campaign,
        }
        for key, sql in SECTIONS:
            data[key] = _rows(conn, sql, (campaign_id,))
    return data


def _export_json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()


def export_size(db_path: Path, campaign_id: int) -> int:
    """Taille estimée du campaign.json qu'aurait l'export (sans les images), calculée par SQLite sans rien charger
    en Python (contre-revue du lot 2 : export/check faisait tout le travail de l'export) : chaque ligne mise en JSON
    par json_object (même forme compacte que l'export : à quelques octets près par ligne, échappements et nombres
    décimaux), plus les séparateurs. Au-delà de MAX_JSON, l'archive ne pourrait pas être réimportée."""
    def measured(conn: sqlite3.Connection, sql: str) -> tuple[int, int]:
        columns = [d[0] for d in conn.execute(f"SELECT * FROM ({sql}) LIMIT 0", (campaign_id,)).description]
        pairs = ", ".join(f"'{c}', \"{c}\"" for c in columns)
        size, count = conn.execute(f"SELECT COALESCE(SUM(LENGTH(CAST(json_object({pairs}) AS BLOB))), 0), COUNT(*) "
                                   f"FROM ({sql})", (campaign_id,)).fetchone()
        return size + max(count - 1, 0), count   # virgules entre les lignes

    with db.transaction(db_path, write=False) as conn:
        total, _ = measured(conn, "SELECT * FROM campaigns WHERE id = ?")
        for key, sql in SECTIONS:
            total += measured(conn, sql)[0] + len(key) + 6   # "clé":[…],
    return total + 160   # en-tête (format, version, schéma, version de Maze, date)


def export_campaign(db_path: Path, media_dir: Path, campaign_id: int, version: str) -> tuple[Path, str, int]:
    """Écrit l'archive dans un fichier temporaire ; renvoie (chemin, nom de fichier proposé, taille de
    campaign.json)."""
    data = _export_data(db_path, campaign_id, version)
    payload = _export_json(data)
    handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)  # noqa: SIM115 - gardé après fermeture
    handle.close()
    path = Path(handle.name)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("campaign.json", payload, compress_type=zipfile.ZIP_DEFLATED)
        written = set()
        icons = [{"file": k["image"], "thumb": ""} for k in data["place_kinds"] if k.get("image")]
        for img in data["images"] + data["document_images"] + data["library_images"] + data["maps"] + icons:
            for rel in (img["file"], img["thumb"], img.get("preview") or ""):
                if not rel:
                    continue
                source = media_dir / rel
                name = Path(rel).name
                if name not in written and source.is_file():
                    archive.write(source, f"media/{name}", compress_type=zipfile.ZIP_STORED)  # déjà compressé
                    written.add(name)
    stamp = datetime.now().strftime("%Y%m%d")
    return path, f"maze-campagne-{defaults.slugify(data['campaign']['name'], 40)}-{stamp}.zip", len(payload)


# --- Import ----------------------------------------------------------------------------------

_COLUMNS: dict[str, frozenset[str]] = {}


def _columns(conn: sqlite3.Connection, table: str) -> frozenset[str]:
    """Colonnes d'une table, lues une fois par processus (le schéma ne change qu'au démarrage)."""
    if table not in _COLUMNS:
        _COLUMNS[table] = frozenset(r[1] for r in conn.execute(f"PRAGMA table_info({table})"))
    return _COLUMNS[table]


def _clean(conn: sqlite3.Connection, table: str, row: Any, drop: set[str]) -> dict[str, Any]:
    """Colonnes connues de la table (exports d'autres versions), valeurs simples uniquement."""
    if not isinstance(row, dict):
        raise TransferError(msg("Donnée invalide dans {table}", table=table))
    columns = _columns(conn, table)
    out = {k: v for k, v in row.items()
           if k in columns and k not in drop and (v is None or isinstance(v, (str, int, float)))}
    for key in out.keys() & db.JSON_COLUMNS:  # étiquettes : texte JSON en base, liste attendue par db.insert
        try:
            value = json.loads(out[key]) if isinstance(out[key], str) else []
        except ValueError:
            value = []
        out[key] = [str(t) for t in value if isinstance(t, (str, int, float))] if isinstance(value, list) else []
    return out


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes | None:
    """Contenu d'un membre, décompressé sans jamais dépasser `limit` octets en mémoire (la taille annoncée par
    l'archive n'est pas crue) ; None s'il est plus grand. Archive corrompue : TransferError."""
    try:
        with archive.open(info) as member:
            raw = member.read(limit + 1)
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError) as exc:
        raise TransferError(msg("Archive corrompue ({name})", name=info.filename)) from exc
    return None if len(raw) > limit else raw


def _read_data(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        info = archive.getinfo("campaign.json")
    except KeyError:
        raise TransferError("Ce fichier n'est pas un export de campagne Maze (campaign.json absent)") from None
    too_big = msg("campaign.json trop volumineux (plus de {size} Mo)", size=MAX_JSON // (1024 * 1024))
    if info.file_size > MAX_JSON:
        raise TransferError(too_big)
    raw = _read_member(archive, info, MAX_JSON)
    if raw is None:
        raise TransferError(too_big)
    if len(raw) > RATIO_FROM and len(raw) > MAX_RATIO * max(info.compress_size, 1):
        raise TransferError("campaign.json anormalement compressé : archive refusée")
    objects = 0

    def count(obj: dict[str, Any]) -> dict[str, Any]:
        nonlocal objects
        objects += 1
        if objects > MAX_OBJECTS:
            raise TransferError("campaign.json contient trop d'éléments")
        return obj
    try:
        data = json.loads(raw, object_hook=count)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise TransferError("campaign.json illisible") from None
    finally:
        del raw
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise TransferError("Ce fichier n'est pas un export de campagne Maze")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise TransferError("Export issu d'une version plus récente de Maze : mettez Maze à jour d'abord")
    if not isinstance(data.get("campaign"), dict):
        raise TransferError("Campagne absente de l'export")
    for table in TABLES:
        if not isinstance(data.get(table, []), list):
            raise TransferError(msg("Section {table} invalide", table=table))
    return data


def _stage_images(archive: zipfile.ZipFile, data: dict[str, Any], staging: Path) -> set[str]:
    """Copie et vérifie (format attendu d'après l'extension) les images citées par l'export, avant d'ouvrir la transaction :
    une longue copie ne doit pas bloquer les écritures des autres utilisateurs."""
    names = set(archive.namelist())
    wanted = {Path(str(row.get(key) or "")).name
              for table in ("images", "document_images", "library_images", "maps", "place_kinds")
              for row in data.get(table, [])
              if isinstance(row, dict)
              for key in ("file", "thumb", "preview", "image") if row.get(key)}
    valid: set[str] = set()
    for name in wanted:
        if not MEDIA_NAME.match(name) or f"media/{name}" not in names:
            continue
        info = archive.getinfo(f"media/{name}")
        if info.file_size > MAX_IMAGE:
            continue
        try:
            raw = _read_member(archive, info, MAX_IMAGE)
        except TransferError:
            continue  # image corrompue : ignorée, comme une image illisible
        if raw is None:
            continue
        try:
            with Image.open(io.BytesIO(raw)) as probe:
                if probe.format != PIL_FORMATS[Path(name).suffix]:
                    continue
                probe.verify()
        except Exception:  # noqa: BLE001 - image corrompue ou piégée : ignorée
            continue
        (staging / name).write_bytes(raw)
        valid.add(name)
    return valid


def _unique_name(conn: sqlite3.Connection, name: str, user_id: int, locale: str = i18n.SOURCE_LOCALE) -> str:
    """Nom libre parmi les campagnes que voit celui qui importe (audit X1-M3 : comparer à toutes celles de l'instance
    révélait si un nom existait dans un autre compte). Deux campagnes de comptes différents peuvent porter le même
    nom, comme à la création."""
    user = conn.execute("SELECT id, role FROM users WHERE id = ?", (user_id,)).fetchone()
    visible = auth.visible_campaign_ids(conn, dict(user)) if user is not None else []
    if visible is None:
        existing = {r[0] for r in conn.execute("SELECT name FROM campaigns")}
    else:
        existing = {r[0] for r in conn.execute(
            f"SELECT name FROM campaigns WHERE id IN ({','.join('?' * len(visible))})", visible)}
    if name not in existing:
        return name
    n = 1
    while True:
        candidate = (i18n.tr("{name} (importée)", locale, name=name) if n == 1
                     else i18n.tr("{name} (importée {n})", locale, name=name, n=n))
        if candidate not in existing:
            return candidate
        n += 1


def import_campaign(db_path: Path, media_dir: Path, archive_path: Path, user_id: int,
                    locale: str = i18n.SOURCE_LOCALE) -> int:
    """Crée une nouvelle campagne à partir d'une archive ; renvoie son identifiant. `locale` : langue de ce que l'import
    crée lui-même (nom par défaut, suffixe « (importée) », types fournis quand l'export n'en a pas)."""
    try:
        archive = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise TransferError("Archive illisible (un fichier .zip exporté depuis Maze est attendu)") from exc
    staging = Path(tempfile.mkdtemp(prefix=".import-", dir=media_dir.parent))
    try:
        with archive:
            data = _read_data(archive)
            valid = _stage_images(archive, data, staging)
        cid = _import_rows(db_path, data, valid, user_id, locale)
        target = media_dir / str(cid)
        if target.exists():  # dossier laissé par une campagne supprimée au même numéro
            for f in staging.iterdir():
                f.replace(target / f.name)
        else:
            staging.rename(target)
        return cid
    except Exception as exc:
        if isinstance(exc, (sqlite3.Error, KeyError, TypeError, ValueError)):
            raise TransferError(msg("Données incohérentes dans l'export : {error}", error=str(exc))) from exc
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        imaging._release_memory()  # JSON décodé et images vérifiées : mémoire rendue au système, même en cas d'échec


def _import_rows(db_path: Path, data: dict[str, Any], valid: set[str], user_id: int,
                 locale: str = i18n.SOURCE_LOCALE) -> int:
    """Insère la campagne et tout son contenu dans une seule transaction ; renvoie son identifiant."""
    with db.transaction(db_path) as conn:

        campaign = _clean(conn, "campaigns", data["campaign"], {"id"})
        if "timeline_enabled" in campaign:   # chronologie activée ou non (migration 032) : 0 ou 1 seulement
            campaign["timeline_enabled"] = 1 if campaign["timeline_enabled"] == 1 else 0
        campaign["name"] = _unique_name(conn, str(campaign.get("name") or i18n.tr("Campagne importée", locale))[:200],
                                        user_id, locale)
        cid = db.insert(conn, "campaigns", campaign)
        conn.execute("INSERT INTO campaign_members (campaign_id, user_id) VALUES (?, ?)", (cid, user_id))

        def media(rel: Any) -> str | None:
            name = Path(str(rel or "")).name
            return f"{cid}/{name}" if name in valid else None

        # Natures de lien (sinon celles par défaut).
        if data.get("relation_types"):
            seen = set()
            for row in data["relation_types"]:
                values = _clean(conn, "relation_types", row, {"id", "campaign_id"})
                if values.get("key") and values["key"] not in seen:
                    seen.add(values["key"])
                    db.insert(conn, "relation_types", {**values, "campaign_id": cid})
        else:
            defaults.seed_relation_types(conn, cid, locale)

        # Types de lieux (export antérieur : types fournis) ; « lieu » existe toujours.
        seen_kinds = set()
        for row in data.get("place_kinds") or []:
            if not isinstance(row, dict):
                continue
            values = _clean(conn, "place_kinds", row, {"id", "campaign_id"})
            key, label = values.get("key"), values.get("label")
            if isinstance(key, str) and isinstance(label, str) and key.strip() and label.strip() and key not in seen_kinds:
                seen_kinds.add(key)
                icon = values.get("icon") if isinstance(values.get("icon"), str) and re.fullmatch(
                    r"[a-z][a-zA-Z0-9-]{0,30}", values["icon"]) else "pin"
                conn.execute("INSERT INTO place_kinds (campaign_id, key, label, icon, sort_order, image) "
                             "VALUES (?, ?, ?, ?, ?, ?)",
                             (cid, key[:40], label[:40], icon, len(seen_kinds), media(row.get("image")) or ""))
        if not seen_kinds:
            defaults.seed_place_kinds(conn, cid, locale)
        elif defaults.DEFAULT_PLACE_KIND not in seen_kinds:
            conn.execute("INSERT INTO place_kinds (campaign_id, key, label, icon, sort_order) VALUES (?, 'lieu', ?, 'pin', ?)",
                         (cid, defaults.place_kind_label(defaults.DEFAULT_PLACE_KIND, locale), len(seen_kinds) + 1))
        place_kind_keys = {r[0] for r in conn.execute("SELECT key FROM place_kinds WHERE campaign_id = ?", (cid,))}

        ids: dict[str, dict[int, int]] = {"npc": {}, "faction": {}, "image": {}, "document": {}, "doc_image": {},
                                          "comment": {}, "place": {}}
        portraits: list[tuple[str, int, Any]] = []
        for kind, table, ref in (("faction", "factions", "emblem_image_id"), ("npc", "npcs", "portrait_image_id")):
            for row in data.get(table, []):
                values = _clean(conn, table, row, {"id", "campaign_id", ref})
                new_id = db.insert(conn, table, {**values, "campaign_id": cid})
                ids[kind][row.get("id")] = new_id
                portraits.append((table, new_id, row.get(ref)))

        # Appartenances : PNJ membres (toutes les versions), sous-factions (member_faction_id, archives depuis la
        # migration 031). Une archive ancienne n'a que des PNJ ; une archive modifiée à la main ne peut pas créer
        # d'appartenance à soi-même ni de cycle (ligne ignorée, comme une référence absente).
        parents_of: dict[int, set[int]] = collections.defaultdict(set)

        def reaches(start: int, goal: int) -> bool:
            """goal est-elle start ou une faction dont start est membre, directement ou non ?"""
            seen, stack = set(), [start]
            while stack:
                cur = stack.pop()
                if cur == goal:
                    return True
                if cur not in seen:
                    seen.add(cur)
                    stack.extend(parents_of[cur])
            return False

        for row in data.get("memberships", []):
            if not isinstance(row, dict):
                raise TransferError(msg("Donnée invalide dans {table}", table="memberships"))
            faction = ids["faction"].get(row.get("faction_id"))
            npc = ids["npc"].get(row.get("npc_id")) if row.get("npc_id") is not None else None
            sub = (ids["faction"].get(row.get("member_faction_id"))
                   if row.get("member_faction_id") is not None and npc is None else None)
            if not faction or not (npc or sub):
                continue
            values = _clean(conn, "memberships", row, {"id", "npc_id", "faction_id", "member_faction_id"})
            role = values.get("role") if isinstance(values.get("role"), str) else ""
            if npc:
                conn.execute("INSERT OR IGNORE INTO memberships (npc_id, faction_id, role, is_leader) VALUES (?, ?, ?, ?)",
                             (npc, faction, role, int(bool(values.get("is_leader")))))
            elif not reaches(faction, sub):
                conn.execute("INSERT OR IGNORE INTO memberships (member_faction_id, faction_id, role) VALUES (?, ?, ?)",
                             (sub, faction, role))
                parents_of[sub].add(faction)

        for row in data.get("relations", []):
            src = ids.get(row.get("source_kind"), {}).get(row.get("source_id"))
            dst = ids.get(row.get("target_kind"), {}).get(row.get("target_id"))
            if src and dst and (src, row.get("source_kind")) != (dst, row.get("target_kind")):
                values = _clean(conn, "relations", row, {"id", "campaign_id", "source_id", "target_id"})
                db.insert(conn, "relations", {**values, "campaign_id": cid, "source_id": src, "target_id": dst})

        # Lieux : les parents d'abord, parcours en largeur (sans récursion : une archive peut contenir
        # une chaîne très profonde). Parent absent de l'export ou boucle : lieu rangé à la racine.
        place_rows = [r for r in data.get("places", []) if isinstance(r, dict)]
        known_ids = {r.get("id") for r in place_rows}
        children: dict[Any, list[dict[str, Any]]] = {}
        for row in place_rows:
            children.setdefault(row.get("parent_id"), []).append(row)
        done: set[int] = set()

        place_parent: dict[int, int | None] = {}
        place_level: dict[int, int] = {}

        def insert_tree(top: dict[str, Any], parent: int | None) -> None:
            queue = collections.deque([(top, parent)])
            while queue:
                row, parent_new = queue.popleft()
                if id(row) in done:
                    continue
                done.add(id(row))
                values = _clean(conn, "places", row, {"id", "campaign_id", "parent_id", "image_id"})
                if not values.get("name"):
                    continue
                if values.get("kind") not in place_kind_keys:
                    values["kind"] = "lieu"
                # Au-delà de MAX_DEPTH niveaux, rangé au dernier niveau admis (audit DATA-10).
                if parent_new is not None and place_level[parent_new] >= places.MAX_DEPTH:
                    parent_new = place_parent[parent_new]
                new_id = db.insert(conn, "places", {**values, "campaign_id": cid, "parent_id": parent_new})
                place_parent[new_id] = parent_new
                place_level[new_id] = place_level[parent_new] + 1 if parent_new is not None else 0
                ids["place"][row.get("id")] = new_id
                portraits.append(("places", new_id, row.get("image_id")))
                queue.extend((child, new_id) for child in children.get(row.get("id"), []) if child is not row)

        for row in place_rows:
            if row.get("parent_id") is None or row.get("parent_id") not in known_ids:
                insert_tree(row, None)
        for row in place_rows:  # restes : boucles dans l'export
            if id(row) not in done:
                insert_tree(row, None)
        # Export antérieur aux lieux : le champ « Lieu » des PNJ devient un lieu, comme à la migration.
        if not place_rows:
            created: dict[str, int] = {}
            for row in data.get("npcs", []):
                name = str(row.get("location") or "").strip()[:200] if isinstance(row, dict) else ""
                npc = ids["npc"].get(row.get("id")) if name else None
                if npc:
                    if name not in created:
                        created[name] = db.insert(conn, "places", {"campaign_id": cid, "name": name})
                    conn.execute("INSERT OR IGNORE INTO place_links (place_id, entity_kind, entity_id, role) "
                                 "VALUES (?, 'npc', ?, 'Se trouve ici')", (created[name], npc))
        for row in data.get("place_links", []):
            if not isinstance(row, dict):
                continue
            place = ids["place"].get(row.get("place_id"))
            entity = ids.get(row.get("entity_kind"), {}).get(row.get("entity_id")) \
                if row.get("entity_kind") in ("npc", "faction") else None
            if place and entity:
                conn.execute("INSERT OR IGNORE INTO place_links (place_id, entity_kind, entity_id, role) "
                             "VALUES (?, ?, ?, ?)", (place, row["entity_kind"], entity, str(row.get("role") or "")[:80]))

        for row in data.get("images", []):
            owner = ids.get(row.get("owner_kind"), {}).get(row.get("owner_id"))
            file = media(row.get("file"))
            thumb = media(row.get("thumb"))
            if owner and file and thumb:
                values = _clean(conn, "images", row, {"id", "campaign_id", "owner_id", "file", "thumb"})
                ids["image"][row.get("id")] = db.insert(conn, "images", {
                    **values, "campaign_id": cid, "owner_id": owner, "file": file, "thumb": thumb})
        owners = {"factions": "faction", "npcs": "npc", "places": "place"}
        for table, new_id, old_image in portraits:
            column = {"factions": "emblem_image_id", "npcs": "portrait_image_id", "places": "image_id"}[table]
            # L'image doit appartenir à cette fiche (archive incohérente ou modifiée).
            if old_image in ids["image"] and conn.execute(
                    "SELECT 1 FROM images WHERE id = ? AND owner_kind = ? AND owner_id = ?",
                    (ids["image"][old_image], owners[table], new_id)).fetchone():
                conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (ids["image"][old_image], new_id))

        # Fiches rédigées : les parents d'abord, par un parcours en largeur depuis les racines (audit M17 : coût
        # linéaire ; l'ancienne boucle repassait sur toute la liste à chaque niveau, avec une recherche linéaire
        # par fiche). Frères dans l'ordre de l'export. Ignorées : fiche dont le parent est absent de l'export ou
        # non importé, cycle, scénario sans parent, fiche de campagne en double (seule la première est gardée).
        children: dict[Any, list[dict[str, Any]]] = {}
        queue: list[dict[str, Any]] = []
        for row in data.get("documents", []):
            if isinstance(row, dict):
                parent = row.get("parent_id")
                if parent is None:
                    queue.append(row)
                else:
                    children.setdefault(parent, []).append(row)
        root_done = False
        doc_parent: dict[int, int | None] = {}
        doc_level: dict[int, int] = {}
        expanded: set[Any] = set()
        position = 0
        while position < len(queue):
            row = queue[position]
            position += 1
            parent = row.get("parent_id")
            values = _clean(conn, "documents", row, {
                "id", "campaign_id", "parent_id", "content", "revision", "updated_by",
                "content_seq", "live_epoch"})
            if values.get("kind") == "campaign":
                if root_done:
                    continue
                root_done = True
            elif parent is None:
                continue  # scénario orphelin
            parent_new = ids["document"].get(parent)
            # Au-delà de MAX_DEPTH niveaux, rattachée au dernier niveau admis (audit DATA-10).
            if parent_new is not None and doc_level[parent_new] >= MAX_DEPTH:
                parent_new = doc_parent[parent_new]
            new_id = db.insert(conn, "documents", {
                **values, "campaign_id": cid, "content": str(row.get("content") or ""), "parent_id": parent_new})
            doc_parent[new_id] = parent_new
            doc_level[new_id] = doc_level[parent_new] + 1 if parent_new is not None else 0
            old_id = row.get("id")
            ids["document"][old_id] = new_id
            if old_id not in expanded:  # identifiant en double dans l'export : enfants rattachés une seule fois
                expanded.add(old_id)
                queue.extend(children.pop(old_id, ()))

        for row in data.get("document_images", []):
            file = media(row.get("file"))
            thumb = media(row.get("thumb"))
            if file and thumb:
                values = _clean(conn, "document_images", row, {"id", "campaign_id", "document_id", "file", "thumb"})
                ids["doc_image"][row.get("id")] = db.insert(conn, "document_images", {
                    **values, "campaign_id": cid, "file": file, "thumb": thumb,
                    "document_id": ids["document"].get(row.get("document_id"))})

        for row in data.get("library_images", []):
            file = media(row.get("file"))
            thumb = media(row.get("thumb"))
            if file and thumb:
                values = _clean(conn, "library_images", row, {"id", "campaign_id", "file", "thumb"})
                db.insert(conn, "library_images", {**values, "campaign_id": cid, "file": file, "thumb": thumb})

        # Cartes interactives et épingles des lieux (positions de 0 à 1).
        map_ids: dict[Any, int] = {}
        for row in data.get("maps", []):
            if not isinstance(row, dict):
                continue
            file, thumb = media(row.get("file")), media(row.get("thumb"))
            values = _clean(conn, "maps", row, {"id", "campaign_id", "place_id", "file", "thumb", "preview"})
            values["name"] = str(values.get("name") or "").strip()[:120]
            if values.get("pin_style") not in ("dot", "icon", "hidden"):
                values["pin_style"] = "dot"
            size_ok = all(isinstance(values.get(k), int) and not isinstance(values.get(k), bool) and values[k] > 0
                          for k in ("width", "height"))
            if file and thumb and size_ok and values.get("name"):
                map_ids[row.get("id")] = db.insert(conn, "maps", {
                    **values, "campaign_id": cid, "file": file, "thumb": thumb,
                    "preview": media(row.get("preview")) or "",  # absente : la pleine définition sert d'affichage
                    "place_id": ids["place"].get(row.get("place_id"))})
        for row in data.get("map_pins", []):
            if not isinstance(row, dict):
                continue
            map_id, place = map_ids.get(row.get("map_id")), ids["place"].get(row.get("place_id"))
            x, y = row.get("x"), row.get("y")
            if map_id and place and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1
                                        for v in (x, y)):
                conn.execute("INSERT OR IGNORE INTO map_pins (map_id, place_id, x, y) VALUES (?, ?, ?, ?)",
                             (map_id, place, float(x), float(y)))

        # Cartes des scénarios : sélection et disposition propres à chaque scénario (jamais sur une page).
        scenario_docs = {r[0] for r in conn.execute(
            "SELECT id FROM documents WHERE campaign_id = ? AND kind = 'scenario'", (cid,))}
        for row in data.get("scenario_nodes", []):
            if not isinstance(row, dict) or row.get("entity_kind") not in ("npc", "faction"):
                continue
            doc = ids["document"].get(row.get("document_id"))
            entity = ids[row["entity_kind"]].get(row.get("entity_id"))
            if doc not in scenario_docs or not entity:
                continue
            values = _clean(conn, "scenario_nodes", row, {"document_id", "entity_kind", "entity_id"})
            if values.get("state") not in ("auto", "added", "excluded"):
                values["state"] = "auto"
            for axis in ("pos_x", "pos_y"):
                v = values.get(axis)
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not abs(v) <= 1e7:
                    values[axis] = None
            conn.execute(
                """INSERT OR IGNORE INTO scenario_nodes (document_id, entity_kind, entity_id, state, pos_x, pos_y)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (doc, row["entity_kind"], entity, values["state"], values.get("pos_x"), values.get("pos_y")))

        # Commentaires (les auteurs ne sont plus reliés à un compte, leur nom est conservé).
        for row in sorted((r for r in data.get("comments", []) if isinstance(r, dict)),
                          key=lambda r: r.get("id") or 0):
            doc = ids["document"].get(row.get("document_id"))
            parent = row.get("parent_id")
            if not doc or (parent is not None and parent not in ids["comment"]):
                continue
            values = _clean(conn, "comments", row, {"id", "document_id", "parent_id", "user_id"})
            if not values.get("body"):
                continue
            ids["comment"][row.get("id")] = db.insert(conn, "comments", {
                **values, "document_id": doc, "parent_id": ids["comment"].get(parent)})

        # Texte des fiches : mentions, commentaires, images et adresses des médias réécrits.
        def mention(m: re.Match) -> str:
            new = ids.get("document" if m.group(1) == "doc" else m.group(1), {}).get(int(m.group(2)), 0)
            return f'data-mention="{m.group(1)}:{new}"'

        for _old_id, new_id in ids["document"].items():
            content = conn.execute("SELECT content FROM documents WHERE id = ?", (new_id,)).fetchone()[0]
            content = MENTION.sub(mention, content)
            content = COMMENT.sub(lambda m: f'data-comment="{ids["comment"].get(int(m.group(1)), 0)}"', content)
            content = DOC_IMAGE.sub(lambda m: f'data-doc-image="{ids["doc_image"].get(int(m.group(1)), 0)}"', content)
            content = MEDIA_URL.sub(lambda m: f"/media/{cid}/{m.group(1)}", content)
            conn.execute("UPDATE documents SET content = ? WHERE id = ?", (content, new_id))
            sync_mentions(conn, new_id, content)

        # Champs longs des fiches écrits avec l'éditeur : mentions @ réécrites vers les nouvelles fiches.
        rich = {entries.TABLES[kind]: entries.keys(kind, rich=True) for kind in entries.KINDS}  # registre (M23)
        for table, cols in rich.items():
            for row in conn.execute(f"SELECT id, {', '.join(cols)} FROM {table} WHERE campaign_id = ?", (cid,)).fetchall():
                changes = {c: MENTION.sub(mention, row[c]) for c in cols if row[c] and "data-mention" in row[c]}
                if changes:
                    conn.execute(f"UPDATE {table} SET {', '.join(f'{c} = ?' for c in changes)} WHERE id = ?",
                                 (*changes.values(), row["id"]))

        # Commentaires des PNJ, factions et lieux, rattachés aux nouvelles fiches.
        roots: dict[int, tuple[int, tuple[str, int]]] = {}   # ancien fil -> (nouveau fil, fiche)
        for row in sorted((r for r in data.get("entry_comments", []) if isinstance(r, dict)),
                          key=lambda r: r.get("id") if isinstance(r.get("id"), int) else 0):
            owner = [(f"{k}_id", ids[k].get(row.get(f"{k}_id"))) for k in ("npc", "faction", "place")
                     if row.get(f"{k}_id") is not None]
            if len(owner) != 1 or not owner[0][1]:
                continue
            kind = owner[0][0][:-3]
            parent = row.get("parent_id")
            # Réponse : seulement à un fil (pas à une réponse) de la même fiche.
            if parent is not None and (parent not in roots or roots[parent][1] != owner[0]):
                continue
            values = _clean(conn, "entry_comments", row,
                            {"id", "campaign_id", "npc_id", "faction_id", "place_id", "parent_id", "user_id"})
            body = values.get("body")
            if not isinstance(body, str) or not body.strip():
                continue
            values["body"] = body[:5000]
            if parent is None:
                field = values.get("field") if isinstance(values.get("field"), str) else ""
                quote = values.get("quote") if isinstance(values.get("quote"), str) else ""
                values.update(field=field if field in COMMENT_FIELDS[kind] else "", quote=quote[:500],
                              resolved=1 if values.get("resolved") in (1, True) else 0)
            else:
                values.update(field="", quote="", resolved=0, resolved_by="", resolved_at=None)
            new_id = db.insert(conn, "entry_comments", {
                **values, "campaign_id": cid, owner[0][0]: owner[0][1],
                "parent_id": roots[parent][0] if parent is not None else None})
            if parent is None and isinstance(row.get("id"), int):
                roots[row["id"]] = (new_id, owner[0])

        # Chronologie : ordre de l'export conservé (renuméroté), mentions de la description réécrites et
        # recopiées dans timeline_mentions. Archive antérieure à la migration 032 : section absente, rien à faire.
        events = [r for r in data.get("timeline_events", []) if isinstance(r, dict)]
        events.sort(key=lambda r: (r.get("sort_order") if isinstance(r.get("sort_order"), int) else 0,
                                   r.get("id") if isinstance(r.get("id"), int) else 0))
        position = 1   # numérotation de timeline._renumber
        for row in events[:MAX_EVENTS]:
            values = _clean(conn, "timeline_events", row, {"id", "campaign_id", "sort_order", "revision"})
            title = values.get("title") if isinstance(values.get("title"), str) else ""
            if not title.strip():
                continue
            date_label = values.get("date_label") if isinstance(values.get("date_label"), str) else ""
            description = values.get("description") if isinstance(values.get("description"), str) else ""
            description = MENTION.sub(mention, description[:MAX_EVENT_TEXT])
            event_id = db.insert(conn, "timeline_events", {
                **values, "campaign_id": cid, "sort_order": position, "title": title[:200],
                "date_label": date_label[:200], "description": description, "tags": values.get("tags", [])[:100]})
            position += 1
            _sync_event_mentions(conn, event_id, description)  # PROVISOIRE : timeline.sync_event_mentions
        return cid


def _sync_event_mentions(conn: sqlite3.Connection, event_id: int, html: str) -> None:
    """PROVISOIRE (en attendant timeline.py) : fiches citées par un événement, limitées à sa campagne."""
    conn.execute("DELETE FROM timeline_mentions WHERE event_id = ?", (event_id,))
    cid = conn.execute("SELECT campaign_id FROM timeline_events WHERE id = ?", (event_id,)).fetchone()[0]
    tables = {"npc": "npcs", "faction": "factions", "place": "places", "doc": "documents"}
    for kind, tid in {(k, int(i)) for k, i in MENTION.findall(html)}:
        if conn.execute(f"SELECT 1 FROM {tables[kind]} WHERE id = ? AND campaign_id = ?", (tid, cid)).fetchone():
            conn.execute("INSERT INTO timeline_mentions (event_id, target_kind, target_id) VALUES (?, ?, ?)",
                         (event_id, kind, tid))


# --- Routes ----------------------------------------------------------------------------------

@threaded
def export_route(request: Request) -> Response:
    cid = path_id(request)
    cfg = settings(request)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
    path, filename, json_size = export_campaign(cfg.db_path, cfg.media_dir, cid, cfg.version)
    # Fichier supprimé dès son ouverture : rien ne traîne sur le disque, même si le
    # téléchargement est interrompu (l'espace est libéré à la fermeture du fichier).
    handle = path.open("rb")
    path.unlink()
    size = os.fstat(handle.fileno()).st_size

    def chunks():
        with handle:
            while block := handle.read(256 * 1024):
                yield block

    headers = {"Content-Disposition": f"attachment; filename=\"{filename}\"", "Content-Length": str(size)}
    if json_size > MAX_JSON:
        # Archive impossible à réimporter (audit M16) : signalé aux scripts ; l'interface demande avant (export_check).
        headers["X-Maze-Export-Warning"] = i18n.translate(_too_big_message(json_size), i18n.request_locale(request.scope))
    return StreamingResponse(chunks(), media_type="application/zip", headers=headers)


def _too_big_message(json_size: int) -> str:
    mib = 1024 * 1024
    return msg("campaign.json fait {size} Mo, au-delà des {max} Mo admis à l'import : cette archive ne pourra pas être réimportée",  # noqa: E501
               size=f"{json_size / mib:.1f}", max=MAX_JSON // mib)


@threaded
def export_check(request: Request) -> Response:
    """Avant l'export : l'archive pourra-t-elle être réimportée (campaign.json sous MAX_JSON) ?"""
    cid = path_id(request)
    cfg = settings(request)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
    json_size = export_size(cfg.db_path, cid)
    return JSONResponse({"json_bytes": json_size, "max_bytes": MAX_JSON, "importable": json_size <= MAX_JSON,
                         "message": None if json_size <= MAX_JSON else i18n.translate(
                             _too_big_message(json_size), i18n.request_locale(request.scope))})


async def import_route(request: Request) -> Response:
    """Corps brut : l'archive .zip. La campagne créée est attribuée à qui l'importe."""
    user = auth.current_user(request)
    cfg = settings(request)
    limit = MAX_IMPORT_MB * 1024 * 1024
    handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False, dir=cfg.data_dir)  # noqa: SIM115 - fermé plus bas
    path = Path(handle.name)
    size = 0
    try:
        with handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, msg("Archive trop volumineuse (max {size} Mo)", size=MAX_IMPORT_MB))
                await run_in_threadpool(handle.write, chunk)
        if size == 0:
            raise HTTPException(400, "Aucun fichier reçu")
        try:
            cid = await run_in_threadpool(import_campaign, cfg.db_path, cfg.media_dir, path, user["id"],
                                          i18n.request_locale(request.scope))
        except TransferError as exc:
            raise HTTPException(400, i18n.message(exc)) from exc
    finally:
        path.unlink(missing_ok=True)

    def _work(conn: sqlite3.Connection):
        row = scoped(conn, request, "campaigns", cid, "Campagne")
        row["npc_count"] = conn.execute("SELECT COUNT(*) FROM npcs WHERE campaign_id = ?", (cid,)).fetchone()[0]
        row["faction_count"] = conn.execute("SELECT COUNT(*) FROM factions WHERE campaign_id = ?", (cid,)).fetchone()[0]
        row["document_count"] = conn.execute("SELECT COUNT(*) FROM documents WHERE campaign_id = ?", (cid,)).fetchone()[0]
        return row
    row = await in_tx(request, _work)
    return JSONResponse(row, status_code=201)


routes = [
    Route("/api/campaigns/{id:int}/export", export_route),
    Route("/api/campaigns/{id:int}/export/check", export_check),
    Route("/api/campaigns/import", import_route, methods=["POST"]),
]
