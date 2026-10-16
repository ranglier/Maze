"""Chronologie d'une campagne : événements ordonnés, mentions, rétroliens, recherche (timeline.py, migration 032).

Lancer : cd backend && python -m unittest -q tests.test_timeline
"""

from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from tests.test_api import Server


class TimelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(Path(self.s.tmp.name) / "maze.db")
        conn.row_factory = sqlite3.Row
        return conn

    def campaign(self, name: str = "Chronique") -> int:
        code, camp = self.s.call("POST", "/api/campaigns", {"name": name})
        self.assertEqual(code, 201)
        return camp["id"]

    def event(self, cid: int, title: str, client=None, **fields) -> dict:
        code, ev = (client or self.s.admin).call("POST", f"/api/campaigns/{cid}/events", {"title": title, **fields})
        self.assertEqual(code, 201, ev)
        return ev

    def titles(self, cid: int, client=None) -> list[str]:
        code, data = (client or self.s.admin).call("GET", f"/api/campaigns/{cid}/events")
        self.assertEqual(code, 200)
        return [e["title"] for e in data["events"]]

    def mj(self, username: str, campaign_ids: list[int]):
        code, _ = self.s.call("POST", "/api/admin/users", {"username": username, "role": "mj",
                                                          "password": "mot-de-passe-mj", "campaign_ids": campaign_ids})
        self.assertEqual(code, 201)
        client = self.s.client()
        self.assertEqual(client.login(username, "mot-de-passe-mj"), 200)
        return client

    def test_flag(self):
        cid = self.campaign()
        code, camp = self.s.call("GET", f"/api/campaigns/{cid}")
        self.assertIs(camp["timeline_enabled"], False)
        code, camp = self.s.call("PATCH", f"/api/campaigns/{cid}", {"timeline_enabled": True})
        self.assertEqual(code, 200)
        self.assertIs(camp["timeline_enabled"], True)
        listed = next(c for c in self.s.call("GET", "/api/campaigns")[1] if c["id"] == cid)
        self.assertIs(listed["timeline_enabled"], True)
        self.assertIs(self.s.call("GET", f"/api/campaigns/{cid}")[1]["timeline_enabled"], True)
        self.assertEqual(self.s.call("PATCH", f"/api/campaigns/{cid}", {"timeline_enabled": "peut-être"})[0], 422)
        # Création de campagne : le drapeau reste désactivé (pas un champ de création).
        self.assertIs(self.s.call("POST", "/api/campaigns", {"name": "Autre"})[1]["timeline_enabled"], False)

    def test_crud(self):
        cid = self.campaign()
        self.assertEqual(self.s.call("GET", f"/api/campaigns/{cid}/events")[1], {"events": []})
        ev = self.event(cid, "Chute de Valdis", date_label="742.M41", description="<p>La cité tombe.</p>",
                        tags=["guerre", " Guerre ", "siège"])
        self.assertEqual(set(ev), {"id", "campaign_id", "sort_order", "date_label", "title", "description", "tags",
                                   "revision", "mentions", "created_at", "updated_at"})
        self.assertEqual((ev["campaign_id"], ev["date_label"], ev["tags"], ev["revision"], ev["mentions"]),
                         (cid, "742.M41", ["guerre", "siège"], 0, []))
        # Titre obligatoire, non vide ; longueurs bornées.
        url = f"/api/campaigns/{cid}/events"
        self.assertEqual(self.s.call("POST", url, {})[0], 422)
        self.assertEqual(self.s.call("POST", url, {"title": "   "})[0], 422)
        self.assertEqual(self.s.call("POST", url, {"title": "x" * 201})[0], 422)
        self.assertEqual(self.s.call("POST", url, {"title": "x", "date_label": "x" * 201})[0], 422)
        self.assertEqual(self.s.call("POST", url, {"title": "x", "description": "x" * 200_001})[0], 422)
        self.assertEqual(self.s.call("POST", url, {"title": "x", "inconnu": 1})[0], 422)
        # Modification.
        code, ev2 = self.s.call("PATCH", f"/api/events/{ev['id']}", {"title": "Chute", "tags": []})
        self.assertEqual(code, 200)
        self.assertEqual((ev2["title"], ev2["tags"], ev2["date_label"], ev2["revision"]), ("Chute", [], "742.M41", 1))
        self.assertEqual(self.s.call("PATCH", f"/api/events/{ev['id']}", {"title": ""})[0], 422)
        # Suppression.
        self.assertEqual(self.s.call("DELETE", f"/api/events/{ev['id']}")[0], 204)
        self.assertEqual(self.s.call("DELETE", f"/api/events/{ev['id']}")[0], 404)
        self.assertEqual(self.s.call("PATCH", f"/api/events/{ev['id']}", {"title": "x"})[0], 404)
        self.assertEqual(self.titles(cid), [])
        self.assertEqual(self.s.call("GET", "/api/campaigns/999999/events")[0], 404)
        self.assertEqual(self.s.call("POST", "/api/campaigns/999999/events", {"title": "x"})[0], 404)

    def test_before_id(self):
        cid = self.campaign()
        a = self.event(cid, "A")
        c = self.event(cid, "C")
        self.event(cid, "B", before_id=c["id"])
        self.event(cid, "Début", before_id=a["id"])
        self.event(cid, "Fin", before_id=None)
        self.assertEqual(self.titles(cid), ["Début", "A", "B", "C", "Fin"])
        orders = [e["sort_order"] for e in self.s.call("GET", f"/api/campaigns/{cid}/events")[1]["events"]]
        self.assertEqual(orders, sorted(set(orders)))   # renuméroté, sans doublon
        other = self.campaign("Ailleurs")
        foreign = self.event(other, "Étranger")
        code, err = self.s.call("POST", f"/api/campaigns/{cid}/events", {"title": "x", "before_id": foreign["id"]})
        self.assertEqual(code, 400)
        code, err_en = self.s.call("POST", f"/api/campaigns/{cid}/events", {"title": "x", "before_id": foreign["id"]},
                                   headers={"Accept-Language": "en"})
        self.assertEqual(code, 400)
        self.assertNotEqual(err["detail"], err_en["detail"])   # message traduit
        self.assertEqual(self.s.call("POST", f"/api/campaigns/{cid}/events", {"title": "x", "before_id": 999999})[0],
                         400)
        self.assertEqual(len(self.titles(cid)), 5)

    def test_order(self):
        cid = self.campaign()
        ids = [self.event(cid, t)["id"] for t in ("A", "B", "C")]
        url = f"/api/campaigns/{cid}/events/order"
        code, data = self.s.call("PUT", url, {"ids": [ids[2], ids[0], ids[1]]})
        self.assertEqual(code, 200)
        self.assertEqual([e["title"] for e in data["events"]], ["C", "A", "B"])
        self.assertEqual(self.titles(cid), ["C", "A", "B"])
        self.assertEqual(self.s.call("PUT", url, {"ids": ids[:2]})[0], 400)                 # incomplet
        self.assertEqual(self.s.call("PUT", url, {"ids": [ids[0], ids[0], ids[1], ids[2]]})[0], 400)  # doublon
        self.assertEqual(self.s.call("PUT", url, {"ids": [ids[0], ids[0], ids[1]]})[0], 400)
        other = self.event(self.campaign("Autre"), "Z")
        self.assertEqual(self.s.call("PUT", url, {"ids": [*ids, other["id"]]})[0], 400)
        self.assertEqual(self.titles(cid), ["C", "A", "B"])
        # Nouvel événement à la fin après un réordonnancement.
        self.event(cid, "D")
        self.assertEqual(self.titles(cid), ["C", "A", "B", "D"])

    def test_revision_conflict(self):
        cid = self.campaign()
        ev = self.event(cid, "Traité", description="<p>Signé.</p>")
        url = f"/api/events/{ev['id']}"
        code, ev1 = self.s.call("PATCH", url, {"title": "Traité de Sorn", "base_revision": 0})
        self.assertEqual((code, ev1["revision"]), (200, 1))
        # Pas de version : aucun contrôle.
        self.assertEqual(self.s.call("PATCH", url, {"date_label": "An 3"})[0], 200)
        code, conflict = self.s.call("PATCH", url, {"title": "Autre", "base_revision": 1})
        self.assertEqual(code, 409)
        self.assertEqual(set(conflict), {"detail", "author", "current"})
        self.assertEqual(conflict["author"], "")
        current = conflict["current"]
        self.assertEqual((current["title"], current["date_label"], current["revision"]), ("Traité de Sorn", "An 3", 2))
        self.assertIn("mentions", conflict["current"])
        self.assertEqual(self.s.call("GET", f"/api/campaigns/{cid}/events")[1]["events"][0]["title"], "Traité de Sorn")
        code, _ = self.s.call("PATCH", url, {"title": "Autre", "base_revision": 0}, headers={"Accept-Language": "en"})
        self.assertEqual(code, 409)

    def test_isolation(self):
        mine, theirs = self.campaign("Mienne"), self.campaign("Leur")
        lea = self.mj("lea-chrono", [theirs])
        ev = self.event(mine, "Secret")
        own = self.event(theirs, "À elle", client=lea)
        self.assertEqual(lea.call("GET", f"/api/campaigns/{mine}/events")[0], 404)
        self.assertEqual(lea.call("POST", f"/api/campaigns/{mine}/events", {"title": "x"})[0], 404)
        self.assertEqual(lea.call("PATCH", f"/api/events/{ev['id']}", {"title": "pris"})[0], 404)
        self.assertEqual(lea.call("DELETE", f"/api/events/{ev['id']}")[0], 404)
        self.assertEqual(lea.call("PUT", f"/api/campaigns/{mine}/events/order", {"ids": [ev["id"]]})[0], 404)
        self.assertEqual(lea.call("PATCH", f"/api/campaigns/{mine}", {"timeline_enabled": True})[0], 404)
        self.assertEqual(lea.call("POST", f"/api/campaigns/{theirs}/events",
                                  {"title": "x", "before_id": ev["id"]})[0], 400)
        self.assertEqual(lea.call("PUT", f"/api/campaigns/{theirs}/events/order",
                                  {"ids": [own["id"], ev["id"]]})[0], 400)
        self.assertEqual(self.titles(mine), ["Secret"])
        self.assertEqual(self.titles(theirs, client=lea), ["À elle"])

    def test_mentions_and_backlinks(self):
        cid, other = self.campaign("Mentions"), self.campaign("Voisine")
        npc = self.s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Ysolde"})[1]
        fac = self.s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "La Vigie"})[1]
        place = self.s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Valdis"})[1]
        doc = self.s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Intrigue"})[1]
        foreign = self.s.call("POST", f"/api/campaigns/{other}/npcs", {"name": "Étranger"})[1]

        def mention(kind, tid, label="x"):
            return f'<a class="mention" data-mention="{kind}:{tid}">{label}</a>'
        html = (f"<p>{mention('npc', npc['id'])} et {mention('faction', fac['id'])} à {mention('place', place['id'])}"
                f" ({mention('doc', doc['id'])}, {mention('npc', foreign['id'])}, {mention('npc', 999999)})</p>")
        first = self.event(cid, "Premier", date_label="An 1")
        ev = self.event(cid, "Rencontre", date_label="An 2", description=html)
        expected_mentions = [("npc", npc["id"]), ("faction", fac["id"]), ("place", place["id"]), ("doc", doc["id"])]
        self.assertEqual(sorted(map(tuple, ev["mentions"])), sorted(expected_mentions))
        # Le premier événement cite la Vigie ensuite (modification) : rétroliens dans l'ordre de la chronologie.
        code, first = self.s.call("PATCH", f"/api/events/{first['id']}",
                                  {"description": f"<p>{mention('faction', fac['id'])}</p>"})
        self.assertEqual(first["mentions"], [["faction", fac["id"]]])
        expected = [{"id": first["id"], "date_label": "An 1", "title": "Premier"},
                    {"id": ev["id"], "date_label": "An 2", "title": "Rencontre"}]
        self.assertEqual(self.s.call("GET", f"/api/factions/{fac['id']}")[1]["timeline"], expected)
        self.assertEqual(self.s.call("GET", f"/api/npcs/{npc['id']}")[1]["timeline"], expected[1:])
        self.assertEqual(self.s.call("GET", f"/api/places/{place['id']}")[1]["timeline"], expected[1:])
        self.assertEqual(self.s.call("GET", f"/api/npcs/{foreign['id']}")[1]["timeline"], [])
        # Ordre manuel inversé : rétroliens suivent.
        self.s.call("PUT", f"/api/campaigns/{cid}/events/order", {"ids": [ev["id"], first["id"]]})
        self.assertEqual(self.s.call("GET", f"/api/factions/{fac['id']}")[1]["timeline"], expected[::-1])
        # Mention retirée quand la description ne la cite plus, ou quand la fiche disparaît.
        self.s.call("PATCH", f"/api/events/{first['id']}", {"description": "<p>Rien.</p>"})
        self.assertEqual(self.s.call("GET", f"/api/factions/{fac['id']}")[1]["timeline"], expected[1:])
        self.assertEqual(self.s.call("DELETE", f"/api/npcs/{npc['id']}")[0], 204)
        events = self.s.call("GET", f"/api/campaigns/{cid}/events")[1]["events"]
        self.assertNotIn(["npc", npc["id"]], next(e for e in events if e["id"] == ev["id"])["mentions"])
        with closing(self.db()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM timeline_mentions WHERE target_kind = 'npc' "
                                          "AND target_id = ?", (npc["id"],)).fetchone()[0], 0)
        # Fiche créée : rétrolien vide.
        self.assertEqual(self.s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Neuf"})[1]["timeline"], [])

    def test_sync_function(self):
        from maze import timeline
        cid = self.campaign("Fonction")
        npc = self.s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Aldric"})[1]
        ev = self.event(cid, "Brut")
        with closing(self.db()) as conn, conn:
            timeline.sync_event_mentions(conn, ev["id"], f'<a data-mention="npc:{npc["id"]}">A</a>')
        self.assertEqual(self.s.call("GET", f"/api/npcs/{npc['id']}")[1]["timeline"],
                         [{"id": ev["id"], "date_label": "", "title": "Brut"}])

    def test_search(self):
        cid = self.campaign("Recherche")
        lea_camp = self.campaign("Cachée")
        ev = self.event(cid, "Couronnement", date_label="Hiver 742", tags=["dynastie"],
                        description='<p>Le <strong>sacre</strong> de la reine.</p>'
                                    '<div data-callout="secret"><p>x</p></div>')
        hidden = self.event(lea_camp, "Couronnement caché")

        def search(q, client=None):
            code, data = (client or self.s.admin).call("GET", f"/api/search?q={q}&campaign={cid}")
            self.assertEqual(code, 200)
            return [r for r in data["results"] if r["kind"] == "event"]
        for q in ("couronnement", "hiver", "sacre", "dynastie"):
            found = search(q)
            self.assertEqual([r["id"] for r in found], [ev["id"]], q)
        hit = search("sacre")[0]
        self.assertEqual((hit["campaign_id"], hit["campaign"], hit["doc_id"], hit["sub"]),
                         (cid, "Recherche", None, "Hiver 742"))
        self.assertIn("\x02", hit["snippet"])
        self.assertEqual(hit["date_label"], "Hiver 742")
        # Modifié : l'index suit ; supprimé : disparaît.
        self.s.call("PATCH", f"/api/events/{ev['id']}", {"title": "Abdication"})
        self.assertEqual(search("couronnement"), [])
        self.assertEqual([r["id"] for r in search("abdication")], [ev["id"]])
        # Un MJ ne trouve pas les événements des autres campagnes.
        lea = self.mj("lea-cherche", [cid])
        code, data = lea.call("GET", "/api/search?q=couronnement")
        self.assertNotIn(hidden["id"], [r["id"] for r in data["results"] if r["kind"] == "event"])
        self.s.call("DELETE", f"/api/events/{ev['id']}")
        self.assertEqual(search("abdication"), [])

    def test_campaign_delete_cascades(self):
        cid = self.campaign("Éphémère")
        npc = self.s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Passant"})[1]
        ev = self.event(cid, "Passage", description=f'<a data-mention="npc:{npc["id"]}">P</a>')
        self.assertEqual(self.s.call("DELETE", f"/api/campaigns/{cid}")[0], 204)
        with closing(self.db()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM timeline_events WHERE campaign_id = ?",
                                          (cid,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM timeline_mentions WHERE event_id = ?",
                                          (ev["id"],)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM search_index WHERE kind = 'event' AND ref_id = ?",
                                          (ev["id"],)).fetchone()[0], 0)

    def test_limit(self):
        from maze import db, timeline
        cid = self.campaign("Pléthore")
        # maze.db.connect : fonction html_text des déclencheurs de recherche.
        with closing(db.connect(Path(self.s.tmp.name) / "maze.db")) as conn, conn:
            conn.executemany("INSERT INTO timeline_events (campaign_id, sort_order, title) VALUES (?, ?, ?)",
                             [(cid, i, f"E{i}") for i in range(timeline.MAX_EVENTS - 1)])
        self.event(cid, "Dernier")
        code, err = self.s.call("POST", f"/api/campaigns/{cid}/events", {"title": "De trop"})
        self.assertEqual(code, 400)
        code, err_en = self.s.call("POST", f"/api/campaigns/{cid}/events", {"title": "De trop"},
                                   headers={"Accept-Language": "en"})
        self.assertNotEqual(err["detail"], err_en["detail"])
        self.assertEqual(len(self.s.call("GET", f"/api/campaigns/{cid}/events")[1]["events"]), timeline.MAX_EVENTS)


if __name__ == "__main__":
    unittest.main()
