"""Historique des versions des fiches rédigées.

Instantanés automatiques (voir collab.py) :
- avant une séance d'écriture : le contenu enregistré, quand la dernière version date de plus de
  AUTO_INTERVAL ;
- en fin de séance, quand le dernier MJ quitte la fiche ;
- avant un remplacement du contenu par l'API (restauration d'une version, import).
Versions manuelles : nommées par un MJ, jamais supprimées automatiquement ; de même pour les repères
automatiques nommés (« Avant restauration »…), seules les KEEP_AUTO dernières versions automatiques sans nom
sont gardées. Un instantané identique
à la version la plus récente n'est pas enregistré. Le HTML est stocké compressé.
"""

from __future__ import annotations

import hashlib
import sqlite3
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, db
from .htmlimg import doc_image_id, img_tags, remove_imgs
from .api import in_tx, parse, parse_body, path_id, scoped, threaded, touch_campaign, tx
from .schemas import VersionCreate, VersionRestore, VersionUpdate

AUTO_INTERVAL = timedelta(minutes=10)
KEEP_AUTO = 100          # versions automatiques conservées par fiche
FIELDS = "id, kind, label, title, size, author_name, created_at"


def _digest(html: str) -> str:
    return hashlib.sha256(html.encode()).hexdigest()


def _user_name(conn: sqlite3.Connection, user_id: int | None) -> str:
    if user_id is None:
        return ""
    row = conn.execute("SELECT display_name, username FROM users WHERE id = ?", (user_id,)).fetchone()
    return (row[0] or row[1]) if row else ""


def _latest(conn: sqlite3.Connection, doc_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT id, digest, created_at FROM document_versions WHERE document_id = ? "
                        "ORDER BY id DESC LIMIT 1", (doc_id,)).fetchone()


def insert_version(conn: sqlite3.Connection, doc_id: int, *, kind: str, html: str, title: str,
                   author: str, label: str = "") -> int:
    vid = db.insert(conn, "document_versions", {
        "document_id": doc_id, "kind": kind, "label": label, "title": title,
        "content": zlib.compress(html.encode(), 6), "size": len(html.encode()),
        "digest": _digest(html), "author_name": author,
    })
    if kind == "auto":
        # Seules les versions automatiques sans nom sont élaguées ; les repères nommés (« Avant modification par
        # Claude », « Avant restauration »…) restent, comme pour les fiches PNJ (audit DATA-12).
        conn.execute(
            """DELETE FROM document_versions WHERE document_id = ? AND kind = 'auto' AND label = '' AND id NOT IN (
                   SELECT id FROM document_versions WHERE document_id = ? AND kind = 'auto' AND label = ''
                    ORDER BY id DESC LIMIT ?)""", (doc_id, doc_id, KEEP_AUTO))
    return vid


def snapshot(conn: sqlite3.Connection, doc_id: int, *, label: str = "", only_if_older: bool = False,
             html: str | None = None, author: str | None = None) -> int | None:
    """Version automatique du contenu enregistré de la fiche (ou de `html`, l'état transmis par
    l'éditeur), sauf s'il est vide ou identique à la dernière version (ou, avec only_if_older, si
    la dernière version est récente)."""
    doc = conn.execute("SELECT title, content, updated_by FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if doc is None:
        return None
    content = doc["content"] if html is None else html
    if not content.strip():
        return None
    latest = _latest(conn, doc_id)
    if latest is not None:
        if latest["digest"] == _digest(content):
            return None
        if only_if_older:
            created = datetime.strptime(latest["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            if datetime.now(UTC) - created < AUTO_INTERVAL:
                return None
    return insert_version(conn, doc_id, kind="auto", html=content, title=doc["title"],
                          author=author if author is not None else _user_name(conn, doc["updated_by"]), label=label)


def replace_content(conn: sqlite3.Connection, doc_id: int, html: str, user_id: int) -> None:
    """Remplace le contenu d'une fiche d'un bloc : le journal temps réel repart de ce HTML
    (les MJ connectés sont rechargés par l'appelant, via hub.close_documents)."""
    from .documents import link_images, sync_mentions  # import circulaire évité

    campaign_id = conn.execute("SELECT campaign_id FROM documents WHERE id = ?", (doc_id,)).fetchone()[0]
    conn.execute(
        """UPDATE documents SET content = ?, revision = revision + 1, updated_by = ?, content_seq = 0,
                  live_epoch = live_epoch + 1, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE id = ?""", (html, user_id, doc_id))
    conn.execute("DELETE FROM document_updates WHERE document_id = ?", (doc_id,))
    sync_mentions(conn, doc_id, html)
    link_images(conn, doc_id, campaign_id, html)
    touch_campaign(conn, campaign_id)


def drop_missing_images(conn: sqlite3.Connection, campaign_id: int, html: str) -> str:
    """Retire d'un contenu restauré les illustrations supprimées entre-temps (Administration > Images) :
    leur fichier n'existe plus, elles s'afficheraient cassées."""
    # Découpage linéaire des <img> (audit PERF-5, htmlimg), attribut contenant « < » ou « > » compris.
    cited = {i for _, _, attrs in img_tags(html) if (i := doc_image_id(attrs)) is not None}
    if not cited:
        return html
    marks = ",".join("?" * len(cited))
    present = {r[0] for r in conn.execute(
        f"SELECT id FROM document_images WHERE campaign_id = ? AND id IN ({marks})", [campaign_id, *cited])}
    missing = cited - present
    return remove_imgs(html, lambda attrs: doc_image_id(attrs) in missing) if missing else html


def _version(conn: sqlite3.Connection, doc_id: int, vid: int) -> dict[str, Any]:
    row = conn.execute(f"SELECT {FIELDS}, content FROM document_versions WHERE id = ? AND document_id = ?",
                       (vid, doc_id)).fetchone()
    if row is None:
        raise HTTPException(404, "Version introuvable")
    out = dict(row)
    out["content"] = zlib.decompress(out["content"]).decode()
    return out


# --- Routes ------------------------------------------------------------------------------

@threaded
def list_versions(request: Request) -> Response:
    did = path_id(request)
    with tx(request) as conn:
        doc = scoped(conn, request, "documents", did, "Fiche")
        rows = [dict(r) for r in conn.execute(
            f"SELECT {FIELDS} FROM document_versions WHERE document_id = ? ORDER BY id DESC", (did,))]
    return JSONResponse({"current": {"title": doc["title"], "updated_at": doc["updated_at"]}, "versions": rows})


@threaded
def get_version(request: Request) -> Response:
    did = path_id(request)
    with tx(request) as conn:
        scoped(conn, request, "documents", did, "Fiche")
        return JSONResponse(_version(conn, did, path_id(request, "vid")))


@threaded
def create_version(request: Request) -> Response:
    """Version nommée. Le navigateur envoie le HTML de son éditeur (le contenu enregistré peut avoir
    une ou deux secondes de retard) ; à défaut, le contenu enregistré."""
    did = path_id(request)
    data = parse_body(request, VersionCreate)
    user = auth.current_user(request)
    with tx(request) as conn:
        doc = scoped(conn, request, "documents", did, "Fiche")
        html = doc["content"] if data.content is None else data.content
        vid = insert_version(conn, did, kind="manual", html=html, title=doc["title"],
                             author=user["display_name"] or user["username"], label=data.label.strip())
        row = dict(conn.execute(f"SELECT {FIELDS} FROM document_versions WHERE id = ?", (vid,)).fetchone())
    return JSONResponse(row, status_code=201)


@threaded
def update_version(request: Request) -> Response:
    """Renommer une version ; une version automatique nommée devient manuelle (conservée)."""
    did = path_id(request)
    vid = path_id(request, "vid")
    data = parse_body(request, VersionUpdate)
    with tx(request) as conn:
        scoped(conn, request, "documents", did, "Fiche")
        current = _version(conn, did, vid)
        label = data.label.strip()
        kind = "manual" if label and current["kind"] == "auto" else current["kind"]
        conn.execute("UPDATE document_versions SET label = ?, kind = ? WHERE id = ?", (label, kind, vid))
        row = dict(conn.execute(f"SELECT {FIELDS} FROM document_versions WHERE id = ?", (vid,)).fetchone())
    return JSONResponse(row)


@threaded
def delete_version(request: Request) -> Response:
    did = path_id(request)
    vid = path_id(request, "vid")
    with tx(request) as conn:
        scoped(conn, request, "documents", did, "Fiche")
        _version(conn, did, vid)
        conn.execute("DELETE FROM document_versions WHERE id = ?", (vid,))
    return Response(status_code=204)


async def restore_version(request: Request) -> Response:
    """Remet le contenu d'une version (le titre actuel est conservé). L'état actuel est d'abord
    enregistré comme version, pour pouvoir revenir en arrière."""
    did = path_id(request)
    vid = path_id(request, "vid")
    data = await parse(request, VersionRestore) if await request.body() else VersionRestore()
    user = auth.current_user(request)
    name = user["display_name"] or user["username"]

    def _work(conn: sqlite3.Connection):
        doc = scoped(conn, request, "documents", did, "Fiche")
        version = _version(conn, did, vid)
        # Contenu enregistré, puis état de l'éditeur qui restaure s'il diffère (l'un peut avoir des
        # modifications d'un autre MJ que l'autre n'a pas encore).
        snapshot(conn, did, label="Avant restauration")
        if data.current is not None:
            snapshot(conn, did, label="Avant restauration (navigateur)", html=data.current)
        content = drop_missing_images(conn, doc["campaign_id"], version["content"])
        replace_content(conn, did, content, user["id"])
        # Repère dans l'historique ; le nom d'une version manuelle restaurée est repris.
        insert_version(conn, did, kind="restore", html=content, title=doc["title"], author=name,
                       label=version["label"] if version["kind"] == "manual" else "")
        row = db.row_to_dict(conn.execute("SELECT * FROM documents WHERE id = ?", (did,)).fetchone())
        return row
    row = await in_tx(request, _work)
    hub = getattr(request.app.state, "hub", None)
    if hub is not None:
        hub.close_documents([did], "reset")
    for key in ("updated_by", "content_seq", "live_epoch"):
        row.pop(key, None)
    return JSONResponse(row)


routes = [
    Route("/api/documents/{id:int}/versions", list_versions),
    Route("/api/documents/{id:int}/versions", create_version, methods=["POST"]),
    Route("/api/documents/{id:int}/versions/{vid:int}", get_version),
    Route("/api/documents/{id:int}/versions/{vid:int}", update_version, methods=["PATCH"]),
    Route("/api/documents/{id:int}/versions/{vid:int}", delete_version, methods=["DELETE"]),
    Route("/api/documents/{id:int}/versions/{vid:int}/restore", restore_version, methods=["POST"]),
]
