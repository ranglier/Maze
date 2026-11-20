"""Serveur MCP de Maze (connecteur Claude) : POST /mcp, transport « Streamable HTTP » sans état.

Chaque requête porte un jeton OAuth (oauth.py). Les outils passent par les routes de l'API REST,
appelées en interne avec l'identité de Claude (connector.acting_user) : mêmes validations, mêmes droits,
mêmes effets (index de recherche, historique, éditeurs ouverts rechargés) que depuis l'interface.

Jeu d'outils regroupé (novembre 2026, lot connecteur-outils) : list_campaigns, get_campaign, search et
update_document ici ; list, get, view et les outils d'écriture par lots (create, update, link, unlink, delete,
add_image, edit_image) dans mcp_tools.py. Les fonctions de ce module (create_entry, update_entry, set_membership,
delete_entry…) ne sont plus des outils : ce sont les traitements d'UN élément, appelés par mcp_tools.
Écriture : campagnes ouvertes en écriture (lecture et écriture, ou lecture, écriture et suppression) ; suppression
de PNJ, factions, lieux, fiches rédigées, événements, cartes et commentaires : seulement les campagnes ouvertes en
lecture, écriture et suppression (migration 033).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field as dc_field, replace
from typing import Any, get_args
from urllib.parse import urlencode

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, connector, db, docsections, entries, mcp_guide
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
# caractères refusés en recette). Grosses campagnes (684 PNJ, 427 factions en recette) : get_campaign résume
# (arbres abrégés), ses sections détaillées et les listes (outil list) sont paginées et chaque page est raccourcie
# pour tenir dans ce budget ; le détail passe par get, list et search.
RESULT_BUDGET = 40_000    # caractères visés pour get_campaign et une page de liste
RESULT_MAX = 80_000       # au-delà, résultat refusé avec un message (plutôt qu'un échec du client)
EVENTS_DEFAULT, EVENTS_MAX = 100, 200
SECTIONS = ("summary", "npcs", "factions", "places", "documents")
SECTION_DEFAULT, SECTION_MAX = 100, 200
# Part du budget de la vue d'ensemble pour chaque arbre abrégé (réduite si l'ensemble dépasse RESULT_BUDGET).
TREE_BUDGETS = {"documents": 8_000, "factions": 9_000, "places": 9_000, "npcs": 6_000}
ENTRY_PATHS = {"npc": "npcs", "faction": "factions", "place": "places"}
ENTRY_TABLES = {"npc": "npcs", "faction": "factions", "place": "places"}
# Niveaux d'accès d'une campagne (mcp_grants.mode) : delete permet tout ce que permet write (migration 033).
WRITE_MODES = ("write", "delete")
ACCESS_LABELS = {"read": "lecture seule", "write": "lecture et écriture", "delete": "lecture, écriture et suppression"}
SUSPENDED = ("L'accès de Claude à Maze est suspendu. Un administrateur peut le réactiver dans "
             "Administration > Connecteur Claude.")

# Instructions du serveur (réponse à initialize), lues par Claude à chaque conversation : courtes (moins de 3 500
# caractères, tests.test_connector_guide), chaque outil nommé ; le détail est dans le guide (mcp_guide.py, maze://guide).
INSTRUCTIONS = """Maze : campagnes de jeu de rôle d'un MJ (PNJ, factions, lieux, fiches rédigées dont les scénarios, cartes, chronologie). Guide détaillé : ressource maze://guide.
Lire : list_campaigns (campagnes ouvertes, niveau d'accès), get_campaign (vue d'ensemble, section summary par défaut), search (plein texte), list (un kind ; filtres query, faction_id, place_id, parent_id, status, tag, type, entity ; offset, limit), get (un élément complet, avec sa révision), view (kind image, document_image, library_image, ou map : carte quadrillée tous les 10 %).
Écrire (campagne en lecture et écriture) : create, update, link / unlink (membership, place, pin), delete, add_image, edit_image prennent items, de 1 à 100 éléments, chacun traité à part ; résultat par élément {index, ok, id, error} : regrouper en un appel, puis reprendre seulement les éléments en erreur. Vérifier avec search avant de créer.
update : id et seulement les champs changés, avec base_revision (de get ou du dernier update) ; refus si l'élément a changé : relire, ne pas écraser. Types de lieux et natures de lien : clés de get_campaign.
update_document (fiche rédigée) : lire d'abord avec get kind document (outline : ses titres) ; base_revision obligatoire. Préférer operations à content, qui remplace tout : replace_section, insert_after_section, insert_before_section, delete_section (heading : titre <h1>-<h3> ; occurrence si plusieurs l'ont), append, prepend, replace_text (find, replace, heading facultatif). Version « Avant modification par Claude » gardée.
HTML de l'éditeur : <p>, <h1>-<h3>, <strong>, <em>, <u>, <ul>/<ol>/<li>, <blockquote>, <hr>, <table><tr><th>/<td> (paragraphes dans les cellules), <a href>. Mention : <a class="mention" data-mention="npc:12">Nom</a> (npc, faction, place, doc). Encadrés : <div data-callout="read"><p>…</p></div> (Accroche, à lire aux joueurs), data-callout="secret". Alignement : data-align="center|right|justify" sur <p>, <h1>-<h3>. Garder tels quels <span data-comment> (commentaires) et <img data-doc-image> (images). Champs longs des fiches : même HTML, ou Markdown.
Images : add_image (source url publique, library_image_id ou image_id ; target npc:12, faction:3, place:7, doc:5, ou library ; pour doc:5, insérer ensuite le <img> rendu avec update_document), edit_image (pivoter, retourner, recadrer). Image sur l'ordinateur du MJ : request_upload (target, target_id), donner le lien au MJ (10 min, usage unique), attendre qu'il l'ait déposée, puis request_upload avec ticket seul.
Commentaire : create kind comment (target doc:5 ou npc:12…, body, quote).
Chronologie : module facultatif, activé par campagne (get_campaign l'indique) ; date en texte libre, ordre manuel (before_id).
delete : seulement à la demande du MJ ; items [{id, name}] (nom ou titre exact ; id seul pour relation, image, library_image), [{id, on}] pour un commentaire (on : document ou entry). PNJ, factions, lieux, fiches rédigées, événements, cartes et commentaires exigent l'accès « lecture, écriture et suppression » ; les six premiers vont 30 jours à la corbeille (trash_id, restaurables dans Maze) ; commentaires, liens et images : définitif.
Résultats longs : paginer ou filtrer. Secrets (champ secrets, encadrés Secret, liens secrets) : jamais dans un texte pour les joueurs.
Répondre au MJ dans sa langue (français par défaut) ; valeurs internes (statuts, clés) telles quelles."""


class ToolError(Exception):
    """Erreur montrée à Claude (résultat d'outil isError), pas une erreur de protocole."""


@dataclass
class Blocks:
    """Résultat d'outil composé de blocs MCP (texte et images) plutôt que d'un seul texte JSON."""
    content: list[dict[str, Any]]


@dataclass
class Batch:
    """Résultat d'un outil d'écriture par lots : un résultat et une ligne de journal par élément
    (journal : campagne, cible, réussite, détail de l'erreur)."""
    results: list[dict[str, Any]]
    log: list[tuple[int | None, str, bool, str]] = dc_field(default_factory=list)


@dataclass
class Context:
    app: Any
    user: dict[str, Any]              # administrateur au nom duquel Claude agit
    grants: dict[int, str]            # campagnes ouvertes : {id: "read" | "write" | "delete"}
    campaign_id: int | None = None    # campagne concernée par l'appel (journal)
    target: str = ""
    base: str = ""                    # adresse publique de Maze (connector.public_base : liens de request_upload)

    def fresh(self) -> Context:
        """Même identité et mêmes accès, journal vierge : un contexte par élément d'un lot."""
        return replace(self, campaign_id=None, target="")

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


def _fold(text: Any) -> str:
    """Texte sans accents ni casse, pour les filtres des listes (query, tag, type)."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _size(data: Any) -> int:
    return len(json.dumps(data, ensure_ascii=False))


def _paging(args: dict[str, Any], default: int, maximum: int) -> tuple[int, int]:
    limit = _int(args, "limit", required=False)
    limit = default if limit is None else limit
    offset = _int(args, "offset", required=False) or 0
    if not 1 <= limit <= maximum or offset < 0:
        raise ToolError(f"limit entre 1 et {maximum}, offset positif ou nul")
    return offset, limit


def _page(items: list[Any], offset: int, limit: int, budget: int = RESULT_BUDGET) -> dict[str, Any]:
    """Page demandée d'une liste, raccourcie s'il le faut pour tenir dans le budget ; next_offset s'il en reste."""
    page = items[offset:offset + limit]
    shortened = len(page) > 1 and _size(page) > budget
    if shortened:   # plus longue page qui tient (au moins un élément)
        low, high = 1, len(page) - 1
        while low < high:
            mid = (low + high + 1) // 2
            low, high = (mid, high) if _size(page[:mid]) <= budget else (low, mid - 1)
        page = page[:low]
    out: dict[str, Any] = {"total": len(items), "offset": offset, "items": page}
    if offset + len(page) < len(items):
        out["next_offset"] = offset + len(page)
    if shortened:
        out["note"] = (f"Page raccourcie à {len(page)} éléments pour tenir dans la taille d'un résultat : "
                       "continuer avec offset=next_offset.")
    return out


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
    section = args.get("section") or "summary"
    if section not in SECTIONS:
        raise ToolError(f"Paramètre section : une valeur parmi {', '.join(SECTIONS)}")
    campaign = await ctx.api("GET", f"/api/campaigns/{cid}")
    docs = await ctx.api("GET", f"/api/campaigns/{cid}/documents")   # crée la fiche de campagne si besoin
    if section != "summary":
        offset, limit = _paging(args, SECTION_DEFAULT, SECTION_MAX)

        def load_section() -> list[dict[str, Any]]:
            with ctx.transaction() as conn:
                return _section_items(conn, cid, section, docs)
        items = await run_in_threadpool(load_section)
        return {"campaign_id": cid, "section": section, **_page(items, offset, limit)}

    def load() -> dict[str, Any]:
        with ctx.transaction() as conn:
            return _summary(conn, cid, docs)
    data = await run_in_threadpool(load)
    head = {
        "campaign": {"id": cid, "name": campaign["name"], "description": _short(campaign["description"], 600),
                     "access": ACCESS_LABELS[ctx.grants[cid]]},
        "totals": data["totals"],
        "npc_statuses": data["npc_statuses"],
        "place_kinds": data["place_kinds"],
        "relation_types": data["relation_types"],
        # Chronologie (migration 032) : activée ou non, et nombre d'événements ; list (kind event) pour le détail.
        "timeline": data["timeline"],
        "tags": data["tags"],
    }
    budgets = dict(TREE_BUDGETS)
    while True:
        out, cut = dict(head), []
        for key in ("documents", "factions", "places"):
            tree, shown, more = _tree(data["nodes"][key], data["kids"][key], data["roots"][key], budgets[key])
            out[key] = {"root_id": docs["root_id"], "tree": tree} if key == "documents" else tree
            if more:
                out[f"{key}_more"] = more
            total = len(data["nodes"][key])
            if shown < total:
                cut.append(f"{key} {shown} sur {total}")
        npcs = data["npcs"]
        if npcs and _size(npcs) <= budgets["npcs"]:
            out["npcs"] = npcs
        elif npcs:
            cut.append(f"npcs 0 sur {len(npcs)}")
        out["note"] = _summary_note(cut)
        if _size(out) <= RESULT_BUDGET or budgets["places"] < 500:
            return out
        budgets = {k: v * 2 // 3 for k, v in budgets.items()}


def _summary_note(cut: list[str]) -> str:
    note = ("Vue d'ensemble. Arbres : sub = sous-éléments ; more = sous-éléments non affichés ; <clé>_more = éléments "
            "de premier niveau non affichés. Détail : get_campaign avec section (npcs, factions, places, documents ; "
            "paginé avec offset et limit), list pour une liste filtrée (membres d'une faction, fiches liées à un lieu, "
            "sous-lieux, étiquette…), get pour une fiche, search pour retrouver un nom ou un passage.")
    if cut:
        note += f" Campagne volumineuse, listes abrégées ({', '.join(cut)})."
    return note


def _tree(nodes: dict[int, dict[str, Any]], kids: dict[int, list[int]], roots: list[int],
          budget: int) -> tuple[list[dict[str, Any]], int, int]:
    """Arbre abrégé, en largeur d'abord (les premiers niveaux d'abord) jusqu'au budget de caractères : un nœud dont
    des enfants ne sont pas affichés porte more (leur nombre). Un nœud à plusieurs parents (sous-faction membre de
    plusieurs factions) n'apparaît que sous le premier. Renvoie l'arbre, le nombre de nœuds affichés et le nombre de
    racines non affichées."""
    out: list[dict[str, Any]] = []
    queue: list[tuple[int, dict[str, Any] | None]] = [(r, None) for r in roots]
    seen: set[int] = set()
    used = shown = root_more = 0
    full = False
    for node_id, parent in queue:   # la file grandit pendant le parcours
        if node_id in seen:
            continue
        seen.add(node_id)
        node = dict(nodes[node_id])
        cost = _size(node) + 12
        if full or used + cost > budget:
            full = True
            if parent is None:
                root_more += 1
            else:
                parent["more"] = parent.get("more", 0) + 1
            continue
        used += cost
        shown += 1
        (out if parent is None else parent.setdefault("sub", [])).append(node)
        queue.extend((child, node) for child in kids.get(node_id, []))
    return out, shown, root_more


def _summary(conn, cid: int, docs: dict[str, Any]) -> dict[str, Any]:
    """Données de la vue d'ensemble : effectifs, types, étiquettes et nœuds des arbres (documents, factions, lieux)."""
    count = lambda sql: conn.execute(sql, (cid,)).fetchone()[0]  # noqa: E731
    npcs = db.rows_to_dicts(conn.execute(
        "SELECT id, name, status, tags FROM npcs WHERE campaign_id = ? ORDER BY sort_order, name COLLATE NOCASE",
        (cid,)).fetchall())
    factions = db.rows_to_dicts(conn.execute(
        "SELECT id, name, tags FROM factions WHERE campaign_id = ? ORDER BY sort_order, name COLLATE NOCASE",
        (cid,)).fetchall())
    places = db.rows_to_dicts(conn.execute(
        "SELECT id, parent_id, name, kind, tags FROM places WHERE campaign_id = ? ORDER BY sort_order, name COLLATE NOCASE",
        (cid,)).fetchall())
    members: dict[int, int] = {}
    parents: dict[int, list[int]] = {}
    for m in conn.execute("""SELECT m.npc_id, m.member_faction_id, m.faction_id FROM memberships m
                               JOIN factions f ON f.id = m.faction_id WHERE f.campaign_id = ? ORDER BY m.id""", (cid,)):
        if m["npc_id"] is not None:
            members[m["faction_id"]] = members.get(m["faction_id"], 0) + 1
        else:
            parents.setdefault(m["member_faction_id"], []).append(m["faction_id"])
    kind_counts: dict[str, int] = {}
    for p in places:
        kind_counts[p["kind"]] = kind_counts.get(p["kind"], 0) + 1
    statuses: dict[str, int] = {}
    for n in npcs:
        statuses[n["status"]] = statuses.get(n["status"], 0) + 1
    tags: dict[str, int] = {}
    for row in (*npcs, *factions, *places):
        for tag in row.get("tags") or []:
            tags[tag] = tags.get(tag, 0) + 1
    timeline = conn.execute("SELECT timeline_enabled FROM campaigns WHERE id = ?", (cid,)).fetchone()

    order = {f["id"]: i for i, f in enumerate(factions)}
    faction_kids: dict[int, list[int]] = {}
    for child, of in parents.items():
        for parent in of:
            faction_kids.setdefault(parent, []).append(child)
    for ids in faction_kids.values():
        ids.sort(key=lambda i: order.get(i, 0))
    place_kids: dict[int, list[int]] = {}
    for p in places:
        if p["parent_id"] is not None:
            place_kids.setdefault(p["parent_id"], []).append(p["id"])
    documents = [d for d in docs["documents"] if d["kind"] != "campaign"]
    doc_kids: dict[int, list[int]] = {}
    for d in documents:
        doc_kids.setdefault(d["parent_id"], []).append(d["id"])
    known = {p["id"] for p in places}
    return {
        "totals": {
            "npcs": len(npcs), "factions": len(factions), "places": len(places), "documents": len(documents),
            "relations": count("SELECT COUNT(*) FROM relations WHERE campaign_id = ?"),
            "events": count("SELECT COUNT(*) FROM timeline_events WHERE campaign_id = ?"),
            "maps": count("SELECT COUNT(*) FROM maps WHERE campaign_id = ?"),
            "library_images": count("SELECT COUNT(*) FROM library_images WHERE campaign_id = ?"),
            "comments": count("SELECT COUNT(*) FROM comments c JOIN documents d ON d.id = c.document_id "
                              "WHERE d.campaign_id = ?")
                        + count("SELECT COUNT(*) FROM entry_comments WHERE campaign_id = ?"),
        },
        "npc_statuses": statuses,
        "place_kinds": [{"key": k["key"], "label": k["label"], "places": kind_counts.get(k["key"], 0)}
                        for k in conn.execute("SELECT key, label FROM place_kinds WHERE campaign_id = ? "
                                              "ORDER BY sort_order, id", (cid,))],
        "relation_types": db.rows_to_dicts(conn.execute(
            "SELECT key, label, directed, hint FROM relation_types WHERE campaign_id = ? ORDER BY sort_order, id",
            (cid,)).fetchall()),
        "timeline": {"enabled": bool(timeline[0]),
                     "events": count("SELECT COUNT(*) FROM timeline_events WHERE campaign_id = ?")},
        "tags": [{"tag": k, "count": v} for k, v in sorted(tags.items(), key=lambda kv: (-kv[1], _fold(kv[0])))[:40]],
        "npcs": [{"id": n["id"], "name": n["name"]} for n in npcs],
        "nodes": {
            "documents": {d["id"]: {"id": d["id"], "title": d["title"], "status": d["status"]} for d in documents},
            "factions": {f["id"]: {"id": f["id"], "name": f["name"],
                                   **({"npcs": members[f["id"]]} if f["id"] in members else {})} for f in factions},
            "places": {p["id"]: {"id": p["id"], "name": p["name"], "kind": p["kind"]} for p in places},
        },
        "kids": {"documents": doc_kids, "factions": faction_kids, "places": place_kids},
        "roots": {
            "documents": doc_kids.get(docs["root_id"], []),
            "factions": [f["id"] for f in factions if f["id"] not in parents],
            "places": [p["id"] for p in places if p["parent_id"] is None or p["parent_id"] not in known],
        },
    }


def _section_items(conn, cid: int, section: str, docs: dict[str, Any]) -> list[dict[str, Any]]:
    """Liste détaillée d'une section de get_campaign (résumés abrégés, appartenances)."""
    if section == "documents":
        return [{k: d[k] for k in ("id", "parent_id", "kind", "title", "status")} for d in docs["documents"]]
    if section == "places":
        return [{**p, "summary": _short(p["summary"])} for p in db.rows_to_dicts(conn.execute(
            "SELECT id, parent_id, name, kind, summary, tags FROM places WHERE campaign_id = ? "
            "ORDER BY sort_order, name COLLATE NOCASE", (cid,)).fetchall())]
    members: dict[int, list[dict[str, Any]]] = {}
    parents: dict[int, list[dict[str, Any]]] = {}
    for m in conn.execute("""SELECT m.npc_id, m.member_faction_id, m.faction_id, m.role, m.is_leader
                               FROM memberships m JOIN factions f ON f.id = m.faction_id
                              WHERE f.campaign_id = ? ORDER BY m.id""", (cid,)):
        if m["npc_id"] is not None:
            members.setdefault(m["npc_id"], []).append(
                {"faction_id": m["faction_id"], "role": m["role"], "leader": bool(m["is_leader"])})
        else:   # sous-faction (migration 031)
            parents.setdefault(m["member_faction_id"], []).append({"faction_id": m["faction_id"], "role": m["role"]})
    if section == "npcs":
        return [{**n, "summary": _short(n["summary"]), "factions": members.get(n["id"], [])}
                for n in db.rows_to_dicts(conn.execute(
                    "SELECT id, name, title, status, summary, tags FROM npcs WHERE campaign_id = ? "
                    "ORDER BY sort_order, name COLLATE NOCASE", (cid,)).fetchall())]
    return [{**f, "summary": _short(f["summary"]), "member_of": parents.get(f["id"], [])}
            for f in db.rows_to_dicts(conn.execute(
                "SELECT id, name, kind, summary, tags FROM factions WHERE campaign_id = ? "
                "ORDER BY sort_order, name COLLATE NOCASE", (cid,)).fetchall())]


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
    # Titres visés par update_document (operations) ; calcul hors de la boucle d'événements (HTML quelconque).
    out["outline"], note = await run_in_threadpool(docsections.describe, doc.get("content") or "")
    if note:
        out["outline_note"] = note
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
    operations = args.get("operations")
    if operations is not None and "content" in body:
        raise ToolError("content et operations s'excluent : content remplace tout le contenu, operations le modifie "
                        "par endroits")
    if not body and operations is None:
        raise ToolError("Rien à modifier : title, status, content ou operations")
    body["base_revision"] = _int(args, "base_revision")
    if "content" in body or operations is not None:
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
                                "enregistrées. Réessayez dans quelques secondes, puis relisez-la avec get (kind document).")
            raise ToolError(UNSAVED_EDITS)
    changes = None
    if operations is not None:
        # Opérations appliquées au HTML enregistré (docsections.py), puis remplacement normal : mêmes versions, même
        # contrôle de base_revision dans la transaction de la route (une écriture entre-temps donne un refus).
        current = await ctx.api("GET", f"/api/documents/{did}")
        if current["revision"] != body["base_revision"]:
            raise ToolError(f"Cette fiche a été modifiée depuis sa lecture (révision actuelle {current['revision']}) : "
                            "la relire avec get (kind document) avant de la modifier.")
        try:
            body["content"], changes = await run_in_threadpool(docsections.apply_operations,
                                                               current.get("content") or "", operations)
        except docsections.SectionError as exc:
            raise ToolError(f"Rien n'est modifié. {exc}") from None
    doc = await ctx.api("PATCH", f"/api/documents/{did}", body, write=True)
    out = _doc_summary(doc)
    if changes is not None:
        out["changes"] = changes
    return out


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
            raise ToolError(f"Événement modifié entre-temps (révision {current['revision']}) : le relire avec get "
                             "(kind event)")
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

def _trash_id(response: Any) -> int | None:
    """Corbeille (migration 034) : les routes DELETE des PNJ, factions, lieux, fiches rédigées, événements et cartes
    répondent {"trash_id": …} ; une réponse sans corps (204) reste lue comme une suppression définitive."""
    return response.get("trash_id") if isinstance(response, dict) else None


def _gone(trash: int | None, feminine: bool) -> str:
    if trash is not None:
        return f"placé{'e' if feminine else ''} dans la corbeille (30 jours, restaurable par le MJ)"
    return f"supprimé{'e' if feminine else ''}"


def _deleted(out: dict[str, Any], trash: int | None) -> dict[str, Any]:
    if trash is not None:
        out["trash_id"] = trash
    return out


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
    trash = _trash_id(await ctx.api("DELETE", f"/api/{ENTRY_PATHS[kind]}/{eid}", delete=True))
    message = f"{_KIND_NAMES[kind]} « {name} » {_gone(trash, kind == 'faction')}."
    if children:
        message += f" Ses sous-lieux ({children}) remontent d'un niveau (rendus au lieu s'il est restauré)."
    return _deleted({"deleted": {"kind": kind, "id": eid, "name": name}, "message": message}, trash)


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
    trash = _trash_id(await ctx.api("DELETE", f"/api/documents/{did}", delete=True))
    message = f"Fiche « {title} » {_gone(trash, True)}"
    message += f", avec ses sous-pages ({pages})." if pages else "."
    return _deleted({"deleted": {"document_id": did, "title": title, "subpages": pages}, "message": message}, trash)


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
    trash = _trash_id(await ctx.api("DELETE", f"/api/events/{eid}", delete=True))
    return _deleted({"deleted": {"event_id": eid, "title": title},
                     "message": f"Événement « {title} » {_gone(trash, False)}."}, trash)


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
    trash = _trash_id(await ctx.api("DELETE", f"/api/maps/{mid}", delete=True))
    return _deleted({"deleted": {"map_id": mid, "name": name, "pins": pins},
                     "message": f"Carte « {name} » {_gone(trash, True)}, avec ses épingles ({pins}) ; les lieux "
                                "restent."}, trash)


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
Handler = Callable[[Context, dict[str, Any]], Awaitable[Any]]


def _tool(name: str, title: str, description: str, properties: dict[str, Any], required: list[str],
          handler: Handler, write: bool = False, destructive: bool = False, delete: bool = False,
          idempotent: bool | None = None) -> dict[str, Any]:
    """delete : outil de suppression (droits vérifiés élément par élément à l'appel, Context.check_delete ou
    check_write selon le type) ; c'est aussi un outil d'écriture, destructeur. Listé pour tous."""
    write, destructive = write or delete, destructive or delete
    return {
        "name": name, "title": title, "description": description,
        "inputSchema": {"type": "object", "properties": properties, "required": required,
                        "additionalProperties": False},
        "annotations": {"title": title, "readOnlyHint": not write, "destructiveHint": destructive,
                        "idempotentHint": (not write) if idempotent is None else idempotent, "openWorldHint": False},
        "_handler": handler, "_write": write, "_delete": delete,
    }


TOOLS = [
    _tool("list_campaigns", "Campagnes", "Campagnes ouvertes à Claude, avec leurs effectifs et le type d'accès.",
          {}, [], list_campaigns),
    _tool("get_campaign", "Vue d'ensemble d'une campagne",
          "Vue d'ensemble, toujours sous 40 000 caractères. section=summary (par défaut) : effectifs (totals), statuts "
          "des PNJ, types de lieux et natures de lien (clés à employer), chronologie activée ou non, étiquettes "
          "fréquentes, arbres abrégés des fiches rédigées, des factions (sous-factions dans sub, npcs : nombre de PNJ "
          "membres) et des lieux, et liste des PNJ si elle tient. section=npcs | factions | places | documents : liste "
          f"détaillée (résumés abrégés, appartenances), paginée (offset ; limit, {SECTION_DEFAULT} par défaut, au "
          f"plus {SECTION_MAX} ; next_offset s'il en reste). Pour filtrer : list ; une fiche : get.",
          {"campaign_id": ID, "section": {"type": "string", "enum": list(SECTIONS)},
           "offset": {"type": "integer", "minimum": 0},
           "limit": {"type": "integer", "minimum": 1, "maximum": SECTION_MAX}},
          ["campaign_id"], get_campaign),
    _tool("search", "Rechercher",
          "Recherche plein texte (sans accents) dans les PNJ, factions, lieux, fiches rédigées, commentaires et "
          "événements de chronologie (kind « event »).",
          {"query": {"type": "string"}, "campaign_id": {**ID, "description": "Limiter à une campagne"}},
          ["query"], search),
    _tool("update_document", "Modifier une fiche rédigée",
          "Modifie le titre, le statut et/ou le contenu HTML d'une fiche : content remplace tout le contenu ; "
          "operations le modifie par sections ou par remplacement de texte, sans renvoyer toute la fiche (préféré "
          "pour une fiche longue). base_revision : la révision lue avec get (kind document ; refus si la fiche a changé "
          "depuis). L'état précédent reste dans l'historique de la fiche. Résultat : nouvelle révision et, avec "
          "operations, changes (résumé de chaque opération).",
          {"document_id": ID, "base_revision": ID, "title": {"type": "string"}, "status": STATUS, "content": CONTENT,
           "operations": docsections.OPERATIONS_SCHEMA},
          ["document_id", "base_revision"], update_document, write=True, destructive=True),
]
from . import mcp_images, mcp_maps, mcp_tools, mcp_upload  # noqa: E402,F401 - outils regroupés et dépôt d'image

TOOLS += mcp_tools.TOOLS + mcp_upload.TOOLS
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}
PUBLIC_TOOLS = [{k: v for k, v in t.items() if not k.startswith("_")} for t in TOOLS]


# --- Protocole ---------------------------------------------------------------------------------

def _rpc_error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _text_result(data: Any, is_error: bool = False) -> dict[str, Any]:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _batch_out(batch: Batch) -> dict[str, Any]:
    """Résultat d'un lot : résumé, puis un résultat par élément ({index, ok, id?, error?…}) ; raccourci au besoin
    (les écritures sont faites : mieux vaut un résultat abrégé qu'un refus)."""
    done = sum(1 for r in batch.results if r["ok"])
    failed = [r["index"] for r in batch.results if not r["ok"]]
    summary = f"{done} élément(s) traité(s) sur {len(batch.results)}"
    if failed:
        shown = ", ".join(str(i) for i in failed[:20]) + ("…" if len(failed) > 20 else "")
        summary += f" ; {len(failed)} en échec (index {shown}), les autres sont enregistrés"
    out: dict[str, Any] = {"summary": summary + ".", "done": done, "failed": len(failed), "results": batch.results}
    if _size(out) > RESULT_MAX * 9 // 10:
        out["results"] = [{k: v for k, v in r.items() if k in ("index", "ok", "id", "trash_id")}
                          | ({"error": r["error"][:200]} if "error" in r else {}) for r in batch.results]
        out["note"] = "Résultats abrégés (taille) : relire les éléments avec get ou list."
    return out


async def _call_tool(request: Request, user: dict[str, Any], tool: dict[str, Any],
                     args: dict[str, Any]) -> dict[str, Any]:
    name = tool["name"]

    def load(conn: sqlite3.Connection) -> tuple[bool, Context]:
        return (connector.load_settings_row(conn)["enabled"],
                Context(app=request.app, user=user, grants=connector.grants(conn),
                        base=connector.public_base(request)))
    enabled, ctx = await in_tx(request, load, write=False)
    if not enabled:
        return _text_result(SUSPENDED, True)
    entries_log: list[tuple[int | None, str, bool, str]] | None = None
    try:
        result = await tool["_handler"](ctx, args)
        ok, detail = True, ""
        if isinstance(result, Batch):
            # Un lot : une ligne de journal par élément ; erreur d'outil seulement si aucun élément n'a abouti.
            entries_log = result.log
            out = _text_result(_batch_out(result), is_error=not any(r["ok"] for r in result.results))
        else:
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
    if entries_log is None:
        entries_log = [(ctx.campaign_id, ctx.target, ok, detail)]
    if tool["_write"]:
        for _campaign, target, done, _detail in entries_log:
            if done:
                log.info("Claude (%s) : %s %s", user["username"], name, target)
    # Journal : attente ordinaire du verrou après une écriture ; après une lecture, courte, et entrée sautée si la base
    # reste occupée (Claude a déjà sa réponse ; un import ou une restauration en cours ne la retarde pas de 15 s).
    path = settings(request).db_path
    timeout = None if tool["_write"] else db.LOOP_TIMEOUT * 4

    def journal() -> None:
        with db.transaction(path, timeout=timeout) as conn:
            for campaign_id, target, done, error in entries_log:
                connector.log_action(conn, user["id"], name, campaign_id, target, done, error)
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
            "capabilities": {"tools": {"listChanged": False}, **mcp_guide.CAPABILITIES},
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
    if method in mcp_guide.METHODS:   # ressources et invites (guide d'usage)
        return await mcp_guide.handle(request, user, rid, method, params)
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
