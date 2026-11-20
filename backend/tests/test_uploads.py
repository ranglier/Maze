"""Dépôt d'image demandé par Claude (migration 035, uploads.py, outil request_upload de mcp_upload.py).

Lancer : cd backend && python -m unittest -q tests.test_uploads
"""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import time
import unittest
import urllib.parse
import uuid
from pathlib import Path

from tests.test_api import ADMIN_PASSWORD, Server, png_bytes
from tests.test_connector import CALLBACK, pkce, raw_call


def form(content: bytes, name: str = "image.png", caption: str | None = None) -> tuple[bytes, str]:
    """Corps multipart d'un dépôt : champ file, et caption si donnée."""
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    if caption is not None:
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n'.encode())
        out.write(caption.encode() + b"\r\n")
    out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode())
    out.write(b"Content-Type: application/octet-stream\r\n\r\n" + content + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


class UploadTicketsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = s = Server()
        _, cls.camp = s.call("POST", "/api/campaigns", {"name": "Dépôts"})
        _, cls.read_camp = s.call("POST", "/api/campaigns", {"name": "Lecture seule"})
        _, cls.closed_camp = s.call("POST", "/api/campaigns", {"name": "Fermée"})
        cid = cls.camp["id"]
        _, cls.npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Ysolde"})
        _, cls.faction = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "La Vigie"})
        _, cls.place = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Valdis"})
        code, cls.doc = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Le bal masqué"})
        assert code == 201, cls.doc
        _, cls.other_npc = s.call("POST", f"/api/campaigns/{cls.read_camp['id']}/npcs", {"name": "Ailleurs"})
        code, out = s.call("POST", "/api/admin/claude/credentials", {})
        assert code == 201, out
        cls.client_id, cls.secret = out["client_id"], out["client_secret"]
        code, _ = s.call("PUT", "/api/admin/claude", {"enabled": True, "grants": [
            {"campaign_id": cid, "mode": "write"}, {"campaign_id": cls.read_camp["id"], "mode": "read"}]})
        assert code == 200
        cls.token = cls.connect()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    # --- Aides ------------------------------------------------------------------------------

    @classmethod
    def connect(cls) -> str:
        verifier, challenge = pkce()
        fields = {"client_id": cls.client_id, "redirect_uri": CALLBACK, "state": "xyz", "code_challenge": challenge,
                  "code_challenge_method": "S256", "scope": "maze", "resource": f"{cls.s.base}/mcp",
                  "username": "admin", "password": ADMIN_PASSWORD, "action": "allow"}
        code, headers, _ = raw_call(cls.s.base, "POST", "/oauth/authorize", urllib.parse.urlencode(fields).encode(),
                                    {"Content-Type": "application/x-www-form-urlencoded"})
        assert code == 303, code
        grant = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)["code"][0]
        code, _, body = raw_call(cls.s.base, "POST", "/oauth/token", urllib.parse.urlencode({
            "grant_type": "authorization_code", "code": grant, "redirect_uri": CALLBACK, "code_verifier": verifier,
            "client_id": cls.client_id, "client_secret": cls.secret}).encode(),
            {"Content-Type": "application/x-www-form-urlencoded"})
        assert code == 200, body
        return json.loads(body)["access_token"]

    def tool(self, **arguments):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "request_upload", "arguments": arguments}}
        code, _, raw = raw_call(self.s.base, "POST", "/mcp", json.dumps(body).encode(), {
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"})
        self.assertEqual(code, 200, raw)
        result = json.loads(raw)["result"]
        text = result["content"][0]["text"]
        return (False, text) if result["isError"] else (True, json.loads(text))

    def ticket(self, **arguments) -> tuple[dict, str]:
        ok, out = self.tool(**arguments)
        self.assertTrue(ok, out)
        prefix = f"{self.s.base}/#/depot/"
        self.assertTrue(out["url"].startswith(prefix), out["url"])
        return out, out["url"][len(prefix):]

    def deposit(self, client, token: str, content: bytes | None = None, caption: str | None = None):
        body, ctype = form(png_bytes() if content is None else content, caption=caption)
        return client.call("POST", f"/api/uploads/{token}", raw=body, content_type=ctype)

    @contextlib.contextmanager
    def db(self):
        conn = sqlite3.connect(Path(self.s.tmp.name) / "maze.db")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # --- Tests ------------------------------------------------------------------------------

    def test_tool_listed(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        _, _, raw = raw_call(self.s.base, "POST", "/mcp", json.dumps(body).encode(), {
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"})
        tool = next(t for t in json.loads(raw)["result"]["tools"] if t["name"] == "request_upload")
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertIn("10 minutes", tool["description"])

    def test_each_target(self):
        s, cid = self.s, self.camp["id"]
        fresh = {kind: s.call("POST", f"/api/campaigns/{cid}/{kind}s", {"name": name})[1]["id"]
                 for kind, name in (("npc", "Ysolde"), ("faction", "La Vigie"), ("place", "Valdis"))}
        cases = [("npc", fresh["npc"]), ("faction", fresh["faction"]), ("place", fresh["place"]),
                 ("document", self.doc["id"]), ("library", None)]
        for target, target_id in cases:
            with self.subTest(target=target):
                args = {"campaign_id": cid, "target": target}
                if target_id is not None:
                    args["target_id"] = target_id
                if target != "document":
                    args["caption"] = f"Proposée pour {target}"
                made, token = self.ticket(**args)
                self.assertEqual(made["valid_minutes"], 10)
                self.assertIn(f"ticket={made['ticket']}", made["next"])
                # Jeton haché en base, jamais en clair.
                with self.db() as conn:
                    stored = conn.execute("SELECT token_hash, state FROM upload_tickets WHERE id = ?",
                                          (made["ticket"],)).fetchone()
                self.assertNotEqual(stored[0], token)
                self.assertEqual(stored[1], "pending")

                code, info = s.call("GET", f"/api/uploads/{token}")
                self.assertEqual(code, 200, info)
                self.assertEqual((info["state"], info["target"], info["campaign"]["name"]), ("pending", target, "Dépôts"))
                expected_name = {"npc": "Ysolde", "faction": "La Vigie", "place": "Valdis", "document": "Le bal masqué",
                                 "library": ""}[target]
                self.assertEqual(info["target_name"], expected_name)
                self.assertGreater(info["seconds_left"], 500)

                ok, status = self.tool(ticket=made["ticket"])
                self.assertTrue(ok, status)
                self.assertEqual(status["state"], "pending")

                code, out = self.deposit(s, token)
                self.assertEqual(code, 201, out)
                self.assertEqual(out["state"], "used")
                image_id = out["image"]["id"]

                ok, status = self.tool(ticket=made["ticket"])
                self.assertEqual(status["state"], "used")
                key = {"library": "library_image_id", "document": "document_image_id"}.get(target, "image_id")
                self.assertEqual(status[key], image_id)

                # L'image est bien dans la cible (vérifié par l'API REST).
                if target in ("npc", "faction", "place"):
                    path = {"npc": "npcs", "faction": "factions", "place": "places"}[target]
                    _, entity = s.call("GET", f"/api/{path}/{target_id}")
                    images = {i["id"]: i for i in entity["images"]}
                    self.assertIn(image_id, images)
                    self.assertEqual(images[image_id]["caption"], f"Proposée pour {target}")
                    cover = {"npc": "portrait_image_id", "faction": "emblem_image_id", "place": "image_id"}[target]
                    self.assertEqual(entity[cover], image_id)   # première image : devenue la couverture
                else:
                    _, listing = s.call("GET", f"/api/admin/campaigns/{cid}/images")
                    source = "library" if target == "library" else "document"
                    found = [i for i in listing["images"] if i["source"] == source and i["id"] == image_id]
                    self.assertEqual(len(found), 1, listing["images"])
                if target == "document":
                    self.assertIn(f'data-doc-image="{image_id}"', status["html"])
                    self.assertIn("update_document", status["next"])

                # Usage unique.
                code, out = self.deposit(s, token)
                self.assertEqual(code, 409, out)
                self.assertIn("déjà servi", out["detail"])

    def test_caption_from_page(self):
        made, token = self.ticket(campaign_id=self.camp["id"], target="library", caption="Proposée")
        code, out = self.deposit(self.s, token, caption="  Choisie par le MJ  ")
        self.assertEqual(code, 201, out)
        with self.db() as conn:
            caption = conn.execute("SELECT caption FROM library_images WHERE id = ?", (out["image"]["id"],)).fetchone()
        self.assertEqual(caption[0], "Choisie par le MJ")

    def test_expiry_and_purge(self):
        s = self.s
        made, token = self.ticket(campaign_id=self.camp["id"], target="npc", target_id=self.npc["id"])
        with self.db() as conn:
            conn.execute("UPDATE upload_tickets SET expires_at = ? WHERE id = ?", (int(time.time()) - 5, made["ticket"]))
        code, info = s.call("GET", f"/api/uploads/{token}")
        self.assertEqual((code, info["state"]), (200, "expired"))
        code, out = self.deposit(s, token)
        self.assertEqual(code, 410, out)
        self.assertIn("expiré", out["detail"])
        ok, status = self.tool(ticket=made["ticket"])
        self.assertEqual(status["state"], "expired")

        # Purge à la création d'un ticket : expiré marqué, puis supprimé un jour après l'échéance.
        self.ticket(campaign_id=self.camp["id"], target="library")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT state FROM upload_tickets WHERE id = ?",
                                          (made["ticket"],)).fetchone()[0], "expired")
            conn.execute("UPDATE upload_tickets SET expires_at = ? WHERE id = ?",
                         (int(time.time()) - 2 * 86400, made["ticket"]))
        self.ticket(campaign_id=self.camp["id"], target="library")
        ok, out = self.tool(ticket=made["ticket"])
        self.assertFalse(ok)
        self.assertIn("Ticket introuvable", out)

    def test_access(self):
        s = self.s
        made, token = self.ticket(campaign_id=self.camp["id"], target="faction", target_id=self.faction["id"])
        anonymous = s.client()
        self.assertEqual(anonymous.call("GET", f"/api/uploads/{token}")[0], 401)
        self.assertEqual(self.deposit(anonymous, token)[0], 401)

        code, mj = s.call("POST", "/api/admin/users", {"username": "bastien", "role": "mj", "password": "mdp-bastien-1",
                                                       "campaign_ids": [self.read_camp["id"]]})
        self.assertEqual(code, 201, mj)
        other = s.client()
        self.assertEqual(other.login("bastien", "mdp-bastien-1"), 200)
        code, out = other.call("GET", f"/api/uploads/{token}")
        self.assertEqual((code, out["detail"]), (404, "Lien de dépôt introuvable"))
        self.assertEqual(self.deposit(other, token)[0], 404)
        self.assertEqual(s.call("GET", "/api/uploads/inconnu-0123456789")[0], 404)

        # Ouverte à ce MJ : il peut déposer (le ticket a été demandé par un administrateur, via Claude).
        self.assertEqual(s.call("PUT", f"/api/admin/campaigns/{self.camp['id']}/members", {"user_ids": [mj["id"]]})[0], 200)
        code, out = self.deposit(other, token)
        self.assertEqual(code, 201, out)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT used_by FROM upload_tickets WHERE id = ?",
                                          (made["ticket"],)).fetchone()[0], mj["id"])

        # Bibliothèque : envoi réservé aux administrateurs (comme Administration > Images), même par un lien (revue).
        made, token = self.ticket(campaign_id=self.camp["id"], target="library")
        for code, out in (other.call("GET", f"/api/uploads/{token}"), self.deposit(other, token)):
            self.assertEqual(code, 403, out)
            self.assertIn("réservé aux administrateurs", out["detail"])
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT state, image_id FROM upload_tickets WHERE id = ?",
                                          (made["ticket"],)).fetchone(), ("pending", None))
        self.assertEqual(self.deposit(s, token)[0], 201)   # l'administrateur, lui, peut déposer

    def test_bad_file(self):
        made, token = self.ticket(campaign_id=self.camp["id"], target="place", target_id=self.place["id"])
        code, out = self.deposit(self.s, token, content=b"ceci n'est pas une image")
        self.assertEqual(code, 400, out)
        self.assertIn("illisible", out["detail"])
        code, info = self.s.call("GET", f"/api/uploads/{token}")
        self.assertEqual(info["state"], "pending")   # ticket toujours utilisable
        code, out = self.s.call("POST", f"/api/uploads/{token}", raw=b"--x--\r\n", content_type="multipart/form-data; boundary=x")
        self.assertEqual(code, 400, out)
        self.assertEqual(self.deposit(self.s, token)[0], 201)

    def test_target_deleted(self):
        _, npc = self.s.call("POST", f"/api/campaigns/{self.camp['id']}/npcs", {"name": "Éphémère"})
        made, token = self.ticket(campaign_id=self.camp["id"], target="npc", target_id=npc["id"])
        self.assertIn(self.s.call("DELETE", f"/api/npcs/{npc['id']}")[0], (200, 204))
        code, info = self.s.call("GET", f"/api/uploads/{token}")
        self.assertIsNone(info["target_name"])
        self.assertEqual(self.deposit(self.s, token)[0], 404)

    def test_tool_refusals(self):
        cid = self.camp["id"]
        cases = [
            ({"campaign_id": self.read_camp["id"], "target": "library"}, "lecture seule"),
            ({"campaign_id": self.closed_camp["id"], "target": "library"}, "non ouverte à Claude"),
            ({"campaign_id": cid, "target": "npc"}, "target_id"),
            ({"campaign_id": cid, "target": "npc", "target_id": self.other_npc["id"]}, "introuvable dans cette campagne"),
            ({"campaign_id": cid, "target": "library", "target_id": 1}, "sans objet"),
            ({"campaign_id": cid, "target": "map"}, "target"),
            ({"campaign_id": cid, "target": "document", "target_id": self.doc["id"], "caption": "x"}, "sans objet"),
            ({"ticket": 1, "campaign_id": cid}, "seul"),
            ({"ticket": 999999}, "Ticket introuvable"),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                ok, out = self.tool(**args)
                self.assertFalse(ok, out)
                self.assertIn(expected, out)
        with self.db() as conn:
            row = conn.execute("SELECT tool, ok FROM mcp_log ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row, ("request_upload", 0))


class PublicUrlTest(unittest.TestCase):
    def test_public_url(self):
        s = Server({"MAZE_PUBLIC_URL": "https://maze.example.org"})
        try:
            conn = sqlite3.connect(Path(s.tmp.name) / "maze.db")
            with conn:
                conn.execute("INSERT INTO campaigns (name) VALUES ('Adresse')")
            conn.close()
            from maze.config import load_settings
            from maze.mcp import Context
            from maze import mcp_upload
            import asyncio

            class App:
                class state:
                    settings = load_settings()
            ctx = Context(app=App, user={"id": 1}, grants={1: "write"}, base="http://interne:8000")
            out = asyncio.run(mcp_upload.request_upload(ctx, {"campaign_id": 1, "target": "library"}))
            self.assertTrue(out["url"].startswith("https://maze.example.org/#/depot/"), out["url"])
        finally:
            s.stop()


if __name__ == "__main__":
    unittest.main()
