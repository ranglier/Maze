"""Cartes interactives du connecteur Claude : voir (aperçu quadrillé avec les épingles), créer une carte, la
renommer ou la rattacher à un lieu, épingler et retirer des lieux. Traitements d'UN élément, appelés par les outils
regroupés de mcp_tools.py (get et view kind map, list kind map, create et update kind map, link et unlink kind pin).

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
from .maps import insert_map, map_detail
from .mcp import Blocks, Context, ToolError, _int
from .mcp_images import VIEW_SIDE, _media_dir, _source_bytes

PIN_STYLES = ("dot", "icon", "hidden")
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
                    "épingles numérotées comme dans pins. Positions en pourcentages pour link (kind pin). "
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
