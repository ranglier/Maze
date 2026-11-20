"""Outils regroupés du connecteur Claude (novembre 2026) : 42 outils remplacés par un petit jeu que claude.ai charge
en entier (il n'en chargeait qu'une partie, et Claude ne trouvait pas les autres).

Lecture : list (listes filtrées et paginées), get (une fiche, un événement, une carte), view (aperçu d'une image ou
d'une carte). Écriture par lots : create, update, link, unlink, delete, add_image, edit_image prennent `items`
(1 à MAX_ITEMS éléments). Chaque élément passe par le traitement d'avant (mcp.create_entry, mcp.set_membership,
mcp_images.add_image…) : mêmes validations, mêmes contrôles de droits (check_write, check_delete), mêmes routes REST.
Un élément ne dépend pas des autres : il réussit ou échoue seul (résultat {index, ok, id?, error?}) et a sa ligne
dans le journal du connecteur (mcp.Batch, écrite par mcp._call_tool).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.concurrency import run_in_threadpool

from . import db, entries, mcp_images, mcp_maps
from .maps import maps_of
from .mcp import (
    COMMENT_FIELDS_DOC, ENTRY_TABLES, EVENTS_MAX, FIELDS_DOC, ID, Batch, Context,
    ToolError, _event_mentions, _event_out, _events, _fold, _int, _kind, _page, _paging, _relations, _short, _timeline,
    _tool, TIMELINE_OFF, log,
)
from . import mcp

MAX_ITEMS = 100
LIST_DEFAULT, LIST_MAX = 50, 200
LIST_KINDS = ("npc", "faction", "place", "event", "map", "relation", "library", "comment")
# Filtres de list permis pour chaque kind (un filtre sans objet est refusé plutôt qu'ignoré).
LIST_FILTERS = {
    "npc": ("query", "faction_id", "place_id", "status", "tag"),
    "faction": ("query", "faction_id", "parent_id", "place_id", "tag", "type"),
    "place": ("query", "parent_id", "entity", "tag", "type"),
    "event": ("query", "tag", "entity"),
    "map": ("query", "place_id"),
    "relation": ("entity", "type"),
    "library": ("query",),
    "comment": ("entity", "query", "status"),
}
FILTER_NAMES = ("query", "faction_id", "place_id", "parent_id", "status", "tag", "type", "entity")
REF_KINDS = {"npc": "npcs", "faction": "factions", "place": "places", "doc": "documents"}


# --- Aides -------------------------------------------------------------------------------------

def _ref(value: Any, key: str, allowed: tuple[str, ...]) -> tuple[str, int]:
    """Référence « kind:id » (npc:12, faction:3, place:7, doc:5 ; document:5 accepté)."""
    match = re.fullmatch(r"\s*([a-z]+)\s*:\s*(\d{1,15})\s*", str(value or ""))
    kind = {"document": "doc"}.get(match.group(1), match.group(1)) if match else None
    if kind not in allowed:
        raise ToolError(f"Paramètre {key} : « kind:id » avec kind parmi {', '.join(allowed)} (ex. {allowed[0]}:12)")
    return kind, int(match.group(2))


def _only(item: dict[str, Any], allowed: tuple[str, ...], what: str) -> None:
    unknown = sorted(set(item) - set(allowed))
    if unknown:
        raise ToolError(f"Champs inconnus pour {what} : {', '.join(unknown)} (champs possibles : {', '.join(allowed)})")


def _pick(item: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: item[k] for k in keys if k in item}


def _items(args: dict[str, Any]) -> list[Any]:
    items = args.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise ToolError(f"Paramètre items : liste de 1 à {MAX_ITEMS} éléments (objets)")
    return items


Work = Callable[[Context, dict[str, Any]], Awaitable[dict[str, Any]]]


async def _batch(ctx: Context, args: dict[str, Any], work: Work) -> Batch:
    """Traite les éléments un par un, chacun dans son propre contexte (droits, journal) : un échec n'arrête pas les
    suivants et n'annule pas les précédents."""
    items = _items(args)
    batch = Batch(results=[])
    for index, item in enumerate(items):
        sub = ctx.fresh()
        try:
            if not isinstance(item, dict):
                raise ToolError("Élément invalide : objet attendu")
            out = await work(sub, item)
            batch.results.append({"index": index, "ok": True, **out})
            batch.log.append((sub.campaign_id, sub.target, True, ""))
            continue
        except ToolError as exc:
            error = str(exc)
        except sqlite3.OperationalError as exc:
            if not db.is_busy(exc):
                log.exception("Outil MCP, élément %s", index)
                error = "Erreur interne de Maze (voir le journal du serveur)"
            else:
                error = db.BUSY_MESSAGE
        except Exception:  # noqa: BLE001 - un élément en panne ne doit pas faire perdre le résultat des autres
            log.exception("Outil MCP, élément %s", index)
            error = "Erreur interne de Maze (voir le journal du serveur)"
        batch.results.append({"index": index, "ok": False, "error": error})
        batch.log.append((sub.campaign_id, sub.target, False, error))
    return batch


def _named(ctx: Context, kind: str, row_id: int, name: str | None) -> None:
    """Cible lisible dans le journal : « npc:12 « Sœur Agathe » »."""
    ctx.target = f"{kind}:{row_id} « {name} »" if name else f"{kind}:{row_id}"


def _entry_fields(item: dict[str, Any], control: tuple[str, ...]) -> dict[str, Any]:
    """Champs d'une fiche : à plat dans l'élément (ou, comme avant, dans fields)."""
    fields = {k: v for k, v in item.items() if k not in control and k != "fields"}
    nested = item.get("fields")
    if nested is not None:
        if not isinstance(nested, dict):
            raise ToolError("Paramètre fields : objet attendu")
        fields.update(nested)
    return fields


def _entry_out(row: dict[str, Any]) -> dict[str, Any]:
    out = {"id": row["id"], "name": row["name"], "revision": row.get("revision")}
    for key in ("ignored_fields", "note"):
        if key in row:
            out[key] = row[key]
    return out


SOURCE_KEYS = {"url": "url", "library_image_id": "from_library_image_id", "image_id": "from_image_id",
               "document_image_id": "from_document_image_id", "from_library_image_id": "from_library_image_id",
               "from_image_id": "from_image_id", "from_document_image_id": "from_document_image_id"}


def _source(item: dict[str, Any]) -> dict[str, Any]:
    """Source d'une image (url, library_image_id, image_id, document_image_id ; anciens noms from_* acceptés)."""
    out: dict[str, Any] = {}
    for key, internal in SOURCE_KEYS.items():
        if item.get(key) is not None:
            if internal in out:
                raise ToolError("Indiquer une source et une seule : url, library_image_id, image_id ou "
                                "document_image_id")
            out[internal] = item[key]
    if len(out) != 1:
        raise ToolError("Indiquer une source et une seule : url, library_image_id, image_id ou document_image_id")
    return out


# --- list ----------------------------------------------------------------------------------------

def _filters(args: dict[str, Any], kind: str) -> dict[str, Any]:
    given = {k: args[k] for k in FILTER_NAMES if args.get(k) is not None and args.get(k) != ""}
    refused = sorted(set(given) - set(LIST_FILTERS[kind]))
    if refused:
        raise ToolError(f"Filtre(s) sans objet pour kind={kind} : {', '.join(refused)} "
                        f"(filtres possibles : {', '.join(LIST_FILTERS[kind])})")
    for key in ("faction_id", "place_id", "parent_id"):
        if key in given:
            given[key] = _int(given, key)
    return given


def _exists(conn, table: str, row_id: int, cid: int, what: str) -> None:
    if not conn.execute(f"SELECT 1 FROM {table} WHERE id = ? AND campaign_id = ?", (row_id, cid)).fetchone():
        raise ToolError(f"{what} n°{row_id} introuvable dans cette campagne")


def _list_entries(conn, cid: int, kind: str, f: dict[str, Any]) -> list[dict[str, Any]]:
    columns = {"npc": "id, name, title, status, summary, tags", "faction": "id, name, kind, summary, tags",
               "place": "id, parent_id, name, kind, summary, tags"}[kind]
    rows = db.rows_to_dicts(conn.execute(
        f"SELECT {columns} FROM {ENTRY_TABLES[kind]} WHERE campaign_id = ? ORDER BY sort_order, name COLLATE NOCASE",
        (cid,)).fetchall())
    keep: dict[int, dict[str, Any]] | None = None   # fiches retenues par un filtre de lien, avec rôle éventuel
    parent = f.get("faction_id", f.get("parent_id")) if kind == "faction" else f.get("parent_id")
    if kind == "npc" and "faction_id" in f:
        _exists(conn, "factions", f["faction_id"], cid, "Faction")
        keep = {m["npc_id"]: {"role": m["role"], "leader": bool(m["is_leader"])} for m in conn.execute(
            "SELECT npc_id, role, is_leader FROM memberships WHERE faction_id = ? AND npc_id IS NOT NULL",
            (f["faction_id"],))}
    elif kind == "faction" and parent is not None:
        if parent == 0:   # factions de premier niveau (membres d'aucune faction)
            subs = {r[0] for r in conn.execute(
                "SELECT m.member_faction_id FROM memberships m JOIN factions f ON f.id = m.faction_id "
                "WHERE f.campaign_id = ? AND m.member_faction_id IS NOT NULL", (cid,))}
            keep = {r["id"]: {} for r in rows if r["id"] not in subs}
        else:
            _exists(conn, "factions", parent, cid, "Faction")
            keep = {m[0]: {"role": m[1]} for m in conn.execute(
                "SELECT member_faction_id, role FROM memberships WHERE faction_id = ? AND member_faction_id IS NOT NULL",
                (parent,))}
    elif kind == "place" and "parent_id" in f:
        if f["parent_id"] != 0:
            _exists(conn, "places", f["parent_id"], cid, "Lieu")
        keep = {r["id"]: {} for r in rows if (r["parent_id"] or 0) == f["parent_id"]}
    if kind in ("npc", "faction") and "place_id" in f:
        _exists(conn, "places", f["place_id"], cid, "Lieu")
        linked = {r[0]: {"role": r[1]} for r in conn.execute(
            "SELECT entity_id, role FROM place_links WHERE place_id = ? AND entity_kind = ?", (f["place_id"], kind))}
        keep = linked if keep is None else {k: {**v, **linked[k]} for k, v in keep.items() if k in linked}
    if kind == "place" and "entity" in f:
        ek, eid = _ref(f["entity"], "entity", ("npc", "faction"))
        _exists(conn, ENTRY_TABLES[ek], eid, cid, "Fiche")
        linked = {r[0]: {"role": r[1]} for r in conn.execute(
            "SELECT place_id, role FROM place_links WHERE entity_kind = ? AND entity_id = ?", (ek, eid))}
        keep = linked if keep is None else {k: {**v, **linked[k]} for k, v in keep.items() if k in linked}
    query, tag, kind_type = _fold(f.get("query")), _fold(f.get("tag")), _fold(f.get("type"))
    out = []
    for r in rows:
        if keep is not None and r["id"] not in keep:
            continue
        tags = r.get("tags") or []
        if "status" in f and r["status"] != f["status"]:
            continue
        if tag and tag not in {_fold(t) for t in tags}:
            continue
        if kind_type and _fold(r["kind"]) != kind_type:
            continue
        if query and query not in _fold(" ".join([r["name"], r.get("title") or "", r.get("kind") or "", *tags])):
            continue
        item = {"id": r["id"], "name": r["name"]}
        if kind == "npc":
            item.update(title=r["title"], status=r["status"])
        elif kind == "faction":
            item["kind"] = r["kind"]
        else:
            item.update(kind=r["kind"], parent_id=r["parent_id"])
        if tags:
            item["tags"] = tags
        if r["summary"]:
            item["summary"] = _short(r["summary"], 80)
        if keep is not None:
            item.update(keep[r["id"]])
        out.append(item)
    return out


def _list_comments(conn, cid: int, f: dict[str, Any]) -> list[dict[str, Any]]:
    names = mcp._names(conn, cid)
    for row in conn.execute("SELECT id, title FROM documents WHERE campaign_id = ?", (cid,)):
        names[("doc", row[0])] = row[1]
    target = _ref(f["entity"], "entity", tuple(REF_KINDS)) if "entity" in f else None
    rows = []
    for r in conn.execute(
            """SELECT c.id, c.document_id, c.parent_id, c.author_name, c.body, c.quote, c.resolved, c.created_at
                 FROM comments c JOIN documents d ON d.id = c.document_id WHERE d.campaign_id = ?""", (cid,)):
        rows.append({"id": r["id"], "on": "document", "target": {"kind": "doc", "id": r["document_id"]},
                     "parent_id": r["parent_id"], "author": r["author_name"], "body": r["body"], "quote": r["quote"],
                     "field": "", "resolved": bool(r["resolved"]), "created_at": r["created_at"]})
    for r in conn.execute(
            """SELECT id, npc_id, faction_id, place_id, parent_id, field, author_name, body, quote, resolved, created_at
                 FROM entry_comments WHERE campaign_id = ?""", (cid,)):
        kind = "npc" if r["npc_id"] is not None else "faction" if r["faction_id"] is not None else "place"
        rows.append({"id": r["id"], "on": "entry", "target": {"kind": kind, "id": r[f"{kind}_id"]},
                     "parent_id": r["parent_id"], "author": r["author_name"], "body": r["body"], "quote": r["quote"],
                     "field": r["field"], "resolved": bool(r["resolved"]), "created_at": r["created_at"]})
    query = _fold(f.get("query"))
    status = f.get("status")
    if status not in (None, "open", "resolved"):
        raise ToolError("Filtre status des commentaires : open ou resolved")
    out = []
    for r in sorted(rows, key=lambda c: (c["created_at"], c["id"]), reverse=True):
        t = r["target"]
        if target is not None and (t["kind"], t["id"]) != target:
            continue
        if status is not None and r["resolved"] != (status == "resolved"):
            continue
        if query and query not in _fold(r["body"] + " " + r["quote"]):
            continue
        t["name"] = names.get((t["kind"], t["id"]))
        r["body"], r["quote"] = _short(r["body"], 300), _short(r["quote"], 120)
        if not r["field"]:
            del r["field"]
        out.append(r)
    return out


async def list_items(ctx: Context, args: dict[str, Any]) -> Any:
    cid = ctx.check_read(_int(args, "campaign_id"))
    kind = _kind(args, "kind", LIST_KINDS)
    offset, limit = _paging(args, LIST_DEFAULT, LIST_MAX)
    f = _filters(args, kind)
    head: dict[str, Any] = {"campaign_id": cid, "kind": kind}
    if kind == "event":
        enabled, name = _timeline(ctx, cid)
        events = await _events(ctx, cid)
        mentions = _event_mentions(ctx, cid, [e["id"] for e in events])
        target = _ref(f["entity"], "entity", tuple(REF_KINDS)) if "entity" in f else None
        query, tag = _fold(f.get("query")), _fold(f.get("tag"))
        items = []
        for position, e in enumerate(events):
            cited = mentions.get(e["id"], [])
            if target is not None and target not in {(m["kind"], m["id"]) for m in cited}:
                continue
            if tag and tag not in {_fold(x) for x in e.get("tags") or []}:
                continue
            if query and query not in _fold(" ".join([e["title"], e.get("date_label") or "", *(e.get("tags") or [])])):
                continue
            items.append(_event_out(e, position, cited))
        head["enabled"] = enabled
        if not enabled:
            head["note"] = TIMELINE_OFF.format(name=name)
        return {**head, **_page(items, offset, limit)}

    def load() -> dict[str, Any]:
        with ctx.transaction() as conn:
            if kind in ("npc", "faction", "place"):
                return {"items": _list_entries(conn, cid, kind, f)}
            if kind == "comment":
                return {"items": _list_comments(conn, cid, f)}
            if kind == "map":
                if "place_id" in f:
                    _exists(conn, "places", f["place_id"], cid, "Lieu")
                names = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM places WHERE campaign_id = ?", (cid,))}
                query = _fold(f.get("query"))
                return {"items": [
                    {"id": m["id"], "name": m["name"],
                     "place": {"id": m["place_id"], "name": names.get(m["place_id"])} if m["place_id"] else None,
                     "width": m["width"], "height": m["height"], "pin_style": m.get("pin_style"), "pins": len(m["pins"])}
                    for m in maps_of(conn, cid)
                    if ("place_id" not in f or m["place_id"] == f["place_id"]) and query in _fold(m["name"])],
                    "note": "Une carte peut représenter un lieu (place) ; les sous-lieux d'un lieu épinglé s'affichent en "
                            "anneaux autour de lui : seuls les lieux principaux ont besoin d'une épingle. get ou view "
                            "(kind map) pour le détail."}
            if kind == "library":
                query = _fold(f.get("query"))
                return {"items": [
                    {"id": r["id"], "caption": r["caption"], "name": r["original_name"], "width": r["width"],
                     "height": r["height"], "created_at": r["created_at"]}
                    for r in conn.execute("SELECT * FROM library_images WHERE campaign_id = ? ORDER BY id DESC", (cid,))
                    if query in _fold(f"{r['caption']} {r['original_name']}")],
                    "note": "Images déposées par le MJ (Administration > Images), sans fiche : view (kind library_image) "
                            "pour les voir, add_image avec library_image_id pour les utiliser (copie)."}
            # relation
            entity = _ref(f["entity"], "entity", ("npc", "faction")) if "entity" in f else (None, None)
            if entity[0] is not None:
                _exists(conn, ENTRY_TABLES[entity[0]], entity[1], cid, "Fiche")
            types = db.rows_to_dicts(conn.execute(
                "SELECT key, label, directed, hint FROM relation_types WHERE campaign_id = ? ORDER BY sort_order, id",
                (cid,)).fetchall())
            relations, _ = _relations(conn, cid, entity[0], entity[1], f.get("type"))
            return {"items": relations, "relation_types": types}

    data = await run_in_threadpool(load)
    items = data.pop("items")
    return {**head, **data, **_page(items, offset, limit)}


# --- get et view ---------------------------------------------------------------------------------

GET_KINDS = ("npc", "faction", "place", "document", "event", "map")
VIEW_KINDS = ("image", "document_image", "library_image", "map")


async def get_item(ctx: Context, args: dict[str, Any]) -> Any:
    kind = _kind(args, "kind", GET_KINDS)
    item_id = _int(args, "id")
    comments = args.get("include_comments") in (True, "true", 1)
    if kind in ("npc", "faction", "place"):
        return await mcp.get_entry(ctx, {"kind": kind, "id": item_id, "include_comments": comments})
    if kind == "document":
        return await mcp.get_document(ctx, {"document_id": item_id, "include_comments": comments})
    if kind == "event":
        ctx.target = f"event:{item_id}"
        cid = ctx.check_read(ctx.campaign_of("timeline_events", item_id), "Événement")
        return await mcp.list_events(ctx, {"campaign_id": cid, "event_id": item_id, "limit": EVENTS_MAX})
    row = mcp_maps._map_row(ctx, item_id)
    ctx.target = f"carte:{row['id']}"
    with ctx.transaction() as conn:
        meta = mcp_maps._overview(conn, row)
    meta["pin_style"] = row.get("pin_style")
    meta["note"] = ("Positions des épingles en pourcentages (x de gauche à droite, y de haut en bas) ; view (kind map) "
                    "pour l'aperçu quadrillé ; unplaced : lieux à épingler de préférence (link kind pin).")
    return meta


async def view_item(ctx: Context, args: dict[str, Any]) -> Any:
    kind = _kind(args, "kind", VIEW_KINDS)
    item_id = _int(args, "id")
    if kind == "map":
        return await mcp_maps.view_map(ctx, {"map_id": item_id, "grid": args.get("grid")})
    return await mcp_images.view_image(ctx, {f"{kind}_id": item_id})


# --- create ------------------------------------------------------------------------------------

CREATE_KINDS = ("npc", "faction", "place", "document", "event", "map", "relation", "comment")
DOC_KEYS = ("title", "parent_id", "content", "status")
EVENT_KEYS = ("title", "date_label", "description", "tags", "before_id")
MAP_KEYS = ("name", "place_id", *SOURCE_KEYS)
RELATION_KEYS = ("source_kind", "source_id", "target_kind", "target_id", "type", "label", "description", "directed",
                 "secret")
COMMENT_KEYS = ("target", "body", "quote", "field", "parent_id")


async def create(ctx: Context, args: dict[str, Any]) -> Any:
    kind = _kind(args, "kind", CREATE_KINDS)
    cid = _int(args, "campaign_id")

    async def work(sub: Context, item: dict[str, Any]) -> dict[str, Any]:
        sub.campaign_id = cid if cid in sub.grants else None
        if kind in ("npc", "faction", "place"):
            fields = _entry_fields(item, ())
            sub.target = f"{kind} « {fields.get('name') or ''} »"
            row = await mcp.create_entry(sub, {"kind": kind, "campaign_id": cid, "fields": fields})
            _named(sub, kind, row["id"], row["name"])
            return _entry_out(row)
        if kind == "document":
            _only(item, DOC_KEYS, "un document")
            sub.target = f"doc « {item.get('title') or ''} »"
            doc = await mcp.create_document(sub, {"campaign_id": cid, **item})
            _named(sub, "doc", doc["id"], doc["title"])
            return {"id": doc["id"], "kind": doc["kind"], "title": doc["title"], "parent_id": doc["parent_id"],
                    "revision": doc["revision"]}
        if kind == "event":
            _only(item, EVENT_KEYS, "un événement")
            sub.target = f"event « {item.get('title') or ''} »"
            event = await mcp.create_event(sub, {"campaign_id": cid, **item})
            _named(sub, "event", event["id"], event["title"])
            return {"id": event["id"], "title": event["title"], "position": event["position"],
                    "revision": event["revision"], "mentions": event["mentions"]}
        if kind == "map":
            _only(item, MAP_KEYS, "une carte")
            meta = await mcp_maps.create_map(sub, {"campaign_id": cid, **_pick(item, ("name", "place_id")),
                                                   **_source(item)})
            out = {"id": meta["map_id"], "name": meta["name"], "place": meta["place"], "width": meta["width"],
                   "height": meta["height"], "unplaced": meta["unplaced"][:30]}
            if "note" in meta:
                out["note"] = meta["note"]
            return out
        if kind == "relation":
            _only(item, RELATION_KEYS, "un lien")
            rel = await mcp.create_relation(sub, {"campaign_id": cid, **item})
            return {"id": rel["id"], "type": rel["type"], "source": f"{rel['source_kind']}:{rel['source_id']}",
                    "target": f"{rel['target_kind']}:{rel['target_id']}"}
        # comment
        _only(item, COMMENT_KEYS, "un commentaire")
        target_kind, target_id = _ref(item.get("target"), "target", tuple(REF_KINDS))
        sub.target = f"{target_kind}:{target_id}"
        owner = sub.campaign_of(REF_KINDS[target_kind], target_id)
        if owner is not None and owner in sub.grants and owner != cid:
            raise ToolError(f"{target_kind}:{target_id} n'appartient pas à la campagne {cid}")
        body = _pick(item, ("body", "quote", "parent_id"))
        if target_kind == "doc":
            if item.get("field"):
                raise ToolError("field : seulement pour un PNJ, une faction ou un lieu")
            comment = await mcp.add_comment(sub, {"document_id": target_id, **body})
        else:
            comment = await mcp.add_entry_comment(sub, {"kind": target_kind, "id": target_id, **body,
                                                        **_pick(item, ("field",))})
        sub.target = f"commentaire:{comment['id']} {target_kind}:{target_id}"
        return {"id": comment["id"], "on": "document" if target_kind == "doc" else "entry",
                "target": f"{target_kind}:{target_id}"}

    return await _batch(ctx, args, work)


# --- update ------------------------------------------------------------------------------------

UPDATE_KINDS = ("npc", "faction", "place", "event", "relation", "map", "image")


async def update(ctx: Context, args: dict[str, Any]) -> Any:
    kind = _kind(args, "kind", UPDATE_KINDS)

    async def work(sub: Context, item: dict[str, Any]) -> dict[str, Any]:
        item_id = _int(item, "id")
        if kind in ("npc", "faction", "place"):
            body: dict[str, Any] = {"kind": kind, "id": item_id, "fields": _entry_fields(item, ("id", "base_revision"))}
            if item.get("base_revision") is not None:
                body["base_revision"] = item["base_revision"]
            if not body["fields"]:
                raise ToolError("Rien à modifier : indiquer au moins un champ")
            row = await mcp.update_entry(sub, body)
            _named(sub, kind, row["id"], row["name"])
            return _entry_out(row)
        if kind == "event":
            _only(item, ("id", "base_revision", *EVENT_KEYS, "to_end"), "un événement")
            event = await mcp.update_event(sub, {"event_id": item_id, **{k: v for k, v in item.items() if k != "id"}})
            _named(sub, "event", event["id"], event["title"])
            return {"id": event["id"], "title": event["title"], "position": event["position"],
                    "revision": event["revision"]}
        if kind == "relation":
            _only(item, ("id", "type", "label", "description", "directed", "secret"), "un lien")
            rel = await mcp.update_relation(sub, {"relation_id": item_id, **{k: v for k, v in item.items() if k != "id"}})
            return {"id": rel["id"], "type": rel["type"], "label": rel["label"], "directed": rel["directed"],
                    "secret": rel["secret"]}
        if kind == "map":
            _only(item, ("id", "name", "place_id", "pin_style"), "une carte")
            out = await mcp_maps.update_map(sub, {"map_id": item_id, **{k: v for k, v in item.items() if k != "id"}})
            return {"id": out.pop("map_id"), **out}
        _only(item, ("id", "caption", "position", "set_cover"), "une image")
        out = await mcp_images.update_image(sub, {"image_id": item_id, **{k: v for k, v in item.items() if k != "id"}})
        return {"id": item_id, "owner": out["owner"],
                "cover": next((i["cover"] for i in out["images"] if i["id"] == item_id), False)}

    return await _batch(ctx, args, work)


# --- link et unlink ------------------------------------------------------------------------------

LINK_KINDS = ("membership", "place", "pin")


def _place_link_args(item: dict[str, Any]) -> dict[str, Any]:
    out = _pick(item, ("place_id", "entity_kind", "entity_id", "role"))
    if item.get("entity") is not None:
        out["entity_kind"], out["entity_id"] = _ref(item["entity"], "entity", ("npc", "faction"))
    return out


async def link(ctx: Context, args: dict[str, Any]) -> Any:
    kind = _kind(args, "kind", LINK_KINDS)

    async def work(sub: Context, item: dict[str, Any]) -> dict[str, Any]:
        if kind == "membership":
            _only(item, ("faction_id", "npc_id", "member_faction_id", "role", "is_leader"), "une appartenance")
            m = await mcp.set_membership(sub, item)
            return {"id": m["id"], "faction_id": m["faction_id"],
                    **({"npc_id": m["npc_id"]} if m.get("npc_id") is not None
                       else {"member_faction_id": m["member_faction_id"]}),
                    "role": m["role"], "is_leader": bool(m.get("is_leader"))}
        if kind == "place":
            _only(item, ("place_id", "entity_kind", "entity_id", "entity", "role"), "un lien avec un lieu")
            row = await mcp.set_place_link(sub, _place_link_args(item))
            return {"id": row["id"], "place_id": row["place_id"], "entity": f"{row['entity_kind']}:{row['entity_id']}",
                    "role": row["role"]}
        _only(item, ("map_id", "place_id", "x", "y"), "une épingle")
        out = await mcp_maps.place_on_map(sub, item)
        return out

    return await _batch(ctx, args, work)


async def unlink(ctx: Context, args: dict[str, Any]) -> Any:
    kind = _kind(args, "kind", LINK_KINDS)

    async def work(sub: Context, item: dict[str, Any]) -> dict[str, Any]:
        if kind == "membership":
            _only(item, ("faction_id", "npc_id", "member_faction_id"), "une appartenance")
            return await mcp.remove_membership(sub, item)
        if kind == "place":
            _only(item, ("place_id", "entity_kind", "entity_id", "entity"), "un lien avec un lieu")
            return await mcp.remove_place_link(sub, _place_link_args(item))
        _only(item, ("map_id", "place_id"), "une épingle")
        return await mcp_maps.remove_from_map(sub, item)

    return await _batch(ctx, args, work)


# --- delete ------------------------------------------------------------------------------------

DELETE_KINDS = ("npc", "faction", "place", "document", "event", "map", "comment", "relation", "image", "library_image")
TRASH_KINDS = ("npc", "faction", "place", "document", "event", "map")


async def delete(ctx: Context, args: dict[str, Any]) -> Any:
    kind = _kind(args, "kind", DELETE_KINDS)

    async def work(sub: Context, item: dict[str, Any]) -> dict[str, Any]:
        item_id = _int(item, "id")
        if kind in TRASH_KINDS:
            _only(item, ("id", "name", "title"), f"la suppression ({kind})")
            name = item.get("name", item.get("title"))
            if not isinstance(name, str):
                raise ToolError("Paramètre manquant : name (nom ou titre actuel exact de l'élément)")
            if kind in ("npc", "faction", "place"):
                out = await mcp.delete_entry(sub, {"kind": kind, "id": item_id, "name": name})
            elif kind == "document":
                out = await mcp.delete_document(sub, {"document_id": item_id, "title": name})
            elif kind == "event":
                out = await mcp.delete_event(sub, {"event_id": item_id, "title": name})
            else:
                out = await mcp.delete_map(sub, {"map_id": item_id, "name": name})
        elif kind == "comment":
            _only(item, ("id", "on"), "la suppression d'un commentaire")
            out = await mcp.delete_comment(sub, {"comment_id": item_id, "on": item.get("on")})
        else:
            _only(item, ("id",), f"la suppression ({kind})")
            if kind == "relation":
                await mcp.delete_relation(sub, {"relation_id": item_id})
                out = {"message": "Lien supprimé (les fiches restent)."}
            elif kind == "image":
                await mcp_images.delete_image(sub, {"image_id": item_id})
                out = {"message": "Image supprimée de la galerie."}
            else:
                await mcp_images.delete_library_image(sub, {"library_image_id": item_id})
                out = {"message": "Image retirée de la bibliothèque."}
        return {"id": item_id, "message": out["message"], **({"trash_id": out["trash_id"]} if "trash_id" in out else {})}

    return await _batch(ctx, args, work)


# --- Images --------------------------------------------------------------------------------------

async def add_image(ctx: Context, args: dict[str, Any]) -> Any:
    async def work(sub: Context, item: dict[str, Any]) -> dict[str, Any]:
        _only(item, ("target", "campaign_id", "caption", "set_cover", "alt", *SOURCE_KEYS), "une image")
        source = _source(item)
        target = str(item.get("target") or "").strip()
        kind, owner = ("library", None) if target == "library" else _ref(target, "target",
                                                                          (*entries.KINDS, "doc", "library"))
        if kind == "library":   # « library » avec campaign_id, ou « library:<id de la campagne> »
            cid = owner if owner is not None else item.get("campaign_id", args.get("campaign_id"))
            out = await mcp_images.add_library_image(sub, {"campaign_id": cid, **source, **_pick(item, ("caption",))})
            return {"id": out["library_image_id"], "target": "library", "width": out["width"], "height": out["height"]}
        if kind == "doc":
            out = await mcp_images.add_document_image(sub, {"document_id": owner, **source, **_pick(item, ("alt",))})
            return {"id": out["document_image_id"], "target": target, "width": out["width"], "height": out["height"],
                    "html": out["html"], "next": out["next"]}
        out = await mcp_images.add_image(sub, {"kind": kind, "id": owner, **source,
                                               **_pick(item, ("caption", "set_cover"))})
        return {"id": out["image_id"], "target": f"{kind}:{owner}", "width": out["width"], "height": out["height"],
                "cover": next((i["cover"] for i in out["images"] if i["id"] == out["image_id"]), False)}

    return await _batch(ctx, args, work)


async def edit_image(ctx: Context, args: dict[str, Any]) -> Any:
    async def work(sub: Context, item: dict[str, Any]) -> dict[str, Any]:
        _only(item, ("id", "image_id", "rotate", "flip", "crop", "keep_original"), "une retouche")
        image_id = _int(item, "id" if "id" in item else "image_id")
        out = await mcp_images.edit_image(sub, {"image_id": image_id, **_pick(item, ("rotate", "flip", "crop",
                                                                                    "keep_original"))})
        return {"id": out["image_id"], "original_id": out["original_id"], "original_kept": out["original_kept"],
                "width": out["width"], "height": out["height"],
                "gallery": [i["id"] for i in out["images"]]}

    return await _batch(ctx, args, work)


# --- Catalogue -----------------------------------------------------------------------------------

def _kind_enum(values: tuple[str, ...], description: str = "") -> dict[str, Any]:
    return {"type": "string", "enum": list(values), **({"description": description} if description else {})}


ITEMS = {"type": "array", "minItems": 1, "maxItems": MAX_ITEMS, "items": {"type": "object"},
         "description": f"Éléments à traiter (1 à {MAX_ITEMS}), champs selon kind (voir la description de l'outil)"}
BATCH_DOC = (f"Par lots : items, 1 à {MAX_ITEMS} éléments ; chaque élément est traité seul (un élément refusé n'empêche "
             "pas les autres) ; résultat : summary, puis results [{index, ok, id, error}] dans l'ordre des items. ")
ENTRY_DOC = FIELDS_DOC.replace("Champs (tous facultatifs sauf name à la création).",
                               "Champs à plat dans l'élément (tous facultatifs sauf name à la création) :")
REF_DOC = "références « kind:id » : npc:12, faction:3, place:7, doc:5"

TOOLS = [
    _tool("list", "Lister",
          "Liste filtrée et paginée d'une campagne, éléments abrégés (id, nom, quelques champs courts ; get pour le "
          f"détail). Pages : offset, limit ({LIST_DEFAULT} par défaut, au plus {LIST_MAX}), next_offset s'il en reste ; "
          "total = nombre d'éléments après filtres. kind et filtres : "
          "npc (query, faction_id : membres de la faction avec rôle, place_id : PNJ reliés au lieu, status, tag) ; "
          "faction (query, faction_id ou parent_id : sous-factions, 0 = factions de premier niveau, place_id, tag, "
          "type) ; place (query, parent_id : sous-lieux, 0 = lieux racines, entity : lieux reliés à npc:12 ou "
          "faction:3, tag, type : clé d'un type de lieu) ; event : chronologie dans l'ordre (query, tag, entity : "
          "événements qui citent npc:12, doc:5…) ; map : cartes interactives (query, place_id) ; relation : liens "
          "entre PNJ et factions et natures de lien (entity : liens de npc:12 ou faction:3, type : clé d'une nature) ; "
          "library : bibliothèque d'images de la campagne (query) ; comment : commentaires des fiches rédigées "
          "(on=document) et des PNJ, factions et lieux (on=entry), les plus récents d'abord (entity, query, status : "
          "open ou resolved). query : texte cherché dans le nom (sans accents ni casse) ; search pour le plein texte.",
          {"campaign_id": ID, "kind": _kind_enum(LIST_KINDS), "query": {"type": "string"},
           "faction_id": ID, "place_id": ID, "parent_id": ID, "status": {"type": "string"}, "tag": {"type": "string"},
           "type": {"type": "string"}, "entity": {"type": "string", "description": REF_DOC},
           "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": LIST_MAX}},
          ["campaign_id", "kind"], list_items),
    _tool("get", "Lire",
          "Un élément complet. npc, faction, place : tous les champs, révision (pour update), galerie d'images "
          "(cover : image de couverture), liens, appartenances (membres PNJ et sous-factions d'une faction ; "
          "member_of : factions dont elle est membre), lieux, fiches rédigées qui la citent. document : contenu HTML, "
          "révision (pour update_document), sous-pages, fiches qui y renvoient. event : description HTML complète, "
          "position, révision, fiches citées. map : épingles (positions en %) et lieux à épingler. include_comments : "
          "commentaires de la fiche (npc, faction, place, document).",
          {"kind": _kind_enum(GET_KINDS), "id": ID, "include_comments": {"type": "boolean"}}, ["kind", "id"], get_item),
    _tool("view", "Voir",
          "Aperçu en image (JPEG, 1024 px au plus). image : image de la galerie d'une fiche (ids dans get) ; "
          "document_image : illustration d'une fiche rédigée (attribut data-doc-image du contenu) ; library_image : "
          "image de la bibliothèque (list kind library) ; map : carte interactive quadrillée tous les 10 % avec ses "
          "épingles numérotées (grid=false : sans quadrillage).",
          {"kind": _kind_enum(VIEW_KINDS), "id": ID, "grid": {"type": "boolean"}}, ["kind", "id"], view_item),
    _tool("create", "Créer",
          BATCH_DOC + "Champs d'un élément selon kind. npc, faction, place : " + ENTRY_DOC + " document : title, "
          "parent_id (sans : scénario ; sous un scénario ou une page : page), content (HTML de l'éditeur), status "
          "(brouillon, pret, en_cours, joue). event (chronologie activée par le MJ) : title, date_label (date en texte "
          "libre, sans calendrier), description (HTML ou Markdown, [[Nom]] : mention de la fiche), tags, before_id "
          "(insérer avant cet événement ; sans : à la fin). map : name, place_id (lieu représenté), source : url "
          "(adresse web publique), library_image_id, image_id ou document_image_id. relation : source_kind et "
          "target_kind (npc ou faction), source_id, target_id, type (clé d'une nature de lien, get_campaign ; autre "
          "par défaut), label, description, directed, secret. comment (signé Claude) : target (" + REF_DOC + "), "
          f"body, quote (passage cité), field (section d'un PNJ, d'une faction ou d'un lieu : {COMMENT_FIELDS_DOC}), "
          "parent_id (réponse à un fil).",
          {"campaign_id": ID, "kind": _kind_enum(CREATE_KINDS), "items": ITEMS},
          ["campaign_id", "kind", "items"], create, write=True),
    _tool("update", "Modifier",
          BATCH_DOC + "Chaque élément : id, et seulement les champs à changer (les autres restent ; un champ donné "
          "remplace l'ancien texte, gardé dans l'historique de la fiche). npc, faction, place : base_revision "
          "(conseillé : révision lue avec get ou renvoyée par le dernier update ; refus si la fiche a changé depuis, "
          "et les modifications successives forment une seule version de l'historique) et les champs ; type d'un "
          "lieu : kind (clé, voir get_campaign). " + ENTRY_DOC + " event : base_revision, title, date_label, "
          "description, tags ; déplacer : before_id ou to_end. relation : type, label, description, directed, secret. "
          "map : name, place_id (null : aucun lieu), pin_style (dot, icon, hidden). image (galerie d'une fiche) : "
          "caption, position (0 = en tête), set_cover. Fiche rédigée : update_document.",
          {"kind": _kind_enum(UPDATE_KINDS), "items": ITEMS}, ["kind", "items"], update, write=True, destructive=True,
          idempotent=True),
    _tool("link", "Relier",
          BATCH_DOC + "membership : faction_id et un membre, npc_id (PNJ) ou member_faction_id (sous-faction ; pas de "
          "cycle), role, is_leader (PNJ seulement) ; crée l'appartenance ou change son rôle. place : place_id et la "
          "fiche reliée (entity : npc:12 ou faction:3, ou entity_kind et entity_id), role libre (« tenancier », "
          "« repaire »…). pin : map_id, place_id, x et y en pourcentages de l'image (0 à 100 depuis le coin haut "
          "gauche, voir view kind map) ; pose ou déplace l'épingle ; inutile pour les sous-lieux d'un lieu épinglé.",
          {"kind": _kind_enum(LINK_KINDS), "items": ITEMS}, ["kind", "items"], link, write=True, idempotent=True),
    _tool("unlink", "Détacher",
          BATCH_DOC + "Défait ce que fait link (les fiches, lieux et cartes restent). membership : faction_id et "
          "npc_id ou member_faction_id. place : place_id et entity (ou entity_kind et entity_id). pin : map_id, "
          "place_id.",
          {"kind": _kind_enum(LINK_KINDS), "items": ITEMS}, ["kind", "items"], unlink, write=True, destructive=True,
          idempotent=True),
    _tool("delete", "Supprimer",
          BATCH_DOC + "Seulement à la demande du MJ. Chaque élément : id ; pour npc, faction, place, document, event "
          "et map, aussi name : nom ou titre actuel exact (casse comprise), sinon refus sans rien supprimer. npc, "
          "faction, place, document (avec TOUTES ses sous-pages ; pas la fiche de campagne), event, map : placés "
          "dans la corbeille de la campagne pendant 30 jours, restaurables par le MJ (résultat : trash_id) ; exigent "
          "une campagne ouverte en « lecture, écriture et suppression » (Administration > Connecteur Claude). "
          "comment : aussi on (document ou entry, voir list kind comment) ; un fil part avec ses réponses ; même "
          "niveau d'accès ; définitif. relation, image (galerie d'une fiche), library_image : niveau écriture suffit ; "
          "définitif. Partent avec une fiche, et reviennent à sa restauration : ses liens, appartenances, "
          "rattachements à des lieux, galerie, historique, commentaires ; les sous-lieux d'un lieu remontent d'un "
          "niveau (rendus au lieu restauré s'ils n'ont pas bougé).",
          {"kind": _kind_enum(DELETE_KINDS), "items": {**ITEMS, "description": "[{id, name}] (voir la description)"}},
          ["kind", "items"], delete, delete=True, idempotent=True),
    _tool("add_image", "Ajouter des images",
          BATCH_DOC + "Chaque élément : une source, url (adresse http(s) publique d'une image JPEG, PNG, WebP ou GIF), "
          "library_image_id, image_id (galerie d'une fiche) ou document_image_id (copie d'une image de Maze) ; et une "
          "cible, target : npc:12, faction:3 ou place:7 (galerie de la fiche ; caption, set_cover : en faire le "
          "portrait, l'emblème ou l'illustration, sinon seulement si la fiche n'en a pas), doc:5 (illustration d'une "
          "fiche rédigée : alt ; le résultat donne le HTML à insérer avec update_document), ou library (bibliothèque "
          "de la campagne campaign_id ; caption).",
          {"campaign_id": {**ID, "description": "Campagne des éléments ciblant library"}, "items": ITEMS},
          ["items"], add_image, write=True),
    _tool("edit_image", "Retoucher des images",
          BATCH_DOC + "Chaque élément : id (image de la galerie d'une fiche), rotate (90, 180 ou 270, sens horaire), "
          "flip (horizontal ou vertical), crop ({left, top, right, bottom} en pourcentages de l'image pivotée), "
          "appliqués dans cet ordre. L'image retouchée prend la place et le rôle de l'originale ; l'originale reste "
          "en fin de galerie (keep_original=false pour la supprimer).",
          {"items": ITEMS}, ["items"], edit_image, write=True),
]
