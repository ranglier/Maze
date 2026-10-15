"""Réutiliser une image déjà dans Maze : catalogue des images d'une campagne (bibliothèque, galeries,
illustrations des fiches rédigées, fonds des cartes interactives) et copie vers une fiche.

Ouvert à tout MJ qui a accès à la campagne (galerie d'un PNJ, d'une faction, d'un lieu ; éditeur des fiches
rédigées). La copie est identique (imaging.copy_stored) et indépendante : supprimer l'original ne
touche pas la copie, et inversement.
"""

from __future__ import annotations

import re
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import db, imaging
from .api import IMAGE_FIELD, TABLES, _store_images, entity_detail, parse_body, path_id, scoped, settings, threaded, touch_campaign, tx
from .schemas import ImageCopy

DOC_IMAGE = re.compile(r'data-doc-image="(\d+)"')
SOURCES = {"library": "library_images", "gallery": "images", "document": "document_images", "map": "maps"}
KIND_LABEL = {"npc": "PNJ", "faction": "Faction", "place": "Lieu"}
DOC_LABEL = {"campaign": "Fiche de campagne", "scenario": "Scénario", "page": "Page"}


def _catalog(conn, cid: int) -> list[dict[str, Any]]:
    names = {(kind, r[0]): r[1] for kind, table in TABLES.items()
             for r in conn.execute(f"SELECT id, name FROM {table} WHERE campaign_id = ?", (cid,))}
    docs = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, title, kind FROM documents WHERE campaign_id = ?", (cid,))}
    used: set[int] = set()
    for (content,) in conn.execute("SELECT content FROM documents WHERE campaign_id = ?", (cid,)):
        used.update(int(i) for i in DOC_IMAGE.findall(content or ""))

    def item(source: str, r, origin: str) -> dict[str, Any]:
        return {"source": source, "id": r["id"], "url": f"/media/{r['file']}", "thumb_url": f"/media/{r['thumb']}",
                "caption": r["caption"] if "caption" in r.keys() else r["name"] if "name" in r.keys() else "", "width": r["width"], "height": r["height"],
                "origin": origin}

    out = [item("library", r, "Bibliothèque")
           for r in conn.execute("SELECT * FROM library_images WHERE campaign_id = ? ORDER BY id DESC", (cid,))]
    out += [item("gallery", r, f"{KIND_LABEL[r['owner_kind']]} : {names.get((r['owner_kind'], r['owner_id']), '')}")
            for r in conn.execute("SELECT * FROM images WHERE campaign_id = ? ORDER BY owner_kind, owner_id, sort_order, id",
                                  (cid,))]
    for r in conn.execute("SELECT * FROM document_images WHERE campaign_id = ? ORDER BY id DESC", (cid,)):
        doc = docs.get(r["document_id"])
        if r["id"] in used and doc:
            out.append(item("document", r, f"{DOC_LABEL.get(doc[1], 'Fiche')} : {doc[0] or 'Sans titre'}"))
    out += [item("map", r, "Carte interactive")
            for r in conn.execute("SELECT * FROM maps WHERE campaign_id = ? ORDER BY sort_order, id", (cid,))]
    return out


@threaded
def campaign_images(request: Request) -> Response:
    cid = path_id(request)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        return JSONResponse({"images": _catalog(conn, cid)})


def _source(conn, data: ImageCopy, campaign_id: int) -> dict[str, Any]:
    row = db.row_to_dict(conn.execute(f"SELECT * FROM {SOURCES[data.source]} WHERE id = ?", (data.id,)).fetchone())
    if row is None or row["campaign_id"] != campaign_id:
        raise HTTPException(404, "Image introuvable dans cette campagne")
    return row


def _copy(request: Request, row: dict[str, Any], campaign_id: int) -> imaging.StoredImage:
    """Copie vers une fiche ; d'un fond de carte, la version d'affichage (4096 px) plutôt que la pleine
    définition."""
    media_dir = settings(request).media_dir
    try:
        if row.get("preview"):
            width, height = imaging.size_of(media_dir / row["preview"])
            return imaging.copy_stored(media_dir, row["preview"], row["thumb"], campaign_id, width, height)
        return imaging.copy_stored(media_dir, row["file"], row["thumb"], campaign_id, row["width"], row["height"])
    except imaging.ImageError as exc:
        raise HTTPException(404, str(exc)) from exc


def _copy_to_entity(kind: str):
    @threaded
    def handler(request: Request) -> Response:
        eid = path_id(request)
        data = parse_body(request, ImageCopy)
        with tx(request) as conn:
            entity = scoped(conn, request, TABLES[kind], eid, "Fiche")
            row = _source(conn, data, entity["campaign_id"])
        img = _copy(request, row, entity["campaign_id"])
        try:
            with tx(request) as conn:
                entity = scoped(conn, request, TABLES[kind], eid, "Fiche")  # supprimée entre-temps ?
                _store_images(conn, kind, eid, entity, [(img, row.get("original_name") or "")])
                new_id = conn.execute("SELECT id FROM images WHERE file = ?", (img.file,)).fetchone()[0]
                caption = row.get("caption") or (row["name"] if data.source == "map" else "")
                if caption:
                    conn.execute("UPDATE images SET caption = ? WHERE id = ?", (caption, new_id))
                if data.set_cover:
                    conn.execute(f"UPDATE {TABLES[kind]} SET {IMAGE_FIELD[kind]} = ? WHERE id = ?", (new_id, eid))
                touch_campaign(conn, entity["campaign_id"])
                out = entity_detail(conn, kind, eid)
        except BaseException:
            imaging.delete_files(settings(request).media_dir, img.file, img.thumb)
            raise
        return JSONResponse(out, status_code=201)
    return handler


@threaded
def copy_to_document(request: Request) -> Response:
    did = path_id(request)
    data = parse_body(request, ImageCopy)
    with tx(request) as conn:
        doc = scoped(conn, request, "documents", did, "Fiche")
        row = _source(conn, data, doc["campaign_id"])
    img = _copy(request, row, doc["campaign_id"])
    try:
        with tx(request) as conn:
            scoped(conn, request, "documents", did, "Fiche")
            new_id = db.insert(conn, "document_images", {
                "campaign_id": doc["campaign_id"], "document_id": did, "file": img.file, "thumb": img.thumb,
                "width": img.width, "height": img.height,
            })
    except BaseException:
        imaging.delete_files(settings(request).media_dir, img.file, img.thumb)
        raise
    return JSONResponse({"id": new_id, "url": f"/media/{img.file}", "width": img.width, "height": img.height},
                        status_code=201)


routes = [
    Route("/api/campaigns/{id:int}/images", campaign_images),
    Route("/api/npcs/{id:int}/images/copy", _copy_to_entity("npc"), methods=["POST"]),
    Route("/api/factions/{id:int}/images/copy", _copy_to_entity("faction"), methods=["POST"]),
    Route("/api/places/{id:int}/images/copy", _copy_to_entity("place"), methods=["POST"]),
    Route("/api/documents/{id:int}/images/copy", copy_to_document, methods=["POST"]),
]
