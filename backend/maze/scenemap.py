"""Carte des relations propre à un scénario : sélection des fiches et disposition.

La distribution d'un scénario = fiches citées (@) dans le scénario et ses pages, plus celles ajoutées
à la main, moins celles retirées. Le calcul se fait dans l'interface (mentions + réglages) ; le serveur
ne garde que les réglages et les positions (table scenario_nodes).
"""

from __future__ import annotations

import sqlite3

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import db
from .api import TABLES, parse_body, path_id, scoped, threaded, tx
from .schemas import PositionsUpdate, ScenarioNodeState

UPSERT_POSITION = """
    INSERT INTO scenario_nodes (document_id, entity_kind, entity_id, pos_x, pos_y) VALUES (?, ?, ?, ?, ?)
    ON CONFLICT (document_id, entity_kind, entity_id) DO UPDATE SET pos_x = excluded.pos_x, pos_y = excluded.pos_y
"""


def _scenario(conn: sqlite3.Connection, request: Request, did: int) -> dict:
    doc = scoped(conn, request, "documents", did, "Scénario")
    if doc["kind"] != "scenario":
        raise HTTPException(400, "Seuls les scénarios ont une carte")
    return doc


def _owned(conn: sqlite3.Connection, campaign_id: int, kind: str, ids: set[int]) -> set[int]:
    """Identifiants de fiches qui appartiennent bien à la campagne."""
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    return {r[0] for r in conn.execute(
        f"SELECT id FROM {TABLES[kind]} WHERE campaign_id = ? AND id IN ({marks})", [campaign_id, *ids])}


def nodes_of(conn: sqlite3.Connection, did: int) -> list[dict]:
    return db.rows_to_dicts(conn.execute(
        """SELECT entity_kind AS kind, entity_id AS id, state, pos_x, pos_y
             FROM scenario_nodes WHERE document_id = ?""", (did,)).fetchall())


@threaded
def get_map(request: Request) -> Response:
    did = path_id(request)
    with tx(request) as conn:
        doc = _scenario(conn, request, did)
        nodes = nodes_of(conn, did)
    return JSONResponse({"document": {"id": doc["id"], "title": doc["title"], "campaign_id": doc["campaign_id"]},
                         "nodes": nodes})


@threaded
def save_positions(request: Request) -> Response:
    did = path_id(request)
    data = parse_body(request, PositionsUpdate)
    with tx(request) as conn:
        doc = _scenario(conn, request, did)
        for kind in ("npc", "faction"):
            wanted = [n for n in data.nodes if n.kind == kind]
            valid = _owned(conn, doc["campaign_id"], kind, {n.id for n in wanted})
            conn.executemany(UPSERT_POSITION, [(did, kind, n.id, n.x, n.y) for n in wanted if n.id in valid])
    return Response(status_code=204)


@threaded
def set_node(request: Request) -> Response:
    did = path_id(request)
    kind = request.path_params["kind"]
    eid = int(request.path_params["eid"])
    if kind not in ("npc", "faction"):
        raise HTTPException(404, "Fiche introuvable")
    data = parse_body(request, ScenarioNodeState)
    with tx(request) as conn:
        doc = _scenario(conn, request, did)
        if not _owned(conn, doc["campaign_id"], kind, {eid}):
            raise HTTPException(400, "Cette fiche n'est pas dans la campagne du scénario")
        conn.execute(
            """INSERT INTO scenario_nodes (document_id, entity_kind, entity_id, state, pos_x, pos_y)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (document_id, entity_kind, entity_id) DO UPDATE SET state = excluded.state,
                   pos_x = COALESCE(excluded.pos_x, scenario_nodes.pos_x),
                   pos_y = COALESCE(excluded.pos_y, scenario_nodes.pos_y)""",
            (did, kind, eid, data.state, data.x, data.y))
        nodes = nodes_of(conn, did)
    return JSONResponse({"nodes": nodes})


def overrides(conn: sqlite3.Connection, campaign_id: int) -> dict[int, list[list]]:
    """Ajouts et retraits manuels de chaque scénario de la campagne : {doc_id: [[kind, id, state]]}."""
    out: dict[int, list[list]] = {}
    for did, kind, eid, state in conn.execute(
            """SELECT s.document_id, s.entity_kind, s.entity_id, s.state FROM scenario_nodes s
                 JOIN documents d ON d.id = s.document_id
                WHERE d.campaign_id = ? AND d.kind = 'scenario' AND s.state != 'auto'""", (campaign_id,)):
        out.setdefault(did, []).append([kind, eid, state])
    return out


routes = [
    Route("/api/documents/{id:int}/map", get_map),
    Route("/api/documents/{id:int}/map/positions", save_positions, methods=["PUT"]),
    Route("/api/documents/{id:int}/map/nodes/{kind}/{eid:int}", set_node, methods=["PUT"]),
]
