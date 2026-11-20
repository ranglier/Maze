"""Outil request_upload du connecteur Claude : lien de dépôt d'image (uploads.py, migration 035).

Claude ne peut pas transmettre à Maze une image jointe à la conversation : il demande un lien vers une page de Maze
(#/depot/<jeton>, 10 minutes, usage unique), le donne au MJ, qui y dépose l'image (session Maze ouverte, accès à la
campagne exigé) ; l'image arrive directement dans la cible. Claude interroge ensuite le ticket pour connaître l'image.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import db, uploads
from .mcp import ID, Context, ToolError, _int, _tool

TARGET_NAMES = {"library": "la bibliothèque", "npc": "le PNJ", "faction": "la faction", "place": "le lieu",
                "document": "la fiche rédigée"}
RESULT_KEY = {"library": "library_image_id", "npc": "image_id", "faction": "image_id", "place": "image_id",
              "document": "document_image_id"}


def _status(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    state = uploads.state_of(row)
    out: dict[str, Any] = {
        "ticket": row["id"], "state": state, "campaign_id": row["campaign_id"], "target": row["target"],
        "target_id": row["target_id"], "target_name": uploads.target_name(conn, row["target"], row["target_id"]),
        "expires_at": uploads.iso(row["expires_at"]),
    }
    if state == "pending":
        out["next"] = ("Le MJ n'a pas encore déposé l'image : attendre qu'il le dise, puis interroger à nouveau ce "
                       "ticket.")
    elif state == "expired":
        out["next"] = "Lien expiré sans dépôt : en demander un nouveau (request_upload) si le MJ le souhaite."
    else:
        image = uploads.result_image(conn, row)
        out["used_at"] = row["used_at"]
        out[RESULT_KEY[row["target"]]] = row["image_id"]
        if image is None:
            out["note"] = "Image supprimée depuis le dépôt."
        else:
            out.update(width=image["width"], height=image["height"])
            if row["target"] == "document":
                out["src"] = image["url"]
                out["html"] = f'<img src="{image["url"]}" alt="" data-doc-image="{row["image_id"]}">'
                out["next"] = ("Insérer ce HTML (bloc à part, hors d'un paragraphe) dans le contenu de la fiche rédigée "
                               "avec update_document.")
            elif row["target"] == "library":
                out["next"] = "Image dans la bibliothèque : view (kind library_image, id = library_image_id) pour la voir."
            else:
                out["next"] = "Image ajoutée à la galerie de la fiche : view (kind image, id = image_id) pour la voir."
    return out


async def request_upload(ctx: Context, args: dict[str, Any]) -> Any:
    ticket_id = _int(args, "ticket", required=False)
    if ticket_id is not None:
        if any(args.get(k) is not None for k in ("campaign_id", "target", "target_id", "caption")):
            raise ToolError("ticket : à donner seul (suivi d'un lien déjà demandé)")
        ctx.target = f"ticket:{ticket_id}"
        with ctx.transaction() as conn:
            row = db.row_to_dict(conn.execute("SELECT * FROM upload_tickets WHERE id = ?", (ticket_id,)).fetchone())
            if row is None or row["campaign_id"] not in ctx.grants:
                raise ToolError("Ticket introuvable (supprimé un jour après son échéance, ou campagne non ouverte à "
                                "Claude)")
            ctx.check_read(row["campaign_id"])
            return _status(conn, row)

    cid = _int(args, "campaign_id")
    target = args.get("target")
    if target not in uploads.TARGETS:
        raise ToolError(f"Paramètre target : une valeur parmi {', '.join(uploads.TARGETS)}")
    ctx.check_write(cid, "Campagne")
    target_id = _int(args, "target_id", required=False)
    if target == "library":
        if target_id is not None:
            raise ToolError("target_id : sans objet pour la bibliothèque")
        ctx.target = "bibliothèque"
    else:
        if target_id is None:
            raise ToolError(f"Paramètre manquant : target_id ({TARGET_NAMES[target]} qui recevra l'image)")
        ctx.target = f"{'doc' if target == 'document' else target}:{target_id}"
        if ctx.campaign_of(uploads.TARGET_TABLES[target], target_id) != cid:
            raise ToolError("Fiche introuvable dans cette campagne")
    caption = args.get("caption")
    if caption is not None and not isinstance(caption, str):
        raise ToolError("caption : texte attendu")
    if target == "document" and caption:
        raise ToolError("caption : sans objet pour une fiche rédigée (texte alternatif à mettre dans le HTML inséré)")

    def work(conn: sqlite3.Connection) -> dict[str, Any]:
        ticket = uploads.create_ticket(conn, ctx.user["id"], cid, target, target_id, caption or "")
        name = uploads.target_name(conn, target, target_id)
        return {**ticket, "target_name": name}
    made = await ctx.run(work)
    base = ctx.app.state.settings.public_url or ctx.base
    minutes = uploads.TICKET_SECONDS // 60
    return {
        "ticket": made["id"], "url": f"{base}/#/depot/{made['token']}",
        "expires_at": uploads.iso(made["expires_at"]), "valid_minutes": minutes,
        "campaign_id": cid, "target": target, "target_id": target_id, "target_name": made["target_name"],
        "next": (f"Donner ce lien au MJ (valable {minutes} minutes, une seule image) : il l'ouvre dans son navigateur, "
                 "connecté à Maze, et y dépose l'image. Attendre qu'il dise l'avoir déposée, puis appeler "
                 f"request_upload avec ticket={made['id']} pour connaître l'image créée."),
    }


TOOLS = [
    _tool("request_upload", "Lien de dépôt d'image",
          "Pour une image que le MJ a sous la main (jointe à la conversation, sur son ordinateur) : Claude ne peut pas "
          "la transmettre lui-même à Maze. Cet outil crée un lien vers une page de Maze, valable 10 minutes et pour "
          "une seule image, où le MJ la dépose ; elle arrive directement dans la cible : bibliothèque de la campagne "
          "(target=library), galerie d'un PNJ, d'une faction ou d'un lieu (npc, faction, place, avec target_id ; "
          "la première image d'une fiche sans portrait le devient) ou illustration d'une fiche rédigée (document, "
          "avec target_id ; à insérer ensuite avec update_document). caption : légende proposée, modifiable par le MJ "
          "(pas pour document). Donner le lien au MJ, attendre qu'il dise l'avoir déposée, puis rappeler cet outil "
          "avec ticket seul : état (pending, used, expired) et identifiant de l'image créée. Pour une image en ligne "
          "(adresse publique), add_image suffit.",
          {"campaign_id": ID, "target": {"type": "string", "enum": list(uploads.TARGETS)}, "target_id": ID,
           "caption": {"type": "string", "maxLength": uploads.CAPTION_MAX},
           "ticket": {**ID, "description": "Suivi d'un lien déjà demandé (à donner seul)"}},
          [], request_upload, write=True),
]
