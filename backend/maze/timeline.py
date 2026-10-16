"""Chronologie d'une campagne (module facultatif, migration 032) : liste d'événements dans un ordre manuel.

La date est un texte libre (aucun calendrier) ; la description est le HTML de l'éditeur, dont les @mentions sont
recopiées dans timeline_mentions (rétroliens « Chronologie » des fiches PNJ, faction et lieu). Le drapeau
campaigns.timeline_enabled ne sert qu'à l'affichage : les routes répondent que le module soit activé ou non.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import db
from .api import get_or_404, parse_body, path_id, scoped, threaded, touch_campaign, tx
from .documents import MENTION
from .i18n import msg
from .schemas import EventCreate, EventOrder, EventUpdate

MAX_EVENTS = 5000
TABLES = {"npc": "npcs", "faction": "factions", "place": "places", "doc": "documents"}
NOT_IN_CAMPAIGN = "L'événement de référence n'existe pas dans cette campagne"
# Champs non nuls en base : une valeur null envoyée dans un PATCH est ignorée.
FIELDS = ("title", "date_label", "description", "tags")


def sync_event_mentions(conn: sqlite3.Connection, event_id: int, html: str) -> None:
    """Fiches citées par la description (comme documents.sync_mentions), limitées à la campagne de l'événement.
    Appelée à la création, à la modification et à l'import d'une campagne (transfer.py)."""
    conn.execute("DELETE FROM timeline_mentions WHERE event_id = ?", (event_id,))
    found = conn.execute("SELECT campaign_id FROM timeline_events WHERE id = ?", (event_id,)).fetchone()
    if found is None:
        return
    targets = {(kind, int(tid)) for kind, tid in MENTION.findall(html or "")}
    targets = {(kind, tid) for kind, tid in targets if conn.execute(
        f"SELECT 1 FROM {TABLES[kind]} WHERE id = ? AND campaign_id = ?", (tid, found[0])).fetchone()}
    conn.executemany("INSERT INTO timeline_mentions (event_id, target_kind, target_id) VALUES (?, ?, ?)",
                     [(event_id, kind, tid) for kind, tid in sorted(targets)])


def _mentions(conn: sqlite3.Connection, where: str, params: tuple) -> dict[int, list[list[Any]]]:
    out: dict[int, list[list[Any]]] = {}
    for r in conn.execute(f"""SELECT m.event_id, m.target_kind, m.target_id FROM timeline_mentions m
                                JOIN timeline_events e ON e.id = m.event_id
                               WHERE {where} ORDER BY m.event_id, m.target_kind, m.target_id""", params):
        out.setdefault(r[0], []).append([r[1], r[2]])
    return out


def event_out(conn: sqlite3.Connection, event_id: int) -> dict[str, Any]:
    row = get_or_404(conn, "timeline_events", event_id, "Événement")
    row["mentions"] = _mentions(conn, "e.id = ?", (event_id,)).get(event_id, [])
    return row


def list_of(conn: sqlite3.Connection, campaign_id: int) -> list[dict[str, Any]]:
    """Événements de la campagne, dans l'ordre de la chronologie, avec les fiches que chacun cite."""
    rows = db.rows_to_dicts(conn.execute(
        "SELECT * FROM timeline_events WHERE campaign_id = ? ORDER BY sort_order, id", (campaign_id,)).fetchall())
    mentions = _mentions(conn, "e.campaign_id = ?", (campaign_id,))
    for r in rows:
        r["mentions"] = mentions.get(r["id"], [])
    return rows


def backlinks(conn: sqlite3.Connection, kind: str, target_id: int, campaign_id: int) -> list[dict[str, Any]]:
    """Événements qui citent cette fiche (rétroliens), dans l'ordre de la chronologie."""
    return db.rows_to_dicts(conn.execute(
        """SELECT e.id, e.date_label, e.title FROM timeline_mentions m
             JOIN timeline_events e ON e.id = m.event_id
            WHERE m.target_kind = ? AND m.target_id = ? AND e.campaign_id = ?
            ORDER BY e.sort_order, e.id""", (kind, target_id, campaign_id)).fetchall())


def _renumber(conn: sqlite3.Connection, ids: list[int]) -> None:
    conn.executemany("UPDATE timeline_events SET sort_order = ? WHERE id = ?", [(i + 1, x) for i, x in enumerate(ids)])


@threaded
def list_events(request: Request) -> Response:
    cid = path_id(request)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        events = list_of(conn, cid)
    return JSONResponse({"events": events})


@threaded
def create_event(request: Request) -> Response:
    cid = path_id(request)
    data = parse_body(request, EventCreate)
    values = data.model_dump(exclude={"before_id"})
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        if conn.execute("SELECT COUNT(*) FROM timeline_events WHERE campaign_id = ?",
                        (cid,)).fetchone()[0] >= MAX_EVENTS:
            raise HTTPException(400, msg("{max} événements au plus par campagne", max=MAX_EVENTS))
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM timeline_events WHERE campaign_id = ? ORDER BY sort_order, id", (cid,))]
        if data.before_id is not None and data.before_id not in ids:
            raise HTTPException(400, NOT_IN_CAMPAIGN)
        new_id = db.insert(conn, "timeline_events", {**values, "campaign_id": cid})
        ids.insert(ids.index(data.before_id) if data.before_id is not None else len(ids), new_id)
        _renumber(conn, ids)
        sync_event_mentions(conn, new_id, values["description"])
        touch_campaign(conn, cid)
        row = event_out(conn, new_id)
    return JSONResponse(row, status_code=201)


def revision_conflict(conn: sqlite3.Connection, current: dict[str, Any], base: int | None) -> JSONResponse | None:
    """Événement modifié depuis la révision sur laquelle le formulaire est parti : 409 avec l'état actuel, même
    forme que pour un PNJ (api.revision_conflict). Les événements n'ont pas d'historique : auteur inconnu."""
    if base is None or base == current["revision"]:
        return None
    return JSONResponse({"detail": "Fiche modifiée entre-temps : vos changements n'ont pas été enregistrés",
                         "author": "", "current": event_out(conn, current["id"])}, status_code=409)


@threaded
def update_event(request: Request) -> Response:
    eid = path_id(request)
    data = parse_body(request, EventUpdate)
    values = {k: v for k, v in data.model_dump(exclude_unset=True).items() if k in FIELDS and v is not None}
    with tx(request) as conn:
        current = scoped(conn, request, "timeline_events", eid, "Événement")
        conflict = revision_conflict(conn, current, data.base_revision)
        if conflict is not None:
            return conflict
        changed = {k: v for k, v in values.items() if v != current[k]}
        if changed:
            db.update(conn, "timeline_events", eid, changed)
            if "description" in changed:
                sync_event_mentions(conn, eid, changed["description"])
            touch_campaign(conn, current["campaign_id"])
        row = event_out(conn, eid)
    return JSONResponse(row)


@threaded
def delete_event(request: Request) -> Response:
    eid = path_id(request)
    with tx(request) as conn:
        current = scoped(conn, request, "timeline_events", eid, "Événement")
        conn.execute("DELETE FROM timeline_events WHERE id = ?", (eid,))
        touch_campaign(conn, current["campaign_id"])
    return Response(status_code=204)


@threaded
def reorder_events(request: Request) -> Response:
    """Ordre manuel : la liste complète des événements de la campagne, du premier au dernier."""
    cid = path_id(request)
    data = parse_body(request, EventOrder)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        existing = {r[0] for r in conn.execute("SELECT id FROM timeline_events WHERE campaign_id = ?", (cid,))}
        if set(data.ids) != existing or len(data.ids) != len(existing):
            raise HTTPException(400, "La liste doit contenir exactement les événements de cette campagne")
        _renumber(conn, data.ids)
        touch_campaign(conn, cid)
        events = list_of(conn, cid)
    return JSONResponse({"events": events})


routes = [
    Route("/api/campaigns/{id:int}/events", list_events),
    Route("/api/campaigns/{id:int}/events", create_event, methods=["POST"]),
    Route("/api/campaigns/{id:int}/events/order", reorder_events, methods=["PUT"]),
    Route("/api/events/{id:int}", update_event, methods=["PATCH"]),
    Route("/api/events/{id:int}", delete_event, methods=["DELETE"]),
]
