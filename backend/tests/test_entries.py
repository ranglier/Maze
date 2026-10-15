"""Historique des versions et commentaires des fiches PNJ, factions et lieux (entry_history.py, migration 022).

Lancer : cd backend && python -m unittest -q tests.test_entries
"""

from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from tests.test_api import Server


class EntryHistoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()
        code, camp = cls.s.call("POST", "/api/campaigns", {"name": "Historique"})
        assert code == 201
        cls.cid = camp["id"]

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(Path(self.s.tmp.name) / "maze.db")
        conn.row_factory = sqlite3.Row
        return conn

    def npc(self, **fields) -> int:
        code, npc = self.s.call("POST", f"/api/campaigns/{self.cid}/npcs", {"name": "Vex", **fields})
        self.assertEqual(code, 201)
        return npc["id"]

    def versions(self, url: str) -> list[dict]:
        code, data = self.s.call("GET", f"{url}/versions")
        self.assertEqual(code, 200)
        return data["versions"]

    def age_versions(self, npc_id: int, minutes: int = 30) -> None:
        with closing(self.db()) as conn, conn:
            conn.execute(f"UPDATE entry_versions SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-{minutes} minutes') "
                         "WHERE npc_id = ?", (npc_id,))

    def test_versions_on_save(self):
        s = self.s
        nid = self.npc(appearance="<p>Grand et maigre.</p>")
        url = f"/api/npcs/{nid}"
        self.assertEqual(self.versions(url), [])
        # Déplacement sur la carte : pas de version.
        self.assertEqual(s.call("PATCH", url, {"pos_x": 10, "pos_y": 20})[0], 200)
        self.assertEqual(self.versions(url), [])
        # Premier enregistrement : état initial + nouvel état.
        self.assertEqual(s.call("PATCH", url, {"appearance": "<p>Grand, maigre, une cicatrice.</p>"})[0], 200)
        v = self.versions(url)
        self.assertEqual([x["label"] for x in v], ["", "État initial"])
        self.assertTrue(v[0]["is_current"])
        self.assertFalse(v[1]["is_current"])
        # Enregistrements rapprochés du même auteur, partis de l'état enregistré (base_revision, comme le
        # formulaire) : même version.
        rev = s.call("GET", url)[1]["revision"]
        s.call("PATCH", url, {"personality": "<p>Méfiant.</p>", "base_revision": rev})
        self.assertEqual(len(self.versions(url)), 2)
        # Plus tard : nouvelle version.
        self.age_versions(nid)
        s.call("PATCH", url, {"personality": "<p>Méfiant et rancunier.</p>"})
        v = self.versions(url)
        self.assertEqual(len(v), 3)
        code, old = s.call("GET", f"{url}/versions/{v[1]['id']}")
        self.assertEqual(code, 200)
        self.assertEqual(old["content"]["personality"], "<p>Méfiant.</p>")
        self.assertEqual(old["content"]["name"], "Vex")

    def test_same_account_two_tabs_keeps_both_texts(self):
        """Audit M1 (DATA-3) : même compte, deux onglets ouverts sur le même état. Le second enregistrement ne
        fond plus la version du premier : son texte reste dans l'historique."""
        import json
        import zlib
        s = self.s
        nid = self.npc()
        url = f"/api/npcs/{nid}"
        # Onglet 1, puis onglet 2 (ouvert avant) sans base_revision (connecteur, scripts) : deux versions.
        s.call("PATCH", url, {"notes": "<p>Texte de l'onglet 1 : long paragraphe</p>"})
        s.call("PATCH", url, {"notes": "<p>Texte de l'onglet 2</p>"})
        with closing(self.db()) as conn:
            contents = [json.loads(zlib.decompress(r[0]))["notes"] for r in conn.execute(
                "SELECT content FROM entry_versions WHERE npc_id = ?", (nid,))]
        self.assertTrue(any("onglet 1" in c for c in contents), contents)
        self.assertTrue(any("onglet 2" in c for c in contents), contents)
        # Avec la révision de départ : l'onglet resté sur l'ancien état est refusé (409), rien n'est écrasé.
        rev = s.call("GET", url)[1]["revision"]
        self.assertEqual(s.call("PATCH", url, {"notes": "<p>Onglet 1 bis</p>", "base_revision": rev})[0], 200)
        code, body = s.call("PATCH", url, {"notes": "<p>Onglet 2 bis</p>", "base_revision": rev})
        self.assertEqual(code, 409)
        self.assertEqual(body["current"]["notes"], "<p>Onglet 1 bis</p>")
        self.assertEqual(body["current"]["revision"], rev + 1)
        self.assertEqual(s.call("GET", url)[1]["notes"], "<p>Onglet 1 bis</p>")
        # Repris après le conflit (révision actuelle, new_version) : version distincte, celle de l'onglet 1 reste.
        count = len(self.versions(url))
        code, _ = s.call("PATCH", url, {"notes": "<p>Onglet 2 bis</p>", "base_revision": rev + 1, "new_version": True})
        self.assertEqual(code, 200)
        self.assertEqual(len(self.versions(url)), count + 1)

    def test_revision_and_conflicts(self):
        """Audit M1 : révision par fiche, base_revision facultatif (409 si elle a changé)."""
        s = self.s
        code, npc = s.call("POST", f"/api/campaigns/{self.cid}/npcs", {"name": "Hart", "notes": "<p>v0</p>"})
        self.assertEqual(npc["revision"], 0)
        url = f"/api/npcs/{npc['id']}"
        # Positions et couverture ne changent pas la révision ; le texte, si.
        s.call("PATCH", url, {"pos_x": 3, "pos_y": 4})
        self.assertEqual(s.call("GET", url)[1]["revision"], 0)
        s.call("PATCH", url, {"notes": "<p>v0</p>"})  # valeur identique
        self.assertEqual(s.call("GET", url)[1]["revision"], 0)
        # Deux MJ partis de la révision 0 : le premier passe, le second reçoit 409 avec l'état actuel.
        other = s.client()
        s.call("POST", "/api/admin/users", {"username": "mj-rev", "role": "mj", "password": "mot-de-passe-mj",
                                            "campaign_ids": [self.cid]})
        self.assertEqual(other.login("mj-rev", "mot-de-passe-mj"), 200)
        code, saved = other.call("PATCH", url, {"notes": "<p>v0</p><p>Ajout de B</p>", "base_revision": 0})
        self.assertEqual((code, saved["revision"]), (200, 1))
        code, body = s.call("PATCH", url, {"notes": "<p>v0</p><p>Ajout de A</p>", "base_revision": 0})
        self.assertEqual(code, 409)
        self.assertIn("modifiée", body["detail"])
        self.assertEqual(body["current"]["notes"], "<p>v0</p><p>Ajout de B</p>")
        self.assertEqual(body["author"], "mj-rev")
        self.assertIn("Ajout de B", s.call("GET", url)[1]["notes"])
        # Sans base_revision (connecteur update_entry, scripts) : aucune vérification.
        code, saved = s.call("PATCH", url, {"summary": "Sans contrôle"})
        self.assertEqual((code, saved["revision"]), (200, 2))
        # Restauration d'une version : la révision change aussi.
        v = self.versions(url)
        s.call("POST", f"{url}/versions/{v[-1]['id']}/restore")
        self.assertEqual(s.call("GET", url)[1]["revision"], 3)
        # Faction et lieu : même règle.
        code, faction = s.call("POST", f"/api/campaigns/{self.cid}/factions", {"name": "Guilde"})
        furl = f"/api/factions/{faction['id']}"
        self.assertEqual(s.call("PATCH", furl, {"goals": "<p>a</p>", "base_revision": 0})[0], 200)
        self.assertEqual(s.call("PATCH", furl, {"goals": "<p>b</p>", "base_revision": 0})[0], 409)
        code, place = s.call("POST", f"/api/campaigns/{self.cid}/places", {"name": "Port"})
        purl = f"/api/places/{place['id']}"
        self.assertEqual(s.call("PATCH", purl, {"description": "<p>a</p>", "base_revision": 0})[0], 200)
        code, body = s.call("PATCH", purl, {"description": "<p>b</p>", "base_revision": 0})
        self.assertEqual((code, body["current"]["description"]), (409, "<p>a</p>"))
        # Type de lieu supprimé : le type des lieux change, la révision aussi.
        code, kind = s.call("POST", f"/api/campaigns/{self.cid}/place-kinds", {"label": "Docks"})
        s.call("PATCH", purl, {"kind": kind["key"]})
        before = s.call("GET", purl)[1]["revision"]
        s.call("DELETE", f"/api/place-kinds/{kind['id']}")
        self.assertEqual(s.call("GET", purl)[1]["revision"], before + 1)

    def test_change_outside_history_is_kept(self):
        s = self.s
        nid = self.npc(notes="<p>Un</p>")
        url = f"/api/npcs/{nid}"
        s.call("PATCH", url, {"notes": "<p>Deux</p>"})
        # La version la plus récente est supprimée : l'état « Deux » n'est plus dans l'historique.
        latest = self.versions(url)[0]
        self.assertEqual(s.call("DELETE", f"{url}/versions/{latest['id']}")[0], 204)
        s.call("PATCH", url, {"notes": "<p>Trois</p>"})
        v = self.versions(url)
        self.assertEqual([x["label"] for x in v], ["", "État précédent", "État initial"])
        code, kept = s.call("GET", f"{url}/versions/{v[1]['id']}")
        self.assertEqual(kept["content"]["notes"], "<p>Deux</p>")
        # Commentaire vide refusé.
        self.assertEqual(s.call("POST", f"{url}/comments", {"body": "   "})[0], 422)

    def test_auto_versions_pruned_but_labelled_kept(self):
        s = self.s
        nid = self.npc(notes="<p>0</p>")
        url = f"/api/npcs/{nid}"
        for i in range(1, 56):
            self.age_versions(nid)
            s.call("PATCH", url, {"notes": f"<p>{i}</p>"})
        v = self.versions(url)
        self.assertEqual(sum(1 for x in v if not x["label"]), 50)
        self.assertIn("État initial", [x["label"] for x in v])

    def test_restore_and_named_versions(self):
        s = self.s
        nid = self.npc(summary="Contrebandier", tags=["port"])
        url = f"/api/npcs/{nid}"
        s.call("PATCH", url, {"summary": "Contrebandier repenti", "tags": ["port", "allié"]})
        initial = self.versions(url)[-1]
        self.assertEqual(initial["label"], "État initial")
        # Version nommée, renommage, suppression.
        code, named = s.call("POST", f"{url}/versions", {"label": "Avant la séance 3"})
        self.assertEqual(code, 201)
        self.assertEqual(named["kind"], "manual")
        code, renamed = s.call("PATCH", f"{url}/versions/{named['id']}", {"label": "Séance 3"})
        self.assertEqual(renamed["label"], "Séance 3")
        # Restauration de l'état initial.
        code, out = s.call("POST", f"{url}/versions/{initial['id']}/restore")
        self.assertEqual(code, 200)
        self.assertIn("summary", out["restored"])
        code, npc = s.call("GET", url)
        self.assertEqual(npc["summary"], "Contrebandier")
        self.assertEqual(npc["tags"], ["port"])
        v = self.versions(url)
        self.assertEqual(v[0]["kind"], "restore")
        self.assertTrue(v[0]["is_current"])
        self.assertEqual(s.call("DELETE", f"{url}/versions/{named['id']}")[0], 204)
        self.assertEqual(s.call("GET", f"{url}/versions/{named['id']}")[0], 404)

    def test_place_history_and_missing_kind(self):
        s = self.s
        code, kind = s.call("POST", f"/api/campaigns/{self.cid}/place-kinds", {"label": "Taverne"})
        self.assertEqual(code, 201)
        code, place = s.call("POST", f"/api/campaigns/{self.cid}/places",
                             {"name": "Le Chaudron", "kind": kind["key"], "ambiance": "<p>Enfumé.</p>"})
        self.assertEqual(code, 201)
        url = f"/api/places/{place['id']}"
        s.call("PATCH", url, {"ambiance": "<p>Enfumé, bruyant.</p>", "kind": "lieu"})
        initial = self.versions(url)[-1]
        # Le type d'origine disparaît : la restauration garde le type actuel.
        self.assertEqual(s.call("DELETE", f"/api/place-kinds/{kind['id']}")[0], 204)
        code, out = s.call("POST", f"{url}/versions/{initial['id']}/restore")
        self.assertEqual(code, 200)
        self.assertNotIn("kind", out["restored"])
        code, place = s.call("GET", url)
        self.assertEqual(place["ambiance"], "<p>Enfumé.</p>")
        self.assertEqual(place["kind"], "lieu")

    def test_comments(self):
        s = self.s
        nid = self.npc(secrets="<p>Il travaille pour la Vigie.</p>")
        url = f"/api/npcs/{nid}"
        code, thread = s.call("POST", f"{url}/comments",
                              {"body": "À révéler en séance 4 ?", "field": "secrets", "quote": "la Vigie"})
        self.assertEqual(code, 201)
        self.assertEqual(thread["field"], "secrets")
        self.assertEqual(s.call("POST", f"{url}/comments", {"body": "x", "field": "inexistant"})[0], 400)
        code, reply = s.call("POST", f"{url}/comments", {"body": "Plutôt la 5.", "parent_id": thread["id"]})
        self.assertEqual(code, 201)
        # Une réponse à une réponse, ou à un fil d'une autre fiche : refusée.
        self.assertEqual(s.call("POST", f"{url}/comments", {"body": "x", "parent_id": reply["id"]})[0], 400)
        other = self.npc()
        self.assertEqual(s.call("POST", f"/api/npcs/{other}/comments", {"body": "x", "parent_id": thread["id"]})[0], 400)
        code, threads = s.call("GET", f"{url}/comments")
        self.assertEqual(len(threads), 1)
        self.assertEqual(len(threads[0]["replies"]), 1)
        # Résolution (fils seulement).
        self.assertEqual(s.call("PATCH", f"/api/entry-comments/{thread['id']}", {"resolved": True})[0], 200)
        self.assertEqual(s.call("PATCH", f"/api/entry-comments/{reply['id']}", {"resolved": True})[0], 400)
        code, threads = s.call("GET", f"{url}/comments")
        self.assertTrue(threads[0]["resolved"])
        # Recherche globale.
        code, found = s.call("GET", f"/api/search?q=seance&campaign={self.cid}")
        hit = next(r for r in found["results"] if r["kind"] == "entry_comment")
        self.assertEqual((hit["entry_kind"], hit["entry_id"], hit["entry_name"]), ("npc", nid, "Vex"))
        # Un commentaire ne modifie pas la fiche, ni son historique.
        self.assertEqual(self.versions(url), [])
        # Suppression de la fiche : fils, versions et index partent avec elle.
        s.call("PATCH", url, {"notes": "<p>x</p>"})
        self.assertEqual(s.call("DELETE", url)[0], 204)
        with closing(self.db()) as conn, conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM entry_comments WHERE npc_id = ?", (nid,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM entry_versions WHERE npc_id = ?", (nid,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM search_index WHERE kind = 'entry_comment' AND ref_id IN (?, ?)",
                                          (thread["id"], reply["id"])).fetchone()[0], 0)

    def test_comment_access_and_authorship(self):
        s = self.s
        nid = self.npc()
        code, thread = s.call("POST", f"/api/npcs/{nid}/comments", {"body": "Note de l'admin"})
        self.assertEqual(code, 201)
        code, _ = s.call("POST", "/api/admin/users", {"username": "mj2", "password": "motdepasse-mj2", "role": "mj"})
        self.assertEqual(code, 201)
        mj = s.client()
        self.assertEqual(mj.login("mj2", "motdepasse-mj2"), 200)
        # Campagne non attribuée : tout est introuvable.
        self.assertEqual(mj.call("GET", f"/api/npcs/{nid}/comments")[0], 404)
        self.assertEqual(mj.call("GET", f"/api/npcs/{nid}/versions")[0], 404)
        self.assertEqual(mj.call("PATCH", f"/api/entry-comments/{thread['id']}", {"resolved": True})[0], 404)
        self.assertEqual(mj.call("DELETE", f"/api/entry-comments/{thread['id']}")[0], 404)

    def test_export_import_keeps_comments(self):
        s = self.s
        code, camp = s.call("POST", "/api/campaigns", {"name": "Export"})
        cid = camp["id"]
        code, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Ilse"})
        code, place = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Port"})
        code, t1 = s.call("POST", f"/api/npcs/{npc['id']}/comments", {"body": "Fil PNJ", "field": "notes"})
        s.call("POST", f"/api/npcs/{npc['id']}/comments", {"body": "Réponse", "parent_id": t1["id"]})
        s.call("POST", f"/api/places/{place['id']}/comments", {"body": "Fil lieu"})
        code, archive = s.call("GET", f"/api/campaigns/{cid}/export")
        self.assertEqual(code, 200)
        code, imported = s.call("POST", "/api/campaigns/import", raw=archive, content_type="application/zip")
        self.assertEqual(code, 201)
        code, graph = s.call("GET", f"/api/campaigns/{imported['id']}/graph")
        new_npc = graph["npcs"][0]["id"]
        code, threads = s.call("GET", f"/api/npcs/{new_npc}/comments")
        self.assertEqual([(t["body"], t["field"], len(t["replies"])) for t in threads], [("Fil PNJ", "notes", 1)])
        code, places = s.call("GET", f"/api/campaigns/{imported['id']}/places")
        new_place = places["places"][0]["id"]
        code, threads = s.call("GET", f"/api/places/{new_place}/comments")
        self.assertEqual([t["body"] for t in threads], ["Fil lieu"])

    def test_import_rejects_invalid_comment_trees(self):
        import io
        import json
        import zipfile
        s = self.s
        code, camp = s.call("POST", "/api/campaigns", {"name": "Archive piégée"})
        cid = camp["id"]
        s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "A"})
        s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "B"})
        code, archive = s.call("GET", f"/api/campaigns/{cid}/export")
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            files = {n: z.read(n) for n in z.namelist()}
        data = json.loads(files["campaign.json"])
        a, b = (n["id"] for n in sorted(data["npcs"], key=lambda n: n["name"]))
        data["entry_comments"] = [
            {"id": 1, "npc_id": a, "body": "Fil", "field": "inconnu", "quote": "x" * 900, "resolved": "oui"},
            {"id": 2, "npc_id": a, "parent_id": 1, "body": "Réponse", "field": "secrets", "resolved": 1},
            {"id": 3, "npc_id": a, "parent_id": 2, "body": "Réponse à une réponse"},
            {"id": 4, "npc_id": b, "parent_id": 1, "body": "Réponse d'une autre fiche"},
            {"id": 5, "npc_id": a, "faction_id": 1, "body": "Deux propriétaires"},
            {"id": 6, "npc_id": a, "body": "   "},
        ]
        files["campaign.json"] = json.dumps(data).encode()
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for name, content in files.items():
                z.writestr(name, content)
        code, imported = s.call("POST", "/api/campaigns/import", raw=out.getvalue(), content_type="application/zip")
        self.assertEqual(code, 201)
        code, graph = s.call("GET", f"/api/campaigns/{imported['id']}/graph")
        ids = {n["name"]: n["id"] for n in graph["npcs"]}
        code, threads = s.call("GET", f"/api/npcs/{ids['A']}/comments")
        self.assertEqual(len(threads), 1)
        t = threads[0]
        self.assertEqual((t["field"], len(t["quote"]), t["resolved"]), ("", 500, False))
        self.assertEqual([(r["body"], r["field"], r["resolved"]) for r in t["replies"]], [("Réponse", "", False)])
        self.assertEqual(s.call("GET", f"/api/npcs/{ids['B']}/comments")[1], [])


if __name__ == "__main__":
    unittest.main()
