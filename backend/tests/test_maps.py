"""Cartes interactives : fonds en haute définition, épingles, cartes de lieux, export / import (maps.py,
migration 017).

Lancer : cd backend && python -m unittest -q tests.test_maps
"""

from __future__ import annotations

import io
import unittest
import uuid
import zipfile
from pathlib import Path

from PIL import Image

from tests.test_api import Server, png_bytes


def form(fields: dict[str, str], file: tuple[str, bytes] | None) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for key, value in fields.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    if file:
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file[0]}"\r\n'.encode())
        out.write(b"Content-Type: application/octet-stream\r\n\r\n" + file[1] + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def jpeg(size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 40, 60)).save(buf, "JPEG", quality=70)
    return buf.getvalue()


class MapsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def media(self, url: str) -> Path:
        return Path(self.s.tmp.name) / "media" / url.removeprefix("/media/")

    def campaign(self, name: str) -> int:
        _, camp = self.s.call("POST", "/api/campaigns", {"name": name})
        self.addCleanup(self.s.call, "DELETE", f"/api/campaigns/{camp['id']}")
        return camp["id"]

    def test_maps_and_pins(self):
        s = self.s
        cid = self.campaign("Morvane")
        _, sector = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Secteur Morvane"})
        _, valdis = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Valdis", "parent_id": sector["id"]})
        _, sorn = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Sorn", "parent_id": valdis["id"]})
        other = self.campaign("Autre")
        _, stranger = s.call("POST", f"/api/campaigns/{other}/places", {"name": "Ailleurs"})

        # Fond en pleine définition (6000 px gardés, 8192 au plus) et version d'affichage de 4096 px.
        body, ctype = form({"name": "Secteur", "place_id": str(sector["id"])}, ("morvane.jpg", jpeg((6000, 3000))))
        code, big = s.call("POST", f"/api/campaigns/{cid}/maps", raw=body, content_type=ctype)
        self.assertEqual(code, 201, big)
        self.assertEqual((big["width"], big["height"], big["name"], big["pins"]), (6000, 3000, "Secteur", []))
        self.assertTrue(self.media(big["url"]).is_file())
        with Image.open(self.media(big["preview_url"])) as preview:
            self.assertEqual(preview.size, (4096, 2048))
        # Pleine définition en JPEG (encodeur WebP trop gourmand à cette taille), servie par /media.
        self.assertTrue(big["url"].endswith(".jpg"))
        code, raw = s.call("GET", big["url"])
        self.assertEqual((code, raw[:2]), (200, b"\xff\xd8"))
        self.assertEqual(s.call("GET", big["url"].replace(".jpg", ".png"))[0], 404)
        body, ctype = form({"name": "Immense"}, ("immense.jpg", jpeg((12000, 6000))))
        code, huge = s.call("POST", f"/api/campaigns/{cid}/maps", raw=body, content_type=ctype)
        self.assertEqual((code, huge["width"], huge["height"]), (201, 8192, 4096))
        self.assertEqual(s.call("DELETE", f"/api/maps/{huge['id']}")[0], 204)
        self.assertFalse(self.media(huge["preview_url"]).exists())
        # Petit fond : pas de version d'affichage.
        body, ctype = form({"name": "Petite"}, ("p.png", png_bytes()))
        code, little = s.call("POST", f"/api/campaigns/{cid}/maps", raw=body, content_type=ctype)
        self.assertIsNone(little["preview_url"])
        self.assertEqual(s.call("DELETE", f"/api/maps/{little['id']}")[0], 204)
        # Nom par défaut : celui du lieu représenté ; lieu d'une autre campagne refusé ; fichier absent refusé.
        body, ctype = form({"place_id": str(valdis["id"])}, ("s.png", png_bytes()))
        code, small = s.call("POST", f"/api/campaigns/{cid}/maps", raw=body, content_type=ctype)
        self.assertEqual((code, small["name"]), (201, "Carte de Valdis"))
        body, ctype = form({"place_id": str(stranger["id"])}, ("s.png", png_bytes()))
        self.assertEqual(s.call("POST", f"/api/campaigns/{cid}/maps", raw=body, content_type=ctype)[0], 400)
        body, ctype = form({"name": "Vide"}, None)
        self.assertEqual(s.call("POST", f"/api/campaigns/{cid}/maps", raw=body, content_type=ctype)[0], 400)

        # Épingles : pose, déplacement, bornes, lieu étranger.
        mid = big["id"]
        self.assertEqual(s.call("PUT", f"/api/maps/{mid}/pins/{valdis['id']}", {"x": 0.25, "y": 0.5})[0], 200)
        code, pin = s.call("PUT", f"/api/maps/{mid}/pins/{valdis['id']}", {"x": 0.3, "y": 0.6})
        self.assertEqual((code, pin["x"]), (200, 0.3))
        self.assertEqual(s.call("PUT", f"/api/maps/{mid}/pins/{sorn['id']}", {"x": 1.2, "y": 0.5})[0], 422)
        self.assertEqual(s.call("PUT", f"/api/maps/{mid}/pins/{stranger['id']}", {"x": 0.1, "y": 0.1})[0], 400)
        _, detail = s.call("GET", f"/api/maps/{mid}")
        self.assertEqual(detail["pins"], [{"place_id": valdis["id"], "x": 0.3, "y": 0.6}])
        _, listing = s.call("GET", f"/api/campaigns/{cid}/maps")
        self.assertEqual([(m["name"], m["pins"]) for m in listing["maps"]],
                         [("Secteur", [valdis["id"]]), ("Carte de Valdis", [])])

        # Fiche d'un lieu : cartes où le trouver (lui-même ou un ancêtre épinglé), cartes qui le représentent.
        _, place = s.call("GET", f"/api/places/{sorn['id']}")
        self.assertEqual([(m["id"], m["via_place_id"], m["depth"]) for m in place["maps"]], [(mid, valdis["id"], 1)])
        _, place = s.call("GET", f"/api/places/{valdis['id']}")
        self.assertEqual([m["id"] for m in place["own_maps"]], [small["id"]])

        # Renommer, changer de lieu, nouveau fond (les épingles restent), ancien fichier supprimé.
        code, out = s.call("PATCH", f"/api/maps/{mid}", {"name": "Secteur Morvane", "place_id": None})
        self.assertEqual((code, out["name"], out["place_id"]), (200, "Secteur Morvane", None))
        self.assertEqual(s.call("PATCH", f"/api/maps/{mid}", {"place_id": stranger["id"]})[0], 400)
        old_file = self.media(big["url"])
        old_preview = self.media(big["preview_url"])
        body, ctype = form({}, ("v2.png", png_bytes(size=(800, 400))))
        code, out = s.call("POST", f"/api/maps/{mid}/image", raw=body, content_type=ctype)
        self.assertEqual((code, out["width"], len(out["pins"])), (200, 800, 1))
        self.assertFalse(old_file.exists())
        self.assertFalse(old_preview.exists())
        self.assertIsNone(out["preview_url"])

        # Depuis une image déjà dans Maze (galerie d'un lieu), et catalogue des images avec les cartes.
        from tests.test_api import multipart
        body, ctype = multipart([("lieu.png", png_bytes((10, 200, 10)))])
        _, with_img = s.call("POST", f"/api/places/{sorn['id']}/images", raw=body, content_type=ctype)
        code, copied = s.call("POST", f"/api/campaigns/{cid}/maps/copy",
                              {"source": "gallery", "id": with_img["images"][0]["id"], "place_id": sorn["id"]})
        self.assertEqual((code, copied["name"]), (201, "Carte de Sorn"))
        self.assertEqual(s.call("POST", f"/api/campaigns/{other}/maps/copy", {"source": "map", "id": mid})[0], 404)
        _, catalog = s.call("GET", f"/api/campaigns/{cid}/images")
        self.assertIn(("map", mid), {(i["source"], i["id"]) for i in catalog["images"]})
        _, admin = s.call("GET", f"/api/admin/campaigns/{cid}/images")
        self.assertEqual(sum(1 for i in admin["images"] if i["source"] == "map"), 3)

        # Ordre ; suppression d'un lieu (épingles et lien de la carte) ; retrait d'une épingle.
        ids = [copied["id"], small["id"], mid]
        self.assertEqual(s.call("PUT", f"/api/campaigns/{cid}/maps/order", {"ids": ids})[0], 204)
        _, listing = s.call("GET", f"/api/campaigns/{cid}/maps")
        self.assertEqual([m["id"] for m in listing["maps"]], ids)
        s.call("PUT", f"/api/maps/{small['id']}/pins/{sorn['id']}", {"x": 0.5, "y": 0.5})
        self.assertEqual(s.call("DELETE", f"/api/places/{sorn['id']}")[0], 204)
        _, out = s.call("GET", f"/api/maps/{copied['id']}")
        self.assertIsNone(out["place_id"])
        self.assertEqual(s.call("GET", f"/api/maps/{small['id']}")[1]["pins"], [])
        self.assertEqual(s.call("DELETE", f"/api/maps/{mid}/pins/{valdis['id']}")[0], 204)
        self.assertEqual(s.call("DELETE", f"/api/maps/{mid}/pins/{valdis['id']}")[0], 404)

        # Suppression : fichiers retirés.
        files = self.media(copied["url"])
        self.assertEqual(s.call("DELETE", f"/api/maps/{copied['id']}")[0], 204)
        self.assertFalse(files.exists())
        self.assertEqual(s.call("GET", f"/api/maps/{copied['id']}")[0], 404)

    def test_kind_icons_and_pin_style(self):
        s = self.s
        cid = self.campaign("Icônes")
        _, tree = s.call("GET", f"/api/campaigns/{cid}/places")
        self.assertTrue(all(k["image_url"] is None for k in tree["kinds"]))
        _, kind = s.call("POST", f"/api/campaigns/{cid}/place-kinds", {"label": "Cité-forteresse", "icon": "planet"})
        _, place = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Valdis", "kind": kind["key"]})
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Halloran"})
        s.call("POST", f"/api/places/{place['id']}/links", {"entity_kind": "npc", "entity_id": npc["id"], "role": ""})

        # Symbole sur fond blanc (comme une légende de carte) : fond rendu transparent, recadré, 128 px.
        from PIL import ImageDraw
        img = Image.new("RGB", (1024, 1024), (255, 255, 255))
        ImageDraw.Draw(img).ellipse([300, 300, 724, 724], fill=(140, 0, 0), outline=(20, 0, 0), width=30)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        body, ctype = form({}, ("forteresse.png", buf.getvalue()))
        code, out = s.call("POST", f"/api/place-kinds/{kind['id']}/image", raw=body, content_type=ctype)
        self.assertEqual(code, 200, out)
        icon_path = self.media(out["image_url"])
        with Image.open(icon_path) as icon:
            self.assertEqual((icon.size, icon.mode), ((128, 128), "RGBA"))
            self.assertEqual(icon.getpixel((0, 0))[3], 0)        # coin transparent
            self.assertEqual(icon.getpixel((64, 64))[3], 255)    # symbole opaque
            self.assertEqual(icon.getpixel((1, 64))[3], 255)     # recadré au ras du cercle
        _, tree = s.call("GET", f"/api/campaigns/{cid}/places")
        self.assertEqual(next(k for k in tree["kinds"] if k["id"] == kind["id"])["image_url"], out["image_url"])
        _, entity = s.call("GET", f"/api/npcs/{npc['id']}")
        self.assertEqual(entity["places"][0]["kind_image"], out["image_url"])
        code, found = s.call("GET", "/api/search?q=Valdis")
        self.assertIn(out["image_url"], [r.get("place_kind_image") for r in found["results"]])
        body, ctype = form({}, ("x.txt", b"pas une image"))
        self.assertEqual(s.call("POST", f"/api/place-kinds/{kind['id']}/image", raw=body, content_type=ctype)[0], 400)

        # Remplacement : l'ancienne icône est supprimée ; export / import ; retrait ; suppression du type.
        body, ctype = form({}, ("ruche2.png", buf.getvalue()))
        _, out2 = s.call("POST", f"/api/place-kinds/{kind['id']}/image", raw=body, content_type=ctype)
        self.assertFalse(icon_path.exists())
        _, archive = s.call("GET", f"/api/campaigns/{cid}/export")
        code, imported = s.call("POST", "/api/campaigns/import", raw=archive, content_type="application/zip")
        self.assertEqual(code, 201)
        _, tree2 = s.call("GET", f"/api/campaigns/{imported['id']}/places")
        copy = next(k for k in tree2["kinds"] if k["key"] == kind["key"])
        self.assertTrue(copy["image_url"] and self.media(copy["image_url"]).is_file())
        copy_path = self.media(copy["image_url"])
        self.assertEqual(s.call("DELETE", f"/api/campaigns/{imported['id']}")[0], 204)
        self.assertFalse(copy_path.exists())
        code, back = s.call("DELETE", f"/api/place-kinds/{kind['id']}/image")
        self.assertEqual((code, back["image_url"], back["icon"]), (200, None, "planet"))
        self.assertFalse(self.media(out2["image_url"]).exists())
        body, ctype = form({}, ("ruche3.png", buf.getvalue()))
        _, out3 = s.call("POST", f"/api/place-kinds/{kind['id']}/image", raw=body, content_type=ctype)
        self.assertEqual(s.call("DELETE", f"/api/place-kinds/{kind['id']}")[0], 204)
        self.assertFalse(self.media(out3["image_url"]).exists())

        # Style des épingles d'une carte.
        body, ctype = form({"name": "Secteur"}, ("c.png", png_bytes()))
        _, m = s.call("POST", f"/api/campaigns/{cid}/maps", raw=body, content_type=ctype)
        self.assertEqual(m["pin_style"], "dot")
        self.assertEqual(s.call("PATCH", f"/api/maps/{m['id']}", {"pin_style": "hidden"})[1]["pin_style"], "hidden")
        self.assertEqual(s.call("PATCH", f"/api/maps/{m['id']}", {"pin_style": None})[1]["pin_style"], "hidden")
        self.assertEqual(s.call("PATCH", f"/api/maps/{m['id']}", {"pin_style": "big"})[0], 422)

    def test_memory_bounds(self):
        """Pixels décodés d'un fond de carte (réduction JPEG au décodage) : bornes de mémoire."""
        from maze import imaging
        self.assertEqual(imaging._decoded_pixels((8000, 5333), 8192), 8000 * 5333)        # gardée telle quelle
        self.assertEqual(imaging._decoded_pixels((20000, 12500), 8192), 10000 * 6250)     # décodée à 1/2
        # Travail (décodé + résultat) : bornes de la recette du 9 octobre.
        work = lambda size: imaging._map_work(size, 8192, "JPEG")  # noqa: E731
        self.assertLessEqual(work((9000, 6000)), imaging.MAX_MAP_WORK)        # 99 Mpx : admise
        self.assertLessEqual(work((20000, 12500)), imaging.MAX_MAP_WORK)      # décodée à 1/2 : admise
        self.assertLessEqual(work((8192, 8192)), imaging.MAX_MAP_WORK)        # pas de réduction : admise
        self.assertGreater(work((8940, 8940)), imaging.MAX_MAP_WORK)          # 147 Mpx : refusée
        self.assertGreater(work((11400, 7000)), imaging.MAX_MAP_WORK)
        self.assertGreater(work((12500, 8000)), imaging.MAX_MAP_WORK)
        self.assertEqual(imaging._decoded_pixels((9000, 6000), 1600), 2250 * 1500)        # galeries : 1/4
        # Réduction par bandes : même résultat qu'une réduction d'un coup (à l'arrondi près), sans raccord.
        from PIL import ImageChops
        src = Image.radial_gradient("L").resize((900, 2000)).convert("RGB")
        banded = imaging._shrink(src, (450, 1000))
        direct = src.resize((450, 1000), Image.Resampling.LANCZOS)
        self.assertLessEqual(max(ImageChops.difference(banded, direct).getextrema()[c][1] for c in range(3)), 2)

    def test_export_import(self):
        s = self.s
        cid = self.campaign("Export cartes")
        _, place = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Port-Errant"})
        body, ctype = form({"name": "Côte", "place_id": str(place["id"])}, ("c.jpg", jpeg((5000, 2500))))
        _, m = s.call("POST", f"/api/campaigns/{cid}/maps", raw=body, content_type=ctype)
        s.call("PUT", f"/api/maps/{m['id']}/pins/{place['id']}", {"x": 0.4, "y": 0.7})
        # Copie d'un fond vers une galerie : la version d'affichage ; vers une autre carte : les deux fichiers.
        code, gallery = s.call("POST", f"/api/places/{place['id']}/images/copy", {"source": "map", "id": m["id"]})
        self.assertEqual((code, gallery["images"][0]["width"]), (201, 4096))
        code, twin = s.call("POST", f"/api/campaigns/{cid}/maps/copy", {"source": "map", "id": m["id"]})
        self.assertEqual((code, twin["width"], twin["name"]), (201, 5000, "Côte"))
        self.assertTrue(self.media(twin["preview_url"]).is_file())
        s.call("DELETE", f"/api/maps/{twin['id']}")
        code, archive = s.call("GET", f"/api/campaigns/{cid}/export")
        self.assertEqual(code, 200)
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            self.assertIn(f"media/{Path(m['url']).name}", z.namelist())
        code, imported = s.call("POST", "/api/campaigns/import", raw=archive, content_type="application/zip")
        self.assertEqual(code, 201, imported)
        self.addCleanup(s.call, "DELETE", f"/api/campaigns/{imported['id']}")
        _, listing = s.call("GET", f"/api/campaigns/{imported['id']}/maps")
        (copy,) = listing["maps"]
        _, places = s.call("GET", f"/api/campaigns/{imported['id']}/places")
        new_place = places["places"][0]["id"]
        self.assertEqual((copy["name"], copy["place_id"], copy["pins"]), ("Côte", new_place, [new_place]))
        self.assertTrue(self.media(copy["url"]).is_file())
        self.assertTrue(self.media(copy["preview_url"]).is_file())
        self.assertEqual(copy["width"], 5000)
        _, detail = s.call("GET", f"/api/maps/{copy['id']}")
        self.assertEqual(detail["pins"][0]["y"], 0.7)

        # Suppression de la campagne : fichiers de la carte retirés.
        path, preview = self.media(copy["url"]), self.media(copy["preview_url"])
        self.assertEqual(s.call("DELETE", f"/api/campaigns/{imported['id']}")[0], 204)
        self.assertFalse(path.exists())
        self.assertFalse(preview.exists())


if __name__ == "__main__":
    unittest.main()
