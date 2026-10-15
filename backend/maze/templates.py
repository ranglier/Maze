"""Modèles de fiches rédigées, communs à tous les MJ de l'instance.

Les modèles fournis avec Maze (scénario, scène, lieu, séance) sont définis dans l'interface ;
ici, ceux enregistrés par les MJ. Leur contenu ne reprend ni mentions ni images (retirées par
l'éditeur, qui les remplace par du texte) ni ancres de commentaires.
"""

from __future__ import annotations

import re
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, db
from .api import parse_body, path_id, threaded, tx
from .documents import COMMENT_ANCHOR
from .htmlimg import remove_imgs
from .schemas import TemplateCreate, TemplateUpdate

MENTION_ATTR = re.compile(r'\sdata-mention="[^"]*"')
FIELDS = "id, name, description, content, user_id, author_name, created_at, updated_at"


def _clean(html: str) -> str:
    html = COMMENT_ANCHOR.sub("", html)
    html = MENTION_ATTR.sub("", html)
    # Images retirées par un découpage linéaire (audit PERF-5 : l'ancien motif <img\b[^>]*> figeait le serveur 4 s
    # pour un modèle de 200 Ko de « <img » sans « > ») ; attribut contenant « < » ou « > » compris (htmlimg).
    return remove_imgs(html)


def _out(row) -> dict[str, Any]:
    # Partage assumé (audit SEC-3) : tout modèle est visible par tous les MJ de l'instance ; dit par l'API pour que
    # l'interface l'affiche (seuls son auteur et les administrateurs peuvent le modifier ou le supprimer).
    return {**dict(row), "shared": True}


def _get(conn, tid: int) -> dict[str, Any]:
    row = conn.execute(f"SELECT {FIELDS} FROM templates WHERE id = ?", (tid,)).fetchone()
    if row is None:
        raise HTTPException(404, "Modèle introuvable")
    return _out(row)


def _check_owner(request: Request, row: dict[str, Any]) -> None:
    user = auth.current_user(request)
    if user["role"] != "admin" and row["user_id"] != user["id"]:
        raise HTTPException(403, "Seul l'auteur du modèle (ou un administrateur) peut le modifier")


@threaded
def list_templates(request: Request) -> Response:
    auth.current_user(request)
    with tx(request) as conn:
        rows = [_out(r) for r in conn.execute(f"SELECT {FIELDS} FROM templates ORDER BY name COLLATE NOCASE, id")]
    return JSONResponse({"templates": rows})


@threaded
def create_template(request: Request) -> Response:
    data = parse_body(request, TemplateCreate)
    user = auth.current_user(request)
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Nom du modèle requis")
    with tx(request) as conn:
        tid = db.insert(conn, "templates", {
            "name": name, "description": data.description.strip(), "content": _clean(data.content),
            "user_id": user["id"], "author_name": user["display_name"] or user["username"],
        })
        row = _get(conn, tid)
    return JSONResponse(row, status_code=201)


@threaded
def update_template(request: Request) -> Response:
    tid = path_id(request)
    data = parse_body(request, TemplateUpdate)
    values = {k: v.strip() for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    if "name" in values and not values["name"]:
        raise HTTPException(400, "Nom du modèle requis")
    with tx(request) as conn:
        _check_owner(request, _get(conn, tid))
        if values:
            db.update(conn, "templates", tid, values)
        row = _get(conn, tid)
    return JSONResponse(row)


@threaded
def delete_template(request: Request) -> Response:
    tid = path_id(request)
    with tx(request) as conn:
        _check_owner(request, _get(conn, tid))
        conn.execute("DELETE FROM templates WHERE id = ?", (tid,))
    return Response(status_code=204)


routes = [
    Route("/api/templates", list_templates),
    Route("/api/templates", create_template, methods=["POST"]),
    Route("/api/templates/{id:int}", update_template, methods=["PATCH"]),
    Route("/api/templates/{id:int}", delete_template, methods=["DELETE"]),
]
