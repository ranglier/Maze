"""Dépôt d'image demandé par Claude (migration 035) : lien à usage unique vers une page de Maze où le MJ dépose une image.

Claude (claude.ai) ne peut pas transmettre à Maze une image jointe à la conversation. L'outil request_upload du
connecteur (mcp_upload.py) crée un ticket et renvoie l'adresse #/depot/<jeton> de l'interface (UploadPage.jsx) ; le MJ,
connecté à Maze avec un compte qui a accès à la campagne, y dépose l'image, qui arrive directement dans la cible :
bibliothèque de la campagne, galerie d'un PNJ, d'une faction ou d'un lieu, illustration d'une fiche rédigée (comme
add_document_image : Claude l'insère ensuite dans le texte avec update_document). Claude interroge ensuite le ticket
(request_upload avec ticket) pour connaître l'image créée.

Jeton : 32 caractères aléatoires, conservé haché (SHA-256, connector.digest) ; valable TICKET_SECONDS, une seule fois.
Même traitement qu'un envoi depuis l'interface : taille MAZE_MAX_UPLOAD_MB (limits.py), imaging.process_upload
(formats, pixels, WebP), écriture dans les mêmes tables avec les mêmes effets (api._store_images : la première image
d'une fiche sans portrait le devient). Un ticket d'une campagne inaccessible au compte connecté est « introuvable »
(404), comme toute ressource d'une autre campagne (api.scoped).
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, db, i18n, imaging
from .api import TABLES, _store_images, get_or_404, in_tx, scoped, settings, threaded, touch_campaign, tx
from .connector import digest
from .i18n import msg

TICKET_SECONDS = 600            # durée de validité d'un lien de dépôt (10 minutes)
KEEP_SECONDS = 86400            # ticket gardé un jour après son échéance (suivi par Claude), puis supprimé
TARGETS = ("library", "npc", "faction", "place", "document")
TARGET_TABLES = {**TABLES, "document": "documents"}
RESULT_TABLES = {"library": "library_images", "npc": "images", "faction": "images", "place": "images",
                 "document": "document_images"}
CAPTION_MAX = 500
NOT_FOUND = "Lien de dépôt introuvable"
EXPIRED = "Ce lien de dépôt a expiré : demandez-en un nouveau à Claude."
USED = "Ce lien de dépôt a déjà servi : demandez-en un nouveau à Claude."


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_of(row: dict[str, Any], now: float | None = None) -> str:
    """État effectif : un ticket en attente dont l'échéance est passée est expiré (sans attendre la purge)."""
    if row["state"] == "pending" and row["expires_at"] < (now if now is not None else time.time()):
        return "expired"
    return row["state"]


def target_name(conn: sqlite3.Connection, target: str, target_id: int | None) -> str | None:
    """Nom de la fiche visée ('' pour la bibliothèque), None si elle n'existe plus."""
    if target == "library":
        return ""
    column = "title" if target == "document" else "name"
    row = conn.execute(f"SELECT {column} FROM {TARGET_TABLES[target]} WHERE id = ?", (target_id,)).fetchone()
    return row[0] if row else None


def purge(conn: sqlite3.Connection, now: int) -> None:
    """Tickets en attente échus passés à expired ; supprimés un jour après leur échéance."""
    conn.execute("UPDATE upload_tickets SET state = 'expired' WHERE state = 'pending' AND expires_at < ?", (now,))
    conn.execute("DELETE FROM upload_tickets WHERE expires_at < ?", (now - KEEP_SECONDS,))


def create_ticket(conn: sqlite3.Connection, user_id: int | None, campaign_id: int, target: str, target_id: int | None,
                  caption: str = "") -> dict[str, Any]:
    """Nouveau ticket (purge des tickets périmés au passage) : {id, token, expires_at (epoch)}. Le jeton n'est
    renvoyé qu'ici ; seule son empreinte est gardée. Cible vérifiée par l'appelant (campagne et existence)."""
    if target not in TARGETS:
        raise ValueError(target)
    now = int(time.time())
    purge(conn, now)
    token = secrets.token_urlsafe(24)
    expires = now + TICKET_SECONDS
    ticket_id = db.insert(conn, "upload_tickets", {
        "campaign_id": campaign_id, "token_hash": digest(token), "target": target,
        "target_id": None if target == "library" else target_id, "caption": (caption or "")[:CAPTION_MAX],
        "created_by": user_id, "expires_at": expires,
    })
    return {"id": ticket_id, "token": token, "expires_at": expires}


def result_image(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any] | None:
    """Image créée par le dépôt (None avant, ou si elle a été supprimée depuis)."""
    if row["image_id"] is None:
        return None
    image = conn.execute(f"SELECT * FROM {RESULT_TABLES[row['target']]} WHERE id = ?", (row["image_id"],)).fetchone()
    if image is None:
        return None
    return {"id": image["id"], "url": f"/media/{image['file']}", "thumb_url": f"/media/{image['thumb']}",
            "width": image["width"], "height": image["height"]}


def _ticket(conn: sqlite3.Connection, request: Request, token: str) -> dict[str, Any]:
    """Ticket de ce jeton, si le compte connecté a accès à sa campagne ; sinon 404 (rien n'est révélé)."""
    row = db.row_to_dict(conn.execute("SELECT * FROM upload_tickets WHERE token_hash = ?",
                                      (digest(token),)).fetchone())
    if row is None or not auth.can_access(conn, auth.current_user(request), row["campaign_id"]):
        raise HTTPException(404, NOT_FOUND)
    return row


def _out(conn: sqlite3.Connection, row: dict[str, Any], max_mb: int) -> dict[str, Any]:
    campaign = conn.execute("SELECT name FROM campaigns WHERE id = ?", (row["campaign_id"],)).fetchone()
    now = time.time()
    return {
        "state": state_of(row, now),
        "campaign": {"id": row["campaign_id"], "name": campaign[0] if campaign else ""},
        "target": row["target"], "target_id": row["target_id"],
        "target_name": target_name(conn, row["target"], row["target_id"]),
        "caption": row["caption"],
        "expires_at": iso(row["expires_at"]), "seconds_left": max(0, int(row["expires_at"] - now)),
        "max_mb": max_mb,
        "image": result_image(conn, row) if row["state"] == "used" else None,
    }


@threaded
def get_upload(request: Request) -> Response:
    token = request.path_params["token"]
    with tx(request) as conn:
        row = _ticket(conn, request, token)
        return JSONResponse(_out(conn, row, settings(request).max_upload_mb))


def _check_pending(row: dict[str, Any]) -> None:
    state = state_of(row)
    if state == "used":
        raise HTTPException(409, USED)
    if state == "expired":
        raise HTTPException(410, EXPIRED)


def _store(conn: sqlite3.Connection, request: Request, row: dict[str, Any], img: imaging.StoredImage, name: str,
           caption: str) -> int:
    """Image traitée rangée dans sa cible, comme les envois existants ; renvoie l'identifiant créé."""
    target, cid = row["target"], row["campaign_id"]
    if target == "library":          # media_admin.upload_library
        get_or_404(conn, "campaigns", cid, "Campagne")
        return db.insert(conn, "library_images", {"campaign_id": cid, "file": img.file, "thumb": img.thumb,
                                                  "original_name": name, "caption": caption,
                                                  "width": img.width, "height": img.height})
    if target == "document":         # documents.upload_document_image
        doc = scoped(conn, request, "documents", row["target_id"], "Fiche")
        if doc["campaign_id"] != cid:
            raise HTTPException(404, msg("{what} introuvable", what=msg("Fiche")))
        return db.insert(conn, "document_images", {
            "campaign_id": cid, "document_id": doc["id"], "file": img.file, "thumb": img.thumb,
            "width": img.width, "height": img.height,
        })
    # api._upload_images (galerie d'un PNJ, d'une faction, d'un lieu), légende comme mcp_images.add_image
    entity = scoped(conn, request, TABLES[target], row["target_id"], "Fiche")
    if entity["campaign_id"] != cid:
        raise HTTPException(404, msg("{what} introuvable", what=msg("Fiche")))
    _store_images(conn, target, entity["id"], entity, [(img, name)])
    new_id = conn.execute("SELECT id FROM images WHERE file = ?", (img.file,)).fetchone()[0]
    if caption:
        conn.execute("UPDATE images SET caption = ? WHERE id = ?", (caption, new_id))
    touch_campaign(conn, cid)
    return new_id


async def post_upload(request: Request) -> Response:
    """Dépôt (multipart : file, caption facultative qui remplace celle proposée par Claude)."""
    cfg = settings(request)
    token = request.path_params["token"]
    limit = cfg.max_upload_mb * 1024 * 1024
    if int(request.headers.get("content-length") or 0) > limit + 1024 * 1024:
        raise HTTPException(413, msg("Image trop volumineuse (max {size} Mo)", size=cfg.max_upload_mb))

    def check(conn: sqlite3.Connection) -> dict[str, Any]:
        row = _ticket(conn, request, token)
        _check_pending(row)
        if target_name(conn, row["target"], row["target_id"]) is None:
            raise HTTPException(404, msg("{what} introuvable", what=msg("Fiche")))
        return row
    row = await in_tx(request, check, write=False)

    form = await request.form(max_files=1, max_fields=2)
    try:
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(400, "Aucun fichier reçu (champ 'file')")
        raw = await upload.read(limit + 1)
        if len(raw) > limit:
            raise HTTPException(413, msg("Image trop volumineuse (max {size} Mo)", size=cfg.max_upload_mb))
        name = (upload.filename or "")[:200]
        given = form.get("caption")
        caption = (given if isinstance(given, str) else row["caption"]).strip()[:CAPTION_MAX]
    finally:
        await form.close()
    try:
        img = await run_in_threadpool(imaging.process_upload, raw, cfg.media_dir, row["campaign_id"])
    except imaging.ImageError as exc:
        raise HTTPException(400, i18n.message(exc)) from exc

    user = auth.current_user(request)

    def store(conn: sqlite3.Connection) -> dict[str, Any]:
        current = _ticket(conn, request, token)
        # Usage unique : un second dépôt simultané échoue ici. L'échéance a été vérifiée au début de l'envoi : un
        # envoi commencé à temps aboutit même s'il se termine après (ou si une purge a marqué le ticket expiré).
        if current["state"] == "used":
            raise HTTPException(409, USED)
        new_id = _store(conn, request, current, img, name, caption)
        conn.execute("UPDATE upload_tickets SET state = 'used', image_id = ?, used_by = ?, "
                     "used_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?", (new_id, user["id"], current["id"]))
        done = db.row_to_dict(conn.execute("SELECT * FROM upload_tickets WHERE id = ?", (current["id"],)).fetchone())
        return _out(conn, done, cfg.max_upload_mb)
    try:
        out = await in_tx(request, store)
    except BaseException:
        imaging.delete_files(cfg.media_dir, img.file, img.thumb)
        raise
    return JSONResponse(out, status_code=201)


routes = [
    Route("/api/uploads/{token:str}", get_upload),
    Route("/api/uploads/{token:str}", post_upload, methods=["POST"]),
]
