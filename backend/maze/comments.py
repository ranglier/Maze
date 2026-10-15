"""Commentaires sur les fiches rédigées : fils ancrés dans le texte, réponses, résolution.

L'ancrage vit dans le document partagé (marque <span data-comment="id">) : il suit les
modifications de tous les MJ. Ici, on ne stocke que les fils et leurs messages. Chaque
changement est signalé aux MJ connectés à la fiche, qui rechargent la liste.
"""

from __future__ import annotations

from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, db
from .api import parse_body, path_id, scoped, threaded, tx
from .schemas import CommentCreate, CommentUpdate

FIELDS = "id, parent_id, user_id, author_name, body, quote, resolved, resolved_by, resolved_at, created_at, updated_at"


def _notify(request: Request, doc_id: int) -> None:
    hub = getattr(request.app.state, "hub", None)
    if hub is not None:
        hub.notify(doc_id, {"t": "comments"})


def _threads(conn, doc_id: int) -> list[dict[str, Any]]:
    rows = [dict(r) for r in conn.execute(
        f"SELECT {FIELDS} FROM comments WHERE document_id = ? ORDER BY id", (doc_id,))]
    threads: dict[int, dict[str, Any]] = {}
    for row in rows:
        row["resolved"] = bool(row["resolved"])
        if row["parent_id"] is None:
            threads[row["id"]] = {**row, "replies": []}
    for row in rows:
        if row["parent_id"] in threads:
            threads[row["parent_id"]]["replies"].append(row)
    return list(threads.values())


def _comment(conn, request: Request, comment_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Commentaire introuvable")
    doc = scoped(conn, request, "documents", row["document_id"], "Commentaire")
    return dict(row), doc


def _can_edit(user: dict[str, Any], row: dict[str, Any]) -> bool:
    return user["role"] == "admin" or row["user_id"] == user["id"]


@threaded
def list_comments(request: Request) -> Response:
    did = path_id(request)
    with tx(request) as conn:
        scoped(conn, request, "documents", did, "Fiche")
        return JSONResponse(_threads(conn, did))


@threaded
def create_comment(request: Request) -> Response:
    did = path_id(request)
    data = parse_body(request, CommentCreate)
    user = auth.current_user(request)
    with tx(request) as conn:
        scoped(conn, request, "documents", did, "Fiche")
        if data.parent_id is not None:
            parent = conn.execute("SELECT document_id, parent_id FROM comments WHERE id = ?",
                                  (data.parent_id,)).fetchone()
            if parent is None or parent["document_id"] != did or parent["parent_id"] is not None:
                raise HTTPException(400, "Fil de commentaires introuvable")
            conn.execute("UPDATE comments SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                         (data.parent_id,))
        new_id = db.insert(conn, "comments", {
            "document_id": did, "parent_id": data.parent_id, "user_id": user["id"],
            "author_name": user["display_name"] or user["username"],
            "body": data.body.strip(), "quote": data.quote.strip() if data.parent_id is None else "",
        })
        row = dict(conn.execute(f"SELECT {FIELDS} FROM comments WHERE id = ?", (new_id,)).fetchone())
        row["resolved"] = bool(row["resolved"])
    _notify(request, did)
    return JSONResponse(row, status_code=201)


@threaded
def update_comment(request: Request) -> Response:
    cid = path_id(request)
    data = parse_body(request, CommentUpdate)
    user = auth.current_user(request)
    with tx(request) as conn:
        row, doc = _comment(conn, request, cid)
        if data.body is not None:
            if not _can_edit(user, row):
                raise HTTPException(403, "Seul l'auteur peut modifier ce commentaire")
            conn.execute("UPDATE comments SET body = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                         "WHERE id = ?", (data.body.strip(), cid))
        if data.resolved is not None:
            if row["parent_id"] is not None:
                raise HTTPException(400, "Seul un fil peut être résolu")
            conn.execute(
                "UPDATE comments SET resolved = ?, resolved_by = ?, "
                "resolved_at = CASE WHEN ? THEN strftime('%Y-%m-%dT%H:%M:%SZ', 'now') END WHERE id = ?",
                (int(data.resolved), (user["display_name"] or user["username"]) if data.resolved else "",
                 int(data.resolved), cid))
    _notify(request, doc["id"])
    return JSONResponse({"id": cid})


@threaded
def delete_comment(request: Request) -> Response:
    cid = path_id(request)
    user = auth.current_user(request)
    with tx(request) as conn:
        row, doc = _comment(conn, request, cid)
        if not _can_edit(user, row):
            raise HTTPException(403, "Seul l'auteur peut supprimer ce commentaire")
        conn.execute("DELETE FROM comments WHERE id = ?", (cid,))  # les réponses suivent (cascade)
    _notify(request, doc["id"])
    return Response(status_code=204)


routes = [
    Route("/api/documents/{id:int}/comments", list_comments),
    Route("/api/documents/{id:int}/comments", create_comment, methods=["POST"]),
    Route("/api/comments/{id:int}", update_comment, methods=["PATCH"]),
    Route("/api/comments/{id:int}", delete_comment, methods=["DELETE"]),
]
