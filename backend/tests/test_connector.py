"""Tests du connecteur Claude : OAuth (oauth.py), serveur MCP (mcp.py) et administration (connector.py).

Lancer : cd backend && python -m unittest -q tests.test_connector
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import io
import json
import secrets
import sqlite3
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tests.test_api import ADMIN_PASSWORD, Server, multipart, png_bytes, recv, recv_until, seq_of, wait_until

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
# Jeu d'outils regroupé (lot connecteur-outils) et lien de dépôt d'image (lot depot-image) : ces 15 noms seulement,
# plus aucun des 42 anciens.
READ_TOOLS = ("list_campaigns", "get_campaign", "search", "list", "get", "view")
TOOL_NAMES = (*READ_TOOLS, "update_document", "create", "update", "link", "unlink", "delete", "add_image",
              "edit_image", "request_upload")
OLD_TOOL_NAMES = (
    "get_entry", "list_relations", "get_document", "create_entry", "update_entry", "create_relation",
    "update_relation", "delete_relation", "set_membership", "remove_membership", "set_place_link",
    "remove_place_link", "create_document", "add_comment", "add_entry_comment", "list_events", "create_event",
    "update_event", "delete_entry", "delete_document", "delete_event", "delete_map", "delete_comment", "view_image",
    "list_library", "update_image", "delete_image", "delete_library_image", "add_document_image", "list_maps",
    "view_map", "create_map", "update_map", "set_place_kind", "place_on_map", "remove_from_map")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def raw_call(base: str, method: str, path: str, data: bytes | None = None, headers: dict | None = None):
    """Requête sans cookie ni redirection suivie : (code, en-têtes, corps)."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers or {})
    try:
        with opener.open(req) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read()


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class ConnectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()
        s = cls.s
        _, cls.write_camp = s.call("POST", "/api/campaigns", {"name": "Ouverte en écriture"})
        _, cls.read_camp = s.call("POST", "/api/campaigns", {"name": "Ouverte en lecture"})
        _, cls.closed_camp = s.call("POST", "/api/campaigns", {"name": "Fermée"})
        _, cls.hidden_npc = s.call("POST", f"/api/campaigns/{cls.closed_camp['id']}/npcs", {"name": "Invisible Varnak"})
        _, cls.read_npc = s.call("POST", f"/api/campaigns/{cls.read_camp['id']}/npcs", {"name": "Lecteur Ombrage"})
        assert s.call("POST", "/api/admin/claude/credentials")[0] == 415  # corps JSON exigé (CSRF)
        code, out = s.call("POST", "/api/admin/claude/credentials", {})
        assert code == 201, out
        cls.client_id, cls.secret = out["client_id"], out["client_secret"]
        code, _ = s.call("PUT", "/api/admin/claude", {"enabled": True, "grants": [
            {"campaign_id": cls.write_camp["id"], "mode": "write"},
            {"campaign_id": cls.read_camp["id"], "mode": "read"},
            {"campaign_id": cls.closed_camp["id"], "mode": "none"},
        ]})
        assert code == 200

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    # --- Aides ------------------------------------------------------------------------------

    def authorize(self, username="admin", password=ADMIN_PASSWORD, action="allow", challenge=None, state="xyz"):
        fields = {"client_id": self.client_id, "redirect_uri": CALLBACK, "state": state,
                  "code_challenge": challenge or pkce()[1], "code_challenge_method": "S256",
                  "scope": "maze", "resource": f"{self.s.base}/mcp",
                  "username": username, "password": password, "action": action}
        return raw_call(self.s.base, "POST", "/oauth/authorize", urllib.parse.urlencode(fields).encode(),
                        {"Content-Type": "application/x-www-form-urlencoded"})

    def token_request(self, fields: dict, secret: str | None = None, basic: bool = False):
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        fields = dict(fields)
        secret = self.secret if secret is None else secret
        if basic:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{self.client_id}:{secret}".encode()).decode()
        else:
            fields.update(client_id=self.client_id, client_secret=secret)
        code, _, body = raw_call(self.s.base, "POST", "/oauth/token", urllib.parse.urlencode(fields).encode(), headers)
        return code, json.loads(body)

    def connect(self) -> dict:
        verifier, challenge = pkce()
        code, headers, _ = self.authorize(challenge=challenge)
        self.assertEqual(code, 303)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)
        status, tokens = self.token_request({"grant_type": "authorization_code", "code": query["code"][0],
                                             "redirect_uri": CALLBACK, "code_verifier": verifier}, basic=True)
        self.assertEqual(status, 200, tokens)
        return tokens

    def rpc(self, token: str, method: str, params: dict | None = None, rid=1):
        body = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            body["params"] = params
        code, headers, raw = raw_call(self.s.base, "POST", "/mcp", json.dumps(body).encode(), {
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"})
        return code, headers, (json.loads(raw) if raw else None)

    def tool(self, token: str, name: str, /, **arguments):
        code, _, reply = self.rpc(token, "tools/call", {"name": name, "arguments": arguments})
        self.assertEqual(code, 200, reply)
        result = reply["result"]
        text = result["content"][0]["text"]
        if result["isError"]:
            return False, text
        return True, json.loads(text)

    def one(self, token: str, name: str, /, item: dict, **arguments):
        """Outil d'écriture par lots appelé avec un seul élément : (réussite, résultat de l'élément ou erreur)."""
        ok, out = self.tool(token, name, items=[item], **arguments)
        if not ok:
            if not out.startswith("{"):
                return False, out   # refus de l'appel entier (paramètres)
            out = json.loads(out)
        result = out["results"][0]
        return (True, result) if result["ok"] else (False, result["error"])

    def tool_names(self, token: str) -> dict:
        return {t["name"]: t for t in self.rpc(token, "tools/list")[2]["result"]["tools"]}

    def grant(self, campaign_id: int, mode: str) -> None:
        self.s.call("PUT", "/api/admin/claude", {"grant": {"campaign_id": campaign_id, "mode": mode}})

    def new_campaign(self, name: str, mode: str = "write") -> int:
        _, camp = self.s.call("POST", "/api/campaigns", {"name": name})
        self.grant(camp["id"], mode)
        self.addCleanup(self.grant, camp["id"], "none")
        return camp["id"]

    # --- Tests ------------------------------------------------------------------------------

    def test_discovery_and_authorization(self):
        s = self.s
        code, headers, _ = raw_call(s.base, "POST", "/mcp", b"{}", {"Content-Type": "application/json"})
        self.assertEqual(code, 401)
        self.assertIn("resource_metadata=", headers["WWW-Authenticate"])
        code, _, body = raw_call(s.base, "GET", "/.well-known/oauth-protected-resource/mcp")
        meta = json.loads(body)
        self.assertEqual((code, meta["resource"]), (200, f"{s.base}/mcp"))
        code, _, body = raw_call(s.base, "GET", "/.well-known/oauth-authorization-server")
        meta = json.loads(body)
        self.assertEqual(meta["token_endpoint"], f"{s.base}/oauth/token")
        self.assertEqual(meta["code_challenge_methods_supported"], ["S256"])
        self.assertEqual(raw_call(s.base, "GET", "/mcp")[0], 405)

        # Page d'autorisation : campagnes ouvertes listées, client et adresse de retour vérifiés.
        params = {"response_type": "code", "client_id": self.client_id, "redirect_uri": CALLBACK, "state": "s",
                  "code_challenge": pkce()[1], "code_challenge_method": "S256"}
        code, _, page = raw_call(s.base, "GET", "/oauth/authorize?" + urllib.parse.urlencode(params))
        self.assertEqual(code, 200)
        self.assertIn("<strong>2</strong>, dont <strong>1</strong>".encode(), page)  # effectifs, pas les noms
        self.assertNotIn("Ouverte en".encode(), page)
        self.assertIn(b"claude.ai", page)
        bad = {**params, "client_id": "inconnu-\u00e9"}  # non ASCII : refus propre
        self.assertEqual(raw_call(s.base, "GET", "/oauth/authorize?" + urllib.parse.urlencode(bad))[0], 400)
        self.assertEqual(raw_call(s.base, "GET", "/oauth/token")[0], 404)
        self.assertEqual(raw_call(s.base, "GET", "/.well-known/openid-configuration")[0], 404)
        bad = {**params, "client_id": "inconnu"}
        self.assertEqual(raw_call(s.base, "GET", "/oauth/authorize?" + urllib.parse.urlencode(bad))[0], 400)
        bad = {**params, "redirect_uri": "https://evil.example/cb"}
        self.assertEqual(raw_call(s.base, "GET", "/oauth/authorize?" + urllib.parse.urlencode(bad))[0], 400)
        no_pkce = {k: v for k, v in params.items() if not k.startswith("code_challenge")}
        code, headers, _ = raw_call(s.base, "GET", "/oauth/authorize?" + urllib.parse.urlencode(no_pkce))
        self.assertEqual(code, 303)
        self.assertIn("error=invalid_request", headers["Location"])
        local = {**params, "redirect_uri": "http://localhost:4321/callback"}  # Claude Code
        self.assertEqual(raw_call(s.base, "GET", "/oauth/authorize?" + urllib.parse.urlencode(local))[0], 200)

        # Refus, mauvais mot de passe, compte non administrateur.
        code, headers, _ = self.authorize(action="deny")
        self.assertEqual(code, 303)
        self.assertTrue(headers["Location"].startswith(CALLBACK + "?error=access_denied"))
        self.assertEqual(self.authorize(password="mauvais")[0], 401)
        s.call("POST", "/api/admin/users", {"username": "mj1", "password": "motdepasse-mj1", "role": "mj"})
        self.assertEqual(self.authorize(username="mj1", password="motdepasse-mj1")[0], 403)

        # Code : PKCE exigé, secret du client exigé, usage unique.
        verifier, challenge = pkce()
        code, headers, _ = self.authorize(challenge=challenge, state="etat-42")
        self.assertEqual(code, 303)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)
        self.assertEqual(query["state"], ["etat-42"])
        grant = {"grant_type": "authorization_code", "code": query["code"][0], "redirect_uri": CALLBACK,
                 "code_verifier": verifier}
        self.assertEqual(self.token_request(grant, secret="faux")[0], 401)
        code, out = self.token_request({**grant, "code_verifier": pkce()[0]})
        self.assertEqual((code, out["error"]), (400, "invalid_grant"))  # code consommé par l'échec
        code, headers, _ = self.authorize(challenge=challenge)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)
        grant["code"] = query["code"][0]
        code, tokens = self.token_request(grant)
        self.assertEqual(code, 200, tokens)
        self.assertEqual(tokens["token_type"], "Bearer")
        self.assertEqual(self.token_request(grant)[0], 400)

        # Rafraîchissement : nouvelle paire, l'ancien jeton de rafraîchissement ne sert plus.
        code, fresh = self.token_request({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
        self.assertEqual(code, 200)
        self.assertEqual(self.token_request({"grant_type": "refresh_token",
                                             "refresh_token": tokens["refresh_token"]})[0], 400)
        self.assertEqual(self.rpc(fresh["access_token"], "ping")[0], 200)

    def test_authorization_attempts_limited_and_address_remembered(self):
        """Vérification finale R1 et R4 sur l'autorisation OAuth (mêmes compteurs que la connexion) : refus de
        l'adresse avec son message et un Retry-After juste ; autorisation réussie = adresse connue gardée en base."""
        from maze import throttle
        throttle.reset()
        self.addCleanup(throttle.reset)
        db_file = Path(self.s.tmp.name) / "maze.db"
        with sqlite3.connect(db_file) as conn:
            conn.execute("DELETE FROM login_addresses")
        code, headers, _ = self.authorize()
        self.assertEqual(code, 303)
        with sqlite3.connect(db_file) as conn:
            self.assertEqual(conn.execute("SELECT u.username, a.address FROM login_addresses AS a "
                                          "JOIN users AS u ON u.id = a.user_id").fetchall(), [("admin", "127.0.0.1")])
        for i in range(throttle.MAX_PER_ADDRESS):
            self.assertEqual(self.authorize(username=f"inconnu{i}", password="faux")[0], 401)
        code, headers, page = self.authorize()
        self.assertEqual(code, 429)
        self.assertIn("Trop de tentatives depuis cette adresse. Réessayez dans 10 minutes.", page.decode())
        self.assertTrue(590 <= int(headers["Retry-After"]) <= 600, headers["Retry-After"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_batch_and_body_limits(self):
        """Audit PERF-13 : lot JSON-RPC de 20 messages au plus ; audit M19 : corps de /mcp borné (413)."""
        token = self.connect()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        batch = [{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(20)]
        code, _, raw = raw_call(self.s.base, "POST", "/mcp", json.dumps(batch).encode(), headers)
        self.assertEqual((code, len(json.loads(raw))), (200, 20))
        code, _, raw = raw_call(self.s.base, "POST", "/mcp", json.dumps(batch * 500).encode(), headers)
        self.assertEqual(code, 400)
        self.assertEqual(json.loads(raw)["error"]["code"], -32600)
        self.assertIn("20 messages au plus", json.loads(raw)["error"]["message"])
        # JSON imbriqué au-delà de la limite de récursion : 400 (-32700) et non une 500 (audit PERF-14).
        code, _, raw = raw_call(self.s.base, "POST", "/mcp", b"[" * 100_000 + b"]" * 100_000, headers)
        self.assertEqual((code, json.loads(raw)["error"]["code"]), (400, -32700))
        # Corps annoncé sans être envoyé (raw de test_limits) : le serveur répond 413 et ferme sans le lire ; l'envoyer
        # en entier ferait échouer le test au hasard (connexion réinitialisée pendant l'envoi).
        from tests.test_limits import raw
        code, _ = raw(self.s.base, "POST", "/mcp", b" " * (4 * 1024 * 1024 + 1), headers)
        self.assertEqual(code, 413)

    def test_suspend_and_revocation(self):
        tokens = self.connect()
        token = tokens["access_token"]
        self.assertTrue(self.tool(token, "list_campaigns")[0])
        self.s.call("PUT", "/api/admin/claude", {"enabled": False})
        ok, err = self.tool(token, "list_campaigns")
        self.assertFalse(ok)
        self.assertIn("suspendu", err)
        self.s.call("PUT", "/api/admin/claude", {"enabled": True})
        self.assertTrue(self.tool(token, "list_campaigns")[0])
        # Réglages réservés aux administrateurs.
        mj = self.s.client()
        self.s.call("POST", "/api/admin/users", {"username": "mj2", "password": "motdepasse-mj2", "role": "mj"})
        mj.login("mj2", "motdepasse-mj2")
        self.assertEqual(mj.call("GET", "/api/admin/claude")[0], 403)
        self.assertEqual(mj.call("PUT", "/api/admin/claude", {"enabled": False})[0], 403)
        # Déconnexion depuis l'administration : jetons d'accès et de rafraîchissement révoqués.
        verifier, challenge = pkce()
        _, headers, _ = self.authorize(challenge=challenge)  # code émis juste avant la déconnexion
        pending = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)["code"][0]
        self.s.call("POST", "/api/admin/claude/disconnect", {})
        self.assertEqual(self.rpc(token, "ping")[0], 401)
        self.assertEqual(self.token_request({"grant_type": "authorization_code", "code": pending,
                                             "redirect_uri": CALLBACK, "code_verifier": verifier})[0], 400)
        self.assertEqual(self.token_request({"grant_type": "refresh_token",
                                             "refresh_token": tokens["refresh_token"]})[0], 400)

    def test_demoted_admin(self):
        self.s.call("POST", "/api/admin/users", {"username": "admin2", "password": "motdepasse-a2", "role": "admin"})
        users = self.s.call("GET", "/api/admin/users")[1]
        uid = next(u["id"] for u in users if u["username"] == "admin2")
        verifier, challenge = pkce()
        _, headers, _ = self.authorize(username="admin2", password="motdepasse-a2", challenge=challenge)
        code = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)["code"][0]
        _, tokens = self.token_request({"grant_type": "authorization_code", "code": code,
                                        "redirect_uri": CALLBACK, "code_verifier": verifier})
        self.assertEqual(self.rpc(tokens["access_token"], "ping")[0], 200)
        self.s.call("PATCH", f"/api/admin/users/{uid}", {"role": "mj"})
        self.s.call("PATCH", f"/api/admin/users/{uid}", {"role": "admin"})
        self.assertEqual(self.rpc(tokens["access_token"], "ping")[0], 401)  # rétrogradé : déconnecté pour de bon

    def test_single_grant_update(self):
        _, camp = self.s.call("POST", "/api/campaigns", {"name": "Ouverte à part"})
        _, state = self.s.call("PUT", "/api/admin/claude", {"grant": {"campaign_id": camp["id"], "mode": "read"}})
        modes = {g["campaign_id"]: g["mode"] for g in state["grants"]}
        self.assertEqual(modes[camp["id"]], "read")
        self.assertEqual(modes[self.write_camp["id"]], "write")  # les autres sont inchangées
        _, state = self.s.call("PUT", "/api/admin/claude", {"grant": {"campaign_id": camp["id"], "mode": "none"}})
        self.assertNotIn(camp["id"], {g["campaign_id"] for g in state["grants"]})

    def test_visible_campaigns_single_rule(self):
        """Audit QUAL-6 : une seule règle « campagnes visibles par ce compte » (auth.visible_campaign_ids). GET
        /api/campaigns appelée au nom de Claude (Context.api, comme tout outil qui passe par l'API) ne montre que
        les campagnes qui lui sont ouvertes, comme GET /api/campaigns/{id} et la recherche."""
        import asyncio

        from maze import auth, db, mcp
        app = self.s.server.config.app
        with db.transaction(app.state.settings.db_path, write=False) as conn:
            admin = dict(conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone())
        opened = {self.write_camp["id"]: "write", self.read_camp["id"]: "read"}
        ctx = mcp.Context(app=app, user=admin, grants=opened)
        names = [c["name"] for c in asyncio.run(ctx.api("GET", "/api/campaigns"))]
        self.assertEqual(sorted(names), ["Ouverte en lecture", "Ouverte en écriture"])
        self.assertNotIn("Fermée", names)
        with self.assertRaises(mcp.ToolError):
            asyncio.run(ctx.api("GET", f"/api/campaigns/{self.closed_camp['id']}"))
        found = asyncio.run(ctx.api("GET", "/api/search", query={"q": "Varnak", "campaign": "all"}))
        self.assertEqual(found["results"], [])
        # Outil list_campaigns : mêmes campagnes ; écriture seule (outils d'écriture) : celle ouverte en écriture.
        listed = asyncio.run(mcp.list_campaigns(ctx, {}))["campaigns"]
        self.assertEqual(sorted(c["id"] for c in listed), sorted(opened))
        writer = mcp.Context(app=app, user=admin, grants=opened)
        names = [c["name"] for c in asyncio.run(writer.api("GET", "/api/campaigns", write=True))]
        self.assertEqual(names, ["Ouverte en écriture"])
        # La règle elle-même : None pour toutes (administrateur), sinon la liste.
        with db.transaction(app.state.settings.db_path, write=False) as conn:
            self.assertIsNone(auth.visible_campaign_ids(conn, admin))
            claude = {**admin, "role": "mj", "mcp_scope": frozenset(opened)}
            self.assertEqual(auth.visible_campaign_ids(conn, claude), sorted(opened))
            self.assertEqual(auth.visible_campaign_ids(conn, {**claude, "mcp_scope": frozenset()}), [])
            self.assertFalse(auth.can_access(conn, claude, self.closed_camp["id"]))
            self.assertTrue(auth.can_access(conn, claude, self.read_camp["id"]))
            mj = {"id": -1, "role": "mj"}
            self.assertEqual(auth.visible_campaign_ids(conn, mj), [])
            self.assertFalse(auth.can_access(conn, mj, self.read_camp["id"]))

    def test_settings_row_created_from_read_transaction(self):
        """load_settings_row sous query_only (route GET, outil) : ligne manquante créée dans une transaction d'écriture
        à part, sans erreur « readonly database »."""
        from maze import connector, db
        path = Path(self.s.tmp.name) / "maze.db"
        with db.transaction(path) as conn:
            saved = dict(conn.execute("SELECT * FROM mcp_settings WHERE id = 1").fetchone())
            conn.execute("DELETE FROM mcp_settings WHERE id = 1")
        try:
            with db.transaction(path, write=False) as conn:
                row = connector.load_settings_row(conn)
            self.assertEqual(row["id"], 1)
            with db.transaction(path, write=False) as conn:
                self.assertIsNotNone(conn.execute("SELECT 1 FROM mcp_settings WHERE id = 1").fetchone())
        finally:
            with db.transaction(path) as conn:
                conn.execute("DELETE FROM mcp_settings WHERE id = 1")
                db.insert(conn, "mcp_settings", saved)

    def test_zz_password_change_and_new_secret(self):
        token = self.connect()["access_token"]
        admin = self.s.client()
        admin.login("admin", ADMIN_PASSWORD)
        self.assertEqual(admin.call("POST", "/api/auth/password", {"current_password": ADMIN_PASSWORD,
                                                                   "new_password": ADMIN_PASSWORD + "-2"})[0], 200)
        self.assertEqual(self.rpc(token, "ping")[0], 401)  # clé de session du compte changée
        admin.call("POST", "/api/auth/password", {"current_password": ADMIN_PASSWORD + "-2",
                                                  "new_password": ADMIN_PASSWORD})
        self.s.admin.login("admin", ADMIN_PASSWORD)
        token = self.connect()["access_token"]
        code, out = self.s.call("POST", "/api/admin/claude/credentials", {})
        self.assertEqual(code, 201)
        self.assertEqual(self.rpc(token, "ping")[0], 401)  # nouveau secret : Claude doit se reconnecter
        type(self).client_id, type(self).secret = out["client_id"], out["client_secret"]
        self.assertEqual(self.rpc(self.connect()["access_token"], "ping")[0], 200)

    def test_nested_factions(self):
        """Factions imbriquées (migration 031) : link / unlink (kind membership) avec member_faction_id, règles de
        l'API (cycle, elle-même, chef), get (sous-factions, member_of), get_campaign (arbre, section factions)."""
        token = self.connect()["access_token"]
        w = self.write_camp["id"]
        mk = lambda name: self.one(token, "create", campaign_id=w, kind="faction", item={"name": name})[1]  # noqa: E731
        council, watch, cell = mk("Conseil des Brumes"), mk("Guet des Brumes"), mk("Cellule des Brumes")
        ok, npc = self.one(token, "create", campaign_id=w, kind="npc", item={"name": "Veilleur Orm"})
        link = lambda **item: self.one(token, "link", kind="membership", item=item)  # noqa: E731
        ok, member = link(member_faction_id=watch["id"], faction_id=council["id"], role="Bras armé")
        self.assertTrue(ok, member)
        self.assertEqual((member["member_faction_id"], member.get("npc_id"), member["role"]),
                         (watch["id"], None, "Bras armé"))
        ok, again = link(member_faction_id=watch["id"], faction_id=council["id"], role="Milice")
        self.assertEqual((again["role"], again["id"]), ("Milice", member["id"]))   # même paire : modifiée, pas doublée
        self.assertTrue(link(member_faction_id=cell["id"], faction_id=watch["id"])[0])
        self.assertTrue(link(npc_id=npc["id"], faction_id=watch["id"], is_leader=True)[0])
        ok, err = link(member_faction_id=council["id"], faction_id=cell["id"])
        self.assertFalse(ok)
        self.assertIn("formerait un cycle", err)
        self.assertEqual(link(member_faction_id=cell["id"], faction_id=cell["id"]),
                         (False, "Une faction ne peut pas être membre d'elle-même"))
        self.assertEqual(link(member_faction_id=cell["id"], faction_id=council["id"], is_leader=True),
                         (False, "Seul un PNJ peut diriger une faction"))
        self.assertFalse(link(faction_id=council["id"])[0])
        self.assertFalse(link(npc_id=npc["id"], member_faction_id=cell["id"], faction_id=council["id"])[0])
        _, hidden = self.s.call("POST", f"/api/campaigns/{self.closed_camp['id']}/factions", {"name": "Fermée"})
        self.assertEqual(link(member_faction_id=hidden["id"], faction_id=council["id"]),
                         (False, "Faction introuvable (ou non ouverte à Claude)"))

        ok, detail = self.tool(token, "get", kind="faction", id=watch["id"])
        self.assertEqual([(m.get("npc"), m.get("member_faction"), m.get("leader")) for m in detail["memberships"]],
                         [("Veilleur Orm", None, True), (None, "Cellule des Brumes", None)])
        self.assertEqual([(m["faction"], m["role"]) for m in detail["member_of"]], [("Conseil des Brumes", "Milice")])
        ok, detail = self.tool(token, "get", kind="npc", id=npc["id"])
        self.assertEqual([m["faction"] for m in detail["memberships"]], ["Guet des Brumes"])
        self.assertNotIn("member_of", detail)
        ok, section = self.tool(token, "get_campaign", campaign_id=w, section="factions")
        factions = {f["name"]: f for f in section["items"]}
        self.assertEqual(factions["Guet des Brumes"]["member_of"], [{"faction_id": council["id"], "role": "Milice"}])
        self.assertEqual(factions["Conseil des Brumes"]["member_of"], [])
        ok, section = self.tool(token, "get_campaign", campaign_id=w, section="npcs")
        orm = next(n for n in section["items"] if n["name"] == "Veilleur Orm")
        self.assertEqual(orm["factions"], [{"faction_id": watch["id"], "role": "", "leader": True}])
        ok, overview = self.tool(token, "get_campaign", campaign_id=w)
        top = next(f for f in overview["factions"] if f["id"] == council["id"])
        self.assertEqual(top["sub"][0]["id"], watch["id"])
        self.assertEqual((top["sub"][0]["npcs"], top["sub"][0]["sub"][0]["name"]), (1, "Cellule des Brumes"))
        self.assertNotIn(watch["id"], [f["id"] for f in overview["factions"]])   # pas au premier niveau

        ok, removed = self.one(token, "unlink", kind="membership",
                               item={"member_faction_id": cell["id"], "faction_id": watch["id"]})
        self.assertEqual(removed["removed"], {"member_faction_id": cell["id"], "faction_id": watch["id"]})
        self.assertEqual(self.one(token, "unlink", kind="membership",
                                  item={"member_faction_id": cell["id"], "faction_id": watch["id"]}),
                         (False, "Cette faction n'est pas membre de cette faction"))
        # Une fois la boucle défaite, l'appartenance inverse devient possible.
        self.assertTrue(link(member_faction_id=council["id"], faction_id=cell["id"])[0])
        for f in (council, watch, cell):
            self.s.call("DELETE", f"/api/factions/{f['id']}")
        self.s.call("DELETE", f"/api/npcs/{npc['id']}")

    def test_protocol_and_tools(self):
        token = self.connect()["access_token"]
        code, _, reply = self.rpc(token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                        "clientInfo": {"name": "test", "version": "1"}})
        self.assertEqual(code, 200)
        self.assertEqual(reply["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", reply["result"]["capabilities"])
        code, _, raw = raw_call(self.s.base, "POST", "/mcp", json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(),
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        self.assertEqual(code, 202)
        # Jeu d'outils regroupé : ces noms et eux seuls (aucun ancien nom), catalogue compact.
        code, _, reply = self.rpc(token, "tools/list")
        tools = {t["name"]: t for t in reply["result"]["tools"]}
        self.assertEqual(set(tools), set(TOOL_NAMES))
        self.assertEqual(len(tools), len(TOOL_NAMES))
        self.assertFalse(set(tools) & set(OLD_TOOL_NAMES))
        self.assertLess(len(json.dumps(reply["result"], ensure_ascii=False)), 25_000)
        for name, tool in tools.items():
            hints = tool["annotations"]
            self.assertEqual(hints["readOnlyHint"], name in READ_TOOLS, name)
            self.assertEqual(hints["destructiveHint"], name in ("update", "update_document", "unlink", "delete"), name)
            self.assertTrue(tool["title"] and tool["description"], name)
        self.assertEqual(tools["create"]["inputSchema"]["properties"]["items"]["maxItems"], 100)
        self.assertEqual(self.rpc(token, "inconnu")[2]["error"]["code"], -32601)
        self.assertEqual(self.rpc(token, "tools/call", {"name": ["x"]})[2]["error"]["code"], -32602)
        self.assertEqual(self.rpc(token, "tools/call", {"name": "get_entry"})[2]["error"]["code"], -32602)

        w, r, closed = self.write_camp["id"], self.read_camp["id"], self.closed_camp["id"]
        ok, out = self.tool(token, "list_campaigns")
        self.assertEqual({c["id"] for c in out["campaigns"]}, {w, r})

        # Fiches : écriture permise, lecture seule, campagne fermée.
        ok, npc = self.one(token, "create", campaign_id=w, kind="npc",
                           item={"name": "Sœur Agathe", "title": "Chanoinesse", "status": "vivant"})
        self.assertTrue(ok, npc)
        self.assertEqual((npc["name"], type(npc["revision"])), ("Sœur Agathe", int))
        self.assertEqual(self.s.call("GET", f"/api/npcs/{npc['id']}")[1]["title"], "Chanoinesse")
        ok, err = self.one(token, "create", campaign_id=r, kind="npc", item={"name": "Interdit"})
        self.assertFalse(ok)
        self.assertIn("lecture seule", err)
        ok, err = self.one(token, "create", campaign_id=closed, kind="npc", item={"name": "Interdit"})
        self.assertFalse(ok)
        self.assertIn("introuvable", err)
        self.assertFalse(self.tool(token, "get", kind="npc", id=self.hidden_npc["id"])[0])
        self.assertTrue(self.tool(token, "get", kind="npc", id=self.read_npc["id"])[0])
        ok, err = self.one(token, "update", kind="npc", item={"id": self.read_npc["id"], "title": "x"})
        self.assertIn("lecture seule", err)
        ok, err = self.one(token, "create", campaign_id=w, kind="npc", item={"name": "X", "status": "zombie"})
        self.assertFalse(ok)
        self.assertIn("status", err)
        ok, err = self.one(token, "update", kind="npc", item={"id": npc["id"], "pos_x": 3})
        self.assertIn("Champs inconnus", err)
        ok, err = self.one(token, "update", kind="npc", item={"id": npc["id"]})
        self.assertIn("Rien à modifier", err)
        ok, upd = self.one(token, "update", kind="npc", item={"id": npc["id"], "summary": "Gardienne des archives"})
        self.assertTrue(ok, upd)
        self.assertEqual(self.s.call("GET", f"/api/npcs/{npc['id']}")[1]["summary"], "Gardienne des archives")
        # Ancienne forme (champs dans fields) toujours acceptée.
        ok, upd = self.one(token, "update", kind="npc", item={"id": npc["id"], "fields": {"title": "Archiviste"}})
        self.assertTrue(ok, upd)
        self.assertEqual(self.s.call("GET", f"/api/npcs/{npc['id']}")[1]["title"], "Archiviste")
        ok, err = self.tool(token, "get", kind="relation", id=1)
        self.assertIn("kind", err)

        # Recherche limitée aux campagnes ouvertes.
        ok, found = self.tool(token, "search", query="agathe")
        self.assertEqual([x["id"] for x in found["results"] if x["kind"] == "npc"], [npc["id"]])
        ok, found = self.tool(token, "search", query="Varnak")
        self.assertEqual(found["results"], [])

        # Faction, appartenance, lien, lieu.
        ok, fac = self.one(token, "create", campaign_id=w, kind="faction", item={"name": "Ordre du Voile"})
        ok, member = self.one(token, "link", kind="membership",
                              item={"npc_id": npc["id"], "faction_id": fac["id"], "role": "Agente"})
        self.assertTrue(ok, member)
        ok, member = self.one(token, "link", kind="membership",
                              item={"npc_id": npc["id"], "faction_id": fac["id"], "is_leader": True})
        self.assertEqual((member["is_leader"], member["role"]), (True, "Agente"))
        ok, other = self.one(token, "create", campaign_id=w, kind="npc", item={"name": "Frère Malik"})
        ok, rel = self.one(token, "create", campaign_id=w, kind="relation",
                           item={"source_kind": "npc", "source_id": npc["id"], "target_kind": "npc",
                                 "target_id": other["id"], "label": "Mentor"})
        self.assertTrue(ok, rel)
        ok, err = self.one(token, "create", campaign_id=w, kind="relation", item={"source": "npc:1"})
        self.assertIn("Champs inconnus pour un lien : source", err)
        ok, upd = self.one(token, "update", kind="relation", item={"id": rel["id"], "secret": True})
        self.assertTrue(upd["secret"])
        ok, place = self.one(token, "create", campaign_id=w, kind="place", item={"name": "Scriptorium"})
        ok, link = self.one(token, "link", kind="place",
                            item={"place_id": place["id"], "entity": f"npc:{npc['id']}", "role": "Archiviste"})
        self.assertTrue(ok, link)
        ok, link = self.one(token, "link", kind="place",
                            item={"place_id": place["id"], "entity_kind": "npc", "entity_id": npc["id"]})
        self.assertEqual(link["role"], "Archiviste")  # sans rôle : inchangé
        ok, detail = self.tool(token, "get", kind="npc", id=npc["id"])
        self.assertEqual(detail["relations"][0]["target"]["name"], "Frère Malik")
        self.assertEqual(detail["memberships"][0]["faction"], "Ordre du Voile")
        self.assertEqual(detail["places"][0]["role"], "Archiviste")
        ok, overview = self.tool(token, "get_campaign", campaign_id=w)
        self.assertEqual(overview["totals"]["relations"], 1)
        self.assertEqual(overview["totals"]["npcs"], 2)
        self.assertNotIn("abrégées", overview["note"])
        self.assertEqual({n["name"] for n in overview["npcs"]}, {"Sœur Agathe", "Frère Malik"})
        self.assertEqual(overview["places"], [{"id": place["id"], "name": "Scriptorium", "kind": "lieu"}])
        ok, page = self.tool(token, "list", campaign_id=w, kind="relation", entity=f"npc:{other['id']}", limit=1)
        self.assertEqual((page["total"], len(page["items"]), page.get("next_offset")), (1, 1, None))
        self.assertIn("relation_types", page)
        ok, page = self.tool(token, "list", campaign_id=w, kind="relation", type="ennemi")
        self.assertEqual(page["total"], 0)
        ok, err = self.tool(token, "list", campaign_id=w, kind="relation", limit=5000)
        self.assertFalse(ok)
        self.assertTrue(self.one(token, "unlink", kind="place",
                                 item={"place_id": place["id"], "entity": f"npc:{npc['id']}"})[0])
        self.assertTrue(self.one(token, "unlink", kind="membership",
                                 item={"npc_id": npc["id"], "faction_id": fac["id"]})[0])
        self.assertTrue(self.one(token, "delete", kind="relation", item={"id": rel["id"]})[0])
        self.assertEqual(self.s.call("GET", f"/api/npcs/{npc['id']}")[1]["places"], [])

        # Fiches rédigées : création, révision exigée, versions signées Claude, commentaire.
        _, before = self.s.call("GET", f"/api/campaigns/{w}/documents")
        ok, err = self.one(token, "create", campaign_id=w, kind="document", item={"title": "Statut faux", "status": "fini"})
        self.assertFalse(ok)
        _, after = self.s.call("GET", f"/api/campaigns/{w}/documents")
        self.assertEqual(len(after["documents"]), len(before["documents"]))  # rien de créé
        mention = f'<p>Rencontre avec <a class="mention" data-mention="npc:{npc["id"]}">Sœur Agathe</a>.</p>'
        ok, doc = self.one(token, "create", campaign_id=w, kind="document", item={"title": "Le scriptorium",
                                                                                 "content": mention})
        self.assertTrue(ok, doc)
        self.assertEqual(doc["kind"], "scenario")
        ok, read = self.tool(token, "get", kind="document", id=doc["id"])
        self.assertIn("Sœur Agathe", read["content"])
        ok, err = self.tool(token, "update_document", document_id=doc["id"], base_revision=read["revision"] - 1,
                            content="<p>Écrasé</p>")
        self.assertFalse(ok)
        ok, upd = self.tool(token, "update_document", document_id=doc["id"], base_revision=read["revision"],
                            content=mention + "<p>Suite écrite par Claude.</p>", status="pret")
        self.assertTrue(ok, upd)
        _, history = self.s.call("GET", f"/api/documents/{doc['id']}/versions")
        labels = [(v["label"], v["author_name"]) for v in history["versions"]]
        self.assertIn(("Modification par Claude", "Claude"), labels)
        self.assertIn("Avant modification par Claude", [v[0] for v in labels])
        _, npc_page = self.s.call("GET", f"/api/npcs/{npc['id']}")
        self.assertEqual([d["id"] for d in npc_page["mentioned_in"]], [doc["id"]])
        ok, comment = self.one(token, "create", campaign_id=w, kind="comment",
                               item={"target": f"doc:{doc['id']}", "body": "À relire", "quote": "Suite"})
        self.assertTrue(ok, comment)
        self.assertEqual(self.s.call("GET", f"/api/documents/{doc['id']}/comments")[1][0]["author_name"], "Claude")
        ok, err = self.one(token, "create", campaign_id=r, kind="comment",
                           item={"target": f"doc:{doc['id']}", "body": "Ailleurs"})
        self.assertIn("n'appartient pas à la campagne", err)
        ok, page = self.one(token, "create", campaign_id=w, kind="document", item={"title": "Acte I",
                                                                                  "parent_id": doc["id"]})
        self.assertEqual(page["kind"], "page")
        ok, read = self.tool(token, "get", kind="document", id=doc["id"], include_comments=True)
        self.assertEqual(read["children"][0]["title"], "Acte I")
        self.assertEqual(read["comments"][0]["body"], "À relire")

        # Journal de l'administration : une ligne par élément, cible lisible.
        _, state = self.s.call("GET", "/api/admin/claude")
        logged = [(e["tool"], e["target"]) for e in state["log"]]
        self.assertIn(("update_document", f"doc:{doc['id']}"), logged)
        self.assertIn(("create", f"npc:{npc['id']} « Sœur Agathe »"), logged)
        self.assertIn(("create", "npc « Interdit »"), logged)
        self.assertTrue(any(not e["ok"] for e in state["log"]))
        self.assertTrue(state["sessions"])

    def test_batches(self):
        """Écriture par lots : 100 éléments en un appel, chacun traité seul (élément invalide au milieu, droits
        élément par élément), une ligne de journal par élément, résultat compact ; bornes de items."""
        token = self.connect()["access_token"]
        w = self.new_campaign("Lots Claude")
        items = [{"name": f"Batelier {i}", "tags": ["port"]} for i in range(100)]
        items[50] = {"name": "Batelier zombie", "status": "zombie"}
        ok, out = self.tool(token, "create", campaign_id=w, kind="npc", items=items)
        self.assertTrue(ok, out)
        self.assertEqual((out["done"], out["failed"]), (99, 1))
        self.assertIn("1 en échec (index 50)", out["summary"])
        self.assertEqual([r["index"] for r in out["results"]], list(range(100)))
        self.assertFalse(out["results"][50]["ok"])
        self.assertIn("status", out["results"][50]["error"])
        ids = [r["id"] for r in out["results"] if r["ok"]]
        self.assertEqual(len(set(ids)), 99)
        self.assertEqual(self.s.call("GET", f"/api/npcs/{ids[-1]}")[1]["name"], "Batelier 99")
        db_file = Path(self.s.tmp.name) / "maze.db"
        with sqlite3.connect(db_file) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM npcs WHERE campaign_id = ?", (w,)).fetchone()[0], 99)
            logged = conn.execute("SELECT ok, target, detail FROM mcp_log WHERE tool = 'create' AND campaign_id = ? "
                                  "ORDER BY id", (w,)).fetchall()
        self.assertEqual(len(logged), 100)
        self.assertEqual(logged[3][:2], (1, f"npc:{ids[3]} « Batelier 3 »"))
        self.assertEqual(logged[50][:2], (0, "npc « Batelier zombie »"))
        self.assertIn("status", logged[50][2])

        # Lot suivant : 99 appartenances, puis la liste des membres (vérifiée aussi par l'API).
        ok, fac = self.one(token, "create", campaign_id=w, kind="faction", item={"name": "Guilde des bateliers"})
        ok, out = self.tool(token, "link", kind="membership",
                            items=[{"npc_id": i, "faction_id": fac["id"], "role": "Batelier"} for i in ids])
        self.assertEqual(out["done"], 99)
        with sqlite3.connect(db_file) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memberships WHERE faction_id = ?",
                                          (fac["id"],)).fetchone()[0], 99)
        ok, page = self.tool(token, "list", campaign_id=w, kind="npc", faction_id=fac["id"], limit=200)
        self.assertEqual((page["total"], page["items"][0]["role"]), (99, "Batelier"))

        # Bornes et éléments invalides.
        for bad in ([], [{"name": "x"}] * 101, "pas une liste", None):
            ok, err = self.tool(token, "create", campaign_id=w, kind="npc", items=bad)
            self.assertFalse(ok)
            self.assertIn("1 à 100", err)
        ok, out = self.tool(token, "create", campaign_id=w, kind="npc", items=[{"name": "Seul"}, "texte", 3])
        self.assertEqual([r["ok"] for r in out["results"]], [True, False, False])
        self.assertIn("objet attendu", out["results"][1]["error"])
        # Aucun élément réussi : erreur d'outil, avec le détail de chacun.
        ok, text = self.tool(token, "create", campaign_id=w, kind="npc", items=[{"status": "vivant"}, {"name": ""}])
        self.assertFalse(ok)
        self.assertEqual(json.loads(text)["done"], 0)
        self.assertFalse(self.tool(token, "create", campaign_id=w, kind="ville", items=[{"name": "x"}])[0])

        # Droits élément par élément : écriture permise, lecture seule, campagne fermée.
        ok, out = self.tool(token, "update", kind="npc", items=[
            {"id": ids[0], "summary": "Premier batelier"}, {"id": self.read_npc["id"], "summary": "x"},
            {"id": self.hidden_npc["id"], "summary": "x"}, {"id": ids[1], "base_revision": 99, "summary": "x"}])
        self.assertEqual([r["ok"] for r in out["results"]], [True, False, False, False])
        self.assertIn("lecture seule", out["results"][1]["error"])
        self.assertIn("introuvable", out["results"][2]["error"])
        self.assertIn("modifiée entre-temps", out["results"][3]["error"])
        self.assertEqual(self.s.call("GET", f"/api/npcs/{ids[0]}")[1]["summary"], "Premier batelier")
        self.assertEqual(self.s.call("GET", f"/api/npcs/{self.read_npc['id']}")[1]["summary"], "")
        # Suppression : la campagne ouverte en suppression oui, celle en écriture non, dans le même lot.
        d = self.new_campaign("Lots suppression", "delete")
        _, doomed = self.s.call("POST", f"/api/campaigns/{d}/npcs", {"name": "Condamné"})
        ok, out = self.tool(token, "delete", kind="npc", items=[
            {"id": ids[2], "name": "Batelier 2"}, {"id": doomed["id"], "name": "Condamné"},
            {"id": ids[3]}, {"id": ids[4], "name": "Batelier 4", "force": True}])
        self.assertEqual([r["ok"] for r in out["results"]], [False, True, False, False])
        self.assertIn("n'autorise pas Claude à supprimer", out["results"][0]["error"])
        self.assertIn("Paramètre manquant : name", out["results"][2]["error"])
        self.assertIn("Champs inconnus", out["results"][3]["error"])
        self.assertEqual(self.s.call("GET", f"/api/npcs/{doomed['id']}")[0], 404)
        self.assertEqual(self.s.call("GET", f"/api/npcs/{ids[2]}")[0], 200)

        # 100 mises à jour avec de longs textes : résultat compact, sous la limite.
        ok, out = self.tool(token, "update", kind="npc", items=[{"id": i, "notes": "<p>" + "Long. " * 300 + "</p>"}
                                                                for i in ids[:99]] + [{"id": 0, "notes": "x"}])
        self.assertEqual(out["done"], 99)
        self.assertLess(len(json.dumps(out, ensure_ascii=False)), 40_000)

    def test_list_filters(self):
        """list : fiches filtrées (membres d'une faction, fiches liées à un lieu, sous-lieux, sous-factions, statut,
        étiquette, type, texte sans accents), pagination, filtres refusés ; commentaires, cartes, bibliothèque."""
        token = self.connect()["access_token"]
        s = self.s
        w = self.new_campaign("Listes Claude")
        mk = lambda path, **body: s.call("POST", f"/api/campaigns/{w}/{path}", body)[1]  # noqa: E731
        council, watch = mk("factions", name="Conseil"), mk("factions", name="Guet", kind="Milice")
        cell = mk("factions", name="Cellule")
        s.call("POST", "/api/memberships", {"member_faction_id": watch["id"], "faction_id": council["id"]})
        s.call("POST", "/api/memberships", {"member_faction_id": cell["id"], "faction_id": watch["id"]})
        orm = mk("npcs", name="Orm", tags=["garde"])
        ysa = mk("npcs", name="Ysa", status="mort")
        mk("npcs", name="Pell", tags=["Garde", "port"])
        mk("npcs", name="Élodie Brume", title="Passeuse")
        s.call("POST", "/api/memberships", {"npc_id": orm["id"], "faction_id": watch["id"], "is_leader": True})
        s.call("POST", "/api/memberships", {"npc_id": ysa["id"], "faction_id": council["id"], "role": "Doyenne"})
        port = mk("places", name="Port")
        inn = mk("places", name="Taverne", parent_id=port["id"])
        s.call("POST", f"/api/places/{inn['id']}/links", {"entity_kind": "npc", "entity_id": orm["id"],
                                                           "role": "tenancier"})
        s.call("POST", f"/api/places/{port['id']}/links", {"entity_kind": "faction", "entity_id": watch["id"],
                                                            "role": "repaire"})

        def names(**args):
            ok, out = self.tool(token, "list", campaign_id=w, **args)
            self.assertTrue(ok, out)
            return [i["name"] for i in out["items"]]
        self.assertEqual(sorted(names(kind="npc")), ["Orm", "Pell", "Ysa", "Élodie Brume"])
        ok, out = self.tool(token, "list", campaign_id=w, kind="npc", faction_id=watch["id"])
        self.assertEqual(out["items"], [{"id": orm["id"], "name": "Orm", "title": "", "status": "vivant",
                                         "tags": ["garde"], "role": "", "leader": True}])
        ok, out = self.tool(token, "list", campaign_id=w, kind="npc", faction_id=council["id"])
        self.assertEqual([(i["name"], i["role"]) for i in out["items"]], [("Ysa", "Doyenne")])
        self.assertEqual(names(kind="npc", status="mort"), ["Ysa"])
        self.assertEqual(sorted(names(kind="npc", tag="GARDE")), ["Orm", "Pell"])
        self.assertEqual(names(kind="npc", query="elodie"), ["Élodie Brume"])
        self.assertEqual(names(kind="npc", query="passeuse"), ["Élodie Brume"])
        ok, out = self.tool(token, "list", campaign_id=w, kind="npc", place_id=inn["id"])
        self.assertEqual([(i["name"], i["role"]) for i in out["items"]], [("Orm", "tenancier")])
        self.assertEqual(names(kind="faction", parent_id=0), ["Conseil"])
        self.assertEqual(names(kind="faction", faction_id=council["id"]), ["Guet"])
        self.assertEqual(names(kind="faction", parent_id=watch["id"]), ["Cellule"])
        self.assertEqual(names(kind="faction", type="milice"), ["Guet"])
        ok, out = self.tool(token, "list", campaign_id=w, kind="faction", place_id=port["id"])
        self.assertEqual([(i["name"], i["role"]) for i in out["items"]], [("Guet", "repaire")])
        self.assertEqual(names(kind="place", parent_id=0), ["Port"])
        self.assertEqual(names(kind="place", parent_id=port["id"]), ["Taverne"])
        self.assertEqual(names(kind="place", entity=f"npc:{orm['id']}"), ["Taverne"])
        self.assertEqual(names(kind="place", type="lieu"), ["Port", "Taverne"])
        # Pagination.
        ok, page = self.tool(token, "list", campaign_id=w, kind="npc", limit=3)
        self.assertEqual((page["total"], len(page["items"]), page["next_offset"]), (4, 3, 3))
        ok, page = self.tool(token, "list", campaign_id=w, kind="npc", offset=3, limit=3)
        self.assertEqual((len(page["items"]), page.get("next_offset")), (1, None))
        # Filtres refusés, références hors de la campagne.
        for args, expected in (({"kind": "npc", "parent_id": 1}, "sans objet"),
                               ({"kind": "map", "tag": "x"}, "sans objet"),
                               ({"kind": "npc", "faction_id": 999999}, "introuvable dans cette campagne"),
                               ({"kind": "place", "entity": "npc"}, "kind:id"),
                               ({"kind": "pnj"}, "kind"), ({"kind": "npc", "limit": 0}, "limit")):
            ok, err = self.tool(token, "list", campaign_id=w, **args)
            self.assertFalse(ok, args)
            self.assertIn(expected, err)
        self.assertIn("introuvable", self.tool(token, "list", campaign_id=self.closed_camp["id"], kind="npc")[1])

        # Commentaires de la campagne (fiches rédigées et fiches), filtrés.
        root = s.call("GET", f"/api/campaigns/{w}/documents")[1]["root_id"]
        ok, out = self.tool(token, "create", campaign_id=w, kind="comment", items=[
            {"target": f"doc:{root}", "body": "Revoir l'ouverture"},
            {"target": f"npc:{orm['id']}", "body": "Trop discret", "field": "personality"}])
        self.assertEqual(out["done"], 2, out)
        ok, out = self.tool(token, "list", campaign_id=w, kind="comment")
        self.assertEqual(sorted((c["on"], c["target"]["kind"], c["author"]) for c in out["items"]),
                         [("document", "doc", "Claude"), ("entry", "npc", "Claude")])
        ok, out = self.tool(token, "list", campaign_id=w, kind="comment", entity=f"npc:{orm['id']}")
        self.assertEqual([(c["target"]["name"], c["field"]) for c in out["items"]], [("Orm", "personality")])
        self.assertEqual(self.tool(token, "list", campaign_id=w, kind="comment", query="ouverture")[1]["total"], 1)
        self.assertEqual(self.tool(token, "list", campaign_id=w, kind="comment", status="resolved")[1]["total"], 0)
        self.assertEqual(self.tool(token, "list", campaign_id=w, kind="map")[1]["items"], [])
        self.assertEqual(self.tool(token, "list", campaign_id=w, kind="library")[1]["items"], [])

    def test_large_campaign(self):
        """Grosse campagne (recette : 684 PNJ, 427 factions) : get_campaign tient sous 40 000 caractères (arbres
        abrégés), ses sections et les listes se lisent page à page sans dépasser la limite."""
        from maze import mcp
        token = self.connect()["access_token"]
        w = self.new_campaign("Secteur Morvane", "read")
        summary = "Une longue présentation du personnage, de ses habitudes et de ses secrets. " * 3
        from maze import db
        with db.transaction(Path(self.s.tmp.name) / "maze.db") as conn:   # fonctions SQL des déclencheurs
            factions = [conn.execute("INSERT INTO factions (campaign_id, name, kind, summary, tags) VALUES (?, ?, ?, ?, ?)",
                                     (w, f"Faction des Brumes numéro {i}", "Guilde", summary, '["brumes"]')).lastrowid
                        for i in range(430)]
            for i, fid in enumerate(factions[30:], start=30):   # 30 factions de premier niveau, les autres dessous
                conn.execute("INSERT INTO memberships (member_faction_id, faction_id) VALUES (?, ?)",
                             (fid, factions[i % 30 if i < 130 else 30 + i % 100]))
            for i in range(700):
                npc = conn.execute("INSERT INTO npcs (campaign_id, name, title, summary, tags) VALUES (?, ?, ?, ?, ?)",
                                   (w, f"Personnage numéro {i} des Brumes", "Batelier du port", summary,
                                    '["port", "brumes"]')).lastrowid
                conn.execute("INSERT INTO memberships (npc_id, faction_id, role) VALUES (?, ?, 'Agent')",
                             (npc, factions[i % 430]))
            parent = None
            for i in range(300):
                parent = conn.execute("INSERT INTO places (campaign_id, parent_id, name) VALUES (?, ?, ?)",
                                      (w, parent if i % 10 else None, f"Lieu des Brumes {i}")).lastrowid
        for i in range(60):
            self.s.call("POST", f"/api/campaigns/{w}/documents", {"title": f"Scénario des Brumes {i}"})

        code, _, reply = self.rpc(token, "tools/call", {"name": "get_campaign", "arguments": {"campaign_id": w}})
        text = reply["result"]["content"][0]["text"]
        self.assertFalse(reply["result"]["isError"], text)
        self.assertLessEqual(len(text), mcp.RESULT_BUDGET)
        overview = json.loads(text)
        self.assertEqual({k: overview["totals"][k] for k in ("npcs", "factions", "places", "documents")},
                         {"npcs": 700, "factions": 430, "places": 300, "documents": 60})
        self.assertIn("abrégées", overview["note"])
        self.assertNotIn("npcs", overview)
        self.assertEqual(overview["tags"][0], {"tag": "brumes", "count": 1130})
        self.assertEqual(len(overview["factions"]), 30)   # premier niveau en entier, sous-niveaux abrégés
        self.assertTrue(any("more" in f or "sub" in f for f in overview["factions"]))
        self.assertEqual(len(overview["documents"]["tree"]), 60)

        for section, total in (("npcs", 700), ("factions", 430), ("places", 300), ("documents", 61)):
            seen, offset, pages = 0, 0, 0
            while offset is not None:
                code, _, reply = self.rpc(token, "tools/call", {"name": "get_campaign", "arguments": {
                    "campaign_id": w, "section": section, "offset": offset, "limit": 200}})
                text = reply["result"]["content"][0]["text"]
                self.assertLessEqual(len(text), mcp.RESULT_BUDGET + 500, section)
                page = json.loads(text)
                self.assertEqual(page["total"], total)
                seen += len(page["items"])
                offset, pages = page.get("next_offset"), pages + 1
            self.assertEqual(seen, total, section)
            self.assertGreater(pages, 1 if section != "documents" else 0, section)
        ok, page = self.tool(token, "list", campaign_id=w, kind="npc", limit=200, tag="port")
        self.assertEqual((page["total"], page["next_offset"]), (700, len(page["items"])))
        self.assertGreater(len(page["items"]), 150)   # page raccourcie s'il le faut pour tenir dans le budget
        self.assertLessEqual(len(json.dumps(page, ensure_ascii=False)), mcp.RESULT_BUDGET + 500)
        ok, page = self.tool(token, "list", campaign_id=w, kind="faction", parent_id=0, limit=200)
        self.assertEqual(page["total"], 30)

    def test_live_document_guard(self):
        token = self.connect()["access_token"]
        w = self.write_camp["id"]
        read_doc = lambda did: self.tool(token, "get", kind="document", id=did)  # noqa: E731
        _, doc = self.s.call("POST", f"/api/campaigns/{w}/documents", {"title": "Ouverte", "content": "<p>Départ</p>"})
        ws = self.s.admin.live(doc["id"], 909)
        recv_until(ws, "welcome")
        ws.send(bytes([0]) + b"frappe")  # modification reçue, pas encore dans le HTML enregistré
        self.assertEqual(recv(ws)[0], 3)
        ok, read = read_doc(doc["id"])
        ok, err = self.tool(token, "update_document", document_id=doc["id"], base_revision=read["revision"],
                            content="<p>Par Claude</p>")
        self.assertFalse(ok)
        self.assertIn("La fiche est ouverte", err)
        ws.send(json.dumps({"t": "content", "html": "<p>Départ et frappe</p>", "upto": 1}))
        recv_until(ws, "meta")
        ok, read = read_doc(doc["id"])
        self.assertIn("frappe", read["content"])
        ok, upd = self.tool(token, "update_document", document_id=doc["id"], base_revision=read["revision"],
                            content="<p>Par Claude</p>")
        self.assertTrue(ok, upd)
        recv_until(ws, "reset")  # l'éditeur ouvert recharge la fiche
        ws.close()

        # Dernier éditeur parti avant que son HTML ne soit enregistré : refus, même sans fiche ouverte.
        _, doc = self.s.call("POST", f"/api/campaigns/{w}/documents", {"title": "Quittée", "content": "<p>A</p>"})
        ws = self.s.admin.live(doc["id"], 910)
        recv_until(ws, "welcome")
        ws.send(bytes([0]) + b"frappe")
        self.assertEqual(recv(ws)[0], 3)
        ws.close()
        wait_until(lambda: doc["id"] not in self.s.server.config.app.state.hub.rooms)   # départ traité (QUAL-9)
        ok, read = read_doc(doc["id"])
        ok, err = self.tool(token, "update_document", document_id=doc["id"], base_revision=read["revision"],
                            content="<p>Par Claude</p>")
        self.assertFalse(ok)
        self.assertIn("ouverte dans Maze", err)

        # Après une compaction, le HTML du meneur la couvre : Claude n'est pas bloqué indéfiniment.
        _, doc = self.s.call("POST", f"/api/campaigns/{w}/documents", {"title": "Longue", "content": "<p>B</p>"})
        ws = self.s.admin.live(doc["id"], 911)
        recv_until(ws, "welcome")
        last = 0
        for i in range(300):
            ws.send(bytes([0]) + f"frappe-{i}".encode())
            while True:
                msg = recv(ws)
                if isinstance(msg, bytes) and msg[0] == 3:
                    last = seq_of(msg)
                    break
        recv_until(ws, "compact")
        # Trames d'une même connexion traitées dans l'ordre : compaction enregistrée avant le HTML (sans attente).
        ws.send(bytes([2]) + last.to_bytes(8, "big") + b"etat-complet")
        ws.send(json.dumps({"t": "content", "html": "<p>B et 300 frappes</p>", "upto": last}))
        recv_until(ws, "meta")
        ok, read = read_doc(doc["id"])
        ok, upd = self.tool(token, "update_document", document_id=doc["id"], base_revision=read["revision"],
                            content="<p>Par Claude</p>")
        self.assertTrue(ok, upd)
        ws.close()

    def test_overview_helpers(self):
        """Arbre abrégé (premiers niveaux d'abord, more : enfants non affichés, nœud à deux parents une fois) et page
        raccourcie pour tenir dans le budget."""
        from maze import mcp
        nodes = {i: {"id": i, "name": f"Nœud {i}"} for i in range(1, 8)}
        kids = {1: [3, 4], 2: [4, 5], 3: [6, 7]}
        tree, shown, more = mcp._tree(nodes, kids, [1, 2], 10_000)
        self.assertEqual((shown, more), (7, 0))
        self.assertEqual([n["id"] for n in tree[0]["sub"]], [3, 4])
        self.assertEqual([n["id"] for n in tree[1]["sub"]], [5])   # 4 déjà sous 1
        tree, shown, more = mcp._tree(nodes, kids, [1, 2], 4 * (mcp._size(nodes[1]) + 12))
        self.assertEqual((shown, more), (4, 0))
        self.assertEqual((tree[0].get("more"), tree[1]["more"], tree[0]["sub"][0]["more"]), (None, 1, 2))
        tree, shown, more = mcp._tree(nodes, kids, [1, 2], 1)
        self.assertEqual((tree, shown, more), ([], 0, 2))
        page = mcp._page([{"x": "y" * 100}] * 500, 0, 200, budget=5_000)
        self.assertLessEqual(mcp._size(page["items"]), 5_000)
        self.assertEqual(page["next_offset"], len(page["items"]))
        self.assertIn("raccourcie", page["note"])
        self.assertEqual(mcp._trash_id({"trash_id": 7}), 7)
        self.assertIsNone(mcp._trash_id(None))

    def test_writes_refused_during_restore(self):
        """Contre-revue M7 : les outils d'écriture du connecteur (appels internes ctx.api, hors des middlewares HTTP)
        sont refusés pendant une restauration ; les outils de lecture marchent."""
        from maze import backup
        token = self.connect()["access_token"]
        ok, npc = self.one(token, "create", campaign_id=self.write_camp["id"], kind="npc", item={"name": "Gardien"})
        self.assertTrue(ok, npc)
        with backup.restore_marker(Path(self.s.tmp.name)):
            ok, err = self.one(token, "update", kind="npc", item={"id": npc["id"], "notes": "<p>Perdu</p>"})
            self.assertFalse(ok)
            self.assertIn("Restauration en cours", err)
            ok, read = self.tool(token, "get", kind="npc", id=npc["id"])
            self.assertTrue(ok, read)
        self.assertEqual(self.s.call("GET", f"/api/npcs/{npc['id']}")[1]["notes"], "")
        ok, out = self.one(token, "update", kind="npc", item={"id": npc["id"], "notes": "<p>Après</p>"})
        self.assertTrue(ok, out)

    def test_update_entry_revision(self):
        """Contre-revue M1 : update (npc, faction, place) accepte base_revision (révision de get ou du dernier update).
        Partis de la révision à jour, les appels successifs de Claude forment une seule version de l'historique (une
        version par appel évincerait celles des MJ, 50 au plus par fiche) ; révision dépassée : refus, rien
        d'écrit. Sans elle : comme avant, jamais de fusion."""
        token = self.connect()["access_token"]
        ok, npc = self.one(token, "create", campaign_id=self.write_camp["id"], kind="npc",
                           item={"name": "Archiviste Hale", "notes": "<p>Départ</p>"})
        self.assertTrue(ok, npc)
        url = f"/api/npcs/{npc['id']}/versions"
        ok, read = self.tool(token, "get", kind="npc", id=npc["id"])
        rev = read["revision"]
        for i in range(4):
            ok, out = self.one(token, "update", kind="npc", item={"id": npc["id"], "base_revision": rev,
                                                                  "notes": f"<p>Passe {i} de Claude</p>"})
            self.assertTrue(ok, out)
            self.assertEqual(out["revision"], rev + 1)
            rev = out["revision"]
        versions = self.s.call("GET", url)[1]["versions"]
        self.assertEqual([(v["label"], v["author_name"]) for v in versions], [("", "Claude"), ("État initial", "")])
        # Un MJ modifie la fiche : la révision de Claude est dépassée, refus sans rien écrire.
        self.assertEqual(self.s.call("PATCH", f"/api/npcs/{npc['id']}", {"notes": "<p>Texte du MJ</p>"})[0], 200)
        ok, err = self.one(token, "update", kind="npc", item={"id": npc["id"], "base_revision": rev,
                                                              "notes": "<p>Écrase le MJ</p>"})
        self.assertFalse(ok)
        self.assertIn("modifiée entre-temps", err)
        self.assertEqual(self.s.call("GET", f"/api/npcs/{npc['id']}")[1]["notes"], "<p>Texte du MJ</p>")
        ok, err = self.one(token, "update", kind="npc", item={"id": npc["id"], "base_revision": "x", "notes": "a"})
        self.assertIn("entier attendu", err)
        # Sans base_revision : accepté, une version par appel.
        before = len(self.s.call("GET", url)[1]["versions"])
        for i in range(2):
            self.assertTrue(self.one(token, "update", kind="npc",
                                     item={"id": npc["id"], "notes": f"<p>Sans révision {i}</p>"})[0])
        self.assertEqual(len(self.s.call("GET", url)[1]["versions"]), before + 2)

    def test_images(self):
        from maze import fetch
        from PIL import Image
        token = self.connect()["access_token"]
        portrait = png_bytes((30, 120, 200), (600, 400))
        huge = png_bytes((128, 128, 128), (7000, 7000))  # 49 Mpx, très compressée

        class Images(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/lent.png":
                    self.send_response(200)
                    self.end_headers()
                    for _ in range(8):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(0.3)
                    return
                if self.path == "/geante.png":
                    body = huge
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/portrait.png")
                    self.end_headers()
                    return
                body = portrait if self.path == "/portrait.png" else b"pas une image"
                self.send_response(200 if self.path != "/absent.png" else 404)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Images)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        self.addCleanup(httpd.shutdown)
        w = self.new_campaign("Images")
        ok, npc = self.one(token, "create", campaign_id=w, kind="npc", item={"name": "Modèle"})
        target = f"npc:{npc['id']}"
        add = lambda **item: self.one(token, "add_image", item={"target": target, **item})  # noqa: E731
        gallery = lambda: self.tool(token, "get", kind="npc", id=npc["id"])[1]["images"]  # noqa: E731

        # Adresse interne : refusée tant que les adresses privées ne sont pas autorisées (SSRF).
        for internal in ("http://127.0.0.1/portrait.png", "http://10.0.0.10/a.png", "http://[::1]/a.png",
                         "http://169.254.169.254/latest/meta-data/"):
            ok, err = add(url=internal)
            self.assertIn("non publique", err)
        ok, err = add(url=f"{base}/portrait.png")
        self.assertIn("Ports 80 et 443", err)
        for bad in ("file:///etc/passwd", "ftp://example.org/a.png", "http://user:pw@example.org/a.png"):
            self.assertFalse(add(url=bad)[0])
        fetch.ALLOW_PRIVATE = True
        self.addCleanup(setattr, fetch, "ALLOW_PRIVATE", False)

        ok, added = add(url=f"{base}/redirect", caption="Profil")
        self.assertTrue(ok, added)
        first = added["id"]
        self.assertEqual((added["width"], added["height"], added["cover"]), (600, 400, True))
        self.assertEqual(gallery(), [{"id": first, "caption": "Profil", "width": 600, "height": 400, "cover": True}])
        ok, err = add(url=f"{base}/texte.png")
        self.assertIn("Image refusée", err)
        ok, err = add(url=f"{base}/absent.png")
        self.assertIn("404", err)
        ok, err = add(url=f"{base}/geante.png")
        self.assertIn("trop grande", err)
        ok, err = add(url=f"{base}/château é.png")
        self.assertIn("Image refusée", err)  # adresse encodée, puis contenu non image
        ok, err = add(url="http://[::1/x")
        self.assertIn("invalide", err)
        self.addCleanup(setattr, fetch, "TOTAL_SECONDS", fetch.TOTAL_SECONDS)
        fetch.TOTAL_SECONDS = 1
        started = time.monotonic()
        ok, err = add(url=f"{base}/lent.png")
        self.assertIn("trop long", err)
        self.assertLess(time.monotonic() - started, 2)
        fetch.TOTAL_SECONDS = 30
        ok, err = add(url=f"{base}/portrait.png", image_id=first)
        self.assertIn("une seule", err)
        ok, err = add()
        self.assertIn("une seule", err)
        ok, err = self.one(token, "add_image", item={"target": f"npc:{self.read_npc['id']}", "url": f"{base}/portrait.png"})
        self.assertIn("lecture seule", err)
        ok, err = self.one(token, "add_image", item={"target": "carte:1", "url": f"{base}/portrait.png"})
        self.assertIn("target", err)

        # Voir : bloc texte + bloc image JPEG.
        code, _, reply = self.rpc(token, "tools/call", {"name": "view", "arguments": {"kind": "image", "id": first}})
        content = reply["result"]["content"]
        self.assertEqual([b["type"] for b in content], ["text", "image"])
        self.assertEqual(content[1]["mimeType"], "image/jpeg")
        with Image.open(io.BytesIO(base64.b64decode(content[1]["data"]))) as seen:
            self.assertEqual(seen.size, (600, 400))
        self.assertTrue(json.loads(content[0]["text"])["cover"])

        # Copie d'une image de Maze (ancien nom from_image_id accepté), couverture, légende et ordre.
        ok, second = add(from_image_id=first, set_cover=True)
        self.assertTrue(ok, second)
        sid = second["id"]
        self.assertEqual([i["cover"] for i in gallery()], [False, True])
        ok, upd = self.one(token, "update", kind="image", item={"id": sid, "position": 0, "caption": "Copie"})
        self.assertTrue(ok, upd)
        self.assertEqual([(i["id"], i["cover"], i["caption"]) for i in gallery()],
                         [(sid, True, "Copie"), (first, False, "Profil")])

        # Retouche : pivot puis recadrage ; la retouche prend la place de l'originale, gardée en fin.
        ok, edited = self.one(token, "edit_image", item={"id": sid, "rotate": 90,
                                                         "crop": {"left": 0, "top": 0, "right": 100, "bottom": 50}})
        self.assertTrue(ok, edited)
        self.assertEqual((edited["width"], edited["height"], edited["original_id"]), (400, 300, sid))
        self.assertEqual(edited["gallery"], [edited["id"], first, sid])
        self.assertTrue(gallery()[0]["cover"])
        ok, err = self.one(token, "edit_image", item={"id": first, "crop": {"left": 50, "top": 0, "right": 40,
                                                                           "bottom": 10}})
        self.assertFalse(ok)
        self.assertIn("Rien à faire", self.one(token, "edit_image", item={"id": first})[1])
        self.assertIn("flip", self.one(token, "edit_image", item={"id": first, "flip": {"x": 1}})[1])
        ok, replaced = self.one(token, "edit_image", item={"image_id": first, "flip": "horizontal",
                                                           "keep_original": False})
        self.assertNotIn(first, replaced["gallery"])

        # Suppression : la couverture passe à l'image suivante.
        ok, gone = self.one(token, "delete", kind="image", item={"id": edited["id"]})
        self.assertTrue(ok, gone)
        self.assertTrue(gallery()[0]["cover"])
        self.assertFalse(self.tool(token, "view", kind="image", id=edited["id"])[0])

        # Fiche rédigée : illustration puis insertion dans le contenu.
        ok, doc = self.one(token, "create", campaign_id=w, kind="document", item={"title": "Illustré",
                                                                                 "content": "<p>Avant</p>"})
        ok, pic = self.one(token, "add_image", item={"target": f"doc:{doc['id']}", "url": f"{base}/portrait.png",
                                                     "alt": 'Le "port"'})
        self.assertTrue(ok, pic)
        self.assertIn(f'data-doc-image="{pic["id"]}"', pic["html"])
        self.assertIn("&quot;port&quot;", pic["html"])
        ok, read = self.tool(token, "get", kind="document", id=doc["id"])
        ok, upd = self.tool(token, "update_document", document_id=doc["id"], base_revision=read["revision"],
                            content="<p>Avant</p>" + pic["html"])
        self.assertTrue(ok, upd)
        code, _, reply = self.rpc(token, "tools/call", {"name": "view", "arguments": {"kind": "document_image",
                                                                                     "id": pic["id"]}})
        self.assertEqual(reply["result"]["content"][1]["type"], "image")
        ok, copy = add(document_image_id=pic["id"])
        self.assertTrue(ok, copy)

        # Campagne non ouverte : rien de visible.
        self.assertFalse(self.one(token, "add_image", item={"target": f"npc:{self.hidden_npc['id']}",
                                                            "url": f"{base}/portrait.png"})[0])
        # Bibliothèque de la campagne (déposée par le MJ dans l'administration, ou par Claude).
        body, ctype = multipart([("carte.png", png_bytes((90, 60, 30), (500, 300)))])
        self.assertEqual(self.s.call("POST", f"/api/admin/campaigns/{w}/library", raw=body, content_type=ctype)[0], 201)
        library = lambda: self.tool(token, "list", campaign_id=w, kind="library")[1]["items"]  # noqa: E731
        lib_id = library()[0]["id"]
        self.s.call("PATCH", f"/api/admin/library/{lib_id}", {"caption": "Carte du port"})
        code, _, reply = self.rpc(token, "tools/call", {"name": "view", "arguments": {"kind": "library_image",
                                                                                     "id": lib_id}})
        self.assertEqual(reply["result"]["content"][1]["type"], "image")
        ok, from_lib = add(library_image_id=lib_id)
        self.assertTrue(ok, from_lib)
        self.assertEqual(next(i for i in gallery() if i["id"] == from_lib["id"])["caption"], "Carte du port")
        self.assertTrue(self.one(token, "delete", kind="library_image", item={"id": lib_id})[0])
        self.assertEqual(library(), [])
        ok, out = self.tool(token, "add_image", campaign_id=w, items=[
            {"target": "library", "url": f"{base}/portrait.png", "caption": "Déposée par Claude"},
            {"target": f"library:{w}", "image_id": from_lib["id"]},
            {"target": "library", "url": f"{base}/texte.png"}])
        self.assertEqual([r["ok"] for r in out["results"]], [True, True, False])
        self.assertEqual(sorted((i["caption"], i["width"]) for i in library()),
                         [("Carte du port", 500), ("Déposée par Claude", 600)])
        # Plusieurs d'un coup : chaque image à part, les absentes en erreur.
        pair = sorted(i["id"] for i in library())
        ok, out = self.tool(token, "delete", kind="library_image",
                            items=[{"id": pair[1]}, {"id": 999999}, {"id": pair[0]}, {"id": pair[1]}])
        self.assertTrue(ok, out)
        self.assertEqual([r["ok"] for r in out["results"]], [True, False, True, False])
        self.assertIn("introuvable", out["results"][1]["error"])
        self.assertEqual(library(), [])

        # Images de campagnes en lecture seule ou non ouvertes.
        ok, ro_img = add(url=f"{base}/portrait.png")
        _, ro = self.s.call("POST", "/api/campaigns", {"name": "Lecture images"})
        _, ro_npc = self.s.call("POST", f"/api/campaigns/{ro['id']}/npcs", {"name": "Lu"})
        _, closed = self.s.call("POST", "/api/campaigns", {"name": "Fermée images"})
        _, closed_npc = self.s.call("POST", f"/api/campaigns/{closed['id']}/npcs", {"name": "Caché"})
        self.grant(ro["id"], "read")
        self.addCleanup(self.grant, ro["id"], "none")
        ok, err = self.one(token, "add_image", item={"target": f"npc:{ro_npc['id']}", "image_id": ro_img["id"]})
        self.assertIn("lecture seule", err)
        with sqlite3.connect(Path(self.s.tmp.name) / "maze.db") as db:
            for owner in (ro_npc["id"], closed_npc["id"]):
                camp = ro["id"] if owner == ro_npc["id"] else closed["id"]
                db.execute("INSERT INTO images (campaign_id, owner_kind, owner_id, file, thumb, width, height) "
                           "SELECT ?, 'npc', ?, file, thumb, width, height FROM images WHERE id = ?",
                           (camp, owner, ro_img["id"]))
            ro_id, closed_id = [r[0] for r in db.execute(
                "SELECT id FROM images WHERE owner_id IN (?, ?) ORDER BY owner_id", (ro_npc["id"], closed_npc["id"]))]
        self.assertTrue(self.tool(token, "view", kind="image", id=ro_id)[0])
        for name, extra in (("update", {"caption": "x"}), ("edit_image", {"rotate": 90}), ("delete", {})):
            kind = {} if name == "edit_image" else {"kind": "image"}
            self.assertIn("lecture seule", self.one(token, name, item={"id": ro_id, **extra}, **kind)[1])
            self.assertIn("introuvable", self.one(token, name, item={"id": closed_id, **extra}, **kind)[1])
        self.assertIn("introuvable", self.tool(token, "view", kind="image", id=closed_id)[1])
        self.assertIn("introuvable", add(image_id=closed_id)[1])
        body, ctype = multipart([("x.png", png_bytes())])
        for camp_id in (ro["id"], closed["id"]):
            self.s.call("POST", f"/api/admin/campaigns/{camp_id}/library", raw=body, content_type=ctype)
        with sqlite3.connect(Path(self.s.tmp.name) / "maze.db") as db:
            ro_lib, closed_lib = [r[0] for r in db.execute(
                "SELECT id FROM library_images WHERE campaign_id IN (?, ?) ORDER BY campaign_id", (ro["id"], closed["id"]))]
        self.assertTrue(self.tool(token, "view", kind="library_image", id=ro_lib)[0])
        self.assertIn("lecture seule", self.one(token, "delete", kind="library_image", item={"id": ro_lib})[1])
        self.assertIn("introuvable", self.one(token, "delete", kind="library_image", item={"id": closed_lib})[1])
        self.assertIn("lecture seule", self.one(token, "add_image", item={"target": f"library:{ro['id']}",
                                                                          "library_image_id": ro_lib})[1])
        self.assertTrue(self.tool(token, "view", kind="library_image", id=ro_lib)[0])   # rien supprimé
        self.assertIn("introuvable", self.tool(token, "list", campaign_id=closed["id"], kind="library")[1])
        self.assertIn("introuvable", self.tool(token, "view", kind="library_image", id=closed_lib)[1])

        _, state = self.s.call("GET", "/api/admin/claude")
        self.assertIn("edit_image", [e["tool"] for e in state["log"]])
        self.assertTrue(any(e["target"].endswith("< 127.0.0.1") for e in state["log"]))

    def test_busy_database_says_retry(self):
        """Contre-revue : base occupée (verrou d'écriture tenu par un import, une restauration…) : un outil d'écriture
        répond « réessayez » (et non « Erreur interne »), un outil de lecture répond sans attendre le verrou."""
        from unittest import mock
        from maze import db
        from tests.test_concurrency import LockHolder
        token = self.connect()["access_token"]
        _, npc = self.s.call("POST", f"/api/campaigns/{self.write_camp['id']}/npcs", {"name": "Verrouillée"})
        with mock.patch.object(db, "BUSY_TIMEOUT", 0.3), LockHolder(Path(self.s.tmp.name) / "maze.db", 2.0):
            start = time.perf_counter()
            ok, data = self.tool(token, "get", kind="npc", id=npc["id"])
            self.assertTrue(ok, data)
            self.assertLess(time.perf_counter() - start, 1.5)
            ok, err = self.one(token, "update", kind="npc", item={"id": npc["id"], "summary": "pendant le verrou"})
            self.assertFalse(ok)
            self.assertIn("réessayez", err)
        ok, data = self.one(token, "update", kind="npc", item={"id": npc["id"], "summary": "après"})
        self.assertTrue(ok, data)
        self.assertEqual(self.s.call("DELETE", f"/api/npcs/{npc['id']}")[0], 200)   # les autres tests comptent les PNJ

    def test_timeline(self):
        """Chronologie (migration 032) : list, get, create et update (kind event) ; get_campaign indique l'activation et
        le nombre d'événements ; écriture refusée tant que la chronologie est désactivée ou la campagne en lecture."""
        token = self.connect()["access_token"]
        w = self.new_campaign("Chronologie Claude")
        _, npc = self.s.call("POST", f"/api/campaigns/{w}/npcs", {"name": "Inquisitrice Vael"})
        create = lambda **item: self.one(token, "create", campaign_id=w, kind="event", item=item)  # noqa: E731
        update = lambda **item: self.one(token, "update", kind="event", item=item)  # noqa: E731
        get = lambda eid: self.tool(token, "get", kind="event", id=eid)[1]["event"]  # noqa: E731

        ok, overview = self.tool(token, "get_campaign", campaign_id=w)
        self.assertEqual(overview["timeline"], {"enabled": False, "events": 0})
        ok, err = create(title="Fondation")
        self.assertFalse(ok)
        self.assertIn("n'est pas activée", err)
        self.assertEqual(self.s.call("PATCH", f"/api/campaigns/{w}", {"timeline_enabled": True})[0], 200)

        ok, first = create(title="Fondation", date_label="vers 850.M39", tags=["origine"],
                           description="La cité naît sous **[[Inquisitrice Vael]]**.")
        self.assertTrue(ok, first)
        self.assertEqual(first["position"], 0)
        self.assertEqual(first["mentions"], [{"kind": "npc", "id": npc["id"], "name": "Inquisitrice Vael"}])
        ok, last = create(title="Le siège")
        ok, middle = create(title="Le schisme", before_id=last["id"],
                            description='<p>Rupture</p><div data-callout="secret"><p>Trahison</p></div>')
        self.assertEqual(middle["position"], 1)
        self.assertIn("Champs inconnus", create(title="X", when="hier")[1])

        ok, listing = self.tool(token, "list", campaign_id=w, kind="event")
        self.assertTrue(ok, listing)
        self.assertEqual([e["title"] for e in listing["items"]], ["Fondation", "Le schisme", "Le siège"])
        self.assertEqual((listing["enabled"], listing["total"]), (True, 3))
        self.assertIn("Inquisitrice Vael", listing["items"][0]["excerpt"])
        self.assertEqual(listing["items"][0]["date_label"], "vers 850.M39")
        ok, page = self.tool(token, "list", campaign_id=w, kind="event", limit=2)
        self.assertEqual((len(page["items"]), page["next_offset"]), (2, 2))
        ok, cited = self.tool(token, "list", campaign_id=w, kind="event", entity=f"npc:{npc['id']}")
        self.assertEqual([e["title"] for e in cited["items"]], ["Fondation"])
        self.assertEqual(self.tool(token, "list", campaign_id=w, kind="event", tag="ORIGINE")[1]["total"], 1)
        self.assertIn('data-callout="secret"', get(middle["id"])["description"])
        self.assertEqual(get(middle["id"])["position"], 1)

        # Modification avec révision, déplacement, conflit.
        ok, changed = update(id=first["id"], base_revision=first["revision"], title="Fondation d'Ys", to_end=True)
        self.assertTrue(ok, changed)
        self.assertEqual((changed["title"], changed["position"]), ("Fondation d'Ys", 2))
        self.assertGreater(changed["revision"], first["revision"])
        self.assertFalse(update(id=first["id"], base_revision=first["revision"], title="X")[0])
        ok, moved = update(id=first["id"], before_id=middle["id"])
        self.assertEqual(moved["position"], 0)
        self.assertEqual(moved["revision"], changed["revision"])   # déplacer ne change pas la révision
        self.assertIn("Rien à modifier", update(id=first["id"])[1])
        # Déplacement invalide : refusé AVANT toute écriture (revue : le titre restait modifié).
        for bad in (999999, first["id"]):
            self.assertFalse(update(id=first["id"], title="Modifié malgré l'erreur", before_id=bad)[0])
        self.assertNotEqual(get(first["id"])["title"], "Modifié malgré l'erreur")
        # Déplacement seul avec une révision périmée : refusé.
        ok, err = update(id=first["id"], base_revision=first["revision"], to_end=True)
        self.assertFalse(ok)
        self.assertIn("modifié entre-temps", err)

        ok, overview = self.tool(token, "get_campaign", campaign_id=w)
        self.assertEqual(overview["timeline"], {"enabled": True, "events": 3})
        ok, found = self.tool(token, "search", query="schisme", campaign_id=w)
        self.assertIn(("event", middle["id"]), [(r["kind"], r["id"]) for r in found["results"]])

        # Droits : lecture seule, campagne fermée ; désactivée : lecture permise (note), écriture refusée.
        self.grant(w, "read")
        self.assertIn("lecture seule", update(id=first["id"], title="Y")[1])
        self.grant(w, "write")
        self.assertIn("introuvable", self.tool(token, "list", campaign_id=self.closed_camp["id"], kind="event")[1])
        self.s.call("PATCH", f"/api/campaigns/{w}", {"timeline_enabled": False})
        ok, listing = self.tool(token, "list", campaign_id=w, kind="event")
        self.assertEqual((listing["enabled"], listing["total"]), (False, 3))
        self.assertIn("n'est pas activée", listing["note"])
        self.assertIn("n'est pas activée", update(id=first["id"], title="Z")[1])

    def test_delete_tools(self):
        """Niveau d'accès « suppression » (migration 033) : delete (npc, faction, place, document, event, map,
        comment) refusé en lecture et en écriture, permis en suppression ; nom ou titre exact exigé ; journal ;
        relation, image, library_image restent permis en écriture ; trash_id quand la route en renvoie un."""
        from tests.test_maps import form
        token = self.connect()["access_token"]
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Suppressions Claude"})
        w = camp["id"]
        code, state = s.call("PUT", "/api/admin/claude", {"grant": {"campaign_id": w, "mode": "delete"}})
        self.assertEqual(code, 200, state)
        self.assertEqual({g["campaign_id"]: g["mode"] for g in state["grants"]}[w], "delete")
        self.addCleanup(self.grant, w, "none")
        self.assertEqual(s.call("PUT", "/api/admin/claude", {"grant": {"campaign_id": w, "mode": "tout"}})[0], 422)
        s.call("PATCH", f"/api/campaigns/{w}", {"timeline_enabled": True})
        # Corbeille (lot corbeille) : la route DELETE répond 200 {"trash_id"} ; sinon 204 (définitif).
        _, probe = s.call("POST", f"/api/campaigns/{w}/npcs", {"name": "Sonde"})
        trash = s.call("DELETE", f"/api/npcs/{probe['id']}")[0] == 200

        _, npc = s.call("POST", f"/api/campaigns/{w}/npcs", {"name": "Inquisitrice Vael"})
        _, faction = s.call("POST", f"/api/campaigns/{w}/factions", {"name": "Ordo Hereticus"})
        _, sector = s.call("POST", f"/api/campaigns/{w}/places", {"name": "Morvane"})
        _, world = s.call("POST", f"/api/campaigns/{w}/places", {"name": "Valdis", "parent_id": sector["id"]})
        _, scenario = s.call("POST", f"/api/campaigns/{w}/documents", {"title": "Acte I"})
        _, scene = s.call("POST", f"/api/campaigns/{w}/documents", {"title": "Scène 1", "parent_id": scenario["id"]})
        root_id = s.call("GET", f"/api/campaigns/{w}/documents")[1]["root_id"]
        _, event = s.call("POST", f"/api/campaigns/{w}/events", {"title": "Fondation"})
        body, ctype = form({"name": "Secteur"}, ("fond.png", png_bytes()))
        _, smap = s.call("POST", f"/api/campaigns/{w}/maps", raw=body, content_type=ctype)
        _, relation = s.call("POST", f"/api/campaigns/{w}/relations", {
            "source_kind": "npc", "source_id": npc["id"], "target_kind": "faction", "target_id": faction["id"]})
        ok, thread = self.one(token, "create", campaign_id=w, kind="comment",
                              item={"target": f"doc:{root_id}", "body": "À revoir"})
        self.assertTrue(ok, thread)
        ok, reply = self.one(token, "create", campaign_id=w, kind="comment",
                             item={"target": f"doc:{root_id}", "body": "D'accord", "parent_id": thread["id"]})
        ok, entry_comment = self.one(token, "create", campaign_id=w, kind="comment",
                                     item={"target": f"faction:{faction['id']}", "body": "Trop vague"})
        self.assertTrue(ok, entry_comment)

        calls = [
            ("npc", {"id": npc["id"], "name": "Inquisitrice Vael"}),
            ("place", {"id": sector["id"], "name": "Morvane"}),
            ("document", {"id": scenario["id"], "name": "Acte I"}),
            ("event", {"id": event["id"], "name": "Fondation"}),
            ("map", {"id": smap["id"], "name": smap["name"]}),
            ("comment", {"id": thread["id"], "on": "document"}),
            ("comment", {"id": entry_comment["id"], "on": "entry"}),
        ]
        for mode in ("write", "read"):
            self.grant(w, mode)
            for kind, item in calls:
                ok, err = self.one(token, "delete", kind=kind, item=item)
                self.assertFalse(ok, (mode, kind))
                self.assertIn("La campagne « Suppressions Claude » n'autorise pas Claude à supprimer", err)
        self.assertEqual(s.call("GET", f"/api/npcs/{npc['id']}")[0], 200)
        # Les suppressions de niveau écriture restent permises en écriture.
        self.grant(w, "write")
        self.assertTrue(self.one(token, "delete", kind="relation", item={"id": relation["id"]})[0])

        self.grant(w, "delete")
        ok, listing = self.tool(token, "list_campaigns")
        self.assertEqual({c["id"]: c["access"] for c in listing["campaigns"]}[w], "lecture, écriture et suppression")
        self.assertEqual(self.tool(token, "get_campaign", campaign_id=w)[1]["campaign"]["access"],
                         "lecture, écriture et suppression")
        self.assertTrue(self.one(token, "update", kind="npc", item={"id": npc["id"], "title": "Inquisitrice"})[0])

        # Nom ou titre différent (casse comprise) : rien n'est supprimé.
        ok, err = self.one(token, "delete", kind="npc", item={"id": npc["id"], "name": "inquisitrice vael"})
        self.assertFalse(ok)
        self.assertIn(f"Nom différent : la fiche n°{npc['id']} s'appelle « Inquisitrice Vael »", err)
        self.assertEqual(s.call("GET", f"/api/npcs/{npc['id']}")[0], 200)
        ok, err = self.one(token, "delete", kind="document", item={"id": scenario["id"], "title": "Acte 1"})
        self.assertIn("Acte I", err)
        self.assertEqual(s.call("GET", f"/api/documents/{scenario['id']}")[0], 200)
        # Fiche invisible pour Claude, type inconnu.
        ok, err = self.one(token, "delete", kind="npc", item={"id": self.hidden_npc["id"], "name": "Invisible Varnak"})
        self.assertIn("introuvable", err)
        self.assertEqual(s.call("GET", f"/api/npcs/{self.hidden_npc['id']}")[0], 200)
        self.assertFalse(self.one(token, "delete", kind="comment", item={"id": thread["id"], "on": "fiche"})[0])
        self.assertFalse(self.tool(token, "delete", kind="campaign", items=[{"id": w}])[0])

        ok, out = self.one(token, "delete", kind="npc", item={"id": npc["id"], "name": " Inquisitrice Vael "})
        self.assertTrue(ok, out)
        self.assertEqual("trash_id" in out, trash)
        self.assertIn("corbeille" if trash else "supprimé", out["message"])
        self.assertEqual(s.call("GET", f"/api/npcs/{npc['id']}")[0], 404)
        ok, out = self.one(token, "delete", kind="comment", item={"id": entry_comment["id"], "on": "entry"})
        self.assertTrue(ok, out)
        self.assertNotIn("trash_id", out)
        self.assertEqual(s.call("GET", f"/api/factions/{faction['id']}/comments")[1], [])
        # Plusieurs kinds d'un coup ne se mélangent pas, mais plusieurs éléments si : faction et lieu à part.
        self.assertTrue(self.one(token, "delete", kind="faction", item={"id": faction["id"], "name": "Ordo Hereticus"})[0])
        # Lieu : ses sous-lieux remontent d'un niveau.
        ok, out = self.one(token, "delete", kind="place", item={"id": sector["id"], "name": "Morvane"})
        self.assertTrue(ok, out)
        self.assertIn("Ses sous-lieux (1) remontent d'un niveau", out["message"])
        self.assertIsNone(s.call("GET", f"/api/places/{world['id']}")[1]["parent_id"])
        # Fiche rédigée : ses sous-pages partent avec elle ; la fiche de campagne ne se supprime pas.
        ok, out = self.one(token, "delete", kind="document", item={"id": scenario["id"], "title": "Acte I"})
        self.assertTrue(ok, out)
        self.assertEqual(s.call("GET", f"/api/documents/{scene['id']}")[0], 404)
        root_title = s.call("GET", f"/api/documents/{root_id}")[1]["title"]
        ok, err = self.one(token, "delete", kind="document", item={"id": root_id, "name": root_title})
        self.assertFalse(ok)
        self.assertIn("fiche de campagne", err)
        ok, out = self.one(token, "delete", kind="event", item={"id": event["id"], "name": "Fondation"})
        self.assertTrue(ok, out)
        self.assertEqual(s.call("GET", f"/api/campaigns/{w}/events")[1]["events"], [])
        ok, out = self.one(token, "delete", kind="map", item={"id": smap["id"], "name": smap["name"]})
        self.assertTrue(ok, out)
        self.assertEqual(s.call("GET", f"/api/maps/{smap['id']}")[0], 404)
        # Commentaire : un fil part avec ses réponses.
        ok, out = self.one(token, "delete", kind="comment", item={"id": thread["id"], "on": "document"})
        self.assertTrue(ok, out)
        self.assertEqual(s.call("GET", f"/api/documents/{root_id}/comments")[1], [])
        self.assertFalse(self.one(token, "delete", kind="comment", item={"id": reply["id"], "on": "document"})[0])
        # Commentaire d'un autre MJ : la route le réserve à son auteur (Claude agit en MJ, pas en administrateur).
        _, user = s.call("POST", "/api/admin/users", {"username": "mj-commente", "password": "motdepasse-mjc",
                                                      "role": "mj"})
        s.call("PUT", f"/api/admin/campaigns/{w}/members", {"user_ids": [user["id"]]})
        mj = s.client()
        mj.login("mj-commente", "motdepasse-mjc")
        code, other = mj.call("POST", f"/api/documents/{root_id}/comments", {"body": "Mon avis"})
        self.assertEqual(code, 201, other)
        ok, err = self.one(token, "delete", kind="comment", item={"id": other["id"], "on": "document"})
        self.assertIn("Seul l'auteur", err)
        self.assertEqual(len(s.call("GET", f"/api/documents/{root_id}/comments")[1]), 1)

        log = s.call("GET", "/api/admin/claude")[1]["log"]
        done = [(e["tool"], e["target"], e["ok"]) for e in log if e["campaign_id"] == w]
        self.assertIn(("delete", f"npc:{npc['id']} « Inquisitrice Vael »", True), done)
        self.assertIn(("delete", f"doc:{scenario['id']} « Acte I »", True), done)
        self.assertIn(("delete", f"npc:{npc['id']} « Inquisitrice Vael »", False), done)
        hints = self.tool_names(token)["delete"]["annotations"]
        self.assertEqual((hints["destructiveHint"], hints["readOnlyHint"]), (True, False))

    def test_maps(self):
        from PIL import Image
        token = self.connect()["access_token"]
        w = self.new_campaign("Cartes Claude")
        _, sector = self.s.call("POST", f"/api/campaigns/{w}/places", {"name": "Morvane"})
        _, valdis = self.s.call("POST", f"/api/campaigns/{w}/places", {"name": "Valdis", "parent_id": sector["id"]})
        _, ebrane = self.s.call("POST", f"/api/campaigns/{w}/places", {"name": "Ebrane", "parent_id": sector["id"]})
        self.s.call("POST", f"/api/campaigns/{w}/places", {"name": "Sorn", "parent_id": valdis["id"]})
        body, ctype = multipart([("fond.png", png_bytes((20, 20, 60), (1200, 800)))])
        self.s.call("POST", f"/api/admin/campaigns/{w}/library", raw=body, content_type=ctype)
        lib_id = self.tool(token, "list", campaign_id=w, kind="library")[1]["items"][0]["id"]

        ok, created = self.one(token, "create", campaign_id=w, kind="map",
                               item={"place_id": sector["id"], "library_image_id": lib_id})
        self.assertTrue(ok, created)
        mid = created["id"]
        self.assertEqual((created["name"], created["place"]["id"], created["width"]), ("Carte de Morvane", sector["id"], 1200))
        self.assertEqual([u["name"] for u in created["unplaced"]], ["Ebrane", "Valdis"])
        self.assertIn("flou", created["note"])
        pin = lambda **item: self.one(token, "link", kind="pin", item={"map_id": mid, **item})  # noqa: E731
        ok, out = pin(place_id=valdis["id"], x=25, y=75.5)
        self.assertEqual((ok, out["y"]), (True, 75.5))
        self.assertIn("0 à 100", pin(place_id=ebrane["id"], x=120, y=10)[1])
        _, hidden_place = self.s.call("POST", f"/api/campaigns/{self.closed_camp['id']}/places", {"name": "Caché"})
        self.assertIn("n'existe pas dans cette campagne", pin(place_id=hidden_place["id"], x=10, y=10)[1])
        ok, maps = self.tool(token, "list", campaign_id=w, kind="map")
        self.assertEqual([(m["id"], m["pins"]) for m in maps["items"]], [(mid, 1)])
        self.assertEqual(self.tool(token, "list", campaign_id=w, kind="map", place_id=valdis["id"])[1]["total"], 0)
        ok, detail = self.tool(token, "get", kind="map", id=mid)
        self.assertEqual(detail["pins"], [{"n": 1, "place_id": valdis["id"], "name": "Valdis", "x": 25.0, "y": 75.5,
                                           "children": 1}])

        code, _, reply = self.rpc(token, "tools/call", {"name": "view", "arguments": {"kind": "map", "id": mid}})
        content = reply["result"]["content"]
        meta = json.loads(content[0]["text"])
        self.assertEqual(meta["pins"], detail["pins"])
        self.assertEqual([u["name"] for u in meta["unplaced"]], ["Ebrane"])
        with Image.open(io.BytesIO(base64.b64decode(content[1]["data"]))) as seen:
            self.assertEqual(seen.size, (1024, 683))

        update = lambda **item: self.one(token, "update", kind="map", item={"id": mid, **item})  # noqa: E731
        ok, upd = update(name="Secteur", place_id=None)
        self.assertEqual((upd["name"], upd["place_id"]), ("Secteur", None))
        self.assertIn("Rien à modifier", update()[1])
        ok, upd = update(pin_style="hidden")
        self.assertEqual(upd["pin_style"], "hidden")
        self.assertIn("pin_style", update(pin_style="huge")[1])
        # Type de plusieurs lieux d'un coup (ancien set_place_kind) : update kind place.
        _, planet = self.s.call("POST", f"/api/campaigns/{w}/place-kinds", {"label": "Planète", "icon": "planet"})
        ok, out = self.tool(token, "update", kind="place", items=[
            {"id": valdis["id"], "kind": planet["key"]}, {"id": hidden_place["id"], "kind": planet["key"]},
            {"id": ebrane["id"], "kind": planet["key"]}, {"id": ebrane["id"], "kind": "inconnu"}])
        self.assertEqual([r["ok"] for r in out["results"]], [True, False, True, False])
        self.assertEqual(self.s.call("GET", f"/api/places/{ebrane['id']}")[1]["kind"], planet["key"])
        self.assertEqual(self.s.call("GET", f"/api/places/{hidden_place['id']}")[1]["kind"], "lieu")
        self.assertTrue(self.one(token, "unlink", kind="pin", item={"map_id": mid, "place_id": valdis["id"]})[0])
        self.assertFalse(self.one(token, "unlink", kind="pin", item={"map_id": mid, "place_id": valdis["id"]})[0])

        # Lecture seule et campagne fermée.
        _, rmap_place = self.s.call("POST", f"/api/campaigns/{self.read_camp['id']}/places", {"name": "Lu"})
        from tests.test_maps import form
        body, ctype = form({}, ("r.png", png_bytes()))
        _, rmap = self.s.call("POST", f"/api/campaigns/{self.read_camp['id']}/maps", raw=body, content_type=ctype)
        code, _, reply = self.rpc(token, "tools/call", {"name": "view", "arguments": {"kind": "map", "id": rmap["id"]}})
        self.assertFalse(reply["result"]["isError"])
        ok, err = self.one(token, "link", kind="pin", item={"map_id": rmap["id"], "place_id": rmap_place["id"],
                                                            "x": 1, "y": 1})
        self.assertIn("lecture seule", err)
        self.assertIn("introuvable", self.tool(token, "list", campaign_id=self.closed_camp["id"], kind="map")[1])

    def test_sections_off_event_loop(self):
        """Revue : plan des titres (get, kind document) et operations d'update_document calculés hors de la boucle
        d'événements : un calcul long ne fige ni /api/health ni les autres requêtes."""
        from unittest import mock

        from maze import docsections
        cid = self.new_campaign("Sections lentes")
        code, doc = self.s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Lent",
                                                                             "content": "<h2>A</h2><p>x</p>"})
        self.assertEqual(code, 201, doc)
        token = self.connect()["access_token"]
        describe, apply_operations = docsections.describe, docsections.apply_operations

        def slow(fn):
            def run(*args):
                time.sleep(1.5)
                return fn(*args)
            return run
        calls = (("get", {"kind": "document", "id": doc["id"]}),
                 ("update_document", {"document_id": doc["id"], "base_revision": doc["revision"],
                                      "operations": [{"op": "append", "content": "<p>y</p>"}]}))
        with mock.patch.object(docsections, "describe", slow(describe)), \
                mock.patch.object(docsections, "apply_operations", slow(apply_operations)):
            for name, arguments in calls:
                result: dict = {}
                th = threading.Thread(target=lambda n=name, a=arguments, out=result:
                                      out.update(r=self.tool(token, n, **a)))
                th.start()
                time.sleep(0.4)
                t0 = time.perf_counter()
                urllib.request.urlopen(self.s.base + "/api/health").read()
                self.assertLess(time.perf_counter() - t0, 0.5, name)
                th.join()
                self.assertTrue(result["r"][0], result["r"])


if __name__ == "__main__":
    unittest.main()
