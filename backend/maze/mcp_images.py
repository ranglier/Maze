"""Images du connecteur Claude : voir, ajouter, retoucher, légender, ordonner, supprimer les images des fiches
(PNJ, factions, lieux), illustrer les fiches rédigées, alimenter la bibliothèque. Traitements d'UN élément, appelés
par les outils regroupés de mcp_tools.py (view, add_image, edit_image, update kind image, delete kind image ou
library_image).

Les images arrivent depuis une adresse web publique (fetch.py) ou depuis une image déjà dans Maze ;
elles passent toujours par imaging.process_upload (validation, WebP 1600 px, miniature), comme un envoi
depuis l'interface. Une retouche crée une nouvelle image (fichiers immuables, en cache chez les
navigateurs) ; l'originale est gardée en fin de galerie sauf demande contraire.
"""

from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import unquote, urlsplit

from PIL import Image
from starlette.concurrency import run_in_threadpool

from . import db, fetch, imaging
from .api import IMAGE_FIELD, TABLES, _store_images, touch_campaign
from .media_admin import delete_library_rows
from .mcp import ENTRY_TABLES, Blocks, Context, ToolError, _int, _kind

VIEW_SIDE = 1024          # côté maximal de l'aperçu renvoyé à Claude (JPEG)
BACKGROUND = (22, 29, 41)  # fond des images transparentes dans l'aperçu (couleur de l'interface)


def _media_dir(ctx: Context) -> Path:
    return ctx.app.state.settings.media_dir


def _image_row(ctx: Context, image_id: int) -> dict[str, Any]:
    with ctx.transaction() as conn:
        row = db.row_to_dict(conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone())
    if row is None or row["campaign_id"] not in ctx.grants:
        raise ToolError("Image introuvable (ou non ouverte à Claude)")
    return row


def _doc_image_row(ctx: Context, image_id: int) -> dict[str, Any]:
    with ctx.transaction() as conn:
        row = db.row_to_dict(conn.execute("SELECT * FROM document_images WHERE id = ?", (image_id,)).fetchone())
    if row is None or row["campaign_id"] not in ctx.grants:
        raise ToolError("Image introuvable (ou non ouverte à Claude)")
    return row


def _library_row(ctx: Context, image_id: int) -> dict[str, Any]:
    with ctx.transaction() as conn:
        row = db.row_to_dict(conn.execute("SELECT * FROM library_images WHERE id = ?", (image_id,)).fetchone())
    if row is None or row["campaign_id"] not in ctx.grants:
        raise ToolError("Image introuvable (ou non ouverte à Claude)")
    return row


def _cover_id(conn, kind: str, owner_id: int) -> int | None:
    row = conn.execute(f"SELECT {IMAGE_FIELD[kind]} FROM {TABLES[kind]} WHERE id = ?", (owner_id,)).fetchone()
    return row[0] if row else None


def _gallery(conn, kind: str, owner_id: int) -> list[dict[str, Any]]:
    cover = _cover_id(conn, kind, owner_id)
    return [{"id": r["id"], "caption": r["caption"], "width": r["width"], "height": r["height"], "cover": r["id"] == cover}
            for r in conn.execute("SELECT * FROM images WHERE owner_kind = ? AND owner_id = ? ORDER BY sort_order, id",
                                  (kind, owner_id))]


# --- Source d'une nouvelle image ---------------------------------------------------------------

SOURCE_KEYS = ("url", "from_library_image_id", "from_image_id", "from_document_image_id")


def _one_source(args: dict[str, Any]) -> str:
    given = [k for k in SOURCE_KEYS if args.get(k) is not None]
    if len(given) != 1:
        raise ToolError("Indiquer une source et une seule : " + ", ".join(SOURCE_KEYS))
    return given[0]


async def _source_bytes(ctx: Context, args: dict[str, Any]) -> tuple[bytes, str, str]:
    """Contenu de l'image source, nom d'origine et libellé pour le journal (légende : _source_caption)."""
    source = _one_source(args)
    if source == "url":
        url = str(args["url"])
        limit = ctx.app.state.settings.max_upload_mb * 1024 * 1024
        try:
            raw = await run_in_threadpool(fetch.fetch, url, limit, ctx.app.state.settings.public_url)
        except fetch.FetchError as exc:
            raise ToolError(f"Image non récupérée : {exc}") from None
        parts = urlsplit(url)
        return raw, unquote(Path(parts.path).name)[:200], parts.hostname or ""
    if source == "from_library_image_id":
        row = _library_row(ctx, _int(args, "from_library_image_id"))
        label = f"bibliothèque:{row['id']}"
    elif source == "from_image_id":
        row = _image_row(ctx, _int(args, "from_image_id"))
        label = f"image:{row['id']}"
    else:
        row = _doc_image_row(ctx, _int(args, "from_document_image_id"))
        label = f"doc-image:{row['id']}"
    path = _media_dir(ctx) / row["file"]
    try:
        raw = await run_in_threadpool(path.read_bytes)
    except OSError:
        raise ToolError("Fichier de l'image source introuvable sur le serveur") from None
    return raw, row.get("original_name") or "", label


def _source_caption(ctx: Context, args: dict[str, Any]) -> str:
    """Légende reprise de l'image copiée (bibliothèque ou galerie), à défaut d'une légende fournie."""
    if args.get("from_library_image_id") is not None:
        return _library_row(ctx, _int(args, "from_library_image_id"))["caption"]
    if args.get("from_image_id") is not None:
        return _image_row(ctx, _int(args, "from_image_id"))["caption"]
    return ""


async def _process(ctx: Context, raw: bytes, campaign_id: int) -> imaging.StoredImage:
    try:
        return await run_in_threadpool(imaging.process_upload, raw, _media_dir(ctx), campaign_id)
    except imaging.ImageError as exc:
        raise ToolError(f"Image refusée : {exc}") from None


def _discard(ctx: Context, img: imaging.StoredImage) -> None:
    imaging.delete_files(_media_dir(ctx), img.file, img.thumb)


# --- Lecture -----------------------------------------------------------------------------------

def _preview(path: Path) -> bytes:
    with Image.open(path) as source:
        img = source.convert("RGBA")
    img.thumbnail((VIEW_SIDE, VIEW_SIDE), Image.Resampling.LANCZOS)
    flat = Image.new("RGB", img.size, BACKGROUND)
    flat.paste(img, mask=img.getchannel("A"))
    out = io.BytesIO()
    flat.save(out, "JPEG", quality=85)
    return out.getvalue()


async def view_image(ctx: Context, args: dict[str, Any]) -> Any:
    given = [k for k in ("image_id", "document_image_id", "library_image_id") if args.get(k) is not None]
    if len(given) != 1:
        raise ToolError("Indiquer image_id (galerie d'une fiche), document_image_id (fiche rédigée) "
                        "ou library_image_id (bibliothèque)")
    if given[0] == "library_image_id":
        row = _library_row(ctx, _int(args, "library_image_id"))
        meta = {"library_image_id": row["id"], "caption": row["caption"], "name": row["original_name"]}
        ctx.target = f"bibliothèque:{row['id']}"
    elif args.get("image_id") is not None:
        row = _image_row(ctx, _int(args, "image_id"))
        with ctx.transaction() as conn:
            cover = _cover_id(conn, row["owner_kind"], row["owner_id"]) == row["id"]
        meta = {"image_id": row["id"], "owner": {"kind": row["owner_kind"], "id": row["owner_id"]},
                "caption": row["caption"], "cover": cover}
        ctx.target = f"image:{row['id']}"
    else:
        row = _doc_image_row(ctx, _int(args, "document_image_id"))
        meta = {"document_image_id": row["id"], "document_id": row["document_id"], "src": f"/media/{row['file']}"}
        ctx.target = f"doc-image:{row['id']}"
    ctx.campaign_id = row["campaign_id"]
    try:
        jpeg = await run_in_threadpool(_preview, _media_dir(ctx) / row["file"])
    except OSError:
        raise ToolError("Fichier de l'image introuvable sur le serveur") from None
    meta.update(width=row["width"], height=row["height"], note=f"Aperçu réduit à {VIEW_SIDE} px au plus.")
    return Blocks([{"type": "text", "text": json.dumps(meta, ensure_ascii=False)},
                   {"type": "image", "data": base64.b64encode(jpeg).decode(), "mimeType": "image/jpeg"}])


# --- Galerie des fiches --------------------------------------------------------------------------

async def add_image(ctx: Context, args: dict[str, Any]) -> Any:
    kind, eid = _kind(args), _int(args, "id")
    ctx.target = f"{kind}:{eid}"
    cid = ctx.check_write(ctx.campaign_of(ENTRY_TABLES[kind], eid))
    raw, name, label = await _source_bytes(ctx, args)
    ctx.campaign_id = cid
    ctx.target = f"{kind}:{eid} < {label}"
    img = await _process(ctx, raw, cid)
    caption = str(args.get("caption") or _source_caption(ctx, args))[:500]
    try:
        def _work(conn: sqlite3.Connection):
            entity = db.row_to_dict(conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE id = ?", (eid,)).fetchone())
            if entity is None or entity["campaign_id"] != cid:
                raise ToolError("Fiche introuvable (supprimée entre-temps ?)")
            _store_images(conn, kind, eid, entity, [(img, name)])
            new_id = conn.execute("SELECT id FROM images WHERE file = ?", (img.file,)).fetchone()[0]
            if caption:
                conn.execute("UPDATE images SET caption = ? WHERE id = ?", (caption, new_id))
            if args.get("set_cover") is True:
                conn.execute(f"UPDATE {TABLES[kind]} SET {IMAGE_FIELD[kind]} = ? WHERE id = ?", (new_id, eid))
            touch_campaign(conn, cid)
            gallery = _gallery(conn, kind, eid)
            return new_id, gallery
        new_id, gallery = await ctx.run(_work)
    except BaseException:
        _discard(ctx, img)
        raise
    return {"image_id": new_id, "width": img.width, "height": img.height, "images": gallery}


async def update_image(ctx: Context, args: dict[str, Any]) -> Any:
    row = _image_row(ctx, _int(args, "image_id"))
    ctx.target = f"image:{row['id']}"
    cid = ctx.check_write(row["campaign_id"], "Image")
    kind, owner = row["owner_kind"], row["owner_id"]
    position = _int(args, "position", required=False)
    if args.get("caption") is None and position is None and args.get("set_cover") is None:
        raise ToolError("Rien à modifier : caption, position ou set_cover")
    def change(conn: sqlite3.Connection) -> dict[str, Any]:
        if args.get("caption") is not None:
            conn.execute("UPDATE images SET caption = ? WHERE id = ?", (str(args["caption"])[:500], row["id"]))
        if position is not None:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM images WHERE owner_kind = ? AND owner_id = ? AND id != ? ORDER BY sort_order, id",
                (kind, owner, row["id"]))]
            ids.insert(max(0, min(position, len(ids))), row["id"])
            conn.executemany("UPDATE images SET sort_order = ? WHERE id = ?", [(i, x) for i, x in enumerate(ids)])
        if args.get("set_cover") is True:
            conn.execute(f"UPDATE {TABLES[kind]} SET {IMAGE_FIELD[kind]} = ? WHERE id = ?", (row["id"], owner))
        elif args.get("set_cover") is False and _cover_id(conn, kind, owner) == row["id"]:
            conn.execute(f"UPDATE {TABLES[kind]} SET {IMAGE_FIELD[kind]} = NULL WHERE id = ?", (owner,))
        touch_campaign(conn, cid)
        return {"owner": {"kind": kind, "id": owner}, "images": _gallery(conn, kind, owner)}
    return await ctx.run(change)


async def delete_image(ctx: Context, args: dict[str, Any]) -> Any:
    row = _image_row(ctx, _int(args, "image_id"))
    ctx.target = f"image:{row['id']}"
    ctx.check_write(row["campaign_id"], "Image")
    await ctx.api("DELETE", f"/api/images/{row['id']}", write=True)
    with ctx.transaction() as conn:
        return {"deleted": row["id"], "images": _gallery(conn, row["owner_kind"], row["owner_id"])}


ROTATIONS = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}
FLIPS = {"horizontal": Image.Transpose.FLIP_LEFT_RIGHT, "vertical": Image.Transpose.FLIP_TOP_BOTTOM}


def _crop_box(crop: Any) -> tuple[float, float, float, float] | None:
    if crop is None:
        return None
    try:
        box = tuple(float(crop[k]) for k in ("left", "top", "right", "bottom"))
    except (TypeError, KeyError, ValueError):
        raise ToolError("crop : objet {left, top, right, bottom} en pourcentages (0 à 100)") from None
    left, top, right, bottom = box
    if not (0 <= left < right <= 100 and 0 <= top < bottom <= 100):
        raise ToolError("crop : 0 <= left < right <= 100 et 0 <= top < bottom <= 100")
    return box


def _transform(path: Path, rotate: int | None, flip: str | None, crop) -> bytes:
    with Image.open(path) as source:
        img = source.convert("RGBA" if "A" in source.getbands() else "RGB")
    if rotate:
        img = img.transpose(ROTATIONS[rotate])
    if flip:
        img = img.transpose(FLIPS[flip])
    if crop:
        left, top, right, bottom = crop
        w, h = img.size
        img = img.crop((round(w * left / 100), round(h * top / 100), round(w * right / 100), round(h * bottom / 100)))
        if min(img.size) < 16:
            raise ToolError("Recadrage trop petit (16 px au moins)")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


async def edit_image(ctx: Context, args: dict[str, Any]) -> Any:
    row = _image_row(ctx, _int(args, "image_id"))
    ctx.target = f"image:{row['id']}"
    cid = ctx.check_write(row["campaign_id"], "Image")
    rotate = _int(args, "rotate", required=False)
    flip = args.get("flip")
    crop = _crop_box(args.get("crop"))
    if rotate not in (None, 0, 90, 180, 270):
        raise ToolError("rotate : 90, 180 ou 270 (sens des aiguilles d'une montre)")
    if flip is not None and (not isinstance(flip, str) or flip not in FLIPS):
        raise ToolError("flip : horizontal ou vertical")
    if not rotate and flip is None and crop is None:
        raise ToolError("Rien à faire : rotate, flip ou crop")
    try:
        raw = await run_in_threadpool(_transform, _media_dir(ctx) / row["file"], rotate, flip, crop)
    except OSError:
        raise ToolError("Fichier de l'image introuvable sur le serveur") from None
    img = await _process(ctx, raw, cid)
    keep = args.get("keep_original") is not False
    kind, owner = row["owner_kind"], row["owner_id"]
    try:
        def _work(conn: sqlite3.Connection):
            current = conn.execute("SELECT sort_order FROM images WHERE id = ?", (row["id"],)).fetchone()
            if current is None:
                raise ToolError("Image introuvable (supprimée entre-temps ?)")
            new_id = db.insert(conn, "images", {
                "campaign_id": cid, "owner_kind": kind, "owner_id": owner, "file": img.file, "thumb": img.thumb,
                "original_name": row["original_name"], "width": img.width, "height": img.height,
                "caption": row["caption"], "sort_order": current[0],
            })
            if _cover_id(conn, kind, owner) == row["id"]:
                conn.execute(f"UPDATE {TABLES[kind]} SET {IMAGE_FIELD[kind]} = ? WHERE id = ?", (new_id, owner))
            if keep:
                last = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM images WHERE owner_kind = ? "
                                    "AND owner_id = ?", (kind, owner)).fetchone()[0]
                conn.execute("UPDATE images SET sort_order = ? WHERE id = ?", (last, row["id"]))
            else:
                conn.execute("DELETE FROM images WHERE id = ?", (row["id"],))
            touch_campaign(conn, cid)
            gallery = _gallery(conn, kind, owner)
            return new_id, gallery
        new_id, gallery = await ctx.run(_work)
    except BaseException:
        _discard(ctx, img)
        raise
    if not keep:
        imaging.delete_files(_media_dir(ctx), row["file"], row["thumb"])
    return {"image_id": new_id, "original_id": row["id"], "original_kept": keep,
            "width": img.width, "height": img.height, "images": gallery}


# --- Fiches rédigées -------------------------------------------------------------------------------

async def add_document_image(ctx: Context, args: dict[str, Any]) -> Any:
    did = _int(args, "document_id")
    ctx.target = f"doc:{did}"
    cid = ctx.check_write(ctx.campaign_of("documents", did))
    raw, _, label = await _source_bytes(ctx, args)
    ctx.campaign_id = cid
    ctx.target = f"doc:{did} < {label}"
    img = await _process(ctx, raw, cid)
    try:
        def _work(conn: sqlite3.Connection):
            if not conn.execute("SELECT 1 FROM documents WHERE id = ? AND campaign_id = ?", (did, cid)).fetchone():
                raise ToolError("Fiche introuvable (supprimée entre-temps ?)")
            new_id = db.insert(conn, "document_images", {
                "campaign_id": cid, "document_id": did, "file": img.file, "thumb": img.thumb,
                "width": img.width, "height": img.height,
            })
            return new_id
        new_id = await ctx.run(_work)
    except BaseException:
        _discard(ctx, img)
        raise
    alt = html.escape(str(args.get("alt") or "")[:200], quote=True)
    return {"document_image_id": new_id, "src": f"/media/{img.file}", "width": img.width, "height": img.height,
            "html": f'<img src="/media/{img.file}" alt="{alt}" data-doc-image="{new_id}">',
            "next": "Insérer ce HTML (bloc à part, hors d'un paragraphe) dans le contenu avec update_document."}


# --- Bibliothèque de la campagne ------------------------------------------------------------------

async def add_library_image(ctx: Context, args: dict[str, Any]) -> Any:
    """Image ajoutée à la bibliothèque de la campagne (comme un dépôt dans Administration > Images)."""
    cid = ctx.check_write(_int(args, "campaign_id"), "Campagne")
    ctx.target = "bibliothèque"
    raw, name, label = await _source_bytes(ctx, args)
    ctx.campaign_id = cid
    ctx.target = f"bibliothèque < {label}"
    img = await _process(ctx, raw, cid)
    caption = str(args.get("caption") or _source_caption(ctx, args))[:500]
    try:
        def _work(conn: sqlite3.Connection):
            if not conn.execute("SELECT 1 FROM campaigns WHERE id = ?", (cid,)).fetchone():
                raise ToolError("Campagne introuvable (supprimée entre-temps ?)")
            return db.insert(conn, "library_images", {
                "campaign_id": cid, "file": img.file, "thumb": img.thumb, "original_name": name[:200],
                "width": img.width, "height": img.height, "caption": caption})
        new_id = await ctx.run(_work)
    except BaseException:
        _discard(ctx, img)
        raise
    ctx.target = f"bibliothèque:{new_id} < {label}"
    return {"library_image_id": new_id, "width": img.width, "height": img.height}


async def delete_library_image(ctx: Context, args: dict[str, Any]) -> Any:
    row = _library_row(ctx, _int(args, "library_image_id"))
    ctx.target = f"bibliothèque:{row['id']}"
    ctx.check_write(row["campaign_id"], "Image")

    def _work(conn: sqlite3.Connection):
        delete_library_rows(conn, [row])
    await ctx.run(_work)
    imaging.delete_files(_media_dir(ctx), row["file"], row["thumb"])
    return {"deleted": row["id"]}
