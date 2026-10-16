"""Campagne de démonstration (`python -m maze.demo`, audit QUAL-16) : premier geste d'un nouvel utilisateur.

Lancer : cd backend && python -m unittest -q tests.test_demo
"""

from __future__ import annotations

import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.test_api import Server


def _run_demo() -> tuple[int, str]:
    from maze import demo
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cid = demo.main()
    return cid, out.getvalue()


class DemoTest(unittest.TestCase):
    def test_main_on_empty_data_dir(self):
        """demo.main() sur un dossier de données vide : base créée et migrée, campagne complète."""
        with tempfile.TemporaryDirectory() as tmp:
            saved = os.environ.get("MAZE_DATA_DIR")
            os.environ["MAZE_DATA_DIR"] = tmp
            try:
                cid, printed = _run_demo()
            finally:
                if saved is None:
                    os.environ.pop("MAZE_DATA_DIR", None)
                else:
                    os.environ["MAZE_DATA_DIR"] = saved
            self.assertIn(f"Campagne de démonstration créée (id {cid})", printed)
            with closing(sqlite3.connect(Path(tmp) / "maze.db")) as conn:
                count = lambda sql: conn.execute(sql, (cid,)).fetchone()[0]  # noqa: E731
                self.assertEqual(count("SELECT COUNT(*) FROM npcs WHERE campaign_id = ?"), 9)
                self.assertEqual(count("SELECT COUNT(*) FROM factions WHERE campaign_id = ?"), 5)
                self.assertEqual(count("SELECT COUNT(*) FROM places WHERE campaign_id = ?"), 13)
                self.assertEqual(count("SELECT COUNT(*) FROM relations WHERE campaign_id = ?"), 12)
                self.assertEqual(count("""SELECT COUNT(*) FROM memberships m JOIN npcs n ON n.id = m.npc_id
                                           WHERE n.campaign_id = ?"""), 9)
                # Sous-factions (migration 031) : la Garde et la maison Valcourt membres du Conseil du Port.
                self.assertEqual(count("""SELECT COUNT(*) FROM memberships m JOIN factions f ON f.id = m.faction_id
                                           WHERE f.campaign_id = ? AND m.member_faction_id IS NOT NULL"""), 2)
                self.assertEqual(count("""SELECT COUNT(*) FROM place_links l JOIN places p ON p.id = l.place_id
                                           WHERE p.campaign_id = ?"""), 14)
                # Chaque lieu a un type connu de la campagne (sans quoi l'interface et le connecteur le
                # montrent comme « Lieu » et refusent de l'enregistrer tel quel).
                self.assertEqual(count("""SELECT COUNT(*) FROM places p WHERE p.campaign_id = ? AND NOT EXISTS
                                           (SELECT 1 FROM place_kinds k WHERE k.campaign_id = p.campaign_id
                                               AND k.key = p.kind)"""), 0)
                # Index de recherche tenu à jour par les déclencheurs.
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM search_index WHERE search_index MATCH ?",
                                              ('"maelis"*',)).fetchone()[0] >= 1, True)

    def test_demo_campaign_through_api(self):
        """Campagne de démonstration créée dans une instance en service : lisible par l'API comme une autre."""
        s = Server()
        try:
            cid, _ = _run_demo()   # même MAZE_DATA_DIR que le serveur de test
            code, graph = s.call("GET", f"/api/campaigns/{cid}/graph")
            self.assertEqual(code, 200)
            self.assertEqual(len(graph["npcs"]), 9)
            self.assertEqual(len(graph["factions"]), 5)
            nested = [m for m in graph["memberships"] if m["member_faction_id"]]
            self.assertEqual(len(nested), 2)
            self.assertEqual({m["npc_id"] for m in nested}, {None})
            code, places = s.call("GET", f"/api/campaigns/{cid}/places")
            self.assertEqual(code, 200)
            code, found = s.call("GET", "/api/search?q=Corvin")
            self.assertEqual(code, 200)
            self.assertIn("npc", {r["kind"] for r in found["results"]})
            code, campaigns = s.call("GET", "/api/campaigns")
            self.assertIn(cid, [c["id"] for c in campaigns])
        finally:
            s.stop()


if __name__ == "__main__":
    unittest.main()
