"""Outils « cartes interactives » du connecteur Claude : lister, voir (aperçu quadrillé avec les épingles),
créer une carte, la renommer ou la rattacher à un lieu, épingler et retirer des lieux.

Les positions sont données à Claude en pourcentages (0 à 100, depuis le coin haut gauche) ; l'aperçu
porte un quadrillage tous les 10 % pour qu'il puisse situer une planète ou une ville sur le fond.
Les écritures passent par les routes REST (maps.py), sauf la création depuis une adresse web ou une
image de Maze, traitée ici en haute définition comme un envoi depuis l'interface.
"""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from . import db, imaging
from .maps import insert_map, map_detail, maps_of
from .mcp import ID, Blocks, Context, ToolError, _int, _tool
from .mcp_images import SOURCE, VIEW_SIDE, _media_dir, _source_bytes

PCT = {"type": "number", "minimum": 0, "maximum": 100}
PIN_STYLES = ("dot", "icon", "hidden")
MAX_KIND_BATCH = 200
GRID = (255, 255, 255, 90)
LABEL = (255, 255, 255, 230)
PIN = (220, 60, 60, 255)


def _map_row(ctx: Context, map_id: int) -> dict[str, Any]:
    with ctx.transaction() as conn:
        row = db.row_to_dict(conn.execute("SELECT * FROM maps WHERE id = ?", (map_id,)).fetchone())
    if row is None or row["campaign_id"] not in ctx.grants:
        raise ToolError("Carte introuvable (ou non ouverte à Claude)")
    ctx.campaign_id = row["campaign_id"]
    return row


def _pct(value: float) -> float:
    return round(value * 100, 1)


# La police par défaut de Pillow n'a pas les lettres accentuées (« Plan▯te ») : DejaVu, installée par le
# Dockerfile (fonts-dejavu-core) et présente sur Debian, ou MAZE_MAP_FONT.
FONT_FILES = [os.environ.get("MAZE_MAP_FONT", ""), "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/dejavu/DejaVuSans.ttf"]


def _font(size: int):
    for path in FONT_FILES:
        if path:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _overview(conn, row: dict[str, Any]) -> dict[str, Any]:
    """Carte, épingles (avec le nom des lieux) et lieux candidats non épinglés."""
    detail = map_detail(conn, row["id"])
    places = {r["id"]: r for r in db.rows_to_dicts(conn.execute(
        "SELECT id, parent_id, name, kind FROM places WHERE campaign_id = ?", (row["campaign_id"],)).fetchall())}
    pins = [{"n": i + 1, "place_id": p["place_id"], "name": places.get(p["place_id"], {}).get("name", "?"),
             "x": _pct(p["x"]), "y": _pct(p["y"]),
             "children": sum(1 for q in places.values() if q["parent_id"] == p["place_id"])}
            for i, p in enumerate(detail["pins"])]
    pinned = {p["place_id"] for p in detail["pins"]}
    # Candidats : sous-lieux du lieu représenté (ou lieux racines pour une carte générale) pas encore épinglés.
    parent = row["place_id"]
    unplaced = [{"place_id": q["id"], "name": q["name"], "kind": q["kind"]}
                for q in places.values() if q["parent_id"] == parent and q["id"] not in pinned]
    represented = places.get(parent)
    return {"map_id": row["id"], "campaign_id": row["campaign_id"], "name": row["name"],
            "place": {"id": represented["id"], "name": represented["name"]} if represented else None,
            "width": row["width"], "height": row["height"], "pins": pins,
            "unplaced": sorted(unplaced, key=lambda q: q["name"].lower())[:100]}


def _render(path: Path, pins: list[dict[str, Any]], grid: bool) -> bytes:
    with Image.open(path) as source:
        img = source.convert("RGB")
    img.thumbnail((VIEW_SIDE, VIEW_SIDE), Image.Resampling.LANCZOS)
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    small = _font(max(11, w // 70))
    if grid:
        for i in range(1, 10):
            x, y = round(w * i / 10), round(h * i / 10)
            draw.line([(x, 0), (x, h)], fill=GRID, width=1)
            draw.line([(0, y), (w, y)], fill=GRID, width=1)
            draw.text((x + 3, 2), str(i * 10), fill=LABEL, font=small, stroke_width=2, stroke_fill=(0, 0, 0, 200))
            draw.text((3, y + 2), str(i * 10), fill=LABEL, font=small, stroke_width=2, stroke_fill=(0, 0, 0, 200))
    radius = max(7, w // 90)
    for pin in pins:
        x, y = w * pin["x"] / 100, h * pin["y"] / 100
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=PIN, outline=(255, 255, 255, 255), width=2)
        draw.text((x + radius + 3, y - radius), f"{pin['n']} {pin['name']}"[:40], fill=(255, 255, 255, 255), font=small,
                  stroke_width=2, stroke_fill=(0, 0, 0, 220))
    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


# --- Outils ------------------------------------------------------------------------------------------

async def list_maps(ctx: Context, args: dict[str, Any]) -> Any:
    cid = ctx.check_read(_int(args, "campaign_id"))
    with ctx.transaction() as conn:
        names = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM places WHERE campaign_id = ?", (cid,))}
        maps = [{"id": m["id"], "name": m["name"],
                 "place": {"id": m["place_id"], "name": names.get(m["place_id"])} if m["place_id"] else None,
                 "width": m["width"], "height": m["height"], "pins": len(m["pins"])} for m in maps_of(conn, cid)]
    return {"campaign_id": cid, "maps": maps,
            "note": "Une carte peut représenter un lieu (place) : depuis une carte plus large, l'interface « entre » "
                    "dans celle du lieu. Les sous-lieux d'un lieu épinglé s'affichent en anneaux autour de lui : "
                    "seuls les lieux principaux de la carte ont besoin d'une épingle. view_map pour la voir."}


async def view_map(ctx: Context, args: dict[str, Any]) -> Any:
    row = _map_row(ctx, _int(args, "map_id"))
    ctx.target = f"carte:{row['id']}"
    with ctx.transaction() as conn:
        meta = _overview(conn, row)
    try:
        jpeg = await run_in_threadpool(_render, _media_dir(ctx) / (row["preview"] or row["file"]), meta["pins"], args.get("grid") is not False)
    except OSError:
        raise ToolError("Fichier de la carte introuvable sur le serveur") from None
    meta["note"] = (f"Aperçu réduit à {VIEW_SIDE} px, quadrillé tous les 10 % (x de gauche à droite, y de haut en bas) ; "
                    "épingles numérotées comme dans pins. Positions en pourcentages pour place_on_map. "
                    "unplaced : lieux à épingler de préférence (sous-lieux directs du lieu représenté, ou lieux "
                    "racines pour une carte générale).")
    return Blocks([{"type": "text", "text": json.dumps(meta, ensure_ascii=False)},
                   {"type": "image", "data": base64.b64encode(jpeg).decode(), "mimeType": "image/jpeg"}])


async def create_map(ctx: Context, args: dict[str, Any]) -> Any:
    cid = ctx.check_write(_int(args, "campaign_id"), "Campagne")
    place_id = _int(args, "place_id", required=False)
    raw, original, label = await _source_bytes(ctx, args)
    ctx.campaign_id = cid
    ctx.target = f"carte < {label}"
    try:
        img = await run_in_threadpool(imaging.process_upload, raw, _media_dir(ctx), cid, imaging.MAP_SIDE,
                                      imaging.MAP_PREVIEW_SIDE)
    except imaging.ImageError as exc:
        raise ToolError(f"Image refusée : {exc}") from None
    try:
        def _work(conn: sqlite3.Connection):
            try:
                new_id = insert_map(conn, cid, img, str(args.get("name") or ""), place_id, original)
            except HTTPException as exc:
                raise ToolError(str(exc.detail)) from None
            meta = _overview(conn, db.row_to_dict(conn.execute("SELECT * FROM maps WHERE id = ?", (new_id,)).fetchone()))
            return new_id, meta
        new_id, meta = await ctx.run(_work)
    except BaseException:
        imaging.delete_files(_media_dir(ctx), img.file, img.thumb, img.preview)
        raise
    ctx.target = f"carte:{new_id} < {label}"
    if max(img.width, img.height) < 2000:
        meta["note"] = "Image source de petite taille : le zoom sera vite flou (une image de 4000 à 8000 px est idéale)."
    return meta


async def update_map(ctx: Context, args: dict[str, Any]) -> Any:
    row = _map_row(ctx, _int(args, "map_id"))
    ctx.target = f"carte:{row['id']}"
    ctx.check_write(row["campaign_id"], "Carte")
    body: dict[str, Any] = {}
    if args.get("name") is not None:
        body["name"] = str(args["name"])
    if "place_id" in args:
        body["place_id"] = _int(args, "place_id", required=False)
    if args.get("pin_style") is not None:
        if args["pin_style"] not in PIN_STYLES:
            raise ToolError("pin_style : dot, icon ou hidden")
        body["pin_style"] = args["pin_style"]
    if not body:
        raise ToolError("Rien à modifier : name, place_id (null pour ne représenter aucun lieu) ou pin_style")
    out = await ctx.api("PATCH", f"/api/maps/{row['id']}", body, write=True)
    return {"map_id": out["id"], "name": out["name"], "place_id": out["place_id"], "pin_style": out["pin_style"]}


async def set_place_kind(ctx: Context, args: dict[str, Any]) -> Any:
    """Même type pour plusieurs lieux (ex. tous les mondes-ruches d'après la légende d'une carte)."""
    ids = args.get("place_ids")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_KIND_BATCH:
        raise ToolError(f"place_ids : liste de 1 à {MAX_KIND_BATCH} identifiants de lieux")
    kind = args.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ToolError("kind : clé d'un type de lieu de la campagne (voir get_campaign, place_kinds)")
    place_ids = [_int({"id": v}, "id") for v in ids]
    campaigns = {ctx.campaign_of("places", pid) for pid in place_ids}
    if len(campaigns) != 1 or None in campaigns:
        raise ToolError("Lieux introuvables, ou de plusieurs campagnes : un appel par campagne")
    cid = ctx.check_write(campaigns.pop(), "Lieu")
    ctx.target = f"type:{kind} x{len(place_ids)}"
    with ctx.transaction() as conn:
        if not conn.execute("SELECT 1 FROM place_kinds WHERE campaign_id = ? AND key = ?", (cid, kind)).fetchone():
            keys = [r[0] for r in conn.execute("SELECT key FROM place_kinds WHERE campaign_id = ? ORDER BY sort_order",
                                               (cid,))]
            raise ToolError(f"Type inconnu dans cette campagne. Types : {', '.join(keys)}")
    updated, failed = [], []
    for pid in place_ids:   # lieu par lieu : un échec (lieu supprimé entre-temps…) n'arrête pas les autres
        try:
            await ctx.api("PATCH", f"/api/places/{pid}", {"kind": kind}, write=True)
            updated.append(pid)
        except ToolError as exc:
            failed.append({"place_id": pid, "error": str(exc)})
    return {"kind": kind, "updated": updated, "failed": failed}


async def place_on_map(ctx: Context, args: dict[str, Any]) -> Any:
    row = _map_row(ctx, _int(args, "map_id"))
    pid = _int(args, "place_id")
    ctx.target = f"carte:{row['id']} lieu:{pid}"
    ctx.check_write(row["campaign_id"], "Carte")
    try:
        x, y = float(args["x"]), float(args["y"])
    except (KeyError, TypeError, ValueError):
        raise ToolError("x et y : pourcentages de 0 à 100") from None
    if not (0 <= x <= 100 and 0 <= y <= 100):
        raise ToolError("x et y : pourcentages de 0 à 100")
    await ctx.api("PUT", f"/api/maps/{row['id']}/pins/{pid}", {"x": x / 100, "y": y / 100}, write=True)
    return {"map_id": row["id"], "place_id": pid, "x": round(x, 1), "y": round(y, 1)}


async def remove_from_map(ctx: Context, args: dict[str, Any]) -> Any:
    row = _map_row(ctx, _int(args, "map_id"))
    pid = _int(args, "place_id")
    ctx.target = f"carte:{row['id']} lieu:{pid}"
    ctx.check_write(row["campaign_id"], "Carte")
    await ctx.api("DELETE", f"/api/maps/{row['id']}/pins/{pid}", write=True)
    return {"map_id": row["id"], "removed": pid}


TOOLS = [
    _tool("list_maps", "Cartes interactives",
          "Cartes interactives d'une campagne (Lieux > Carte interactive) : fond importé et lieux épinglés.",
          {"campaign_id": ID}, ["campaign_id"], list_maps),
    _tool("view_map", "Voir une carte",
          "Aperçu d'une carte interactive quadrillé tous les 10 %, avec ses épingles numérotées, la liste des "
          "lieux épinglés (positions en %) et des lieux à épingler. grid=false pour l'image sans quadrillage.",
          {"map_id": ID, "grid": {"type": "boolean"}}, ["map_id"], view_map),
    _tool("create_map", "Créer une carte interactive",
          "Crée une carte depuis une adresse web publique ou une image de Maze (gardée jusqu'à 8192 px). "
          "place_id : lieu que la carte représente (la carte de Valdis), facultatif.",
          {"campaign_id": ID, "name": {"type": "string"}, "place_id": ID, **SOURCE},
          ["campaign_id"], create_map, write=True),
    _tool("update_map", "Modifier une carte",
          "Renomme une carte (name), change le lieu qu'elle représente (place_id, null pour aucun) ou le style "
          "de ses épingles (pin_style).",
          {"map_id": ID, "name": {"type": "string"}, "place_id": {"type": ["integer", "null"]},
           "pin_style": {"type": "string", "enum": list(PIN_STYLES),
                         "description": "dot : pastille ; icon : icône du type de lieu ; hidden : discrète, posée sur "
                                        "le symbole déjà imprimé sur le fond"}},
          ["map_id"], update_map, write=True),
    _tool("set_place_kind", "Typer des lieux",
          "Donne le même type (kind, clé d'un type de lieu de la campagne, voir get_campaign) à plusieurs lieux d'une "
          "campagne, par exemple tous les mondes-ruches d'après la légende de la carte (200 au plus par appel).",
          {"place_ids": {"type": "array", "items": ID, "minItems": 1, "maxItems": MAX_KIND_BATCH},
           "kind": {"type": "string"}}, ["place_ids", "kind"], set_place_kind, write=True),
    _tool("place_on_map", "Épingler un lieu",
          "Pose ou déplace l'épingle d'un lieu sur une carte : x et y en pourcentages de l'image (0 à 100, depuis "
          "le coin haut gauche ; voir le quadrillage de view_map). Inutile pour les sous-lieux d'un lieu épinglé.",
          {"map_id": ID, "place_id": ID, "x": PCT, "y": PCT}, ["map_id", "place_id", "x", "y"],
          place_on_map, write=True),
    _tool("remove_from_map", "Retirer une épingle",
          "Retire l'épingle d'un lieu d'une carte (le lieu lui-même n'est pas touché).",
          {"map_id": ID, "place_id": ID}, ["map_id", "place_id"], remove_from_map, write=True),
]
