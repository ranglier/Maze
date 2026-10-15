"""Fiches rédigées : fiche de campagne, scénarios et sous-pages, avec @mentions."""

from __future__ import annotations

import re
import sqlite3
import zlib
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, db, imaging, scenemap, versions
from .api import in_tx, parse_body, path_id, scoped, settings, threaded, touch_campaign, tx
from .schemas import DocumentCreate, DocumentMove, DocumentOrder, DocumentUpdate

# Lien de mention produit par l'éditeur : <a class="mention" data-mention="npc:12">…</a> ; « doc:12 » renvoie à une fiche.
MENTION = re.compile(r'data-mention="(npc|faction|place|doc):(\d+)"')
DOC_IMAGE = re.compile(r'data-doc-image="(\d+)"')
COMMENT_ANCHOR = re.compile(r'\sdata-comment="\d+"')
TREE_FIELDS = "id, parent_id, kind, title, status, sort_order, updated_at"
TITLES = {"campaign": "Fiche de campagne", "scenario": "Nouveau scénario", "page": "Nouvelle page"}
# Profondeur maximale de l'arbre des fiches (audit DATA-10) : nombre de fiches au-dessus d'une fiche (scénario : 1),
# 32 au plus. Une chaîne de milliers de niveaux rendait la campagne impossible à supprimer (récursion des
# déclencheurs de SQLite). Création, déplacement et import la respectent ; un arbre déjà plus profond reste
# utilisable, et la suppression ne dépend plus de la profondeur (delete_tree).
MAX_DEPTH = 32
TOO_DEEP = f"Trop de niveaux de sous-pages ({MAX_DEPTH} au plus)"


def ensure_root(conn: sqlite3.Connection, campaign_id: int) -> int:
    row = conn.execute("SELECT id FROM documents WHERE campaign_id = ? AND kind = 'campaign'",
                       (campaign_id,)).fetchone()
    if row:
        return row[0]
    return db.insert(conn, "documents", {"campaign_id": campaign_id, "kind": "campaign", "title": ""})


def _ensure_root(request: Request, cid: int) -> None:
    """Fiche de campagne créée à la première consultation de l'arbre : écriture à part, seulement si elle manque,
    pour que la lecture de l'arbre reste une lecture en lecture seule (sans attendre les écritures, audit M15)."""
    with tx(request, write=False) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        if conn.execute("SELECT 1 FROM documents WHERE campaign_id = ? AND kind = 'campaign'", (cid,)).fetchone():
            return
    with tx(request, write=True) as conn:
        ensure_root(conn, cid)


def depth(conn: sqlite3.Connection, doc_id: int) -> int:
    """Nombre de fiches au-dessus de celle-ci (fiche de campagne : 0, scénario : 1)."""
    return conn.execute(
        """WITH RECURSIVE anc(id) AS (SELECT parent_id FROM documents WHERE id = ?
               UNION SELECT d.parent_id FROM documents d JOIN anc a ON d.id = a.id)
           SELECT COUNT(id) FROM anc""", (doc_id,)).fetchone()[0]


def tree_levels(conn: sqlite3.Connection, roots_sql: str, params: tuple) -> list[tuple[int, int]]:
    """(identifiant, niveau sous la racine) des fiches désignées par roots_sql (SELECT id …) et de leurs sous-pages."""
    return [(r[0], r[1]) for r in conn.execute(
        f"""WITH RECURSIVE tree(id, level) AS (SELECT id, 0 FROM documents WHERE id IN ({roots_sql}) UNION ALL
                SELECT d.id, t.level + 1 FROM documents d JOIN tree t ON d.parent_id = t.id)
            SELECT id, level FROM tree""", params)]


def delete_tree(conn: sqlite3.Connection, levels: list[tuple[int, int]]) -> None:
    """Supprime ces fiches des feuilles vers la racine (audit DATA-10) : chacune n'a plus de sous-page quand elle est
    supprimée, la cascade de parent_id n'enchaîne donc pas les déclencheurs (limite de récursion de SQLite)."""
    conn.executemany("DELETE FROM documents WHERE id = ?",
                     [(doc_id,) for doc_id, _ in sorted(levels, key=lambda r: r[1], reverse=True)])


def sync_mentions(conn: sqlite3.Connection, document_id: int, content: str) -> None:
    conn.execute("DELETE FROM document_mentions WHERE document_id = ?", (document_id,))
    targets = {(kind, int(tid)) for kind, tid in MENTION.findall(content)}
    # Seules comptent les fiches de la campagne (contenu repris d'une autre campagne, d'un modèle).
    found = conn.execute("SELECT campaign_id FROM documents WHERE id = ?", (document_id,)).fetchone()
    if found and targets:
        tables = {"npc": "npcs", "faction": "factions", "place": "places", "doc": "documents"}
        targets = {(kind, tid) for kind, tid in targets if kind in tables and conn.execute(
            f"SELECT 1 FROM {tables[kind]} WHERE id = ? AND campaign_id = ?", (tid, found[0])).fetchone()}
    conn.executemany(
        "INSERT INTO document_mentions (document_id, target_kind, target_id) VALUES (?, ?, ?)",
        [(document_id, kind, tid) for kind, tid in targets])


def link_images(conn: sqlite3.Connection, document_id: int, campaign_id: int, content: str) -> None:
    """Une image de la campagne sans fiche (sa fiche d'origine a été supprimée alors qu'elle était
    reprise ailleurs) est rattachée à la fiche qui l'affiche."""
    image_ids = [int(i) for i in DOC_IMAGE.findall(content)]
    if image_ids:
        marks = ",".join("?" * len(image_ids))
        conn.execute(f"UPDATE document_images SET document_id = ? "
                     f"WHERE campaign_id = ? AND document_id IS NULL AND id IN ({marks})",
                     [document_id, campaign_id, *image_ids])


def strip_comments(html: str) -> str:
    """Retire les ancres de commentaires (contenu copié : les fils restent sur la fiche d'origine)."""
    return COMMENT_ANCHOR.sub("", html)


UNSAVED_EDITS = ("Des modifications récentes de cette fiche ne sont pas encore enregistrées. "
                 "Elles le seront dès qu'elle sera ouverte dans Maze ; il faudra ensuite la relire.")


def has_unsaved_edits(conn: sqlite3.Connection, document_id: int) -> bool:
    """Modifications temps réel reçues mais absentes du HTML enregistré (rendu par le meneur avec un léger
    décalage, ou jamais si le dernier éditeur est parti trop tôt). Remplacer le contenu les effacerait."""
    row = conn.execute(
        """SELECT (SELECT MAX(id) FROM document_updates WHERE document_id = d.id) > d.content_seq
             FROM documents d WHERE d.id = ?""", (document_id,)).fetchone()
    return bool(row and row[0])


def hub(request: Request):
    """Salles d'édition en temps réel (voir collab.py), absentes dans certains contextes de test."""
    return getattr(request.app.state, "hub", None)


def _doc_out(row: dict[str, Any]) -> dict[str, Any]:
    row.pop("updated_by", None)
    row.pop("content_seq", None)
    row.pop("live_epoch", None)
    return row


@threaded
def list_documents(request: Request) -> Response:
    cid = path_id(request)
    _ensure_root(request, cid)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        root_id = ensure_root(conn, cid)
        rows = db.rows_to_dicts(conn.execute(
            f"SELECT {TREE_FIELDS} FROM documents WHERE campaign_id = ? ORDER BY sort_order, id", (cid,)).fetchall())
    return JSONResponse({"root_id": root_id, "documents": rows})


def mentions_payload(conn: sqlite3.Connection, cid: int, root_id: int) -> dict[str, Any]:
    """Arbre des fiches, mentions et réglages des cartes de scénario : ce dont l'interface tire la distribution
    de chaque scénario (castOf, frontend/src/cast.js ; même règle que scenario_maps_of et foundry.scenarios_of,
    comparées par tests.test_cast_rules)."""
    docs = db.rows_to_dicts(conn.execute(
        f"SELECT {TREE_FIELDS} FROM documents WHERE campaign_id = ? ORDER BY sort_order, id", (cid,)).fetchall())
    mentions: dict[int, list[list[Any]]] = {}
    for row in conn.execute(
            """SELECT m.document_id, m.target_kind, m.target_id FROM document_mentions m
                 JOIN documents d ON d.id = m.document_id WHERE d.campaign_id = ?""", (cid,)):
        mentions.setdefault(row[0], []).append([row[1], row[2]])
    return {"root_id": root_id, "documents": docs, "mentions": mentions, "overrides": scenemap.overrides(conn, cid)}


@threaded
def document_mentions(request: Request) -> Response:
    """Fiches de la campagne et fiches PNJ / factions citées dans chacune (filtre de la carte)."""
    cid = path_id(request)
    _ensure_root(request, cid)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        payload = mentions_payload(conn, cid, ensure_root(conn, cid))
    return JSONResponse(payload)


@threaded
def create_document(request: Request) -> Response:
    cid = path_id(request)
    data = parse_body(request, DocumentCreate)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        root_id = ensure_root(conn, cid)
        parent_id = data.parent_id or root_id
        parent = scoped(conn, request, "documents", parent_id, "Fiche parente")
        if parent["campaign_id"] != cid:
            raise HTTPException(400, "La fiche parente n'est pas dans cette campagne")
        if depth(conn, parent_id) + 1 > MAX_DEPTH:
            raise HTTPException(400, TOO_DEEP)
        kind = "scenario" if parent["kind"] == "campaign" else "page"
        order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM documents WHERE parent_id = ?",
                             (parent_id,)).fetchone()[0]
        content = strip_comments(data.content)
        new_id = db.insert(conn, "documents", {
            "campaign_id": cid, "parent_id": parent_id, "kind": kind,
            "title": data.title.strip() or TITLES[kind], "sort_order": order, "content": content,
            "updated_by": auth.current_user(request)["id"],
        })
        if content:
            sync_mentions(conn, new_id, content)
        touch_campaign(conn, cid)
        row = db.row_to_dict(conn.execute("SELECT * FROM documents WHERE id = ?", (new_id,)).fetchone())
    return JSONResponse(_doc_out(row), status_code=201)


@threaded
def get_document(request: Request) -> Response:
    with tx(request) as conn:
        row = scoped(conn, request, "documents", path_id(request), "Fiche")
    # Époque du contenu lu : si elle a changé à la connexion temps réel (restauration entre-temps),
    # le navigateur recharge la fiche au lieu de réécrire l'ancien contenu.
    epoch = row["live_epoch"]
    with tx(request) as conn:
        # Fiches qui renvoient à celle-ci (mentions « doc: »).
        linked = db.rows_to_dicts(conn.execute(
            """SELECT d.id, d.kind, d.title, d.status FROM document_mentions m JOIN documents d ON d.id = m.document_id
                WHERE m.target_kind = 'doc' AND m.target_id = ? AND d.id != ? ORDER BY d.sort_order, d.id""",
            (row["id"], row["id"])).fetchall())
    return JSONResponse({**_doc_out(row), "live_epoch": epoch, "linked_from": linked})


@threaded
def update_document(request: Request) -> Response:
    did = path_id(request)
    data = parse_body(request, DocumentUpdate)
    values = data.model_dump(exclude_unset=True, exclude={"base_revision"})
    with tx(request) as conn:
        current = scoped(conn, request, "documents", did, "Fiche")
        if data.base_revision is not None and data.base_revision != current["revision"]:
            raise HTTPException(409, "Cette fiche a été modifiée ailleurs entre-temps. "
                                     "Rechargez-la pour récupérer la dernière version.")
        if current["kind"] == "campaign":
            values.pop("status", None)
        if "title" in values:
            values["title"] = values["title"].strip()
        if not values:
            return JSONResponse(_doc_out(current))
        user = auth.current_user(request)
        values["updated_by"] = user["id"]
        by_claude = user.get("mcp_scope") is not None  # connecteur Claude (mcp.py)
        if "content" in values and by_claude and has_unsaved_edits(conn, did):
            raise HTTPException(409, UNSAVED_EDITS)
        if "content" in values:
            versions.snapshot(conn, did, label="Avant modification par Claude" if by_claude else "Avant remplacement")
        db.update(conn, "documents", did, values)
        conn.execute("UPDATE documents SET revision = revision + 1 WHERE id = ?", (did,))
        if "content" in values:
            sync_mentions(conn, did, values["content"])
            link_images(conn, did, current["campaign_id"], values["content"])
            # Contenu remplacé d'un bloc (import, restauration) : le journal temps réel repart de ce HTML.
            conn.execute("DELETE FROM document_updates WHERE document_id = ?", (did,))
            conn.execute("UPDATE documents SET content_seq = 0, live_epoch = live_epoch + 1 WHERE id = ?", (did,))
            if by_claude:
                versions.snapshot(conn, did, label="Modification par Claude", author="Claude")
        touch_campaign(conn, current["campaign_id"])
        row = db.row_to_dict(conn.execute("SELECT * FROM documents WHERE id = ?", (did,)).fetchone())
    live = hub(request)
    if live is not None:
        if "content" in values:
            live.close_documents([did], "reset")
        else:
            live.notify_meta(did, {k: row[k] for k in ("title", "status", "updated_at")})
    return JSONResponse(_doc_out(row))


@threaded
def delete_document(request: Request) -> Response:
    did = path_id(request)
    with tx(request) as conn:
        current = scoped(conn, request, "documents", did, "Fiche")
        if current["kind"] == "campaign":
            raise HTTPException(400, "La fiche de campagne ne peut pas être supprimée")
        levels = tree_levels(conn, "?", (did,))
        tree_ids = [doc_id for doc_id, _ in levels]
        marks = ",".join("?" * len(tree_ids))
        images = conn.execute(f"SELECT id, file, thumb FROM document_images WHERE document_id IN ({marks})",
                              tree_ids).fetchall()
        delete_tree(conn, levels)
        # Une image reprise (copier-coller) dans une autre fiche de la campagne y reste affichée.
        files = []
        # Images encore présentes dans une version d'une autre fiche : gardées pour une restauration.
        in_versions: dict[int, int] = {}
        if images:
            for vdoc, blob in conn.execute(
                    """SELECT v.document_id, v.content FROM document_versions v
                         JOIN documents d ON d.id = v.document_id WHERE d.campaign_id = ?""",
                    (current["campaign_id"],)):
                for image_id in DOC_IMAGE.findall(zlib.decompress(blob).decode(errors="replace")):
                    in_versions.setdefault(int(image_id), vdoc)
        for img in images:
            user_doc = conn.execute(
                "SELECT id FROM documents WHERE campaign_id = ? AND content LIKE ? LIMIT 1",
                (current["campaign_id"], f'%data-doc-image="{img["id"]}"%')).fetchone()
            if not user_doc and img["id"] in in_versions:
                user_doc = (in_versions[img["id"]],)
            if user_doc:
                conn.execute("UPDATE document_images SET document_id = ? WHERE id = ?", (user_doc[0], img["id"]))
            else:
                conn.execute("DELETE FROM document_images WHERE id = ?", (img["id"],))
                files.append(img)
        touch_campaign(conn, current["campaign_id"])
    if hub(request) is not None:
        hub(request).close_documents(tree_ids, "deleted")
    for f in files:
        imaging.delete_files(settings(request).media_dir, f["file"], f["thumb"])
    return Response(status_code=204)


@threaded
def duplicate_document(request: Request) -> Response:
    """Copie d'une fiche et de ses sous-pages, placée juste après l'original, en brouillon.
    Les images sont partagées avec l'original ; commentaires et historique ne sont pas copiés."""
    did = path_id(request)
    user_id = auth.current_user(request)["id"]
    with tx(request) as conn:
        source = scoped(conn, request, "documents", did, "Fiche")
        if source["kind"] == "campaign":
            raise HTTPException(400, "La fiche de campagne ne peut pas être dupliquée")
        conn.execute("UPDATE documents SET sort_order = sort_order + 1 WHERE parent_id = ? AND sort_order > ?",
                     (source["parent_id"], source["sort_order"]))

        def copy(row: dict[str, Any], parent_id: int, title: str, sort_order: int) -> int:
            content = strip_comments(row["content"])
            new_id = db.insert(conn, "documents", {
                "campaign_id": row["campaign_id"], "parent_id": parent_id, "kind": row["kind"],
                "title": title, "status": "brouillon", "content": content, "sort_order": sort_order,
                "updated_by": user_id,
            })
            sync_mentions(conn, new_id, content)
            # La carte du scénario (sélection et disposition) suit la copie.
            conn.execute("""INSERT INTO scenario_nodes (document_id, entity_kind, entity_id, state, pos_x, pos_y)
                            SELECT ?, entity_kind, entity_id, state, pos_x, pos_y FROM scenario_nodes
                             WHERE document_id = ?""", (new_id, row["id"]))
            return new_id

        title = f"{source['title'] or 'Sans titre'} (copie)"[:200]
        new_id = copy(source, source["parent_id"], title, source["sort_order"] + 1)
        # Sous-pages copiées niveau par niveau, sans récursion (arbre très profond, audit DATA-10).
        pending = [(source["id"], new_id)]
        while pending:
            original, copied = pending.pop()
            for child in conn.execute("SELECT * FROM documents WHERE parent_id = ? ORDER BY sort_order, id",
                                      (original,)).fetchall():
                child = db.row_to_dict(child)
                pending.append((child["id"], copy(child, copied, child["title"], child["sort_order"])))
        touch_campaign(conn, source["campaign_id"])
        row = db.row_to_dict(conn.execute("SELECT * FROM documents WHERE id = ?", (new_id,)).fetchone())
    return JSONResponse(_doc_out(row), status_code=201)


@threaded
def reorder_documents(request: Request) -> Response:
    cid = path_id(request)
    data = parse_body(request, DocumentOrder)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        parent = scoped(conn, request, "documents", data.parent_id, "Fiche parente")
        if parent["campaign_id"] != cid:
            raise HTTPException(400, "La fiche parente n'est pas dans cette campagne")
        children = {r[0] for r in conn.execute("SELECT id FROM documents WHERE parent_id = ?", (data.parent_id,))}
        if set(data.ids) != children:
            raise HTTPException(400, "La liste doit contenir exactement les sous-fiches de cette fiche")
        conn.executemany("UPDATE documents SET sort_order = ? WHERE id = ?",
                         [(i, doc_id) for i, doc_id in enumerate(data.ids)])
    return Response(status_code=204)


@threaded
def move_document(request: Request) -> Response:
    """Déplace une fiche (et ses sous-pages) : rang parmi ses sœurs, ou sous une autre fiche de la campagne.
    Sous la fiche de campagne, elle devient un scénario ; ailleurs, une page."""
    did = path_id(request)
    data = parse_body(request, DocumentMove)
    with tx(request) as conn:
        doc = scoped(conn, request, "documents", did, "Fiche")
        if doc["kind"] == "campaign":
            raise HTTPException(400, "La fiche de campagne ne se déplace pas")
        parent = scoped(conn, request, "documents", data.parent_id, "Fiche parente")
        if parent["campaign_id"] != doc["campaign_id"]:
            raise HTTPException(400, "La fiche parente n'est pas dans cette campagne")
        # Pas sous elle-même ni sous une de ses sous-pages.
        ancestor, seen = parent, set()
        while ancestor is not None and ancestor["id"] not in seen:
            if ancestor["id"] == did:
                raise HTTPException(400, "Une fiche ne peut pas être rangée dans ses propres sous-pages")
            seen.add(ancestor["id"])
            ancestor = conn.execute("SELECT id, parent_id FROM documents WHERE id = ?",
                                    (ancestor["parent_id"],)).fetchone() if ancestor["parent_id"] else None
        # Profondeur (audit DATA-10) : refusée au-delà de MAX_DEPTH, sauf déplacement qui n'enfonce pas l'arbre.
        below = max(level for _, level in tree_levels(conn, "?", (did,)))
        deepest = depth(conn, data.parent_id) + 1 + below
        if deepest > MAX_DEPTH and deepest > depth(conn, did) + below:
            raise HTTPException(400, TOO_DEEP)
        siblings = [r[0] for r in conn.execute(
            "SELECT id FROM documents WHERE parent_id = ? AND id != ? ORDER BY sort_order, id", (data.parent_id, did))]
        siblings.insert(min(data.index, len(siblings)), did)
        kind = "scenario" if parent["kind"] == "campaign" else "page"
        conn.execute("UPDATE documents SET parent_id = ?, kind = ? WHERE id = ?", (data.parent_id, kind, did))
        conn.executemany("UPDATE documents SET sort_order = ? WHERE id = ?", [(i, x) for i, x in enumerate(siblings)])
        if doc["parent_id"] != data.parent_id:
            rest = [r[0] for r in conn.execute(
                "SELECT id FROM documents WHERE parent_id = ? ORDER BY sort_order, id", (doc["parent_id"],))]
            conn.executemany("UPDATE documents SET sort_order = ? WHERE id = ?", [(i, x) for i, x in enumerate(rest)])
    return Response(status_code=204)


async def upload_document_image(request: Request) -> Response:
    cfg = settings(request)
    did = path_id(request)
    limit = cfg.max_upload_mb * 1024 * 1024

    def _work(conn: sqlite3.Connection):
        doc = scoped(conn, request, "documents", did, "Fiche")
        return doc
    doc = await in_tx(request, _work)
    form = await request.form(max_files=1, max_fields=2)
    upload = form.get("file")
    try:
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(400, "Aucun fichier reçu (champ 'file')")
        raw = await upload.read(limit + 1)
        if len(raw) > limit:
            raise HTTPException(413, f"Image trop volumineuse (max {cfg.max_upload_mb} Mo)")
        try:
            img = await run_in_threadpool(imaging.process_upload, raw, cfg.media_dir, doc["campaign_id"])
        except imaging.ImageError as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        await form.close()

    def _work(conn: sqlite3.Connection):
        new_id = db.insert(conn, "document_images", {
            "campaign_id": doc["campaign_id"], "document_id": did, "file": img.file, "thumb": img.thumb,
            "width": img.width, "height": img.height,
        })
        return new_id
    new_id = await in_tx(request, _work)
    return JSONResponse({"id": new_id, "url": f"/media/{img.file}", "width": img.width, "height": img.height},
                        status_code=201)


routes = [
    Route("/api/campaigns/{id:int}/documents", list_documents),
    Route("/api/campaigns/{id:int}/documents", create_document, methods=["POST"]),
    Route("/api/campaigns/{id:int}/documents/order", reorder_documents, methods=["PUT"]),
    Route("/api/documents/{id:int}/move", move_document, methods=["PUT"]),
    Route("/api/campaigns/{id:int}/documents/mentions", document_mentions),
    Route("/api/documents/{id:int}", get_document),
    Route("/api/documents/{id:int}", update_document, methods=["PATCH"]),
    Route("/api/documents/{id:int}", delete_document, methods=["DELETE"]),
    Route("/api/documents/{id:int}/images", upload_document_image, methods=["POST"]),
    Route("/api/documents/{id:int}/duplicate", duplicate_document, methods=["POST"]),
]
