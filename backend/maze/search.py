"""Recherche globale : PNJ, factions, lieux, fiches rédigées et commentaires (index FTS5, migrations 008 et 010)."""

from __future__ import annotations

import re
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth
from .api import threaded, tx

MAX_RESULTS = 40
# Marqueurs de surlignage (caractères de contrôle, jamais présents dans le texte) : l'interface les
# transforme en <mark> sans interpréter de HTML.
HL_START, HL_END = "\x02", "\x03"


def fts_query(text: str) -> str | None:
    """« bal masq » -> "bal"* "masq"* : chaque mot (début de mot) doit apparaître."""
    words = re.findall(r"\w+", text)[:8]
    if not words or sum(len(w) for w in words) < 2:
        return None
    return " ".join(f'"{w}"*' for w in words)


def _campaign_ids(conn, user: dict[str, Any], requested: str | None) -> list[int]:
    allowed = auth.visible_campaign_ids(conn, user)  # connecteur Claude : campagnes qui lui sont ouvertes
    if allowed is None:
        allowed = [r[0] for r in conn.execute("SELECT id FROM campaigns")]
    if requested in (None, "", "all"):
        return allowed
    try:
        cid = int(requested)
    except ValueError:
        raise HTTPException(400, "Campagne invalide") from None
    return [cid] if cid in allowed else []


@threaded
def search(request: Request) -> Response:
    user = auth.current_user(request)
    query = fts_query(request.query_params.get("q", "")[:200])
    if query is None:
        return JSONResponse({"results": []})
    with tx(request) as conn:
        campaigns = _campaign_ids(conn, user, request.query_params.get("campaign"))
        if not campaigns:
            return JSONResponse({"results": []})
        marks = ",".join("?" * len(campaigns))
        rows = conn.execute(
            f"""SELECT kind, ref_id, campaign_id, doc_id,
                       highlight(search_index, 4, ?, ?) AS title_hl,
                       snippet(search_index, 5, ?, ?, '…', 14) AS snippet
                  FROM search_index
                 WHERE search_index MATCH ? AND campaign_id IN ({marks})
                 ORDER BY bm25(search_index, 0, 0, 0, 0, 8.0, 1.0)
                 LIMIT {MAX_RESULTS}""",
            (HL_START, HL_END, HL_START, HL_END, query, *campaigns)).fetchall()
        names = dict(conn.execute(f"SELECT id, name FROM campaigns WHERE id IN ({marks})", campaigns).fetchall())
        results = []
        for r in rows:
            item = {"kind": r["kind"], "id": r["ref_id"], "campaign_id": r["campaign_id"],
                    "campaign": names.get(r["campaign_id"], ""), "doc_id": r["doc_id"],
                    "title": r["title_hl"], "sub": "",
                    # Liens [[Nom]] des notes : sans les crochets dans l'extrait.
                    "snippet": (r["snippet"] or "").replace("[[", "").replace("]]", "")}
            if r["kind"] == "npc":
                npc = conn.execute("""SELECT n.title, n.status, i.thumb FROM npcs n
                                        LEFT JOIN images i ON i.id = n.portrait_image_id WHERE n.id = ?""",
                                   (r["ref_id"],)).fetchone()
                if npc is None:
                    continue
                item.update(sub=npc["title"], status=npc["status"], thumb=f"/media/{npc['thumb']}" if npc["thumb"] else None)
            elif r["kind"] == "faction":
                fac = conn.execute("""SELECT f.kind, f.color, i.thumb FROM factions f
                                        LEFT JOIN images i ON i.id = f.emblem_image_id WHERE f.id = ?""",
                                   (r["ref_id"],)).fetchone()
                if fac is None:
                    continue
                item.update(sub=fac["kind"], color=fac["color"], thumb=f"/media/{fac['thumb']}" if fac["thumb"] else None)
            elif r["kind"] == "place":
                place = conn.execute("""SELECT p.kind, parent.name AS parent, i.thumb, k.label AS kind_label,
                                                 k.icon AS kind_icon, k.image AS kind_image FROM places p
                                          LEFT JOIN places parent ON parent.id = p.parent_id
                                          LEFT JOIN place_kinds k ON k.campaign_id = p.campaign_id AND k.key = p.kind
                                          LEFT JOIN images i ON i.id = p.image_id WHERE p.id = ?""",
                                     (r["ref_id"],)).fetchone()
                if place is None:
                    continue
                item.update(sub=place["parent"] or "", place_kind=place["kind"],
                            place_kind_label=place["kind_label"] or "Lieu", place_kind_icon=place["kind_icon"] or "pin",
                            place_kind_image=f"/media/{place['kind_image']}" if place["kind_image"] else None,
                            thumb=f"/media/{place['thumb']}" if place["thumb"] else None)
            elif r["kind"] == "entry_comment":
                # Commentaire d'un PNJ, d'une faction ou d'un lieu : la fiche commentée.
                owner = conn.execute(
                    """SELECT c.npc_id, c.faction_id, c.place_id,
                              COALESCE(n.name, f.name, p.name) AS name
                         FROM entry_comments c
                         LEFT JOIN npcs n ON n.id = c.npc_id LEFT JOIN factions f ON f.id = c.faction_id
                         LEFT JOIN places p ON p.id = c.place_id WHERE c.id = ?""", (r["ref_id"],)).fetchone()
                if owner is None:
                    continue
                kind = "npc" if owner["npc_id"] else "faction" if owner["faction_id"] else "place"
                item.update(entry_kind=kind, entry_id=owner[f"{kind}_id"], entry_name=owner["name"] or "")
            elif r["kind"] in ("document", "comment"):
                doc = conn.execute("SELECT kind, title FROM documents WHERE id = ?", (r["doc_id"],)).fetchone()
                if doc is None:
                    continue
                item["doc_kind"] = doc["kind"]
                item["doc_title"] = doc["title"]
            results.append(item)
    return JSONResponse({"results": results})


routes = [Route("/api/search", search)]
