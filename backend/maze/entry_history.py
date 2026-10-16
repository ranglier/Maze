"""Historique des versions et commentaires des fiches PNJ, factions et lieux (migration 022).

Ces fiches s'éditent par formulaire (un enregistrement = un PATCH), pas en temps réel : une version
est l'état des champs de texte après un enregistrement. Les enregistrements rapprochés d'un même auteur
(moins de MERGE_INTERVAL) se fondent dans la même version automatique ; avant la toute première, l'état
d'origine de la fiche est gardé (« État initial »). La version la plus récente correspond donc en principe à
l'état actuel. Ni images, ni liens, ni appartenances, ni positions : seulement le texte de la fiche.

Commentaires : fils rattachés à une section (field) et à un extrait (quote) de la fiche, sans marque dans
le texte (le contenu de la fiche n'est jamais modifié par un commentaire). L'interface retrouve l'extrait
dans la section affichée ; s'il a changé, le fil reste rattaché à la section.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, db, entries
from .api import TABLES, parse_body, path_id, scoped, threaded, touch_campaign, tx
from .versions import labels_out
from .schemas import CommentUpdate, EntryCommentCreate, EntryVersionCreate, NpcStatus, VersionUpdate

MERGE_INTERVAL = timedelta(minutes=10)
KEEP_AUTO = 50           # versions automatiques conservées par fiche
WHAT = {"npc": "PNJ", "faction": "Faction", "place": "Lieu"}
URL = {"npc": "npcs", "faction": "factions", "place": "places"}

# Champs suivis par l'historique (texte de la fiche), dans l'ordre du registre (entries.py, audit M23) ; la révision
# (déclencheurs trg_*_revision) suit les mêmes champs (tests.test_registry).
FIELDS: dict[str, tuple[str, ...]] = {kind: entries.keys(kind, history=True) for kind in entries.KINDS}
# Sections qu'un commentaire peut viser (vide : la fiche entière).
COMMENT_FIELDS: dict[str, frozenset[str]] = {kind: frozenset(entries.keys(kind, comment=True))
                                             for kind in entries.KINDS}

VERSION_FIELDS = "id, kind, label, name, size, digest, author_name, created_at"
COMMENT_COLUMNS = ("id, parent_id, field, user_id, author_name, body, quote, resolved, resolved_by, resolved_at, "
                   "created_at, updated_at")


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def author_of(user: dict[str, Any]) -> str:
    return user.get("display_name") or user.get("username") or ""


def state_of(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    """Champs suivis d'une fiche (ligne lue par db.row_to_dict : étiquettes en liste)."""
    out: dict[str, Any] = {}
    for key in FIELDS[kind]:
        value = row.get(key)
        if key == "tags":
            out[key] = list(value or [])
        else:
            out[key] = value if value is not None else ""
    return out


def _encode(state: dict[str, Any]) -> str:
    return json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(state: dict[str, Any]) -> str:
    return hashlib.sha256(_encode(state).encode()).hexdigest()


def _has_text(kind: str, state: dict[str, Any]) -> bool:
    """Une fiche qui n'a que son nom n'a pas d'« État initial » qui vaille d'être gardé."""
    return any(str(state.get(k) or "").strip() for k in FIELDS[kind]
               if k not in ("name", "status", "color", "kind", "tags")) or bool(state.get("tags"))


def _col(kind: str) -> str:
    return f"{kind}_id"


def _latest(conn: sqlite3.Connection, kind: str, eid: int) -> sqlite3.Row | None:
    return conn.execute(f"SELECT id, kind, label, digest, user_id, author_name, created_at FROM entry_versions "
                        f"WHERE {_col(kind)} = ? ORDER BY id DESC LIMIT 1", (eid,)).fetchone()


def _insert(conn: sqlite3.Connection, kind: str, eid: int, state: dict[str, Any], *, vkind: str,
            label: str = "", user_id: int | None = None, author: str = "") -> int:
    raw = _encode(state).encode()
    vid = db.insert(conn, "entry_versions", {
        _col(kind): eid, "kind": vkind, "label": label, "name": state.get("name") or "",
        "content": zlib.compress(raw, 6), "size": len(raw), "digest": _digest(state),
        "user_id": user_id, "author_name": author,
    })
    if vkind in ("auto", "restore"):
        # Versions automatiques et repères de restauration sans nom : les KEEP_AUTO plus récents. Les versions
        # nommées et les repères (« État initial », « Avant restauration »…) sont conservés.
        conn.execute(
            f"""DELETE FROM entry_versions WHERE {_col(kind)} = ? AND kind IN ('auto', 'restore') AND label = ''
                   AND id NOT IN (SELECT id FROM entry_versions WHERE {_col(kind)} = ? AND kind IN ('auto', 'restore')
                                     AND label = '' ORDER BY id DESC LIMIT ?)""", (eid, eid, KEEP_AUTO))
    return vid


def record_change(conn: sqlite3.Connection, kind: str, eid: int, before: dict[str, Any], after: dict[str, Any],
                  user: dict[str, Any], merge: bool = False) -> None:
    """Après un enregistrement de la fiche : version automatique de l'état obtenu (fondue avec la
    précédente si elle est du même auteur et récente). Rien si le texte n'a pas changé (positions…).

    Fusion seulement si l'état de départ de celui qui enregistre est connu et correspond à la dernière version
    (audit M1) : `merge` est vrai quand la requête portait la révision de départ (base_revision) et qu'elle était
    à jour, `before` étant alors l'état d'où le formulaire est parti, et que l'enregistrement ne suit pas un
    conflit (new_version : la version précédente est celle de l'autre). Sinon (connecteur, scripts, onglet resté
    sur un état plus ancien), la version précédente n'est jamais écrasée : son texte resterait introuvable."""
    old, new = state_of(kind, before), state_of(kind, after)
    if old == new:
        return
    latest = _latest(conn, kind, eid)
    if latest is None and _has_text(kind, old):
        _insert(conn, kind, eid, old, vkind="auto", label="État initial")
        latest = _latest(conn, kind, eid)
    elif latest is not None and latest["digest"] != _digest(old):
        # Fiche changée hors de l'historique depuis la dernière version (type de lieu supprimé, version la plus
        # récente supprimée…) : l'état d'avant cet enregistrement est gardé.
        _insert(conn, kind, eid, old, vkind="auto", label="État précédent")
        latest = _latest(conn, kind, eid)
    author = author_of(user)
    digest = _digest(new)
    if latest is not None and latest["digest"] == digest:
        return
    if (merge and latest is not None and latest["kind"] == "auto" and not latest["label"]
            and latest["digest"] == _digest(old)
            and latest["user_id"] == user.get("id") and latest["author_name"] == author
            and _now() - _parse_time(latest["created_at"]) < MERGE_INTERVAL):
        raw = _encode(new).encode()
        conn.execute("""UPDATE entry_versions SET content = ?, size = ?, digest = ?, name = ?,
                               created_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?""",
                     (zlib.compress(raw, 6), len(raw), digest, new.get("name") or "", latest["id"]))
        return
    _insert(conn, kind, eid, new, vkind="auto", user_id=user.get("id"), author=author)


def _ensure_current(conn: sqlite3.Connection, kind: str, eid: int, current: dict[str, Any], label: str) -> None:
    """Garde l'état actuel s'il n'est pas déjà la dernière version (fiche modifiée hors de l'historique :
    import, ancienne fiche jamais enregistrée depuis)."""
    state = state_of(kind, current)
    latest = _latest(conn, kind, eid)
    if latest is not None and latest["digest"] == _digest(state):
        return
    if latest is None and not _has_text(kind, state):
        return
    _insert(conn, kind, eid, state, vkind="auto", label=label)


def _entry(conn: sqlite3.Connection, request: Request, kind: str) -> dict[str, Any]:
    return scoped(conn, request, TABLES[kind], path_id(request), WHAT[kind])


def _version(conn: sqlite3.Connection, kind: str, eid: int, vid: int) -> dict[str, Any]:
    row = conn.execute(f"SELECT {VERSION_FIELDS}, content FROM entry_versions WHERE id = ? AND {_col(kind)} = ?",
                       (vid, eid)).fetchone()
    if row is None:
        raise HTTPException(404, "Version introuvable")
    out = dict(row)
    out["content"] = json.loads(zlib.decompress(out["content"]).decode())
    return out


def _version_row(conn: sqlite3.Connection, vid: int) -> dict[str, Any]:
    return dict(conn.execute(f"SELECT {VERSION_FIELDS} FROM entry_versions WHERE id = ?", (vid,)).fetchone())


# --- Routes : versions ---------------------------------------------------------------------

def _list_versions(kind: str):
    @threaded
    def handler(request: Request) -> Response:
        with tx(request) as conn:
            entry = _entry(conn, request, kind)
            digest = _digest(state_of(kind, entry))
            rows = [dict(r) for r in conn.execute(
                f"SELECT {VERSION_FIELDS} FROM entry_versions WHERE {_col(kind)} = ? ORDER BY id DESC", (entry["id"],))]
        for r in rows:
            r["is_current"] = r.pop("digest") == digest
        return JSONResponse({"current": {"name": entry["name"], "updated_at": entry.get("updated_at")},
                             "versions": labels_out(request, rows)})
    return handler


def _get_version(kind: str):
    @threaded
    def handler(request: Request) -> Response:
        with tx(request) as conn:
            entry = _entry(conn, request, kind)
            out = _version(conn, kind, entry["id"], path_id(request, "vid"))
        out["is_current"] = out.pop("digest") == _digest(state_of(kind, entry))
        return JSONResponse(labels_out(request, [out])[0])
    return handler


def _create_version(kind: str):
    """Version nommée de l'état actuel (enregistré) de la fiche."""
    @threaded
    def handler(request: Request) -> Response:
        data = parse_body(request, EntryVersionCreate)
        user = auth.current_user(request)
        with tx(request) as conn:
            entry = _entry(conn, request, kind)
            vid = _insert(conn, kind, entry["id"], state_of(kind, entry), vkind="manual", label=data.label.strip(),
                          user_id=user["id"], author=author_of(user))
            row = _version_row(conn, vid)
        row.pop("digest")
        row["is_current"] = True
        return JSONResponse(row, status_code=201)
    return handler


def _update_version(kind: str):
    """Renommer une version ; une version automatique nommée devient manuelle (conservée)."""
    @threaded
    def handler(request: Request) -> Response:
        data = parse_body(request, VersionUpdate)
        with tx(request) as conn:
            entry = _entry(conn, request, kind)
            vid = path_id(request, "vid")
            current = _version(conn, kind, entry["id"], vid)
            label = data.label.strip()
            vkind = "manual" if label and current["kind"] == "auto" else current["kind"]
            conn.execute("UPDATE entry_versions SET label = ?, kind = ? WHERE id = ?", (label, vkind, vid))
            row = _version_row(conn, vid)
        row["is_current"] = row.pop("digest") == _digest(state_of(kind, entry))
        return JSONResponse(row)
    return handler


def _delete_version(kind: str):
    @threaded
    def handler(request: Request) -> Response:
        with tx(request) as conn:
            entry = _entry(conn, request, kind)
            vid = path_id(request, "vid")
            _version(conn, kind, entry["id"], vid)
            conn.execute("DELETE FROM entry_versions WHERE id = ?", (vid,))
        return Response(status_code=204)
    return handler


def _restorable(conn: sqlite3.Connection, kind: str, entry: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    """Valeurs à réécrire : champs suivis présents dans la version, sous réserve qu'ils restent valides
    (un type de lieu supprimé entre-temps est laissé tel quel)."""
    values: dict[str, Any] = {}
    for key in FIELDS[kind]:
        if key not in content:
            continue
        value = content[key]
        if key == "tags":
            value = [str(t) for t in value] if isinstance(value, list) else []
        elif not isinstance(value, str):
            continue
        if key == "name" and not value.strip():
            continue
        if kind == "place" and key == "kind":
            if not conn.execute("SELECT 1 FROM place_kinds WHERE campaign_id = ? AND key = ?",
                                (entry["campaign_id"], value)).fetchone():
                continue
        if kind == "npc" and key == "status" and value not in get_args(NpcStatus):
            continue
        values[key] = value
    return values


def _restore_version(kind: str):
    """Remet le texte d'une version. L'état actuel est d'abord gardé s'il n'est pas déjà dans l'historique."""
    @threaded
    def handler(request: Request) -> Response:
        user = auth.current_user(request)
        with tx(request) as conn:
            entry = _entry(conn, request, kind)
            eid = entry["id"]
            version = _version(conn, kind, eid, path_id(request, "vid"))
            _ensure_current(conn, kind, eid, entry, "Avant restauration")
            values = _restorable(conn, kind, entry, version["content"])
            db.update(conn, TABLES[kind], eid, values)
            touch_campaign(conn, entry["campaign_id"])
            after = db.row_to_dict(conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE id = ?", (eid,)).fetchone())
            _insert(conn, kind, eid, state_of(kind, after), vkind="restore",
                    label=version["label"] if version["kind"] == "manual" else "",
                    user_id=user["id"], author=author_of(user))
        return JSONResponse({"id": eid, "restored": sorted(values)})
    return handler


# --- Routes : commentaires ----------------------------------------------------------------

def _threads(conn: sqlite3.Connection, kind: str, eid: int) -> list[dict[str, Any]]:
    rows = [dict(r) for r in conn.execute(
        f"SELECT {COMMENT_COLUMNS} FROM entry_comments WHERE {_col(kind)} = ? ORDER BY id", (eid,))]
    threads: dict[int, dict[str, Any]] = {}
    for row in rows:
        row["resolved"] = bool(row["resolved"])
        if row["parent_id"] is None:
            threads[row["id"]] = {**row, "replies": []}
    for row in rows:
        if row["parent_id"] in threads:
            threads[row["parent_id"]]["replies"].append(row)
    return list(threads.values())


def _list_comments(kind: str):
    @threaded
    def handler(request: Request) -> Response:
        with tx(request) as conn:
            entry = _entry(conn, request, kind)
            return JSONResponse(_threads(conn, kind, entry["id"]))
    return handler


def _create_comment(kind: str):
    @threaded
    def handler(request: Request) -> Response:
        data = parse_body(request, EntryCommentCreate)
        user = auth.current_user(request)
        with tx(request) as conn:
            entry = _entry(conn, request, kind)
            eid = entry["id"]
            field = data.field.strip()
            if data.parent_id is not None:
                parent = conn.execute(f"SELECT {_col(kind)} AS eid, parent_id FROM entry_comments WHERE id = ?",
                                      (data.parent_id,)).fetchone()
                if parent is None or parent["eid"] != eid or parent["parent_id"] is not None:
                    raise HTTPException(400, "Fil de commentaires introuvable")
                conn.execute("UPDATE entry_comments SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                             "WHERE id = ?", (data.parent_id,))
                field, quote = "", ""
            else:
                if field and field not in COMMENT_FIELDS[kind]:
                    raise HTTPException(400, "Section inconnue pour cette fiche")
                quote = data.quote.strip()
            new_id = db.insert(conn, "entry_comments", {
                "campaign_id": entry["campaign_id"], _col(kind): eid, "parent_id": data.parent_id,
                "field": field, "user_id": user["id"], "author_name": author_of(user),
                "body": data.body.strip(), "quote": quote,
            })
            row = dict(conn.execute(f"SELECT {COMMENT_COLUMNS} FROM entry_comments WHERE id = ?", (new_id,)).fetchone())
        row["resolved"] = bool(row["resolved"])
        return JSONResponse(row, status_code=201)
    return handler


def _comment(conn: sqlite3.Connection, request: Request, cid: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM entry_comments WHERE id = ?", (cid,)).fetchone()
    if row is None or not auth.can_access(conn, auth.current_user(request), row["campaign_id"]):
        raise HTTPException(404, "Commentaire introuvable")
    return dict(row)


def _can_edit(user: dict[str, Any], row: dict[str, Any]) -> bool:
    return user["role"] == "admin" or row["user_id"] == user["id"]


@threaded
def update_comment(request: Request) -> Response:
    cid = path_id(request)
    data = parse_body(request, CommentUpdate)
    user = auth.current_user(request)
    with tx(request) as conn:
        row = _comment(conn, request, cid)
        if data.body is not None:
            if not _can_edit(user, row):
                raise HTTPException(403, "Seul l'auteur peut modifier ce commentaire")
            conn.execute("UPDATE entry_comments SET body = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                         "WHERE id = ?", (data.body.strip(), cid))
        if data.resolved is not None:
            if row["parent_id"] is not None:
                raise HTTPException(400, "Seul un fil peut être résolu")
            conn.execute(
                "UPDATE entry_comments SET resolved = ?, resolved_by = ?, "
                "resolved_at = CASE WHEN ? THEN strftime('%Y-%m-%dT%H:%M:%SZ', 'now') END WHERE id = ?",
                (int(data.resolved), author_of(user) if data.resolved else "", int(data.resolved), cid))
    return JSONResponse({"id": cid})


@threaded
def delete_comment(request: Request) -> Response:
    cid = path_id(request)
    user = auth.current_user(request)
    with tx(request) as conn:
        row = _comment(conn, request, cid)
        if not _can_edit(user, row):
            raise HTTPException(403, "Seul l'auteur peut supprimer ce commentaire")
        conn.execute("DELETE FROM entry_comments WHERE id = ?", (cid,))  # les réponses suivent (cascade)
    return Response(status_code=204)


routes = [Route("/api/entry-comments/{id:int}", update_comment, methods=["PATCH"]),
          Route("/api/entry-comments/{id:int}", delete_comment, methods=["DELETE"])]
for _kind, _url in URL.items():
    routes += [
        Route(f"/api/{_url}/{{id:int}}/versions", _list_versions(_kind)),
        Route(f"/api/{_url}/{{id:int}}/versions", _create_version(_kind), methods=["POST"]),
        Route(f"/api/{_url}/{{id:int}}/versions/{{vid:int}}", _get_version(_kind)),
        Route(f"/api/{_url}/{{id:int}}/versions/{{vid:int}}", _update_version(_kind), methods=["PATCH"]),
        Route(f"/api/{_url}/{{id:int}}/versions/{{vid:int}}", _delete_version(_kind), methods=["DELETE"]),
        Route(f"/api/{_url}/{{id:int}}/versions/{{vid:int}}/restore", _restore_version(_kind), methods=["POST"]),
        Route(f"/api/{_url}/{{id:int}}/comments", _list_comments(_kind)),
        Route(f"/api/{_url}/{{id:int}}/comments", _create_comment(_kind), methods=["POST"]),
    ]
