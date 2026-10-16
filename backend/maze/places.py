"""Lieux : arborescence libre (région > ville > quartier > bâtiment…), reliés aux PNJ et factions
(table place_links, avec un rôle) et cités dans les fiches rédigées (@mentions « place »)."""

from __future__ import annotations

import sqlite3
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, db, defaults, i18n, imaging
from .i18n import msg
from .entry_history import record_change
from .maps import maps_for_place
from .timeline import backlinks
from .api import (
    _upload_images, check_entity, check_image_owner, get_or_404, image_out, in_tx, parse_body, path_id,
    revision_conflict, scoped, settings, threaded, thumb_url, touch_campaign, tx,
)
from .schemas import (
    KeyOrder, PlaceCreate, PlaceKindCreate, PlaceKindUpdate, PlaceLinkCreate, PlaceLinkUpdate, PlaceMove, PlaceOrder,
    PlaceUpdate,
)

NULLABLE = {"parent_id", "image_id"}
KIND_FIELDS = "id, key, label, icon, sort_order, image"
TREE_FIELDS = ("p.id, p.parent_id, p.name, p.kind, p.summary, p.tags, p.sort_order, p.image_id, p.updated_at, "
               "i.thumb AS thumb")


# Profondeur maximale de l'arbre des lieux (audit DATA-10) : nombre de lieux au-dessus d'un lieu, 32 au plus. Une
# chaîne de milliers de niveaux (archive générée par un script) dépassait les limites de récursion (SQLite, Python,
# rendu de l'arbre). Création, déplacement et import la respectent ; un arbre déjà plus profond reste utilisable.
MAX_DEPTH = 32
TOO_DEEP = msg("Trop de niveaux de sous-lieux ({max} au plus)", max=MAX_DEPTH)


def depth(conn: sqlite3.Connection, place_id: int) -> int:
    """Nombre de lieux au-dessus de celui-ci (0 : lieu à la racine)."""
    return conn.execute(
        """WITH RECURSIVE anc(id) AS (SELECT parent_id FROM places WHERE id = ?
               UNION SELECT p.parent_id FROM places p JOIN anc a ON p.id = a.id)
           SELECT COUNT(id) FROM anc""", (place_id,)).fetchone()[0]


def height(conn: sqlite3.Connection, place_id: int) -> int:
    """Niveaux de sous-lieux sous celui-ci (0 : aucun sous-lieu)."""
    return conn.execute(
        """WITH RECURSIVE tree(id, level) AS (SELECT ?, 0 UNION SELECT p.id, t.level + 1 FROM places p
               JOIN tree t ON p.parent_id = t.id WHERE t.level < 100000)
           SELECT MAX(level) FROM tree""", (place_id,)).fetchone()[0]


def _subtree(conn: sqlite3.Connection, place_id: int) -> list[int]:
    """Le lieu et tous ses sous-lieux (identifiants)."""
    return [r[0] for r in conn.execute(
        """WITH RECURSIVE tree(id) AS (SELECT ? UNION SELECT p.id FROM places p JOIN tree t ON p.parent_id = t.id)
           SELECT id FROM tree""", (place_id,))]


def _links(conn: sqlite3.Connection, place_ids: list[int]) -> list[dict[str, Any]]:
    """Liens des lieux donnés, avec le nom et la vignette du PNJ ou de la faction."""
    if not place_ids:
        return []
    marks = ",".join("?" * len(place_ids))
    rows = db.rows_to_dicts(conn.execute(
        f"""SELECT l.id, l.place_id, l.entity_kind, l.entity_id, l.role,
                   COALESCE(n.name, f.name) AS name, COALESCE(n.title, f.kind) AS sub,
                   n.status AS status, f.color AS color, i.thumb AS thumb
              FROM place_links l
              LEFT JOIN npcs n ON l.entity_kind = 'npc' AND n.id = l.entity_id
              LEFT JOIN factions f ON l.entity_kind = 'faction' AND f.id = l.entity_id
              LEFT JOIN images i ON i.id = COALESCE(n.portrait_image_id, f.emblem_image_id)
             WHERE l.place_id IN ({marks})
             ORDER BY l.entity_kind DESC, name COLLATE NOCASE""", place_ids).fetchall())
    for r in rows:
        r["thumb"] = thumb_url(r["thumb"])
    return [r for r in rows if r["name"] is not None]


def place_detail(conn: sqlite3.Connection, place_id: int) -> dict[str, Any]:
    row = get_or_404(conn, "places", place_id, "Lieu")
    row["images"] = [image_out(i) for i in db.rows_to_dicts(conn.execute(
        "SELECT * FROM images WHERE owner_kind = 'place' AND owner_id = ? ORDER BY sort_order, id",
        (place_id,)).fetchall())]
    row["mentioned_in"] = db.rows_to_dicts(conn.execute(
        """SELECT d.id, d.kind, d.title, d.status FROM document_mentions m
             JOIN documents d ON d.id = m.document_id
            WHERE m.target_kind = 'place' AND m.target_id = ? AND d.campaign_id = ?
            ORDER BY d.kind = 'campaign' DESC, d.sort_order, d.id""", (place_id, row["campaign_id"])).fetchall())
    row["timeline"] = backlinks(conn, "place", place_id, row["campaign_id"])   # événements de la chronologie
    row["links"] = _links(conn, [place_id])
    # PNJ et factions des sous-lieux (à tous les niveaux), avec le sous-lieu concerné.
    nested = [pid for pid in _subtree(conn, place_id) if pid != place_id]
    names = {r[0]: r[1] for r in conn.execute(
        f"SELECT id, name FROM places WHERE id IN ({','.join('?' * len(nested))})", nested)} if nested else {}
    row["nested_links"] = [{**link, "place_name": names.get(link["place_id"], "")} for link in _links(conn, nested)]
    row["children"] = db.rows_to_dicts(conn.execute(
        f"""SELECT {TREE_FIELDS} FROM places p LEFT JOIN images i ON i.id = p.image_id
             WHERE p.parent_id = ? ORDER BY p.sort_order, p.name COLLATE NOCASE""", (place_id,)).fetchall())
    for child in row["children"]:
        child["thumb"] = thumb_url(child["thumb"])
    row.update(maps_for_place(conn, place_id))  # cartes interactives où le trouver, et qui le représentent
    return row


def _check_parent(conn: sqlite3.Connection, campaign_id: int, parent_id: int | None, place_id: int | None) -> None:
    if parent_id is None:
        return
    parent = conn.execute("SELECT campaign_id FROM places WHERE id = ?", (parent_id,)).fetchone()
    if parent is None or parent[0] != campaign_id:
        raise HTTPException(400, "Le lieu parent n'existe pas dans cette campagne")
    if place_id is not None and parent_id in _subtree(conn, place_id):
        raise HTTPException(400, "Un lieu ne peut pas être rangé dans lui-même ni dans l'un de ses sous-lieux")
    # Profondeur (audit DATA-10) : refusée au-delà de MAX_DEPTH, sauf déplacement qui n'enfonce pas l'arbre.
    below = height(conn, place_id) if place_id is not None else 0
    deepest = depth(conn, parent_id) + 1 + below
    if deepest > MAX_DEPTH and (place_id is None or deepest > depth(conn, place_id) + below):
        raise HTTPException(400, TOO_DEEP)


def kind_out(row: dict[str, Any]) -> dict[str, Any]:
    """Type de lieu pour l'interface : image importée (icône) en adresse /media, sinon icône intégrée."""
    image = row.pop("image", "")
    return {**row, "image_url": f"/media/{image}" if image else None}


def kinds_of(conn: sqlite3.Connection, campaign_id: int) -> list[dict[str, Any]]:
    return [kind_out(k) for k in db.rows_to_dicts(conn.execute(
        f"SELECT {KIND_FIELDS} FROM place_kinds WHERE campaign_id = ? ORDER BY sort_order, id",
        (campaign_id,)).fetchall())]


def _check_kind(conn: sqlite3.Connection, campaign_id: int, kind: str | None) -> None:
    if kind is not None and not conn.execute(
            "SELECT 1 FROM place_kinds WHERE campaign_id = ? AND key = ?", (campaign_id, kind)).fetchone():
        raise HTTPException(400, "Type de lieu inconnu dans cette campagne")


def _next_order(conn: sqlite3.Connection, campaign_id: int, parent_id: int | None) -> int:
    return conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM places WHERE campaign_id = ? AND parent_id IS ?",
        (campaign_id, parent_id)).fetchone()[0]


# --- Routes ----------------------------------------------------------------------------------

@threaded
def list_places(request: Request) -> Response:
    """Arbre des lieux de la campagne et tous les liens (carte filtrée sur un lieu, fiches PNJ).
    `pinned` : lieu épinglé par l'utilisateur connecté."""
    cid = path_id(request)
    uid = auth.current_user(request)["id"]
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        places = db.rows_to_dicts(conn.execute(
            f"""SELECT {TREE_FIELDS}, pp.place_id IS NOT NULL AS pinned
                  FROM places p LEFT JOIN images i ON i.id = p.image_id
                  LEFT JOIN place_pins pp ON pp.place_id = p.id AND pp.user_id = ?
                 WHERE p.campaign_id = ? ORDER BY p.sort_order, p.name COLLATE NOCASE""", (uid, cid)).fetchall())
        links = db.rows_to_dicts(conn.execute(
            """SELECT l.id, l.place_id, l.entity_kind, l.entity_id, l.role FROM place_links l
                 JOIN places p ON p.id = l.place_id WHERE p.campaign_id = ?""", (cid,)).fetchall())
        kinds = kinds_of(conn, cid)
    for p in places:
        p["thumb"] = thumb_url(p["thumb"])
        p["pinned"] = bool(p["pinned"])
    return JSONResponse({"places": places, "links": links, "kinds": kinds})


@threaded
def pin_place(request: Request) -> Response:
    """Épingle (PUT) ou désépingle (DELETE) un lieu pour l'utilisateur connecté."""
    pid = path_id(request)
    uid = auth.current_user(request)["id"]
    with tx(request) as conn:
        scoped(conn, request, "places", pid, "Lieu")
        if request.method == "PUT":
            conn.execute("INSERT OR IGNORE INTO place_pins (user_id, place_id) VALUES (?, ?)", (uid, pid))
        else:
            conn.execute("DELETE FROM place_pins WHERE user_id = ? AND place_id = ?", (uid, pid))
    return Response(status_code=204)


@threaded
def create_place(request: Request) -> Response:
    cid = path_id(request)
    data = parse_body(request, PlaceCreate)
    values = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None or k in NULLABLE}
    values.pop("image_id", None)  # aucune image avant la création
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        _check_parent(conn, cid, values.get("parent_id"), None)
        _check_kind(conn, cid, values.get("kind"))
        values.update(campaign_id=cid, sort_order=_next_order(conn, cid, values.get("parent_id")))
        new_id = db.insert(conn, "places", values)
        touch_campaign(conn, cid)
        row = place_detail(conn, new_id)
    return JSONResponse(row, status_code=201)


@threaded
def get_place(request: Request) -> Response:
    pid = path_id(request)
    with tx(request) as conn:
        scoped(conn, request, "places", pid, "Lieu")
        row = place_detail(conn, pid)
    return JSONResponse(row)


@threaded
def update_place(request: Request) -> Response:
    pid = path_id(request)
    data = parse_body(request, PlaceUpdate)
    # null n'efface que le parent et l'illustration ; ailleurs, il est ignoré.
    values = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None or k in NULLABLE}
    base = values.pop("base_revision", None)
    separate = values.pop("new_version", False)
    with tx(request) as conn:
        current = scoped(conn, request, "places", pid, "Lieu")
        conflict = revision_conflict(conn, "place", current, base)
        if conflict is not None:
            return conflict
        _check_kind(conn, current["campaign_id"], values.get("kind"))
        if "parent_id" in values and values["parent_id"] != current["parent_id"]:
            _check_parent(conn, current["campaign_id"], values["parent_id"], pid)
            values["sort_order"] = _next_order(conn, current["campaign_id"], values["parent_id"])
        if "image_id" in values:
            check_image_owner(conn, values["image_id"], "place", pid)
        db.update(conn, "places", pid, values)
        touch_campaign(conn, current["campaign_id"])
        after = get_or_404(conn, "places", pid, "Lieu")
        record_change(conn, "place", pid, current, after, auth.current_user(request), merge=base is not None and not separate)
        row = place_detail(conn, pid)
    return JSONResponse(row)


@threaded
def delete_place(request: Request) -> Response:
    """Supprime le lieu ; ses sous-lieux remontent d'un niveau (rien d'autre n'est perdu)."""
    pid = path_id(request)
    with tx(request) as conn:
        current = scoped(conn, request, "places", pid, "Lieu")
        start = _next_order(conn, current["campaign_id"], current["parent_id"])
        children = [r[0] for r in conn.execute(
            "SELECT id FROM places WHERE parent_id = ? ORDER BY sort_order, id", (pid,))]
        conn.executemany("UPDATE places SET parent_id = ?, sort_order = ? WHERE id = ?",
                         [(current["parent_id"], start + i, child) for i, child in enumerate(children)])
        files = conn.execute("SELECT file, thumb FROM images WHERE owner_kind = 'place' AND owner_id = ?",
                             (pid,)).fetchall()
        conn.execute("DELETE FROM places WHERE id = ?", (pid,))
        touch_campaign(conn, current["campaign_id"])
    for f in files:
        imaging.delete_files(settings(request).media_dir, f["file"], f["thumb"])
    return Response(status_code=204)


@threaded
def reorder_places(request: Request) -> Response:
    cid = path_id(request)
    data = parse_body(request, PlaceOrder)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        siblings = {r[0] for r in conn.execute(
            "SELECT id FROM places WHERE campaign_id = ? AND parent_id IS ?", (cid, data.parent_id))}
        if set(data.ids) != siblings or len(data.ids) != len(siblings):
            raise HTTPException(400, "La liste doit contenir exactement les lieux de ce niveau")
        conn.executemany("UPDATE places SET sort_order = ? WHERE id = ?", [(i, x) for i, x in enumerate(data.ids)])
    return Response(status_code=204)


@threaded
def move_place(request: Request) -> Response:
    """Déplace un lieu (et ses sous-lieux) : rang parmi ses voisins, ou sous un autre lieu (ou à la racine)."""
    pid = path_id(request)
    data = parse_body(request, PlaceMove)
    with tx(request) as conn:
        current = scoped(conn, request, "places", pid, "Lieu")
        cid = current["campaign_id"]
        _check_parent(conn, cid, data.parent_id, pid)
        siblings = [r[0] for r in conn.execute(
            "SELECT id FROM places WHERE campaign_id = ? AND parent_id IS ? AND id != ? ORDER BY sort_order, id",
            (cid, data.parent_id, pid))]
        siblings.insert(min(data.index, len(siblings)), pid)
        conn.execute("UPDATE places SET parent_id = ? WHERE id = ?", (data.parent_id, pid))
        conn.executemany("UPDATE places SET sort_order = ? WHERE id = ?", [(i, x) for i, x in enumerate(siblings)])
        if current["parent_id"] != data.parent_id:
            rest = [r[0] for r in conn.execute(
                "SELECT id FROM places WHERE campaign_id = ? AND parent_id IS ? ORDER BY sort_order, id",
                (cid, current["parent_id"]))]
            conn.executemany("UPDATE places SET sort_order = ? WHERE id = ?", [(i, x) for i, x in enumerate(rest)])
        touch_campaign(conn, cid)
    return Response(status_code=204)


# --- Types de lieux --------------------------------------------------------------------------

@threaded
def create_place_kind(request: Request) -> Response:
    cid = path_id(request)
    data = parse_body(request, PlaceKindCreate)
    label = data.label.strip()
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        if conn.execute("SELECT 1 FROM place_kinds WHERE campaign_id = ? AND lower(label) = lower(?)",
                        (cid, label)).fetchone():
            raise HTTPException(409, msg("Le type « {label} » existe déjà", label=label))
        order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM place_kinds WHERE campaign_id = ?",
                             (cid,)).fetchone()[0]
        new_id = db.insert(conn, "place_kinds", {
            "campaign_id": cid, "label": label, "icon": data.icon, "sort_order": order,
            "key": defaults.unique_type_key(conn, cid, label, "place_kinds"),
        })
        row = kind_out(db.rows_to_dicts(conn.execute(
            f"SELECT {KIND_FIELDS} FROM place_kinds WHERE id = ?", (new_id,)).fetchall())[0])
    return JSONResponse(row, status_code=201)


@threaded
def update_place_kind(request: Request) -> Response:
    kid = path_id(request)
    data = parse_body(request, PlaceKindUpdate)
    values = data.model_dump(exclude_unset=True, exclude_none=True)
    with tx(request) as conn:
        current = scoped(conn, request, "place_kinds", kid, "Type de lieu")
        if "label" in values:
            values["label"] = values["label"].strip()
            if conn.execute("SELECT 1 FROM place_kinds WHERE campaign_id = ? AND lower(label) = lower(?) AND id <> ?",
                            (current["campaign_id"], values["label"], kid)).fetchone():
                raise HTTPException(409, msg("Le type « {label} » existe déjà", label=values["label"]))
        if values:
            db.update(conn, "place_kinds", kid, values, touch=False)
        row = kind_out(db.rows_to_dicts(conn.execute(
            f"SELECT {KIND_FIELDS} FROM place_kinds WHERE id = ?", (kid,)).fetchall())[0])
    return JSONResponse(row)


@threaded
def delete_place_kind(request: Request) -> Response:
    """Supprime un type ; ses lieux passent au type par défaut « Lieu »."""
    kid = path_id(request)
    with tx(request) as conn:
        current = scoped(conn, request, "place_kinds", kid, "Type de lieu")
        if current["key"] == defaults.DEFAULT_PLACE_KIND:
            raise HTTPException(400, "Le type « Lieu » est le type par défaut : il ne se supprime pas")
        conn.execute("UPDATE places SET kind = ? WHERE campaign_id = ? AND kind = ?",
                     (defaults.DEFAULT_PLACE_KIND, current["campaign_id"], current["key"]))
        conn.execute("DELETE FROM place_kinds WHERE id = ?", (kid,))
    imaging.delete_files(settings(request).media_dir, current["image"])
    return Response(status_code=204)


async def set_place_kind_image(request: Request) -> Response:
    """Image importée comme icône du type (champ file) : fond clair rendu transparent, 128 px."""
    kid = path_id(request)
    cfg = settings(request)

    def _work(conn: sqlite3.Connection):
        current = scoped(conn, request, "place_kinds", kid, "Type de lieu")
        return current
    current = await in_tx(request, _work)
    limit = cfg.max_upload_mb * 1024 * 1024
    if int(request.headers.get("content-length") or 0) > limit * 2:
        raise HTTPException(413, msg("Envoi trop volumineux (max {size} Mo)", size=cfg.max_upload_mb))
    form = await request.form(max_files=1, max_fields=2)
    try:
        upload = form.get("file")
        if not hasattr(upload, "read"):
            raise HTTPException(400, "Aucun fichier reçu (champ 'file')")
        raw = await upload.read(limit + 1)
        if len(raw) > limit:
            raise HTTPException(413, msg("{name} dépasse {size} Mo", name=upload.filename or "", size=cfg.max_upload_mb))
    finally:
        await form.close()
    try:
        rel = await run_in_threadpool(imaging.process_icon, raw, cfg.media_dir, current["campaign_id"])
    except imaging.ImageError as exc:
        raise HTTPException(400, i18n.message(exc)) from exc
    try:
        def _work(conn: sqlite3.Connection):
            current = scoped(conn, request, "place_kinds", kid, "Type de lieu")  # supprimé entre-temps ?
            conn.execute("UPDATE place_kinds SET image = ? WHERE id = ?", (rel, kid))
            row = kind_out(db.rows_to_dicts(conn.execute(
                f"SELECT {KIND_FIELDS} FROM place_kinds WHERE id = ?", (kid,)).fetchall())[0])
            return current, row
        current, row = await in_tx(request, _work)
    except BaseException:
        imaging.delete_files(cfg.media_dir, rel)
        raise
    imaging.delete_files(cfg.media_dir, current["image"])
    return JSONResponse(row)


@threaded
def delete_place_kind_image(request: Request) -> Response:
    """Retour à l'icône intégrée du type."""
    kid = path_id(request)
    with tx(request) as conn:
        current = scoped(conn, request, "place_kinds", kid, "Type de lieu")
        conn.execute("UPDATE place_kinds SET image = '' WHERE id = ?", (kid,))
        row = kind_out(db.rows_to_dicts(conn.execute(
            f"SELECT {KIND_FIELDS} FROM place_kinds WHERE id = ?", (kid,)).fetchall())[0])
    imaging.delete_files(settings(request).media_dir, current["image"])
    return JSONResponse(row)


@threaded
def reorder_place_kinds(request: Request) -> Response:
    cid = path_id(request)
    data = parse_body(request, KeyOrder)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        existing = {r[0] for r in conn.execute("SELECT id FROM place_kinds WHERE campaign_id = ?", (cid,))}
        if set(data.ids) != existing or len(data.ids) != len(existing):
            raise HTTPException(400, "La liste doit contenir exactement les types de lieux de la campagne")
        conn.executemany("UPDATE place_kinds SET sort_order = ? WHERE id = ?", [(i, x) for i, x in enumerate(data.ids)])
    return Response(status_code=204)


@threaded
def create_link(request: Request) -> Response:
    pid = path_id(request)
    data = parse_body(request, PlaceLinkCreate)
    with tx(request) as conn:
        place = scoped(conn, request, "places", pid, "Lieu")
        check_entity(conn, data.entity_kind, data.entity_id, place["campaign_id"])
        try:
            new_id = db.insert(conn, "place_links", {"place_id": pid, **data.model_dump()})
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Déjà relié à ce lieu : modifiez son rôle") from None
        touch_campaign(conn, place["campaign_id"])
        row = dict(conn.execute("SELECT * FROM place_links WHERE id = ?", (new_id,)).fetchone())
    return JSONResponse(row, status_code=201)


@threaded
def update_link(request: Request) -> Response:
    lid = path_id(request)
    data = parse_body(request, PlaceLinkUpdate)
    with tx(request) as conn:
        scoped(conn, request, "place_links", lid, "Lien")
        conn.execute("UPDATE place_links SET role = ? WHERE id = ?", (data.role, lid))
        row = dict(conn.execute("SELECT * FROM place_links WHERE id = ?", (lid,)).fetchone())
    return JSONResponse(row)


@threaded
def delete_link(request: Request) -> Response:
    lid = path_id(request)
    with tx(request) as conn:
        scoped(conn, request, "place_links", lid, "Lien")
        conn.execute("DELETE FROM place_links WHERE id = ?", (lid,))
    return Response(status_code=204)


routes = [
    Route("/api/campaigns/{id:int}/places", list_places),
    Route("/api/campaigns/{id:int}/places", create_place, methods=["POST"]),
    Route("/api/campaigns/{id:int}/places/order", reorder_places, methods=["PUT"]),
    Route("/api/campaigns/{id:int}/place-kinds", create_place_kind, methods=["POST"]),
    Route("/api/campaigns/{id:int}/place-kinds/order", reorder_place_kinds, methods=["PUT"]),
    Route("/api/place-kinds/{id:int}", update_place_kind, methods=["PATCH"]),
    Route("/api/place-kinds/{id:int}", delete_place_kind, methods=["DELETE"]),
    Route("/api/place-kinds/{id:int}/image", set_place_kind_image, methods=["POST"]),
    Route("/api/place-kinds/{id:int}/image", delete_place_kind_image, methods=["DELETE"]),
    Route("/api/places/{id:int}/move", move_place, methods=["PUT"]),
    Route("/api/places/{id:int}/pin", pin_place, methods=["PUT", "DELETE"]),
    Route("/api/places/{id:int}", get_place),
    Route("/api/places/{id:int}", update_place, methods=["PATCH"]),
    Route("/api/places/{id:int}", delete_place, methods=["DELETE"]),
    Route("/api/places/{id:int}/images", _upload_images("place"), methods=["POST"]),
    Route("/api/places/{id:int}/links", create_link, methods=["POST"]),
    Route("/api/place-links/{id:int}", update_link, methods=["PATCH"]),
    Route("/api/place-links/{id:int}", delete_link, methods=["DELETE"]),
]
