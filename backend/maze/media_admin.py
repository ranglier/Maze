"""Administration > Images : toutes les images d'une campagne, sa bibliothèque et le ménage.

Trois sortes d'images :
- galerie d'une fiche (table images : PNJ, faction, lieu ; l'une peut être portrait / emblème / illustration) ;
- illustrations des fiches rédigées (table document_images, citées par <img data-doc-image> dans le texte) ;
- bibliothèque de la campagne (table library_images, migration 016) : images déposées sans fiche,
  copiées vers une fiche à la demande (depuis cette page ou par Claude, outils du connecteur) ;
- fonds des cartes interactives (table maps, migration 017), gérés depuis Lieux > Carte interactive.
Le ménage ne supprime que des illustrations citées nulle part : ni dans le texte d'une fiche, ni dans
une version de son historique (une restauration les réafficherait).
"""

from __future__ import annotations

import re
import sqlite3
import zlib
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, db, i18n, imaging, limits, versions
from .i18n import msg
from .connector import _require_json
from .api import IMAGE_FIELD, TABLES, _store_images, get_or_404, in_tx, parse_body, path_id, settings, threaded, touch_campaign, tx
from .documents import UNSAVED_EDITS, has_unsaved_edits
from .htmlimg import doc_image_id, remove_imgs
from .schemas import LibraryAssign, LibraryUpdate

DOC_IMAGE = re.compile(r'data-doc-image="(\d+)"')
MAX_FILES = limits.LIBRARY_FILES


def _size(media_dir: Path, *paths: str) -> int:
    total = 0
    for rel in paths:
        try:
            total += (media_dir / rel).stat().st_size
        except OSError:
            pass
    return total


def doc_image_usage(conn: sqlite3.Connection, campaign_id: int, with_versions: bool) -> tuple[set[int], set[int]]:
    """Illustrations citées dans le texte actuel des fiches, et (si demandé) dans leurs versions."""
    in_content: set[int] = set()
    for (content,) in conn.execute("SELECT content FROM documents WHERE campaign_id = ?", (campaign_id,)):
        in_content.update(int(i) for i in DOC_IMAGE.findall(content or ""))
    in_versions: set[int] = set()
    if with_versions:
        for (blob,) in conn.execute("""SELECT v.content FROM document_versions v JOIN documents d ON d.id = v.document_id
                                        WHERE d.campaign_id = ?""", (campaign_id,)):
            in_versions.update(int(i) for i in DOC_IMAGE.findall(zlib.decompress(blob).decode(errors="replace")))
    return in_content, in_versions


def _url(rel: str) -> str:
    return f"/media/{rel}"


def _list(request: Request, cid: int) -> dict[str, Any]:
    media_dir = settings(request).media_dir
    with tx(request) as conn:
        get_or_404(conn, "campaigns", cid, "Campagne")
        names: dict[tuple[str, int], str] = {}
        covers: set[int] = set()
        for kind, table in TABLES.items():
            for row in conn.execute(f"SELECT id, name, {IMAGE_FIELD[kind]} FROM {table} WHERE campaign_id = ?", (cid,)):
                names[(kind, row[0])] = row[1]
                if row[2]:
                    covers.add(row[2])
        docs = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT id, title, kind FROM documents WHERE campaign_id = ?", (cid,))}
        used, in_versions = doc_image_usage(conn, cid, with_versions=True)
        out: list[dict[str, Any]] = []
        for r in conn.execute("SELECT * FROM library_images WHERE campaign_id = ? ORDER BY id DESC", (cid,)):
            out.append({"source": "library", "id": r["id"], "caption": r["caption"], "name": r["original_name"],
                        "width": r["width"], "height": r["height"], "created_at": r["created_at"],
                        "url": _url(r["file"]), "thumb_url": _url(r["thumb"]), "bytes": _size(media_dir, r["file"], r["thumb"])})
        for r in conn.execute("SELECT * FROM images WHERE campaign_id = ? ORDER BY owner_kind, owner_id, sort_order, id",
                              (cid,)):
            out.append({"source": "gallery", "id": r["id"], "caption": r["caption"], "name": r["original_name"],
                        "width": r["width"], "height": r["height"], "created_at": r["created_at"],
                        "url": _url(r["file"]), "thumb_url": _url(r["thumb"]), "bytes": _size(media_dir, r["file"], r["thumb"]),
                        "owner": {"kind": r["owner_kind"], "id": r["owner_id"],
                                  "name": names.get((r["owner_kind"], r["owner_id"]), "")},
                        "cover": r["id"] in covers})
        orphans = 0
        for r in conn.execute("SELECT * FROM document_images WHERE campaign_id = ? ORDER BY id DESC", (cid,)):
            doc = docs.get(r["document_id"])
            in_use = r["id"] in used
            if not in_use and r["id"] not in in_versions:
                orphans += 1
            out.append({"source": "document", "id": r["id"], "caption": "", "name": "",
                        "width": r["width"], "height": r["height"], "created_at": r["created_at"],
                        "url": _url(r["file"]), "thumb_url": _url(r["thumb"]), "bytes": _size(media_dir, r["file"], r["thumb"]),
                        "document": {"id": r["document_id"], "title": doc[0], "kind": doc[1]} if doc else None,
                        "used": in_use, "in_history": r["id"] in in_versions})
        for r in conn.execute("SELECT * FROM maps WHERE campaign_id = ? ORDER BY sort_order, id", (cid,)):
            out.append({"source": "map", "id": r["id"], "caption": r["name"], "name": r["original_name"],
                        "width": r["width"], "height": r["height"], "created_at": r["created_at"],
                        "url": _url(r["file"]), "thumb_url": _url(r["thumb"]),
                        "bytes": _size(media_dir, r["file"], r["thumb"], *([r["preview"]] if r["preview"] else []))})
    return {"campaign_id": cid, "images": out, "orphans": orphans,
            "bytes": sum(i["bytes"] for i in out), "count": len(out)}


@threaded
def list_images(request: Request) -> Response:
    auth.require_admin(request)
    cid = path_id(request)
    return JSONResponse(_list(request, cid))


async def upload_library(request: Request) -> Response:
    auth.require_admin(request)
    cfg = settings(request)
    cid = path_id(request)
    limit = cfg.max_upload_mb * 1024 * 1024

    def _work(conn: sqlite3.Connection):
        get_or_404(conn, "campaigns", cid, "Campagne")
    await in_tx(request, _work)
    form = await request.form(max_files=MAX_FILES, max_fields=MAX_FILES)
    uploads = [f for f in form.getlist("files") if hasattr(f, "read")]
    stored: list[tuple[imaging.StoredImage, str]] = []
    try:
        if not uploads:
            raise HTTPException(400, "Aucun fichier reçu (champ 'files')")
        for upload in uploads:
            raw = await upload.read(limit + 1)
            if len(raw) > limit:
                raise HTTPException(413, msg("{name} dépasse {size} Mo", name=upload.filename or "", size=cfg.max_upload_mb))
            try:
                img = await run_in_threadpool(imaging.process_upload, raw, cfg.media_dir, cid)
            except imaging.ImageError as exc:
                raise HTTPException(400, msg("{name} : {error}", name=upload.filename or "", error=i18n.message(exc))) from exc
            stored.append((img, (upload.filename or "")[:200]))

        def _work(conn: sqlite3.Connection):
            get_or_404(conn, "campaigns", cid, "Campagne")  # supprimée pendant l'envoi
            for img, name in stored:
                db.insert(conn, "library_images", {"campaign_id": cid, "file": img.file, "thumb": img.thumb,
                                                   "original_name": name, "width": img.width, "height": img.height})
            return img
        img = await in_tx(request, _work)
    except BaseException:
        for img, _ in stored:
            imaging.delete_files(cfg.media_dir, img.file, img.thumb)
        raise
    finally:
        await form.close()
    return JSONResponse({"added": len(stored)}, status_code=201)


@threaded
def update_library(request: Request) -> Response:
    auth.require_admin(request)
    lid = path_id(request)
    data = parse_body(request, LibraryUpdate)
    with tx(request) as conn:
        get_or_404(conn, "library_images", lid, "Image")
        conn.execute("UPDATE library_images SET caption = ? WHERE id = ?", (data.caption, lid))
    return Response(status_code=204)


@threaded
def delete_library(request: Request) -> Response:
    auth.require_admin(request)
    lid = path_id(request)
    with tx(request) as conn:
        row = get_or_404(conn, "library_images", lid, "Image")
        conn.execute("DELETE FROM library_images WHERE id = ?", (lid,))
    imaging.delete_files(settings(request).media_dir, row["file"], row["thumb"])
    return Response(status_code=204)


def copy_to_gallery(media_dir: Path, conn_factory, source_file: str, campaign_id: int, kind: str, entity_id: int,
                    caption: str, name: str, set_cover: bool) -> int:
    """Copie une image (fichier de Maze) dans la galerie d'une fiche ; renvoie l'identifiant créé."""
    raw = (media_dir / source_file).read_bytes()
    img = imaging.process_upload(raw, media_dir, campaign_id)
    try:
        with conn_factory() as conn:
            entity = db.row_to_dict(conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE id = ?", (entity_id,)).fetchone())
            if entity is None or entity["campaign_id"] != campaign_id:
                raise HTTPException(400, "La fiche n'existe pas dans cette campagne")
            _store_images(conn, kind, entity_id, entity, [(img, name)])
            new_id = conn.execute("SELECT id FROM images WHERE file = ?", (img.file,)).fetchone()[0]
            if caption:
                conn.execute("UPDATE images SET caption = ? WHERE id = ?", (caption, new_id))
            if set_cover:
                conn.execute(f"UPDATE {TABLES[kind]} SET {IMAGE_FIELD[kind]} = ? WHERE id = ?", (new_id, entity_id))
            touch_campaign(conn, campaign_id)
    except BaseException:
        imaging.delete_files(media_dir, img.file, img.thumb)
        raise
    return new_id


@threaded
def assign_library(request: Request) -> Response:
    """Copie une image de la bibliothèque dans la galerie d'un PNJ, d'une faction ou d'un lieu."""
    auth.require_admin(request)
    _require_json(request)
    lid = path_id(request)
    data = parse_body(request, LibraryAssign)
    cfg = settings(request)
    with tx(request) as conn:
        row = get_or_404(conn, "library_images", lid, "Image")

    def run() -> int:
        try:
            return copy_to_gallery(cfg.media_dir, lambda: db.transaction(cfg.db_path), row["file"], row["campaign_id"],
                                   data.kind, data.entity_id, row["caption"], row["original_name"], data.set_cover)
        except OSError as exc:
            raise HTTPException(404, "Fichier de l'image introuvable sur le serveur") from exc
        except imaging.ImageError as exc:
            raise HTTPException(400, i18n.message(exc)) from exc

    new_id = run()
    if data.remove:
        with tx(request) as conn:
            conn.execute("DELETE FROM library_images WHERE id = ?", (lid,))
        imaging.delete_files(cfg.media_dir, row["file"], row["thumb"])
    return JSONResponse({"image_id": new_id}, status_code=201)


def strip_doc_image(html: str, image_id: int) -> str:
    """Retire du texte d'une fiche les <img> qui affichent cette illustration."""
    # Découpage linéaire des <img> (audit PERF-5, htmlimg), attribut contenant « < » ou « > » compris.
    return remove_imgs(html, lambda attrs: doc_image_id(attrs) == image_id)


@threaded
def delete_document_image(request: Request) -> Response:
    """Illustration d'une fiche rédigée. Si une fiche l'affiche, elle est d'abord retirée de son texte
    (état précédent gardé dans l'historique ; les éditeurs ouverts rechargent la fiche)."""
    user = auth.require_admin(request)
    iid = path_id(request)
    touched: list[int] = []
    with tx(request) as conn:
        row = get_or_404(conn, "document_images", iid, "Image")
        docs = conn.execute("SELECT id, content FROM documents WHERE campaign_id = ? AND content LIKE ?",
                            (row["campaign_id"], f'%data-doc-image="{iid}"%')).fetchall()
        for doc in docs:
            if has_unsaved_edits(conn, doc["id"]):
                raise HTTPException(409, msg("Une fiche qui affiche cette image est en cours d'écriture. {detail}",
                                             detail=UNSAVED_EDITS))
            versions.snapshot(conn, doc["id"], label="Avant suppression d'une image")
            versions.replace_content(conn, doc["id"], strip_doc_image(doc["content"], iid), user["id"])
            touched.append(doc["id"])
        conn.execute("DELETE FROM document_images WHERE id = ?", (iid,))
    hub = getattr(request.app.state, "hub", None)
    if hub is not None and touched:
        hub.close_documents(touched, "reset")
    imaging.delete_files(settings(request).media_dir, row["file"], row["thumb"])
    return JSONResponse({"deleted": iid, "documents": touched})


def _in_journal(conn: sqlite3.Connection, campaign_id: int, rows: list[sqlite3.Row]) -> set[int]:
    """Illustrations encore présentes dans le journal temps réel d'une fiche de la campagne (audit DATA-13).
    Le HTML enregistré peut être en retard sur le journal ; celui-ci garde l'attribut `src` de l'image (son nom
    de fichier), et non `data-doc-image` (attribut docImage de l'éditeur)."""
    names = {r["id"]: Path(r["file"]).stem.encode() for r in rows}
    found: set[int] = set()
    for (blob,) in conn.execute("""SELECT u.data FROM document_updates u JOIN documents d ON d.id = u.document_id
                                    WHERE d.campaign_id = ?""", (campaign_id,)):
        for image_id, name in names.items():
            if image_id not in found and name in blob:
                found.add(image_id)
    return found


@threaded
def clean_orphans(request: Request) -> Response:
    """Supprime les illustrations citées nulle part (texte actuel et historique des fiches, journal temps réel).
    Les illustrations d'une fiche dont le HTML enregistré est en retard sur le journal sont laissées (DATA-13)."""
    auth.require_admin(request)
    _require_json(request)
    cid = path_id(request)
    media_dir = settings(request).media_dir

    def run() -> list[sqlite3.Row]:
        with tx(request) as conn:
            get_or_404(conn, "campaigns", cid, "Campagne")
            used, in_versions = doc_image_usage(conn, cid, with_versions=True)
            rows = [r for r in conn.execute(
                "SELECT id, document_id, file, thumb FROM document_images WHERE campaign_id = ?", (cid,))
                if r["id"] not in used and r["id"] not in in_versions]
            if rows:
                unsaved = {d for (d,) in conn.execute("SELECT id FROM documents WHERE campaign_id = ?", (cid,))
                           if has_unsaved_edits(conn, d)}
                journal = _in_journal(conn, cid, rows)
                rows = [r for r in rows if r["id"] not in journal and r["document_id"] not in unsaved]
            conn.executemany("DELETE FROM document_images WHERE id = ?", [(r["id"],) for r in rows])
        return rows

    rows = run()
    for r in rows:
        imaging.delete_files(media_dir, r["file"], r["thumb"])
    return JSONResponse({"deleted": len(rows)})


routes = [
    Route("/api/admin/campaigns/{id:int}/images", list_images),
    Route("/api/admin/campaigns/{id:int}/library", upload_library, methods=["POST"]),
    Route("/api/admin/campaigns/{id:int}/images/clean", clean_orphans, methods=["POST"]),
    Route("/api/admin/library/{id:int}", update_library, methods=["PATCH"]),
    Route("/api/admin/library/{id:int}", delete_library, methods=["DELETE"]),
    Route("/api/admin/library/{id:int}/assign", assign_library, methods=["POST"]),
    Route("/api/admin/document-images/{id:int}", delete_document_image, methods=["DELETE"]),
]
