"""Administration > Images : liste, bibliothèque, ménage, export / import (media_admin.py, migration 016).

Lancer : cd backend && python -m unittest -q tests.test_media
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.test_api import ADMIN_PASSWORD, Server, multipart, png_bytes, recv, recv_until


class MediaAdminTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def media(self, url: str) -> Path:
        return Path(self.s.tmp.name) / "media" / url.removeprefix("/media/")

    def doc_image(self, doc_id: int) -> dict:
        body, ctype = multipart([("p.png", png_bytes())])
        body = body.replace(b'name="files"', b'name="file"')
        code, img = self.s.call("POST", f"/api/documents/{doc_id}/images", raw=body, content_type=ctype)
        self.assertEqual(code, 201)
        return img

    def test_clean_keeps_images_of_unsaved_journal(self):
        """Audit DATA-13 : « Faire le ménage » ne se fie pas au seul HTML enregistré, qui peut être en retard sur le
        journal temps réel. Une illustration insérée par le journal (ou collée dans une autre fiche dont le HTML
        n'est pas encore enregistré) reste ; une illustration citée nulle part est supprimée."""
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Ménage en retard"})
        cid = camp["id"]
        _, first = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "A"})
        _, second = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "B"})
        _, third = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "C"})
        inserted = self.doc_image(first["id"])    # insérée dans A par le journal, HTML pas encore envoyé
        pasted = self.doc_image(third["id"])      # téléversée depuis C, collée dans B par le journal
        unused = self.doc_image(third["id"])      # citée nulle part
        for doc, img in ((first, inserted), (second, pasted)):
            ws = s.admin.live(doc["id"], 900 + doc["id"])
            recv_until(ws, "welcome")
            # Le journal Yjs garde l'adresse de l'image (attribut src), pas data-doc-image.
            ws.send(bytes([0]) + f'image src {img["url"]} docImage'.encode())
            self.assertEqual(recv(ws)[0], 3)
            ws.close()
        code, out = s.call("POST", f"/api/admin/campaigns/{cid}/images/clean", {})
        self.assertEqual((code, out), (200, {"deleted": 1}))
        self.assertTrue(self.media(inserted["url"]).exists())
        self.assertTrue(self.media(pasted["url"]).exists())
        self.assertFalse(self.media(unused["url"]).exists())

    def test_library_and_listing(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Bibliothèque"})
        cid = camp["id"]
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Ysolde"})

        # Dépôt dans la bibliothèque (plusieurs fichiers), refus d'un fichier non image.
        body, ctype = multipart([("a.png", png_bytes((200, 0, 0))), ("b.png", png_bytes((0, 200, 0), (300, 300)))])
        code, out = s.call("POST", f"/api/admin/campaigns/{cid}/library", raw=body, content_type=ctype)
        self.assertEqual((code, out["added"]), (201, 2))
        from PIL import Image
        import io
        buf = io.BytesIO()
        Image.new("RGB", (5100, 5000), (1, 2, 3)).save(buf, "PNG")  # 25,5 Mpx, hors JPEG
        body, ctype = multipart([("grand.png", buf.getvalue())])
        code, err = s.call("POST", f"/api/admin/campaigns/{cid}/library", raw=body, content_type=ctype)
        self.assertEqual(code, 400)
        self.assertIn("25 millions", err["detail"])
        # Grande carte en JPEG (décodée à échelle réduite) : acceptée, ramenée à 1600 px.
        buf = io.BytesIO()
        Image.new("RGB", (9000, 6000), (40, 60, 80)).save(buf, "JPEG", quality=70)  # 54 Mpx
        body, ctype = multipart([("carte.jpg", buf.getvalue())])
        code, out = s.call("POST", f"/api/admin/campaigns/{cid}/library", raw=body, content_type=ctype)
        self.assertEqual(code, 201, out)
        buf = io.BytesIO()
        Image.new("RGB", (10000, 9000), (40, 60, 80)).save(buf, "JPEG", quality=70, progressive=True)  # 90 Mpx
        body, ctype = multipart([("carte-p.jpg", buf.getvalue())])
        code, err = s.call("POST", f"/api/admin/campaigns/{cid}/library", raw=body, content_type=ctype)
        self.assertEqual(code, 400)
        self.assertIn("progressif", err["detail"])
        from maze import fetch
        self.assertEqual(fetch.user_agent("https://maze.example.org"), "Maze/1.0 (+https://maze.example.org; connecteur Claude)")
        body, ctype = multipart([("c.txt", b"pas une image")])
        self.assertEqual(s.call("POST", f"/api/admin/campaigns/{cid}/library", raw=body, content_type=ctype)[0], 400)

        _, listing = s.call("GET", f"/api/admin/campaigns/{cid}/images")
        library = [i for i in listing["images"] if i["source"] == "library"]
        self.assertEqual(sorted(i["name"] for i in library), ["a.png", "b.png", "carte.jpg"])
        carte = next(i for i in library if i["name"] == "carte.jpg")
        self.assertEqual((carte["width"], carte["height"]), (1600, 1067))
        s.call("DELETE", f"/api/admin/library/{carte['id']}")
        self.assertTrue(all(i["bytes"] > 0 for i in library))
        first = next(i for i in library if i["name"] == "a.png")
        self.assertEqual(s.call("PATCH", f"/api/admin/library/{first['id']}", {"caption": "Portrait rouge"})[0], 204)

        # Utilisation sur une fiche : copie dans la galerie, couverture, retrait de la bibliothèque.
        self.assertEqual(s.call("POST", f"/api/admin/library/{first['id']}/assign",
                                raw=b'{"kind":"npc","entity_id":1}', content_type="text/plain")[0], 415)
        code, out = s.call("POST", f"/api/admin/library/{first['id']}/assign",
                           {"kind": "npc", "entity_id": npc["id"], "set_cover": True, "remove": True})
        self.assertEqual(code, 201, out)
        _, detail = s.call("GET", f"/api/npcs/{npc['id']}")
        self.assertEqual(detail["portrait_image_id"], out["image_id"])
        self.assertEqual(detail["images"][0]["caption"], "Portrait rouge")
        self.assertFalse(self.media(first["url"]).exists())
        _, other = s.call("POST", "/api/campaigns", {"name": "Autre"})
        _, alien = s.call("POST", f"/api/campaigns/{other['id']}/npcs", {"name": "Étranger"})
        second = next(i for i in library if i["name"] == "b.png")
        self.assertEqual(s.call("POST", f"/api/admin/library/{second['id']}/assign",
                                {"kind": "npc", "entity_id": alien["id"]})[0], 400)

        # Illustrations de fiches rédigées : utilisée, inutilisée, citée dans l'historique seulement.
        _, doc = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Scène"})
        ids = []
        for color in ((1, 2, 3), (4, 5, 6), (7, 8, 9)):
            body, ctype = multipart([("x.png", png_bytes(color))])
            _, up = s.call("POST", f"/api/documents/{doc['id']}/images",
                           raw=body.replace(b'name="files"', b'name="file"'), content_type=ctype)
            ids.append(up)
        used, historic, orphan = ids
        s.call("PATCH", f"/api/documents/{doc['id']}",
               {"content": f'<p>a</p><img src="{historic["url"]}" data-doc-image="{historic["id"]}">'})
        s.call("PATCH", f"/api/documents/{doc['id']}",  # version « Avant remplacement » : cite historic
               {"content": f'<p>b</p><img src="{used["url"]}" data-doc-image="{used["id"]}">'})
        _, listing = s.call("GET", f"/api/admin/campaigns/{cid}/images")
        docs = {i["id"]: i for i in listing["images"] if i["source"] == "document"}
        self.assertTrue(docs[used["id"]]["used"])
        self.assertTrue(docs[historic["id"]]["in_history"] and not docs[historic["id"]]["used"])
        self.assertEqual(listing["orphans"], 1)
        gallery = [i for i in listing["images"] if i["source"] == "gallery"]
        self.assertEqual((gallery[0]["owner"]["name"], gallery[0]["cover"]), ("Ysolde", True))

        self.assertEqual(s.call("POST", f"/api/admin/campaigns/{cid}/images/clean")[0], 415)
        code, out = s.call("POST", f"/api/admin/campaigns/{cid}/images/clean", {})
        self.assertEqual((code, out["deleted"]), (200, 1))
        self.assertFalse(self.media(orphan["url"]).exists())
        self.assertTrue(self.media(historic["url"]).exists())
        self.assertEqual(s.call("DELETE", f"/api/admin/document-images/{historic['id']}")[0], 200)

        # Illustration affichée : retirée du texte (version gardée), puis supprimée.
        _, before = s.call("GET", f"/api/documents/{doc['id']}")
        code, out = s.call("DELETE", f"/api/admin/document-images/{used['id']}")
        self.assertEqual((code, out["documents"]), (200, [doc["id"]]))
        _, after = s.call("GET", f"/api/documents/{doc['id']}")
        self.assertNotIn("data-doc-image", after["content"])
        self.assertIn("<p>b</p>", after["content"])
        self.assertGreater(after["revision"], before["revision"])
        _, hist = s.call("GET", f"/api/documents/{doc['id']}/versions")
        self.assertIn("Avant suppression d'une image", [v["label"] for v in hist["versions"]])
        self.assertFalse(self.media(used["url"]).exists())
        # Restaurer la version d'avant : l'illustration supprimée n'est pas remise (elle serait cassée).
        before_id = next(v["id"] for v in hist["versions"] if v["label"] == "Avant suppression d'une image")
        code, restored = s.call("POST", f"/api/documents/{doc['id']}/versions/{before_id}/restore")
        self.assertEqual(code, 200, restored)
        self.assertNotIn(f'data-doc-image="{used["id"]}"', restored["content"])
        self.assertIn("<p>b</p>", restored["content"])

        # Export / import : la bibliothèque suit la campagne.
        _, archive = s.call("GET", f"/api/campaigns/{cid}/export")
        code, new = s.call("POST", "/api/campaigns/import", raw=archive, content_type="application/zip")
        self.assertEqual(code, 201, new)
        new_id = new["id"] if "id" in new else new["campaign"]["id"]
        _, listing = s.call("GET", f"/api/admin/campaigns/{new_id}/images")
        self.assertEqual([i["name"] for i in listing["images"] if i["source"] == "library"], ["b.png"])

        # Réservé aux administrateurs ; suppression de la campagne : fichiers de la bibliothèque supprimés.
        s.call("POST", "/api/admin/users", {"username": "mj9", "password": "motdepasse-mj9", "role": "mj",
                                            "campaign_ids": [cid]})
        mj = s.client()
        mj.login("mj9", "motdepasse-mj9")
        self.assertEqual(mj.call("GET", f"/api/admin/campaigns/{cid}/images")[0], 403)
        self.assertEqual(mj.call("DELETE", f"/api/admin/library/{second['id']}")[0], 403)
        self.assertEqual(s.call("DELETE", f"/api/campaigns/{cid}")[0], 204)
        self.assertFalse(self.media(second["url"]).exists())
        self.assertEqual(s.call("GET", f"/api/admin/campaigns/{cid}/images")[0], 404)
        self.assertTrue(ADMIN_PASSWORD)

    def test_reuse_existing_image(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Réutilisation"})
        cid = camp["id"]
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Source"})
        _, target = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "Cible"})
        _, doc = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Scène"})
        body, ctype = multipart([("p.png", png_bytes((10, 100, 200)))])
        _, detail = s.call("POST", f"/api/npcs/{npc['id']}/images", raw=body, content_type=ctype)
        src = detail["images"][0]
        s.call("PATCH", f"/api/images/{src['id']}", {"caption": "Profil"})
        body, ctype = multipart([("lib.png", png_bytes((50, 50, 50)))])
        s.call("POST", f"/api/admin/campaigns/{cid}/library", raw=body, content_type=ctype)

        # Catalogue ouvert aux MJ de la campagne seulement.
        s.call("POST", "/api/admin/users", {"username": "mjr", "password": "motdepasse-mjr", "role": "mj",
                                            "campaign_ids": [cid]})
        s.call("POST", "/api/admin/users", {"username": "mjx", "password": "motdepasse-mjx", "role": "mj"})
        mj, outsider = s.client(), s.client()
        mj.login("mjr", "motdepasse-mjr")
        outsider.login("mjx", "motdepasse-mjx")
        code, cat = mj.call("GET", f"/api/campaigns/{cid}/images")
        self.assertEqual(code, 200)
        self.assertEqual(sorted(i["source"] for i in cat["images"]), ["gallery", "library"])
        self.assertEqual(outsider.call("GET", f"/api/campaigns/{cid}/images")[0], 404)

        # Copie identique vers une autre fiche (légende reprise, emblème), puis vers une fiche rédigée.
        code, out = mj.call("POST", f"/api/factions/{target['id']}/images/copy",
                            {"source": "gallery", "id": src["id"], "set_cover": True})
        self.assertEqual(code, 201, out)
        copy = out["images"][0]
        self.assertEqual((out["emblem_image_id"], copy["caption"]), (copy["id"], "Profil"))
        self.assertNotEqual(copy["url"], src["url"])
        self.assertEqual(self.media(copy["url"]).read_bytes(), self.media(src["url"]).read_bytes())
        lib = next(i for i in cat["images"] if i["source"] == "library")
        code, pic = mj.call("POST", f"/api/documents/{doc['id']}/images/copy", {"source": "library", "id": lib["id"]})
        self.assertEqual(code, 201, pic)
        self.assertTrue(self.media(pic["url"]).exists())
        # L'original supprimé, la copie reste.
        s.call("DELETE", f"/api/images/{src['id']}")
        self.assertTrue(self.media(copy["url"]).exists())

        # Image d'une autre campagne, ou fiche inaccessible : refus.
        _, other = s.call("POST", "/api/campaigns", {"name": "Ailleurs"})
        _, alien = s.call("POST", f"/api/campaigns/{other['id']}/npcs", {"name": "Alien"})
        self.assertEqual(s.call("POST", f"/api/npcs/{alien['id']}/images/copy",
                                {"source": "gallery", "id": copy["id"]})[0], 404)
        self.assertEqual(outsider.call("POST", f"/api/factions/{target['id']}/images/copy",
                                       {"source": "library", "id": lib["id"]})[0], 404)
        self.assertEqual(mj.call("POST", f"/api/factions/{target['id']}/images/copy",
                                 {"source": "autre", "id": 1})[0], 422)


if __name__ == "__main__":
    unittest.main()
