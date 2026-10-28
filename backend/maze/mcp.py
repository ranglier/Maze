"""Serveur MCP de Maze (connecteur Claude) : POST /mcp, transport « Streamable HTTP » sans état.

Chaque requête porte un jeton OAuth (oauth.py). Les outils passent par les routes de l'API REST,
appelées en interne avec l'identité de Claude (connector.acting_user) : mêmes validations, mêmes droits,
mêmes effets (index de recherche, historique, éditeurs ouverts rechargés) que depuis l'interface.
Les outils d'écriture ne voient que les campagnes ouvertes en écriture (lecture et écriture, ou lecture, écriture et
suppression) ; les outils de suppression (delete_entry, delete_document, delete_event, delete_map, delete_comment),
seulement celles ouvertes en lecture, écriture et suppression (migration 033).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, get_args
from urllib.parse import urlencode

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, connector, db, entries
from .collab import RESTORING
from .api import RequestValidationError, in_tx, settings
from .documents import UNSAVED_EDITS, has_unsaved_edits
from .foundry_html import is_rich_html, markdown_to_html
from .schemas import DocStatus

log = logging.getLogger("maze.mcp")

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_BODY = 4 * 1024 * 1024
MAX_BATCH = 20           # messages par lot JSON-RPC (audit PERF-13 ; les lots ont disparu du protocole 2025-06-18)
# Taille des résultats : les clients MCP plafonnent un résultat d'outil (environ 25 000 jetons ; 66 000
# caractères refusés en recette). Grosses campagnes : get_campaign se compacte puis se tronque pour tenir
# dans ce budget, les liens sont paginés ; le détail passe par search et get_entry.
RESULT_BUDGET = 40_000    # caractères visés pour get_campaign et une page de liens
RESULT_MAX = 80_000       # au-delà, résultat refusé avec un message (plutôt qu'un échec du client)
RELATIONS_DEFAULT, RELATIONS_MAX = 50, 100
EVENTS_DEFAULT, EVENTS_MAX = 100, 200
ENTRY_PATHS = {"npc": "npcs", "faction": "factions", "place": "places"}
ENTRY_TABLES = {"npc": "npcs", "faction": "factions", "place": "places"}
# Niveaux d'accès d'une campagne (mcp_grants.mode) : delete permet tout ce que permet write (migration 033).
WRITE_MODES = ("write", "delete")
ACCESS_LABELS = {"read": "lecture seule", "write": "lecture et écriture", "delete": "lecture, écriture et suppression"}
SUSPENDED = ("L'accès de Claude à Maze est suspendu. Un administrateur peut le réactiver dans "
             "Administration > Connecteur Claude.")

INSTRUCTIONS = """Maze : campagnes de jeu de rôle d'un MJ (PNJ, factions, lieux, scénarios rédigés).
Commencer par list_campaigns puis get_campaign pour s'orienter ; search pour retrouver une fiche.
Avant de modifier une fiche rédigée, la lire avec get_document : update_document exige sa révision
(base_revision) et remplace tout le contenu HTML. Une version « Avant modification par Claude » est
gardée dans l'historique de la fiche ; les MJ qui l'ont ouverte la voient se recharger.
update_entry (PNJ, factions, lieux) : passer base_revision (révision de get_entry ou du dernier update_entry).
Contenu HTML de l'éditeur : <p>, <h1>-<h3>, <strong>, <em>, <u>, <ul>/<ol>/<li>, <blockquote>, <hr>,
tableaux <table><tr><th>/<td> (paragraphes dans les cellules), liens <a href>. Mention d'une fiche :
<a class="mention" data-mention="npc:12">Nom</a> (npc, faction, place ou doc). Encadré « Accroche »
(texte à lire aux joueurs) : <div data-callout="read"><p>…</p></div> ; encadré « Secret » :
data-callout="secret". Alignement : data-align="center|right|justify" sur <p> ou <h1>-<h3>.
En modifiant un contenu existant, conserver tels quels les <span data-comment> (commentaires) et
<img data-doc-image> (images). Les champs longs des PNJ, factions et lieux acceptent le même HTML
(ou du Markdown). Images : get_entry liste la galerie d'une fiche (image de couverture : cover) ; view_image
pour la voir ; list_library : images déposées par le MJ sans fiche (bibliothèque) ; add_image depuis une
adresse web publique, la bibliothèque ou une autre image de Maze ; edit_image pour
pivoter, retourner ou recadrer (l'originale est gardée en fin de galerie sauf keep_original=false) ;
add_document_image puis update_document pour illustrer une fiche rédigée. Cartes interactives (Lieux > Carte
interactive) : list_maps, view_map (aperçu quadrillé tous les 10 %), place_on_map pour épingler un lieu
(x, y en %) ; les sous-lieux d'un lieu épinglé s'affichent en anneaux autour de lui sans épingle.
set_place_kind pour donner un type (clé, voir get_campaign) à plusieurs lieux d'un coup.
Chronologie (module facultatif, activé par le MJ campagne par campagne : get_campaign l'indique) :
list_events, create_event, update_event ; date en texte libre, ordre manuel (before_id pour insérer ou déplacer).
Suppression (delete_entry, delete_document, delete_event, delete_map, delete_comment) : seulement sur demande du MJ,
dans une campagne ouverte en « lecture, écriture et suppression » (access de list_campaigns), avec le nom ou le titre
exact de l'élément ; définitive (pas de corbeille, seules les sauvegardes de Maze permettent de revenir en arrière).
Répondre aux MJ en français."""


class ToolError(Exception):
    """Erreur montrée à Claude (résultat d'outil isError), pas une erreur de protocole."""


@dataclass
class Blocks:
    """Résultat d'outil composé de blocs MCP (texte et images) plutôt que d'un seul texte JSON."""
    content: list[dict[str, Any]]


@dataclass
class Context:
    app: Any
    user: dict[str, Any]              # administrateur au nom duquel Claude agit
    grants: dict[int, str]            # campagnes ouvertes : {id: "read" | "write" | "delete"}
    campaign_id: int | None = None    # campagne concernée par l'appel (journal)
    target: str = ""

    @property
    def readable(self) -> set[int]:
        return set(self.grants)

    @property
    def writable(self) -> set[int]:
        return {cid for cid, mode in self.grants.items() if mode in WRITE_MODES}

    @property
    def deletable(self) -> set[int]:
        """Campagnes ouvertes en lecture, écriture et suppression (migration 033)."""
        return {cid for cid, mode in self.grants.items() if mode == "delete"}

    def transaction(self):
        """Lecture dans la boucle d'événements (outils du connecteur) : instantané en lecture seule, qui n'attend pas
        les écritures (WAL) ; au pire une attente courte, puis « réessayez » (audit M15). Écritures : run."""
        return db.transaction(self.app.state.settings.db_path, write=False, timeout=db.LOOP_TIMEOUT)

    async def run(self, work: Any) -> Any:
        """work(conn) dans une transaction d'écriture, dans un fil de travail (db.run_db)."""
        path = self.app.state.settings.db_path

        def run() -> Any:
            with db.transaction(path) as conn:
                return work(conn)
        return await db.run_db(run)

    # --- Appel interne de l'API REST ----------------------------------------------------------

    async def api(self, method: str, path: str, body: Any = None, query: dict[str, Any] | None = None,
                  write: bool = False, delete: bool = False) -> Any:
        scope = self.deletable if delete else self.writable if write else self.readable
        user = connector.acting_user(self.user, scope)
        # Appels internes, hors des middlewares HTTP : même refus des écritures pendant une restauration (audit M7).
        hub = getattr(self.app.state, "hub", None)
        if method not in ("GET", "HEAD", "OPTIONS") and hub is not None and hub.restoring():
            raise ToolError(RESTORING)
        raw = json.dumps(body).encode() if body is not None else b""
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
            "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
            "query_string": urlencode(query or {}).encode(),
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())],
            "client": ("claude", 0), "server": ("maze", 80), "app": self.app, "state": {"user": user},
            # Pas de gestionnaire d'exceptions : les erreurs des routes remontent jusqu'ici.
            "starlette.exception_handlers": ({}, {}),
        }
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return {"type": "http.disconnect"}

        status, chunks = 500, []

        async def send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        try:
            await self.app.router(scope, receive, send)
        except HTTPException as exc:
            raise ToolError(str(exc.detail)) from None
        except RequestValidationError as exc:
            details = "; ".join(f"{'.'.join(str(p) for p in e.get('loc', ()))} : {e.get('msg', '')}"
                                for e in exc.errors[:8])
            raise ToolError(f"Données invalides ({details})") from None
        except OverflowError:
            raise ToolError("Nombre hors limites") from None
        payload = b"".join(chunks)
        data = json.loads(payload) if payload else None
        if status >= 400:
            detail = data.get("detail") if isinstance(data, dict) else None
            raise ToolError(str(detail or f"Erreur {status}"))
        return data

    # --- Droits ---------------------------------------------------------------------------------

    def campaign_name(self, conn, cid: int) -> str:
        row = conn.execute("SELECT name FROM campaigns WHERE id = ?", (cid,)).fetchone()
        return row[0] if row else str(cid)

    def check_read(self, cid: int | None, what: str = "Campagne") -> int:
        if cid is None or cid not in self.grants:
            raise ToolError(f"{what} introuvable (ou non ouverte à Claude)")
        self.campaign_id = cid
        return cid

    def check_write(self, cid: int | None, what: str = "Fiche") -> int:
        self.check_read(cid, what)
        if self.grants[cid] not in WRITE_MODES:
            with self.transaction() as conn:
                name = self.campaign_name(conn, cid)
            raise ToolError(f"La campagne « {name} » est ouverte à Claude en lecture seule.")
        return cid

    def check_delete(self, cid: int | None, what: str = "Fiche") -> int:
        """Outils de suppression : campagne ouverte en lecture, écriture et suppression (migration 033)."""
        self.check_read(cid, what)
        if self.grants[cid] != "delete":
            with self.transaction() as conn:
                name = self.campaign_name(conn, cid)
            raise ToolError(f"La campagne « {name} » n'autorise pas Claude à supprimer (Administration > "
                            "Connecteur Claude).")
        return cid

    def campaign_of(self, table: str, row_id: int) -> int | None:
        queries = {
            "memberships": "SELECT f.campaign_id FROM memberships m JOIN factions f ON f.id = m.faction_id "
                           "WHERE m.id = ?",
            "place_links": "SELECT p.campaign_id FROM place_links l JOIN places p ON p.id = l.place_id WHERE l.id = ?",
            "comments": "SELECT d.campaign_id FROM comments c JOIN documents d ON d.id = c.document_id WHERE c.id = ?",
        }
        sql = queries.get(table, f"SELECT campaign_id FROM {table} WHERE id = ?")
        with self.transaction() as conn:
            row = conn.execute(sql, (row_id,)).fetchone()
        return row[0] if row else None


# --- Aides -------------------------------------------------------------------------------------

def _int(args: dict[str, Any], key: str, required: bool = True) -> int | None:
    value = args.get(key)
    if value is None:
        if required:
            raise ToolError(f"Paramètre manquant : {key}")
        return None
    if isinstance(value, bool) or not isinstance(value, int | str) or not re.fullmatch(r"-?\d{1,15}", str(value)):
        raise ToolError(f"Paramètre {key} : entier attendu")
    return int(value)


def _kind(args: dict[str, Any], key: str = "kind", allowed=("npc", "faction", "place")) -> str:
    value = args.get(key)
    if value not in allowed:
        raise ToolError(f"Paramètre {key} : une valeur parmi {', '.join(allowed)}")
    return value


# Champs modifiables par Claude : registre (entries.py, audit M23) ; ni images, ni positions sur les cartes, ni les
# anciens champs invisibles dans Maze (people, age, location du PNJ).
ENTRY_FIELDS = {kind: entries.connector_fields(kind) for kind in entries.KINDS}
# Anciens champs (PNJ : people, age, location), invisibles dans Maze : Claude les connaît encore (conversations,
# habitudes d'avant le 5 octobre). Ignorés, et signalés dans la réponse, au lieu de faire refuser tout l'appel
# (contre-revue du lot 5) ; l'API REST les refuse toujours (422).
LEGACY_FIELDS = {kind: frozenset(entries.keys(kind, legacy=True)) for kind in entries.KINDS}


def _fields(args: dict[str, Any], kind: str, ignored: list[str] | None = None) -> dict[str, Any]:
    fields = args.get("fields") or {}
    if not isinstance(fields, dict):
        raise ToolError("Paramètre fields : objet attendu")
    if ignored is not None:
        ignored.extend(sorted(set(fields) & LEGACY_FIELDS[kind]))
        fields = {k: v for k, v in fields.items() if k not in LEGACY_FIELDS[kind]}
    unknown = sorted(set(fields) - ENTRY_FIELDS[kind])
    if unknown:
        raise ToolError(f"Champs inconnus pour {kind} : {', '.join(unknown)} "
                        f"(champs possibles : {', '.join(sorted(ENTRY_FIELDS[kind]))})")
    return fields


def _short(text: str | None, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", db.html_text(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _names(conn, campaign_id: int) -> dict[tuple[str, int], str]:
    names: dict[tuple[str, int], str] = {}
    for kind, table in ENTRY_TABLES.items():
        for row in conn.execute(f"SELECT id, name FROM {table} WHERE campaign_id = ?", (campaign_id,)):
            names[(kind, row[0])] = row[1]
    return names


def _relations(conn, campaign_id: int, kind: str | None = None, entity_id: int | None = None,
               rel_type: str | None = None, limit: int | None = None, offset: int = 0) -> tuple[list[dict], int]:
    """Liens de la campagne (ou d'une fiche, ou d'une nature), page demandée et nombre total."""
    names = _names(conn, campaign_id)
    labels = dict(conn.execute("SELECT key, label FROM relation_types WHERE campaign_id = ?", (campaign_id,)).fetchall())
    where = "WHERE campaign_id = ?"
    params: list[Any] = [campaign_id]
    if kind is not None:
        where += " AND ((source_kind = ? AND source_id = ?) OR (target_kind = ? AND target_id = ?))"
        params += [kind, entity_id, kind, entity_id]
    if rel_type is not None:
        where += " AND type = ?"
        params.append(rel_type)
    total = conn.execute(f"SELECT COUNT(*) FROM relations {where}", params).fetchone()[0]
    page = f" LIMIT {int(limit)} OFFSET {int(offset)}" if limit is not None else ""
    out = []
    for r in db.rows_to_dicts(conn.execute(f"SELECT * FROM relations {where} ORDER BY id{page}", params).fetchall()):
        out.append({
            "id": r["id"], "type": r["type"], "type_label": labels.get(r["type"], r["type"]),
            "label": r["label"], "description": r["description"], "directed": r["directed"], "secret": r["secret"],
            "source": {"kind": r["source_kind"], "id": r["source_id"], "name": names.get((r["source_kind"], r["source_id"]))},
            "target": {"kind": r["target_kind"], "id": r["target_id"], "name": names.get((r["target_kind"], r["target_id"]))},
        })
    return out, total


def _clean_entry(row: dict[str, Any], kind: str) -> dict[str, Any]:
    cover = row.get("portrait_image_id") or row.get("emblem_image_id") or row.get("image_id")
    row["images"] = [{"id": i["id"], "caption": i["caption"], "width": i["width"], "height": i["height"],
                      "cover": i["id"] == cover} for i in row.get("images", [])]
    # Ni images, ni positions, ni les anciens champs invisibles dans Maze (registre : connector=False).
    for key in ("portrait_image_id", "emblem_image_id", "image_id", "pos_x", "pos_y",
                *entries.hidden_from_connector(kind)):
        row.pop(key, None)
    for link in row.get("places", []) + row.get("links", []) + row.get("nested_links", []):
        for key in ("thumb", "kind_icon"):
            link.pop(key, None)
    for child in row.get("children", []):
        child.pop("thumb", None)
        child.pop("image_id", None)
    return row


# --- Outils de lecture --------------------------------------------------------------------------

async def list_campaigns(ctx: Context, args: dict[str, Any]) -> Any:
    if not ctx.grants:
        return {"campaigns": [], "note": "Aucune campagne n'est ouverte à Claude (Administration > Connecteur Claude)."}
    with ctx.transaction() as conn:
        # Même règle que l'API (auth.visible_campaign_ids, audit QUAL-6) : campagnes ouvertes à Claude.
        visible = auth.visible_campaign_ids(conn, connector.acting_user(ctx.user, ctx.readable)) or []
        marks = ",".join("?" * len(visible))
        rows = conn.execute(
            f"""SELECT c.id, c.name, c.description, c.updated_at,
                       (SELECT COUNT(*) FROM npcs WHERE campaign_id = c.id) AS npcs,
                       (SELECT COUNT(*) FROM factions WHERE campaign_id = c.id) AS factions,
                       (SELECT COUNT(*) FROM places WHERE campaign_id = c.id) AS places,
                       (SELECT COUNT(*) FROM documents WHERE campaign_id = c.id AND kind != 'campaign') AS documents
                  FROM campaigns c WHERE c.id IN ({marks}) ORDER BY c.updated_at DESC""",
            visible).fetchall()
    return {"campaigns": [{**dict(r), "description": _short(r["description"], 300),
                           "access": ACCESS_LABELS[ctx.grants[r["id"]]]}
                          for r in rows]}


async def search(ctx: Context, args: dict[str, Any]) -> Any:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("Paramètre manquant : query")
    cid = _int(args, "campaign_id", required=False)
    if cid is not None:
        ctx.check_read(cid)
    data = await ctx.api("GET", "/api/search", query={"q": query, "campaign": cid if cid is not None else "all"})
    results = []
    for r in data["results"]:
        item = {"kind": r["kind"], "id": r["id"], "campaign_id": r["campaign_id"],
                "title": r["title"].replace("\x02", "").replace("\x03", ""),
                "snippet": r["snippet"].replace("\x02", "«").replace("\x03", "»")}
        if r.get("doc_id"):
            item["document_id"] = r["doc_id"]
            item["document_title"] = r.get("doc_title")
        if r.get("entry_kind"):  # commentaire d'un PNJ, d'une faction ou d'un lieu : la fiche commentée
            item.update(entry_kind=r["entry_kind"], entry_id=r["entry_id"], entry_name=r.get("entry_name"))
        results.append(item)
    return {"results": results}


async def get_campaign(ctx: Context, args: dict[str, Any]) -> Any:
    cid = ctx.check_read(_int(args, "campaign_id"))
    campaign = await ctx.api("GET", f"/api/campaigns/{cid}")
    docs = await ctx.api("GET", f"/api/campaigns/{cid}/documents")
    places = await ctx.api("GET", f"/api/campaigns/{cid}/places")

    def load() -> tuple:
        with ctx.transaction() as conn:
            npcs = db.rows_to_dicts(conn.execute(
                "SELECT id, name, title, status, summary, tags FROM npcs WHERE campaign_id = ? "
                "ORDER BY sort_order, name COLLATE NOCASE", (cid,)).fetchall())
            factions = db.rows_to_dicts(conn.execute(
                "SELECT id, name, kind, summary, tags FROM factions WHERE campaign_id = ? "
                "ORDER BY sort_order, name COLLATE NOCASE", (cid,)).fetchall())
            members: dict[int, list[dict[str, Any]]] = {}
            parents: dict[int, list[dict[str, Any]]] = {}
            for m in conn.execute("""SELECT m.npc_id, m.member_faction_id, m.faction_id, m.role, m.is_leader
                                       FROM memberships m JOIN factions f ON f.id = m.faction_id
                                      WHERE f.campaign_id = ? ORDER BY m.id""", (cid,)):
                if m["npc_id"] is not None:
                    members.setdefault(m["npc_id"], []).append(
                        {"faction_id": m["faction_id"], "role": m["role"], "leader": bool(m["is_leader"])})
                else:   # sous-faction (migration 031)
                    parents.setdefault(m["member_faction_id"], []).append(
                        {"faction_id": m["faction_id"], "role": m["role"]})
            relation_types = db.rows_to_dicts(conn.execute(
                "SELECT key, label, directed, hint FROM relation_types WHERE campaign_id = ? ORDER BY sort_order, id",
                (cid,)).fetchall())
            relation_count = conn.execute("SELECT COUNT(*) FROM relations WHERE campaign_id = ?", (cid,)).fetchone()[0]
            timeline = conn.execute(
                "SELECT timeline_enabled, (SELECT COUNT(*) FROM timeline_events WHERE campaign_id = c.id) "
                "FROM campaigns c WHERE id = ?", (cid,)).fetchone()
        return npcs, factions, members, parents, relation_types, relation_count, timeline

    npcs, factions, members, parents, relation_types, relation_count, timeline = await run_in_threadpool(load)
    totals = {"npcs": len(npcs), "factions": len(factions), "places": len(places["places"])}
    for n in npcs:
        n["summary"] = _short(n["summary"])
        n["factions"] = members.get(n["id"], [])
    for f in factions:
        f["summary"] = _short(f["summary"])
        f["member_of"] = parents.get(f["id"], [])
    lists = {
        "npcs": npcs, "factions": factions,
        "places": [{"id": p["id"], "parent_id": p["parent_id"], "name": p["name"], "kind": p["kind"],
                    "summary": _short(p["summary"]), "tags": p["tags"]} for p in places["places"]],
    }
    out = {
        "campaign": {"id": cid, "name": campaign["name"], "description": campaign["description"],
                     "access": ACCESS_LABELS[ctx.grants[cid]]},
        "documents": {"root_id": docs["root_id"], "tree": [
            {k: d[k] for k in ("id", "parent_id", "kind", "title", "status")} for d in docs["documents"]]},
        "totals": totals,
        "place_kinds": [{"key": k["key"], "label": k["label"]} for k in places["kinds"]],
        "relation_types": relation_types,
        "relation_count": relation_count,
        # Chronologie (migration 032) : activée ou non, et nombre d'événements ; list_events pour le détail.
        "timeline": {"enabled": bool(timeline[0]), "events": timeline[1]},
    }
    return _fit_overview(out, lists)


def _size(data: Any) -> int:
    return len(json.dumps(data, ensure_ascii=False))


def _fit_overview(out: dict[str, Any], lists: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Vue d'ensemble dans le budget : complète ; sinon sans résumés ni étiquettes ; sinon listes tronquées
    (au prorata), avec une note qui renvoie vers search et get_entry."""
    if _size(out) + _size(lists) <= RESULT_BUDGET:
        return {**out, **lists}
    compact = {
        "npcs": [{"id": n["id"], "name": n["name"], "title": n["title"], "status": n["status"],
                  "factions": [m["faction_id"] for m in n["factions"]]} for n in lists["npcs"]],
        "factions": [{"id": f["id"], "name": f["name"], "kind": f["kind"],
                      **({"member_of": [m["faction_id"] for m in f["member_of"]]} if f.get("member_of") else {})}
                     for f in lists["factions"]],
        "places": [{"id": p["id"], "parent_id": p["parent_id"], "name": p["name"], "kind": p["kind"]}
                   for p in lists["places"]],
    }
    note = "Campagne volumineuse : résumés et étiquettes omis (get_entry pour le détail)."
    room = RESULT_BUDGET - _size(out) - 400
    if _size(compact) > room:
        # Tronquées dans cet ordre : PNJ d'abord (les plus nombreux), puis lieux, puis factions.
        for key in ("npcs", "places", "factions"):
            others = sum(_size(v) for k, v in compact.items() if k != key)
            items = compact[key]
            if others + _size(items) > room and items:
                keep = max(room - others, 0) * len(items) // _size(items)
                while keep and others + _size(items[:keep]) > room:
                    keep -= 1
                compact[key] = items[:keep]
        shown = ", ".join(f"{len(compact[k])} {k} sur {out['totals'][k]}" for k in compact)
        note += f" Listes tronquées ({shown}) : search pour retrouver les autres fiches."
    return {**out, **compact, "note": note}


async def get_entry(ctx: Context, args: dict[str, Any]) -> Any:
    kind = _kind(args)
    eid = _int(args, "id")
    ctx.target = f"{kind}:{eid}"
    ctx.check_read(ctx.campaign_of(ENTRY_TABLES[kind], eid), "Fiche")
    row = _clean_entry(await ctx.api("GET", f"/api/{ENTRY_PATHS[kind]}/{eid}"), kind)
    if kind in ("npc", "faction"):
        with ctx.transaction() as conn:
            row["relations"] = _relations(conn, row["campaign_id"], kind, eid)[0]
            column = "npc_id" if kind == "npc" else "faction_id"
            # PNJ membres (d'un PNJ : ses factions), puis, pour une faction, ses sous-factions (migration 031).
            row["memberships"] = [
                {"id": m["id"], "npc_id": m["npc_id"], "npc": m["npc"], "faction_id": m["faction_id"],
                 "faction": m["faction"], "role": m["role"], "leader": bool(m["is_leader"])}
                for m in conn.execute(
                    f"""SELECT m.*, n.name AS npc, f.name AS faction FROM memberships m
                          JOIN npcs n ON n.id = m.npc_id JOIN factions f ON f.id = m.faction_id
                         WHERE m.{column} = ? ORDER BY n.name COLLATE NOCASE""", (eid,))]
            if kind == "faction":
                row["memberships"] += [
                    {"id": m["id"], "member_faction_id": m["member_faction_id"], "member_faction": m["sub"],
                     "faction_id": m["faction_id"], "faction": m["faction"], "role": m["role"]}
                    for m in conn.execute(
                        """SELECT m.*, s.name AS sub, f.name AS faction FROM memberships m
                             JOIN factions s ON s.id = m.member_faction_id JOIN factions f ON f.id = m.faction_id
                            WHERE m.faction_id = ? ORDER BY s.name COLLATE NOCASE""", (eid,))]
                row["member_of"] = [
                    {"id": m["id"], "faction_id": m["faction_id"], "faction": m["faction"], "role": m["role"]}
                    for m in conn.execute(
                        """SELECT m.*, f.name AS faction FROM memberships m JOIN factions f ON f.id = m.faction_id
                            WHERE m.member_faction_id = ? ORDER BY f.name COLLATE NOCASE""", (eid,))]
    if args.get("include_comments") in (True, "true", 1):
        row["comments"] = await ctx.api("GET", f"/api/{ENTRY_PATHS[kind]}/{eid}/comments")
    return row


async def list_relations(ctx: Context, args: dict[str, Any]) -> Any:
    cid = ctx.check_read(_int(args, "campaign_id"))
    kind = _kind(args, "kind", ("npc", "faction")) if args.get("kind") is not None else None
    entity_id = _int(args, "id", required=kind is not None)
    rel_type = str(args["type"]) if args.get("type") is not None else None
    limit = _int(args, "limit", required=False) or RELATIONS_DEFAULT
    offset = _int(args, "offset", required=False) or 0
    if not 1 <= limit <= RELATIONS_MAX or offset < 0:
        raise ToolError(f"limit entre 1 et {RELATIONS_MAX}, offset positif")

    def load() -> dict[str, Any]:
        with ctx.transaction() as conn:
            types = db.rows_to_dicts(conn.execute(
                "SELECT key, label, directed, hint FROM relation_types WHERE campaign_id = ? ORDER BY sort_order, id",
                (cid,)).fetchall())
            relations, total = _relations(conn, cid, kind, entity_id, rel_type, limit, offset)
        out = {"relation_types": types, "total": total, "offset": offset, "relations": relations}
        if offset + len(relations) < total:
            out["next_offset"] = offset + len(relations)
        return out

    return await run_in_threadpool(load)


async def get_document(ctx: Context, args: dict[str, Any]) -> Any:
    did = _int(args, "document_id")
    ctx.target = f"doc:{did}"
    ctx.check_read(ctx.campaign_of("documents", did), "Fiche")
    doc = await ctx.api("GET", f"/api/documents/{did}")
    with ctx.transaction() as conn:
        children = db.rows_to_dicts(conn.execute(
            "SELECT id, kind, title, status FROM documents WHERE parent_id = ? ORDER BY sort_order, id",
            (did,)).fetchall())
    out = {k: doc.get(k) for k in ("id", "campaign_id", "parent_id", "kind", "title", "status", "revision",
                                   "updated_at", "content")}
    out["children"] = children
    out["linked_from"] = doc.get("linked_from", [])
    if args.get("include_comments") in (True, "true", 1):
        out["comments"] = await ctx.api("GET", f"/api/documents/{did}/comments")
    return out


# --- Outils d'écriture -------------------------------------------------------------------------

def _with_ignored(row: dict[str, Any], ignored: list[str]) -> dict[str, Any]:
    if ignored:
        row["ignored_fields"] = ignored
        row["note"] = (f"Champ(s) ignoré(s) : {', '.join(ignored)} (anciens champs, invisibles dans Maze : mettre "
                       "ces informations dans le résumé ou une rubrique)")
    return row


async def create_entry(ctx: Context, args: dict[str, Any]) -> Any:
    kind = _kind(args)
    cid = ctx.check_write(_int(args, "campaign_id"), "Campagne")
    ignored: list[str] = []
    row = await ctx.api("POST", f"/api/campaigns/{cid}/{ENTRY_PATHS[kind]}", _fields(args, kind, ignored), write=True)
    ctx.target = f"{kind}:{row['id']}"
    return _with_ignored(_clean_entry(row, kind), ignored)


async def update_entry(ctx: Context, args: dict[str, Any]) -> Any:
    kind = _kind(args)
    eid = _int(args, "id")
    ctx.target = f"{kind}:{eid}"
    ctx.check_write(ctx.campaign_of(ENTRY_TABLES[kind], eid))
    ignored: list[str] = []
    body = dict(_fields(args, kind, ignored))
    if args.get("base_revision") is not None:
        # Révision lue avec get_entry (audit M1, contre-revue) : refus si la fiche a changé depuis ; les appels
        # successifs de Claude partis de la révision à jour se fondent alors dans une même version de l'historique
        # (sans elle, une version par appel, qui évincerait celles des MJ de la limite de 50).
        body["base_revision"] = _int(args, "base_revision")
    row = await ctx.api("PATCH", f"/api/{ENTRY_PATHS[kind]}/{eid}", body, write=True)
    return _with_ignored(_clean_entry(row, kind), ignored)


RELATION_FIELDS = ("type", "label", "description", "directed", "secret")


async def create_relation(ctx: Context, args: dict[str, Any]) -> Any:
    cid = ctx.check_write(_int(args, "campaign_id"), "Campagne")
    body = {"source_kind": _kind(args, "source_kind", ("npc", "faction")), "source_id": _int(args, "source_id"),
            "target_kind": _kind(args, "target_kind", ("npc", "faction")), "target_id": _int(args, "target_id"),
            **{k: args[k] for k in RELATION_FIELDS if args.get(k) is not None}}
    row = await ctx.api("POST", f"/api/campaigns/{cid}/relations", body, write=True)
    ctx.target = f"relation:{row['id']}"
    return row


async def update_relation(ctx: Context, args: dict[str, Any]) -> Any:
    rid = _int(args, "relation_id")
    ctx.target = f"relation:{rid}"
    ctx.check_write(ctx.campaign_of("relations", rid), "Lien")
    body = {k: args[k] for k in RELATION_FIELDS if args.get(k) is not None}
    return await ctx.api("PATCH", f"/api/relations/{rid}", body, write=True)


async def delete_relation(ctx: Context, args: dict[str, Any]) -> Any:
    rid = _int(args, "relation_id")
    ctx.target = f"relation:{rid}"
    ctx.check_write(ctx.campaign_of("relations", rid), "Lien")
    await ctx.api("DELETE", f"/api/relations/{rid}", write=True)
    return {"deleted": rid}


def _member(ctx: Context, args: dict[str, Any]) -> tuple[str, int, int]:
    """Membre désigné par npc_id (PNJ) ou member_faction_id (sous-faction), et faction visée ; droits vérifiés."""
    faction_id = _int(args, "faction_id")
    npc_id, sub_id = _int(args, "npc_id", required=False), _int(args, "member_faction_id", required=False)
    if (npc_id is None) == (sub_id is None):
        raise ToolError("Indiquer un seul membre : npc_id (PNJ) ou member_faction_id (faction)")
    column, member_id = ("npc_id", npc_id) if npc_id is not None else ("member_faction_id", sub_id)
    ctx.target = f"{'npc' if npc_id is not None else 'faction'}:{member_id}>faction:{faction_id}"
    if npc_id is not None:
        ctx.check_write(ctx.campaign_of("npcs", npc_id), "PNJ")
    else:
        ctx.check_write(ctx.campaign_of("factions", sub_id), "Faction")
    return column, member_id, faction_id


async def set_membership(ctx: Context, args: dict[str, Any]) -> Any:
    column, member_id, faction_id = _member(ctx, args)
    body = {k: args[k] for k in ("role", "is_leader") if args.get(k) is not None}
    with ctx.transaction() as conn:
        found = conn.execute(f"SELECT id FROM memberships WHERE {column} = ? AND faction_id = ?",
                             (member_id, faction_id)).fetchone()
    if found:
        return await ctx.api("PATCH", f"/api/memberships/{found[0]}", body, write=True)
    return await ctx.api("POST", "/api/memberships", {column: member_id, "faction_id": faction_id, **body}, write=True)


async def remove_membership(ctx: Context, args: dict[str, Any]) -> Any:
    column, member_id, faction_id = _member(ctx, args)
    with ctx.transaction() as conn:
        found = conn.execute(f"SELECT id FROM memberships WHERE {column} = ? AND faction_id = ?",
                             (member_id, faction_id)).fetchone()
    if not found:
        raise ToolError("Ce PNJ n'est pas membre de cette faction" if column == "npc_id"
                        else "Cette faction n'est pas membre de cette faction")
    await ctx.api("DELETE", f"/api/memberships/{found[0]}", write=True)
    return {"removed": {column: member_id, "faction_id": faction_id}}


def _place_link(ctx: Context, args: dict[str, Any]) -> tuple[int, str, int, int | None]:
    place_id = _int(args, "place_id")
    kind = _kind(args, "entity_kind", ("npc", "faction"))
    entity_id = _int(args, "entity_id")
    ctx.target = f"place:{place_id}<{kind}:{entity_id}"
    ctx.check_write(ctx.campaign_of("places", place_id), "Lieu")
    with ctx.transaction() as conn:
        found = conn.execute("SELECT id FROM place_links WHERE place_id = ? AND entity_kind = ? AND entity_id = ?",
                             (place_id, kind, entity_id)).fetchone()
    return place_id, kind, entity_id, found[0] if found else None


async def set_place_link(ctx: Context, args: dict[str, Any]) -> Any:
    place_id, kind, entity_id, link_id = _place_link(ctx, args)
    role = str(args.get("role") or "")
    if link_id:
        if args.get("role") is None:  # sans rôle : lien déjà là, rien à changer
            with ctx.transaction() as conn:
                return dict(conn.execute("SELECT * FROM place_links WHERE id = ?", (link_id,)).fetchone())
        return await ctx.api("PATCH", f"/api/place-links/{link_id}", {"role": role}, write=True)
    return await ctx.api("POST", f"/api/places/{place_id}/links",
                         {"entity_kind": kind, "entity_id": entity_id, "role": role}, write=True)


async def remove_place_link(ctx: Context, args: dict[str, Any]) -> Any:
    place_id, kind, entity_id, link_id = _place_link(ctx, args)
    if not link_id:
        raise ToolError("Cette fiche n'est pas reliée à ce lieu")
    await ctx.api("DELETE", f"/api/place-links/{link_id}", write=True)
    return {"removed": {"place_id": place_id, "entity_kind": kind, "entity_id": entity_id}}


def _doc_summary(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: doc.get(k) for k in ("id", "campaign_id", "parent_id", "kind", "title", "status", "revision", "updated_at")}


async def create_document(ctx: Context, args: dict[str, Any]) -> Any:
    cid = ctx.check_write(_int(args, "campaign_id"), "Campagne")
    status = args.get("status")
    if status is not None and status not in get_args(DocStatus):
        raise ToolError(f"Paramètre status : une valeur parmi {', '.join(get_args(DocStatus))}")
    body = {"title": str(args.get("title") or ""), "content": str(args.get("content") or "")}
    parent_id = _int(args, "parent_id", required=False)
    if parent_id is not None:
        body["parent_id"] = parent_id
    doc = await ctx.api("POST", f"/api/campaigns/{cid}/documents", body, write=True)
    ctx.target = f"doc:{doc['id']}"
    if status is not None:
        doc = await ctx.api("PATCH", f"/api/documents/{doc['id']}", {"status": status}, write=True)
    return _doc_summary(doc)


async def update_document(ctx: Context, args: dict[str, Any]) -> Any:
    did = _int(args, "document_id")
    ctx.target = f"doc:{did}"
    ctx.check_write(ctx.campaign_of("documents", did))
    body = {k: args[k] for k in ("title", "status", "content") if args.get(k) is not None}
    if not body:
        raise ToolError("Rien à modifier : title, status ou content")
    body["base_revision"] = _int(args, "base_revision")
    if "content" in body:
        # Option retenue : remplacement d'un bloc (les MJ connectés rechargent la fiche). Refusé tant que
        # des frappes reçues par le serveur ne sont pas encore dans le HTML enregistré (elles seraient perdues ;
        # documents.update_document le revérifie dans sa transaction).
        with ctx.transaction() as conn:
            unsaved = has_unsaved_edits(conn, did)
        if unsaved:
            hub = getattr(ctx.app.state, "hub", None)
            room = hub.rooms.get(did) if hub is not None else None
            if room is not None and room.peers:
                names = ", ".join(sorted({p.name for p in room.peers.values()}))
                raise ToolError(f"La fiche est ouverte ({names}) et ses dernières modifications ne sont pas encore "
                                "enregistrées. Réessayez dans quelques secondes, puis relisez-la avec get_document.")
            raise ToolError(UNSAVED_EDITS)
    doc = await ctx.api("PATCH", f"/api/documents/{did}", body, write=True)
    return _doc_summary(doc)


async def add_comment(ctx: Context, args: dict[str, Any]) -> Any:
    did = _int(args, "document_id")
    ctx.target = f"doc:{did}"
    ctx.check_write(ctx.campaign_of("documents", did))
    body = {"body": str(args.get("body") or ""), "quote": str(args.get("quote") or "")}
    parent_id = _int(args, "parent_id", required=False)
    if parent_id is not None:
        body["parent_id"] = parent_id
    return await ctx.api("POST", f"/api/documents/{did}/comments", body, write=True)


async def add_entry_comment(ctx: Context, args: dict[str, Any]) -> Any:
    kind = _kind(args)
    eid = _int(args, "id")
    ctx.target = f"{kind}:{eid}"
    ctx.check_write(ctx.campaign_of(ENTRY_TABLES[kind], eid))
    body: dict[str, Any] = {"body": str(args.get("body") or ""), "quote": str(args.get("quote") or ""),
                            "field": str(args.get("field") or "")}
    parent_id = _int(args, "parent_id", required=False)
    if parent_id is not None:
        body["parent_id"] = parent_id
    return await ctx.api("POST", f"/api/{ENTRY_PATHS[kind]}/{eid}/comments", body, write=True)


# --- Chronologie (migration 032, routes de timeline.py) ------------------------------------------

TIMELINE_OFF = ("La chronologie n'est pas activée pour la campagne « {name} » : le MJ peut l'activer dans le menu de "
                "la campagne (les événements déjà saisis sont conservés).")


def _timeline(ctx: Context, cid: int) -> tuple[bool, str]:
    with ctx.transaction() as conn:
        row = conn.execute("SELECT timeline_enabled, name FROM campaigns WHERE id = ?", (cid,)).fetchone()
    return bool(row and row[0]), row[1] if row else str(cid)


def _event_mentions(ctx: Context, cid: int, event_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Fiches citées par chaque événement (timeline_mentions), avec leur nom."""
    if not event_ids:
        return {}
    with ctx.transaction() as conn:
        names = _names(conn, cid)
        for row in conn.execute("SELECT id, title FROM documents WHERE campaign_id = ?", (cid,)):
            names[("doc", row[0])] = row[1]
        marks = ",".join("?" * len(event_ids))
        rows = conn.execute(f"SELECT event_id, target_kind, target_id FROM timeline_mentions "
                            f"WHERE event_id IN ({marks}) ORDER BY target_kind, target_id", event_ids).fetchall()
    out: dict[int, list[dict[str, Any]]] = {}
    for event_id, kind, target in rows:
        out.setdefault(event_id, []).append({"kind": kind, "id": target, "name": names.get((kind, target))})
    return out


def _event_out(event: dict[str, Any], position: int | None, mentions: list[dict[str, Any]],
               full: bool = False) -> dict[str, Any]:
    out = {"id": event["id"], "campaign_id": event["campaign_id"], "date_label": event.get("date_label") or "",
           "title": event["title"], "tags": event.get("tags") or [], "revision": event["revision"],
           "mentions": mentions}
    if position is not None:
        out["position"] = position
    if full:
        out["description"] = event.get("description") or ""
    else:
        out["excerpt"] = _short(event.get("description"), 200)
    return out


def _event_html(ctx: Context, cid: int, text: str) -> str:
    """Description : HTML de l'éditeur tel quel ; sinon Markdown converti (comme l'interface le fait pour les champs
    longs), [[Nom]] devenant une mention de la fiche de ce nom dans la campagne."""
    if not text.strip() or is_rich_html(text):
        return text
    with ctx.transaction() as conn:
        by_name = {name.lower(): f"{kind}:{eid}" for (kind, eid), name in _names(conn, cid).items()}
    return markdown_to_html(text, by_name.get)


async def _events(ctx: Context, cid: int) -> list[dict[str, Any]]:
    return (await ctx.api("GET", f"/api/campaigns/{cid}/events"))["events"]


async def list_events(ctx: Context, args: dict[str, Any]) -> Any:
    cid = ctx.check_read(_int(args, "campaign_id"))
    event_id = _int(args, "event_id", required=False)
    limit = _int(args, "limit", required=False) or EVENTS_DEFAULT
    offset = _int(args, "offset", required=False) or 0
    if not 1 <= limit <= EVENTS_MAX or offset < 0:
        raise ToolError(f"limit entre 1 et {EVENTS_MAX}, offset positif")
    enabled, name = _timeline(ctx, cid)
    events = await _events(ctx, cid)
    if event_id is not None:
        ctx.target = f"event:{event_id}"
        found = [(i, e) for i, e in enumerate(events) if e["id"] == event_id]
        if not found:
            raise ToolError("Événement introuvable dans cette campagne")
        position, event = found[0]
        return {"enabled": enabled, "event": _event_out(event, position, _event_mentions(ctx, cid, [event_id]).get(
            event_id, []), full=True)}
    page = events[offset:offset + limit]
    mentions = _event_mentions(ctx, cid, [e["id"] for e in page])
    out: dict[str, Any] = {"enabled": enabled, "total": len(events), "offset": offset,
                           "events": [_event_out(e, offset + i, mentions.get(e["id"], [])) for i, e in enumerate(page)]}
    if offset + len(page) < len(events):
        out["next_offset"] = offset + len(page)
    if not enabled:
        out["note"] = TIMELINE_OFF.format(name=name)
    return out


def _event_body(ctx: Context, cid: int, args: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for key in ("title", "date_label"):
        if args.get(key) is not None:
            body[key] = str(args[key])
    if args.get("description") is not None:
        body["description"] = _event_html(ctx, cid, str(args["description"]))
    if args.get("tags") is not None:
        if not isinstance(args["tags"], list):
            raise ToolError("Paramètre tags : liste de textes attendue")
        body["tags"] = [str(t) for t in args["tags"]]
    return body


def _check_timeline_write(ctx: Context, cid: int) -> None:
    enabled, name = _timeline(ctx, cid)
    if not enabled:
        raise ToolError(TIMELINE_OFF.format(name=name))


async def _event_result(ctx: Context, cid: int, event_id: int) -> dict[str, Any]:
    events = await _events(ctx, cid)
    position, event = next(((i, e) for i, e in enumerate(events) if e["id"] == event_id), (None, None))
    if event is None:   # supprimé entre l'écriture et la relecture (par un MJ)
        raise ToolError("Événement introuvable dans cette campagne")
    return _event_out(event, position, _event_mentions(ctx, cid, [event_id]).get(event_id, []))


async def create_event(ctx: Context, args: dict[str, Any]) -> Any:
    cid = ctx.check_write(_int(args, "campaign_id"), "Campagne")
    _check_timeline_write(ctx, cid)
    body = _event_body(ctx, cid, args)
    if not body.get("title", "").strip():
        raise ToolError("Paramètre manquant : title")
    before_id = _int(args, "before_id", required=False)
    if before_id is not None:
        body["before_id"] = before_id
    event = await ctx.api("POST", f"/api/campaigns/{cid}/events", body, write=True)
    ctx.target = f"event:{event['id']}"
    return await _event_result(ctx, cid, event["id"])


async def update_event(ctx: Context, args: dict[str, Any]) -> Any:
    eid = _int(args, "event_id")
    ctx.target = f"event:{eid}"
    cid = ctx.check_write(ctx.campaign_of("timeline_events", eid), "Événement")
    _check_timeline_write(ctx, cid)
    body = _event_body(ctx, cid, args)
    before_id = _int(args, "before_id", required=False)
    to_end = args.get("to_end") in (True, "true", 1)
    if before_id is not None and to_end:
        raise ToolError("Indiquer before_id ou to_end, pas les deux")
    if not body and before_id is None and not to_end:
        raise ToolError("Rien à modifier : title, date_label, description, tags, before_id ou to_end")
    base_revision = _int(args, "base_revision", required=False)
    ids: list[int] | None = None
    if before_id is not None or to_end:
        # Déplacement vérifié AVANT toute écriture (revue : un before_id faux ne doit pas laisser le PATCH
        # enregistré derrière un message d'erreur).
        if before_id == eid:
            raise ToolError("before_id : un autre événement que celui qu'on déplace")
        events = await _events(ctx, cid)
        current = next((e for e in events if e["id"] == eid), None)
        if current is None:
            raise ToolError("Événement introuvable dans cette campagne")
        if not body and base_revision is not None and base_revision != current["revision"]:
            # Le déplacement seul ne passe pas par le PATCH : même contrôle de révision ici.
            raise ToolError(f"Événement modifié entre-temps (révision {current['revision']}) : relire avec list_events")
        ids = [e["id"] for e in events if e["id"] != eid]
        if before_id is not None and before_id not in ids:
            raise ToolError("before_id : événement introuvable dans cette campagne")
        ids.insert(ids.index(before_id) if before_id is not None else len(ids), eid)
    if body:
        if base_revision is not None:
            body["base_revision"] = base_revision
        await ctx.api("PATCH", f"/api/events/{eid}", body, write=True)
    if ids is not None:
        await ctx.api("PUT", f"/api/campaigns/{cid}/events/order", {"ids": ids}, write=True)
    return await _event_result(ctx, cid, eid)


# --- Suppression (niveau « lecture, écriture et suppression », migration 033) ---------------------
# Chaque outil vérifie d'abord le niveau d'accès de la campagne de l'objet (Context.check_delete), puis exige le nom
# ou le titre actuel de l'objet (garde-fou contre un identifiant mal recopié), et passe par la route REST de
# l'interface (mêmes effets : cascades, fichiers, éditeurs ouverts fermés).

def _same(args: dict[str, Any], key: str, current: str, refusal: str) -> None:
    given = args.get(key)
    if not isinstance(given, str):
        raise ToolError(f"Paramètre manquant : {key}")
    if given.strip() != (current or "").strip():
        raise ToolError(f"{refusal} ; rien n'a été supprimé.")


async def delete_entry(ctx: Context, args: dict[str, Any]) -> Any:
    kind = _kind(args)
    eid = _int(args, "id")
    table = ENTRY_TABLES[kind]
    ctx.target = f"{kind}:{eid}"
    ctx.check_delete(ctx.campaign_of(table, eid), "Fiche")
    with ctx.transaction() as conn:
        found = conn.execute(f"SELECT name FROM {table} WHERE id = ?", (eid,)).fetchone()
        children = conn.execute("SELECT COUNT(*) FROM places WHERE parent_id = ?", (eid,)).fetchone()[0] \
            if kind == "place" else 0
    if found is None:
        raise ToolError("Fiche introuvable (déjà supprimée ?)")
    name = found[0]
    ctx.target = f"{kind}:{eid} « {name} »"
    _same(args, "name", name, f"Nom différent : la fiche n°{eid} s'appelle « {name} »")
    await ctx.api("DELETE", f"/api/{ENTRY_PATHS[kind]}/{eid}", delete=True)
    message = f"{_KIND_NAMES[kind]} « {name} » supprimé{'e' if kind == 'faction' else ''}."
    if children:
        message += f" Ses sous-lieux ({children}) remontent d'un niveau."
    return {"deleted": {"kind": kind, "id": eid, "name": name}, "message": message}


async def delete_document(ctx: Context, args: dict[str, Any]) -> Any:
    did = _int(args, "document_id")
    ctx.target = f"doc:{did}"
    ctx.check_delete(ctx.campaign_of("documents", did), "Fiche")
    with ctx.transaction() as conn:
        found = conn.execute("SELECT title, kind FROM documents WHERE id = ?", (did,)).fetchone()
        pages = conn.execute(
            """WITH RECURSIVE tree(id) AS (SELECT id FROM documents WHERE parent_id = ? UNION ALL
                   SELECT d.id FROM documents d JOIN tree t ON d.parent_id = t.id)
               SELECT COUNT(*) FROM tree""", (did,)).fetchone()[0]
    if found is None:
        raise ToolError("Fiche introuvable (déjà supprimée ?)")
    title = found[0]
    ctx.target = f"doc:{did} « {title} »"
    if found[1] == "campaign":   # comme la route (et l'interface) : avant même de comparer le titre
        raise ToolError("La fiche de campagne ne peut pas être supprimée")
    _same(args, "title", title, f"Titre différent : la fiche n°{did} s'intitule « {title} »")
    await ctx.api("DELETE", f"/api/documents/{did}", delete=True)
    message = f"Fiche « {title} » supprimée"
    message += f", avec ses sous-pages ({pages})." if pages else "."
    return {"deleted": {"document_id": did, "title": title, "subpages": pages}, "message": message}


async def delete_event(ctx: Context, args: dict[str, Any]) -> Any:
    eid = _int(args, "event_id")
    ctx.target = f"event:{eid}"
    cid = ctx.check_delete(ctx.campaign_of("timeline_events", eid), "Événement")
    _check_timeline_write(ctx, cid)
    with ctx.transaction() as conn:
        found = conn.execute("SELECT title FROM timeline_events WHERE id = ?", (eid,)).fetchone()
    if found is None:
        raise ToolError("Événement introuvable (déjà supprimé ?)")
    title = found[0]
    ctx.target = f"event:{eid} « {title} »"
    _same(args, "title", title, f"Titre différent : l'événement n°{eid} s'intitule « {title} »")
    await ctx.api("DELETE", f"/api/events/{eid}", delete=True)
    return {"deleted": {"event_id": eid, "title": title}, "message": f"Événement « {title} » supprimé."}


async def delete_map(ctx: Context, args: dict[str, Any]) -> Any:
    mid = _int(args, "map_id")
    ctx.target = f"carte:{mid}"
    ctx.check_delete(ctx.campaign_of("maps", mid), "Carte")
    with ctx.transaction() as conn:
        found = conn.execute("SELECT name, (SELECT COUNT(*) FROM map_pins WHERE map_id = maps.id) FROM maps "
                             "WHERE id = ?", (mid,)).fetchone()
    if found is None:
        raise ToolError("Carte introuvable (déjà supprimée ?)")
    name, pins = found[0], found[1]
    ctx.target = f"carte:{mid} « {name} »"
    _same(args, "name", name, f"Nom différent : la carte n°{mid} s'appelle « {name} »")
    await ctx.api("DELETE", f"/api/maps/{mid}", delete=True)
    return {"deleted": {"map_id": mid, "name": name, "pins": pins},
            "message": f"Carte « {name} » supprimée, avec ses épingles ({pins}) ; les lieux restent."}


COMMENT_TABLES = {"document": ("comments", "/api/comments"), "entry": ("entry_comments", "/api/entry-comments")}


async def delete_comment(ctx: Context, args: dict[str, Any]) -> Any:
    cid = _int(args, "comment_id")
    on = _kind(args, "on", tuple(COMMENT_TABLES))
    table, path = COMMENT_TABLES[on]
    ctx.target = f"commentaire:{cid}"
    ctx.check_delete(ctx.campaign_of(table, cid), "Commentaire")
    owner = ("'doc:' || document_id" if on == "document"
             else "COALESCE('npc:' || npc_id, 'faction:' || faction_id, 'place:' || place_id)")
    with ctx.transaction() as conn:
        found = conn.execute(f"SELECT parent_id, {owner}, (SELECT COUNT(*) FROM {table} r WHERE r.parent_id = c.id) "
                             f"FROM {table} c WHERE id = ?", (cid,)).fetchone()
    if found is None:
        raise ToolError("Commentaire introuvable (déjà supprimé ?)")
    parent_id, where, replies = found
    ctx.target = f"commentaire:{cid} {where}"
    await ctx.api("DELETE", f"{path}/{cid}", delete=True)
    if parent_id is not None:
        message = "Réponse supprimée (le reste du fil est gardé)."
    else:
        message = f"Fil de commentaires supprimé{f', avec ses réponses ({replies})' if replies else ''}."
    return {"deleted": {"comment_id": cid, "on": on, "replies": 0 if parent_id is not None else replies},
            "message": message}


# --- Catalogue ---------------------------------------------------------------------------------

ID = {"type": "integer"}
ENTRY_KIND = {"type": "string", "enum": ["npc", "faction", "place"],
              "description": "npc (PNJ), faction ou place (lieu)"}
LINK_KIND = {"type": "string", "enum": ["npc", "faction"]}
_KIND_NAMES = {"npc": "PNJ", "faction": "Faction", "place": "Lieu"}


def _fields_doc() -> str:
    """Champs de create_entry et update_entry, depuis le registre (entries.py)."""
    parts = [f"{_KIND_NAMES[kind]} : " + ", ".join(f"{f.key} ({f.doc})" if f.doc else f.key
                                                    for f in entries.fields(kind) if f.key in ENTRY_FIELDS[kind]) + "."
             for kind in entries.KINDS]
    return ("Champs (tous facultatifs sauf name à la création). " + " ".join(parts)
            + " Champs longs : HTML de l'éditeur ou Markdown.")


FIELDS_DOC = _fields_doc()
COMMENT_FIELDS_DOC = " ; ".join(f"{_KIND_NAMES[kind]} : {', '.join(entries.keys(kind, comment=True))}"
                                for kind in entries.KINDS)
STATUS = {"type": "string", "enum": ["brouillon", "pret", "en_cours", "joue"]}
CONTENT = {"type": "string", "description": "HTML de l'éditeur (voir les instructions du serveur)"}
DELETE_DOC = ("Définitif : pas de corbeille, seules les sauvegardes de Maze permettent de revenir en arrière ; ne "
              "supprimer qu'à la demande du MJ. Exige que la campagne soit ouverte à Claude en « lecture, écriture et "
              "suppression » (Administration > Connecteur Claude).")

Handler = Callable[[Context, dict[str, Any]], Awaitable[Any]]


def _tool(name: str, title: str, description: str, properties: dict[str, Any], required: list[str],
          handler: Handler, write: bool = False, destructive: bool = False, delete: bool = False) -> dict[str, Any]:
    """delete : outil de suppression (niveau « lecture, écriture et suppression », vérifié par Context.check_delete
    à l'appel) ; c'est aussi un outil d'écriture, destructeur. Listé pour tous : le refus se fait à l'appel."""
    write, destructive = write or delete, destructive or delete
    return {
        "name": name, "title": title, "description": description,
        "inputSchema": {"type": "object", "properties": properties, "required": required,
                        "additionalProperties": False},
        "annotations": {"title": title, "readOnlyHint": not write, "destructiveHint": destructive,
                        "idempotentHint": not write, "openWorldHint": False},
        "_handler": handler, "_write": write, "_delete": delete,
    }


TOOLS = [
    _tool("list_campaigns", "Campagnes", "Campagnes ouvertes à Claude, avec leurs effectifs et le type d'accès.",
          {}, [], list_campaigns),
    _tool("search", "Rechercher",
          "Recherche plein texte (sans accents) dans les PNJ, factions, lieux, fiches rédigées, commentaires et "
          "événements de chronologie (kind « event »).",
          {"query": {"type": "string"}, "campaign_id": {**ID, "description": "Limiter à une campagne"}},
          ["query"], search),
    _tool("get_campaign", "Vue d'ensemble d'une campagne",
          "Arbre des fiches rédigées (fiche de campagne > scénarios > pages), PNJ (avec factions), factions (avec "
          "member_of : factions dont elle est membre), "
          "lieux (arbre), types de lieux et natures de lien ; timeline : chronologie activée ou non et nombre "
          "d'événements (list_events pour le détail). Résumés abrégés ; sur une grosse campagne, listes "
          "compactes ou tronquées (effectifs dans totals, voir note) : get_entry et search pour le détail.",
          {"campaign_id": ID}, ["campaign_id"], get_campaign),
    _tool("get_entry", "Lire une fiche", "Fiche complète d'un PNJ, d'une faction ou d'un lieu (liens, appartenances : "
          "membres PNJ et sous-factions d'une faction, factions dont elle est membre dans member_of ; lieux, fiches "
          "rédigées qui la citent, cartes de scénario ; commentaires sur demande).",
          {"kind": ENTRY_KIND, "id": ID, "include_comments": {"type": "boolean"}}, ["kind", "id"], get_entry),
    _tool("list_relations", "Liens de la campagne",
          f"Liens entre PNJ et factions (par pages : limit, {RELATIONS_DEFAULT} par défaut, au plus {RELATIONS_MAX} ; "
          "offset ; next_offset s'il en reste) et natures de lien. Filtres : kind + id (liens d'une fiche), type.",
          {"campaign_id": ID, "kind": LINK_KIND, "id": ID, "type": {"type": "string"},
           "limit": {"type": "integer", "minimum": 1, "maximum": RELATIONS_MAX}, "offset": {"type": "integer", "minimum": 0}},
          ["campaign_id"], list_relations),
    _tool("get_document", "Lire une fiche rédigée",
          "Fiche de campagne, scénario ou page : contenu HTML, révision (pour update_document), sous-pages, "
          "fiches qui y renvoient ; commentaires sur demande.",
          {"document_id": ID, "include_comments": {"type": "boolean"}}, ["document_id"], get_document),

    _tool("create_entry", "Créer une fiche", "Crée un PNJ, une faction ou un lieu. " + FIELDS_DOC,
          {"kind": ENTRY_KIND, "campaign_id": ID, "fields": {"type": "object"}}, ["kind", "campaign_id", "fields"],
          create_entry, write=True),
    _tool("update_entry", "Modifier une fiche",
          "Modifie les champs donnés d'un PNJ, d'une faction ou d'un lieu (les autres restent inchangés ; l'état "
          "précédent reste dans l'historique des versions de la fiche). base_revision (conseillé) : la révision lue "
          "avec get_entry ou renvoyée par le dernier update_entry ; refus si la fiche a changé depuis (la relire), et "
          "les modifications successives forment une seule version de l'historique. " + FIELDS_DOC,
          {"kind": ENTRY_KIND, "id": ID, "base_revision": ID, "fields": {"type": "object"}}, ["kind", "id", "fields"],
          update_entry, write=True),
    _tool("create_relation", "Créer un lien",
          "Lien entre deux PNJ / factions. type : clé d'une nature de lien de la campagne (get_campaign), "
          "« autre » par défaut ; directed : lien orienté de source vers cible ; secret : inconnu des joueurs.",
          {"campaign_id": ID, "source_kind": LINK_KIND, "source_id": ID, "target_kind": LINK_KIND, "target_id": ID,
           "type": {"type": "string"}, "label": {"type": "string"}, "description": {"type": "string"},
           "directed": {"type": "boolean"}, "secret": {"type": "boolean"}},
          ["campaign_id", "source_kind", "source_id", "target_kind", "target_id"], create_relation, write=True),
    _tool("update_relation", "Modifier un lien", "Modifie la nature, l'intitulé, la description ou les options d'un lien.",
          {"relation_id": ID, "type": {"type": "string"}, "label": {"type": "string"},
           "description": {"type": "string"}, "directed": {"type": "boolean"}, "secret": {"type": "boolean"}},
          ["relation_id"], update_relation, write=True),
    _tool("delete_relation", "Supprimer un lien", "Supprime un lien entre deux fiches (les fiches restent).",
          {"relation_id": ID}, ["relation_id"], delete_relation, write=True, destructive=True),
    _tool("set_membership", "Appartenance à une faction",
          "Rend membre d'une faction (faction_id) un PNJ (npc_id) ou une autre faction (member_faction_id, qui en "
          "devient une sous-faction ; une faction peut être membre de plusieurs factions, mais ni d'elle-même ni "
          "d'une de ses propres sous-factions : pas de cycle), ou modifie son rôle (role). is_leader : le PNJ dirige "
          "la faction (PNJ seulement).",
          {"faction_id": ID, "npc_id": ID, "member_faction_id": ID, "role": {"type": "string"},
           "is_leader": {"type": "boolean"}},
          ["faction_id"], set_membership, write=True),
    _tool("remove_membership", "Retirer d'une faction",
          "Retire d'une faction (faction_id) un PNJ (npc_id) ou une sous-faction (member_faction_id).",
          {"faction_id": ID, "npc_id": ID, "member_faction_id": ID}, ["faction_id"], remove_membership, write=True,
          destructive=True),
    _tool("set_place_link", "Relier à un lieu",
          "Relie un PNJ ou une faction à un lieu avec un rôle libre (« tenancier », « repaire »…), ou change ce rôle.",
          {"place_id": ID, "entity_kind": LINK_KIND, "entity_id": ID, "role": {"type": "string"}},
          ["place_id", "entity_kind", "entity_id"], set_place_link, write=True),
    _tool("remove_place_link", "Détacher d'un lieu", "Retire le lien entre un PNJ ou une faction et un lieu.",
          {"place_id": ID, "entity_kind": LINK_KIND, "entity_id": ID}, ["place_id", "entity_kind", "entity_id"],
          remove_place_link, write=True, destructive=True),
    _tool("create_document", "Créer une fiche rédigée",
          "Crée un scénario (sans parent_id, ou sous la fiche de campagne) ou une page (sous un scénario ou une page).",
          {"campaign_id": ID, "title": {"type": "string"}, "parent_id": ID, "content": CONTENT, "status": STATUS},
          ["campaign_id", "title"], create_document, write=True),
    _tool("update_document", "Modifier une fiche rédigée",
          "Remplace le titre, le statut et/ou tout le contenu HTML d'une fiche. base_revision : la révision lue avec "
          "get_document (refus si la fiche a changé depuis). L'état précédent reste dans l'historique de la fiche.",
          {"document_id": ID, "base_revision": ID, "title": {"type": "string"}, "status": STATUS, "content": CONTENT},
          ["document_id", "base_revision"], update_document, write=True, destructive=True),
    _tool("add_comment", "Commenter une fiche rédigée",
          "Ajoute un commentaire (signé Claude) dans le panneau Commentaires d'une fiche, ou une réponse à un fil "
          "(parent_id). quote : passage concerné, cité en tête du fil.",
          {"document_id": ID, "body": {"type": "string"}, "quote": {"type": "string"}, "parent_id": ID},
          ["document_id", "body"], add_comment, write=True),
    _tool("add_entry_comment", "Commenter une fiche PNJ, faction ou lieu",
          "Ajoute un commentaire (signé Claude) dans le panneau Commentaires d'un PNJ, d'une faction ou d'un lieu, "
          f"ou une réponse à un fil (parent_id). field : section visée ({COMMENT_FIELDS_DOC} ; vide = la fiche "
          "entière) ; "
          "quote : passage de cette section, cité en tête du fil et surligné s'il est retrouvé tel quel.",
          {"kind": ENTRY_KIND, "id": ID, "body": {"type": "string"}, "field": {"type": "string"},
           "quote": {"type": "string"}, "parent_id": ID},
          ["kind", "id", "body"], add_entry_comment, write=True),
    _tool("list_events", "Chronologie",
          "Événements de la chronologie d'une campagne, dans leur ordre (position 0 = le premier) : date_label (date en "
          "texte libre), "
          "titre, id, révision, étiquettes, extrait du texte, fiches citées (mentions). Par pages (limit, "
          f"{EVENTS_DEFAULT} par défaut, au plus {EVENTS_MAX} ; offset ; next_offset s'il en reste). event_id : un "
          "seul événement, avec sa description HTML complète (à relire avant de la remplacer avec update_event). "
          "enabled : chronologie activée ou non par le MJ (désactivée, elle n'apparaît pas dans Maze).",
          {"campaign_id": ID, "event_id": ID, "limit": {"type": "integer", "minimum": 1, "maximum": EVENTS_MAX},
           "offset": {"type": "integer", "minimum": 0}},
          ["campaign_id"], list_events),
    _tool("create_event", "Ajouter un événement",
          "Ajoute un événement à la chronologie d'une campagne (activée par le MJ). date_label : date en texte libre (« 742.M41 », "
          "« vers l'an 850 », « Séance 4 »), sans calendrier : l'ordre est celui de la liste. description : HTML de "
          "l'éditeur (mentions @, encadrés Secret ou Accroche) ou Markdown ([[Nom]] : mention de la fiche de ce nom). "
          "before_id : insérer avant cet événement ; sans lui, à la fin.",
          {"campaign_id": ID, "title": {"type": "string"}, "date_label": {"type": "string"}, "description": CONTENT,
           "tags": {"type": "array", "items": {"type": "string"}}, "before_id": ID},
          ["campaign_id", "title"], create_event, write=True),
    _tool("update_event", "Modifier un événement",
          "Modifie les champs donnés d'un événement (les autres restent inchangés ; description : remplace tout le "
          "texte, HTML ou Markdown comme create_event). base_revision (conseillé) : la révision lue avec list_events "
          "ou renvoyée par le dernier appel ; refus si l'événement a changé depuis (le relire). Déplacer : before_id "
          "(placer avant cet événement) ou to_end (le mettre à la fin). Supprimer : delete_event.",
          {"event_id": ID, "base_revision": ID, "title": {"type": "string"}, "date_label": {"type": "string"},
           "description": CONTENT, "tags": {"type": "array", "items": {"type": "string"}}, "before_id": ID,
           "to_end": {"type": "boolean"}},
          ["event_id"], update_event, write=True),

    _tool("delete_entry", "Supprimer une fiche PNJ, faction ou lieu",
          "Supprime un PNJ, une faction ou un lieu. name : le nom actuel exact de la fiche (get_entry ; casse "
          "comprise), sinon refus sans rien supprimer. Partent avec la fiche : ses liens, ses appartenances (comme "
          "membre ou comme faction : les PNJ et sous-factions membres restent, sans cette faction), ses rattachements "
          "à des lieux, sa galerie d'images, son historique et ses commentaires, ses mentions dans les fiches rédigées "
          "et la chronologie (le texte reste, sans lien), sa place sur les cartes de scénario (PNJ, faction) ; pour un "
          "lieu, ses "
          "épingles sur les cartes interactives (une carte qui le représentait reste, sans lieu) et ses sous-lieux "
          "remontent d'un niveau (rien d'autre n'est perdu). " + DELETE_DOC,
          {"kind": ENTRY_KIND, "id": ID, "name": {"type": "string"}}, ["kind", "id", "name"], delete_entry,
          delete=True),
    _tool("delete_document", "Supprimer une fiche rédigée",
          "Supprime un scénario ou une page, AVEC TOUTES SES SOUS-PAGES (get_document : children). title : le titre "
          "actuel exact (casse comprise), sinon refus sans rien supprimer. La fiche de campagne (racine) ne peut pas "
          "être supprimée. Partent aussi : versions, commentaires, illustrations qu'aucune autre fiche n'affiche ; les "
          "MJ qui l'avaient ouverte la voient fermée. " + DELETE_DOC,
          {"document_id": ID, "title": {"type": "string"}}, ["document_id", "title"], delete_document, delete=True),
    _tool("delete_event", "Supprimer un événement",
          "Supprime un événement de la chronologie (activée par le MJ). title : le titre actuel exact (list_events ; "
          "casse comprise), sinon refus sans rien supprimer. Les fiches citées restent. " + DELETE_DOC,
          {"event_id": ID, "title": {"type": "string"}}, ["event_id", "title"], delete_event, delete=True),
    _tool("delete_map", "Supprimer une carte interactive",
          "Supprime une carte interactive : son fond et toutes ses épingles ; les lieux restent. name : le nom "
          "actuel exact de la carte (list_maps ; casse comprise), sinon refus sans rien supprimer. " + DELETE_DOC,
          {"map_id": ID, "name": {"type": "string"}}, ["map_id", "name"], delete_map, delete=True),
    _tool("delete_comment", "Supprimer un commentaire",
          "Supprime un commentaire : on=document pour une fiche rédigée (get_document avec include_comments), "
          "on=entry pour un PNJ, une faction ou un lieu (get_entry avec include_comments). Un fil part avec toutes "
          "ses réponses ; une réponse seule laisse le reste du fil. Seulement les commentaires signés Claude ou "
          "écrits par l'administrateur qui a autorisé Claude (les autres sont refusés). Fiche rédigée : le passage "
          "surligné (<span data-comment>) reste dans le texte, à retirer avec update_document si besoin. "
          + DELETE_DOC,
          {"comment_id": ID, "on": {"type": "string", "enum": list(COMMENT_TABLES)}}, ["comment_id", "on"],
          delete_comment, delete=True),
]
from . import mcp_images, mcp_maps  # noqa: E402 - outils d'images et de cartes, qui s'appuient sur ce qui précède

TOOLS += mcp_images.TOOLS + mcp_maps.TOOLS
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}
PUBLIC_TOOLS = [{k: v for k, v in t.items() if not k.startswith("_")} for t in TOOLS]


# --- Protocole ---------------------------------------------------------------------------------

def _rpc_error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _text_result(data: Any, is_error: bool = False) -> dict[str, Any]:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


async def _call_tool(request: Request, user: dict[str, Any], tool: dict[str, Any],
                     args: dict[str, Any]) -> dict[str, Any]:
    name = tool["name"]

    def load(conn: sqlite3.Connection) -> tuple[bool, Context]:
        return (connector.load_settings_row(conn)["enabled"],
                Context(app=request.app, user=user, grants=connector.grants(conn)))
    enabled, ctx = await in_tx(request, load, write=False)
    if not enabled:
        return _text_result(SUSPENDED, True)
    try:
        result = await tool["_handler"](ctx, args)
        ok, detail = True, ""
        out = ({"content": result.content, "isError": False} if isinstance(result, Blocks)
               else _text_result(result))
        size = sum(len(b.get("text", "")) for b in out["content"])
        if size > RESULT_MAX:
            ok, detail = False, f"Résultat trop volumineux ({size} caractères)"
            out = _text_result(f"{detail} : préciser la demande (pagination, filtre, fiche par fiche).", True)
    except ToolError as exc:
        ok, detail, out = False, str(exc), _text_result(str(exc), True)
    except sqlite3.OperationalError as exc:
        if not db.is_busy(exc):
            log.exception("Outil MCP %s", name)
            ok, detail, out = False, "Erreur interne", _text_result(
                "Erreur interne de Maze (voir le journal du serveur)", True)
        else:   # verrou d'écriture toujours pris (import, restauration…) : Claude peut réessayer
            log.warning("Outil MCP %s : base occupée (%s)", name, exc)
            ok, detail, out = False, db.BUSY_MESSAGE, _text_result(db.BUSY_MESSAGE, True)
    except Exception:  # noqa: BLE001 - une panne d'un outil ne doit pas couper la connexion de Claude
        log.exception("Outil MCP %s", name)
        ok, detail, out = False, "Erreur interne", _text_result("Erreur interne de Maze (voir le journal du serveur)", True)
    if ok and tool["_write"]:
        log.info("Claude (%s) : %s %s", user["username"], name, ctx.target)
    # Journal : attente ordinaire du verrou après une écriture ; après une lecture, courte, et entrée sautée si la base
    # reste occupée (Claude a déjà sa réponse ; un import ou une restauration en cours ne la retarde pas de 15 s).
    path = settings(request).db_path
    timeout = None if tool["_write"] else db.LOOP_TIMEOUT * 4

    def journal() -> None:
        with db.transaction(path, timeout=timeout) as conn:
            connector.log_action(conn, user["id"], name, ctx.campaign_id, ctx.target, ok, detail)
    try:
        await db.run_db(journal)
    except sqlite3.OperationalError as exc:
        if not db.is_busy(exc):
            raise
        log.warning("Journal du connecteur : base occupée, %s %s non journalisé", name, ctx.target)
    return out


async def _handle(request: Request, user: dict[str, Any], message: Any) -> dict[str, Any] | None:
    if isinstance(message, dict) and "method" not in message and ("result" in message or "error" in message):
        return None  # réponse du client (aucune requête n'est envoyée par ce serveur) : 202 sans corps
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _rpc_error(message.get("id") if isinstance(message, dict) else None, -32600, "Requête invalide")
    rid, method = message.get("id"), message["method"]
    params = message.get("params") or {}
    if "id" not in message:  # notification (initialized, cancelled…) : pas de réponse
        return None
    if not isinstance(params, dict):
        return _rpc_error(rid, -32602, "Paramètres invalides")
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "maze", "title": "Maze", "version": settings(request).version},
            "instructions": INSTRUCTIONS,
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": PUBLIC_TOOLS}}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        tool = TOOLS_BY_NAME.get(name) if isinstance(name, str) else None
        if tool is None:
            return _rpc_error(rid, -32602, f"Outil inconnu : {name}")
        if not isinstance(args, dict):
            return _rpc_error(rid, -32602, "Arguments invalides")
        return {"jsonrpc": "2.0", "id": rid, "result": await _call_tool(request, user, tool, args)}
    return _rpc_error(rid, -32601, f"Méthode inconnue : {method}")


def _unauthorized(request: Request, error: str | None) -> Response:
    base = connector.public_base(request)
    challenge = f'Bearer realm="Maze", resource_metadata="{base}/.well-known/oauth-protected-resource/mcp"'
    if error:
        challenge += f', error="{error}"'
    return JSONResponse({"detail": "Authentification requise"}, status_code=401,
                        headers={"WWW-Authenticate": challenge})


async def endpoint(request: Request) -> Response:
    if request.method != "POST":
        # Pas de flux SSE ni de session côté serveur (transport sans état).
        return Response(status_code=405, headers={"Allow": "POST"})
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return _unauthorized(request, None)
    access = header[7:].strip()
    # Jeton vérifié en lecture seule (sans attendre un verrou d'écriture), date d'usage écrite à part, au mieux.
    user = await in_tx(request, lambda conn: connector.authenticate(conn, access), write=False)
    if user is None:
        return _unauthorized(request, "invalid_token")
    path = settings(request).db_path

    def touch() -> None:
        with db.transaction(path, timeout=db.LOOP_TIMEOUT) as conn:
            connector.touch_token(conn, access)
    try:
        await db.run_db(touch)
    except sqlite3.OperationalError as exc:
        if not db.is_busy(exc):
            raise

    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            return JSONResponse(_rpc_error(None, -32600, "Requête trop volumineuse"), status_code=413)
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        message = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):   # imbrication trop profonde : 400 (audit PERF-14)
        return JSONResponse(_rpc_error(None, -32700, "JSON invalide"), status_code=400)

    if isinstance(message, list):  # lot (versions 2025-03-26 et antérieures)
        if not message:
            return JSONResponse(_rpc_error(None, -32600, "Lot vide"), status_code=400)
        if len(message) > MAX_BATCH:
            return JSONResponse(_rpc_error(None, -32600, f"Lot trop grand ({MAX_BATCH} messages au plus)"),
                                status_code=400)
        replies = [r for r in [await _handle(request, user, m) for m in message] if r is not None]
        return JSONResponse(replies) if replies else Response(status_code=202)
    reply = await _handle(request, user, message)
    return JSONResponse(reply) if reply is not None else Response(status_code=202)


routes = [Route("/mcp", endpoint, methods=["GET", "POST", "DELETE"])]
