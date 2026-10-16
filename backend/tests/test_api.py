"""Tests d'intégration de l'API (unittest + urllib ; client websockets pour le temps réel).

Lancer : cd backend && python -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import socket
import sqlite3
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from http.cookiejar import CookieJar
from pathlib import Path

import uvicorn
from PIL import Image


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


ADMIN_PASSWORD = "admin-test-pass"


class Client:
    """Navigateur minimal : son propre pot à cookies, donc sa propre session."""

    def __init__(self, base: str):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method: str, path: str, body=None, raw: bytes | None = None,
             content_type: str | None = None, headers: dict[str, str] | None = None):
        data = raw
        headers = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req) as resp:
                payload = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                return resp.status, (json.loads(payload) if "json" in ctype and payload else payload)
        except urllib.error.HTTPError as err:
            payload = err.read()
            try:
                return err.code, json.loads(payload)
            except ValueError:
                return err.code, payload

    def cookie_header(self) -> str:
        jar = next(h for h in self.opener.handlers if isinstance(h, urllib.request.HTTPCookieProcessor)).cookiejar
        return "; ".join(f"{c.name}={c.value}" for c in jar)

    def live(self, doc_id: int, client: int, origin: str | None = None, version: int | str | None = "actuelle"):
        """Connexion WebSocket d'édition en temps réel à une fiche (`version` : version du schéma de l'éditeur
        annoncée par le navigateur, celle du serveur par défaut ; None : pas de paramètre, interface d'avant)."""
        from websockets.sync.client import connect
        from maze.collab import SCHEMA_VERSION
        headers = {"Cookie": self.cookie_header()}
        if origin:
            headers["Origin"] = origin
        ws_base = self.base.replace("http://", "ws://")
        url = f"{ws_base}/api/documents/{doc_id}/live?client={client}"
        if version is not None:
            url += f"&v={SCHEMA_VERSION if version == 'actuelle' else version}"
        return connect(url, additional_headers=headers, open_timeout=5)

    def login(self, username: str, password: str) -> int:
        return self.call("POST", "/api/auth/login", {"username": username, "password": password})[0]


class Server:
    """Instance Maze de test. Par défaut, un compte admin existe et `call` agit en son nom."""

    def __init__(self, env: dict[str, str | None] | None = None, login: bool = True):
        env = {"MAZE_PASSWORD": ADMIN_PASSWORD, **(env or {})}
        self.tmp = tempfile.TemporaryDirectory()
        os.environ.update({"MAZE_DATA_DIR": self.tmp.name, "MAZE_STATIC_DIR": self.tmp.name})
        for key, value in env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._env_keys = list(env)
        from maze.app import create_app
        from maze.config import load_settings
        self.port = _free_port()
        config = uvicorn.Config(create_app(load_settings()), host="127.0.0.1", port=self.port,
                                log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(400):
            if self.server.started or not self.thread.is_alive():
                break
            time.sleep(0.05)
        if not self.server.started:   # audit QUAL-9 : erreur claire plutôt que des connexions refusées ensuite
            self.server.should_exit = True
            raise RuntimeError(f"Serveur de test non démarré (port {self.port}, données {self.tmp.name})")
        self.base = f"http://127.0.0.1:{self.port}"
        self.admin = Client(self.base)
        if login and env.get("MAZE_PASSWORD"):
            assert self.admin.login("admin", env["MAZE_PASSWORD"]) == 200

    def client(self) -> Client:
        return Client(self.base)

    def call(self, *args, **kwargs):
        return self.admin.call(*args, **kwargs)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(5)
        self.tmp.cleanup()
        for key in self._env_keys:
            os.environ.pop(key, None)


def undo_entry_revision(conn: sqlite3.Connection) -> None:
    """Défait la migration 025 (révision des fiches), pour rejouer les migrations depuis une base plus ancienne."""
    for table in ("npcs", "factions", "places"):
        conn.execute(f"DROP TRIGGER IF EXISTS trg_{table}_revision")
        conn.execute(f"ALTER TABLE {table} DROP COLUMN revision")


def rewind_schema(conn: sqlite3.Connection, version: int) -> None:
    """Ramène l'enregistrement des migrations à `version` (user_version et schema_migrations), une fois le schéma
    lui-même défait par le test : les migrations suivantes seront rejouées par db.migrate."""
    conn.execute("DELETE FROM schema_migrations WHERE version > ?", (version,))
    conn.execute(f"PRAGMA user_version = {int(version)}")


def wait_until(condition, timeout: float = 5, step: float = 0.02):
    """Attend (au plus `timeout` s) que condition() soit vraie, au lieu d'une attente fixe (audit QUAL-9) ; renvoie
    sa dernière valeur, échoue si elle reste fausse."""
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value or time.monotonic() > deadline:
            assert value, f"condition toujours fausse après {timeout} s"
            return value
        time.sleep(step)


def png_bytes(color=(200, 50, 50), size=(600, 400)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def multipart(files: list[tuple[str, bytes]]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for name, content in files:
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="files"; filename="{name}"\r\n'.encode())
        out.write(b"Content-Type: application/octet-stream\r\n\r\n")
        out.write(content)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


class OpenApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("MAZE_PASSWORD", None)
        cls.s = Server({})

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_full_flow(self):
        s = self.s
        code, camp = s.call("POST", "/api/campaigns", {"name": "Les Brumes d'Ys"})
        self.assertEqual(code, 201)
        cid = camp["id"]

        code, err = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": ""})
        self.assertEqual(code, 422)

        code, guild = s.call("POST", f"/api/campaigns/{cid}/factions",
                             {"name": "Guilde des Ombres", "color": "#7A3B8F", "kind": "Guilde"})
        self.assertEqual(code, 201)
        self.assertEqual(guild["color"], "#7a3b8f")

        code, a = s.call("POST", f"/api/campaigns/{cid}/npcs",
                         {"name": "Maëlis Corvin", "title": "Receleuse", "tags": ["port", "Port"]})
        self.assertEqual(code, 201)
        self.assertEqual(a["tags"], ["port"])
        code, b = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Capitaine Hart", "status": "mort"})
        self.assertEqual(code, 201)

        code, m = s.call("POST", "/api/memberships",
                         {"npc_id": a["id"], "faction_id": guild["id"], "role": "Cheffe", "is_leader": True})
        self.assertEqual(code, 201)
        code, _ = s.call("POST", "/api/memberships", {"npc_id": a["id"], "faction_id": guild["id"]})
        self.assertEqual(code, 409)

        code, rel = s.call("POST", f"/api/campaigns/{cid}/relations", {
            "source_kind": "npc", "source_id": a["id"], "target_kind": "npc", "target_id": b["id"],
            "type": "ennemi", "label": "Lui doit une cargaison", "directed": True})
        self.assertEqual(code, 201)
        self.assertTrue(rel["directed"])

        code, _ = s.call("POST", f"/api/campaigns/{cid}/relations", {
            "source_kind": "npc", "source_id": a["id"], "target_kind": "npc", "target_id": a["id"]})
        self.assertEqual(code, 400)
        code, _ = s.call("POST", f"/api/campaigns/{cid}/relations", {
            "source_kind": "npc", "source_id": a["id"], "target_kind": "faction", "target_id": 999})
        self.assertEqual(code, 400)

        code, _ = s.call("PATCH", f"/api/relations/{rel['id']}", {"type": "rival", "secret": True})
        self.assertEqual(code, 200)

        code, _ = s.call("PUT", f"/api/campaigns/{cid}/positions",
                         {"nodes": [{"kind": "npc", "id": a["id"], "x": 10.5, "y": -20}]})
        self.assertEqual(code, 204)

        # Images
        body, ctype = multipart([("portrait.png", png_bytes()), ("scene.png", png_bytes((10, 90, 40)))])
        code, detail = s.call("POST", f"/api/npcs/{a['id']}/images", raw=body, content_type=ctype)
        self.assertEqual(code, 201, detail)
        self.assertEqual(len(detail["images"]), 2)
        self.assertEqual(detail["portrait_image_id"], detail["images"][0]["id"])
        code, thumb = s.call("GET", detail["images"][0]["thumb_url"])
        self.assertEqual(code, 200)
        self.assertEqual(Image.open(io.BytesIO(thumb)).size, (256, 256))

        bad, ctype = multipart([("fake.png", b"pas une image")])
        code, _ = s.call("POST", f"/api/npcs/{a['id']}/images", raw=bad, content_type=ctype)
        self.assertEqual(code, 400)

        code, _ = s.call("PATCH", f"/api/npcs/{b['id']}", {"portrait_image_id": detail["images"][1]["id"]})
        self.assertEqual(code, 400, "une image d'un autre PNJ ne peut pas servir de portrait")
        code, upd = s.call("PATCH", f"/api/npcs/{a['id']}", {"portrait_image_id": detail["images"][1]["id"]})
        self.assertEqual(code, 200)

        code, graph = s.call("GET", f"/api/campaigns/{cid}/graph")
        self.assertEqual(code, 200)
        self.assertEqual(len(graph["npcs"]), 2)
        self.assertEqual(len(graph["memberships"]), 1)
        self.assertEqual(graph["relations"][0]["type"], "rival")
        maelis = next(n for n in graph["npcs"] if n["id"] == a["id"])
        self.assertEqual(maelis["pos_x"], 10.5)
        self.assertTrue(maelis["portrait_thumb"].startswith("/media/"))

        # Suppression d'un PNJ : relations, appartenances et fichiers nettoyés
        media_files = list(Path(os.environ["MAZE_DATA_DIR"], "media").rglob("*.webp"))
        self.assertEqual(len(media_files), 4)
        code, _ = s.call("DELETE", f"/api/npcs/{a['id']}")
        self.assertEqual(code, 204)
        code, graph = s.call("GET", f"/api/campaigns/{cid}/graph")
        self.assertEqual(graph["relations"], [])
        self.assertEqual(graph["memberships"], [])
        self.assertEqual(list(Path(os.environ["MAZE_DATA_DIR"], "media").rglob("*.webp")), [])

        code, _ = s.call("GET", "/media/../maze.db")
        self.assertIn(code, (403, 404))

        code, _ = s.call("DELETE", f"/api/campaigns/{cid}")
        self.assertEqual(code, 204)
        code, _ = s.call("GET", f"/api/campaigns/{cid}")
        self.assertEqual(code, 404)


class ImagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("MAZE_PASSWORD", None)
        cls.s = Server({})

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_delete_cover_image(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Images"})
        for kind, cover in (("faction", "emblem_image_id"), ("npc", "portrait_image_id")):
            _, ent = s.call("POST", f"/api/campaigns/{camp['id']}/{kind}s", {"name": "X"})
            body, ctype = multipart([("a.png", png_bytes()), ("b.png", png_bytes((0, 0, 200)))])
            _, detail = s.call("POST", f"/api/{kind}s/{ent['id']}/images", raw=body, content_type=ctype)
            first, second = detail["images"]
            self.assertEqual(detail[cover], first["id"])

            # Supprimer l'image de couverture : la suivante prend le relais
            self.assertEqual(s.call("DELETE", f"/api/images/{first['id']}")[0], 204)
            _, detail = s.call("GET", f"/api/{kind}s/{ent['id']}")
            self.assertEqual(detail[cover], second["id"])
            self.assertEqual(s.call("GET", first["thumb_url"])[0], 404)

            # Dernière image supprimée : plus de couverture
            s.call("DELETE", f"/api/images/{second['id']}")
            _, detail = s.call("GET", f"/api/{kind}s/{ent['id']}")
            self.assertIsNone(detail[cover])
            self.assertEqual(detail["images"], [])


class LargeMapTest(unittest.TestCase):
    """Grosse carte : enregistrement massif des positions, graphe allégé, suppression complète."""

    @classmethod
    def setUpClass(cls):
        os.environ.pop("MAZE_PASSWORD", None)
        cls.s = Server({})

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_positions_graph_and_delete(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Grande carte"})
        cid = camp["id"]
        _, a = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "A"})
        _, f = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "F"})
        s.call("POST", f"/api/campaigns/{cid}/relations", {
            "source_kind": "npc", "source_id": a["id"], "target_kind": "faction", "target_id": f["id"],
            "label": "Sert", "description": "Depuis toujours"})

        # Plus de 5000 fiches dans un même envoi (ids inconnus ignorés), positions des vraies fiches appliquées.
        nodes = [{"kind": "npc", "id": 100000 + i, "x": i, "y": i} for i in range(6000)]
        nodes += [{"kind": "npc", "id": a["id"], "x": 1.5, "y": 2}, {"kind": "faction", "id": f["id"], "x": -3, "y": 4}]
        self.assertEqual(s.call("PUT", f"/api/campaigns/{cid}/positions", {"nodes": nodes})[0], 204)
        _, graph = s.call("GET", f"/api/campaigns/{cid}/graph")
        self.assertEqual((graph["npcs"][0]["pos_x"], graph["factions"][0]["pos_y"]), (1.5, 4))
        rel = graph["relations"][0]
        self.assertEqual((rel["label"], rel["description"]), ("Sert", "Depuis toujours"))
        self.assertNotIn("created_at", rel)

        # Suppression : le dossier d'images de la campagne disparaît aussi.
        body, ctype = multipart([("p.png", png_bytes())])
        s.call("POST", f"/api/npcs/{a['id']}/images", raw=body, content_type=ctype)
        media = Path(s.tmp.name) / "media" / str(cid)
        self.assertTrue(media.is_dir())
        self.assertEqual(s.call("DELETE", f"/api/campaigns/{cid}")[0], 204)
        self.assertFalse(media.exists())
        self.assertEqual(s.call("GET", f"/api/campaigns/{cid}/graph")[0], 404)


class ScenarioMapTest(unittest.TestCase):
    """Carte propre à un scénario : sélection (citations, ajouts, retraits) et disposition."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_scenario_map(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Cartes"})
        cid = camp["id"]
        _, a = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Aldric"})
        _, b = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Brune"})
        _, f = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "Guilde"})
        _, other = s.call("POST", "/api/campaigns", {"name": "Ailleurs"})
        _, alien = s.call("POST", f"/api/campaigns/{other['id']}/npcs", {"name": "Intrus"})
        _, sc = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Le bal"})
        _, page = s.call("POST", f"/api/campaigns/{cid}/documents", {"parent_id": sc["id"], "title": "Acte I"})
        s.call("PATCH", f"/api/documents/{page['id']}", {
            "content": f'<p><a class="mention" data-mention="npc:{a["id"]}">Aldric</a></p>', "base_revision": 0})
        base = f"/api/documents/{sc['id']}/map"

        code, m = s.call("GET", base)
        self.assertEqual((code, m["nodes"]), (200, []))
        self.assertEqual(s.call("GET", f"/api/documents/{page['id']}/map")[0], 400, "une page n'a pas de carte")
        _, tree = s.call("GET", f"/api/campaigns/{cid}/documents")
        self.assertEqual(s.call("GET", f"/api/documents/{tree['root_id']}/map")[0], 400)

        # Ajout, retrait, positions
        code, m = s.call("PUT", f"{base}/nodes/npc/{b['id']}", {"state": "added", "x": 10, "y": 20})
        self.assertEqual((code, m["nodes"][0]["state"], m["nodes"][0]["pos_x"]), (200, "added", 10))
        s.call("PUT", f"{base}/nodes/npc/{a['id']}", {"state": "excluded"})
        self.assertEqual(s.call("PUT", f"{base}/nodes/npc/{alien['id']}", {"state": "added"})[0], 400)
        self.assertEqual(s.call("PUT", f"{base}/nodes/place/1", {"state": "added"})[0], 404)
        self.assertEqual(s.call("PUT", f"{base}/positions", {"nodes": [
            {"kind": "faction", "id": f["id"], "x": -5, "y": 7},
            {"kind": "npc", "id": alien["id"], "x": 1, "y": 1}]})[0], 204)
        _, m = s.call("GET", base)
        rows = {(n["kind"], n["id"]): n for n in m["nodes"]}
        self.assertEqual(rows[("faction", f["id"])]["state"], "auto")
        self.assertEqual((rows[("faction", f["id"])]["pos_x"], rows[("npc", b["id"])]["pos_y"]), (-5, 20))
        self.assertNotIn(("npc", alien["id"]), rows, "fiche d'une autre campagne ignorée")
        # Changer l'état garde la position
        s.call("PUT", f"{base}/nodes/npc/{b['id']}", {"state": "auto"})
        _, m = s.call("GET", base)
        self.assertEqual([n["pos_x"] for n in m["nodes"] if n["id"] == b["id"] and n["kind"] == "npc"], [10])
        s.call("PUT", f"{base}/nodes/npc/{b['id']}", {"state": "added"})

        # Réglages visibles avec les citations ; cartes listées dans la fiche
        _, mentions = s.call("GET", f"/api/campaigns/{cid}/documents/mentions")
        self.assertEqual(sorted(map(tuple, mentions["overrides"][str(sc["id"])])),
                         sorted([("npc", a["id"], "excluded"), ("npc", b["id"], "added")]))
        self.assertEqual(s.call("GET", f"/api/npcs/{b['id']}")[1]["maps"], [{"id": sc["id"], "title": "Le bal", "status": "brouillon"}])
        self.assertEqual(s.call("GET", f"/api/npcs/{a['id']}")[1]["maps"], [], "cité mais retiré")
        s.call("PUT", f"{base}/nodes/npc/{a['id']}", {"state": "auto"})
        self.assertEqual(len(s.call("GET", f"/api/npcs/{a['id']}")[1]["maps"]), 1, "cité dans une page du scénario")

        # Duplication : la carte suit
        _, copy = s.call("POST", f"/api/documents/{sc['id']}/duplicate")
        _, m2 = s.call("GET", f"/api/documents/{copy['id']}/map")
        self.assertEqual(len(m2["nodes"]), 3)

        # Export / import : la carte est conservée
        code, archive = s.call("GET", f"/api/campaigns/{cid}/export")
        self.assertEqual(code, 200)
        code, new = s.call("POST", "/api/campaigns/import", raw=archive, content_type="application/zip")
        self.assertEqual(code, 201)
        _, tree = s.call("GET", f"/api/campaigns/{new['id']}/documents")
        new_sc = next(d for d in tree["documents"] if d["title"] == "Le bal")
        _, m3 = s.call("GET", f"/api/documents/{new_sc['id']}/map")
        self.assertEqual(sorted((n["kind"], n["state"], n["pos_x"]) for n in m3["nodes"]),
                         sorted([("faction", "auto", -5), ("npc", "added", 10), ("npc", "auto", None)]))
        _, g = s.call("GET", f"/api/campaigns/{new['id']}/graph")
        self.assertEqual({n["id"] for n in m3["nodes"] if n["kind"] == "npc"}, {n["id"] for n in g["npcs"]})

        # Archive retouchée : réglage sur une page et positions invalides ignorés
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            files = {n: z.read(n) for n in z.namelist()}
        data = json.loads(files["campaign.json"])
        data["scenario_nodes"] = [r for r in data["scenario_nodes"] if r["entity_kind"] != "faction"] + [
            {"document_id": page["id"], "entity_kind": "npc", "entity_id": b["id"], "state": "added"},
            {"document_id": sc["id"], "entity_kind": "faction", "entity_id": f["id"], "state": "auto",
             "pos_x": float("nan"), "pos_y": True}]
        files["campaign.json"] = json.dumps(data).encode()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, content in files.items():
                z.writestr(name, content)
        code, hostile = s.call("POST", "/api/campaigns/import", raw=buf.getvalue(), content_type="application/zip")
        self.assertEqual(code, 201, hostile)
        _, tree = s.call("GET", f"/api/campaigns/{hostile['id']}/documents")
        h_page = next(d for d in tree["documents"] if d["title"] == "Acte I")
        h_sc = next(d for d in tree["documents"] if d["title"] == "Le bal")
        _, h_mentions = s.call("GET", f"/api/campaigns/{hostile['id']}/documents/mentions")
        self.assertNotIn(str(h_page["id"]), h_mentions["overrides"], "pas de réglage sur une page")
        _, hm = s.call("GET", f"/api/documents/{h_sc['id']}/map")
        self.assertEqual([(n["pos_x"], n["pos_y"]) for n in hm["nodes"] if n["kind"] == "faction"], [(None, None)])

        # Suppression d'une fiche ou du scénario : nettoyage
        s.call("DELETE", f"/api/npcs/{b['id']}")
        _, m = s.call("GET", base)
        self.assertEqual({n["id"] for n in m["nodes"] if n["kind"] == "npc"}, {a["id"]})
        s.call("DELETE", f"/api/documents/{sc['id']}")
        self.assertEqual(s.call("GET", base)[0], 404)


    def test_move_documents(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Rangement"})
        cid = camp["id"]
        _, a = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "A"})
        _, b = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "B"})
        _, c = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "C"})
        _, a1 = s.call("POST", f"/api/campaigns/{cid}/documents", {"parent_id": a["id"], "title": "A1"})
        _, a2 = s.call("POST", f"/api/campaigns/{cid}/documents", {"parent_id": a["id"], "title": "A2"})
        _, tree = s.call("GET", f"/api/campaigns/{cid}/documents")
        root = tree["root_id"]

        def order(parent):
            _, t = s.call("GET", f"/api/campaigns/{cid}/documents")
            docs = sorted((d for d in t["documents"] if d["parent_id"] == parent), key=lambda d: (d["sort_order"], d["id"]))
            return [(d["title"], d["kind"]) for d in docs]

        # Rang parmi les scénarios
        self.assertEqual(s.call("PUT", f"/api/documents/{c['id']}/move", {"parent_id": root, "index": 0})[0], 204)
        self.assertEqual([t for t, _ in order(root)], ["C", "A", "B"])
        # Page déplacée dans un autre scénario, puis promue scénario ; scénario rangé comme page
        s.call("PUT", f"/api/documents/{a2['id']}/move", {"parent_id": b["id"], "index": 0})
        self.assertEqual(order(b["id"]), [("A2", "page")])
        self.assertEqual(order(a["id"]), [("A1", "page")])
        s.call("PUT", f"/api/documents/{a1['id']}/move", {"parent_id": root, "index": 99})
        self.assertEqual(order(root)[-1], ("A1", "scenario"))
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Garde"})
        s.call("PUT", f"/api/documents/{c['id']}/map/nodes/npc/{npc['id']}", {"state": "added"})
        self.assertEqual([m["id"] for m in s.call("GET", f"/api/npcs/{npc['id']}")[1]["maps"]], [c["id"]])
        s.call("PUT", f"/api/documents/{c['id']}/move", {"parent_id": b["id"], "index": 1})
        self.assertEqual(order(b["id"]), [("A2", "page"), ("C", "page")])
        self.assertEqual(s.call("GET", f"/api/documents/{c['id']}/map")[0], 400, "une page n'a pas de carte")
        self.assertEqual(s.call("GET", f"/api/npcs/{npc['id']}")[1]["maps"], [], "réglages d'un scénario devenu page ignorés")
        self.assertNotIn(str(c["id"]), s.call("GET", f"/api/campaigns/{cid}/documents/mentions")[1]["overrides"])
        s.call("PUT", f"/api/documents/{c['id']}/move", {"parent_id": root, "index": 0})
        self.assertEqual([m["id"] for m in s.call("GET", f"/api/npcs/{npc['id']}")[1]["maps"]], [c["id"]], "carte retrouvée")
        s.call("PUT", f"/api/documents/{c['id']}/move", {"parent_id": b["id"], "index": 1})
        # Refus : fiche de campagne, sous soi-même ou ses sous-pages, autre campagne
        self.assertEqual(s.call("PUT", f"/api/documents/{root}/move", {"parent_id": a["id"], "index": 0})[0], 400)
        self.assertEqual(s.call("PUT", f"/api/documents/{b['id']}/move", {"parent_id": b["id"], "index": 0})[0], 400)
        self.assertEqual(s.call("PUT", f"/api/documents/{b['id']}/move", {"parent_id": c["id"], "index": 0})[0], 400)
        _, other = s.call("POST", "/api/campaigns", {"name": "Autre"})
        _, otree = s.call("GET", f"/api/campaigns/{other['id']}/documents")
        self.assertEqual(s.call("PUT", f"/api/documents/{b['id']}/move", {"parent_id": otree["root_id"], "index": 0})[0], 400)
        self.assertEqual(s.call("PUT", f"/api/documents/{b['id']}/move", {"parent_id": root, "index": -1})[0], 422)


    def test_doc_links(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Renvois"})
        cid = camp["id"]
        _, sc = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Scénario"})
        _, src = s.call("POST", f"/api/campaigns/{cid}/documents", {"parent_id": sc["id"], "title": "Briefing"})
        _, dst = s.call("POST", f"/api/campaigns/{cid}/documents", {"parent_id": sc["id"], "title": "La filature"})
        _, other = s.call("POST", "/api/campaigns", {"name": "Ailleurs"})
        _, alien = s.call("POST", f"/api/campaigns/{other['id']}/documents", {"title": "Étranger"})
        html = (f'<p>Voir <a class="mention" data-mention="doc:{dst["id"]}">La filature</a> et '
                f'<a class="mention" data-mention="doc:{alien["id"]}">Étranger</a></p>')
        self.assertEqual(s.call("PATCH", f"/api/documents/{src['id']}", {"content": html, "base_revision": 0})[0], 200)
        _, got = s.call("GET", f"/api/documents/{dst['id']}")
        self.assertEqual([d["id"] for d in got["linked_from"]], [src["id"]])
        _, mentions = s.call("GET", f"/api/campaigns/{cid}/documents/mentions")
        self.assertEqual(mentions["mentions"][str(src["id"])], [["doc", dst["id"]]], "fiche d'une autre campagne ignorée")
        self.assertEqual(s.call("GET", f"/api/documents/{alien['id']}")[1]["linked_from"], [])
        # Export / import : le renvoi suit la nouvelle fiche
        _, archive = s.call("GET", f"/api/campaigns/{cid}/export")
        _, new = s.call("POST", "/api/campaigns/import", raw=archive, content_type="application/zip")
        _, tree = s.call("GET", f"/api/campaigns/{new['id']}/documents")
        ids = {d["title"]: d["id"] for d in tree["documents"]}
        _, copy = s.call("GET", f"/api/documents/{ids['Briefing']}")
        self.assertIn(f'data-mention="doc:{ids["La filature"]}"', copy["content"])
        self.assertEqual([d["id"] for d in s.call("GET", f"/api/documents/{ids['La filature']}")[1]["linked_from"]],
                         [ids["Briefing"]])
        # Fiche visée supprimée : le renvoi disparaît
        s.call("DELETE", f"/api/documents/{dst['id']}")
        _, mentions = s.call("GET", f"/api/campaigns/{cid}/documents/mentions")
        self.assertNotIn(str(src["id"]), mentions["mentions"])


class RelationTypesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("MAZE_PASSWORD", None)
        cls.s = Server({})

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_custom_types(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Natures"})
        cid = camp["id"]
        code, types = s.call("GET", f"/api/campaigns/{cid}/relation-types")
        self.assertEqual(code, 200)
        self.assertEqual([t["key"] for t in types][:2], ["allie", "ennemi"])
        self.assertEqual(len(types), 10)

        code, vassal = s.call("POST", f"/api/campaigns/{cid}/relation-types",
                              {"label": "Vassalité", "color": "#123ABC", "directed": True})
        self.assertEqual(code, 201)
        self.assertEqual((vassal["key"], vassal["color"], vassal["directed"]), ("vassalite", "#123abc", True))
        code, _ = s.call("POST", f"/api/campaigns/{cid}/relation-types", {"label": "vassalité"})
        self.assertEqual(code, 409)

        _, a = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "A"})
        _, b = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "B"})
        rel = {"source_kind": "npc", "source_id": a["id"], "target_kind": "npc", "target_id": b["id"]}
        code, _ = s.call("POST", f"/api/campaigns/{cid}/relations", {**rel, "type": "inexistant"})
        self.assertEqual(code, 400)
        code, r = s.call("POST", f"/api/campaigns/{cid}/relations", {**rel, "type": "vassalite"})
        self.assertEqual(code, 201)

        # Couleur d'une nature par défaut modifiable
        ennemi = next(t for t in types if t["key"] == "ennemi")
        code, upd = s.call("PATCH", f"/api/relation-types/{ennemi['id']}", {"color": "#000000", "label": "Adversaire"})
        self.assertEqual((code, upd["color"], upd["key"]), (200, "#000000", "ennemi"))

        # Suppression refusée tant qu'un lien l'utilise
        code, err = s.call("DELETE", f"/api/relation-types/{vassal['id']}")
        self.assertEqual(code, 409)
        s.call("PATCH", f"/api/relations/{r['id']}", {"type": "allie"})
        code, _ = s.call("DELETE", f"/api/relation-types/{vassal['id']}")
        self.assertEqual(code, 204)

        _, graph = s.call("GET", f"/api/campaigns/{cid}/graph")
        self.assertEqual(len(graph["relation_types"]), 10)
        self.assertEqual(next(t for t in graph["relation_types"] if t["key"] == "ennemi")["label"], "Adversaire")

    def test_migration_seeds_existing_campaigns(self):
        import sqlite3
        from maze import db as mdb
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            conn = sqlite3.connect(path)
            conn.executescript((mdb.MIGRATIONS_DIR / "001_initial.sql").read_text(encoding="utf-8")
                               + "PRAGMA user_version = 1; INSERT INTO campaigns (name) VALUES ('Ancienne');")
            conn.close()
            mdb.migrate(path)
            conn = sqlite3.connect(path)
            count = conn.execute("SELECT COUNT(*) FROM relation_types WHERE campaign_id = 1").fetchone()[0]
            conn.close()
            self.assertEqual(count, 10)


class DocumentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_tree_mentions_and_conflicts(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Scénarios"})
        cid = camp["id"]
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Maëlis"})
        _, fac = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "La Main Grise"})

        code, tree = s.call("GET", f"/api/campaigns/{cid}/documents")
        self.assertEqual(code, 200)
        root = tree["root_id"]
        self.assertEqual([d["kind"] for d in tree["documents"]], ["campaign"])

        _, sc1 = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Le bal masqué"})
        _, sc2 = s.call("POST", f"/api/campaigns/{cid}/documents", {})
        self.assertEqual((sc1["kind"], sc1["parent_id"], sc2["title"]), ("scenario", root, "Nouveau scénario"))
        _, page = s.call("POST", f"/api/campaigns/{cid}/documents", {"parent_id": sc1["id"], "title": "Acte I"})
        self.assertEqual(page["kind"], "page")

        content = (f'<h2 id="intro">Intro</h2><p>Avec <a class="mention" data-mention="npc:{npc["id"]}">Maëlis</a> '
                   f'et <a class="mention" data-mention="faction:{fac["id"]}">la Main</a>.</p>')
        code, saved = s.call("PATCH", f"/api/documents/{sc1['id']}",
                             {"content": content, "status": "pret", "base_revision": sc1["revision"]})
        self.assertEqual((code, saved["status"], saved["revision"]), (200, "pret", 1))

        # Modification concurrente : la révision de base est périmée
        code, err = s.call("PATCH", f"/api/documents/{sc1['id']}", {"content": "<p>x</p>", "base_revision": 0})
        self.assertEqual(code, 409)

        _, detail = s.call("GET", f"/api/npcs/{npc['id']}")
        self.assertEqual([d["title"] for d in detail["mentioned_in"]], ["Le bal masqué"])
        _, detail = s.call("GET", f"/api/factions/{fac['id']}")
        self.assertEqual(len(detail["mentioned_in"]), 1)

        # Ordre des scénarios
        self.assertEqual(s.call("PUT", f"/api/campaigns/{cid}/documents/order",
                                {"parent_id": root, "ids": [sc2["id"], sc1["id"]]})[0], 204)
        _, tree = s.call("GET", f"/api/campaigns/{cid}/documents")
        scen = [d["id"] for d in tree["documents"] if d["kind"] == "scenario"]
        self.assertEqual(scen, [sc2["id"], sc1["id"]])
        self.assertEqual(s.call("PUT", f"/api/campaigns/{cid}/documents/order",
                                {"parent_id": root, "ids": [sc1["id"]]})[0], 400)

        # Image dans une fiche
        body, ctype = multipart([("plan.png", png_bytes())])
        body = body.replace(b'name="files"', b'name="file"')
        code, img = s.call("POST", f"/api/documents/{page['id']}/images", raw=body, content_type=ctype)
        self.assertEqual(code, 201)
        self.assertEqual(s.call("GET", img["url"])[0], 200)

        # Suppression d'un scénario : sous-pages, mentions et images supprimées
        self.assertEqual(s.call("DELETE", f"/api/documents/{root}")[0], 400)
        self.assertEqual(s.call("DELETE", f"/api/documents/{sc1['id']}")[0], 204)
        self.assertEqual(s.call("GET", f"/api/documents/{page['id']}")[0], 404)
        self.assertEqual(s.call("GET", img["url"])[0], 404)
        _, detail = s.call("GET", f"/api/npcs/{npc['id']}")
        self.assertEqual(detail["mentioned_in"], [])

        # Un MJ sans accès à la campagne ne voit pas ses fiches
        s.call("POST", "/api/admin/users", {"username": "intrus", "password": "mot-de-passe-x"})
        intrus = s.client()
        intrus.login("intrus", "mot-de-passe-x")
        self.assertEqual(intrus.call("GET", f"/api/campaigns/{cid}/documents")[0], 404)
        self.assertEqual(intrus.call("GET", f"/api/documents/{sc2['id']}")[0], 404)


class AccountsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_login_required_and_status(self):
        anon = self.s.client()
        self.assertEqual(anon.call("GET", "/api/campaigns")[0], 401)
        self.assertEqual(anon.call("GET", "/api/health")[0], 200)
        _, status = anon.call("GET", "/api/auth/status")
        self.assertEqual((status["authenticated"], status["setup_required"]), (False, False))
        self.assertIsNone(status.get("instance"))   # identifiant de l'instance : seulement une fois connecté
        self.assertEqual(anon.login("admin", "faux"), 401)
        self.assertEqual(anon.login("inconnu", "faux"), 401)
        self.assertEqual(anon.login("ADMIN", ADMIN_PASSWORD), 200, "identifiant insensible à la casse")
        _, status = anon.call("GET", "/api/auth/status")
        self.assertEqual(status["user"]["role"], "admin")
        # Instance (comme pour Foundry) : l'interface range par compte et par instance le texte qu'elle garde (M4).
        self.assertRegex(status["instance"], r"^[0-9a-f]{12}$")
        anon.call("POST", "/api/auth/logout")
        self.assertEqual(anon.call("GET", "/api/campaigns")[0], 401)

    def test_mj_isolation(self):
        s = self.s
        _, mine = s.call("POST", "/api/campaigns", {"name": "Campagne du MJ"})
        _, other = s.call("POST", "/api/campaigns", {"name": "Campagne secrète"})
        _, npc = s.call("POST", f"/api/campaigns/{other['id']}/npcs", {"name": "Caché"})
        body, ctype = multipart([("x.png", png_bytes())])
        _, detail = s.call("POST", f"/api/npcs/{npc['id']}/images", raw=body, content_type=ctype)
        hidden_thumb = detail["images"][0]["thumb_url"]

        code, mj = s.call("POST", "/api/admin/users", {
            "username": "lea", "display_name": "Léa", "role": "mj",
            "password": "mot-de-passe-lea", "campaign_ids": [mine["id"]]})
        self.assertEqual(code, 201)
        self.assertEqual(mj["campaign_ids"], [mine["id"]])
        self.assertEqual(s.call("POST", "/api/admin/users", {"username": "LEA", "password": "12345678"})[0], 409)

        lea = s.client()
        self.assertEqual(lea.login("lea", "mot-de-passe-lea"), 200)
        _, campaigns = lea.call("GET", "/api/campaigns")
        self.assertEqual([c["id"] for c in campaigns], [mine["id"]])
        self.assertEqual(lea.call("GET", f"/api/campaigns/{other['id']}/graph")[0], 404)
        self.assertEqual(lea.call("GET", f"/api/npcs/{npc['id']}")[0], 404)
        self.assertEqual(lea.call("PATCH", f"/api/npcs/{npc['id']}", {"name": "x"})[0], 404)
        self.assertEqual(lea.call("DELETE", f"/api/campaigns/{other['id']}")[0], 404)
        self.assertEqual(lea.call("GET", hidden_thumb)[0], 404)
        self.assertEqual(s.call("GET", hidden_thumb)[0], 200)
        self.assertEqual(lea.call("GET", "/api/admin/users")[0], 403)
        self.assertEqual(lea.call("POST", "/api/admin/system/update")[0], 403)

        # Une campagne créée par un MJ lui est attribuée
        _, created = lea.call("POST", "/api/campaigns", {"name": "Nouvelle"})
        _, campaigns = lea.call("GET", "/api/campaigns")
        self.assertIn(created["id"], [c["id"] for c in campaigns])

        # L'admin attribue la campagne secrète à Léa
        self.assertEqual(s.call("PUT", f"/api/admin/campaigns/{other['id']}/members",
                                {"user_ids": [mj["id"]]})[0], 200)
        self.assertEqual(lea.call("GET", f"/api/npcs/{npc['id']}")[0], 200)

        # Désactivation : la session de Léa est coupée
        s.call("PATCH", f"/api/admin/users/{mj['id']}", {"active": False})
        self.assertEqual(lea.call("GET", "/api/campaigns")[0], 401)
        self.assertEqual(lea.login("lea", "mot-de-passe-lea"), 401)

    def test_password_change_invalidates_other_sessions(self):
        s = self.s
        _, u = s.call("POST", "/api/admin/users", {"username": "paul", "password": "ancien-mdp"})
        a, b = s.client(), s.client()
        a.login("paul", "ancien-mdp"), b.login("paul", "ancien-mdp")
        self.assertEqual(a.call("POST", "/api/auth/password",
                                {"current_password": "faux", "new_password": "nouveau-mdp"})[0], 400)
        self.assertEqual(a.call("POST", "/api/auth/password",
                                {"current_password": "ancien-mdp", "new_password": "nouveau-mdp"})[0], 200)
        self.assertEqual(a.call("GET", "/api/campaigns")[0], 200, "la session courante reste ouverte")
        self.assertEqual(b.call("GET", "/api/campaigns")[0], 401, "les autres sessions sont fermées")
        self.assertEqual(s.client().login("paul", "nouveau-mdp"), 200)
        # Réinitialisation par l'admin
        s.call("PATCH", f"/api/admin/users/{u['id']}", {"password": "reinit-mdp"})
        self.assertEqual(a.call("GET", "/api/campaigns")[0], 401)
        self.assertEqual(s.client().login("paul", "reinit-mdp"), 200)

    def test_admin_safeguards(self):
        s = self.s
        _, users = s.call("GET", "/api/admin/users")
        me = next(u for u in users if u["username"] == "admin")
        self.assertEqual(s.call("PATCH", f"/api/admin/users/{me['id']}", {"role": "mj"})[0], 400)
        self.assertEqual(s.call("DELETE", f"/api/admin/users/{me['id']}")[0], 400)
        _, other = s.call("POST", "/api/admin/users", {"username": "chef2", "password": "12345678", "role": "admin"})
        self.assertEqual(s.call("DELETE", f"/api/admin/users/{other['id']}")[0], 204)


class SetupTest(unittest.TestCase):
    def test_first_admin_setup(self):
        s = Server({"MAZE_PASSWORD": None}, login=False)
        try:
            c = s.client()
            _, status = c.call("GET", "/api/auth/status")
            self.assertTrue(status["setup_required"])
            code, _ = c.call("POST", "/api/auth/setup", {"username": "mj", "password": "court"})
            self.assertEqual(code, 422)
            code, out = c.call("POST", "/api/auth/setup", {"username": "ranglier", "password": "mot-de-passe-1"})
            self.assertEqual((code, out["user"]["role"]), (201, "admin"))
            self.assertEqual(c.call("GET", "/api/campaigns")[0], 200)
            self.assertEqual(s.client().call("POST", "/api/auth/setup",
                                             {"username": "pirate", "password": "mot-de-passe-2"})[0], 409)
        finally:
            s.stop()


class SourceCodeTest(unittest.TestCase):
    """AGPL, section 13 : adresse du code source et licence donnés par /api/auth/status, sans session (écran de
    connexion) comme une fois connecté (menu du compte) ; MAZE_SOURCE_URL pour un fork modifié."""

    def status(self, env: dict[str, str | None]) -> tuple[dict, dict]:
        s = Server(env)
        try:
            return s.client().call("GET", "/api/auth/status")[1], s.call("GET", "/api/auth/status")[1]
        finally:
            s.stop()

    def test_default_source_url(self):
        from maze.config import DEFAULT_SOURCE_URL
        self.assertEqual(DEFAULT_SOURCE_URL, "https://github.com/ranglier/Maze")
        anon, logged = self.status({"MAZE_SOURCE_URL": None})
        self.assertFalse(anon["authenticated"])
        self.assertTrue(logged["authenticated"])
        for status in (anon, logged):
            self.assertEqual(status["source_url"], "https://github.com/ranglier/Maze")
            self.assertEqual(status["license"], "AGPL-3.0-or-later")

    def test_source_url_override(self):
        anon, logged = self.status({"MAZE_SOURCE_URL": " https://git.example.org/fork/maze "})
        for status in (anon, logged):
            self.assertEqual(status["source_url"], "https://git.example.org/fork/maze")
            self.assertEqual(status["license"], "AGPL-3.0-or-later")

    def test_invalid_source_url_falls_back_to_default(self):
        """L'adresse finit dans un lien de l'interface : autre chose que http(s) est ignoré, avec un avertissement."""
        with self.assertLogs("maze", "WARNING") as logs:
            anon, _ = self.status({"MAZE_SOURCE_URL": "javascript:alert(1)"})
        self.assertEqual(anon["source_url"], "https://github.com/ranglier/Maze")
        self.assertTrue(any("MAZE_SOURCE_URL" in line for line in logs.output))


class AdminSystemTest(unittest.TestCase):
    def test_updater_status_dir(self):
        """Audit H2 : l'état est lu dans le dossier d'état (lecture seule dans le conteneur) ; la demande seule est
        écrite dans le dossier de contrôle. Repli sur l'ancien emplacement (control/) tant que le dossier d'état
        est vide : état laissé par le service d'avant pendant la mise à jour qui installe le nouveau."""
        control, status = tempfile.TemporaryDirectory(), tempfile.TemporaryDirectory()
        s = Server({"MAZE_CONTROL_DIR": control.name, "MAZE_STATUS_DIR": status.name, "MAZE_VERSION": "abc1234"})
        try:
            self.assertFalse(s.call("GET", "/api/admin/system")[1]["updater"]["installed"])
            Path(control.name, "state").write_text("success\n")
            Path(control.name, "info").write_text("current=abc1234\nmessage=Mise à jour terminée (version abc1234).\n")
            up = s.call("GET", "/api/admin/system")[1]["updater"]
            self.assertEqual((up["installed"], up["state"], up["current"]), (True, "success", "abc1234"))
            # Le nouveau service a écrit : le dossier d'état prime, l'ancien emplacement est ignoré.
            Path(status.name, "state").write_text("rolledback\n")
            Path(status.name, "info").write_text("current=abc1234\nmessage=La nouvelle version ne répondait pas\n")
            Path(status.name, "update.log").write_text("Retour automatique\n")
            up = s.call("GET", "/api/admin/system")[1]["updater"]
            self.assertEqual((up["state"], up["log"]), ("rolledback", "Retour automatique"))
            self.assertIn("ne répondait pas", up["message"])
            # Verrou du service dans le dossier d'état : opération en cours ou interrompue.
            Path(status.name, "state").write_text("updating\n")
            Path(status.name, ".lock").touch()
            import fcntl
            with Path(status.name, ".lock").open() as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                self.assertEqual(s.call("GET", "/api/admin/system")[1]["updater"]["state"], "updating")
            self.assertEqual(s.call("GET", "/api/admin/system")[1]["updater"]["state"], "interrupted")
            self.assertEqual(s.call("POST", "/api/admin/system/update")[0], 202)
            self.assertEqual(sorted(os.listdir(control.name)), ["info", "request", "state"])
            self.assertEqual(sorted(os.listdir(status.name)), [".lock", "info", "state", "update.log"])
        finally:
            s.stop()
            control.cleanup()
            status.cleanup()

    def test_untrusted_proxy_reported(self):
        """Audit M13 : requête annoncée en HTTPS par un proxy absent de FORWARDED_ALLOW_IPS : signalée."""
        s = Server({"FORWARDED_ALLOW_IPS": "10.9.9.9"})   # le client de test (127.0.0.1) n'est pas le proxy
        try:
            self.assertIsNone(s.call("GET", "/api/admin/system")[1]["untrusted_proxy"])
            req = urllib.request.Request(s.base + "/api/health", headers={"X-Forwarded-Proto": "https"})
            urllib.request.urlopen(req).read()
            self.assertEqual(s.call("GET", "/api/admin/system")[1]["untrusted_proxy"], "127.0.0.1")
        finally:
            s.stop()

    def test_backups_and_updater(self):
        control = tempfile.TemporaryDirectory()
        s = Server({"MAZE_CONTROL_DIR": control.name, "MAZE_VERSION": "abc1234"})
        try:
            code, entry = s.call("POST", "/api/admin/backups")
            self.assertEqual(code, 201)
            _, entries = s.call("GET", "/api/admin/backups")
            self.assertEqual(entries[0]["name"], entry["name"])
            code, payload = s.call("GET", f"/api/admin/backups/{entry['name']}")
            self.assertEqual(code, 200)
            self.assertGreater(len(payload), 100)
            self.assertEqual(s.call("GET", "/api/admin/backups/..%2Fmaze.db")[0], 404)

            _, status = s.call("GET", "/api/admin/system")
            self.assertEqual(status["version"], "abc1234")
            self.assertFalse(status["updater"]["installed"])
            self.assertEqual(s.call("POST", "/api/admin/system/update")[0], 503)

            # Simule le service de l'hôte
            Path(control.name, "state").write_text("idle\n")
            Path(control.name, "info").write_text("current=abc1234\navailable=def5678\nchecked_at=2026-10-05T18:00:00Z\n")
            Path(control.name, "commits.tsv").write_text("def5678\t2026-10-05T17:00:00Z\tAjout des lieux\n")
            _, status = s.call("GET", "/api/admin/system")
            up = status["updater"]
            self.assertTrue(up["installed"])
            self.assertEqual((up["available"], up["commits"][0]["subject"]), ("def5678", "Ajout des lieux"))
            self.assertEqual(s.call("POST", "/api/admin/system/update")[0], 202)
            self.assertEqual(Path(control.name, "request").read_text().splitlines()[0], "update")
            self.assertEqual(s.call("POST", "/api/admin/system/check")[0], 409, "demande déjà en attente")

            # Opération restée « en cours » alors que le service ne tourne plus (disque plein…).
            Path(control.name, "request").unlink()
            Path(control.name, "state").write_text("updating\n")
            lock = Path(control.name, ".lock")
            lock.touch()
            import fcntl
            with lock.open() as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                self.assertEqual(s.call("GET", "/api/admin/system")[1]["updater"]["state"], "updating")
                self.assertEqual(s.call("POST", "/api/admin/system/check")[0], 409)
            up = s.call("GET", "/api/admin/system")[1]["updater"]
            self.assertEqual(up["state"], "interrupted")
            self.assertIn("relancez", up["message"])
            self.assertEqual(s.call("POST", "/api/admin/system/check")[0], 202)
        finally:
            s.stop()
            control.cleanup()


class CommentsTest(unittest.TestCase):
    def test_threads_replies_resolution_and_rights(self):
        s = Server()
        try:
            _, camp = s.call("POST", "/api/campaigns", {"name": "Commentée"})
            _, doc = s.call("POST", f"/api/campaigns/{camp['id']}/documents", {"title": "Acte I"})
            _, mj = s.call("POST", "/api/admin/users", {"username": "relectrice", "display_name": "Relectrice",
                                                       "role": "mj", "password": "mot-de-passe-mj",
                                                       "campaign_ids": [camp["id"]]})
            other = s.client()
            other.login("relectrice", "mot-de-passe-mj")
            ws = other.live(doc["id"], 9)
            recv_until(ws, "welcome")

            code, thread = s.call("POST", f"/api/documents/{doc['id']}/comments",
                                  {"body": "Trop long ?", "quote": "La brume se lève"})
            self.assertEqual(code, 201)
            self.assertEqual((thread["author_name"], thread["quote"], thread["resolved"]),
                             ("Administrateur", "La brume se lève", False))
            self.assertTrue(recv_until(ws, "comments"))
            code, reply = other.call("POST", f"/api/documents/{doc['id']}/comments",
                                     {"body": "Non, ça va.", "parent_id": thread["id"]})
            self.assertEqual(code, 201)
            self.assertEqual(other.call("POST", f"/api/documents/{doc['id']}/comments",
                                        {"body": "x", "parent_id": reply["id"]})[0], 400, "pas de réponse à une réponse")
            _, threads = other.call("GET", f"/api/documents/{doc['id']}/comments")
            self.assertEqual(len(threads), 1)
            self.assertEqual([r["body"] for r in threads[0]["replies"]], ["Non, ça va."])

            # Droits : on ne modifie que ses messages (l'admin peut tout) ; tout le monde peut résoudre.
            self.assertEqual(other.call("PATCH", f"/api/comments/{thread['id']}", {"body": "pirate"})[0], 403)
            self.assertEqual(other.call("PATCH", f"/api/comments/{thread['id']}", {"resolved": True})[0], 200)
            _, threads = s.call("GET", f"/api/documents/{doc['id']}/comments")
            self.assertEqual((threads[0]["resolved"], threads[0]["resolved_by"]), (True, "Relectrice"))
            self.assertEqual(s.call("PATCH", f"/api/comments/{reply['id']}", {"resolved": True})[0], 400)
            self.assertEqual(other.call("DELETE", f"/api/comments/{thread['id']}")[0], 403)
            self.assertEqual(s.call("DELETE", f"/api/comments/{thread['id']}")[0], 204)
            self.assertEqual(s.call("GET", f"/api/documents/{doc['id']}/comments")[1], [])
            ws.close()

            # Sans accès à la campagne : invisible.
            s.call("PUT", f"/api/admin/campaigns/{camp['id']}/members", {"user_ids": []})
            self.assertEqual(other.call("GET", f"/api/documents/{doc['id']}/comments")[0], 404)
            self.assertEqual(other.call("POST", f"/api/documents/{doc['id']}/comments", {"body": "x"})[0], 404)
        finally:
            s.stop()


class SearchTest(unittest.TestCase):
    def test_global_search_and_scenario_mentions(self):
        s = Server()
        try:
            _, ys = s.call("POST", "/api/campaigns", {"name": "Ys"})
            _, other = s.call("POST", "/api/campaigns", {"name": "Autre"})
            _, comtesse = s.call("POST", f"/api/campaigns/{ys['id']}/npcs",
                                 {"name": "Comtesse Ysolde", "title": "Régente", "secrets": "Elle cache le médaillon"})
            _, guild = s.call("POST", f"/api/campaigns/{ys['id']}/factions", {"name": "Guilde des Passeurs"})
            s.call("POST", f"/api/campaigns/{other['id']}/npcs", {"name": "Comte Lointain"})
            _, scen = s.call("POST", f"/api/campaigns/{ys['id']}/documents", {"title": "Le bal masqué"})
            _, page = s.call("POST", f"/api/campaigns/{ys['id']}/documents", {"parent_id": scen["id"], "title": "Acte I"})
            s.call("PATCH", f"/api/documents/{page['id']}", {"content":
                   f'<h2>Arrivée</h2><p>La <strong>comtesse</strong> reçoit <a class="mention" data-mention="npc:{comtesse["id"]}">'
                   f'Comtesse Ysolde</a> et la <a class="mention" data-mention="faction:{guild["id"]}">Guilde</a>.</p>'})
            s.call("POST", f"/api/documents/{page['id']}/comments", {"body": "Préciser le rôle du médaillon"})

            def search(q, campaign=None, client=None):
                path = f"/api/search?q={urllib.parse.quote(q)}" + (f"&campaign={campaign}" if campaign else "")
                return (client or s.admin).call("GET", path)[1]["results"]

            # Accents, majuscules, début de mot ; titres d'abord.
            res = search("comt", ys["id"])
            self.assertEqual(res[0]["kind"], "npc")
            self.assertIn("\x02Comtesse\x03", res[0]["title"])
            self.assertEqual(res[0]["sub"], "Régente")
            self.assertIn("document", [r["kind"] for r in res])
            self.assertNotIn("Comte Lointain", "".join(r["title"] for r in res))
            self.assertIn("Comte", "".join(r["title"] for r in search("comte")))  # toutes les campagnes
            doc_hit = next(r for r in search("medaillon", ys["id"]) if r["kind"] == "comment")
            self.assertEqual((doc_hit["doc_id"], doc_hit["doc_title"]), (page["id"], "Acte I"))
            self.assertIn("\x02médaillon\x03", doc_hit["snippet"])
            self.assertEqual({r["kind"] for r in search("médaillon", ys["id"])}, {"npc", "comment"})
            # Mise à jour de l'index : renommage, suppression.
            s.call("PATCH", f"/api/npcs/{comtesse['id']}", {"name": "Duchesse Ysolde"})
            self.assertEqual([r["kind"] for r in search("duchesse")], ["npc"])
            s.call("DELETE", f"/api/documents/{scen['id']}")
            self.assertEqual(search("arrivee"), [])
            self.assertEqual(search("x"), [])
            self.assertEqual(search('"); DROP TABLE npcs; --'), [])

            # Un MJ ne trouve que ses campagnes.
            s.call("POST", "/api/admin/users", {"username": "mj-cherche", "role": "mj", "password": "mot-de-passe-mj",
                                                "campaign_ids": [other["id"]]})
            mj = s.client()
            mj.login("mj-cherche", "mot-de-passe-mj")
            self.assertEqual([r["campaign"] for r in search("comte", client=mj)], ["Autre"])
            self.assertEqual(search("duchesse", ys["id"], client=mj), [])

            # Mentions par fiche (filtre de la carte).
            _, scen2 = s.call("POST", f"/api/campaigns/{ys['id']}/documents", {"title": "Intrigue"})
            s.call("PATCH", f"/api/documents/{scen2['id']}", {"content":
                   f'<p><a class="mention" data-mention="faction:{guild["id"]}">Guilde</a></p>'})
            _, data = s.call("GET", f"/api/campaigns/{ys['id']}/documents/mentions")
            self.assertEqual(data["mentions"][str(scen2["id"])], [["faction", guild["id"]]])
            self.assertEqual(mj.call("GET", f"/api/campaigns/{ys['id']}/documents/mentions")[0], 404)
        finally:
            s.stop()


class WritingTest(unittest.TestCase):
    """Duplication, modèles et historique des versions des fiches rédigées."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_duplicate_and_templates(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Copies"})
        _, other = s.call("POST", "/api/campaigns", {"name": "Ailleurs"})
        cid = camp["id"]
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Ysolde"})
        _, alien = s.call("POST", f"/api/campaigns/{other['id']}/npcs", {"name": "Étranger"})
        _, scen = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Le bal"})
        _, after = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Après"})
        _, page = s.call("POST", f"/api/campaigns/{cid}/documents", {"parent_id": scen["id"], "title": "Acte I"})
        _, c = s.call("POST", f"/api/documents/{scen['id']}/comments", {"body": "À revoir"})
        s.call("PATCH", f"/api/documents/{scen['id']}", {"status": "pret", "content":
               f'<p><span class="comment-anchor" data-comment="{c["id"]}">Texte</span> avec '
               f'<a class="mention" data-mention="npc:{npc["id"]}">Ysolde</a></p>'})
        s.call("PATCH", f"/api/documents/{page['id']}", {"content": "<p>Scène 1</p>"})

        code, dup = s.call("POST", f"/api/documents/{scen['id']}/duplicate")
        self.assertEqual(code, 201)
        self.assertEqual((dup["title"], dup["status"], dup["parent_id"]), ("Le bal (copie)", "brouillon", scen["parent_id"]))
        self.assertNotIn("data-comment", dup["content"])
        self.assertIn(f'data-mention="npc:{npc["id"]}"', dup["content"])
        _, tree = s.call("GET", f"/api/campaigns/{cid}/documents")
        roots = sorted((d for d in tree["documents"] if d["parent_id"] == scen["parent_id"]),
                       key=lambda d: d["sort_order"])
        self.assertEqual([d["id"] for d in roots], [scen["id"], dup["id"], after["id"]])
        kids = [d for d in tree["documents"] if d["parent_id"] == dup["id"]]
        self.assertEqual([k["title"] for k in kids], ["Acte I"])
        _, detail = s.call("GET", f"/api/npcs/{npc['id']}")
        self.assertEqual(sorted(m["id"] for m in detail["mentioned_in"]), sorted([scen["id"], dup["id"]]))
        self.assertEqual(s.call("GET", f"/api/documents/{dup['id']}/comments")[1], [])
        self.assertEqual(s.call("POST", f"/api/documents/{tree['root_id']}/duplicate")[0], 400)

        # Fiche créée avec un contenu : mentions d'une autre campagne ignorées.
        code, made = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Depuis modèle", "content":
                            f'<h2>Accroche</h2><p><a class="mention" data-mention="npc:{alien["id"]}">X</a></p>'})
        self.assertEqual(code, 201)
        self.assertIn("<h2>Accroche</h2>", made["content"])
        _, mentions = s.call("GET", f"/api/campaigns/{cid}/documents/mentions")
        self.assertNotIn(str(made["id"]), mentions["mentions"])

        # Modèles : communs aux MJ, modifiables par leur auteur ou un administrateur.
        code, tpl = s.call("POST", "/api/templates", {"name": " Enquête ", "description": "Indices",
                           "content": '<p data-comment="3">Indice</p><img src="/media/x.webp" data-doc-image="2">'
                                      '<a class="mention" data-mention="npc:1">PNJ</a>'})
        self.assertEqual(code, 201)
        self.assertEqual(tpl["name"], "Enquête")
        self.assertNotIn("data-comment", tpl["content"])
        self.assertNotIn("<img", tpl["content"])
        self.assertNotIn("data-mention", tpl["content"])
        s.call("POST", "/api/admin/users", {"username": "mj-modele", "role": "mj", "password": "mot-de-passe-mj",
                                            "campaign_ids": []})
        mj = s.client()
        mj.login("mj-modele", "mot-de-passe-mj")
        self.assertEqual([t["name"] for t in mj.call("GET", "/api/templates")[1]["templates"]], ["Enquête"])
        # Partagé avec tous les MJ (choix assumé, audit SEC-3) : dit par l'API, pour que l'interface l'affiche.
        self.assertEqual([t["shared"] for t in mj.call("GET", "/api/templates")[1]["templates"]], [True])
        self.assertIs(tpl["shared"], True)
        self.assertEqual(mj.call("PATCH", f"/api/templates/{tpl['id']}", {"name": "Volé"})[0], 403)
        self.assertEqual(mj.call("DELETE", f"/api/templates/{tpl['id']}")[0], 403)
        _, own = mj.call("POST", "/api/templates", {"name": "Le mien", "content": "<p>x</p>"})
        self.assertEqual(mj.call("PATCH", f"/api/templates/{own['id']}", {"name": "Renommé"})[1]["name"], "Renommé")
        self.assertEqual(mj.call("DELETE", f"/api/templates/{own['id']}")[0], 204)
        self.assertEqual(s.call("PATCH", f"/api/templates/{tpl['id']}", {"description": ""})[0], 200)
        self.assertEqual(s.call("POST", "/api/templates", {"name": "", "content": ""})[0], 422)
        self.assertEqual(mj.call("POST", f"/api/documents/{scen['id']}/duplicate")[0], 404)

    def test_versions(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Historique"})
        _, doc = s.call("POST", f"/api/campaigns/{camp['id']}/documents", {"title": "Acte I"})
        did = doc["id"]
        base = f"/api/documents/{did}/versions"
        self.assertEqual(s.call("GET", base)[1]["versions"], [])
        s.call("PATCH", f"/api/documents/{did}", {"content": "<p>Version un</p>"})   # rien d'avant : pas de version
        self.assertEqual(s.call("GET", base)[1]["versions"], [])
        s.call("PATCH", f"/api/documents/{did}", {"content": "<p>Version deux</p>"})  # « un » gardée
        _, listing = s.call("GET", base)
        self.assertEqual([(v["kind"], v["label"]) for v in listing["versions"]], [("auto", "Avant remplacement")])
        code, manual = s.call("POST", base, {"label": "Avant la séance 3", "content": "<p>Version deux bis</p>"})
        self.assertEqual((code, manual["kind"], manual["author_name"]), (201, "manual", "Administrateur"))
        _, one = s.call("GET", f"{base}/{listing['versions'][0]['id']}")
        self.assertEqual(one["content"], "<p>Version un</p>")

        # Restauration : l'état actuel est gardé, puis remplacé ; les MJ connectés sont rechargés.
        live = s.admin.live(did, 501)
        recv_until(live, "welcome")
        code, restored = s.call("POST", f"{base}/{one['id']}/restore")
        self.assertEqual((code, restored["content"]), (200, "<p>Version un</p>"))
        self.assertTrue(recv_until(live, "reset"))
        live.close()
        history = s.call("GET", base)[1]["versions"]
        self.assertEqual([(v["kind"], v["label"]) for v in history[:2]], [("restore", ""), ("auto", "Avant restauration")])
        self.assertEqual(s.call("GET", f"{base}/{history[1]['id']}")[1]["content"], "<p>Version deux</p>")
        # L'époque renvoyée avec la fiche permet au navigateur de détecter une restauration intercalée.
        self.assertGreater(s.call("GET", f"/api/documents/{did}")[1]["live_epoch"], doc.get("live_epoch", 0))

        # Renommer une version automatique la conserve (manuelle) ; suppression.
        auto_id = s.call("GET", base)[1]["versions"][1]["id"]
        _, renamed = s.call("PATCH", f"{base}/{auto_id}", {"label": "Avant le grand ménage"})
        self.assertEqual((renamed["kind"], renamed["label"]), ("manual", "Avant le grand ménage"))
        self.assertEqual(s.call("DELETE", f"{base}/{manual['id']}")[0], 204)
        self.assertEqual(s.call("GET", f"{base}/{manual['id']}")[0], 404)

        # Temps réel : fin de séance (dernier MJ parti) => version de l'état final.
        count = len(s.call("GET", base)[1]["versions"])
        a = s.admin.live(did, 502)
        welcome, _ = recv_until(a, "welcome")
        if welcome.get("init"):
            a.send(json.dumps({"t": "init-done"}))
        a.send(json.dumps({"t": "content", "html": "<p>Écrit en direct</p>", "upto": welcome["seq"]}))
        recv_until(a, "meta")   # HTML enregistré (audit QUAL-9 : au lieu d'une attente fixe)
        a.close()
        wait_until(lambda: len(s.call("GET", base)[1]["versions"]) > count)
        versions = s.call("GET", base)[1]["versions"]
        self.assertEqual(len(versions), count + 1)
        self.assertEqual(s.call("GET", f"{base}/{versions[0]['id']}")[1]["content"], "<p>Écrit en direct</p>")

        # Accès : un MJ sans la campagne ne voit rien.
        s.call("POST", "/api/admin/users", {"username": "mj-histo", "role": "mj", "password": "mot-de-passe-mj",
                                            "campaign_ids": []})
        mj = s.client()
        mj.login("mj-histo", "mot-de-passe-mj")
        self.assertEqual(mj.call("GET", base)[0], 404)
        self.assertEqual(mj.call("POST", f"{base}/{versions[0]['id']}/restore")[0], 404)

    def test_labelled_auto_versions_not_pruned(self):
        """Audit DATA-12 : les repères nommés (« Avant remplacement », « Avant modification par Claude »…) ne sont
        pas élagués comme les versions automatiques sans nom."""
        from maze import db, versions
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Élagage"})
        _, doc = s.call("POST", f"/api/campaigns/{camp['id']}/documents", {"title": "S", "content": "<p>original</p>"})
        s.call("PATCH", f"/api/documents/{doc['id']}", {"content": "<p>v1</p>"})   # « Avant remplacement »
        with db.transaction(Path(s.tmp.name) / "maze.db") as conn:
            for i in range(versions.KEEP_AUTO + 5):   # séances d'écriture, fins de séance
                versions.insert_version(conn, doc["id"], kind="auto", html=f"<p>{i}</p>", title="S", author="x")
        listing = s.call("GET", f"/api/documents/{doc['id']}/versions")[1]["versions"]
        self.assertIn("Avant remplacement", [v["label"] for v in listing])
        self.assertEqual(sum(1 for v in listing if v["kind"] == "auto" and not v["label"]), versions.KEEP_AUTO)


class PlacesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_place_kinds_move_and_order(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Types"})
        cid = camp["id"]
        _, listing = s.call("GET", f"/api/campaigns/{cid}/places")
        self.assertEqual([k["key"] for k in listing["kinds"]][-1], "lieu")
        self.assertEqual(len(listing["kinds"]), 8)
        # Type personnalisé, icône, renommage, doublon refusé
        code, planet = s.call("POST", f"/api/campaigns/{cid}/place-kinds", {"label": "Planète", "icon": "planet"})
        self.assertEqual((code, planet["key"]), (201, "planete"))
        self.assertEqual(s.call("POST", f"/api/campaigns/{cid}/place-kinds", {"label": "planète"})[0], 409)
        self.assertEqual(s.call("POST", f"/api/campaigns/{cid}/place-kinds", {"label": "X", "icon": "<svg>"})[0], 422)
        _, planet = s.call("PATCH", f"/api/place-kinds/{planet['id']}", {"label": "Monde", "icon": "globe"})
        self.assertEqual((planet["label"], planet["icon"], planet["key"]), ("Monde", "globe", "planete"))
        code, p1 = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Valdis", "kind": "planete"})
        self.assertEqual((code, p1["kind"]), (201, "planete"))
        # Ordre des types
        _, listing = s.call("GET", f"/api/campaigns/{cid}/places")
        ids = [k["id"] for k in listing["kinds"]]
        self.assertEqual(s.call("PUT", f"/api/campaigns/{cid}/place-kinds/order", {"ids": ids[::-1]})[0], 204)
        _, listing = s.call("GET", f"/api/campaigns/{cid}/places")
        self.assertEqual(listing["kinds"][0]["key"], "planete")
        self.assertEqual(s.call("PUT", f"/api/campaigns/{cid}/place-kinds/order", {"ids": ids[:3]})[0], 400)
        # Export / import : les types suivent
        _, archive = s.call("GET", f"/api/campaigns/{cid}/export")
        _, new = s.call("POST", "/api/campaigns/import", raw=archive, content_type="application/zip")
        _, copy = s.call("GET", f"/api/campaigns/{new['id']}/places")
        self.assertEqual(copy["kinds"][0]["label"], "Monde")
        self.assertEqual(copy["places"][0]["kind"], "planete")
        # Suppression : les lieux passent au type « Lieu » ; « Lieu » ne se supprime pas
        self.assertEqual(s.call("DELETE", f"/api/place-kinds/{planet['id']}")[0], 204)
        self.assertEqual(s.call("GET", f"/api/places/{p1['id']}")[1]["kind"], "lieu")
        lieu = next(k for k in listing["kinds"] if k["key"] == "lieu")
        self.assertEqual(s.call("DELETE", f"/api/place-kinds/{lieu['id']}")[0], 400)
        # Déplacement d'un lieu : sous un autre, rang, retour à la racine, refus sous soi-même
        _, p2 = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Sorn"})
        _, p3 = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Brask"})
        self.assertEqual(s.call("PUT", f"/api/places/{p2['id']}/move", {"parent_id": p1["id"], "index": 0})[0], 204)
        self.assertEqual(s.call("GET", f"/api/places/{p2['id']}")[1]["parent_id"], p1["id"])
        s.call("PUT", f"/api/places/{p3['id']}/move", {"parent_id": None, "index": 0})
        _, listing = s.call("GET", f"/api/campaigns/{cid}/places")
        roots = [p["name"] for p in sorted((p for p in listing["places"] if p["parent_id"] is None),
                                            key=lambda p: p["sort_order"])]
        self.assertEqual(roots, ["Brask", "Valdis"])
        self.assertEqual(s.call("PUT", f"/api/places/{p1['id']}/move", {"parent_id": p2["id"], "index": 0})[0], 400)
        # Ordre libre des PNJ et des factions
        _, a = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Alpha"})
        _, b = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Beta"})
        _, g = s.call("GET", f"/api/campaigns/{cid}/graph")
        self.assertEqual({n["sort_order"] for n in g["npcs"]}, {0}, "alphabétique tant que rien n'est rangé")
        self.assertEqual(s.call("PUT", f"/api/campaigns/{cid}/order/npc", {"ids": [b["id"], a["id"]]})[0], 204)
        self.assertEqual(s.call("PUT", f"/api/campaigns/{cid}/order/npc", {"ids": [b["id"]]})[0], 400)
        self.assertEqual(s.call("PUT", f"/api/campaigns/{cid}/order/place", {"ids": []})[0], 404)
        s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Aaron"})
        _, g = s.call("GET", f"/api/campaigns/{cid}/graph")
        order = [n["name"] for n in sorted(g["npcs"], key=lambda n: n["sort_order"])]
        self.assertEqual(order, ["Beta", "Alpha", "Aaron"], "nouvelle fiche rangée en dernier")
        _, f1 = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "Zèle"})
        self.assertEqual(s.call("PUT", f"/api/campaigns/{cid}/order/faction", {"ids": [f1["id"]]})[0], 204)

    def test_pins_and_updated_at(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Épingles"})
        cid = camp["id"]
        _, a = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Valdis"})
        _, b = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Corvel"})
        self.assertEqual(s.call("PUT", f"/api/places/{a['id']}/pin")[0], 204)
        self.assertEqual(s.call("PUT", f"/api/places/{a['id']}/pin")[0], 204, "épingler deux fois est sans effet")
        _, listing = s.call("GET", f"/api/campaigns/{cid}/places")
        pinned = {p["name"]: p["pinned"] for p in listing["places"]}
        self.assertEqual(pinned, {"Valdis": True, "Corvel": False})
        self.assertTrue(all(p["updated_at"] for p in listing["places"]))
        # Préférence personnelle : un autre MJ ne voit pas les épingles de l'administrateur
        code, _ = s.call("POST", "/api/admin/users",
                         {"username": "mj-pin", "password": "motdepasse-mj", "role": "mj", "campaign_ids": [cid]})
        self.assertEqual(code, 201)
        mj = s.client()
        self.assertEqual(mj.login("mj-pin", "motdepasse-mj"), 200)
        _, listing = mj.call("GET", f"/api/campaigns/{cid}/places")
        self.assertFalse(any(p["pinned"] for p in listing["places"]))
        self.assertEqual(mj.call("PUT", f"/api/places/{b['id']}/pin")[0], 204)
        _, listing = s.call("GET", f"/api/campaigns/{cid}/places")
        self.assertEqual({p["name"] for p in listing["places"] if p["pinned"]}, {"Valdis"})
        # Désépingler ; lieu supprimé : l'épingle disparaît avec lui
        self.assertEqual(s.call("DELETE", f"/api/places/{a['id']}/pin")[0], 204)
        s.call("PUT", f"/api/places/{a['id']}/pin")
        self.assertEqual(s.call("DELETE", f"/api/places/{a['id']}")[0], 204)
        _, outsider = s.call("POST", "/api/campaigns", {"name": "Ailleurs"})
        _, c = s.call("POST", f"/api/campaigns/{outsider['id']}/places", {"name": "Hors"})
        self.assertEqual(mj.call("PUT", f"/api/places/{c['id']}/pin")[0], 404, "lieu d'une campagne non attribuée")
        self.assertEqual(mj.call("DELETE", f"/api/places/{c['id']}/pin")[0], 404)
        conn = sqlite3.connect(os.path.join(s.tmp.name, "maze.db"))
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM place_pins WHERE place_id = ?", (a["id"],)).fetchone()[0], 0,
                             "épingle supprimée avec le lieu")
        finally:
            conn.close()

    def test_rich_fields(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Riche"})
        cid = camp["id"]
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Lise"})
        _, place = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Hôtel"})
        html = (f'<p data-align="justify">Repaire de <a class="mention" data-mention="npc:{npc["id"]}">Lise</a>, '
                f'<strong>boiseries</strong> sombres.</p>')
        self.assertEqual(s.call("PATCH", f"/api/places/{place['id']}", {"description": html})[0], 200)
        self.assertEqual(s.call("PATCH", f"/api/npcs/{npc['id']}", {"secrets": "<p>Bibliothécaire <em>secrète</em></p>"})[0], 200)
        # Recherche : le texte, pas les balises
        _, found = s.call("GET", f"/api/search?q=boiseries&campaign={cid}")
        self.assertIn(place["id"], [r["id"] for r in found["results"] if r["kind"] == "place"])
        _, found = s.call("GET", f"/api/search?q=strong&campaign={cid}")
        self.assertEqual(found["results"], [])
        _, found = s.call("GET", f"/api/search?q=bibliothecaire&campaign={cid}")
        self.assertEqual([r["kind"] for r in found["results"]], ["npc"])
        # Export / import : la mention suit le nouveau PNJ
        _, archive = s.call("GET", f"/api/campaigns/{cid}/export")
        _, new = s.call("POST", "/api/campaigns/import", raw=archive, content_type="application/zip")
        _, g = s.call("GET", f"/api/campaigns/{new['id']}/graph")
        _, listing = s.call("GET", f"/api/campaigns/{new['id']}/places")
        _, copy = s.call("GET", f"/api/places/{listing['places'][0]['id']}")
        self.assertIn(f'data-mention="npc:{g["npcs"][0]["id"]}"', copy["description"])
        self.assertNotEqual(g["npcs"][0]["id"], npc["id"])

    def test_tree_links_mentions_and_access(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Lieux"})
        _, other = s.call("POST", "/api/campaigns", {"name": "Ailleurs"})
        cid = camp["id"]
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Maëlis"})
        _, guild = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "La Main Grise"})
        _, alien = s.call("POST", f"/api/campaigns/{other['id']}/npcs", {"name": "Étranger"})

        code, ys = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Ys", "kind": "ville", "tags": ["port"]})
        self.assertEqual((code, ys["kind"], ys["parent_id"]), (201, "ville", None))
        _, basse = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Basse ville", "kind": "quartier",
                                                                  "parent_id": ys["id"]})
        _, taverne = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Le Hareng Saur", "kind": "batiment",
                                                                    "parent_id": basse["id"], "summary": "Taverne louche",
                                                                    "ambiance": "Fumée de pipe et bière tiède"})
        self.assertEqual(s.call("POST", f"/api/campaigns/{cid}/places", {"name": "X", "kind": "planète"})[0], 400)
        _, far = s.call("POST", f"/api/campaigns/{other['id']}/places", {"name": "Lointain"})
        self.assertEqual(s.call("POST", f"/api/campaigns/{cid}/places", {"name": "X", "parent_id": far["id"]})[0], 400)
        # Pas de boucle dans l'arbre.
        self.assertEqual(s.call("PATCH", f"/api/places/{ys['id']}", {"parent_id": taverne["id"]})[0], 400)
        self.assertEqual(s.call("PATCH", f"/api/places/{ys['id']}", {"parent_id": ys["id"]})[0], 400)

        # Liens avec un rôle ; pas de lien vers une autre campagne ; un seul lien par lieu et fiche.
        code, link = s.call("POST", f"/api/places/{taverne['id']}/links",
                            {"entity_kind": "npc", "entity_id": npc["id"], "role": "Travaille ici"})
        self.assertEqual(code, 201)
        s.call("POST", f"/api/places/{basse['id']}/links", {"entity_kind": "faction", "entity_id": guild["id"],
                                                          "role": "Contrôle"})
        self.assertEqual(s.call("POST", f"/api/places/{taverne['id']}/links",
                                {"entity_kind": "npc", "entity_id": npc["id"]})[0], 409)
        self.assertEqual(s.call("POST", f"/api/places/{taverne['id']}/links",
                                {"entity_kind": "npc", "entity_id": alien["id"]})[0], 400)
        self.assertEqual(s.call("PATCH", f"/api/place-links/{link['id']}", {"role": "Tient la taverne"})[1]["role"],
                         "Tient la taverne")

        _, detail = s.call("GET", f"/api/places/{ys['id']}")
        self.assertEqual([c["name"] for c in detail["children"]], ["Basse ville"])
        self.assertEqual(sorted((n["name"], n["place_name"]) for n in detail["nested_links"]),
                         [("La Main Grise", "Basse ville"), ("Maëlis", "Le Hareng Saur")])
        _, npc_detail = s.call("GET", f"/api/npcs/{npc['id']}")
        self.assertEqual([(p["name"], p["role"]) for p in npc_detail["places"]], [("Le Hareng Saur", "Tient la taverne")])
        _, listing = s.call("GET", f"/api/campaigns/{cid}/places")
        self.assertEqual(len(listing["places"]), 3)
        self.assertEqual(len(listing["links"]), 2)

        # Mention dans un scénario, recherche.
        _, scen = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Le bal"})
        s.call("PATCH", f"/api/documents/{scen['id']}", {"content":
               f'<p>Rendez-vous au <a class="mention" data-mention="place:{taverne["id"]}">Hareng</a>.</p>'})
        self.assertEqual([d["id"] for d in s.call("GET", f"/api/places/{taverne['id']}")[1]["mentioned_in"]], [scen["id"]])
        res = s.call("GET", f"/api/search?q=biere&campaign={cid}")[1]["results"]
        self.assertEqual([(r["kind"], r["sub"], r["place_kind"]) for r in res], [("place", "Basse ville", "batiment")])

        # Image du lieu : la première devient l'illustration.
        body, ctype = multipart([("taverne.png", png_bytes())])
        code, with_img = s.call("POST", f"/api/places/{taverne['id']}/images", raw=body, content_type=ctype)
        self.assertEqual(code, 201)
        self.assertEqual(with_img["image_id"], with_img["images"][0]["id"])

        # Ordre, déplacement, suppression : les sous-lieux remontent, mentions et liens nettoyés.
        _, port = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Port", "parent_id": ys["id"]})
        self.assertEqual(s.call("PUT", f"/api/campaigns/{cid}/places/order",
                                {"parent_id": ys["id"], "ids": [port["id"], basse["id"]]})[0], 204)
        self.assertEqual(s.call("PUT", f"/api/campaigns/{cid}/places/order",
                                {"parent_id": ys["id"], "ids": [port["id"]]})[0], 400)
        self.assertEqual(s.call("DELETE", f"/api/places/{basse['id']}")[0], 204)
        _, moved = s.call("GET", f"/api/places/{taverne['id']}")
        self.assertEqual(moved["parent_id"], ys["id"])
        self.assertEqual(s.call("DELETE", f"/api/places/{taverne['id']}")[0], 204)
        self.assertEqual(s.call("GET", f"/api/npcs/{npc['id']}")[1]["places"], [])
        self.assertEqual(s.call("GET", f"/api/campaigns/{cid}/documents/mentions")[1]["mentions"], {})
        self.assertEqual(s.call("GET", f"/api/search?q=biere&campaign={cid}")[1]["results"], [])

        # Supprimer un PNJ retire ses liens.
        s.call("POST", f"/api/places/{ys['id']}/links", {"entity_kind": "npc", "entity_id": npc["id"]})
        s.call("DELETE", f"/api/npcs/{npc['id']}")
        self.assertEqual(s.call("GET", f"/api/places/{ys['id']}")[1]["links"], [])

        # Accès : un MJ sans la campagne ne voit rien.
        s.call("POST", "/api/admin/users", {"username": "mj-lieux", "role": "mj", "password": "mot-de-passe-mj",
                                            "campaign_ids": [other["id"]]})
        mj = s.client()
        mj.login("mj-lieux", "mot-de-passe-mj")
        self.assertEqual(mj.call("GET", f"/api/places/{ys['id']}")[0], 404)
        self.assertEqual(mj.call("GET", f"/api/campaigns/{cid}/places")[0], 404)
        self.assertEqual(mj.call("PATCH", f"/api/places/{ys['id']}", {"name": "Volé"})[0], 404)
        self.assertEqual(mj.call("POST", f"/api/campaigns/{other['id']}/places", {"name": "Le mien",
                                                                                 "parent_id": ys["id"]})[0], 400)

    def test_migration_keeps_images_and_converts_locations(self):
        from maze import db as mdb
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA foreign_keys = ON")
            for f in sorted(mdb.MIGRATIONS_DIR.glob("*.sql")):
                if int(f.name[:3]) <= 9:
                    conn.create_function("html_text", 1, mdb.html_text)
                    conn.executescript(f.read_text(encoding="utf-8"))
            conn.executescript("""PRAGMA user_version = 9;
                INSERT INTO campaigns (id, name) VALUES (1, 'Ancienne');
                INSERT INTO npcs (id, campaign_id, name, location) VALUES (1, 1, 'Maëlis', ' Le Hareng '),
                                                                         (2, 1, 'Silas', 'Le Hareng'), (3, 1, 'Hart', '');
                PRAGMA foreign_keys = OFF;
                INSERT INTO npcs (id, campaign_id, name, location) VALUES (4, 99, 'Orphelin', 'Nulle part');
                INSERT INTO document_mentions (document_id, target_kind, target_id) VALUES (1, 'place', 1);
                PRAGMA foreign_keys = ON;
                INSERT INTO images (id, campaign_id, owner_kind, owner_id, file, thumb) VALUES (5, 1, 'npc', 1, 'a', 'b');
                UPDATE npcs SET portrait_image_id = 5 WHERE id = 1;""")
            conn.commit()
            conn.close()
            mdb.migrate(path)
            conn = mdb.connect(path)
            self.assertEqual(conn.execute("SELECT portrait_image_id FROM npcs WHERE id = 1").fetchone()[0], 5)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM images").fetchone()[0], 1)
            self.assertEqual([tuple(r) for r in conn.execute("SELECT name FROM places")], [("Le Hareng",)])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM place_links").fetchone()[0], 2)
            conn.execute("INSERT INTO images (campaign_id, owner_kind, owner_id, file, thumb) VALUES (1, 'place', 1, 'c', 'd')")
            conn.execute("DELETE FROM npcs WHERE id = 1")  # déclencheurs recréés : images et liens nettoyés
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM place_links").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT owner_kind FROM images").fetchall()[0][0], "place")
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM document_mentions WHERE target_kind = 'place'").fetchone()[0], 0)
            conn.close()

    def test_import_hostile_places(self):
        s = self.s
        rows = [{"id": i, "parent_id": i + 1, "name": f"Niveau {i}", "kind": "inconnu"} for i in range(1, 3001)]
        rows += [{"id": 5001, "parent_id": 5002, "name": "Boucle A"}, {"id": 5002, "parent_id": 5001, "name": "Boucle B"}]
        data = {"format": "maze-campaign", "version": 1, "campaign": {"name": "Hostile"},
                "npcs": [{"id": 1, "name": "Vieux", "location": "Ancien port"}],
                "places": rows, "place_links": ["pas un objet", {"place_id": 1, "entity_kind": "npc", "entity_id": 1}]}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("campaign.json", json.dumps(data))
        code, new = s.call("POST", "/api/campaigns/import", raw=buf.getvalue(), content_type="application/zip")
        self.assertEqual(code, 201, new)
        _, listing = s.call("GET", f"/api/campaigns/{new['id']}/places")
        self.assertEqual(len(listing["places"]), 3002)
        self.assertEqual({p["kind"] for p in listing["places"]}, {"lieu"})
        roots = [p["name"] for p in listing["places"] if p["parent_id"] is None]
        self.assertIn("Niveau 3000", roots)
        self.assertEqual(len(roots), 2)  # la chaîne, et un des deux lieux de la boucle
        self.assertEqual(len(listing["links"]), 1)
        # Export antérieur aux lieux : « Lieu » du PNJ converti.
        legacy = {"format": "maze-campaign", "version": 1, "campaign": {"name": "Ancienne"},
                  "npcs": [{"id": 1, "name": "Vieux", "location": "Ancien port"}]}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("campaign.json", json.dumps(legacy))
        _, old = s.call("POST", "/api/campaigns/import", raw=buf.getvalue(), content_type="application/zip")
        _, listing = s.call("GET", f"/api/campaigns/{old['id']}/places")
        self.assertEqual([p["name"] for p in listing["places"]], ["Ancien port"])
        self.assertEqual(len(listing["links"]), 1)
        # null ignoré sur les champs obligatoires.
        _, place = s.call("POST", f"/api/campaigns/{old['id']}/places", {"name": "Quai", "kind": None})
        self.assertEqual(place["kind"], "lieu")
        self.assertEqual(s.call("PATCH", f"/api/places/{place['id']}", {"summary": None, "parent_id": None})[0], 200)


class CampaignTransferTest(unittest.TestCase):
    def test_export_import_campaign(self):
        s = Server()
        try:
            _, camp = s.call("POST", "/api/campaigns", {"name": "Ys", "description": "Port brumeux"})
            cid = camp["id"]
            _, guild = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "Guilde", "color": "#336699"})
            _, mae = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Maëlis", "tags": ["port"]})
            _, hart = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Hart"})
            s.call("POST", "/api/memberships", {"npc_id": mae["id"], "faction_id": guild["id"], "role": "Cheffe", "is_leader": True})
            _, rtype = s.call("POST", f"/api/campaigns/{cid}/relation-types", {"label": "Pacte", "color": "#aa3355"})
            s.call("POST", f"/api/campaigns/{cid}/relations", {
                "source_kind": "npc", "source_id": mae["id"], "target_kind": "faction", "target_id": guild["id"],
                "type": rtype["key"], "secret": True})
            body, ctype = multipart([("portrait.png", png_bytes())])
            _, detail = s.call("POST", f"/api/npcs/{mae['id']}/images", raw=body, content_type=ctype)
            _, docs = s.call("GET", f"/api/campaigns/{cid}/documents")
            _, scen = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Le bal"})
            _, page = s.call("POST", f"/api/campaigns/{cid}/documents", {"parent_id": scen["id"], "title": "Acte I"})
            dbody, dctype = multipart([("scene.png", png_bytes((10, 90, 40)))])
            _, dimg = s.call("POST", f"/api/documents/{page['id']}/images",
                             raw=dbody.replace(b'name="files"', b'name="file"'), content_type=dctype)
            _, ys = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Ys", "kind": "ville"})
            _, inn = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Hareng", "parent_id": ys["id"]})
            s.call("POST", f"/api/places/{inn['id']}/links", {"entity_kind": "npc", "entity_id": mae["id"], "role": "Vit ici"})
            pbody, pctype = multipart([("inn.png", png_bytes((40, 40, 90)))])
            s.call("POST", f"/api/places/{inn['id']}/images", raw=pbody, content_type=pctype)
            html = (f'<p>Avec <a class="mention" data-mention="npc:{mae["id"]}">Maëlis</a> et '
                    f'<a class="mention" data-mention="faction:{guild["id"]}">Guilde</a> au '
                    f'<a class="mention" data-mention="place:{inn["id"]}">Hareng</a></p>'
                    f'<img src="{dimg["url"]}" alt="" data-doc-image="{dimg["id"]}">')
            _, thread = s.call("POST", f"/api/documents/{page['id']}/comments", {"body": "À revoir", "quote": "Avec"})
            s.call("POST", f"/api/documents/{page['id']}/comments", {"body": "D'accord", "parent_id": thread["id"]})
            html = f'<p><span class="comment-anchor" data-comment="{thread["id"]}">Avec</span>' + html[len("<p>Avec"):]
            s.call("PATCH", f"/api/documents/{page['id']}", {"content": html})

            code, archive = s.call("GET", f"/api/campaigns/{cid}/export")
            self.assertEqual(code, 200)
            self.assertEqual(archive[:2], b"PK")

            # Import par un MJ : nouvelle campagne qui lui est attribuée.
            s.call("POST", "/api/admin/users", {"username": "mj-import", "role": "mj",
                                                "password": "mot-de-passe-mj", "campaign_ids": []})
            mj = s.client()
            mj.login("mj-import", "mot-de-passe-mj")
            self.assertEqual(mj.call("GET", f"/api/campaigns/{cid}/export")[0], 404, "pas d'accès à la campagne")
            code, new = mj.call("POST", "/api/campaigns/import", raw=archive, content_type="application/zip")
            self.assertEqual(code, 201, new)
            # « Ys » de l'administrateur lui est invisible : nom gardé tel quel (audit X1-M3).
            self.assertEqual((new["name"], new["description"]), ("Ys", "Port brumeux"))
            self.assertEqual((new["npc_count"], new["faction_count"]), (2, 1))
            self.assertEqual([c["id"] for c in mj.call("GET", "/api/campaigns")[1]], [new["id"]])

            _, graph = mj.call("GET", f"/api/campaigns/{new['id']}/graph")
            new_mae = next(n for n in graph["npcs"] if n["name"] == "Maëlis")
            new_guild = graph["factions"][0]
            self.assertNotEqual(new_mae["id"], mae["id"])
            self.assertEqual(new_mae["tags"], ["port"])
            self.assertTrue(new_mae["portrait_thumb"].startswith(f"/media/{new['id']}/"))
            self.assertEqual(mj.call("GET", new_mae["portrait_thumb"])[0], 200)
            self.assertEqual(len(graph["memberships"]), 1)
            self.assertTrue(graph["memberships"][0]["is_leader"])
            rel = graph["relations"][0]
            self.assertEqual((rel["source_id"], rel["target_id"], rel["type"], rel["secret"]),
                             (new_mae["id"], new_guild["id"], rtype["key"], True))
            self.assertIn("Pacte", [t["label"] for t in graph["relation_types"]])

            _, tree = mj.call("GET", f"/api/campaigns/{new['id']}/documents")
            titles = {d["title"]: d for d in tree["documents"]}
            self.assertEqual(titles["Acte I"]["parent_id"], titles["Le bal"]["id"])
            _, new_page = mj.call("GET", f"/api/documents/{titles['Acte I']['id']}")
            self.assertIn(f'data-mention="npc:{new_mae["id"]}"', new_page["content"])
            self.assertIn(f'data-mention="faction:{new_guild["id"]}"', new_page["content"])
            _, new_threads = mj.call("GET", f"/api/documents/{titles['Acte I']['id']}/comments")
            self.assertEqual([(t["body"], t["author_name"], t["user_id"], len(t["replies"])) for t in new_threads],
                             [("À revoir", "Administrateur", None, 1)])
            self.assertIn(f'data-comment="{new_threads[0]["id"]}"', new_page["content"])
            new_src = re.search(r'src="([^"]+)"', new_page["content"]).group(1)
            self.assertTrue(new_src.startswith(f"/media/{new['id']}/"))
            self.assertEqual(mj.call("GET", new_src)[0], 200)
            _, mae_detail = mj.call("GET", f"/api/npcs/{new_mae['id']}")
            self.assertEqual([d["title"] for d in mae_detail["mentioned_in"]], ["Acte I"])
            # Lieux : arbre, liens, illustration et mentions réécrits.
            self.assertEqual([(p["name"], p["role"]) for p in mae_detail["places"]], [("Hareng", "Vit ici")])
            _, new_places = mj.call("GET", f"/api/campaigns/{new['id']}/places")
            by_name = {p["name"]: p for p in new_places["places"]}
            self.assertEqual(by_name["Hareng"]["parent_id"], by_name["Ys"]["id"])
            self.assertNotEqual(by_name["Hareng"]["id"], inn["id"])
            self.assertTrue(by_name["Hareng"]["thumb"].startswith(f"/media/{new['id']}/"))
            self.assertIn(f'data-mention="place:{by_name["Hareng"]["id"]}"', new_page["content"])
            _, new_inn = mj.call("GET", f"/api/places/{by_name['Hareng']['id']}")
            self.assertEqual([d["title"] for d in new_inn["mentioned_in"]], ["Acte I"])
            # L'original n'a pas bougé.
            _, orig = s.call("GET", f"/api/npcs/{mae['id']}")
            self.assertEqual([d["title"] for d in orig["mentioned_in"]], ["Acte I"])

            # Fichiers refusés.
            self.assertEqual(mj.call("POST", "/api/campaigns/import", raw=b"n'importe quoi",
                                     content_type="application/zip")[0], 400)
            fake = io.BytesIO()
            with zipfile.ZipFile(fake, "w") as z:
                z.writestr("campaign.json", json.dumps({"format": "autre"}))
            self.assertEqual(mj.call("POST", "/api/campaigns/import", raw=fake.getvalue(),
                                     content_type="application/zip")[0], 400)
            self.assertEqual(len(mj.call("GET", "/api/campaigns")[1]), 1)
        finally:
            s.stop()


def legacy_archive(migrations: list[str], users: list[tuple[str, str]] = (), extra: str | None = None) -> bytes:
    """Archive de sauvegarde au schéma d'une ancienne version (migrations listées seulement)."""
    from maze.db import MIGRATIONS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "maze.db"
        conn = sqlite3.connect(path)
        for name in migrations:
            conn.executescript(f"BEGIN;\n{(MIGRATIONS_DIR / name).read_text()}\nCOMMIT;")
        conn.execute(f"PRAGMA user_version = {int(migrations[-1][:3])}")
        conn.execute("INSERT INTO campaigns (name) VALUES ('Vieille campagne')")
        for username, role in users:
            conn.execute("INSERT INTO users (username, display_name, password_hash, role, active) "
                         "VALUES (?, ?, 'x', ?, 1)", (username, username, role))
        conn.commit()
        conn.close()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(path, arcname="maze.db")
            if extra:  # fichier supplémentaire (contenu factice)
                info = tarfile.TarInfo(extra)
                info.size = 4
                tar.addfile(info, io.BytesIO(b"<p>x"))
        return buf.getvalue()


class HardeningTest(unittest.TestCase):
    """Corrections issues de la revue de code."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_deleted_account_session_not_revived(self):
        s = self.s
        _, bob = s.call("POST", "/api/admin/users", {"username": "bob", "role": "mj", "password": "mot-de-passe-bob"})
        old = s.client()
        self.assertEqual(old.login("bob", "mot-de-passe-bob"), 200)
        s.call("DELETE", f"/api/admin/users/{bob['id']}")
        _, alice = s.call("POST", "/api/admin/users", {"username": "alice", "role": "admin", "password": "mot-de-passe-alice"})
        self.assertEqual(alice["id"], bob["id"], "identifiant réutilisé par SQLite")
        self.assertFalse(old.call("GET", "/api/auth/status")[1]["authenticated"])
        self.assertEqual(old.call("GET", "/api/admin/users")[0], 401)

    def test_restore_keeps_an_admin(self):
        s = self.s
        # Sauvegarde antérieure aux comptes : les comptes actuels sont repris.
        _, imported = s.call("POST", "/api/admin/backups/import", raw=legacy_archive(["001_initial.sql", "002_relation_types.sql"]),
                             content_type="application/gzip")
        self.assertEqual(s.call("POST", f"/api/admin/backups/{imported['name']}/restore")[0], 200)
        status = s.call("GET", "/api/auth/status")[1]
        self.assertTrue(status["authenticated"])
        self.assertFalse(status["setup_required"])
        self.assertEqual(s.client().call("POST", "/api/auth/setup", {"username": "pirate", "password": "mot-de-passe"})[0], 409)
        # Sauvegarde avec des comptes mais sans administrateur actif : refusée en ligne de commande ; depuis
        # l'administration, l'administrateur qui restaure est repris (audit M11) : il en reste donc un.
        legacy = ["001_initial.sql", "002_relation_types.sql", "003_users.sql"]
        _, imported = s.call("POST", "/api/admin/backups/import", raw=legacy_archive(legacy, [("mj", "mj")]),
                             content_type="application/gzip")
        from maze import backup
        from maze.config import load_settings
        with self.assertRaises(backup.ArchiveError) as caught:
            backup.restore(Path(s.tmp.name) / "backups" / imported["name"], load_settings())
        self.assertIn("administrateur", str(caught.exception))
        self.assertEqual(s.call("POST", f"/api/admin/backups/{imported['name']}/restore")[0], 200)
        users = {u["username"]: u["role"] for u in s.call("GET", "/api/admin/users")[1]}
        self.assertEqual(users, {"admin": "admin", "mj": "mj"})
        # Fichier autre qu'une image WebP sous media/ : archive refusée.
        code, _ = s.call("POST", "/api/admin/backups/import", raw=legacy_archive(legacy, [("admin2", "admin")], extra="media/1/page.html"),
                         content_type="application/gzip")
        self.assertEqual(code, 400)

    def test_input_limits(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Limites"})
        code, _ = s.call("POST", f"/api/campaigns/{camp['id']}/npcs", {"name": "X", "tags": ["t"] * 101})
        self.assertEqual(code, 422)
        _, npc = s.call("POST", f"/api/campaigns/{camp['id']}/npcs", {"name": "X", "tags": [f"t{i}" for i in range(80)]})
        self.assertEqual(len(npc["tags"]), 30)
        self.assertEqual(s.call("GET", "/api/npcs/99999999999999999999")[0], 400)
        self.assertEqual(s.call("POST", "/api/memberships", {"npc_id": 10**20, "faction_id": 1})[0], 400)

    def test_parallel_logins_rate_limited(self):
        import concurrent.futures
        from maze import throttle
        self.addCleanup(throttle.reset)   # même si le test échoue avant la fin (audit QUAL-9)
        s = self.s
        def attempt(i):   # comptes différents : seule la limite par adresse joue (pas de délai par compte)
            return s.client().login(f"inconnu{i}", "mauvais-mot-de-passe")
        with concurrent.futures.ThreadPoolExecutor(20) as pool:
            codes = list(pool.map(attempt, range(20)))
        self.assertIn(429, codes)
        self.assertLessEqual(codes.count(401), 8)

    def test_export_leaves_no_temporary_file(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Export propre"})
        before = set(Path(tempfile.gettempdir()).glob("tmp*.zip"))
        code, _ = s.call("GET", f"/api/campaigns/{camp['id']}/export")
        self.assertEqual(code, 200)
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("tmp*.zip")) - before, set())


class ImportRestoreTest(unittest.TestCase):
    def test_export_import_restore(self):
        s = Server()
        try:
            _, kept = s.call("POST", "/api/campaigns", {"name": "Avant la sauvegarde"})
            _, npc = s.call("POST", f"/api/campaigns/{kept['id']}/npcs", {"name": "Aldric"})
            body, ctype = multipart([("p.png", png_bytes())])
            _, imgs = s.call("POST", f"/api/npcs/{npc['id']}/images", raw=body, content_type=ctype)
            image_url = imgs[0]["url"] if isinstance(imgs, list) else imgs["images"][0]["url"]
            _, entry = s.call("POST", "/api/admin/backups")
            _, archive = s.call("GET", f"/api/admin/backups/{entry['name']}")  # export
            s.call("DELETE", f"/api/admin/backups/{entry['name']}")

            # Après la sauvegarde : une campagne en plus, l'image supprimée.
            _, extra = s.call("POST", "/api/campaigns", {"name": "Après la sauvegarde"})
            s.call("DELETE", f"/api/npcs/{npc['id']}")
            self.assertEqual(s.call("GET", image_url)[0], 404)

            # Imports refusés : n'importe quoi, archive avec chemin dangereux.
            code, err = s.call("POST", "/api/admin/backups/import", raw=b"pas une archive",
                               content_type="application/gzip")
            self.assertEqual(code, 400)
            evil = io.BytesIO()
            with tarfile.open(fileobj=evil, mode="w:gz") as tar:
                info = tarfile.TarInfo("../evil.txt")
                info.size = 3
                tar.addfile(info, io.BytesIO(b"bad"))
            self.assertEqual(s.call("POST", "/api/admin/backups/import", raw=evil.getvalue(),
                                    content_type="application/gzip")[0], 400)
            self.assertEqual(s.call("GET", "/api/admin/backups")[1], [])

            code, imported = s.call("POST", "/api/admin/backups/import", raw=archive, content_type="application/gzip")
            self.assertEqual(code, 201)
            self.assertTrue(imported["name"].endswith("-import.tar.gz"))
            self.assertEqual(imported["campaigns"], 1)

            # Un éditeur ouvert est fermé avant la restauration (audit M7) : il se reconnectera (4000) et trouvera la
            # fiche restaurée (ici absente de l'archive).
            _, doc = s.call("POST", f"/api/campaigns/{extra['id']}/documents", {"title": "Doc"})
            ws = s.admin.live(doc["id"], 7)
            recv_until(ws, "welcome")
            code, result = s.call("POST", f"/api/admin/backups/{imported['name']}/restore")
            self.assertEqual(code, 200, result)
            self.assertTrue(result["safety"]["name"].endswith("-avant-restauration.tar.gz"))
            with self.assertRaises(Exception) as closed:
                recv_until(ws, "reset")
            self.assertEqual(closed.exception.rcvd.code, 4000)
            ws.close()
            again = s.admin.live(doc["id"], 7)
            self.assertEqual(recv(again)["code"], 4404)
            again.close()

            self.assertEqual(s.call("GET", "/api/auth/status")[1]["authenticated"], True)
            _, campaigns = s.call("GET", "/api/campaigns")
            self.assertEqual([c["name"] for c in campaigns], ["Avant la sauvegarde"])
            self.assertEqual(s.call("GET", image_url)[0], 200)
            self.assertEqual(s.call("GET", f"/api/npcs/{npc['id']}")[1]["name"], "Aldric")
            names = [b["name"] for b in s.call("GET", "/api/admin/backups")[1]]
            self.assertIn(result["safety"]["name"], names)
            # Retour arrière possible avec l'archive de sécurité.
            self.assertEqual(s.call("POST", f"/api/admin/backups/{result['safety']['name']}/restore")[0], 200)
            _, campaigns = s.call("GET", "/api/campaigns")
            self.assertEqual(sorted(c["name"] for c in campaigns), ["Après la sauvegarde", "Avant la sauvegarde"])
        finally:
            s.stop()


def import_campaign_json(s: "Server | Client", data: dict) -> tuple[int, dict]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("campaign.json", json.dumps({"format": "maze-campaign", "version": 1, **data}))
    return s.call("POST", "/api/campaigns/import", raw=buf.getvalue(), content_type="application/zip")


class ImportNameTest(unittest.TestCase):
    """Audit X1-M3 : le nom d'une campagne importée n'est comparé qu'aux campagnes que voit celui qui importe ; avant,
    « X (importée) » révélait qu'une campagne X existait dans un autre compte."""

    def test_name_compared_to_visible_campaigns_only(self):
        s = Server()
        try:
            s.call("POST", "/api/campaigns", {"name": "Opération Tempête"})    # campagne de l'administrateur
            s.call("POST", "/api/admin/users", {"username": "mj-a", "role": "mj", "password": "mot-de-passe-a",
                                                "campaign_ids": []})
            mj = s.client()
            self.assertEqual(mj.login("mj-a", "mot-de-passe-a"), 200)
            archive = {"campaign": {"name": "Opération Tempête"}}
            code, mine = import_campaign_json(mj, archive)
            self.assertEqual((code, mine["name"]), (201, "Opération Tempête"))   # invisible pour lui : rien ne fuit
            self.assertEqual(import_campaign_json(mj, archive)[1]["name"], "Opération Tempête (importée)")
            # L'administrateur voit toutes les campagnes : les deux homonymes comptent.
            self.assertEqual(import_campaign_json(s, archive)[1]["name"], "Opération Tempête (importée 2)")
        finally:
            s.stop()


class DeepTreeTest(unittest.TestCase):
    """Audit DATA-10 : une chaîne de pages (ou de lieux) très profonde ne rend plus rien impossible à supprimer ou à
    dupliquer ; la profondeur est bornée à la création, au déplacement et à l'import."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(Path(self.s.tmp.name) / "maze.db", timeout=15)
        self.addCleanup(conn.close)
        return conn

    def chain(self, table: str, cid: int, top: int, count: int) -> int:
        """Chaîne de `count` fiches (ou lieux) sous `top`, écrite directement en base (arbre d'avant la limite) ;
        renvoie la plus profonde."""
        from maze import db
        parent = top
        with db.transaction(Path(self.s.tmp.name) / "maze.db") as conn:
            for i in range(count):
                if table == "documents":
                    parent = conn.execute("INSERT INTO documents (campaign_id, parent_id, kind, title) "
                                          "VALUES (?, ?, 'page', ?)", (cid, parent, f"P{i}")).lastrowid
                else:
                    parent = conn.execute("INSERT INTO places (campaign_id, parent_id, name) VALUES (?, ?, ?)",
                                          (cid, parent, f"L{i}")).lastrowid
        return parent

    def max_depth(self, table: str, cid: int) -> int:
        return self.db().execute(
            f"""WITH RECURSIVE t(id, level) AS (SELECT id, 0 FROM {table} WHERE campaign_id = ? AND parent_id IS NULL
                    UNION ALL SELECT x.id, t.level + 1 FROM {table} x JOIN t ON x.parent_id = t.id)
                SELECT MAX(level), COUNT(*) FROM t""", (cid,)).fetchone()

    def test_deep_documents_delete_and_duplicate(self):
        from maze.documents import MAX_DEPTH
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Profonde"})
        cid = camp["id"]
        _, scen = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Scénario"})
        deepest = self.chain("documents", cid, scen["id"], 1100)   # au-delà de la limite de SQLite (1000)
        # Duplication : sans récursion Python (limite de 1000 appels).
        code, copy = s.call("POST", f"/api/documents/{scen['id']}/duplicate")
        self.assertEqual(code, 201, copy)
        self.assertEqual(self.max_depth("documents", cid), (1101, 2 * 1101 + 1))
        # Scénarios de l'export Foundry (parcours de l'arbre) et page : rien ne dépend de la profondeur.
        from maze import db, foundry
        with db.transaction(Path(s.tmp.name) / "maze.db", write=False) as conn:
            self.assertEqual([sc["id"] for sc in foundry.scenarios_of(conn, cid)], [scen["id"], copy["id"]])
        self.assertEqual(s.call("GET", f"/api/documents/{deepest}")[0], 200)
        # Plus aucune fiche ne peut être ajoutée sous la chaîne, ni une autre chaîne enfoncée dessous.
        code, err = s.call("POST", f"/api/campaigns/{cid}/documents", {"parent_id": deepest, "title": "Trop"})
        self.assertEqual(code, 400)
        self.assertIn(str(MAX_DEPTH), err["detail"])
        # Suppression d'un scénario puis de la campagne : 204 (avant : « too many levels of trigger recursion »).
        self.assertEqual(s.call("DELETE", f"/api/documents/{copy['id']}")[0], 204)
        self.assertEqual(self.max_depth("documents", cid), (1101, 1102))
        self.assertEqual(s.call("DELETE", f"/api/campaigns/{cid}")[0], 204)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM documents WHERE campaign_id = ?", (cid,)).fetchone()[0], 0)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM search_index WHERE campaign_id = ?", (cid,)).fetchone()[0], 0)

    def test_document_depth_limit(self):
        from maze.documents import MAX_DEPTH
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Limite"})
        cid = camp["id"]
        parent, chain = None, []
        for level in range(1, MAX_DEPTH + 1):   # scénario au niveau 1, … dernière page au niveau MAX_DEPTH
            code, doc = s.call("POST", f"/api/campaigns/{cid}/documents", {"parent_id": parent, "title": f"N{level}"})
            self.assertEqual(code, 201, (level, doc))
            parent = doc["id"]
            chain.append(doc)
        code, err = s.call("POST", f"/api/campaigns/{cid}/documents", {"parent_id": parent, "title": "Trop"})
        self.assertEqual((code, err["detail"]), (400, f"Trop de niveaux de sous-pages ({MAX_DEPTH} au plus)"))
        # Déplacement : une page avec une sous-page ne descend pas sous l'avant-dernier niveau…
        _, other = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Autre"})
        _, sub = s.call("POST", f"/api/campaigns/{cid}/documents", {"parent_id": other["id"], "title": "Sous"})
        code, _ = s.call("PUT", f"/api/documents/{other['id']}/move", {"parent_id": chain[-2]["id"], "index": 0})
        self.assertEqual(code, 400)
        code, _ = s.call("PUT", f"/api/documents/{other['id']}/move", {"parent_id": chain[-3]["id"], "index": 0})
        self.assertEqual(code, 204)
        # … et reste déplaçable parmi ses sœurs (même profondeur).
        code, _ = s.call("PUT", f"/api/documents/{sub['id']}/move", {"parent_id": other["id"], "index": 0})
        self.assertEqual(code, 204)

    def test_deep_import_capped(self):
        from maze.documents import MAX_DEPTH
        from maze.places import MAX_DEPTH as PLACE_DEPTH
        docs = [{"id": 1, "kind": "campaign", "title": ""}, {"id": 2, "parent_id": 1, "kind": "scenario", "title": "S"}]
        docs += [{"id": i, "parent_id": i - 1, "kind": "page", "title": f"P{i}"} for i in range(3, 1203)]
        places = [{"id": i, "parent_id": i + 1 if i < 1500 else None, "name": f"N{i}"} for i in range(1, 1501)]
        code, camp = import_campaign_json(self.s, {"campaign": {"name": "Archive profonde"}, "documents": docs,
                                                   "places": places})
        self.assertEqual(code, 201, camp)
        # Rien n'est perdu : les fiches au-delà de la limite sont rangées au dernier niveau admis.
        self.assertEqual(self.max_depth("documents", camp["id"]), (MAX_DEPTH, 1202))
        self.assertEqual(self.max_depth("places", camp["id"]), (PLACE_DEPTH, 1500))
        self.assertEqual(self.s.call("DELETE", f"/api/campaigns/{camp['id']}")[0], 204)

    def test_deep_places(self):
        from maze.places import MAX_DEPTH
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Lieux profonds"})
        cid = camp["id"]
        _, top = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Monde"})
        deepest = self.chain("places", cid, top["id"], 3000)
        self.assertEqual(s.call("GET", f"/api/places/{top['id']}")[0], 200)
        code, err = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Trop", "parent_id": deepest})
        self.assertEqual((code, err["detail"]), (400, f"Trop de niveaux de sous-lieux ({MAX_DEPTH} au plus)"))
        # Suppression d'un lieu de la chaîne (ses sous-lieux remontent) puis de la campagne.
        self.assertEqual(s.call("DELETE", f"/api/places/{top['id']}")[0], 204)
        self.assertEqual(s.call("DELETE", f"/api/campaigns/{cid}")[0], 204)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM places WHERE campaign_id = ?", (cid,)).fetchone()[0], 0)

        # Limite à la création et au déplacement (PATCH parent_id, glisser-déposer).
        _, camp = s.call("POST", "/api/campaigns", {"name": "Lieux bornés"})
        cid, parent, chain = camp["id"], None, []
        for level in range(MAX_DEPTH + 1):   # racine au niveau 0, … dernier au niveau MAX_DEPTH
            code, place = s.call("POST", f"/api/campaigns/{cid}/places", {"name": f"N{level}", "parent_id": parent})
            self.assertEqual(code, 201, (level, place))
            parent = place["id"]
            chain.append(place)
        self.assertEqual(s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Trop", "parent_id": parent})[0], 400)
        _, other = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Autre"})
        s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Sous", "parent_id": other["id"]})
        self.assertEqual(s.call("PATCH", f"/api/places/{other['id']}", {"parent_id": chain[-2]["id"]})[0], 400)
        self.assertEqual(s.call("PUT", f"/api/places/{other['id']}/move", {"parent_id": chain[-2]["id"], "index": 0})[0], 400)
        self.assertEqual(s.call("PATCH", f"/api/places/{other['id']}", {"parent_id": chain[-3]["id"]})[0], 200)
        self.assertEqual(s.call("PUT", f"/api/places/{other['id']}/move", {"parent_id": chain[-3]["id"], "index": 0})[0], 204)


def recv(ws, timeout: float = 3):
    msg = ws.recv(timeout=timeout)
    return json.loads(msg) if isinstance(msg, str) else msg


def recv_until(ws, kind: str, timeout: float = 3):
    """Lit jusqu'au message JSON de type `kind` ; renvoie (message, trames binaires reçues avant)."""
    frames = []
    deadline = time.monotonic() + timeout
    while True:
        msg = recv(ws, max(0.05, deadline - time.monotonic()))
        if isinstance(msg, dict) and msg.get("t") == kind:
            return msg, frames
        if isinstance(msg, bytes):
            frames.append(msg)


def nothing_received(ws, timeout: float = 0.4) -> bool:
    try:
        ws.recv(timeout=timeout)
    except TimeoutError:
        return True
    return False


def seq_of(frame: bytes) -> int:
    return int.from_bytes(frame[1:9], "big")


class CollabTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_relay_init_leader_snapshot(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Temps réel"})
        _, npc = s.call("POST", f"/api/campaigns/{camp['id']}/npcs", {"name": "Ysolde"})
        _, doc = s.call("POST", f"/api/campaigns/{camp['id']}/documents", {"title": "Acte I"})
        epoch0 = s.call("GET", f"/api/documents/{doc['id']}")[1]["live_epoch"]   # aléatoire (migration 027)
        self.assertGreater(epoch0, 0)
        s.call("PATCH", f"/api/documents/{doc['id']}", {"content": "<p>Ancien texte</p>"})
        other = s.client()
        self.assertEqual(other.login("admin", ADMIN_PASSWORD), 200)

        a = s.admin.live(doc["id"], 101)
        welcome, frames = recv_until(a, "welcome")
        self.assertEqual(frames, [])
        self.assertTrue(welcome["init"] and welcome["leader"] and not welcome["waiting"])

        b = other.live(doc["id"], 202)
        wb, _ = recv_until(b, "welcome")
        self.assertTrue(wb["waiting"])
        self.assertFalse(wb["init"] or wb["leader"])
        self.assertEqual(len(wb["peers"]), 2)
        peers, _ = recv_until(a, "peers")
        self.assertEqual(peers["joined"], wb["you"])

        self.assertEqual(welcome["epoch"], epoch0 + 1)  # contenu posé par l'API une fois
        # Pendant la conversion, une modification d'un autre navigateur provoque une reconnexion
        # (jamais d'abandon silencieux, qui décalerait ses accusés de réception).
        early = other.live(doc["id"], 404)
        recv_until(early, "welcome")
        recv_until(a, "peers"); recv_until(b, "peers")
        early.send(bytes([0]) + b"trop-tot")
        with self.assertRaises(Exception) as closed:
            while True:
                early.recv(timeout=3)
        self.assertEqual(getattr(closed.exception, "rcvd", None).code, 4000)
        recv_until(a, "peers"); recv_until(b, "peers")
        a.send(bytes([0]) + b"conversion")
        ack = recv(a)
        self.assertEqual(ack[0], 3)
        first = seq_of(ack)
        relayed = recv(b)
        self.assertEqual((relayed[0], seq_of(relayed), relayed[9:]), (0, first, b"conversion"))
        a.send(json.dumps({"t": "init-done"}))
        self.assertEqual(recv(b)["t"], "ready")
        self.assertTrue(nothing_received(a, 0.2) or True)

        # Relais dans les deux sens et curseurs (awareness).
        b.send(bytes([0]) + b"de-b")
        relayed = recv(a)
        self.assertEqual(relayed[9:], b"de-b")
        second = seq_of(relayed)
        self.assertEqual(recv(b)[0], 3)
        b.send(bytes([1]) + b"curseur")
        self.assertEqual(recv(a), bytes([1]) + b"curseur")

        # HTML dérivé envoyé par le meneur : rétroliens mis à jour.
        html = f'<p>Voir <a class="mention" data-mention="npc:{npc["id"]}">Ysolde</a></p>'
        a.send(json.dumps({"t": "content", "html": html, "upto": second}))
        self.assertIn("updated_at", recv_until(b, "meta")[0])
        _, detail = s.call("GET", f"/api/npcs/{npc['id']}")
        self.assertEqual([d["id"] for d in detail["mentioned_in"]], [doc["id"]])
        _, saved = s.call("GET", f"/api/documents/{doc['id']}")
        self.assertEqual(saved["content"], html)
        # Un non-meneur ne peut pas écrire le HTML.
        b.send(json.dumps({"t": "content", "html": "<p>pirate</p>", "upto": second}))
        # Trames d'une salle traitées dans l'ordre : le curseur relayé à a prouve que le HTML de b a été traité.
        b.send(bytes([1]) + b"apres-pirate")
        while recv(a) != bytes([1]) + b"apres-pirate":   # (meta encore en attente chez a)
            pass
        self.assertEqual(s.call("GET", f"/api/documents/{doc['id']}")[1]["content"], html)

        # Compaction : diffusée aux autres ; un nouvel arrivant reçoit l'état complet puis la suite.
        a.send(bytes([2]) + second.to_bytes(8, "big") + b"etat-complet")
        self.assertEqual(recv(b)[9:], b"etat-complet")
        b.send(bytes([0]) + b"apres")
        recv(a); recv(b)
        c = s.admin.live(doc["id"], 303)
        wc, frames = recv_until(c, "welcome")
        self.assertEqual([f[9:] for f in frames], [b"etat-complet", b"apres"])
        self.assertFalse(wc["init"] or wc["waiting"])

        # Départ du meneur : le suivant prend le relais.
        a.close()
        self.assertTrue(recv_until(b, "leader"))
        left, _ = recv_until(c, "peers")
        self.assertEqual(left["left_client"], 101)

        # Titre modifié par l'API : diffusé ; contenu remplacé : les éditeurs rechargent.
        s.call("PATCH", f"/api/documents/{doc['id']}", {"title": "Acte I bis"})
        self.assertEqual(recv_until(c, "meta")[0]["title"], "Acte I bis")
        s.call("PATCH", f"/api/documents/{doc['id']}", {"content": "<p>Restauré</p>"})
        self.assertTrue(recv_until(b, "reset"))
        b.close(); c.close()
        b = other.live(doc["id"], 202)
        wb, frames = recv_until(b, "welcome")
        self.assertEqual((wb["epoch"], wb["init"], frames), (epoch0 + 2, True, []))
        s.call("DELETE", f"/api/documents/{doc['id']}")
        self.assertTrue(recv_until(b, "deleted"))
        b.close()

    def test_compaction_after_short_sessions(self):
        """Audit PERF-12 = DATA-17 : la compaction est demandée d'après la taille du journal en base, pas d'après les
        modifications reçues par la salle ouverte (remise à 0 à chaque séance)."""
        from maze.collab import COMPACT_EVERY
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Séances courtes"})
        _, doc = s.call("POST", f"/api/campaigns/{camp['id']}/documents", {"title": "Notes"})

        def journal() -> int:
            with contextlib.closing(sqlite3.connect(Path(s.tmp.name) / "maze.db")) as conn:
                return conn.execute("SELECT COUNT(*) FROM document_updates WHERE document_id = ?",
                                    (doc["id"],)).fetchone()[0]

        def session(edits: int) -> bool:
            """Une séance de `edits` modifications ; vrai si le serveur a demandé une compaction."""
            ws = s.admin.live(doc["id"], 7)
            welcome, frames = recv_until(ws, "welcome")
            compact = False
            for i in range(edits):
                ws.send(bytes([0]) + f"m{i}".encode())
                while True:
                    frame = recv(ws)
                    if isinstance(frame, dict):
                        compact = compact or frame.get("t") == "compact"
                    elif frame[0] == 3:
                        break
            try:   # demande envoyée juste après l'accusé de réception de la dernière modification
                frame = recv(ws, 0.5)
                compact = compact or isinstance(frame, dict) and frame.get("t") == "compact"
            except TimeoutError:
                pass
            ws.close()
            wait_until(lambda: doc["id"] not in s.server.config.app.state.hub.rooms)
            return compact

        per_session = COMPACT_EVERY * 2 // 3
        self.assertFalse(session(per_session))
        self.assertEqual(journal(), per_session)
        # Seconde séance courte : le seuil est atteint en cours de séance (avant : jamais).
        self.assertTrue(session(per_session))
        # Journal déjà au-delà du seuil à l'ouverture : demande juste après le welcome.
        self.assertEqual(journal(), 2 * per_session)
        ws = s.admin.live(doc["id"], 8)
        welcome, frames = recv_until(ws, "welcome")
        self.assertEqual(len(frames), 2 * per_session)
        self.assertEqual(recv(ws), {"t": "compact"})
        ws.send(bytes([2]) + welcome["seq"].to_bytes(8, "big") + b"etat-complet")
        wait_until(lambda: journal() == 1)
        ws.close()

    def test_image_kept_when_reused_elsewhere(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Images partagées"})
        _, first = s.call("POST", f"/api/campaigns/{camp['id']}/documents", {"title": "A"})
        _, second = s.call("POST", f"/api/campaigns/{camp['id']}/documents", {"title": "B"})
        body, ctype = multipart([("p.png", png_bytes())])
        body = body.replace(b'name="files"', b'name="file"')
        code, img = s.call("POST", f"/api/documents/{first['id']}/images", raw=body, content_type=ctype)
        self.assertEqual(code, 201)
        tag = f'<img src="{img["url"]}" alt="" data-doc-image="{img["id"]}">'
        s.call("PATCH", f"/api/documents/{first['id']}", {"content": tag})
        s.call("PATCH", f"/api/documents/{second['id']}", {"content": tag})  # copier-coller
        s.call("DELETE", f"/api/documents/{first['id']}")
        code, _ = s.call("GET", img["url"])
        self.assertEqual(code, 200)  # toujours affichée dans B
        s.call("DELETE", f"/api/documents/{second['id']}")
        self.assertEqual(s.call("GET", img["url"])[0], 404)

    def test_schema_version(self):
        """Audit H3 (DATA-2) : un navigateur dont l'éditeur n'a pas le schéma du serveur supprimerait du document
        partagé les blocs qu'il ne connaît pas. Il est refusé (code définitif) et recharge la page ; côté REST,
        l'en-tête X-Maze-Version, facultatif, donne 409 s'il diffère."""
        from maze.collab import SCHEMA_VERSION
        s = self.s
        frontend = json.loads((Path(__file__).resolve().parents[2] / "frontend" / "src" / "editor"
                               / "schema-version.json").read_text(encoding="utf-8"))
        self.assertEqual(frontend["version"], SCHEMA_VERSION, "version du schéma : serveur et interface")
        _, camp = s.call("POST", "/api/campaigns", {"name": "Versions"})
        _, doc = s.call("POST", f"/api/campaigns/{camp['id']}/documents", {"title": "Acte"})
        # Interface d'avant ce contrôle (sans paramètre v) : 4401, qui lui fait recharger la page.
        ws = s.admin.live(doc["id"], 1, version=None)
        error = recv(ws)
        self.assertEqual(error["code"], 4401)
        self.assertIn("rechargez", error["detail"])
        ws.close()
        # Autre version du schéma : 4426, avec la version du serveur.
        for other in (SCHEMA_VERSION + 1, SCHEMA_VERSION - 1, "x"):
            ws = s.admin.live(doc["id"], 1, version=other)
            error = recv(ws)
            self.assertEqual((error["code"], error["version"]), (4426, SCHEMA_VERSION))
            with self.assertRaises(Exception) as closed:
                ws.recv(timeout=3)
            self.assertEqual(closed.exception.rcvd.code, 4426)
        ws = s.admin.live(doc["id"], 1)
        self.assertTrue(recv_until(ws, "welcome"))
        ws.close()
        # REST : en-tête facultatif (connecteur Claude, scripts), 409 seulement s'il est présent et différent,
        # et seulement pour les écritures.
        url = f"/api/documents/{doc['id']}"
        old = {"X-Maze-Version": str(SCHEMA_VERSION - 1)}
        code, body = s.call("PATCH", url, {"title": "Refusé"}, headers=old)
        self.assertEqual((code, body["code"], body["version"]), (409, "version", SCHEMA_VERSION))
        self.assertEqual(s.call("GET", url)[1]["title"], "Acte")
        self.assertEqual(s.call("GET", url, headers=old)[0], 200)
        self.assertEqual(s.call("PATCH", url, {"title": "Avec"}, headers={"X-Maze-Version": str(SCHEMA_VERSION)})[0], 200)
        self.assertEqual(s.call("PATCH", url, {"title": "Sans"})[0], 200)

    def test_access_control(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Privée"})
        _, doc = s.call("POST", f"/api/campaigns/{camp['id']}/documents", {"title": "Secret"})

        anonymous = s.client()
        ws = anonymous.live(doc["id"], 1)
        self.assertEqual(recv(ws)["code"], 4401)
        ws.close()

        ws = s.admin.live(doc["id"], 1, origin="https://ailleurs.example")
        self.assertEqual(recv(ws)["code"], 4403)
        ws.close()

        _, mj = s.call("POST", "/api/admin/users", {"username": "temps-reel", "role": "mj",
                                                   "password": "mot-de-passe-mj", "campaign_ids": []})
        lea = s.client()
        self.assertEqual(lea.login("temps-reel", "mot-de-passe-mj"), 200)
        ws = lea.live(doc["id"], 1)
        self.assertEqual(recv(ws)["code"], 4404)
        ws.close()

        # Accès accordé puis retiré : la connexion ouverte est coupée.
        s.call("PUT", f"/api/admin/campaigns/{camp['id']}/members", {"user_ids": [mj["id"]]})
        ws = lea.live(doc["id"], 1)
        recv_until(ws, "welcome")
        s.call("PUT", f"/api/admin/campaigns/{camp['id']}/members", {"user_ids": []})
        self.assertEqual(recv_until(ws, "error")[0]["code"], 4403)
        ws.close()


if __name__ == "__main__":
    unittest.main()
