"""Accès de Foundry VTT : jetons, export des fiches et module « maze-sync » (migration 021).

Le module Foundry (dossier foundry_module/, servi par Maze sous /foundry/) tourne dans le navigateur du MJ :
il lit l'export d'une campagne avec un jeton de Maze, puis crée ou met à jour les journaux, scènes et
acteurs du monde Foundry. Rien ne va de Foundry vers Maze.

- Jetons créés dans Administration > Foundry, en lecture seule, limités aux campagnes choisies ; seule leur
  empreinte est conservée. Ils se présentent en en-tête `Authorization: Bearer mzf_…`.
- Routes sous /api/foundry/v1/ : hors session (AuthMiddleware les laisse passer), CORS ouvert à toute
  origine (`FoundryCors`) : le jeton, jamais envoyé automatiquement par le navigateur, est la seule
  autorisation, et Foundry est servi depuis une autre adresse (IP du LAN, autre nom).
- L'export est un JSON prêt à l'emploi : le HTML de chaque page est composé ici (foundry_html.py), le module
  n'a plus qu'à remplacer les marqueurs `data-maze` (liens) et `data-maze-image` (images).
"""

from __future__ import annotations

import hashlib
import io
import json
import secrets
import sqlite3
import zipfile
from collections import defaultdict
from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from . import auth, db, entries, i18n
from .api import instance_id, parse_body, path_id, settings, threaded, tx
from .connector import digest, public_base
from .foundry_html import field_html
from .mediafiles import MEDIA_TYPES
from .schemas import FoundryTokenCreate, FoundryTokenUpdate

PREFIX = "/api/foundry/v1/"
FORMAT = "maze-foundry/1"
TOKEN_PREFIX = "mzf_"
MODULE_DIR = Path(__file__).resolve().parent / "foundry_module"
# Texte de la licence (MIT), joint à l'archive du module, que « license » de module.json désigne
# (chemin relatif au module, format du manifeste de Foundry) : /app/LICENSE dans l'image (Dockerfile), racine du
# dépôt hors de l'image.
LICENSE_FILES = (MODULE_DIR.parents[1] / "LICENSE", MODULE_DIR.parents[2] / "LICENSE")
STATUS_LABELS = {"vivant": "Vivant", "mort": "Mort", "disparu": "Disparu", "inconnu": "Inconnu"}
# Langue des pages composées ici (intitulés des rubriques, statuts, « passage secret »…) : celle des pages du monde,
# choisie par le module et envoyée dans le paramètre `lang` de l'export (réglage « Langue des pages importées » du
# monde, sinon langue retenue au premier import, jamais celle du poste du MJ qui importe), sinon MAZE_DEFAULT_LOCALE ;
# traductions dans maze/i18n/<langue>/foundry.json. Les données des MJ (noms, types de lieux, natures de lien) ne
# sont jamais traduites. Changer de langue change le HTML, donc l'empreinte (`hash`) des pages : à l'import suivant, le
# module réécrit les pages non modifiées dans Foundry et signale les autres (« Modifiés dans Foundry »).
#
# Noms créés par le module dans le monde (dossiers, pages « Fiche » et « Secrets (MJ) », lien de la biographie d'un
# acteur) : renvoyés par l'export dans la même langue (`labels`, clés de lang/<langue>.json du module, paramètres
# {campaign}, {label}, {name} remplis par le module), car Foundry ne charge que la langue du poste du MJ. Traduits
# avec le contexte « foundry » (i18n.ptr, clés « foundry|Fiche »…) : « Fiche » et « PNJ » ont ailleurs une autre
# traduction. Textes français identiques à lang/fr.json du module (vérifié par tests.test_foundry).
WORLD_LABELS = {
    "Folder.Root": "{campaign} - {label}",
    "Folder.Maps": "Cartes",
    "Folder.NoFaction": "Sans faction",
    "Folder.Removed": "Retirés de Maze",
    "Kind.npc": "PNJ",
    "Kind.faction": "Factions",
    "Kind.place": "Lieux",
    "Page.Sheet": "Fiche",
    "Page.Secrets": "Secrets (MJ)",
    "Page.Maps": "Cartes",
    "Page.ActorLink": "Fiche de {name} (Maze)",
}


def world_labels(locale: str) -> dict[str, str]:
    """Noms des dossiers et des pages créés dans le monde, dans la langue des pages (voir WORLD_LABELS)."""
    return {key: i18n.ptr("foundry", text, locale) for key, text in WORLD_LABELS.items()}


# --- CORS ------------------------------------------------------------------------------------------

class FoundryCors:
    """En-têtes CORS des routes de Foundry (préflight compris). Aucun cookie n'est accepté ni renvoyé
    (pas de `Access-Control-Allow-Credentials`) : seul le jeton porté par la requête compte."""

    HEADERS = [
        (b"access-control-allow-origin", b"*"),
        (b"access-control-allow-methods", b"GET, OPTIONS"),
        (b"access-control-allow-headers", b"authorization, content-type"),
        (b"access-control-max-age", b"600"),
    ]

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(PREFIX):
            await self.app(scope, receive, send)
            return
        if scope["method"] == "OPTIONS":
            await send({"type": "http.response.start", "status": 204, "headers": list(self.HEADERS)})
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(self.HEADERS[:1])
            await send(message)

        await self.app(scope, receive, send_with_cors)


# --- Jetons ----------------------------------------------------------------------------------------

def bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip().startswith(TOKEN_PREFIX):
        return None
    return value.strip()


def token_campaigns(request: Request) -> set[int]:
    """Campagnes lisibles avec le jeton de la requête (401 si le jeton manque ou est inconnu).
    Le jeton n'est valable que si l'administrateur qui l'a créé l'est toujours et reste actif : ses jetons sont
    supprimés quand il perd ce rôle, est désactivé ou supprimé (admin.py, migration 023) ; vérifié ici aussi.
    Lecture seule ; la date de dernier usage est écrite à part, au plus une fois par minute (le module
    télécharge beaucoup d'images), dans une transaction courte."""
    token = bearer(request)
    db_path = settings(request).db_path
    # Lecture seule : n'attend pas les écritures (import, suppression d'une campagne) et ne prend pas le verrou.
    with db.transaction(db_path, write=False) as conn:
        row = conn.execute("""SELECT t.id, t.last_used_at FROM foundry_tokens t
                                JOIN users u ON u.id = t.created_by AND u.role = 'admin' AND u.active = 1
                               WHERE t.token_hash = ?""", (digest(token),)).fetchone() if token else None
        if row is None:
            raise HTTPException(401, "Jeton Foundry manquant ou révoqué", headers={"WWW-Authenticate": "Bearer"})
        allowed = {r[0] for r in conn.execute("SELECT campaign_id FROM foundry_grants WHERE token_id = ?",
                                              (row["id"],))}
        stale = conn.execute("SELECT ? IS NULL OR ? < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-60 seconds')",
                             (row["last_used_at"], row["last_used_at"])).fetchone()[0]
    if stale:
        # Au mieux, sans attendre : pendant un import, les requêtes parallèles du module (toutes « périmées » à la
        # première de la minute) attendraient sinon le verrou d'écriture dans les fils des lectures (re-revue du lot 4).
        try:
            with db.transaction(db_path, timeout=db.LOOP_TIMEOUT) as conn:
                conn.execute("UPDATE foundry_tokens SET last_used_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                             "WHERE id = ?", (row["id"],))
        except sqlite3.OperationalError as exc:
            if not db.is_busy(exc):
                raise
    return allowed




# --- Export ----------------------------------------------------------------------------------------

class _Page:
    """Page « Fiche » d'un journal et page « Secrets » réservée au MJ : rubriques publiques d'un côté, secrets,
    liens secrets et notes du MJ de l'autre (en blocs secrets de Foundry, au cas où la page serait partagée)."""

    def __init__(self, key: str, locale: str = i18n.SOURCE_LOCALE):
        self.key = key
        self.locale = locale
        self.count = 0
        self.parts: list[str] = []
        self.secret_parts: list[str] = []
        # Encadrés « Secret » des rubriques publiques (audit M6) : (rubrique, passages), pour la page du MJ.
        self.moved: list[tuple[str, list[str]]] = []

    def secret_id(self) -> str:
        self.count += 1
        return "secret-" + hashlib.sha1(f"{self.key}:{self.count}".encode()).hexdigest()[:16]

    def add(self, title: str, body: str, secret: bool = False) -> None:
        if not body:
            return
        section = f"<h2>{escape(title)}</h2>{body}"
        if secret:
            self.secret_parts.append(f'<section class="secret" id="{self.secret_id()}">{section}</section>')
        else:
            self.parts.append(section)

    def public(self, title: str, text: str, resolve: Callable[[str], str | None]) -> str:
        """HTML d'un champ long dans une rubrique publique ; ses encadrés « Secret » sont gardés pour add_moved."""
        found: list[str] = []
        body = field_html(text, resolve, self.secret_id, secrets=found)
        if found:
            self.moved.append((title, found))
        return body

    def add_moved(self) -> None:
        """Passages secrets trouvés dans les rubriques publiques, en fin de page réservée au MJ."""
        for title, found in self.moved:
            self.add(i18n.tr("{title} : passage secret", self.locale, title=title), "".join(found), secret=True)
        self.moved = []

    def html(self) -> str:
        return "".join(self.parts)

    def secret_html(self) -> str:
        return "".join(self.secret_parts)


def _link(kind: str, entity_id: int, name: str) -> str:
    return f'<a data-maze="{kind}:{entity_id}">{escape(name)}</a>'


def _facts(rows: list[tuple[str, str]], locale: str = i18n.SOURCE_LOCALE) -> str:
    items = "".join(f"<li><p><strong>{i18n.tr('{label} :', locale, label=escape(label))}</strong> {value}</p></li>"
                    for label, value in rows if value)
    return f"<ul>{items}</ul>" if items else ""


def _list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li><p>{item}</p></li>" for item in items) + "</ul>" if items else ""


def _digest(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]


def scenarios_of(conn: sqlite3.Connection, cid: int, cast: bool = True) -> list[dict[str, Any]]:
    """Scénarios de la campagne dans l'ordre de l'arbre ; avec `cast`, leurs fiches.

    Même règle que la carte d'un scénario (castOf dans l'interface, scenario_maps_of) : PNJ et factions cités (@)
    dans le scénario et toutes ses pages, plus les ajouts manuels, moins les retraits ; lieux cités. « related » :
    fiches voisines (factions des PNJ, lieux où se trouvent PNJ et factions, présents des lieux cités)."""
    docs = conn.execute("SELECT id, parent_id, kind, title, status FROM documents WHERE campaign_id = ? "
                        "ORDER BY sort_order, id", (cid,)).fetchall()
    children: dict[int | None, list[Any]] = defaultdict(list)
    ids = {d[0] for d in docs}
    for d in docs:
        children[d[1] if d[1] in ids else None].append(d)
    ordered: list[Any] = []
    # Parcours en profondeur dans l'ordre de l'arbre, sans récursion (chaîne de pages très profonde, audit DATA-10).
    seen: set[int] = set()
    stack = list(reversed(children[None]))
    while stack:
        d = stack.pop()
        if d[0] in seen:
            continue
        seen.add(d[0])
        if d[2] == "scenario":
            ordered.append(d)
        stack.extend(reversed(children[d[0]]))
    out = [{"id": d[0], "title": d[3], "status": d[4]} for d in ordered]
    if not cast or not out:
        return out

    mentions: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for did, kind, eid in conn.execute(
            """SELECT m.document_id, m.target_kind, m.target_id FROM document_mentions m
                 JOIN documents d ON d.id = m.document_id WHERE d.campaign_id = ?""", (cid,)):
        mentions[did].append((kind, eid))
    overrides: dict[int, list[tuple[str, int, str]]] = defaultdict(list)
    for did, kind, eid, state in conn.execute(
            """SELECT n.document_id, n.entity_kind, n.entity_id, n.state FROM scenario_nodes n
                 JOIN documents d ON d.id = n.document_id WHERE d.campaign_id = ? AND n.state != 'auto'""", (cid,)):
        overrides[did].append((kind, eid, state))
    existing = {kind: {r[0] for r in conn.execute(f"SELECT id FROM {table} WHERE campaign_id = ?", (cid,))}
                for kind, table in (("npc", "npcs"), ("faction", "factions"), ("place", "places"))}
    factions_of: dict[int, set[int]] = defaultdict(set)
    for npc_id, faction_id in conn.execute(
            "SELECT m.npc_id, m.faction_id FROM memberships m JOIN npcs n ON n.id = m.npc_id WHERE n.campaign_id = ?",
            (cid,)):
        factions_of[npc_id].add(faction_id)
    # Factions dont une faction est membre (sous-factions, migration 031) : fiches liées comme celles d'un PNJ.
    parents_of: dict[int, set[int]] = defaultdict(set)
    for sub_id, faction_id in conn.execute(
            """SELECT m.member_faction_id, m.faction_id FROM memberships m JOIN factions f ON f.id = m.faction_id
                WHERE f.campaign_id = ? AND m.member_faction_id IS NOT NULL""", (cid,)):
        parents_of[sub_id].add(faction_id)
    places_of: dict[tuple[str, int], set[int]] = defaultdict(set)
    present: dict[int, set[tuple[str, int]]] = defaultdict(set)
    for place_id, kind, eid in conn.execute(
            """SELECT l.place_id, l.entity_kind, l.entity_id FROM place_links l JOIN places p ON p.id = l.place_id
                WHERE p.campaign_id = ?""", (cid,)):
        places_of[(kind, eid)].add(place_id)
        present[place_id].add((kind, eid))

    def subtree(did: int) -> list[int]:
        found, stack, seen = [], [did], set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            found.append(cur)
            stack.extend(c[0] for c in children[cur])
        return found

    for sc in out:
        cited: set[tuple[str, int]] = set()
        for did in subtree(sc["id"]):
            cited.update(m for m in mentions[did] if m[0] in existing)
        core = set(cited)
        for kind, eid, state in overrides[sc["id"]]:
            if state == "added":
                core.add((kind, eid))
            elif state == "excluded" and kind in ("npc", "faction"):
                core.discard((kind, eid))
        core = {(k, e) for k, e in core if e in existing.get(k, ())}
        related: set[tuple[str, int]] = set()
        for kind, eid in core:
            if kind == "npc":
                related.update(("faction", f) for f in factions_of[eid])
            if kind == "faction":
                related.update(("faction", f) for f in parents_of[eid])
            if kind in ("npc", "faction"):
                related.update(("place", p) for p in places_of[(kind, eid)])
            if kind == "place":
                related.update(present[eid])
        related = {(k, e) for k, e in related - core if e in existing.get(k, ())}
        for name, group in (("cast", core), ("related", related)):
            sc[name] = {kind: sorted(e for k, e in group if k == kind) for kind in ("npc", "faction", "place")}
    return out


def build_export(conn: sqlite3.Connection, cid: int, version: str, instance: str,
                 locale: str = i18n.SOURCE_LOCALE) -> dict[str, Any]:
    campaign = db.row_to_dict(conn.execute("SELECT * FROM campaigns WHERE id = ?", (cid,)).fetchone())
    if campaign is None:
        raise HTTPException(404, "Campagne introuvable")
    q = lambda sql, *args: db.rows_to_dicts(conn.execute(sql, args).fetchall())  # noqa: E731

    def tr(text: str, **params: Any) -> str:
        """Intitulé des pages dans la langue de l'export (les paramètres, déjà en HTML, sont insérés tels quels)."""
        return i18n.tr(text, locale, **params)

    def label_of(f: entries.EntryField) -> str:
        return tr(f.foundry_label or f.label)
    kinds = q("SELECT key, label, image FROM place_kinds WHERE campaign_id = ? ORDER BY sort_order, id", cid)
    rtypes = {r["key"]: r for r in q("SELECT key, label, directed FROM relation_types WHERE campaign_id = ?", cid)}
    factions = q("SELECT * FROM factions WHERE campaign_id = ? ORDER BY sort_order, lower(name), id", cid)
    npcs = q("SELECT * FROM npcs WHERE campaign_id = ? ORDER BY sort_order, lower(name), id", cid)
    places = q("SELECT * FROM places WHERE campaign_id = ? ORDER BY sort_order, lower(name), id", cid)
    images = q("SELECT * FROM images WHERE campaign_id = ? ORDER BY sort_order, id", cid)
    memberships = q("""SELECT m.* FROM memberships m JOIN npcs n ON n.id = m.npc_id
                        JOIN factions f ON f.id = m.faction_id
                       WHERE n.campaign_id = ? ORDER BY f.sort_order, lower(f.name), f.id""", cid)
    # Sous-factions (migration 031), à part : la requête des PNJ membres reste celle d'avant (même ordre, mêmes
    # pages et empreintes pour une faction sans sous-faction).
    nested = q("""SELECT m.* FROM memberships m JOIN factions f ON f.id = m.faction_id
                   WHERE f.campaign_id = ? AND m.member_faction_id IS NOT NULL ORDER BY m.id""", cid)
    relations = q("SELECT * FROM relations WHERE campaign_id = ? ORDER BY id", cid)
    links = q("""SELECT l.* FROM place_links l JOIN places p ON p.id = l.place_id
                  WHERE p.campaign_id = ? ORDER BY l.id""", cid)
    maps = q("SELECT * FROM maps WHERE campaign_id = ? ORDER BY sort_order, id", cid)
    pins = q("SELECT p.* FROM map_pins p JOIN maps m ON m.id = p.map_id WHERE m.campaign_id = ?", cid)

    names = {("npc", n["id"]): n["name"] for n in npcs}
    names.update({("faction", f["id"]): f["name"] for f in factions})
    names.update({("place", p["id"]): p["name"] for p in places})
    # [[Nom]] des anciens textes : PNJ d'abord, puis factions, puis lieux (casse ignorée).
    by_name: dict[str, str] = {}
    for (kind, eid), name in sorted(names.items(), key=lambda kv: ["npc", "faction", "place"].index(kv[0][0])):
        by_name.setdefault(name.strip().lower(), f"{kind}:{eid}")
    resolve = by_name.get

    def link(kind: str, eid: int) -> str:
        return _link(kind, eid, names[(kind, eid)]) if (kind, eid) in names else ""

    kind_label = {k["key"]: k["label"] for k in kinds}
    gallery: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    files: dict[str, dict[str, Any]] = {}
    for img in images:
        gallery[(img["owner_kind"], img["owner_id"])].append({"id": img["id"], "caption": img["caption"]})
        files[str(img["id"])] = {"path": img["file"], "width": img["width"], "height": img["height"],
                                 "caption": img["caption"]}
    members_of: dict[int, list[dict[str, Any]]] = defaultdict(list)
    factions_of: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for m in memberships:
        members_of[m["faction_id"]].append(m)
        factions_of[m["npc_id"]].append(m)
    faction_rank = {f["id"]: i for i, f in enumerate(factions)}
    subs_of: dict[int, list[dict[str, Any]]] = defaultdict(list)
    parents_of: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for m in sorted(nested, key=lambda m: faction_rank.get(m["member_faction_id"], 0)):
        subs_of[m["faction_id"]].append(m)
    for m in sorted(nested, key=lambda m: faction_rank.get(m["faction_id"], 0)):
        parents_of[m["member_faction_id"]].append(m)
    present: dict[int, list[dict[str, Any]]] = defaultdict(list)
    places_of: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for pl in links:
        present[pl["place_id"]].append(pl)
        places_of[(pl["entity_kind"], pl["entity_id"])].append(pl)
    rels_of: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in relations:
        rels_of[(r["source_kind"], r["source_id"])].append(r)
        rels_of[(r["target_kind"], r["target_id"])].append(r)
    children: dict[int | None, list[dict[str, Any]]] = defaultdict(list)
    place_ids = {p["id"] for p in places}
    for p in places:
        children[p["parent_id"] if p["parent_id"] in place_ids else None].append(p)

    def cover_html(cover: int | None, name: str) -> str:
        if cover is None or str(cover) not in files:
            return ""
        return f'<p><img data-maze-image="{cover}" alt="{escape(name)}"></p>'

    def relation_items(kind: str, eid: int, secret: bool) -> list[str]:
        items = []
        for r in rels_of[(kind, eid)]:
            if r["secret"] != secret:
                continue
            src, tgt = (r["source_kind"], r["source_id"]), (r["target_kind"], r["target_id"])
            if src not in names or tgt not in names:
                continue
            rtype = rtypes.get(r["type"], {})
            label = r["label"].strip() or rtype.get("label") or r["type"]
            if rtype.get("label") and rtype["label"].lower() != label.lower():
                label = f"{label} ({rtype['label']})"

            def ref(key: tuple[str, int]) -> str:
                return escape(names[key]) if key == (kind, eid) else link(*key)

            who = f"{ref(src)} → {ref(tgt)}" if r["directed"] else ref(tgt if src == (kind, eid) else src)
            desc = f". {escape(r['description'].strip())}" if r["description"].strip() else ""
            items.append(tr("{label} : {value}", label=f"<strong>{escape(label)}</strong>", value=f"{who}{desc}"))
        return items

    def place_items(kind: str, eid: int) -> list[str]:
        return [tr("{label} : {value}", label=link("place", pl["place_id"]), value=escape(pl["role"]))
                if pl["role"].strip() else link("place", pl["place_id"])
                for pl in places_of[(kind, eid)] if ("place", pl["place_id"]) in names]

    def public_rich(page: _Page, title: str, text: str) -> None:
        page.add(title, page.public(title, text, resolve))

    def secret_rich(page: _Page, title: str, text: str) -> None:
        page.add(title, field_html(text, resolve, page.secret_id, in_secret=True), secret=True)

    # Composition des pages depuis le registre des champs (entries.py, audit M23) : faits en tête (étiquettes en
    # dernier, après les faits propres à la fiche), texte à lire, rubriques publiques puis les listes de la fiche
    # (relations, membres, sous-lieux…), rubriques secrètes (chacune suivie de ses listes secrètes) ; les anciens
    # champs invisibles dans Maze (people, age) ne sont plus publiés.
    fact_value: dict[str, Callable[[Any], str]] = {
        "status": lambda v: escape(tr(STATUS_LABELS[v])) if v in STATUS_LABELS else escape(v),
        "place_kind": lambda v: escape(kind_label.get(v, v)),
        "parent": lambda v: link("place", v) if v in place_ids else "",
        "tags": lambda v: escape(", ".join(v)),
    }

    def facts(kind: str, row: dict[str, Any], extra: list[tuple[str, str]]) -> str:
        def item(f: entries.EntryField) -> tuple[str, str]:
            return label_of(f), fact_value.get(f.type, escape)(row[f.key])
        own = entries.foundry_fields(kind, "fact")
        return _facts([item(f) for f in own if f.type != "tags"] + extra + [item(f) for f in own if f.type == "tags"],
                      locale)

    def texts(kind: str, row: dict[str, Any], page: _Page, lists: list[tuple[str, str]],
              secret_lists: dict[str, list[tuple[str, str]]] | None = None) -> None:
        for f in entries.foundry_fields(kind, "read"):
            title = label_of(f)
            body = page.public(title, row[f.key], resolve)
            page.add(title, f'<blockquote class="maze-read">{body}</blockquote>' if body else "")
        for f in entries.foundry_fields(kind, "public"):
            public_rich(page, label_of(f), row[f.key])
        for title, body in lists:
            page.add(title, body)
        for f in entries.foundry_fields(kind, "secret"):
            secret_rich(page, label_of(f), row[f.key])
            for title, body in (secret_lists or {}).get(f.key, []):
                page.add(title, body, secret=True)
        page.add_moved()

    out_npcs = []
    for n in npcs:
        key = ("npc", n["id"])
        page = _Page(f"npc:{n['id']}", locale)
        cover = n["portrait_image_id"]
        factions_html = []
        for m in factions_of[n["id"]]:
            item = link("faction", m["faction_id"]) + (f" ({escape(m['role'])})" if m["role"].strip() else "")
            factions_html.append(tr("{faction} - chef", faction=item) if m["is_leader"] else item)
        page.parts.append(cover_html(cover, n["name"]))
        page.parts.append(facts("npc", n, [(tr("Factions"), ", ".join(factions_html)),
                                           (tr("Lieux"), ", ".join(place_items("npc", n["id"])))]))
        texts("npc", n, page, [(tr("Relations"), _list(relation_items("npc", n["id"], False)))],
              {"secrets": [(tr("Relations secrètes"), _list(relation_items("npc", n["id"], True)))]})
        bio = _Page(f"npc-bio:{n['id']}", locale)
        for f in entries.foundry_fields("npc", "public"):
            public_rich(bio, label_of(f), n[f.key])
        group = factions_of[n["id"]][0]["faction_id"] if factions_of[n["id"]] else None
        entry = {"id": n["id"], "created": n["created_at"], "name": n["name"], "title": n["title"],
                 "status": n["status"], "group": group, "cover": cover, "images": gallery[key], "html": page.html(),
                 "secret_html": page.secret_html(), "actor_bio": bio.html(),
                 "faction_names": [names[("faction", m["faction_id"])] for m in factions_of[n["id"]]]}
        entry["hash"] = _digest(entry)
        out_npcs.append(entry)

    out_factions = []
    for f in factions:
        key = ("faction", f["id"])
        page = _Page(f"faction:{f['id']}", locale)
        cover = f["emblem_image_id"]
        leaders = [link("npc", m["npc_id"]) for m in members_of[f["id"]] if m["is_leader"]]
        page.parts.append(cover_html(cover, f["name"]))
        # Factions dont elle est membre et sous-factions (migration 031) : faits et liste vides, donc absents, pour
        # une faction sans imbrication (page et empreinte inchangées).
        parents = [link("faction", m["faction_id"]) + (f" ({escape(m['role'])})" if m["role"].strip() else "")
                   for m in parents_of[f["id"]]]
        page.parts.append(facts("faction", f, [(tr("Chef"), ", ".join(leaders)),
                                               (tr("Membre de"), ", ".join(parents)),
                                               (tr("Lieux"), ", ".join(place_items("faction", f["id"])))]))
        member_items = []
        for m in members_of[f["id"]]:
            item = link("npc", m["npc_id"])
            if m["role"].strip():
                item = tr("{label} : {value}", label=item, value=escape(m["role"]))
            member_items.append(tr("{member} (chef)", member=item) if m["is_leader"] else item)
        sub_items = []
        for m in subs_of[f["id"]]:
            item = link("faction", m["member_faction_id"])
            sub_items.append(tr("{label} : {value}", label=item, value=escape(m["role"])) if m["role"].strip() else item)
        texts("faction", f, page, [(tr("Membres"), _list(member_items)),
                                   (tr("Sous-factions"), _list(sub_items)),
                                   (tr("Relations"), _list(relation_items("faction", f["id"], False)))],
              {"secrets": [(tr("Relations secrètes"), _list(relation_items("faction", f["id"], True)))]})
        entry = {"id": f["id"], "created": f["created_at"], "name": f["name"], "kind": f["kind"], "color": f["color"],
                 "cover": cover, "images": gallery[key], "html": page.html(), "secret_html": page.secret_html()}
        entry["hash"] = _digest(entry)
        out_factions.append(entry)

    out_places = []
    for p in places:
        key = ("place", p["id"])
        page = _Page(f"place:{p['id']}", locale)
        cover = p["image_id"]
        parent = p["parent_id"] if p["parent_id"] in place_ids else None
        page.parts.append(cover_html(cover, p["name"]))
        page.parts.append(facts("place", p, []))
        present_html = _list([tr("{label} : {value}", label=link(pl["entity_kind"], pl["entity_id"]),
                                 value=escape(pl["role"])) if pl["role"].strip()
                              else link(pl["entity_kind"], pl["entity_id"])
                              for pl in present[p["id"]] if (pl["entity_kind"], pl["entity_id"]) in names])
        texts("place", p, page, [(tr("Sous-lieux"), _list([link("place", c["id"]) for c in children[p["id"]]])),
                                 (tr("Présents"), present_html)])
        entry = {"id": p["id"], "created": p["created_at"], "name": p["name"], "parent": parent, "kind": p["kind"],
                 "has_children": bool(children[p["id"]]), "cover": cover, "images": gallery[key],
                 "html": page.html(), "secret_html": page.secret_html()}
        entry["hash"] = _digest(entry)
        out_places.append(entry)

    pins_of: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for pin in pins:
        pins_of[pin["map_id"]].append({"place": pin["place_id"], "x": pin["x"], "y": pin["y"]})
    out_maps = []
    for m in maps:
        entry = {"id": m["id"], "created": m["created_at"], "name": m["name"],
                 "place": m["place_id"] if m["place_id"] in place_ids else None,
                 "width": m["width"], "height": m["height"], "file": m["file"], "preview": m["preview"] or None,
                 "pin_style": m["pin_style"], "pins": sorted(pins_of[m["id"]], key=lambda p: p["place"])}
        entry["hash"] = _digest(entry)
        out_maps.append(entry)

    return {
        "format": FORMAT,
        "maze_version": version,
        "instance": instance,
        # Langue des pages (celle demandée si le serveur la connaît) et noms à créer dans le monde dans cette langue.
        "locale": locale,
        "labels": world_labels(locale),
        "campaign": {"id": campaign["id"], "name": campaign["name"], "description": campaign["description"],
                     "updated_at": campaign["updated_at"]},
        "place_kinds": [{"key": k["key"], "label": k["label"], "icon": k["image"] or None} for k in kinds],
        "files": files,
        "factions": out_factions,
        "npcs": out_npcs,
        "places": out_places,
        "maps": out_maps,
        "scenarios": scenarios_of(conn, cid),
    }


# --- Routes de Foundry -------------------------------------------------------------------------------

@threaded
def list_campaigns(request: Request) -> Response:
    return JSONResponse(_list_campaigns(request))


def _list_campaigns(request: Request) -> dict[str, Any]:
    allowed = token_campaigns(request)
    with tx(request, write=False) as conn:
        rows = db.rows_to_dicts(conn.execute(
            f"""SELECT c.id, c.name, c.description, c.updated_at,
                       (SELECT COUNT(*) FROM npcs WHERE campaign_id = c.id) AS npcs,
                       (SELECT COUNT(*) FROM factions WHERE campaign_id = c.id) AS factions,
                       (SELECT COUNT(*) FROM places WHERE campaign_id = c.id) AS places,
                       (SELECT COUNT(*) FROM maps WHERE campaign_id = c.id) AS maps
                  FROM campaigns c WHERE c.id IN ({",".join("?" * len(allowed))}) ORDER BY lower(c.name)""",
            sorted(allowed)).fetchall()) if allowed else []
        for row in rows:
            row["scenarios"] = scenarios_of(conn, row["id"], cast=False)
    return {"format": FORMAT, "instance": instance_id(request), "campaigns": rows}


@threaded
def export_campaign(request: Request) -> Response:
    cid = path_id(request)
    cfg = settings(request)
    # Langue des pages du monde choisie par le module (?lang=en), sinon MAZE_DEFAULT_LOCALE (voir WORLD_LABELS).
    locale = i18n.chosen_locale(request.query_params.get("lang"), request.scope)

    def work() -> dict[str, Any]:
        if cid not in token_campaigns(request):
            raise HTTPException(404, "Campagne introuvable")
        with db.transaction(cfg.db_path, write=False) as conn:
            return build_export(conn, cid, cfg.version, instance_id(request), locale)

    return JSONResponse(work())


@threaded
def media(request: Request) -> Response:
    cfg = settings(request)
    # Jeton d'abord : sans lui, rien n'indique si un fichier existe.
    allowed = token_campaigns(request)
    root = cfg.media_dir.resolve()
    target = (root / request.path_params["path"]).resolve()
    if root not in target.parents or target.suffix not in MEDIA_TYPES or not target.is_file():
        raise HTTPException(404, "Fichier introuvable")
    campaign = target.relative_to(root).parts[0]
    if not campaign.isdigit() or int(campaign) not in allowed:
        raise HTTPException(404, "Fichier introuvable")
    return FileResponse(target, media_type=MEDIA_TYPES[target.suffix],
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})


# --- Module Foundry servi par Maze --------------------------------------------------------------------

_ZIP_CACHE: dict[str, bytes] = {}


def module_manifest(base: str) -> dict[str, Any]:
    manifest = json.loads((MODULE_DIR / "module.json").read_text(encoding="utf-8"))
    manifest["manifest"] = f"{base}/foundry/module.json"
    manifest["download"] = f"{base}/foundry/maze-sync.zip"
    manifest["url"] = base
    return manifest


def module_zip(base: str) -> bytes:
    """Archive du module, module.json (adresses de cette instance) à la racine."""
    manifest = module_manifest(base)
    key = f"{base}|{manifest['version']}"
    if key not in _ZIP_CACHE:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(MODULE_DIR.rglob("*")):
                if path.is_file() and path.name != "module.json" and "__pycache__" not in path.parts:
                    zf.write(path, path.relative_to(MODULE_DIR).as_posix())
            license_file = next((p for p in LICENSE_FILES if p.is_file()), None)
            if license_file is not None:
                zf.write(license_file, "LICENSE")
            zf.writestr("module.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        _ZIP_CACHE.clear()
        _ZIP_CACHE[key] = buf.getvalue()
    return _ZIP_CACHE[key]


async def manifest(request: Request) -> Response:
    return JSONResponse(module_manifest(public_base(request)), headers={"Cache-Control": "no-cache"})


async def download(request: Request) -> Response:
    data = module_zip(public_base(request))
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="maze-sync.zip"', "Cache-Control": "no-cache"})


# --- Administration > Foundry ---------------------------------------------------------------------------

def _state(request: Request, conn: sqlite3.Connection) -> dict[str, Any]:
    tokens = db.rows_to_dicts(conn.execute(
        """SELECT t.id, t.name, t.hint, t.created_at, t.last_used_at,
                  COALESCE(NULLIF(u.display_name, ''), u.username) AS created_by
             FROM foundry_tokens t LEFT JOIN users u ON u.id = t.created_by ORDER BY t.id""").fetchall())
    grants: dict[int, list[int]] = defaultdict(list)
    for token_id, campaign_id in conn.execute("SELECT token_id, campaign_id FROM foundry_grants ORDER BY campaign_id"):
        grants[token_id].append(campaign_id)
    for t in tokens:
        t["campaigns"] = grants[t["id"]]
    base = public_base(request)
    return {"tokens": tokens, "maze_url": base, "manifest_url": f"{base}/foundry/module.json",
            "module_version": module_manifest(base)["version"]}


def _set_campaigns(conn: sqlite3.Connection, token_id: int, campaigns: list[int]) -> None:
    wanted = set(campaigns)
    known = {r[0] for r in conn.execute("SELECT id FROM campaigns")}
    if not wanted <= known:
        raise HTTPException(400, "Campagne inconnue")
    conn.execute("DELETE FROM foundry_grants WHERE token_id = ?", (token_id,))
    conn.executemany("INSERT INTO foundry_grants (token_id, campaign_id) VALUES (?, ?)",
                     [(token_id, c) for c in sorted(wanted)])


@threaded
def admin_state(request: Request) -> Response:
    auth.require_admin(request)
    with tx(request) as conn:
        return JSONResponse(_state(request, conn))


@threaded
def create_token(request: Request) -> Response:
    """Nouveau jeton : affiché une seule fois (seule son empreinte est conservée)."""
    user = auth.require_admin(request)
    data = parse_body(request, FoundryTokenCreate)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with tx(request) as conn:
        token_id = db.insert(conn, "foundry_tokens", {"name": data.name, "token_hash": digest(token),
                                                     "hint": token[len(TOKEN_PREFIX):][:4], "created_by": user["id"]})
        _set_campaigns(conn, token_id, data.campaigns)
        out = _state(request, conn)
    return JSONResponse({**out, "token": token, "token_id": token_id}, status_code=201)


@threaded
def update_token(request: Request) -> Response:
    auth.require_admin(request)
    token_id = path_id(request)
    data = parse_body(request, FoundryTokenUpdate)
    with tx(request) as conn:
        if not conn.execute("SELECT 1 FROM foundry_tokens WHERE id = ?", (token_id,)).fetchone():
            raise HTTPException(404, "Jeton introuvable")
        if data.name is not None:
            conn.execute("UPDATE foundry_tokens SET name = ? WHERE id = ?", (data.name, token_id))
        if data.campaigns is not None:
            _set_campaigns(conn, token_id, data.campaigns)
        out = _state(request, conn)
    return JSONResponse(out)


@threaded
def delete_token(request: Request) -> Response:
    auth.require_admin(request)
    token_id = path_id(request)
    with tx(request) as conn:
        if conn.execute("DELETE FROM foundry_tokens WHERE id = ?", (token_id,)).rowcount == 0:
            raise HTTPException(404, "Jeton introuvable")
        out = _state(request, conn)
    return JSONResponse(out)


routes = [
    Route(PREFIX + "campaigns", list_campaigns),
    Route(PREFIX + "campaigns/{id:int}/export", export_campaign),
    Route(PREFIX + "media/{path:path}", media),
    Route("/foundry/module.json", manifest),
    Route("/foundry/maze-sync.zip", download),
    Route("/api/admin/foundry", admin_state),
    Route("/api/admin/foundry/tokens", create_token, methods=["POST"]),
    Route("/api/admin/foundry/tokens/{id:int}", update_token, methods=["PUT"]),
    Route("/api/admin/foundry/tokens/{id:int}", delete_token, methods=["DELETE"]),
]
