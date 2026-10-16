"""Routes de l'API REST."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, db, defaults, i18n, imaging, limits, throttle
from .config import LICENSE, Settings
from .i18n import msg, msgn
from .schemas import (
    CampaignCreate, CampaignUpdate, EntityOrder, FactionCreate, FactionUpdate, ImageUpdate, LocaleChange, Login,
    PasswordChange, Setup,
    MembershipCreate, MembershipUpdate, NpcCreate, NpcUpdate, PositionsUpdate,
    RelationCreate, RelationTypeCreate, RelationTypeUpdate, RelationUpdate,
)

M = TypeVar("M", bound=BaseModel)
TABLES = {"npc": "npcs", "faction": "factions", "place": "places"}
IMAGE_FIELD = {"npc": "portrait_image_id", "faction": "emblem_image_id", "place": "image_id"}


class RequestValidationError(Exception):
    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors


# --- Utilitaires -----------------------------------------------------------

def settings(request: Request) -> Settings:
    return request.app.state.settings


READ_METHODS = ("GET", "HEAD", "OPTIONS")


def tx(request: Request, write: bool | None = None):
    """Transaction sur la base de l'instance. Par défaut, lecture seule (instantané, sans attendre les écritures)
    pour GET et HEAD, BEGIN IMMEDIATE sinon (audit M15) ; write=True pour une route de lecture qui écrit."""
    if write is None:
        write = request.method not in READ_METHODS
    return db.transaction(settings(request).db_path, write=write)


def threaded(handler: Callable[[Request], Response]) -> Callable[[Request], Awaitable[Response]]:
    """Route exécutée dans un fil de travail réservé à SQLite (db.run_db, audit M15 = PERF-1) : le corps de la
    requête est lu d'abord, dans la boucle (limites de taille comprises), puis tout le traitement (décodage du JSON,
    transactions, réponse) se fait hors de la boucle. Le gestionnaire est une fonction ordinaire : parse_body au
    lieu de parse, appels directs au lieu de run_in_threadpool ; le temps réel (collab.Hub) revient de lui-même
    dans la boucle."""
    @functools.wraps(handler)
    async def endpoint(request: Request) -> Response:
        write = request.method not in READ_METHODS
        if write:
            request.state.body = await request.body()
        # Fils des lectures pour GET et HEAD (mêmes règles que tx) ; les rares lectures qui écrivent un peu (fiche de
        # campagne créée à la première consultation, date d'usage d'un jeton) le font dans une transaction courte.
        return await db.run_db(handler, request, write=write)
    return endpoint


async def in_tx(request: Request, work: Callable[[sqlite3.Connection], Any], write: bool | None = None) -> Any:
    """work(conn) dans une transaction (tx), dans un fil de travail (db.run_db) : pour les routes qui restent
    dans la boucle (envoi de fichiers, limites d'essais de connexion, corps lu avant la base)."""
    if write is None:
        write = request.method not in READ_METHODS

    def run() -> Any:
        with tx(request, write) as conn:
            return work(conn)
    return await db.run_db(run, write=write)


def _validate(model: type[M], payload: Any) -> M:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        # Sans la valeur reçue (input) : un champ de plusieurs Mo n'est pas renvoyé tel quel (audit M19).
        raise RequestValidationError(exc.errors(include_url=False, include_context=False, include_input=False)) from exc


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} n'est pas du JSON valide")


def loads(body: bytes | str) -> Any:
    """JSON d'un corps de requête ; 400 s'il est invalide (audit PERF-14) : y compris imbriqué au-delà de la limite de
    récursion de Python (RecursionError, auparavant une 500) et NaN, Infinity (hors norme JSON)."""
    try:
        return json.loads(body, parse_constant=_no_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(400, "Corps de requête JSON invalide") from None


async def parse(request: Request, model: type[M]) -> M:
    return _validate(model, loads(await request.body()))


def parse_body(request: Request, model: type[M]) -> M:
    """parse pour une route threaded : corps déjà lu par threaded, décodé dans le fil de travail."""
    return _validate(model, loads(request.state.body))


def path_id(request: Request, name: str = "id") -> int:
    return int(request.path_params[name])


def get_or_404(conn: sqlite3.Connection, table: str, row_id: int, what: str) -> dict[str, Any]:
    row = db.row_to_dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone())
    if row is None:
        # Message traduit à la réponse (maze.i18n) : modèle « {what} introuvable », nom de la sorte de fiche traduit
        # lui aussi (« Campagne », « PNJ »… dans le catalogue).
        raise HTTPException(404, msg("{what} introuvable", what=msg(what)))
    return row


def campaign_of(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> int | None:
    if table == "campaigns":
        return row["id"]
    if table == "memberships":
        # Campagne de la faction (toujours renseignée), que le membre soit un PNJ ou une sous-faction.
        found = conn.execute("SELECT campaign_id FROM factions WHERE id = ?", (row["faction_id"],)).fetchone()
        return found[0] if found else None
    if table == "place_links":
        found = conn.execute("SELECT campaign_id FROM places WHERE id = ?", (row["place_id"],)).fetchone()
        return found[0] if found else None
    return row.get("campaign_id")


def scoped(conn: sqlite3.Connection, request: Request, table: str, row_id: int, what: str) -> dict[str, Any]:
    """Comme get_or_404, mais vérifie aussi que l'utilisateur a accès à la campagne concernée.
    Une ressource inaccessible est signalée comme introuvable, pour ne pas révéler son existence."""
    row = get_or_404(conn, table, row_id, what)
    if not auth.can_access(conn, auth.current_user(request), campaign_of(conn, table, row)):
        raise HTTPException(404, msg("{what} introuvable", what=msg(what)))
    return row


def image_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "owner_kind": row["owner_kind"],
        "owner_id": row["owner_id"],
        "url": f"/media/{row['file']}",
        "thumb_url": f"/media/{row['thumb']}",
        "caption": row["caption"],
        "width": row["width"],
        "height": row["height"],
        "sort_order": row["sort_order"],
    }


def thumb_url(path: str | None) -> str | None:
    return f"/media/{path}" if path else None


def check_image_owner(conn: sqlite3.Connection, image_id: int | None, kind: str, owner_id: int) -> None:
    if image_id is None:
        return
    row = conn.execute(
        "SELECT 1 FROM images WHERE id = ? AND owner_kind = ? AND owner_id = ?",
        (image_id, kind, owner_id),
    ).fetchone()
    if row is None:
        raise HTTPException(400, "Cette image n'appartient pas à cette fiche")


def check_entity(conn: sqlite3.Connection, kind: str, entity_id: int, campaign_id: int) -> None:
    row = conn.execute(
        f"SELECT 1 FROM {TABLES[kind]} WHERE id = ? AND campaign_id = ?",
        (entity_id, campaign_id),
    ).fetchone()
    if row is None:
        raise HTTPException(400, "L'entité liée n'existe pas dans cette campagne")


# --- Santé et authentification --------------------------------------------

async def health(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


def _open_session(request: Request, user: dict[str, Any]) -> None:
    request.session.clear()
    request.session["uid"] = user["id"]
    request.session["epoch"] = user["session_epoch"]
    request.session["key"] = user["session_key"]


def instance_id(request: Request) -> str:
    """Identifiant stable de cette instance de Maze (dérivé de sa clé secrète, sans la révéler) : le module Foundry
    ne confond pas les fiches de deux instances importées dans un même monde, et l'interface range par instance et
    par compte le texte non fusionné qu'elle garde dans le navigateur (audit M4)."""
    return hashlib.sha256(f"maze-instance:{settings(request).secret_key}".encode()).hexdigest()[:12]


@threaded
def auth_status(request: Request) -> Response:
    cfg = settings(request)
    user = auth.load_session_user(cfg.db_path, request.session)
    return JSONResponse({
        "authenticated": user is not None,
        "setup_required": auth.count_users(cfg.db_path) == 0,
        "user": auth.public_user(user) if user else None,   # langue du compte : user.locale (vide = automatique)
        "instance": instance_id(request) if user else None,
        # Langue par défaut de l'instance (MAZE_DEFAULT_LOCALE), après celles du compte et du navigateur.
        "default_locale": cfg.default_locale,
        "version": cfg.version,
        # Public, même sans session (écran de connexion) : code source et licence.
        "source_url": cfg.source_url,
        "license": LICENSE,
    })


async def login(request: Request) -> Response:
    data = await parse(request, Login)
    client = request.client.host if request.client else "?"
    # Limite par adresse et délai croissant par compte (throttle) ; essai compté avant toute attente.
    refused = await throttle.admit(client, data.username)
    if refused:
        raise HTTPException(429, refused.message, headers=refused.headers)
    user = await in_tx(request, lambda conn: db.row_to_dict(conn.execute(
        "SELECT * FROM users WHERE username = ? AND active = 1", (data.username.strip(),)).fetchone()), write=False)
    if user is None:
        await run_in_threadpool(auth.verify_dummy, data.password)
        ok = False
    else:
        ok = await run_in_threadpool(auth.verify_password, data.password, user["password_hash"])
    if not ok:
        await asyncio.sleep(1)
        raise HTTPException(401, "Identifiant ou mot de passe incorrect")
    throttle.succeeded(client, data.username)

    def logged_in(conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE users SET last_login_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?", (user["id"],))
        throttle.remember(conn, user["id"], client)   # adresse connue, gardée en base (vérification finale R1)
    await in_tx(request, logged_in)
    _open_session(request, user)
    return JSONResponse({"authenticated": True, "user": auth.public_user(user)})


async def setup(request: Request) -> Response:
    """Création du premier compte administrateur, possible uniquement sans aucun compte."""
    # Refus immédiat dès qu'un compte existe, avant de lire le corps et de calculer scrypt (audit M18).
    if await db.run_db(auth.count_users, settings(request).db_path, write=False):
        raise HTTPException(409, "Un compte existe déjà : connectez-vous.")
    data = await parse(request, Setup)
    password_hash = await run_in_threadpool(auth.hash_password, data.password)

    def create(conn: sqlite3.Connection) -> dict[str, Any]:
        # Revérifié dans la transaction (BEGIN IMMEDIATE) : deux premiers comptes simultanés, un seul créé.
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise HTTPException(409, "Un compte existe déjà : connectez-vous.")
        uid = db.insert(conn, "users", {
            "username": data.username, "display_name": data.display_name or data.username,
            "password_hash": password_hash, "role": "admin",
        })
        return get_or_404(conn, "users", uid, "Compte")
    user = await in_tx(request, create)
    _open_session(request, user)
    return JSONResponse({"authenticated": True, "user": auth.public_user(user)}, status_code=201)


async def logout(request: Request) -> Response:
    request.session.clear()
    return JSONResponse({"authenticated": False})


async def change_password(request: Request) -> Response:
    user = auth.current_user(request)
    data = await parse(request, PasswordChange)
    if not await run_in_threadpool(auth.verify_password, data.current_password, user["password_hash"]):
        await asyncio.sleep(1)
        raise HTTPException(400, "Mot de passe actuel incorrect")
    password_hash = await run_in_threadpool(auth.hash_password, data.new_password)

    def change(conn: sqlite3.Connection) -> dict[str, Any]:
        conn.execute("UPDATE users SET password_hash = ?, session_epoch = session_epoch + 1 WHERE id = ?",
                     (password_hash, user["id"]))
        return get_or_404(conn, "users", user["id"], "Compte")
    user = await in_tx(request, change)
    _open_session(request, user)  # les autres sessions ouvertes sont invalidées
    hub = getattr(request.app.state, "hub", None)
    if hub is not None:
        hub.recheck()  # connexions temps réel des autres sessions : coupées
    return JSONResponse({"changed": True})


@threaded
def change_locale(request: Request) -> Response:
    """Langue de l'interface du compte (menu du compte > Langue) : « fr », « en »… ou vide pour suivre le navigateur.
    Sert aussi aux messages d'erreur du serveur (maze.i18n.request_locale)."""
    user = auth.current_user(request)
    data = parse_body(request, LocaleChange)
    with tx(request) as conn:
        conn.execute("UPDATE users SET locale = ? WHERE id = ?", (data.locale, user["id"]))
    return JSONResponse({"locale": data.locale})


# --- Campagnes -------------------------------------------------------------

@threaded
def list_campaigns(request: Request) -> Response:
    user = auth.current_user(request)
    with tx(request) as conn:
        visible = auth.visible_campaign_ids(conn, user)  # connecteur Claude compris (mcp_scope, audit QUAL-6)
        where = "" if visible is None else f"WHERE c.id IN ({','.join('?' * len(visible))})"
        rows = conn.execute(
            f"""SELECT c.*,
                      (SELECT COUNT(*) FROM npcs n WHERE n.campaign_id = c.id) AS npc_count,
                      (SELECT COUNT(*) FROM factions f WHERE f.campaign_id = c.id) AS faction_count
                 FROM campaigns c {where} ORDER BY c.updated_at DESC, c.id DESC""",
            visible or [],
        ).fetchall()
    return JSONResponse(db.rows_to_dicts(rows))


@threaded
def create_campaign(request: Request) -> Response:
    data = parse_body(request, CampaignCreate)
    with tx(request) as conn:
        new_id = db.insert(conn, "campaigns", data.model_dump())
        # Natures de lien et types de lieux dans la langue de celui qui crée la campagne (compte, navigateur, puis
        # MAZE_DEFAULT_LOCALE) ; clés identiques dans toutes les langues.
        locale = i18n.request_locale(request.scope)
        defaults.seed_relation_types(conn, new_id, locale)
        defaults.seed_place_kinds(conn, new_id, locale)
        conn.execute("INSERT INTO campaign_members (campaign_id, user_id) VALUES (?, ?)",
                     (new_id, auth.current_user(request)["id"]))
        row = scoped(conn, request, "campaigns", new_id, "Campagne")
    return JSONResponse(row, status_code=201)


@threaded
def get_campaign(request: Request) -> Response:
    with tx(request) as conn:
        row = scoped(conn, request, "campaigns", path_id(request), "Campagne")
    return JSONResponse(row)


@threaded
def update_campaign(request: Request) -> Response:
    data = parse_body(request, CampaignUpdate)
    cid = path_id(request)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        # null ignoré (revue de la chronologie : {"timeline_enabled": null} désactivait le module).
        db.update(conn, "campaigns", cid, data.model_dump(exclude_unset=True, exclude_none=True))
        row = scoped(conn, request, "campaigns", cid, "Campagne")
    return JSONResponse(row)


@threaded
def delete_campaign(request: Request) -> Response:
    cid = path_id(request)

    def remove() -> tuple[list, list[int]]:
        # Cascade et déclencheurs de recherche : plusieurs centaines de ms pour une grosse campagne,
        # hors de la boucle d'événements pour ne pas figer les autres requêtes.
        with tx(request) as conn:
            scoped(conn, request, "campaigns", cid, "Campagne")
            files = conn.execute("SELECT file, thumb FROM images WHERE campaign_id = ? UNION ALL "
                                 "SELECT file, thumb FROM document_images WHERE campaign_id = ? UNION ALL "
                                 "SELECT file, thumb FROM library_images WHERE campaign_id = ? UNION ALL "
                                 "SELECT file, thumb FROM maps WHERE campaign_id = ? UNION ALL "
                                 "SELECT preview AS file, '' AS thumb FROM maps WHERE campaign_id = ? AND preview <> '' "
                                 "UNION ALL SELECT image AS file, '' AS thumb FROM place_kinds "
                                 "WHERE campaign_id = ? AND image <> ''",
                                 (cid, cid, cid, cid, cid, cid)).fetchall()
            doc_ids = [r[0] for r in conn.execute("SELECT id FROM documents WHERE campaign_id = ?", (cid,))]
            # Fiches rédigées supprimées des feuilles vers la racine : la cascade sur une chaîne de pages très
            # profonde dépassait la limite de récursion des déclencheurs (audit DATA-10).
            from .documents import delete_tree, tree_levels  # import circulaire évité
            delete_tree(conn, tree_levels(conn, "SELECT id FROM documents WHERE campaign_id = ? AND parent_id IS NULL",
                                          (cid,)))
            conn.execute("DELETE FROM campaigns WHERE id = ?", (cid,))
        return files, doc_ids

    files, doc_ids = remove()
    hub = getattr(request.app.state, "hub", None)
    if hub is not None:
        hub.close_documents(doc_ids, "deleted")
    media_dir = settings(request).media_dir
    for f in files:
        imaging.delete_files(media_dir, f["file"], f["thumb"])
    # Seulement s'il est vide (fichiers ajoutés pendant la suppression : laissés, sans effet sur une autre campagne).
    try:
        (media_dir / str(cid)).rmdir()
    except OSError:
        pass
    return Response(status_code=204)


def touch_campaign(conn: sqlite3.Connection, campaign_id: int) -> None:
    conn.execute(
        "UPDATE campaigns SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
        (campaign_id,),
    )


def _load_graph(request: Request, cid: int) -> dict[str, Any]:
    with tx(request, write=False) as conn:
        campaign = scoped(conn, request, "campaigns", cid, "Campagne")
        npcs = db.rows_to_dicts(conn.execute(
            """SELECT n.id, n.name, n.title, n.status, n.summary, n.tags, n.sort_order,
                      n.pos_x, n.pos_y, n.portrait_image_id, i.thumb AS portrait_thumb
                 FROM npcs n LEFT JOIN images i ON i.id = n.portrait_image_id
                WHERE n.campaign_id = ? ORDER BY n.name COLLATE NOCASE""", (cid,)).fetchall())
        factions = db.rows_to_dicts(conn.execute(
            """SELECT f.id, f.name, f.kind, f.color, f.summary, f.tags, f.sort_order, f.pos_x, f.pos_y,
                      f.emblem_image_id, i.thumb AS emblem_thumb
                 FROM factions f LEFT JOIN images i ON i.id = f.emblem_image_id
                WHERE f.campaign_id = ? ORDER BY f.name COLLATE NOCASE""", (cid,)).fetchall())
        memberships = db.rows_to_dicts(conn.execute(
            """SELECT m.* FROM memberships m JOIN factions f ON f.id = m.faction_id
                WHERE f.campaign_id = ? ORDER BY m.id""", (cid,)).fetchall())
        # Colonnes utiles à la carte seulement : les liens sont l'essentiel du volume.
        relations = db.rows_to_dicts(conn.execute(
            """SELECT id, source_kind, source_id, target_kind, target_id, type, label,
                      description, directed, secret FROM relations WHERE campaign_id = ?""", (cid,)).fetchall())
        relation_types = list_types(conn, cid)
    for n in npcs:
        n["portrait_thumb"] = thumb_url(n["portrait_thumb"])
    for f in factions:
        f["emblem_thumb"] = thumb_url(f["emblem_thumb"])
    return {
        "campaign": campaign, "npcs": npcs, "factions": factions,
        "memberships": memberships, "relations": relations, "relation_types": relation_types,
    }


@threaded
def campaign_graph(request: Request) -> Response:
    """Tout ce qu'il faut pour dessiner la carte d'une campagne, en une requête.
    Calculé dans le pool de threads : une grosse carte ne fige pas les autres requêtes."""
    cid = path_id(request)
    data = _load_graph(request, cid)
    return JSONResponse(data)


@threaded
def save_positions(request: Request) -> Response:
    cid = path_id(request)
    data = parse_body(request, PositionsUpdate)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        for kind in ("npc", "faction"):
            conn.executemany(
                f"UPDATE {TABLES[kind]} SET pos_x = ?, pos_y = ? WHERE id = ? AND campaign_id = ?",
                [(n.x, n.y, n.id, cid) for n in data.nodes if n.kind == kind],
            )
    return Response(status_code=204)


# --- PNJ et factions (logique commune) -------------------------------------

def scenario_maps_of(conn: sqlite3.Connection, kind: str, entity_id: int, campaign_id: int) -> list[dict[str, Any]]:
    """Scénarios dont la carte montre cette fiche : citée dans le scénario ou ses pages et non retirée,
    ou ajoutée à la main."""
    docs = {r[0]: (r[1], r[2], r[3], r[4], r[5]) for r in conn.execute(
        "SELECT id, parent_id, kind, title, status, sort_order FROM documents WHERE campaign_id = ?", (campaign_id,))}

    def scenario_of(did: int | None) -> int | None:
        seen = set()
        while did is not None and did in docs and did not in seen:
            seen.add(did)
            if docs[did][1] == "scenario":
                return did
            did = docs[did][0]
        return None

    found = {scenario_of(r[0]) for r in conn.execute(
        "SELECT document_id FROM document_mentions WHERE target_kind = ? AND target_id = ?", (kind, entity_id))}
    for did, state in conn.execute(
            "SELECT document_id, state FROM scenario_nodes WHERE entity_kind = ? AND entity_id = ? AND state != 'auto'",
            (kind, entity_id)):
        if docs.get(did, (None, None))[1] != "scenario":
            continue  # réglage d'un scénario devenu page : sans effet tant qu'il n'est pas redevenu scénario
        if state == "added":
            found.add(did)
        else:
            found.discard(did)
    found.discard(None)
    return [{"id": did, "title": docs[did][2], "status": docs[did][3]}
            for did in sorted((d for d in found if d in docs), key=lambda d: (docs[d][4], d))]


def entity_detail(conn: sqlite3.Connection, kind: str, entity_id: int) -> dict[str, Any]:
    if kind == "place":
        from .places import place_detail  # import différé (places importe ce module)
        return place_detail(conn, entity_id)
    row = get_or_404(conn, TABLES[kind], entity_id, "PNJ" if kind == "npc" else "Faction")
    images = db.rows_to_dicts(conn.execute(
        "SELECT * FROM images WHERE owner_kind = ? AND owner_id = ? ORDER BY sort_order, id",
        (kind, entity_id)).fetchall())
    row["images"] = [image_out(i) for i in images]
    # Fiches rédigées (scénarios…) qui mentionnent cette entité.
    row["mentioned_in"] = db.rows_to_dicts(conn.execute(
        """SELECT d.id, d.kind, d.title, d.status FROM document_mentions m
             JOIN documents d ON d.id = m.document_id
            WHERE m.target_kind = ? AND m.target_id = ? AND d.campaign_id = ?
            ORDER BY d.kind = 'campaign' DESC, d.sort_order, d.id""", (kind, entity_id, row["campaign_id"])).fetchall())
    row["maps"] = scenario_maps_of(conn, kind, entity_id, row["campaign_id"])
    from .timeline import backlinks  # import circulaire évité
    row["timeline"] = backlinks(conn, kind, entity_id, row["campaign_id"])   # événements qui la citent
    # Lieux où se trouve ce PNJ / cette faction.
    row["places"] = db.rows_to_dicts(conn.execute(
        """SELECT l.id AS link_id, l.role, p.id, p.name, p.kind, k.label AS kind_label, k.icon AS kind_icon,
                  k.image AS kind_image
             FROM place_links l JOIN places p ON p.id = l.place_id
             LEFT JOIN place_kinds k ON k.campaign_id = p.campaign_id AND k.key = p.kind
            WHERE l.entity_kind = ? AND l.entity_id = ? ORDER BY p.name COLLATE NOCASE""",
        (kind, entity_id)).fetchall())
    for place in row["places"]:
        place["kind_image"] = thumb_url(place["kind_image"])  # icône importée du type de lieu
    return row


def _create_entity(kind: str, model: type[BaseModel]):
    @threaded
    def handler(request: Request) -> Response:
        cid = path_id(request)
        data = parse_body(request, model)
        values = data.model_dump(exclude_unset=True)
        values.pop(IMAGE_FIELD[kind], None)  # aucune image ne peut exister avant la création
        values["campaign_id"] = cid
        with tx(request) as conn:
            scoped(conn, request, "campaigns", cid, "Campagne")
            # Rangée en dernier si le MJ a déjà ordonné la liste ; sinon ordre alphabétique (tout à 0).
            top = conn.execute(f"SELECT COALESCE(MAX(sort_order), 0) FROM {TABLES[kind]} WHERE campaign_id = ?",
                               (cid,)).fetchone()[0]
            values["sort_order"] = top + 1 if top > 0 else 0
            new_id = db.insert(conn, TABLES[kind], values)
            touch_campaign(conn, cid)
            row = entity_detail(conn, kind, new_id)
        return JSONResponse(row, status_code=201)
    return handler


def _get_entity(kind: str):
    @threaded
    def handler(request: Request) -> Response:
        with tx(request) as conn:
            scoped(conn, request, TABLES[kind], path_id(request), "Fiche")
            row = entity_detail(conn, kind, path_id(request))
        return JSONResponse(row)
    return handler


def revision_conflict(conn: sqlite3.Connection, kind: str, current: dict[str, Any],
                      base: int | None) -> JSONResponse | None:
    """Fiche PNJ, faction ou lieu modifiée depuis la révision sur laquelle le formulaire est parti (audit M1) :
    409 avec l'état actuel (le formulaire montre les champs en conflit, sans perdre la saisie). Sans
    base_revision (connecteur Claude, scripts), aucune vérification."""
    if base is None or base == current["revision"]:
        return None
    last = conn.execute(f"SELECT author_name FROM entry_versions WHERE {kind}_id = ? ORDER BY id DESC LIMIT 1",
                        (current["id"],)).fetchone()
    author = last["author_name"] if last else ""
    # Réponse écrite directement : `detail` traduit par I18nMiddleware (modèle reconnu dans le catalogue).
    detail = (msg("Fiche modifiée entre-temps par {author} : vos changements n'ont pas été enregistrés", author=author)
              if author else "Fiche modifiée entre-temps : vos changements n'ont pas été enregistrés")
    return JSONResponse({"detail": detail, "author": author, "current": entity_detail(conn, kind, current["id"])},
                        status_code=409)


def _update_entity(kind: str, model: type[BaseModel]):
    @threaded
    def handler(request: Request) -> Response:
        eid = path_id(request)
        data = parse_body(request, model)
        values = data.model_dump(exclude_unset=True)
        base = values.pop("base_revision", None)
        separate = values.pop("new_version", False)
        with tx(request) as conn:
            current = scoped(conn, request, TABLES[kind], eid, "Fiche")
            conflict = revision_conflict(conn, kind, current, base)
            if conflict is not None:
                return conflict
            if IMAGE_FIELD[kind] in values:
                check_image_owner(conn, values[IMAGE_FIELD[kind]], kind, eid)
            db.update(conn, TABLES[kind], eid, values)
            touch_campaign(conn, current["campaign_id"])
            after = get_or_404(conn, TABLES[kind], eid, "Fiche")
            from .entry_history import record_change  # import circulaire évité
            record_change(conn, kind, eid, current, after, auth.current_user(request), merge=base is not None and not separate)
            row = entity_detail(conn, kind, eid)
        return JSONResponse(row)
    return handler


def _delete_entity(kind: str):
    @threaded
    def handler(request: Request) -> Response:
        eid = path_id(request)
        with tx(request) as conn:
            current = scoped(conn, request, TABLES[kind], eid, "Fiche")
            files = conn.execute(
                "SELECT file, thumb FROM images WHERE owner_kind = ? AND owner_id = ?",
                (kind, eid)).fetchall()
            conn.execute(f"DELETE FROM {TABLES[kind]} WHERE id = ?", (eid,))
            touch_campaign(conn, current["campaign_id"])
        for f in files:
            imaging.delete_files(settings(request).media_dir, f["file"], f["thumb"])
        return Response(status_code=204)
    return handler


# --- Appartenances ---------------------------------------------------------

def faction_ancestors(conn: sqlite3.Connection, faction_id: int) -> set[int]:
    """La faction et toutes celles dont elle est membre, directement ou non (UNION : s'arrête même sur un cycle)."""
    return {r[0] for r in conn.execute(
        """WITH RECURSIVE up(id) AS (
               SELECT ? UNION SELECT m.faction_id FROM memberships m JOIN up ON m.member_faction_id = up.id)
           SELECT id FROM up""", (faction_id,))}


def check_faction_member(conn: sqlite3.Connection, member: dict[str, Any], faction: dict[str, Any]) -> None:
    """Règles d'une faction membre d'une autre : même campagne, pas elle-même, pas de cycle (A dans B dans A, ou
    plus long). Le reste (une seule fois par paire, pas de chef) est aussi tenu par la base (migration 031)."""
    if member["campaign_id"] != faction["campaign_id"]:
        raise HTTPException(400, "Les deux factions ne sont pas dans la même campagne")
    if member["id"] == faction["id"]:
        raise HTTPException(400, "Une faction ne peut pas être membre d'elle-même")
    if member["id"] in faction_ancestors(conn, faction["id"]):
        raise HTTPException(400, msg("« {faction} » est déjà membre, directement ou non, de « {member} » : cette appartenance formerait un cycle",  # noqa: E501
                                     faction=faction["name"], member=member["name"]))


@threaded
def create_membership(request: Request) -> Response:
    """Membre d'une faction : un PNJ (npc_id) ou une autre faction (member_faction_id), qui en devient une
    sous-faction."""
    data = parse_body(request, MembershipCreate)
    if (data.npc_id is None) == (data.member_faction_id is None):
        raise HTTPException(400, "Indiquez un seul membre : un PNJ (npc_id) ou une faction (member_faction_id)")
    with tx(request) as conn:
        if data.npc_id is not None:
            npc = scoped(conn, request, "npcs", data.npc_id, "PNJ")
            faction = scoped(conn, request, "factions", data.faction_id, "Faction")
            if npc["campaign_id"] != faction["campaign_id"]:
                raise HTTPException(400, "Le PNJ et la faction ne sont pas dans la même campagne")
        else:
            member = scoped(conn, request, "factions", data.member_faction_id, "Faction")
            faction = scoped(conn, request, "factions", data.faction_id, "Faction")
            if data.is_leader:
                raise HTTPException(400, "Seul un PNJ peut diriger une faction")
            check_faction_member(conn, member, faction)
        try:
            new_id = db.insert(conn, "memberships", data.model_dump(exclude_none=True))
        except sqlite3.IntegrityError:
            if data.npc_id is not None:
                raise HTTPException(409, "Ce PNJ est déjà membre de cette faction") from None
            raise HTTPException(409, msg("« {member} » est déjà membre de « {faction} »",
                                         member=member["name"], faction=faction["name"])) from None
        row = scoped(conn, request, "memberships", new_id, "Appartenance")
    return JSONResponse(row, status_code=201)


@threaded
def update_membership(request: Request) -> Response:
    mid = path_id(request)
    data = parse_body(request, MembershipUpdate)
    with tx(request) as conn:
        current = scoped(conn, request, "memberships", mid, "Appartenance")
        if current["member_faction_id"] is not None and data.is_leader:
            raise HTTPException(400, "Seul un PNJ peut diriger une faction")
        db.update(conn, "memberships", mid, data.model_dump(exclude_unset=True), touch=False)
        row = scoped(conn, request, "memberships", mid, "Appartenance")
    return JSONResponse(row)


@threaded
def delete_membership(request: Request) -> Response:
    mid = path_id(request)
    with tx(request) as conn:
        scoped(conn, request, "memberships", mid, "Appartenance")
        conn.execute("DELETE FROM memberships WHERE id = ?", (mid,))
    return Response(status_code=204)


# --- Natures de lien ------------------------------------------------------

@threaded
def reorder_entities(request: Request) -> Response:
    """Ordre libre des PNJ ou des factions (index des personnages) : la liste complète, dans l'ordre."""
    cid = path_id(request)
    kind = request.path_params["kind"]
    if kind not in ("npc", "faction"):
        raise HTTPException(404, "Route inconnue")
    data = parse_body(request, EntityOrder)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        existing = {r[0] for r in conn.execute(f"SELECT id FROM {TABLES[kind]} WHERE campaign_id = ?", (cid,))}
        if set(data.ids) != existing or len(data.ids) != len(existing):
            raise HTTPException(400, "La liste doit contenir exactement les fiches de cette campagne")
        conn.executemany(f"UPDATE {TABLES[kind]} SET sort_order = ? WHERE id = ?",
                         [(i + 1, x) for i, x in enumerate(data.ids)])
    return Response(status_code=204)


def list_types(conn: sqlite3.Connection, campaign_id: int) -> list[dict[str, Any]]:
    return db.rows_to_dicts(conn.execute(
        "SELECT * FROM relation_types WHERE campaign_id = ? ORDER BY sort_order, id",
        (campaign_id,)).fetchall())


def check_type(conn: sqlite3.Connection, campaign_id: int, key: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM relation_types WHERE campaign_id = ? AND key = ?", (campaign_id, key)).fetchone()
    if row is None:
        raise HTTPException(400, "Nature de lien inconnue dans cette campagne")


@threaded
def list_relation_types(request: Request) -> Response:
    cid = path_id(request)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        rows = list_types(conn, cid)
    return JSONResponse(rows)


@threaded
def create_relation_type(request: Request) -> Response:
    cid = path_id(request)
    data = parse_body(request, RelationTypeCreate)
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        exists = conn.execute(
            "SELECT 1 FROM relation_types WHERE campaign_id = ? AND lower(label) = lower(?)",
            (cid, data.label)).fetchone()
        if exists:
            raise HTTPException(409, msg("La nature « {label} » existe déjà", label=data.label))
        order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM relation_types WHERE campaign_id = ?",
            (cid,)).fetchone()[0]
        new_id = db.insert(conn, "relation_types", {
            **data.model_dump(), "campaign_id": cid, "sort_order": order,
            "key": defaults.unique_type_key(conn, cid, data.label),
        })
        row = scoped(conn, request, "relation_types", new_id, "Nature de lien")
    return JSONResponse(row, status_code=201)


@threaded
def update_relation_type(request: Request) -> Response:
    tid = path_id(request)
    data = parse_body(request, RelationTypeUpdate)
    values = data.model_dump(exclude_unset=True)
    with tx(request) as conn:
        current = scoped(conn, request, "relation_types", tid, "Nature de lien")
        if "label" in values:
            clash = conn.execute(
                "SELECT 1 FROM relation_types WHERE campaign_id = ? AND lower(label) = lower(?) AND id <> ?",
                (current["campaign_id"], values["label"], tid)).fetchone()
            if clash:
                raise HTTPException(409, msg("La nature « {label} » existe déjà", label=values["label"]))
        db.update(conn, "relation_types", tid, values, touch=False)
        row = scoped(conn, request, "relation_types", tid, "Nature de lien")
    return JSONResponse(row)


@threaded
def delete_relation_type(request: Request) -> Response:
    tid = path_id(request)
    with tx(request) as conn:
        current = scoped(conn, request, "relation_types", tid, "Nature de lien")
        used = conn.execute(
            "SELECT COUNT(*) FROM relations WHERE campaign_id = ? AND type = ?",
            (current["campaign_id"], current["key"])).fetchone()[0]
        if used:
            raise HTTPException(409, msgn("{n} lien utilise la nature « {label} ». Changez sa nature avant de la supprimer.",
                                          "{n} liens utilisent la nature « {label} ». Changez leur nature avant de la supprimer.",
                                          used, label=current["label"]))
        conn.execute("DELETE FROM relation_types WHERE id = ?", (tid,))
    return Response(status_code=204)


# --- Relations -------------------------------------------------------------

@threaded
def create_relation(request: Request) -> Response:
    cid = path_id(request)
    data = parse_body(request, RelationCreate)
    if data.source_kind == data.target_kind and data.source_id == data.target_id:
        raise HTTPException(400, "Une relation doit relier deux entités différentes")
    with tx(request) as conn:
        scoped(conn, request, "campaigns", cid, "Campagne")
        check_entity(conn, data.source_kind, data.source_id, cid)
        check_entity(conn, data.target_kind, data.target_id, cid)
        check_type(conn, cid, data.type)
        new_id = db.insert(conn, "relations", {**data.model_dump(), "campaign_id": cid})
        touch_campaign(conn, cid)
        row = scoped(conn, request, "relations", new_id, "Relation")
    return JSONResponse(row, status_code=201)


@threaded
def update_relation(request: Request) -> Response:
    rid = path_id(request)
    data = parse_body(request, RelationUpdate)
    values = data.model_dump(exclude_unset=True)
    with tx(request) as conn:
        current = scoped(conn, request, "relations", rid, "Relation")
        merged = {**current, **values}
        if merged["source_kind"] == merged["target_kind"] and merged["source_id"] == merged["target_id"]:
            raise HTTPException(400, "Une relation doit relier deux entités différentes")
        check_entity(conn, merged["source_kind"], merged["source_id"], current["campaign_id"])
        check_entity(conn, merged["target_kind"], merged["target_id"], current["campaign_id"])
        if "type" in values:
            check_type(conn, current["campaign_id"], values["type"])
        db.update(conn, "relations", rid, values)
        row = scoped(conn, request, "relations", rid, "Relation")
    return JSONResponse(row)


@threaded
def delete_relation(request: Request) -> Response:
    rid = path_id(request)
    with tx(request) as conn:
        scoped(conn, request, "relations", rid, "Relation")
        conn.execute("DELETE FROM relations WHERE id = ?", (rid,))
    return Response(status_code=204)


# --- Images ----------------------------------------------------------------

def _upload_images(kind: str):
    async def handler(request: Request) -> Response:
        cfg = settings(request)
        eid = path_id(request)
        limit = cfg.max_upload_mb * 1024 * 1024
        declared = int(request.headers.get("content-length") or 0)
        if declared > limit * limits.GALLERY_FILES:
            raise HTTPException(413, msg("Envoi trop volumineux (max {size} Mo par image)", size=cfg.max_upload_mb))

        entity = await in_tx(request, lambda conn: scoped(conn, request, TABLES[kind], eid, "Fiche"), write=False)

        form = await request.form(max_files=limits.GALLERY_FILES, max_fields=limits.GALLERY_FILES)
        uploads = [f for f in form.getlist("files") if hasattr(f, "read")]
        if not uploads:
            raise HTTPException(400, "Aucun fichier reçu (champ 'files')")

        stored: list[tuple[imaging.StoredImage, str]] = []
        try:
            for upload in uploads:
                raw = await upload.read(limit + 1)
                if len(raw) > limit:
                    raise HTTPException(413, msg("{name} dépasse {size} Mo", name=upload.filename or "", size=cfg.max_upload_mb))
                try:
                    img = await run_in_threadpool(imaging.process_upload, raw, cfg.media_dir,
                                                  entity["campaign_id"])
                except imaging.ImageError as exc:
                    raise HTTPException(400, msg("{name} : {error}", name=upload.filename or "", error=i18n.message(exc))) from exc
                stored.append((img, (upload.filename or "")[:200]))
        except BaseException:
            for img, _ in stored:
                imaging.delete_files(cfg.media_dir, img.file, img.thumb)
            raise
        finally:
            await form.close()

        def store(conn: sqlite3.Connection) -> dict[str, Any]:
            # La fiche a pu être supprimée pendant l'envoi.
            entity = scoped(conn, request, TABLES[kind], eid, "Fiche")
            return _store_images(conn, kind, eid, entity, stored)
        try:
            row = await in_tx(request, store)
        except BaseException:
            for img, _ in stored:
                imaging.delete_files(cfg.media_dir, img.file, img.thumb)
            raise
        return JSONResponse(row, status_code=201)
    return handler


def _store_images(conn: sqlite3.Connection, kind: str, eid: int, entity: dict[str, Any],
                  stored: list[tuple[imaging.StoredImage, str]]) -> dict[str, Any]:
    next_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM images WHERE owner_kind = ? AND owner_id = ?",
        (kind, eid)).fetchone()[0]
    first_id = None
    for offset, (img, name) in enumerate(stored):
        new_id = db.insert(conn, "images", {
            "campaign_id": entity["campaign_id"], "owner_kind": kind, "owner_id": eid,
            "file": img.file, "thumb": img.thumb, "original_name": name,
            "width": img.width, "height": img.height, "sort_order": next_order + offset,
        })
        first_id = first_id or new_id
    # La première image devient le portrait / l'emblème si la fiche n'en a pas.
    if entity[IMAGE_FIELD[kind]] is None and first_id:
        db.update(conn, TABLES[kind], eid, {IMAGE_FIELD[kind]: first_id})
    return entity_detail(conn, kind, eid)


@threaded
def update_image(request: Request) -> Response:
    iid = path_id(request)
    data = parse_body(request, ImageUpdate)
    with tx(request) as conn:
        scoped(conn, request, "images", iid, "Image")
        db.update(conn, "images", iid, data.model_dump(exclude_unset=True), touch=False)
        row = scoped(conn, request, "images", iid, "Image")
    return JSONResponse(image_out(row))


@threaded
def delete_image(request: Request) -> Response:
    iid = path_id(request)
    with tx(request) as conn:
        row = scoped(conn, request, "images", iid, "Image")
        kind, owner = row["owner_kind"], row["owner_id"]
        cover_field = IMAGE_FIELD[kind]
        was_cover = conn.execute(
            f"SELECT 1 FROM {TABLES[kind]} WHERE id = ? AND {cover_field} = ?", (owner, iid)).fetchone()
        conn.execute("DELETE FROM images WHERE id = ?", (iid,))
        if was_cover:
            # L'image suivante de la galerie devient le portrait / l'emblème.
            nxt = conn.execute(
                "SELECT id FROM images WHERE owner_kind = ? AND owner_id = ? ORDER BY sort_order, id LIMIT 1",
                (kind, owner)).fetchone()
            conn.execute(f"UPDATE {TABLES[kind]} SET {cover_field} = ? WHERE id = ?",
                         (nxt[0] if nxt else None, owner))
    imaging.delete_files(settings(request).media_dir, row["file"], row["thumb"])
    return Response(status_code=204)


routes = [
    Route("/api/health", health),
    Route("/api/auth/status", auth_status),
    Route("/api/auth/login", login, methods=["POST"]),
    Route("/api/auth/logout", logout, methods=["POST"]),
    Route("/api/auth/setup", setup, methods=["POST"]),
    Route("/api/auth/password", change_password, methods=["POST"]),
    Route("/api/auth/locale", change_locale, methods=["PUT"]),

    Route("/api/campaigns", list_campaigns),
    Route("/api/campaigns", create_campaign, methods=["POST"]),
    Route("/api/campaigns/{id:int}", get_campaign),
    Route("/api/campaigns/{id:int}", update_campaign, methods=["PATCH"]),
    Route("/api/campaigns/{id:int}", delete_campaign, methods=["DELETE"]),
    Route("/api/campaigns/{id:int}/graph", campaign_graph),
    Route("/api/campaigns/{id:int}/positions", save_positions, methods=["PUT"]),
    Route("/api/campaigns/{id:int}/npcs", _create_entity("npc", NpcCreate), methods=["POST"]),
    Route("/api/campaigns/{id:int}/factions", _create_entity("faction", FactionCreate), methods=["POST"]),
    Route("/api/campaigns/{id:int}/relations", create_relation, methods=["POST"]),
    Route("/api/campaigns/{id:int}/order/{kind}", reorder_entities, methods=["PUT"]),
    Route("/api/campaigns/{id:int}/relation-types", list_relation_types),
    Route("/api/campaigns/{id:int}/relation-types", create_relation_type, methods=["POST"]),
    Route("/api/relation-types/{id:int}", update_relation_type, methods=["PATCH"]),
    Route("/api/relation-types/{id:int}", delete_relation_type, methods=["DELETE"]),

    Route("/api/npcs/{id:int}", _get_entity("npc")),
    Route("/api/npcs/{id:int}", _update_entity("npc", NpcUpdate), methods=["PATCH"]),
    Route("/api/npcs/{id:int}", _delete_entity("npc"), methods=["DELETE"]),
    Route("/api/npcs/{id:int}/images", _upload_images("npc"), methods=["POST"]),

    Route("/api/factions/{id:int}", _get_entity("faction")),
    Route("/api/factions/{id:int}", _update_entity("faction", FactionUpdate), methods=["PATCH"]),
    Route("/api/factions/{id:int}", _delete_entity("faction"), methods=["DELETE"]),
    Route("/api/factions/{id:int}/images", _upload_images("faction"), methods=["POST"]),

    Route("/api/memberships", create_membership, methods=["POST"]),
    Route("/api/memberships/{id:int}", update_membership, methods=["PATCH"]),
    Route("/api/memberships/{id:int}", delete_membership, methods=["DELETE"]),

    Route("/api/relations/{id:int}", update_relation, methods=["PATCH"]),
    Route("/api/relations/{id:int}", delete_relation, methods=["DELETE"]),

    Route("/api/images/{id:int}", update_image, methods=["PATCH"]),
    Route("/api/images/{id:int}", delete_image, methods=["DELETE"]),
]
