"""Cartes interactives (Lieux > Carte interactive, migration 017).

Une carte = une image de fond importée (WebP en pleine définition jusqu'à 8192 px, imaging.MAP_SIDE, et version
d'affichage de 4096 px, preview, chargée d'abord ; l'interface passe à la pleine définition en zoomant)
et des épingles de lieux posées librement (position en proportion de l'image). Une carte peut représenter
un lieu (place_id) : depuis la carte du secteur, on « entre » dans celle de Valdis. Les sous-lieux
d'un lieu épinglé n'ont pas besoin d'épingle : l'interface les montre en anneaux autour de lui.
Ouvert à tout MJ qui a accès à la campagne.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import db, imaging
from .api import in_tx, parse, parse_body, path_id, scoped, settings, threaded, touch_campaign, tx
from .image_copy import SOURCES
from .schemas import ImageCopy, MapCopy, MapOrder, MapUpdate, PinSet

MAX_MAPS = 200


def map_out(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "campaign_id": row["campaign_id"], "name": row["name"], "place_id": row["place_id"],
            "url": f"/media/{row['file']}", "thumb_url": f"/media/{row['thumb']}", "original_name": row["original_name"],
            "preview_url": f"/media/{row['preview']}" if row.get("preview") else None,
            "pin_style": row.get("pin_style") or "dot",
            "width": row["width"], "height": row["height"], "sort_order": row["sort_order"],
            "updated_at": row["updated_at"]}


def pins_of(conn: sqlite3.Connection, map_id: int) -> list[dict[str, Any]]:
    return [{"place_id": r[0], "x": r[1], "y": r[2]}
            for r in conn.execute("SELECT place_id, x, y FROM map_pins WHERE map_id = ? ORDER BY id", (map_id,))]


def map_detail(conn: sqlite3.Connection, map_id: int) -> dict[str, Any]:
    row = db.row_to_dict(conn.execute("SELECT * FROM maps WHERE id = ?", (map_id,)).fetchone())
    if row is None:
        raise HTTPException(404, "Carte introuvable")
    return {**map_out(row), "pins": pins_of(conn, map_id)}


def maps_of(conn: sqlite3.Connection, campaign_id: int) -> list[dict[str, Any]]:
    pins: dict[int, list[int]] = {}
    for map_id, place_id in conn.execute(
            "SELECT p.map_id, p.place_id FROM map_pins p JOIN maps m ON m.id = p.map_id WHERE m.campaign_id = ?",
            (campaign_id,)):
        pins.setdefault(map_id, []).append(place_id)
    return [{**map_out(r), "pins": pins.get(r["id"], [])}
            for r in db.rows_to_dicts(conn.execute(
                "SELECT * FROM maps WHERE campaign_id = ? ORDER BY sort_order, id", (campaign_id,)).fetchall())]


def maps_for_place(conn: sqlite3.Connection, place_id: int) -> dict[str, list[dict[str, Any]]]:
    """Cartes où l'on trouve le lieu (épinglé lui-même, ou dans un anneau d'un ancêtre épinglé), la plus
    proche d'abord ; et cartes qui le représentent."""
    found: dict[int, dict[str, Any]] = {}
    for r in conn.execute(
            """WITH RECURSIVE anc(id, depth) AS (
                   SELECT ?, 0
                   UNION SELECT p.parent_id, a.depth + 1 FROM places p JOIN anc a ON p.id = a.id
                    WHERE p.parent_id IS NOT NULL AND a.depth < 50)
               SELECT m.id, m.name, mp.place_id, a.depth FROM anc a
                 JOIN map_pins mp ON mp.place_id = a.id JOIN maps m ON m.id = mp.map_id
                ORDER BY a.depth, m.sort_order, m.id""", (place_id,)):
        found.setdefault(r[0], {"id": r[0], "name": r[1], "via_place_id": r[2], "depth": r[3]})
    own = [{"id": r[0], "name": r[1]} for r in conn.execute(
        "SELECT id, name FROM maps WHERE place_id = ? ORDER BY sort_order, id", (place_id,))]
    return {"maps": list(found.values()), "own_maps": own}


def _check_place(conn: sqlite3.Connection, campaign_id: int, place_id: int | None) -> None:
    if place_id is None:
        return
    row = conn.execute("SELECT campaign_id FROM places WHERE id = ?", (place_id,)).fetchone()
    if row is None or row[0] != campaign_id:
        raise HTTPException(400, "Ce lieu n'existe pas dans cette campagne")


def _default_name(conn: sqlite3.Connection, campaign_id: int, place_id: int | None, original: str) -> str:
    if place_id is not None:
        row = conn.execute("SELECT name FROM places WHERE id = ?", (place_id,)).fetchone()
        if row:
            return f"Carte de {row[0]}"[:120]
    stem = original.rsplit(".", 1)[0].strip()
    return (stem or "Nouvelle carte")[:120]


# --- Images de fond ------------------------------------------------------------------------------

async def _upload(request: Request, campaign_id: int) -> tuple[imaging.StoredImage, str, dict[str, str]]:
    """Image envoyée (champ file) traitée en haute définition ; autres champs du formulaire."""
    cfg = settings(request)
    limit = cfg.max_upload_mb * 1024 * 1024
    if int(request.headers.get("content-length") or 0) > limit * 2:
        raise HTTPException(413, f"Envoi trop volumineux (max {cfg.max_upload_mb} Mo)")
    form = await request.form(max_files=1, max_fields=5)
    try:
        upload = form.get("file")
        if not hasattr(upload, "read"):
            raise HTTPException(400, "Aucun fichier reçu (champ 'file')")
        raw = await upload.read(limit + 1)
        if len(raw) > limit:
            raise HTTPException(413, f"{upload.filename} dépasse {cfg.max_upload_mb} Mo")
        fields = {k: str(v) for k, v in form.items() if isinstance(v, str)}
        name = (upload.filename or "")[:200]
    finally:
        await form.close()
    try:
        img = await run_in_threadpool(imaging.process_upload, raw, cfg.media_dir, campaign_id, imaging.MAP_SIDE,
                                      imaging.MAP_PREVIEW_SIDE)
    except imaging.ImageError as exc:
        raise HTTPException(400, f"{name} : {exc}") from exc
    return img, name, fields


def _source_row(conn: sqlite3.Connection, source: str, source_id: int, campaign_id: int) -> dict[str, Any]:
    row = db.row_to_dict(conn.execute(f"SELECT * FROM {SOURCES[source]} WHERE id = ?", (source_id,)).fetchone())
    if row is None or row["campaign_id"] != campaign_id:
        raise HTTPException(404, "Image introuvable dans cette campagne")
    return row


async def _copy(request: Request, row: dict[str, Any], campaign_id: int) -> imaging.StoredImage:
    """Copie d'une image de Maze comme fond ; d'une autre carte, pleine définition et version d'affichage."""
    try:
        return await run_in_threadpool(imaging.copy_stored, settings(request).media_dir, row["file"], row["thumb"],
                                       campaign_id, row["width"], row["height"], row.get("preview") or "")
    except imaging.ImageError as exc:
        raise HTTPException(404, str(exc)) from exc


def _optional_int(value: str | None, what: str) -> int | None:
    if value is None or value.strip() in ("", "null"):
        return None
    if not value.strip().isdigit():
        raise HTTPException(400, f"{what} : identifiant attendu")
    return int(value)


def insert_map(conn: sqlite3.Connection, campaign_id: int, img: imaging.StoredImage, name: str | None,
               place_id: int | None, original_name: str) -> int:
    """Enregistre une nouvelle carte (image déjà sur le disque) ; renvoie son identifiant."""
    _check_place(conn, campaign_id, place_id)
    if conn.execute("SELECT COUNT(*) FROM maps WHERE campaign_id = ?", (campaign_id,)).fetchone()[0] >= MAX_MAPS:
        raise HTTPException(400, f"{MAX_MAPS} cartes au plus par campagne")
    order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM maps WHERE campaign_id = ?",
                         (campaign_id,)).fetchone()[0]
    new_id = db.insert(conn, "maps", {
        "campaign_id": campaign_id, "place_id": place_id,
        "name": (name or "").strip()[:120] or _default_name(conn, campaign_id, place_id, original_name),
        "file": img.file, "thumb": img.thumb, "preview": img.preview, "original_name": original_name[:200],
        "width": img.width, "height": img.height, "sort_order": order,
    })
    touch_campaign(conn, campaign_id)
    return new_id


def replace_image(conn: sqlite3.Connection, map_id: int, img: imaging.StoredImage, original_name: str) -> dict[str, Any]:
    """Nouveau fond (les épingles restent : positions relatives) ; renvoie l'ancienne ligne."""
    old = db.row_to_dict(conn.execute("SELECT * FROM maps WHERE id = ?", (map_id,)).fetchone())
    if old is None:
        raise HTTPException(404, "Carte introuvable (supprimée entre-temps ?)")
    db.update(conn, "maps", map_id, {"file": img.file, "thumb": img.thumb, "preview": img.preview,
                                     "original_name": original_name[:200],
                                     "width": img.width, "height": img.height})
    touch_campaign(conn, old["campaign_id"])
    return old


# --- Routes ---------------------------------------------------------------------------------------

@threaded
def list_maps(request: Request) -> Response:
    cid = path_id(request)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        return JSONResponse({"maps": maps_of(conn, cid)})


async def create_map(request: Request) -> Response:
    cid = path_id(request)

    def _work(conn: sqlite3.Connection):
        scoped(conn, request, "campaigns", cid, "Campagne")
    await in_tx(request, _work)
    img, original, fields = await _upload(request, cid)
    try:
        place_id = _optional_int(fields.get("place_id"), "place_id")

        def _work(conn: sqlite3.Connection):
            scoped(conn, request, "campaigns", cid, "Campagne")
            new_id = insert_map(conn, cid, img, fields.get("name"), place_id, original)
            out = map_detail(conn, new_id)
            return out
        out = await in_tx(request, _work)
    except BaseException:
        imaging.delete_files(settings(request).media_dir, img.file, img.thumb, img.preview)
        raise
    return JSONResponse(out, status_code=201)


async def copy_map(request: Request) -> Response:
    cid = path_id(request)
    data = await parse(request, MapCopy)

    def _work(conn: sqlite3.Connection):
        scoped(conn, request, "campaigns", cid, "Campagne")
        _check_place(conn, cid, data.place_id)
        row = _source_row(conn, data.source, data.id, cid)
        return row
    row = await in_tx(request, _work)
    img = await _copy(request, row, cid)
    try:
        def _work(conn: sqlite3.Connection):
            scoped(conn, request, "campaigns", cid, "Campagne")
            name = data.name or row.get("caption") or (row["name"] if data.source == "map" else None)
            new_id = insert_map(conn, cid, img, name, data.place_id, row.get("original_name") or "")
            out = map_detail(conn, new_id)
            return out
        out = await in_tx(request, _work)
    except BaseException:
        imaging.delete_files(settings(request).media_dir, img.file, img.thumb, img.preview)
        raise
    return JSONResponse(out, status_code=201)


@threaded
def reorder_maps(request: Request) -> Response:
    cid = path_id(request)
    data = parse_body(request, MapOrder)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        existing = {r[0] for r in conn.execute("SELECT id FROM maps WHERE campaign_id = ?", (cid,))}
        if set(data.ids) != existing or len(data.ids) != len(existing):
            raise HTTPException(400, "La liste doit contenir exactement les cartes de la campagne")
        conn.executemany("UPDATE maps SET sort_order = ? WHERE id = ?", [(i, x) for i, x in enumerate(data.ids)])
    return Response(status_code=204)


@threaded
def get_map(request: Request) -> Response:
    mid = path_id(request)
    with tx(request) as conn:
        scoped(conn, request, "maps", mid, "Carte")
        return JSONResponse(map_detail(conn, mid))


@threaded
def update_map(request: Request) -> Response:
    mid = path_id(request)
    data = parse_body(request, MapUpdate)
    values = data.model_dump(exclude_unset=True)
    for key in ("name", "pin_style"):   # null : inchangé (seul place_id null a un sens)
        if key in values and values[key] is None:
            values.pop(key)
    with tx(request) as conn:
        current = scoped(conn, request, "maps", mid, "Carte")
        if "place_id" in values:
            _check_place(conn, current["campaign_id"], values["place_id"])
        db.update(conn, "maps", mid, values)
        touch_campaign(conn, current["campaign_id"])
        return JSONResponse(map_detail(conn, mid))


async def upload_map_image(request: Request) -> Response:
    mid = path_id(request)

    def _work(conn: sqlite3.Connection):
        current = scoped(conn, request, "maps", mid, "Carte")
        return current
    current = await in_tx(request, _work)
    img, original, _ = await _upload(request, current["campaign_id"])
    return await _set_image(request, mid, img, original)


async def copy_map_image(request: Request) -> Response:
    mid = path_id(request)
    data = await parse(request, ImageCopy)

    def _work(conn: sqlite3.Connection):
        current = scoped(conn, request, "maps", mid, "Carte")
        row = _source_row(conn, data.source, data.id, current["campaign_id"])
        return current, row
    current, row = await in_tx(request, _work)
    img = await _copy(request, row, current["campaign_id"])
    return await _set_image(request, mid, img, row.get("original_name") or "")


async def _set_image(request: Request, mid: int, img: imaging.StoredImage, original: str) -> Response:
    media_dir = settings(request).media_dir
    try:
        def _work(conn: sqlite3.Connection):
            scoped(conn, request, "maps", mid, "Carte")
            old = replace_image(conn, mid, img, original)
            out = map_detail(conn, mid)
            return old, out
        old, out = await in_tx(request, _work)
    except BaseException:
        imaging.delete_files(media_dir, img.file, img.thumb, img.preview)
        raise
    imaging.delete_files(media_dir, old["file"], old["thumb"], old["preview"])
    return JSONResponse(out)


@threaded
def delete_map(request: Request) -> Response:
    mid = path_id(request)
    with tx(request) as conn:
        row = scoped(conn, request, "maps", mid, "Carte")
        conn.execute("DELETE FROM maps WHERE id = ?", (mid,))
        touch_campaign(conn, row["campaign_id"])
    imaging.delete_files(settings(request).media_dir, row["file"], row["thumb"], row["preview"])
    return Response(status_code=204)


@threaded
def set_pin(request: Request) -> Response:
    """Pose ou déplace l'épingle d'un lieu sur la carte."""
    mid = path_id(request)
    pid = path_id(request, "place_id")
    data = parse_body(request, PinSet)
    with tx(request) as conn:
        current = scoped(conn, request, "maps", mid, "Carte")
        _check_place(conn, current["campaign_id"], pid)
        conn.execute("""INSERT INTO map_pins (map_id, place_id, x, y) VALUES (?, ?, ?, ?)
                        ON CONFLICT (map_id, place_id) DO UPDATE SET x = excluded.x, y = excluded.y""",
                     (mid, pid, data.x, data.y))
        conn.execute("UPDATE maps SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?", (mid,))
    return JSONResponse({"map_id": mid, "place_id": pid, "x": data.x, "y": data.y})


@threaded
def delete_pin(request: Request) -> Response:
    mid = path_id(request)
    pid = path_id(request, "place_id")
    with tx(request) as conn:
        scoped(conn, request, "maps", mid, "Carte")
        deleted = conn.execute("DELETE FROM map_pins WHERE map_id = ? AND place_id = ?", (mid, pid)).rowcount
        if not deleted:
            raise HTTPException(404, "Ce lieu n'est pas sur cette carte")
        conn.execute("UPDATE maps SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?", (mid,))
    return Response(status_code=204)


routes = [
    Route("/api/campaigns/{id:int}/maps", list_maps),
    Route("/api/campaigns/{id:int}/maps", create_map, methods=["POST"]),
    Route("/api/campaigns/{id:int}/maps/copy", copy_map, methods=["POST"]),
    Route("/api/campaigns/{id:int}/maps/order", reorder_maps, methods=["PUT"]),
    Route("/api/maps/{id:int}", get_map),
    Route("/api/maps/{id:int}", update_map, methods=["PATCH"]),
    Route("/api/maps/{id:int}", delete_map, methods=["DELETE"]),
    Route("/api/maps/{id:int}/image", upload_map_image, methods=["POST"]),
    Route("/api/maps/{id:int}/image/copy", copy_map_image, methods=["POST"]),
    Route("/api/maps/{id:int}/pins/{place_id:int}", set_pin, methods=["PUT"]),
    Route("/api/maps/{id:int}/pins/{place_id:int}", delete_pin, methods=["DELETE"]),
]
