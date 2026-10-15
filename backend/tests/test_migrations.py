"""Migrations de la base (audit M22 = QUAL-2, DATA-14) : fichiers du dépôt (numéros uniques et contigus),
enregistrement par nom dans schema_migrations avec reprise des bases existantes depuis PRAGMA user_version, refus
d'une numérotation en double ou à trou, d'une base plus récente que le code ou migrée par une autre branche.
Migration 026 (audit M5 = DATA-8, PERF-2) : identifiants jamais réutilisés (AUTOINCREMENT), tables reconstruites
avec tous leurs index et déclencheurs (comparés dans sqlite_master avant et après), index des clés étrangères.

Lancer : cd backend && python -m unittest -q tests.test_migrations
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_api import Server, recv

from maze import backup, db

AUTOINCREMENT = ["campaigns", "npcs", "factions", "places", "documents", "images", "maps", "document_images",
                 "comments"]
FK_INDEXES = {"ix_npcs_portrait", "ix_factions_emblem", "ix_places_image", "ix_images_campaign", "ix_images_file",
              "ix_document_images_campaign", "ix_documents_updated_by", "ix_document_updates_user",
              "ix_comments_user", "ix_templates_user", "ix_foundry_tokens_created_by", "ix_entry_versions_user",
              "ix_entry_comments_user"}


def latest_number() -> int:
    return max(int(p.name[:3]) for p in db.MIGRATIONS_DIR.glob("*.sql"))


class MigrationFilesTest(unittest.TestCase):
    def test_numbers_unique_and_contiguous(self):
        names = sorted(p.name for p in db.MIGRATIONS_DIR.glob("*.sql"))
        self.assertTrue(names)
        for name in names:
            self.assertRegex(name, r"^\d{3}_[a-z0-9_]+\.sql$")
        numbers = [int(re.match(r"(\d+)_", n).group(1)) for n in names]
        duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
        self.assertEqual(duplicates, [], f"numéros en double : {duplicates}")
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)), "numérotation avec un trou")
        self.assertEqual([n for n, _, _ in db.migration_files()], numbers)


class MigrateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "maze.db"
        self.dir = self.root / "migrations"
        shutil.copytree(db.MIGRATIONS_DIR, self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def migrate(self, directory: Path | None = None) -> None:
        with mock.patch.object(db, "MIGRATIONS_DIR", directory or db.MIGRATIONS_DIR):
            db.migrate(self.path)

    def state(self) -> tuple[int, list[tuple[int, str]]]:
        with sqlite3.connect(self.path) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            rows = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
        return version, rows

    def expected(self, directory: Path | None = None) -> list[tuple[int, str]]:
        return [(n, name) for n, name, _ in db.migration_files(directory)]

    def test_new_database_records_every_migration_by_name(self):
        self.migrate()
        version, rows = self.state()
        self.assertEqual(version, latest_number())   # deploy/update.sh compare toujours user_version
        self.assertEqual(rows, self.expected())
        with sqlite3.connect(self.path) as conn:
            sums = [r[0] for r in conn.execute("SELECT checksum FROM schema_migrations")]
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", s) for s in sums))
        self.migrate()   # rien à faire, rien ne change
        self.assertEqual(self.state(), (version, rows))
        self.assertFalse((self.root / db.MIGRATING_MARKER).exists())

    def test_existing_database_taken_over_from_user_version(self):
        """Base d'avant cette table (production) : migrations jusqu'à user_version reprises sous leur nom, puis
        seules les suivantes sont jouées."""
        partial = self.root / "partial"
        partial.mkdir()
        for path in sorted(self.dir.glob("*.sql"))[:-1]:
            shutil.copy(path, partial)
        self.migrate(partial)
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TABLE schema_migrations")
            before = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(before, latest_number() - 1)
        with self.assertLogs("maze", "INFO") as logs:
            self.migrate()
        applied = [m for m in logs.output if "Migration " in m]
        self.assertEqual(len(applied), 1, applied)   # la dernière seulement
        self.assertIn(self.expected()[-1][1], applied[0])
        self.assertEqual(self.state(), (latest_number(), self.expected()))

    def test_duplicate_number_refused(self):
        last = sorted(self.dir.glob("*.sql"))[-1]
        (self.dir / f"{last.name[:3]}_autre_branche.sql").write_text("CREATE TABLE autre (id INTEGER);")
        with self.assertRaises(db.MigrationError) as err:
            self.migrate(self.dir)
        self.assertIn("même numéro", str(err.exception))
        self.assertIn("autre_branche", str(err.exception))
        self.assertFalse(self.path.exists() and self.path.stat().st_size)   # base jamais touchée

    def test_gap_refused(self):
        (self.dir / f"{latest_number() + 2:03d}_trop_loin.sql").write_text("SELECT 1;")
        with self.assertRaises(db.MigrationError) as err:
            self.migrate(self.dir)
        self.assertIn(f"{latest_number() + 1:03d}", str(err.exception))
        self.assertIn("trou", str(err.exception))

    def test_badly_named_file_refused(self):
        (self.dir / "nouvelle.sql").write_text("SELECT 1;")
        with self.assertRaises(db.MigrationError):
            self.migrate(self.dir)

    def newer_database(self) -> str:
        """Base migrée par une version plus récente (une migration de plus que le code du dépôt)."""
        name = f"{latest_number() + 1:03d}_plus_recente.sql"
        (self.dir / name).write_text("CREATE TABLE plus_recente (id INTEGER PRIMARY KEY);")
        self.migrate(self.dir)
        return name

    def test_newer_database_refused(self):
        name = self.newer_database()
        before = self.state()
        with self.assertRaises(db.MigrationError) as err:
            self.migrate()
        message = str(err.exception)
        self.assertIn(name, message)
        self.assertIn("plus récente", message)
        self.assertIn("-maj", message)   # que faire : restaurer la sauvegarde d'avant mise à jour
        self.assertEqual(self.state(), before)

    def test_other_branch_migration_refused(self):
        """Même numéro, autre nom : base passée par la migration d'une autre branche (collision QUAL-2)."""
        last = sorted(self.dir.glob("*.sql"))[-1]
        other = self.dir / f"{last.name[:3]}_autre_branche.sql"
        last.rename(other)
        self.migrate(self.dir)
        with self.assertRaises(db.MigrationError) as err:
            self.migrate()
        self.assertIn("autre_branche", str(err.exception))
        self.assertIn("autre branche", str(err.exception))

    def test_newer_database_without_table_refused(self):
        """Base au schéma plus récent sans schema_migrations (DATA-14 a, code d'avant cette table)."""
        self.migrate()
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TABLE schema_migrations")
            conn.execute(f"PRAGMA user_version = {latest_number() + 3}")
        with self.assertRaises(db.MigrationError) as err:
            self.migrate()
        self.assertIn("plus récent", str(err.exception))

    def test_unrecorded_migration_refused(self):
        """user_version au-delà des migrations enregistrées : une version qui ne les enregistre pas l'a migrée."""
        self.migrate()
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM schema_migrations WHERE version = ?", (latest_number(),))
            conn.execute(f"PRAGMA user_version = {latest_number()}")
        with self.assertRaises(db.MigrationError):
            self.migrate()

    def test_failed_migration_records_nothing(self):
        name = f"{latest_number() + 1:03d}_en_echec.sql"
        (self.dir / name).write_text("CREATE TABLE a_moitie (id INTEGER); SELECT * FROM table_absente;")
        with self.assertRaises(sqlite3.OperationalError):
            self.migrate(self.dir)
        version, rows = self.state()
        self.assertEqual(version, latest_number())
        self.assertNotIn(name, [r[1] for r in rows])
        with sqlite3.connect(self.path) as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'a_moitie'").fetchone())
        self.assertFalse((self.root / db.MIGRATING_MARKER).exists())

    def test_backup_of_newer_database_refused(self):
        """Une sauvegarde d'une base migrée par une autre branche est refusée avant toute restauration."""
        self.newer_database()
        archive = self.root / "maze-20261014-120000.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(self.path, arcname="maze.db")
        with self.assertRaises(backup.ArchiveError) as err:
            backup.inspect_archive(archive)
        self.assertIn("plus récente", str(err.exception))
        # Une base au niveau du code est acceptée.
        ok = self.root / "ok" / "maze.db"
        ok.parent.mkdir()
        db.migrate(ok)
        archive2 = self.root / "maze-20261014-120001.tar.gz"
        with tarfile.open(archive2, "w:gz") as tar:
            tar.add(ok, arcname="maze.db")
        self.assertEqual(backup.inspect_archive(archive2)["schema"], latest_number())


def schema_objects(conn: sqlite3.Connection) -> dict[tuple[str, str], tuple[str, str | None]]:
    """Index et déclencheurs de la base : {(type, nom): (table, SQL)} (index implicites compris, SQL vide)."""
    return {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE type IN ('index', 'trigger')")}


SAMPLE = """
INSERT INTO users (id, username, password_hash) VALUES (1, 'mj', 'x');
INSERT INTO campaigns (id, name) VALUES (1, 'Morvane'), (2, 'Autre');
INSERT INTO npcs (id, campaign_id, name, secrets) VALUES (1, 1, 'Ysolde', 'Traîtresse'), (2, 1, 'Aldric', '');
INSERT INTO factions (id, campaign_id, name) VALUES (1, 1, 'La Vigie');
INSERT INTO memberships (npc_id, faction_id) VALUES (1, 1);
INSERT INTO relations (campaign_id, source_kind, source_id, target_kind, target_id) VALUES (1, 'npc', 1, 'npc', 2);
INSERT INTO places (id, campaign_id, name) VALUES (1, 1, 'Valdis'), (2, 1, 'Sorn');
UPDATE places SET parent_id = 1 WHERE id = 2;
INSERT INTO place_links (place_id, entity_kind, entity_id) VALUES (1, 'npc', 1);
INSERT INTO images (id, campaign_id, owner_kind, owner_id, file, thumb) VALUES (1, 1, 'npc', 1, '1/a.webp', '1/a_t.webp');
UPDATE npcs SET portrait_image_id = 1 WHERE id = 1;
INSERT INTO documents (id, campaign_id, kind, title) VALUES (1, 1, 'campaign', '');
INSERT INTO documents (id, campaign_id, parent_id, kind, title, content)
    VALUES (2, 1, 1, 'scenario', 'Bal', '<p>Le coupable est <a class="mention" data-mention="npc:1">Ysolde</a></p>');
INSERT INTO document_mentions (document_id, target_kind, target_id) VALUES (2, 'npc', 1);
INSERT INTO scenario_nodes (document_id, entity_kind, entity_id, state) VALUES (2, 'npc', 2, 'added');
INSERT INTO document_images (id, campaign_id, document_id, file, thumb) VALUES (1, 1, 2, '1/d.webp', '1/d_t.webp');
INSERT INTO comments (id, document_id, user_id, body) VALUES (1, 2, 1, 'À revoir');
INSERT INTO maps (id, campaign_id, name, file, thumb, width, height) VALUES (1, 1, 'Secteur', '1/m.jpg', '1/m_t.webp', 10, 10);
INSERT INTO map_pins (map_id, place_id, x, y) VALUES (1, 1, 0.5, 0.5);
"""


class AutoincrementMigrationTest(unittest.TestCase):
    """Migration 026 sur une base au schéma 025 (production actuelle) contenant des données."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        before, upto = root / "025", root / "027"
        before.mkdir()
        upto.mkdir()
        for path in sorted(db.MIGRATIONS_DIR.glob("*.sql")):
            if int(path.name[:3]) <= 25:
                shutil.copy(path, before)
            if int(path.name[:3]) <= 27:   # 026 et 027 seules (les suivantes ont leurs propres tests)
                shutil.copy(path, upto)
        cls.path = root / "maze.db"
        with mock.patch.object(db, "MIGRATIONS_DIR", before):
            db.migrate(cls.path)
        with db.transaction(cls.path) as conn:
            for statement in SAMPLE.split(";\n"):
                if statement.strip():
                    conn.execute(statement)
        conn = sqlite3.connect(cls.path)
        cls.objects_before = schema_objects(conn)
        cls.rows_before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()
                           for t in (*AUTOINCREMENT, "search_index", "document_mentions", "relations", "place_links",
                                     "memberships", "scenario_nodes", "map_pins")}
        conn.close()
        with mock.patch.object(db, "MIGRATIONS_DIR", upto):
            db.migrate(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def test_triggers_and_indexes_recreated(self):
        with self.connect() as conn:
            after = schema_objects(conn)
        added = {key for key in after if key not in self.objects_before}
        self.assertEqual(added, {("index", name) for name in FK_INDEXES} | {("trigger", "trg_documents_live_epoch")})
        self.assertEqual({k: v for k, v in after.items() if k not in added}, self.objects_before)
        # Les déclencheurs de 025 (révision), de la recherche et du nettoyage sont tous là, avec le même texte.
        names = {name for kind, name in after if kind == "trigger"}
        for expected in ("trg_npcs_revision", "trg_factions_revision", "trg_places_revision", "trg_search_npc_insert",
                         "trg_search_document_update", "trg_search_comment_insert", "trg_npcs_delete",
                         "trg_documents_delete_links", "trg_places_delete"):
            self.assertIn(expected, names)

    def test_tables_autoincrement_and_data_kept(self):
        with self.connect() as conn:
            for table in AUTOINCREMENT:
                sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()[0]
                self.assertIn("AUTOINCREMENT", sql, table)
                top = conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0]
                seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)).fetchone()
                self.assertEqual(seq[0] if seq else 0, top, table)
            for table, rows in self.rows_before.items():
                self.assertEqual(conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall(), rows, table)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)

    def test_triggers_still_work(self):
        path = Path(self.tmp.name) / "copie.db"
        path.write_bytes(self.path.read_bytes())
        with db.transaction(path) as conn:
            conn.execute("UPDATE npcs SET name = 'Ysolde la Rouge' WHERE id = 1")
            self.assertEqual(conn.execute("SELECT revision FROM npcs WHERE id = 1").fetchone()[0], 1)  # 025
            self.assertEqual(conn.execute("SELECT title FROM search_index WHERE rowid = 1 * 8 + 1").fetchone()[0],
                             "Ysolde la Rouge")
            conn.execute("DELETE FROM images WHERE id = 1")   # ON DELETE SET NULL (index PERF-2)
            self.assertIsNone(conn.execute("SELECT portrait_image_id FROM npcs WHERE id = 1").fetchone()[0])
            conn.execute("DELETE FROM npcs WHERE id = 1")      # nettoyage polymorphe et index de recherche
            for sql in ("SELECT COUNT(*) FROM relations", "SELECT COUNT(*) FROM place_links",
                        "SELECT COUNT(*) FROM document_mentions", "SELECT COUNT(*) FROM memberships",
                        "SELECT COUNT(*) FROM search_index WHERE rowid = 1 * 8 + 1"):
                self.assertEqual(conn.execute(sql).fetchone()[0], 0, sql)
            conn.execute("DELETE FROM npcs WHERE id = 2")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scenario_nodes").fetchone()[0], 0)
            conn.execute("DELETE FROM documents WHERE id = 2")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0], 0)
            conn.execute("DELETE FROM campaigns WHERE id = 1")
            for table in ("npcs", "factions", "places", "documents", "maps", "map_pins", "document_images"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)

    def test_ids_never_reused(self):
        path = Path(self.tmp.name) / "ids.db"
        path.write_bytes(self.path.read_bytes())
        inserts = {
            "campaigns": "INSERT INTO campaigns (name) VALUES ('x')",
            "npcs": "INSERT INTO npcs (campaign_id, name) VALUES (2, 'x')",
            "factions": "INSERT INTO factions (campaign_id, name) VALUES (2, 'x')",
            "places": "INSERT INTO places (campaign_id, name) VALUES (2, 'x')",
            "documents": "INSERT INTO documents (campaign_id, kind, title) VALUES (2, 'page', 'x')",
            "images": "INSERT INTO images (campaign_id, owner_kind, owner_id, file, thumb) VALUES (2, 'npc', 2, 'f', 't')",
            "maps": "INSERT INTO maps (campaign_id, name, file, thumb, width, height) VALUES (2, 'x', 'f', 't', 1, 1)",
            "document_images": "INSERT INTO document_images (campaign_id, file, thumb) VALUES (2, 'f', 't')",
            "comments": "INSERT INTO comments (document_id, body) VALUES (2, 'x')",
        }
        with db.transaction(path) as conn:
            for table, sql in inserts.items():
                first = conn.execute(sql).lastrowid
                conn.execute(f"DELETE FROM {table} WHERE id = ?", (first,))
                second = conn.execute(sql).lastrowid
                self.assertGreater(second, first, table)


class LiveEpochTest(unittest.TestCase):
    """Migration 027 (DATA-8, contre-revue) : époque initiale aléatoire des fiches rédigées, jamais 0 ; une époque
    donnée explicitement est gardée."""

    def test_random_initial_epoch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "maze.db"
            db.migrate(path)
            with db.transaction(path) as conn:
                conn.execute("INSERT INTO campaigns (name) VALUES ('C')")
                epochs = [conn.execute("SELECT live_epoch FROM documents WHERE id = ?", (db.insert(
                    conn, "documents", {"campaign_id": 1, "kind": "page", "title": str(i)}),)).fetchone()[0]
                    for i in range(5)]
                kept = db.insert(conn, "documents", {"campaign_id": 1, "kind": "page", "title": "x", "live_epoch": 42})
                self.assertEqual(conn.execute("SELECT live_epoch FROM documents WHERE id = ?", (kept,)).fetchone()[0], 42)
        self.assertNotIn(0, epochs)
        self.assertEqual(len(set(epochs)), 5)
        self.assertTrue(all(0 < e < 2**53 for e in epochs))


class IdReuseTest(unittest.TestCase):
    """Repris de work-DATA/tests_data/test_collab_data.py (DATA-8) : avant, « ancien PNJ 2 nouveau PNJ 2 » et la
    mention du traître rattachée à l'innocente aubergiste ; fiche rédigée recréée au même numéro, même époque, et
    un navigateur resté hors ligne sur l'ancienne y versait ses modifications."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_deleted_npc_mentions_not_attached_to_new_npc(self):
        s = self.s
        cid = s.call("POST", "/api/campaigns", {"name": "Bal masqué"})[1]["id"]
        s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Autre"})
        traitre = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Le traître"})[1]
        mention = f'<a class="mention" data-mention="npc:{traitre["id"]}">Le traître</a>'
        _, scen = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Bal"})
        s.call("PATCH", f"/api/documents/{scen['id']}", {"content": f"<p>Le coupable est {mention}</p>"})
        self.assertEqual(s.call("DELETE", f"/api/npcs/{traitre['id']}")[0], 204)
        nouveau = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Innocente aubergiste"})[1]
        self.assertGreater(nouveau["id"], traitre["id"])
        _, d = s.call("GET", f"/api/documents/{scen['id']}")
        s.call("PATCH", f"/api/documents/{scen['id']}", {"content": d["content"] + "<p>.</p>"})
        self.assertEqual(s.call("GET", f"/api/npcs/{nouveau['id']}")[1]["mentioned_in"], [])

    def test_deleted_document_not_reopened_by_old_client(self):
        s = self.s
        cid = s.call("POST", "/api/campaigns", {"name": "Hors ligne"})[1]["id"]
        old = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Ancienne"})[1]
        self.assertEqual(s.call("DELETE", f"/api/documents/{old['id']}")[0], 204)
        new = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Nouvelle"})[1]
        self.assertGreater(new["id"], old["id"])
        ws = s.admin.live(old["id"], 7)   # navigateur resté sur l'ancienne fiche : refusé, rien n'est versé
        try:
            self.assertEqual(recv(ws)["code"], 4404)
        finally:
            ws.close()
        _, camp = s.call("POST", "/api/campaigns", {"name": "Suivante"})
        self.assertGreater(camp["id"], cid)


if __name__ == "__main__":
    unittest.main()
