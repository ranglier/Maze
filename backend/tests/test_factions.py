"""Factions imbriquées (migration 031) : une faction membre d'une ou plusieurs autres factions (sous-faction).
Règles (pas elle-même, pas de cycle, même campagne, pas de chef), accès d'un autre compte, suppression en cascade,
migration d'une base existante, export / import (archives anciennes comprises), sauvegarde et restauration.
Connecteur Claude : tests.test_connector ; Foundry : tests.test_foundry.

Lancer : cd backend && python -m unittest -q tests.test_factions
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tests.test_api import Server

from maze import db


class NestedFactionsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def campaign(self, name: str) -> int:
        _, camp = self.s.call("POST", "/api/campaigns", {"name": name})
        self.addCleanup(self.s.call, "DELETE", f"/api/campaigns/{camp['id']}")
        return camp["id"]

    def faction(self, cid: int, name: str) -> dict:
        code, fac = self.s.call("POST", f"/api/campaigns/{cid}/factions", {"name": name})
        self.assertEqual(code, 201, fac)
        return fac

    def nest(self, member: dict, faction: dict, **extra):
        return self.s.call("POST", "/api/memberships",
                           {"member_faction_id": member["id"], "faction_id": faction["id"], **extra})

    def graph(self, cid: int) -> dict:
        return self.s.call("GET", f"/api/campaigns/{cid}/graph")[1]

    def test_create_update_and_graph(self):
        s = self.s
        cid = self.campaign("Imbrications")
        guild, cell, council = (self.faction(cid, n) for n in ("Guilde", "Cellule", "Conseil"))
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Maëlis"})
        code, npc_m = s.call("POST", "/api/memberships",
                             {"npc_id": npc["id"], "faction_id": guild["id"], "role": "Cheffe", "is_leader": True})
        self.assertEqual(code, 201, npc_m)
        self.assertIsNone(npc_m["member_faction_id"])   # PNJ membre : comme avant, colonne de plus à vide

        code, m = self.nest(cell, guild, role="Bras armé")
        self.assertEqual(code, 201, m)
        self.assertEqual((m["member_faction_id"], m["npc_id"], m["faction_id"], m["role"], m["is_leader"]),
                         (cell["id"], None, guild["id"], "Bras armé", False))
        # Plusieurs factions parentes.
        self.assertEqual(self.nest(cell, council)[0], 201)
        self.assertEqual(self.nest(cell, guild)[0], 409)
        nested = [x for x in self.graph(cid)["memberships"] if x["member_faction_id"]]
        self.assertEqual(sorted(x["faction_id"] for x in nested), sorted([guild["id"], council["id"]]))

        # Rôle modifiable, chef réservé aux PNJ (création comme modification).
        code, upd = s.call("PATCH", f"/api/memberships/{m['id']}", {"role": "Lames"})
        self.assertEqual((code, upd["role"]), (200, "Lames"))
        code, err = s.call("PATCH", f"/api/memberships/{m['id']}", {"is_leader": True})
        self.assertEqual((code, err["detail"]), (400, "Seul un PNJ peut diriger une faction"))
        other = self.faction(cid, "Autre")
        self.assertEqual(self.nest(other, guild, is_leader=True)[0], 400)
        self.assertEqual(s.call("PATCH", f"/api/memberships/{npc_m['id']}", {"is_leader": False})[0], 200)

        # Un seul membre : PNJ ou faction.
        self.assertEqual(s.call("POST", "/api/memberships", {"faction_id": guild["id"]})[0], 400)
        self.assertEqual(s.call("POST", "/api/memberships", {"npc_id": npc["id"], "member_faction_id": other["id"],
                                                             "faction_id": guild["id"]})[0], 400)
        self.assertEqual(s.call("POST", "/api/memberships", {"member_faction_id": 999999,
                                                             "faction_id": guild["id"]})[0], 404)
        self.assertEqual(s.call("POST", "/api/memberships", {"member_faction_id": 10**20,
                                                             "faction_id": guild["id"]})[0], 400)

        self.assertEqual(s.call("DELETE", f"/api/memberships/{m['id']}")[0], 204)
        self.assertEqual(len([x for x in self.graph(cid)["memberships"] if x["member_faction_id"]]), 1)

    def test_self_and_cycles_refused(self):
        cid = self.campaign("Cycles")
        a, b, c, d = (self.faction(cid, n) for n in ("Alpha", "Bêta", "Gamma", "Delta"))
        code, err = self.nest(a, a)
        self.assertEqual((code, err["detail"]), (400, "Une faction ne peut pas être membre d'elle-même"))
        self.assertEqual(self.nest(a, b)[0], 201)   # A dans B
        code, err = self.nest(b, a)                  # B dans A : cycle direct
        self.assertEqual(code, 400)
        self.assertEqual(err["detail"], "« Alpha » est déjà membre, directement ou non, de « Bêta » : "
                                        "cette appartenance formerait un cycle")
        self.assertEqual(self.nest(b, c)[0], 201)   # B dans C
        self.assertEqual(self.nest(c, d)[0], 201)   # C dans D
        code, err = self.nest(d, a)                  # D dans A : A dans B dans C dans D dans A
        self.assertEqual(code, 400)
        self.assertIn("« Alpha » est déjà membre, directement ou non, de « Delta »", err["detail"])
        # Message traduit (Accept-Language), noms des factions laissés tels quels.
        code, err = self.s.call("POST", "/api/memberships", {"member_faction_id": c["id"], "faction_id": a["id"]},
                                headers={"Accept-Language": "en"})
        self.assertEqual(code, 400)
        self.assertEqual(err["detail"], "“Alpha” is already a member, directly or indirectly, of “Gamma”: "
                                        "this membership would create a loop")
        # Losange autorisé : A dans B et dans C (déjà dans B), plusieurs chemins sans cycle.
        self.assertEqual(self.nest(a, c)[0], 201)
        self.assertEqual(self.nest(a, d)[0], 201)

    def test_other_campaign_and_other_account(self):
        s = self.s
        cid = self.campaign("Mienne")
        foreign = self.campaign("Étrangère")
        mine, there = self.faction(cid, "Ici"), self.faction(foreign, "Là-bas")
        code, err = self.nest(mine, there)
        self.assertEqual((code, err["detail"]), (400, "Les deux factions ne sont pas dans la même campagne"))
        sub = self.faction(foreign, "Sous Là-bas")
        _, m = self.nest(sub, there)

        _, user = s.call("POST", "/api/admin/users", {"username": "mj-factions", "role": "mj",
                                                      "password": "mot-de-passe-mj", "campaign_ids": [cid]})
        self.addCleanup(s.call, "DELETE", f"/api/admin/users/{user['id']}")
        mj = s.client()
        self.assertEqual(mj.login("mj-factions", "mot-de-passe-mj"), 200)
        # Faction d'une campagne inaccessible, comme membre ou comme faction : introuvable.
        for body in ({"member_faction_id": there["id"], "faction_id": mine["id"]},
                     {"member_faction_id": mine["id"], "faction_id": there["id"]}):
            code, err = mj.call("POST", "/api/memberships", body)
            self.assertEqual((code, err["detail"]), (404, "Faction introuvable"))
        self.assertEqual(mj.call("PATCH", f"/api/memberships/{m['id']}", {"role": "x"})[0], 404)
        self.assertEqual(mj.call("DELETE", f"/api/memberships/{m['id']}")[0], 404)
        self.assertEqual(mj.call("GET", f"/api/campaigns/{foreign}/graph")[0], 404)
        # Sa propre campagne : permis.
        sub2 = mj.call("POST", f"/api/campaigns/{cid}/factions", {"name": "Sous Ici"})[1]
        self.assertEqual(mj.call("POST", "/api/memberships",
                                 {"member_faction_id": sub2["id"], "faction_id": mine["id"]})[0], 201)
        self.assertEqual([x["member_faction_id"] for x in self.graph(foreign)["memberships"]], [sub["id"]])

    def test_cascade_on_delete(self):
        s = self.s
        cid = self.campaign("Cascade")
        top, mid, low = (self.faction(cid, n) for n in ("Haut", "Milieu", "Bas"))
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Tess"})
        s.call("POST", "/api/memberships", {"npc_id": npc["id"], "faction_id": mid["id"]})
        self.nest(mid, top)
        self.nest(low, mid)
        self.assertEqual(len(self.graph(cid)["memberships"]), 3)
        self.assertEqual(s.call("DELETE", f"/api/factions/{mid['id']}")[0], 204)
        self.assertEqual(self.graph(cid)["memberships"], [])   # comme membre et comme faction
        self.nest(low, top)
        self.assertEqual(s.call("DELETE", f"/api/factions/{top['id']}")[0], 204)
        self.assertEqual(self.graph(cid)["memberships"], [])
        # Campagne supprimée : plus aucune appartenance d'elle en base.
        a, b = self.faction(cid, "A"), self.faction(cid, "B")
        self.nest(a, b)
        self.assertEqual(s.call("DELETE", f"/api/campaigns/{cid}")[0], 204)
        with sqlite3.connect(Path(s.tmp.name) / "maze.db") as conn:
            left = conn.execute("SELECT COUNT(*) FROM memberships WHERE faction_id IN (?, ?) OR "
                                "member_faction_id IN (?, ?)", (a["id"], b["id"], a["id"], b["id"])).fetchone()[0]
        self.assertEqual(left, 0)

    def test_database_rules(self):
        """Règles tenues par la base elle-même (migration 031), même hors de l'API."""
        s = self.s
        cid = self.campaign("Base")
        a, b = self.faction(cid, "A"), self.faction(cid, "B")
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Brann"})
        with sqlite3.connect(Path(s.tmp.name) / "maze.db") as conn:
            for sql, args in (
                    ("INSERT INTO memberships (faction_id) VALUES (?)", (a["id"],)),
                    ("INSERT INTO memberships (npc_id, member_faction_id, faction_id) VALUES (?, ?, ?)",
                     (npc["id"], b["id"], a["id"])),
                    ("INSERT INTO memberships (member_faction_id, faction_id) VALUES (?, ?)", (a["id"], a["id"])),
                    ("INSERT INTO memberships (member_faction_id, faction_id, is_leader) VALUES (?, ?, 1)",
                     (b["id"], a["id"]))):
                with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                    conn.execute(sql, args)
            conn.execute("INSERT INTO memberships (member_faction_id, faction_id) VALUES (?, ?)", (b["id"], a["id"]))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO memberships (member_faction_id, faction_id) VALUES (?, ?)",
                             (b["id"], a["id"]))


class NestedFactionsMigrationTest(unittest.TestCase):
    """Migration 031 sur une base au schéma 030 (production actuelle) : appartenances de PNJ conservées telles
    quelles (identifiants compris), table reconstruite sans rien perdre."""

    def test_existing_memberships_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = root / "030"
            before.mkdir()
            for path in db.MIGRATIONS_DIR.glob("*.sql"):
                if int(path.name[:3]) <= 30:
                    shutil.copy(path, before)
            path = root / "maze.db"
            with mock.patch.object(db, "MIGRATIONS_DIR", before):
                db.migrate(path)
            with db.transaction(path) as conn:
                conn.execute("INSERT INTO campaigns (id, name) VALUES (1, 'Ys')")
                conn.execute("INSERT INTO npcs (id, campaign_id, name) VALUES (1, 1, 'Maëlis'), (2, 1, 'Hart')")
                conn.execute("INSERT INTO factions (id, campaign_id, name) VALUES (1, 1, 'Main'), (2, 1, 'Garde')")
                conn.execute("INSERT INTO memberships (id, npc_id, faction_id, role, is_leader) VALUES "
                             "(5, 1, 1, 'Lieutenante', 0), (9, 2, 2, 'Capitaine', 1), (12, 1, 2, '', 0)")
                rows = conn.execute("SELECT * FROM memberships ORDER BY id").fetchall()
            db.migrate(path)
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.migration_files()[-1][0])
                after = conn.execute("SELECT id, npc_id, faction_id, role, is_leader, member_faction_id "
                                     "FROM memberships ORDER BY id").fetchall()
                self.assertEqual(after, [tuple(r) + (None,) for r in rows])
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                indexes = {r[1]: r[2] for r in conn.execute("PRAGMA index_list(memberships)")}
                self.assertIn("ix_memberships_faction", indexes)
                self.assertEqual(sorted(v for k, v in indexes.items() if k.startswith("sqlite_autoindex")), [1, 1])
                self.assertEqual({r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE instr(sql, 'memberships__nested') OR "
                    "instr(name, 'memberships__nested')")}, set())
                # Suppression en cascade toujours en place (PNJ, faction).
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("DELETE FROM npcs WHERE id = 2")
                self.assertEqual([r[0] for r in conn.execute("SELECT id FROM memberships ORDER BY id")], [5, 12])
                conn.execute("INSERT INTO memberships (member_faction_id, faction_id) VALUES (2, 1)")
                conn.execute("DELETE FROM factions WHERE id = 2")
                self.assertEqual([r[0] for r in conn.execute("SELECT id FROM memberships ORDER BY id")], [5])


def _archive(data: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("campaign.json", json.dumps(data))
    return buf.getvalue()


class NestedFactionsTransferTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_export_import_keeps_nesting(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Ys imbriquée"})
        cid = camp["id"]
        mk = lambda name: s.call("POST", f"/api/campaigns/{cid}/factions", {"name": name})[1]  # noqa: E731
        council, watch, house = mk("Conseil"), mk("Garde"), mk("Valcourt")
        _, hart = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Hart"})
        s.call("POST", "/api/memberships", {"npc_id": hart["id"], "faction_id": watch["id"], "role": "Capitaine",
                                            "is_leader": True})
        for sub, role in ((watch, "Bras armé"), (house, "Siège")):
            self.assertEqual(s.call("POST", "/api/memberships", {"member_faction_id": sub["id"],
                                                                 "faction_id": council["id"], "role": role})[0], 201)
        code, raw = s.call("GET", f"/api/campaigns/{cid}/export")
        self.assertEqual(code, 200)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            data = json.loads(zf.read("campaign.json"))
        self.assertEqual(len(data["memberships"]), 3)
        code, new = s.call("POST", "/api/campaigns/import", raw=raw, content_type="application/zip")
        self.assertEqual(code, 201, new)
        _, graph = s.call("GET", f"/api/campaigns/{new['id']}/graph")
        name = {f["id"]: f["name"] for f in graph["factions"]}
        nested = sorted((name[m["member_faction_id"]], name[m["faction_id"]], m["role"])
                        for m in graph["memberships"] if m["member_faction_id"])
        self.assertEqual(nested, [("Garde", "Conseil", "Bras armé"), ("Valcourt", "Conseil", "Siège")])
        (npc_m,) = [m for m in graph["memberships"] if m["npc_id"]]
        self.assertEqual((name[npc_m["faction_id"]], npc_m["role"], npc_m["is_leader"]), ("Garde", "Capitaine", True))
        self.assertNotIn(council["id"], name)   # identifiants réécrits

    def test_old_archive_still_imports(self):
        """Archive d'avant la migration 031 (schéma 30) : pas de member_faction_id, PNJ membres seulement."""
        data = {
            "format": "maze-campaign", "version": 1, "schema": 30, "maze_version": "ancienne",
            "campaign": {"id": 7, "name": "Archive 030", "description": ""},
            "factions": [{"id": 3, "campaign_id": 7, "name": "Main Grise", "color": "#8e5aa8"}],
            "npcs": [{"id": 4, "campaign_id": 7, "name": "Silas"}, {"id": 5, "campaign_id": 7, "name": "Maëlis"}],
            "memberships": [{"id": 1, "npc_id": 4, "faction_id": 3, "role": "Lame", "is_leader": 0},
                            {"id": 2, "npc_id": 5, "faction_id": 3, "role": "Lieutenante", "is_leader": 1}],
        }
        code, new = self.s.call("POST", "/api/campaigns/import", raw=_archive(data), content_type="application/zip")
        self.assertEqual(code, 201, new)
        _, graph = self.s.call("GET", f"/api/campaigns/{new['id']}/graph")
        npcs = {n["id"]: n["name"] for n in graph["npcs"]}
        self.assertEqual(sorted((npcs[m["npc_id"]], m["role"], m["is_leader"], m["member_faction_id"])
                                for m in graph["memberships"]),
                         [("Maëlis", "Lieutenante", True, None), ("Silas", "Lame", False, None)])

    def test_crafted_archive_cannot_break_rules(self):
        """Archive modifiée à la main : appartenance à soi-même, cycle, membre double ou chef de faction ignorés."""
        data = {
            "format": "maze-campaign", "version": 1, "schema": 31, "maze_version": "test",
            "campaign": {"id": 1, "name": "Archive piégée", "description": ""},
            "factions": [{"id": i, "campaign_id": 1, "name": n} for i, n in ((1, "A"), (2, "B"), (3, "C"))],
            "npcs": [{"id": 9, "campaign_id": 1, "name": "Pnj"}],
            "memberships": [
                {"id": 1, "member_faction_id": 1, "faction_id": 1},                 # elle-même
                {"id": 2, "member_faction_id": 1, "faction_id": 2},                 # A dans B
                {"id": 3, "member_faction_id": 2, "faction_id": 3, "is_leader": 1},  # B dans C (chef ignoré)
                {"id": 4, "member_faction_id": 3, "faction_id": 1},                 # C dans A : cycle
                {"id": 5, "npc_id": 9, "member_faction_id": 3, "faction_id": 2},    # PNJ prioritaire
                {"id": 6, "member_faction_id": 99, "faction_id": 2},                # faction absente
                "pas un objet",
            ],
        }
        code, err = self.s.call("POST", "/api/campaigns/import", raw=_archive(data), content_type="application/zip")
        self.assertEqual(code, 400, err)    # ligne qui n'est pas un objet : archive refusée, comme ailleurs
        data["memberships"].pop()
        code, new = self.s.call("POST", "/api/campaigns/import", raw=_archive(data), content_type="application/zip")
        self.assertEqual(code, 201, new)
        _, graph = self.s.call("GET", f"/api/campaigns/{new['id']}/graph")
        name = {f["id"]: f["name"] for f in graph["factions"]}
        got = sorted((name[m["member_faction_id"]] if m["member_faction_id"] else "Pnj", name[m["faction_id"]],
                      m["is_leader"]) for m in graph["memberships"])
        self.assertEqual(got, [("A", "B", False), ("B", "C", False), ("Pnj", "B", False)])


class NestedFactionsBackupTest(unittest.TestCase):
    def test_backup_and_restore(self):
        s = Server()
        try:
            _, camp = s.call("POST", "/api/campaigns", {"name": "Sauvegardée"})
            cid = camp["id"]
            a = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "Parente"})[1]
            b = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "Fille"})[1]
            _, m = s.call("POST", "/api/memberships", {"member_faction_id": b["id"], "faction_id": a["id"],
                                                       "role": "Cellule"})
            code, entry = s.call("POST", "/api/admin/backups")
            self.assertEqual(code, 201, entry)
            s.call("DELETE", f"/api/memberships/{m['id']}")
            code, result = s.call("POST", f"/api/admin/backups/{entry['name']}/restore")
            self.assertEqual(code, 200, result)
            _, graph = s.call("GET", f"/api/campaigns/{cid}/graph")
            self.assertEqual([(x["id"], x["member_faction_id"], x["faction_id"], x["role"]) for x in graph["memberships"]],
                             [(m["id"], b["id"], a["id"], "Cellule")])
        finally:
            s.stop()


if __name__ == "__main__":
    unittest.main()
