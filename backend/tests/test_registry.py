"""Registre unique des champs des fiches PNJ, factions et lieux (entries.py, audit M23 = QUAL-3).

Échoue dès qu'un champ ajouté au registre (ou une colonne ajoutée aux tables) n'est pas pris en compte partout :
colonnes de la base, déclencheurs de la révision (trg_*_revision) et de la recherche (trg_search_*), modèles de
l'API, historique et commentaires, connecteur Claude, export Foundry, export / import, fichier de l'interface
(frontend/src/entry-fields.generated.js). Les vérifications « en service » parcourent le registre : chaque champ y
est rempli d'un mot unique, puis cherché là où il doit être (et seulement là). Anciens champs (people, age,
location) : ni modifiables, ni publiés, ni lus par le connecteur, ni indexés, mais gardés (export, import).

Lancer : cd backend && python -m unittest -q tests.test_registry
"""

from __future__ import annotations

import asyncio
import re
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from tests.test_api import Server

from maze import db, entries, entry_history, foundry, mcp, schemas

UI_FILE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "entry-fields.generated.js"
TEXT_TYPES = ("name", "short", "summary", "long", "tags")
URLS = {"npc": "npcs", "faction": "factions", "place": "places"}


def word(kind: str, key: str) -> str:
    """Mot unique d'un champ (lettres seules : un seul jeton pour la recherche)."""
    return f"zq{kind}{key.replace('_', '')}mot"


def value(f: entries.EntryField, kind: str):
    w = word(kind, f.key)
    if f.type == "tags":
        return [w]
    if f.type == "long":
        return f"<p>Texte {w}</p>"
    return f"Texte {w}"


def migrated_db(root: Path) -> Path:
    path = root / "maze.db"
    db.migrate(path)
    return path


def trigger_sql(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (name,)).fetchone()
    assert row is not None, name
    return row[0]


def update_of(sql: str) -> set[str]:
    found = re.search(r"AFTER\s+UPDATE\s+OF\s+(.*?)\s+ON\s", sql, re.S | re.I)
    assert found, sql
    return {c.strip() for c in found.group(1).split(",")}


class RegistryStaticTest(unittest.TestCase):
    """Le registre et tout ce qui en dérive (ou doit le suivre), sans serveur."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = migrated_db(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_registry_well_formed(self):
        for kind in entries.KINDS:
            keys = [f.key for f in entries.fields(kind)]
            self.assertEqual(len(keys), len(set(keys)), kind)
            self.assertEqual(entries.fields(kind)[0].type, "name", kind)
            for f in entries.fields(kind):
                self.assertIn(f.type, entries.TYPES, f)
                self.assertIn(f.group, entries.GROUPS, f)
                self.assertIn(f.foundry, entries.FOUNDRY_ROLES, f)
                if f.legacy:
                    self.assertFalse(f.connector or f.search or f.foundry, f"{kind}.{f.key} : ancien champ exposé")
                if f.foundry in ("read", "public", "secret"):
                    self.assertIn(f.type, ("summary", "long"), f)

    def test_every_column_described(self):
        """Chaque colonne de npcs, factions et places est un champ du registre ou dans OTHER_COLUMNS, et chaque
        champ du registre a sa colonne (nouvelle colonne : migration ET registre)."""
        with closing(sqlite3.connect(self.path)) as conn:
            for kind, table in entries.TABLES.items():
                columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                described = {f.key for f in entries.fields(kind)} | entries.OTHER_COLUMNS[kind]
                self.assertEqual(columns, described, f"{table} : colonnes {sorted(columns ^ described)} "
                                                     "absentes de la base ou du registre (entries.py)")

    def test_revision_triggers_follow_history(self):
        """trg_<table>_revision : mêmes champs que l'historique (entry_history.FIELDS)."""
        with closing(sqlite3.connect(self.path)) as conn:
            for kind, table in entries.TABLES.items():
                sql = trigger_sql(conn, f"trg_{table}_revision")
                history = set(entries.keys(kind, history=True))
                self.assertEqual(update_of(sql), history, f"trg_{table}_revision : AFTER UPDATE OF")
                compared = set(re.findall(r"OLD\.(\w+) IS NOT NEW\.\1", sql))
                self.assertEqual(compared, history, f"trg_{table}_revision : WHEN")

    def test_search_triggers_follow_registry(self):
        """trg_search_<kind>_insert / _update : exactement les champs search=True du registre."""
        with closing(sqlite3.connect(self.path)) as conn:
            for kind in entries.KINDS:
                expected = set(entries.keys(kind, search=True))
                for event in ("insert", "update"):
                    sql = trigger_sql(conn, f"trg_search_{kind}_{event}")
                    used = set(re.findall(r"NEW\.(\w+)", sql)) - {"id", "campaign_id"}
                    self.assertEqual(used, expected, f"trg_search_{kind}_{event} : champs indexés")
                self.assertEqual(update_of(trigger_sql(conn, f"trg_search_{kind}_update")), expected,
                                 f"trg_search_{kind}_update : AFTER UPDATE OF")

    def test_api_models(self):
        extra = {"npc": {"portrait_image_id", "pos_x", "pos_y"}, "faction": {"emblem_image_id", "pos_x", "pos_y"},
                 "place": {"image_id"}}
        models = {"npc": (schemas.NpcCreate, schemas.NpcUpdate), "faction": (schemas.FactionCreate, schemas.FactionUpdate),
                  "place": (schemas.PlaceCreate, schemas.PlaceUpdate)}
        for kind, (create, update) in models.items():
            form = set(entries.keys(kind, form=True))
            self.assertEqual(set(create.model_fields), form | extra[kind], kind)
            self.assertEqual(set(update.model_fields), form | extra[kind] | {"base_revision", "new_version"}, kind)
            self.assertTrue(create.model_fields["name"].is_required())
            self.assertFalse(update.model_fields["name"].is_required())

    def test_history_comments_connector(self):
        for kind in entries.KINDS:
            self.assertEqual(entry_history.FIELDS[kind], entries.keys(kind, history=True))
            self.assertEqual(entry_history.COMMENT_FIELDS[kind], frozenset(entries.keys(kind, comment=True)))
            writable = {f.key for f in entries.fields(kind) if f.connector and f.form}
            self.assertEqual(mcp.ENTRY_FIELDS[kind], writable)
            for key in writable:
                self.assertRegex(mcp.FIELDS_DOC, rf"\b{key}\b")
            for key in entries.keys(kind, comment=True):
                self.assertIn(key, mcp.COMMENT_FIELDS_DOC)
        for key in ("people", "age", "location"):
            self.assertNotRegex(mcp.FIELDS_DOC, rf"\b{key}\b")
            self.assertTrue(entries.field("npc", key).legacy)

    def test_interface_file_up_to_date(self):
        """frontend/src/entry-fields.generated.js : `python -m maze.entries` à relancer après un changement."""
        self.assertTrue(UI_FILE.is_file(), UI_FILE)
        self.assertEqual(UI_FILE.read_text(encoding="utf-8"), entries.ui_module(),
                         "Interface en retard sur le registre : cd backend && python -m maze.entries > "
                         "../frontend/src/entry-fields.generated.js")


class RegistryServiceTest(unittest.TestCase):
    """Chaque champ du registre, rempli d'un mot unique, retrouvé là où il doit l'être."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server()
        code, camp = cls.s.call("POST", "/api/campaigns", {"name": "Registre"})
        assert code == 201
        cls.cid = camp["id"]
        cls.ids: dict[str, int] = {}
        for kind in entries.KINDS:
            body = {f.key: value(f, kind) for f in entries.fields(kind) if f.form and f.type in TEXT_TYPES}
            code, row = cls.s.call("POST", f"/api/campaigns/{cls.cid}/{URLS[kind]}", body)
            assert code == 201, row
            cls.ids[kind] = row["id"]
        # Anciens champs : écrits directement (plus aucun chemin de l'API ne les accepte), comme sur les PNJ créés
        # avant le 5 octobre.
        with db.transaction(cls.db_path()) as conn:
            for kind in entries.KINDS:
                for f in entries.fields(kind):
                    if f.legacy:
                        conn.execute(f"UPDATE {entries.TABLES[kind]} SET {f.key} = ? WHERE id = ?",
                                     (value(f, kind), cls.ids[kind]))

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    @classmethod
    def db_path(cls) -> Path:
        return Path(cls.s.tmp.name) / "maze.db"

    def admin(self) -> dict:
        with db.transaction(self.db_path(), write=False) as conn:
            return dict(conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone())

    def test_search(self):
        for kind in entries.KINDS:
            for f in entries.fields(kind):
                if f.type not in TEXT_TYPES:
                    continue
                code, found = self.s.call("GET", f"/api/search?q={word(kind, f.key)}&campaign={self.cid}")
                self.assertEqual(code, 200)
                hits = [(r["kind"], r["id"]) for r in found["results"]]
                if f.search:
                    self.assertIn((kind, self.ids[kind]), hits, f"{kind}.{f.key} introuvable")
                else:
                    self.assertEqual(hits, [], f"{kind}.{f.key} indexé alors que search=False")

    def test_foundry_pages(self):
        with db.transaction(self.db_path(), write=False) as conn:
            export = foundry.build_export(conn, self.cid, "v", "instance")
        out = {kind: next(e for e in export[f"{entries.TABLES[kind]}"] if e["id"] == self.ids[kind])
               for kind in entries.KINDS}
        for kind in entries.KINDS:
            page, secret = out[kind]["html"], out[kind]["secret_html"]
            for f in entries.fields(kind):
                if f.type not in TEXT_TYPES or f.type == "name":
                    continue
                w = word(kind, f.key)
                if f.foundry in ("fact", "read", "public"):
                    self.assertIn(w, page, f"{kind}.{f.key} absent de la page Fiche")
                    self.assertNotIn(w, secret, f"{kind}.{f.key}")
                    self.assertIn(escape_label(f), page)
                elif f.foundry == "secret":
                    self.assertIn(w, secret, f"{kind}.{f.key} absent de la page Secrets")
                    self.assertNotIn(w, page, f"{kind}.{f.key} publié aux joueurs")
                else:
                    self.assertNotIn(w, page + secret, f"{kind}.{f.key} publié dans Foundry")
            if kind == "npc":
                bio = out[kind]["actor_bio"]
                for f in entries.fields(kind):
                    if f.type in TEXT_TYPES:
                        self.assertEqual(word(kind, f.key) in bio, f.foundry == "public", f"biographie : {f.key}")
        # Anciens champs : plus jamais publiés (« Peuple », « Âge » dans la page Fiche, avant M23).
        self.assertNotIn("Peuple", out["npc"]["html"])
        self.assertNotIn("Âge", out["npc"]["html"])

    def test_rest_refuses_legacy_but_keeps_them(self):
        url = f"/api/npcs/{self.ids['npc']}"
        code, row = self.s.call("GET", url)
        self.assertEqual(code, 200)
        for key in ("people", "age", "location"):
            self.assertEqual(row[key], f"Texte {word('npc', key)}")   # données gardées
            code, err = self.s.call("PATCH", url, {key: "Nouveau"})
            self.assertEqual(code, 422, key)
            code, err = self.s.call("POST", f"/api/campaigns/{self.cid}/npcs", {"name": "X", key: "Nouveau"})
            self.assertEqual(code, 422, key)
        self.assertEqual(self.s.call("GET", url)[1]["people"], f"Texte {word('npc', 'people')}")

    def test_revision_and_history_follow_registry(self):
        for kind in entries.KINDS:
            url = f"/api/{URLS[kind]}/{self.ids[kind]}"
            for f in entries.fields(kind):
                if not f.form or f.type not in TEXT_TYPES or f.type == "name":
                    continue
                before = self.s.call("GET", url)[1]["revision"]
                new = [f"{word(kind, f.key)}2"] if f.type == "tags" else f"Autre {word(kind, f.key)}"
                code, row = self.s.call("PATCH", url, {f.key: new})
                self.assertEqual(code, 200, row)
                self.assertEqual(row["revision"] > before, f.history, f"{kind}.{f.key} : révision")
                code, versions = self.s.call("GET", f"{url}/versions")
                latest = self.s.call("GET", f"{url}/versions/{versions['versions'][0]['id']}")[1]
                self.assertEqual(latest["content"].get(f.key), new, f"{kind}.{f.key} : historique")
                self.s.call("PATCH", url, {f.key: value(f, kind)})   # remis pour les autres tests
            # Rangement d'un lieu : ni révision ni version.
            if kind == "place":
                code, parent = self.s.call("POST", f"/api/campaigns/{self.cid}/places", {"name": "Parent"})
                before = self.s.call("GET", url)[1]["revision"]
                code, row = self.s.call("PATCH", url, {"parent_id": parent["id"]})
                self.assertEqual(code, 200)
                self.assertEqual(row["revision"], before)
                self.s.call("PATCH", url, {"parent_id": None})

    def test_connector(self):
        admin = self.admin()
        app = self.s.server.config.app
        ctx = mcp.Context(app=app, user=admin, grants={self.cid: "write"})
        for kind in entries.KINDS:
            got = asyncio.run(mcp.get_entry(ctx, {"kind": kind, "id": self.ids[kind]}))
            for f in entries.fields(kind):
                self.assertEqual(f.key in got, f.connector, f"get_entry {kind}.{f.key}")
            for key in mcp.ENTRY_FIELDS[kind]:
                f = entries.field(kind, key)
                if f.type not in TEXT_TYPES:
                    continue
                row = asyncio.run(mcp.update_entry(ctx, {"kind": kind, "id": self.ids[kind],
                                                         "fields": {key: value(f, kind)}}))
                self.assertNotIn("people", row)
        # Anciens champs envoyés par Claude : ignorés et signalés, le reste de l'appel est fait (contre-revue du lot
        # 5 : avant, tout create_entry ou update_entry qui en contenait un était refusé). REST : toujours 422.
        url = f"/api/npcs/{self.ids['npc']}"
        for key in ("people", "age", "location"):
            row = asyncio.run(mcp.update_entry(ctx, {"kind": "npc", "id": self.ids["npc"],
                                                     "fields": {key: "x", "summary": f"Résumé {key}"}}))
            self.assertEqual((row["ignored_fields"], row["summary"]), ([key], f"Résumé {key}"))
            self.assertIn(key, row["note"])
            self.assertEqual(self.s.call("GET", url)[1][key], f"Texte {word('npc', key)}")   # inchangé
            row = asyncio.run(mcp.create_entry(ctx, {"kind": "npc", "campaign_id": self.cid,
                                                     "fields": {"name": f"Y {key}", key: "x"}}))
            self.assertEqual((row["name"], row["ignored_fields"]), (f"Y {key}", [key]))
            self.assertNotIn(key, row)
        row = asyncio.run(mcp.update_entry(ctx, {"kind": "npc", "id": self.ids["npc"],
                                                 "fields": {"age": "40", "people": "elfe"}}))
        self.assertEqual(row["ignored_fields"], ["age", "people"])
        self.assertNotIn("ignored_fields", asyncio.run(mcp.update_entry(
            ctx, {"kind": "npc", "id": self.ids["npc"], "fields": {"summary": "Sans ancien champ"}})))
        with self.assertRaises(mcp.ToolError):   # champ réellement inconnu : toujours refusé
            asyncio.run(mcp.update_entry(ctx, {"kind": "npc", "id": self.ids["npc"], "fields": {"pos_x": 1}}))
        self.s.call("PATCH", url, {"summary": value(entries.field("npc", "summary"), "npc")})   # remis

    def test_comment_sections(self):
        for kind in entries.KINDS:
            url = f"/api/{URLS[kind]}/{self.ids[kind]}/comments"
            for f in entries.fields(kind):
                code, _ = self.s.call("POST", url, {"body": "Note", "field": f.key})
                self.assertEqual(code, 201 if f.comment else 400, f"{kind}.{f.key}")

    def test_export_import_keeps_every_field(self):
        """Export puis import : chaque champ (anciens compris) gardé, mentions des champs longs réécrites."""
        mention = f'<a class="mention" data-mention="npc:{self.ids["npc"]}">Moi</a>'
        with db.transaction(self.db_path()) as conn:
            for kind in entries.KINDS:
                for f in entries.fields(kind):
                    if f.rich:
                        conn.execute(f"UPDATE {entries.TABLES[kind]} SET {f.key} = ? WHERE id = ?",
                                     (f"<p>{word(kind, f.key)} {mention}</p>", self.ids[kind]))
        code, archive = self.s.call("GET", f"/api/campaigns/{self.cid}/export")
        self.assertEqual(code, 200)
        code, new = self.s.call("POST", "/api/campaigns/import", raw=archive, content_type="application/zip")
        self.assertEqual(code, 201, new)
        with db.transaction(self.db_path(), write=False) as conn:
            new_npc = conn.execute("SELECT id FROM npcs WHERE campaign_id = ?", (new["id"],)).fetchone()[0]
            for kind in entries.KINDS:
                table = entries.TABLES[kind]
                old = dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (self.ids[kind],)).fetchone())
                row = dict(conn.execute(f"SELECT * FROM {table} WHERE campaign_id = ? AND name = ?",
                                        (new["id"], old["name"])).fetchone())
                for f in entries.fields(kind):
                    if f.type == "parent":
                        continue
                    if f.rich:
                        self.assertIn(f'data-mention="npc:{new_npc}"', row[f.key], f"{kind}.{f.key}")
                        self.assertIn(word(kind, f.key), row[f.key])
                    else:
                        self.assertEqual(row[f.key], old[f.key], f"{kind}.{f.key}")


def escape_label(f: entries.EntryField) -> str:
    from html import escape
    return escape(f.foundry_label or f.label)


class SearchLegacyMigrationTest(unittest.TestCase):
    """Migration 028 sur une base au schéma 027 : PNJ réindexés sans peuple ni lieu, valeurs gardées."""

    def test_reindexed_without_legacy_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upto = root / "027"
            upto.mkdir()
            for path in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
                if int(path.name[:3]) <= 27:
                    shutil.copy(path, upto)
            path = root / "maze.db"
            with mock.patch.object(db, "MIGRATIONS_DIR", upto):
                db.migrate(path)
            with db.transaction(path) as conn:
                conn.execute("INSERT INTO campaigns (id, name) VALUES (1, 'C')")
                conn.execute("INSERT INTO npcs (id, campaign_id, name, title, people, age, location, appearance, tags) "
                             "VALUES (7, 1, 'Brunhald', 'Garde', 'Géant', 'Centenaire', 'Faubourg', "
                             "'<p>Énorme</p>', '[\"brute\"]')")

            def hits(conn, text: str) -> int:
                return conn.execute("SELECT COUNT(*) FROM search_index WHERE search_index MATCH ?",
                                    (f'"{text}"*',)).fetchone()[0]

            with closing(db.connect(path)) as conn:
                self.assertEqual(hits(conn, "geant"), 1)   # schéma 027 : peuple indexé
                self.assertEqual(hits(conn, "faubourg"), 1)
            db.migrate(path)
            with closing(db.connect(path)) as conn:
                for text in ("geant", "faubourg", "centenaire"):
                    self.assertEqual(hits(conn, text), 0, text)
                for text in ("brunhald", "garde", "enorme", "brute"):
                    self.assertEqual(hits(conn, text), 1, text)
                row = conn.execute("SELECT people, age, location FROM npcs WHERE id = 7").fetchone()
                self.assertEqual(tuple(row), ("Géant", "Centenaire", "Faubourg"))
                conn.execute("UPDATE npcs SET people = 'Autre' WHERE id = 7")     # plus de déclencheur
                conn.execute("UPDATE npcs SET appearance = '<p>Colossal</p>' WHERE id = 7")
                self.assertEqual(hits(conn, "colossal"), 1)
                self.assertEqual(hits(conn, "autre"), 0)


if __name__ == "__main__":
    unittest.main()
