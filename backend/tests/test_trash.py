"""Corbeille (trash.py, migration 034) : suppression de chaque sorte d'élément puis restauration à l'identique (toutes
les tables, index de recherche et fichiers comparés avant et après), éléments liés disparus entre-temps, identifiant
repris, purge après 30 jours, vidage, droits, suppression par le connecteur Claude, ménage des illustrations,
sauvegarde.

Lancer : cd backend && python -m unittest -q tests.test_trash
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import sqlite3
import tarfile
import unittest
import urllib.parse
from datetime import timedelta
from pathlib import Path

from tests.test_api import Server, multipart, png_bytes
from tests.test_maps import form

from maze import backup, db, trash
from maze.mediafiles import TRASH_DIR

# Tables qui changent sans rapport avec l'élément restauré (date de la campagne, journaux, comptes).
IGNORED = {"campaigns", "trash", "sqlite_sequence", "schema_migrations", "mcp_log", "users", "login_addresses"}


class TrashTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()
        cls.data = Path(cls.s.tmp.name)
        cls.media = cls.data / "media"
        cls.db_path = cls.data / "maze.db"

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    # --- Outils ---------------------------------------------------------------------------------

    def campaign(self, name: str, timeline: bool = False) -> int:
        _, camp = self.s.call("POST", "/api/campaigns", {"name": name})
        if timeline:
            self.s.call("PATCH", f"/api/campaigns/{camp['id']}", {"timeline_enabled": True})
        return camp["id"]

    def snapshot(self) -> dict:
        """Toutes les lignes de la base (sauf IGNORED), index de recherche compris ; époque temps réel à part."""
        conn = sqlite3.connect(self.db_path)
        try:
            out = {}
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
                if name in IGNORED or name.startswith("search_index"):
                    continue
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")') if r[1] != "live_epoch"]
                out[name] = sorted(conn.execute(f'SELECT {", ".join(cols)} FROM "{name}"').fetchall(), key=repr)
            out["search_index"] = sorted(conn.execute(
                "SELECT rowid, kind, ref_id, campaign_id, doc_id, title, body FROM search_index").fetchall(), key=repr)
            return out
        finally:
            conn.close()

    def files(self, cid: int) -> dict[str, str]:
        folder = self.media / str(cid)
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob("*") if p.is_file()} \
            if folder.is_dir() else {}

    def trashed_files(self, cid: int) -> set[str]:
        folder = self.media / str(cid) / TRASH_DIR
        return {p.name for p in folder.iterdir()} if folder.is_dir() else set()

    def items(self, cid: int, client=None) -> list[dict]:
        code, out = (client or self.s).call("GET", f"/api/campaigns/{cid}/trash")
        self.assertEqual(code, 200, out)
        return out["items"]

    def delete(self, path: str) -> int:
        code, out = self.s.call("DELETE", path)
        self.assertEqual(code, 200, out)
        self.assertIsInstance(out["trash_id"], int)
        return out["trash_id"]

    def restore(self, trash_id: int) -> dict:
        code, out = self.s.call("POST", f"/api/trash/{trash_id}/restore")
        self.assertEqual(code, 200, out)
        return out

    def found(self, cid: int, q: str) -> set[tuple[str, int]]:
        _, out = self.s.call("GET", f"/api/search?q={urllib.parse.quote(q)}&campaign={cid}")
        return {(r["kind"], r["id"]) for r in out["results"]}

    def gallery(self, kind: str, eid: int, n: int = 1) -> dict:
        body, ctype = multipart([(f"img{i}.png", png_bytes((20 * i, 90, 40))) for i in range(n)])
        code, out = self.s.call("POST", f"/api/{kind}/{eid}/images", raw=body, content_type=ctype)
        self.assertEqual(code, 201, out)
        return out

    def round_trip(self, cid: int, path: str, kind: str, object_id: int) -> dict:
        """Supprime, vérifie la corbeille, restaure, compare base et fichiers à l'état d'avant."""
        before, files = self.snapshot(), self.files(cid)
        tid = self.delete(path)
        self.assertNotEqual(self.snapshot(), before)
        item = next(i for i in self.items(cid) if i["id"] == tid)
        self.assertEqual((item["kind"], item["object_id"], item["via"]), (kind, object_id, "interface"))
        self.assertEqual(item["deleted_by_name"], "Administrateur")
        self.assertGreater(item["expires_at"], item["deleted_at"])
        self.assertEqual(self.s.call("GET", path)[0], 404)
        self.assertEqual(self.restore(tid), {"kind": kind, "object_id": object_id})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.files(cid), files)
        self.assertEqual(self.trashed_files(cid), set())
        self.assertNotIn(tid, [i["id"] for i in self.items(cid)])
        self.assertEqual(self.s.call("POST", f"/api/trash/{tid}/restore")[0], 404)   # restaurée deux fois
        return item

    # --- Chaque sorte d'élément -------------------------------------------------------------------

    def test_npc_with_everything(self):
        s = self.s
        cid = self.campaign("Corbeille PNJ", timeline=True)
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Inquisitrice Vael", "summary": "Zélée"})
        _, other = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Frère Odo"})
        _, faction = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "Ordo Hereticus"})
        _, place = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Bastion"})
        detail = self.gallery("npcs", npc["id"], 2)
        self.assertIsNotNone(detail["portrait_image_id"])
        s.call("POST", "/api/memberships", {"npc_id": npc["id"], "faction_id": faction["id"], "is_leader": True})
        s.call("POST", f"/api/campaigns/{cid}/relations", {
            "source_kind": "npc", "source_id": npc["id"], "target_kind": "npc", "target_id": other["id"]})
        s.call("POST", f"/api/places/{place['id']}/links", {"entity_kind": "npc", "entity_id": npc["id"], "role": "Garde"})
        s.call("PATCH", f"/api/npcs/{npc['id']}", {"summary": "Très zélée"})            # une version
        s.call("POST", f"/api/npcs/{npc['id']}/comments", {"body": "Trop méchante ?"})
        mention = f'<a class="mention" data-mention="npc:{npc["id"]}">Vael</a>'
        _, scen = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Bal", "content": f"<p>{mention}</p>"})
        s.call("PUT", f"/api/documents/{scen['id']}/map/nodes/npc/{npc['id']}", {"state": "added", "x": 10, "y": 20})
        s.call("POST", f"/api/campaigns/{cid}/events", {"title": "Arrivée", "description": f"<p>{mention}</p>"})
        self.assertIn(("npc", npc["id"]), self.found(cid, "Vael"))

        item = self.round_trip(cid, f"/api/npcs/{npc['id']}", "npc", npc["id"])
        self.assertEqual(item["summary"], "2 images, 1 lien")
        self.assertEqual(item["name"], "Inquisitrice Vael")
        self.assertIn(("npc", npc["id"]), self.found(cid, "Vael"))
        _, back = s.call("GET", f"/api/npcs/{npc['id']}")
        self.assertEqual(back["portrait_image_id"], detail["portrait_image_id"])
        self.assertEqual(s.call("GET", back["images"][0]["url"])[0], 200)
        self.assertEqual([d["id"] for d in back["mentioned_in"]], [scen["id"]])

    def test_files_moved_not_served(self):
        s = self.s
        cid = self.campaign("Corbeille fichiers")
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Porteur"})
        url = self.gallery("npcs", npc["id"])["images"][0]["url"]
        name = url.rsplit("/", 1)[1]
        tid = self.delete(f"/api/npcs/{npc['id']}")
        self.assertIn(name, self.trashed_files(cid))
        self.assertFalse((self.media / str(cid) / name).exists())
        self.assertEqual(s.call("GET", url)[0], 404)
        self.assertEqual(s.call("GET", f"/media/{cid}/{TRASH_DIR}/{name}")[0], 404)   # jamais servi
        self.restore(tid)
        self.assertEqual(s.call("GET", url)[0], 200)

    def test_faction_with_subfactions(self):
        s = self.s
        cid = self.campaign("Corbeille factions")
        _, top = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "Imperium"})
        _, mid = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "Inquisition"})
        _, low = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "Ordo Malleus"})
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Coteaz"})
        s.call("POST", "/api/memberships", {"member_faction_id": mid["id"], "faction_id": top["id"]})
        s.call("POST", "/api/memberships", {"member_faction_id": low["id"], "faction_id": mid["id"]})
        s.call("POST", "/api/memberships", {"npc_id": npc["id"], "faction_id": mid["id"]})
        self.gallery("factions", mid["id"])
        self.round_trip(cid, f"/api/factions/{mid['id']}", "faction", mid["id"])
        _, graph = s.call("GET", f"/api/campaigns/{cid}/graph")
        self.assertEqual(len(graph["memberships"]), 3)

    def test_place_with_subplaces_and_pin(self):
        s = self.s
        cid = self.campaign("Corbeille lieux")
        _, sector = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Secteur"})
        _, world = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Valdis", "parent_id": sector["id"]})
        _, a = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Ruche", "parent_id": world["id"]})
        _, b = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Spatioport", "parent_id": world["id"]})
        self.gallery("places", world["id"])
        body, ctype = form({"name": "Carte de Valdis", "place_id": str(world["id"])}, ("fond.png", png_bytes()))
        code, smap = s.call("POST", f"/api/campaigns/{cid}/maps", raw=body, content_type=ctype)
        self.assertEqual(code, 201, smap)
        body, ctype = form({"name": "Secteur"}, ("s.png", png_bytes()))
        _, outer = s.call("POST", f"/api/campaigns/{cid}/maps", raw=body, content_type=ctype)
        s.call("PUT", f"/api/maps/{outer['id']}/pins/{world['id']}", {"x": 0.5, "y": 0.5})
        s.call("PUT", f"/api/places/{world['id']}/pin")
        tid = self.delete(f"/api/places/{world['id']}")
        self.assertEqual(s.call("GET", f"/api/places/{a['id']}")[1]["parent_id"], sector["id"])   # remontés
        self.assertIsNone(s.call("GET", f"/api/maps/{smap['id']}")[1]["place_id"])
        item = next(i for i in self.items(cid) if i["id"] == tid)
        self.assertEqual(item["summary"], "2 sous-lieux, 1 image")
        self.restore(tid)
        for child in (a, b):
            self.assertEqual(s.call("GET", f"/api/places/{child['id']}")[1]["parent_id"], world["id"])
        self.assertEqual(s.call("GET", f"/api/maps/{smap['id']}")[1]["place_id"], world["id"])
        # Puis la comparaison complète, en une autre suppression.
        self.round_trip(cid, f"/api/places/{world['id']}", "place", world["id"])

    def test_subplace_moved_meanwhile_stays(self):
        s = self.s
        cid = self.campaign("Corbeille sous-lieu déplacé")
        _, world = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Valdis"})
        _, other = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Sorn"})
        _, child = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Ruche", "parent_id": world["id"]})
        tid = self.delete(f"/api/places/{world['id']}")
        s.call("PUT", f"/api/places/{child['id']}/move", {"parent_id": other["id"], "index": 0})
        self.restore(tid)
        self.assertEqual(s.call("GET", f"/api/places/{child['id']}")[1]["parent_id"], other["id"])

    def test_place_parent_deleted_meanwhile_goes_to_root(self):
        s = self.s
        cid = self.campaign("Corbeille lieu orphelin")
        _, top = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Haut"})
        _, low = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Bas", "parent_id": top["id"]})
        tid = self.delete(f"/api/places/{low['id']}")
        self.delete(f"/api/places/{top['id']}")
        self.restore(tid)
        self.assertIsNone(s.call("GET", f"/api/places/{low['id']}")[1]["parent_id"])

    def test_document_with_subpages_versions_comments(self):
        s = self.s
        cid = self.campaign("Corbeille fiches", timeline=True)
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Maëlis"})
        _, scen = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Le bal masqué"})
        _, act = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Acte I", "parent_id": scen["id"]})
        _, scene = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Scène des miroirs", "parent_id": act["id"]})
        body, ctype = multipart([("scene.png", png_bytes((10, 90, 40)))])
        _, img = s.call("POST", f"/api/documents/{act['id']}/images",
                        raw=body.replace(b'name="files"', b'name="file"'), content_type=ctype)
        _, thread = s.call("POST", f"/api/documents/{act['id']}/comments", {"body": "À revoir", "quote": "Avec"})
        s.call("POST", f"/api/documents/{act['id']}/comments", {"body": "D'accord", "parent_id": thread["id"]})
        html = (f'<p><span data-comment="{thread["id"]}">Avec</span> '
                f'<a class="mention" data-mention="npc:{npc["id"]}">Maëlis</a></p>'
                f'<img src="{img["url"]}" alt="" data-doc-image="{img["id"]}">')
        s.call("PATCH", f"/api/documents/{act['id']}", {"content": html})
        s.call("PATCH", f"/api/documents/{act['id']}", {"content": html + "<p>Suite</p>"})   # une version
        s.call("POST", f"/api/documents/{act['id']}/versions", {"label": "Avant la séance"})
        s.call("PUT", f"/api/documents/{scen['id']}/map/nodes/npc/{npc['id']}", {"state": "added"})
        _, other = s.call("POST", f"/api/campaigns/{cid}/documents", {
            "title": "Renvois", "content": f'<p><a class="mention" data-mention="doc:{scene["id"]}">miroirs</a></p>'})
        s.call("POST", f"/api/campaigns/{cid}/events", {
            "title": "Bal", "description": f'<p><a class="mention" data-mention="doc:{act["id"]}">I</a></p>'})
        epoch = s.call("GET", f"/api/documents/{act['id']}")[1]["live_epoch"]

        item = self.round_trip(cid, f"/api/documents/{scen['id']}", "document", scen["id"])
        self.assertEqual(item["summary"], "2 sous-pages, 1 image, 2 commentaires")
        self.assertEqual(item["name"], "Le bal masqué")
        self.assertEqual(s.call("GET", img["url"])[0], 200)
        self.assertIn(("document", scene["id"]), self.found(cid, "miroirs"))
        _, back = s.call("GET", f"/api/documents/{act['id']}")
        self.assertEqual(back["live_epoch"], epoch + 1)   # navigateurs restés sur l'ancienne : rechargent
        _, linked = s.call("GET", f"/api/documents/{scene['id']}")
        self.assertEqual([d["id"] for d in linked["linked_from"]], [other["id"]])

    def test_subpage_parent_deleted_meanwhile_refused(self):
        s = self.s
        cid = self.campaign("Corbeille sous-page")
        _, scen = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Scénario parent"})
        _, page = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Page", "parent_id": scen["id"]})
        first = self.delete(f"/api/documents/{page['id']}")
        second = self.delete(f"/api/documents/{scen['id']}")
        code, out = s.call("POST", f"/api/trash/{first}/restore")
        self.assertEqual(code, 409)
        self.assertIn("« Scénario parent »", out["detail"])
        self.assertEqual(len(self.items(cid)), 2)   # rien n'a bougé
        self.restore(second)
        self.restore(first)
        _, docs = s.call("GET", f"/api/campaigns/{cid}/documents")
        self.assertEqual(next(d for d in docs["documents"] if d["id"] == page["id"])["parent_id"], scen["id"])

    def test_event(self):
        s = self.s
        cid = self.campaign("Corbeille chronologie", timeline=True)
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Vael"})
        s.call("POST", f"/api/campaigns/{cid}/events", {"title": "Avant"})
        _, ev = s.call("POST", f"/api/campaigns/{cid}/events", {
            "title": "Couronnement", "date_label": "Hiver 1204",
            "description": f'<p><a class="mention" data-mention="npc:{npc["id"]}">Vael</a></p>'})
        s.call("POST", f"/api/campaigns/{cid}/events", {"title": "Après"})
        self.round_trip(cid, f"/api/events/{ev['id']}", "event", ev["id"])
        self.assertIn(("event", ev["id"]), self.found(cid, "Couronnement"))
        _, events = s.call("GET", f"/api/campaigns/{cid}/events")
        self.assertEqual([e["title"] for e in events["events"]], ["Avant", "Couronnement", "Après"])

    def test_map_with_pins_and_files(self):
        s = self.s
        cid = self.campaign("Corbeille cartes")
        _, place = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Valdis"})
        body, ctype = form({"name": "Secteur"}, ("fond.png", png_bytes(size=(1200, 800))))
        _, smap = s.call("POST", f"/api/campaigns/{cid}/maps", raw=body, content_type=ctype)
        s.call("PUT", f"/api/maps/{smap['id']}/pins/{place['id']}", {"x": 0.25, "y": 0.75})
        moved = len(self.files(cid))
        tid = self.delete(f"/api/maps/{smap['id']}")
        self.assertEqual(len(self.trashed_files(cid)), moved)
        item = next(i for i in self.items(cid) if i["id"] == tid)
        self.assertEqual(item["summary"], "1 épingle")
        self.restore(tid)
        self.round_trip(cid, f"/api/maps/{smap['id']}", "map", smap["id"])

    def test_bulk_image_deletion_trashes_maps(self):
        """Administration > Images, suppression groupée : les cartes interactives vont à la corbeille (une entrée par
        carte), les autres images restent supprimées définitivement."""
        s = self.s
        cid = self.campaign("Corbeille suppression groupée")
        _, place = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Valdis"})
        maps = []
        for name in ("Nord", "Sud"):
            body, ctype = form({"name": name}, (f"{name}.png", png_bytes()))
            maps.append(s.call("POST", f"/api/campaigns/{cid}/maps", raw=body, content_type=ctype)[1])
        s.call("PUT", f"/api/maps/{maps[0]['id']}/pins/{place['id']}", {"x": 0.5, "y": 0.5})
        before = self.snapshot()
        code, out = s.call("POST", f"/api/admin/campaigns/{cid}/images/delete",
                           {"items": [{"source": "map", "id": m["id"]} for m in maps]})
        self.assertEqual(code, 200, out)
        self.assertEqual(len(out["trash_ids"]), 2)
        self.assertEqual(sorted((i["kind"], i["name"]) for i in self.items(cid)), [("map", "Nord"), ("map", "Sud")])
        for tid in out["trash_ids"]:
            self.restore(tid)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.trashed_files(cid), set())

    # --- Conflits ---------------------------------------------------------------------------------

    def test_reused_relation_id_gets_new_number(self):
        """Relations sans AUTOINCREMENT : le numéro d'une relation partie avec un PNJ peut être repris ; restaurée
        sous un nouveau numéro, sans écraser l'autre."""
        s = self.s
        cid = self.campaign("Corbeille numéros")
        a, b, c = (s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": n})[1] for n in ("A", "B", "C"))
        _, rel = s.call("POST", f"/api/campaigns/{cid}/relations", {
            "source_kind": "npc", "source_id": a["id"], "target_kind": "npc", "target_id": b["id"]})
        tid = self.delete(f"/api/npcs/{a['id']}")
        _, again = s.call("POST", f"/api/campaigns/{cid}/relations", {
            "source_kind": "npc", "source_id": b["id"], "target_kind": "npc", "target_id": c["id"]})
        self.assertEqual(again["id"], rel["id"])
        self.restore(tid)
        _, graph = s.call("GET", f"/api/campaigns/{cid}/graph")
        pairs = sorted((r["source_id"], r["target_id"]) for r in graph["relations"])
        self.assertEqual(pairs, sorted([(a["id"], b["id"]), (b["id"], c["id"])]))

    def test_related_entry_deleted_meanwhile(self):
        s = self.s
        cid = self.campaign("Corbeille liens disparus")
        _, a = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "A"})
        _, b = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "B"})
        _, f = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "F"})
        s.call("POST", "/api/memberships", {"npc_id": a["id"], "faction_id": f["id"]})
        s.call("POST", f"/api/campaigns/{cid}/relations", {
            "source_kind": "npc", "source_id": a["id"], "target_kind": "npc", "target_id": b["id"]})
        tid = self.delete(f"/api/npcs/{a['id']}")
        self.delete(f"/api/npcs/{b['id']}")
        self.delete(f"/api/factions/{f['id']}")
        self.restore(tid)
        _, graph = s.call("GET", f"/api/campaigns/{cid}/graph")
        self.assertEqual(([n["name"] for n in graph["npcs"]], graph["relations"], graph["memberships"]), (["A"], [], []))

    # --- Purge, vidage, droits ----------------------------------------------------------------------

    def test_purge_after_30_days(self):
        s = self.s
        cid = self.campaign("Corbeille purge")
        _, old = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Ancien"})
        _, recent = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Récent"})
        self.gallery("npcs", old["id"])
        old_tid = self.delete(f"/api/npcs/{old['id']}")
        recent_tid = self.delete(f"/api/npcs/{recent['id']}")
        with db.transaction(self.db_path) as conn:   # date simulée : supprimé il y a 31 jours
            conn.execute("UPDATE trash SET deleted_at = ? WHERE id = ?",
                         (trash.now_text(-timedelta(days=31)), old_tid))
        self.assertEqual([i["id"] for i in self.items(cid)], [recent_tid])
        self.assertEqual(s.call("POST", f"/api/trash/{old_tid}/restore")[0], 404)
        self.assertEqual(len(self.trashed_files(cid)), 2)   # image et miniature, en attente de la purge
        self.assertEqual(trash.purge_expired(self.db_path, self.media), 1)
        self.assertEqual(self.trashed_files(cid), set())
        with db.transaction(self.db_path, write=False) as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT id FROM trash WHERE campaign_id = ?", (cid,))],
                             [recent_tid])

    def test_delete_item_and_empty(self):
        s = self.s
        cid = self.campaign("Corbeille vidage")
        ids = []
        for name in ("Un", "Deux", "Trois"):
            _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": name})
            self.gallery("npcs", npc["id"])
            ids.append(self.delete(f"/api/npcs/{npc['id']}"))
        self.assertEqual(len(self.trashed_files(cid)), 6)
        self.assertEqual(s.call("DELETE", f"/api/trash/{ids[0]}")[0], 204)
        self.assertEqual(len(self.trashed_files(cid)), 4)
        self.assertEqual(s.call("DELETE", f"/api/trash/{ids[0]}")[0], 404)
        self.assertEqual(s.call("DELETE", f"/api/campaigns/{cid}/trash")[0], 204)
        self.assertEqual((self.items(cid), self.trashed_files(cid)), ([], set()))

    def test_campaign_deletion_takes_its_trash(self):
        s = self.s
        cid = self.campaign("Corbeille campagne")
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Dernier"})
        self.gallery("npcs", npc["id"])
        self.delete(f"/api/npcs/{npc['id']}")
        self.assertEqual(s.call("DELETE", f"/api/campaigns/{cid}")[0], 204)
        self.assertFalse((self.media / str(cid)).exists())
        with db.transaction(self.db_path, write=False) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trash WHERE campaign_id = ?", (cid,)).fetchone()[0], 0)

    def test_access(self):
        s = self.s
        cid = self.campaign("Corbeille privée")
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Secret"})
        tid = self.delete(f"/api/npcs/{npc['id']}")
        s.call("POST", "/api/admin/users", {"username": "curieux", "password": "mot-de-passe-x"})
        mj = s.client()
        self.assertEqual(mj.login("curieux", "mot-de-passe-x"), 200)
        self.assertEqual(mj.call("GET", f"/api/campaigns/{cid}/trash")[0], 404)
        self.assertEqual(mj.call("POST", f"/api/trash/{tid}/restore")[0], 404)
        self.assertEqual(mj.call("DELETE", f"/api/trash/{tid}")[0], 404)
        self.assertEqual(mj.call("DELETE", f"/api/campaigns/{cid}/trash")[0], 404)
        self.assertEqual([i["id"] for i in self.items(cid)], [tid])

    def test_deleted_by_claude(self):
        from maze import mcp
        s = self.s
        cid = self.campaign("Corbeille Claude")
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Vael"})
        app = s.server.config.app
        with db.transaction(self.db_path, write=False) as conn:
            admin = dict(conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone())
        ctx = mcp.Context(app=app, user=admin, grants={cid: "delete"})
        asyncio.run(mcp.delete_entry(ctx, {"kind": "npc", "id": npc["id"], "name": "Vael"}))
        item = self.items(cid)[0]
        self.assertEqual((item["kind"], item["object_id"], item["via"], item["deleted_by_name"]),
                         ("npc", npc["id"], "claude", "Administrateur"))
        self.restore(item["id"])

    # --- Images, sauvegarde, cohérence ---------------------------------------------------------------

    def test_image_cited_by_trashed_document_kept_by_cleanup(self):
        """Illustration qui appartient à une autre fiche mais n'est affichée que par la fiche mise à la corbeille :
        ni orpheline ni supprimée par le ménage d'Administration > Images."""
        s = self.s
        cid = self.campaign("Corbeille ménage")
        _, owner = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Propriétaire"})
        body, ctype = multipart([("x.png", png_bytes())])
        _, img = s.call("POST", f"/api/documents/{owner['id']}/images",
                        raw=body.replace(b'name="files"', b'name="file"'), content_type=ctype)
        _, shower = s.call("POST", f"/api/campaigns/{cid}/documents", {
            "title": "Montre", "content": f'<img src="{img["url"]}" alt="" data-doc-image="{img["id"]}">'})
        tid = self.delete(f"/api/documents/{shower['id']}")
        _, listing = s.call("GET", f"/api/admin/campaigns/{cid}/images")
        self.assertEqual(listing["orphans"], 0)
        code, out = s.call("POST", f"/api/admin/campaigns/{cid}/images/clean", {})
        self.assertEqual((code, out["deleted"]), (200, 0))
        self.restore(tid)
        self.assertEqual(s.call("GET", img["url"])[0], 200)

    def test_backup_contains_trash(self):
        s = self.s
        cid = self.campaign("Corbeille sauvegarde")
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Sauvé"})
        name = self.gallery("npcs", npc["id"])["images"][0]["url"].rsplit("/", 1)[1]
        self.delete(f"/api/npcs/{npc['id']}")
        archive = backup.backup(keep=None, cfg=s.server.config.app.state.settings)
        with tarfile.open(archive) as tar:
            self.assertIn(f"media/{cid}/{TRASH_DIR}/{name}", tar.getnames())
        with tarfile.open(archive) as tar:
            backup._check_members(tar)   # restauration : la corbeille est admise dans l'archive

    def test_polymorphic_cleanup_covered(self):
        """Toute table nettoyée par un déclencheur de suppression d'une fiche (sans clé étrangère) est connue de
        trash.POLY_REFS : sinon une restauration pourrait recréer une ligne qui vise un élément disparu."""
        with db.transaction(self.db_path, write=False) as conn:
            schema = trash.schema(conn)
            sqls = [r[0] for r in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND sql LIKE '%AFTER DELETE ON%'")]
        cleaned = {m for sql in sqls for m in re.findall(r"DELETE FROM (\w+) WHERE", sql)}
        cleaned -= {"search_index"}
        for table in cleaned:
            self.assertIn(table, schema, table)
            self.assertIn(table, trash.POLY_REFS, table)


if __name__ == "__main__":
    unittest.main()
