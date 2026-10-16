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

    def test_nested_factions(self):
        """Factions imbriquées (migration 031) : set_membership / remove_membership avec member_faction_id, règles
        de l'API (cycle, elle-même, chef), get_entry (sous-factions, member_of) et get_campaign (member_of)."""
        token = self.connect()["access_token"]
        w = self.write_camp["id"]
        mk = lambda name: self.tool(token, "create_entry", kind="faction", campaign_id=w, fields={"name": name})[1]  # noqa: E731
        council, watch, cell = mk("Conseil des Brumes"), mk("Guet des Brumes"), mk("Cellule des Brumes")
        ok, npc = self.tool(token, "create_entry", kind="npc", campaign_id=w, fields={"name": "Veilleur Orm"})
        ok, member = self.tool(token, "set_membership", member_faction_id=watch["id"], faction_id=council["id"],
                               role="Bras armé")
        self.assertTrue(ok, member)
        self.assertEqual((member["member_faction_id"], member["npc_id"], member["role"]),
                         (watch["id"], None, "Bras armé"))
        ok, member = self.tool(token, "set_membership", member_faction_id=watch["id"], faction_id=council["id"],
                               role="Milice")
        self.assertEqual(member["role"], "Milice")   # même paire : modifiée, pas doublée
        self.assertTrue(self.tool(token, "set_membership", member_faction_id=cell["id"], faction_id=watch["id"])[0])
        self.assertTrue(self.tool(token, "set_membership", npc_id=npc["id"], faction_id=watch["id"],
                                  is_leader=True)[0])
        ok, err = self.tool(token, "set_membership", member_faction_id=council["id"], faction_id=cell["id"])
        self.assertFalse(ok)
        self.assertIn("formerait un cycle", err)
        ok, err = self.tool(token, "set_membership", member_faction_id=cell["id"], faction_id=cell["id"])
        self.assertEqual((ok, err), (False, "Une faction ne peut pas être membre d'elle-même"))
        ok, err = self.tool(token, "set_membership", member_faction_id=cell["id"], faction_id=council["id"],
                            is_leader=True)
        self.assertEqual((ok, err), (False, "Seul un PNJ peut diriger une faction"))
        ok, err = self.tool(token, "set_membership", faction_id=council["id"])
        self.assertFalse(ok)
        ok, err = self.tool(token, "set_membership", npc_id=npc["id"], member_faction_id=cell["id"],
                            faction_id=council["id"])
        self.assertFalse(ok)
        # Campagne en lecture seule ou fermée : refus, comme pour un PNJ.
        ok, ro = self.tool(token, "set_membership", member_faction_id=watch["id"], faction_id=council["id"])
        self.assertTrue(ok)
        _, hidden = self.s.call("POST", f"/api/campaigns/{self.closed_camp['id']}/factions", {"name": "Fermée"})
        ok, err = self.tool(token, "set_membership", member_faction_id=hidden["id"], faction_id=council["id"])
        self.assertEqual((ok, err), (False, "Faction introuvable (ou non ouverte à Claude)"))

        ok, detail = self.tool(token, "get_entry", kind="faction", id=watch["id"])
        self.assertEqual([(m.get("npc"), m.get("member_faction"), m.get("leader")) for m in detail["memberships"]],
                         [("Veilleur Orm", None, True), (None, "Cellule des Brumes", None)])
        self.assertEqual([(m["faction"], m["role"]) for m in detail["member_of"]], [("Conseil des Brumes", "Milice")])
        ok, detail = self.tool(token, "get_entry", kind="npc", id=npc["id"])
        self.assertEqual([m["faction"] for m in detail["memberships"]], ["Guet des Brumes"])
        self.assertNotIn("member_of", detail)
        ok, overview = self.tool(token, "get_campaign", campaign_id=w)
        factions = {f["name"]: f for f in overview["factions"]}
        self.assertEqual(factions["Guet des Brumes"]["member_of"], [{"faction_id": council["id"], "role": "Milice"}])
        self.assertEqual(factions["Conseil des Brumes"]["member_of"], [])
        orm = next(n for n in overview["npcs"] if n["name"] == "Veilleur Orm")
        self.assertEqual(orm["factions"], [{"faction_id": watch["id"], "role": "", "leader": True}])

        ok, removed = self.tool(token, "remove_membership", member_faction_id=cell["id"], faction_id=watch["id"])
        self.assertEqual(removed, {"removed": {"member_faction_id": cell["id"], "faction_id": watch["id"]}})
        ok, err = self.tool(token, "remove_membership", member_faction_id=cell["id"], faction_id=watch["id"])
        self.assertEqual((ok, err), (False, "Cette faction n'est pas membre de cette faction"))
        # Une fois la boucle défaite, l'appartenance inverse devient possible.
        self.assertTrue(self.tool(token, "set_membership", member_faction_id=council["id"], faction_id=cell["id"])[0])
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
        _, _, reply = self.rpc(token, "tools/list")
        names = {t["name"] for t in reply["result"]["tools"]}
        self.assertTrue({"list_campaigns", "get_document", "update_document", "create_entry"} <= names)
        self.assertEqual(self.rpc(token, "inconnu")[2]["error"]["code"], -32601)
        self.assertEqual(self.rpc(token, "tools/call", {"name": ["x"]})[2]["error"]["code"], -32602)
        self.assertEqual(self.rpc(token, "tools/call", {"name": "nope"})[2]["error"]["code"], -32602)

        w, r, closed = self.write_camp["id"], self.read_camp["id"], self.closed_camp["id"]
        ok, out = self.tool(token, "list_campaigns")
        self.assertEqual({c["id"] for c in out["campaigns"]}, {w, r})

        # Fiches : écriture permise, lecture seule, campagne fermée.
        ok, npc = self.tool(token, "create_entry", kind="npc", campaign_id=w,
                            fields={"name": "Sœur Agathe", "title": "Chanoinesse", "status": "vivant"})
        self.assertTrue(ok, npc)
        ok, err = self.tool(token, "create_entry", kind="npc", campaign_id=r, fields={"name": "Interdit"})
        self.assertFalse(ok)
        self.assertIn("lecture seule", err)
        ok, err = self.tool(token, "create_entry", kind="npc", campaign_id=closed, fields={"name": "Interdit"})
        self.assertFalse(ok)
        self.assertIn("introuvable", err)
        self.assertFalse(self.tool(token, "get_entry", kind="npc", id=self.hidden_npc["id"])[0])
        self.assertTrue(self.tool(token, "get_entry", kind="npc", id=self.read_npc["id"])[0])
        ok, err = self.tool(token, "update_entry", kind="npc", id=self.read_npc["id"], fields={"title": "x"})
        self.assertIn("lecture seule", err)
        ok, err = self.tool(token, "create_entry", kind="npc", campaign_id=w, fields={"name": "X", "status": "zombie"})
        self.assertFalse(ok)
        self.assertIn("status", err)
        ok, err = self.tool(token, "update_entry", kind="npc", id=npc["id"], fields={"pos_x": 3})
        self.assertIn("Champs inconnus", err)
        ok, upd = self.tool(token, "update_entry", kind="npc", id=npc["id"], fields={"summary": "Gardienne des archives"})
        self.assertEqual(upd["summary"], "Gardienne des archives")

        # Recherche limitée aux campagnes ouvertes.
        ok, found = self.tool(token, "search", query="agathe")
        self.assertEqual([x["id"] for x in found["results"] if x["kind"] == "npc"], [npc["id"]])
        ok, found = self.tool(token, "search", query="Varnak")
        self.assertEqual(found["results"], [])

        # Faction, appartenance, lien, lieu.
        ok, fac = self.tool(token, "create_entry", kind="faction", campaign_id=w, fields={"name": "Ordre du Voile"})
        ok, member = self.tool(token, "set_membership", npc_id=npc["id"], faction_id=fac["id"], role="Agente")
        self.assertTrue(ok, member)
        ok, member = self.tool(token, "set_membership", npc_id=npc["id"], faction_id=fac["id"], is_leader=True)
        self.assertTrue(member["is_leader"])
        ok, other = self.tool(token, "create_entry", kind="npc", campaign_id=w, fields={"name": "Frère Malik"})
        ok, rel = self.tool(token, "create_relation", campaign_id=w, source_kind="npc", source_id=npc["id"],
                            target_kind="npc", target_id=other["id"], label="Mentor")
        self.assertTrue(ok, rel)
        ok, rel = self.tool(token, "update_relation", relation_id=rel["id"], secret=True)
        self.assertTrue(rel["secret"])
        ok, place = self.tool(token, "create_entry", kind="place", campaign_id=w, fields={"name": "Scriptorium"})
        ok, link = self.tool(token, "set_place_link", place_id=place["id"], entity_kind="npc",
                             entity_id=npc["id"], role="Archiviste")
        self.assertTrue(ok, link)
        ok, link = self.tool(token, "set_place_link", place_id=place["id"], entity_kind="npc", entity_id=npc["id"])
        self.assertEqual(link["role"], "Archiviste")  # sans rôle : inchangé
        ok, detail = self.tool(token, "get_entry", kind="npc", id=npc["id"])
        self.assertEqual(detail["relations"][0]["target"]["name"], "Frère Malik")
        self.assertEqual(detail["memberships"][0]["faction"], "Ordre du Voile")
        self.assertEqual(detail["places"][0]["role"], "Archiviste")
        ok, overview = self.tool(token, "get_campaign", campaign_id=w)
        self.assertEqual(overview["relation_count"], 1)
        self.assertEqual(overview["totals"]["npcs"], 2)
        self.assertNotIn("note", overview)
        ok, page = self.tool(token, "list_relations", campaign_id=w, kind="npc", id=other["id"], limit=1)
        self.assertEqual((page["total"], len(page["relations"]), page.get("next_offset")), (1, 1, None))
        ok, page = self.tool(token, "list_relations", campaign_id=w, type="ennemi")
        self.assertEqual(page["total"], 0)
        ok, err = self.tool(token, "list_relations", campaign_id=w, limit=5000)
        self.assertFalse(ok)
        self.assertEqual({n["name"] for n in overview["npcs"]}, {"Sœur Agathe", "Frère Malik"})
        self.assertTrue(self.tool(token, "remove_place_link", place_id=place["id"], entity_kind="npc",
                                  entity_id=npc["id"])[0])
        self.assertTrue(self.tool(token, "remove_membership", npc_id=npc["id"], faction_id=fac["id"])[0])
        self.assertTrue(self.tool(token, "delete_relation", relation_id=rel["id"])[0])

        # Fiches rédigées : création, révision exigée, versions signées Claude, commentaire.
        _, before = self.s.call("GET", f"/api/campaigns/{w}/documents")
        ok, err = self.tool(token, "create_document", campaign_id=w, title="Statut faux", status="fini")
        self.assertFalse(ok)
        _, after = self.s.call("GET", f"/api/campaigns/{w}/documents")
        self.assertEqual(len(after["documents"]), len(before["documents"]))  # rien de créé
        mention = f'<p>Rencontre avec <a class="mention" data-mention="npc:{npc["id"]}">Sœur Agathe</a>.</p>'
        ok, doc = self.tool(token, "create_document", campaign_id=w, title="Le scriptorium", content=mention)
        self.assertTrue(ok, doc)
        self.assertEqual(doc["kind"], "scenario")
        ok, read = self.tool(token, "get_document", document_id=doc["id"])
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
        ok, comment = self.tool(token, "add_comment", document_id=doc["id"], body="À relire", quote="Suite")
        self.assertEqual(comment["author_name"], "Claude")
        ok, page = self.tool(token, "create_document", campaign_id=w, title="Acte I", parent_id=doc["id"])
        self.assertEqual(page["kind"], "page")
        ok, read = self.tool(token, "get_document", document_id=doc["id"], include_comments=True)
        self.assertEqual(read["children"][0]["title"], "Acte I")
        self.assertEqual(read["comments"][0]["body"], "À relire")

        # Journal de l'administration.
        _, state = self.s.call("GET", "/api/admin/claude")
        tools = [e["tool"] for e in state["log"]]
        self.assertIn("update_document", tools)
        self.assertTrue(any(not e["ok"] for e in state["log"]))
        self.assertTrue(state["sessions"])

    def test_live_document_guard(self):
        token = self.connect()["access_token"]
        w = self.write_camp["id"]
        _, doc = self.s.call("POST", f"/api/campaigns/{w}/documents", {"title": "Ouverte", "content": "<p>Départ</p>"})
        ws = self.s.admin.live(doc["id"], 909)
        recv_until(ws, "welcome")
        ws.send(bytes([0]) + b"frappe")  # modification reçue, pas encore dans le HTML enregistré
        self.assertEqual(recv(ws)[0], 3)
        ok, read = self.tool(token, "get_document", document_id=doc["id"])
        ok, err = self.tool(token, "update_document", document_id=doc["id"], base_revision=read["revision"],
                            content="<p>Par Claude</p>")
        self.assertFalse(ok)
        self.assertIn("La fiche est ouverte", err)
        ws.send(json.dumps({"t": "content", "html": "<p>Départ et frappe</p>", "upto": 1}))
        recv_until(ws, "meta")
        ok, read = self.tool(token, "get_document", document_id=doc["id"])
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
        ok, read = self.tool(token, "get_document", document_id=doc["id"])
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
        ok, read = self.tool(token, "get_document", document_id=doc["id"])
        ok, upd = self.tool(token, "update_document", document_id=doc["id"], base_revision=read["revision"],
                            content="<p>Par Claude</p>")
        self.assertTrue(ok, upd)
        ws.close()

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

    def test_overview_fits_budget(self):
        from maze import mcp
        out = {"campaign": {"id": 1}, "documents": {"tree": []}, "relation_types": [], "relation_count": 0,
               "place_kinds": [], "totals": {"npcs": 1000, "factions": 50, "places": 300}}
        lists = {
            "npcs": [{"id": i, "name": f"PNJ numéro {i}", "title": "Batelier du port", "status": "vivant",
                      "summary": "x" * 160, "tags": ["a", "b"], "factions": [{"faction_id": 3, "role": "Agent"}]}
                     for i in range(1000)],
            "factions": [{"id": i, "name": f"Faction {i}", "kind": "Guilde", "summary": "y" * 160, "tags": []}
                         for i in range(50)],
            "places": [{"id": i, "parent_id": None, "name": f"Lieu {i}", "kind": "lieu", "summary": "z" * 160,
                        "tags": []} for i in range(300)],
        }
        fitted = mcp._fit_overview(out, lists)
        self.assertLessEqual(mcp._size(fitted), mcp.RESULT_BUDGET)
        self.assertIn("tronquées", fitted["note"])
        self.assertNotIn("summary", fitted["npcs"][0])
        self.assertEqual(fitted["npcs"][0]["factions"], [3])
        self.assertEqual(len(fitted["factions"]), 50)  # PNJ tronqués en premier
        small = mcp._fit_overview(out, {k: v[:5] for k, v in lists.items()})
        self.assertNotIn("note", small)
        self.assertIn("summary", small["npcs"][0])

    def test_writes_refused_during_restore(self):
        """Contre-revue M7 : les outils d'écriture du connecteur (appels internes ctx.api, hors des middlewares HTTP)
        sont refusés pendant une restauration ; les outils de lecture marchent."""
        from maze import backup
        token = self.connect()["access_token"]
        ok, npc = self.tool(token, "create_entry", kind="npc", campaign_id=self.write_camp["id"], fields={"name": "Gardien"})
        self.assertTrue(ok, npc)
        with backup.restore_marker(Path(self.s.tmp.name)):
            ok, err = self.tool(token, "update_entry", kind="npc", id=npc["id"], fields={"notes": "<p>Perdu</p>"})
            self.assertFalse(ok)
            self.assertIn("Restauration en cours", err)
            ok, read = self.tool(token, "get_entry", kind="npc", id=npc["id"])
            self.assertTrue(ok, read)
        self.assertEqual(self.s.call("GET", f"/api/npcs/{npc['id']}")[1]["notes"], "")
        ok, out = self.tool(token, "update_entry", kind="npc", id=npc["id"], fields={"notes": "<p>Après</p>"})
        self.assertTrue(ok, out)

    def test_update_entry_revision(self):
        """Contre-revue M1 : update_entry accepte base_revision (révision de get_entry ou du dernier update_entry).
        Partis de la révision à jour, les appels successifs de Claude forment une seule version de l'historique (une
        version par appel évincerait celles des MJ, 50 au plus par fiche) ; révision dépassée : refus, rien
        d'écrit. Sans elle : comme avant, jamais de fusion."""
        token = self.connect()["access_token"]
        ok, npc = self.tool(token, "create_entry", kind="npc", campaign_id=self.write_camp["id"],
                            fields={"name": "Archiviste Hale", "notes": "<p>Départ</p>"})
        self.assertTrue(ok, npc)
        url = f"/api/npcs/{npc['id']}/versions"
        ok, read = self.tool(token, "get_entry", kind="npc", id=npc["id"])
        rev = read["revision"]
        for i in range(4):
            ok, out = self.tool(token, "update_entry", kind="npc", id=npc["id"], base_revision=rev,
                                fields={"notes": f"<p>Passe {i} de Claude</p>"})
            self.assertTrue(ok, out)
            self.assertEqual(out["revision"], rev + 1)
            rev = out["revision"]
        versions = self.s.call("GET", url)[1]["versions"]
        self.assertEqual([(v["label"], v["author_name"]) for v in versions], [("", "Claude"), ("État initial", "")])
        # Un MJ modifie la fiche : la révision de Claude est dépassée, refus sans rien écrire.
        self.assertEqual(self.s.call("PATCH", f"/api/npcs/{npc['id']}", {"notes": "<p>Texte du MJ</p>"})[0], 200)
        ok, err = self.tool(token, "update_entry", kind="npc", id=npc["id"], base_revision=rev,
                            fields={"notes": "<p>Écrase le MJ</p>"})
        self.assertFalse(ok)
        self.assertIn("modifiée entre-temps", err)
        self.assertEqual(self.s.call("GET", f"/api/npcs/{npc['id']}")[1]["notes"], "<p>Texte du MJ</p>")
        ok, err = self.tool(token, "update_entry", kind="npc", id=npc["id"], base_revision="x", fields={"notes": "a"})
        self.assertIn("entier attendu", err)
        # Sans base_revision : accepté, une version par appel.
        before = len(self.s.call("GET", url)[1]["versions"])
        for i in range(2):
            self.assertTrue(self.tool(token, "update_entry", kind="npc", id=npc["id"],
                                      fields={"notes": f"<p>Sans révision {i}</p>"})[0])
        self.assertEqual(len(self.s.call("GET", url)[1]["versions"]), before + 2)

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
        _, camp = self.s.call("POST", "/api/campaigns", {"name": "Images"})
        w = camp["id"]
        self.s.call("PUT", "/api/admin/claude", {"grant": {"campaign_id": w, "mode": "write"}})
        self.addCleanup(self.s.call, "PUT", "/api/admin/claude", {"grant": {"campaign_id": w, "mode": "none"}})
        ok, npc = self.tool(token, "create_entry", kind="npc", campaign_id=w, fields={"name": "Modèle"})

        # Adresse interne : refusée tant que les adresses privées ne sont pas autorisées (SSRF).
        for internal in ("http://127.0.0.1/portrait.png", "http://10.0.0.10/a.png", "http://[::1]/a.png",
                         "http://169.254.169.254/latest/meta-data/"):
            ok, err = self.tool(token, "add_image", kind="npc", id=npc["id"], url=internal)
            self.assertIn("non publique", err)
        ok, err = self.tool(token, "add_image", kind="npc", id=npc["id"], url=f"{base}/portrait.png")
        self.assertIn("Ports 80 et 443", err)
        for bad in ("file:///etc/passwd", "ftp://example.org/a.png", "http://user:pw@example.org/a.png"):
            self.assertFalse(self.tool(token, "add_image", kind="npc", id=npc["id"], url=bad)[0])
        fetch.ALLOW_PRIVATE = True
        self.addCleanup(setattr, fetch, "ALLOW_PRIVATE", False)

        ok, added = self.tool(token, "add_image", kind="npc", id=npc["id"], url=f"{base}/redirect", caption="Profil")
        self.assertTrue(ok, added)
        first = added["image_id"]
        self.assertEqual((added["width"], added["height"]), (600, 400))
        self.assertEqual(added["images"], [{"id": first, "caption": "Profil", "width": 600, "height": 400, "cover": True}])
        ok, err = self.tool(token, "add_image", kind="npc", id=npc["id"], url=f"{base}/texte.png")
        self.assertIn("Image refusée", err)
        ok, err = self.tool(token, "add_image", kind="npc", id=npc["id"], url=f"{base}/absent.png")
        self.assertIn("404", err)
        ok, err = self.tool(token, "add_image", kind="npc", id=npc["id"], url=f"{base}/geante.png")
        self.assertIn("trop grande", err)
        ok, err = self.tool(token, "add_image", kind="npc", id=npc["id"], url=f"{base}/château é.png")
        self.assertIn("Image refusée", err)  # adresse encodée, puis contenu non image
        ok, err = self.tool(token, "add_image", kind="npc", id=npc["id"], url="http://[::1/x")
        self.assertIn("invalide", err)
        self.addCleanup(setattr, fetch, "TOTAL_SECONDS", fetch.TOTAL_SECONDS)
        fetch.TOTAL_SECONDS = 1
        started = time.monotonic()
        ok, err = self.tool(token, "add_image", kind="npc", id=npc["id"], url=f"{base}/lent.png")
        self.assertIn("trop long", err)
        self.assertLess(time.monotonic() - started, 2)
        fetch.TOTAL_SECONDS = 30
        ok, err = self.tool(token, "add_image", kind="npc", id=npc["id"], url=f"{base}/portrait.png", from_image_id=first)
        self.assertIn("une seule", err)
        ok, err = self.tool(token, "add_image", kind="npc", id=self.read_npc["id"], url=f"{base}/portrait.png")
        self.assertIn("lecture seule", err)

        # Voir : bloc texte + bloc image JPEG.
        code, _, reply = self.rpc(token, "tools/call", {"name": "view_image", "arguments": {"image_id": first}})
        content = reply["result"]["content"]
        self.assertEqual([b["type"] for b in content], ["text", "image"])
        self.assertEqual(content[1]["mimeType"], "image/jpeg")
        with Image.open(io.BytesIO(base64.b64decode(content[1]["data"]))) as seen:
            self.assertEqual(seen.size, (600, 400))
        self.assertTrue(json.loads(content[0]["text"])["cover"])

        # Copie d'une image de Maze, couverture, légende et ordre.
        ok, second = self.tool(token, "add_image", kind="npc", id=npc["id"], from_image_id=first, set_cover=True)
        self.assertTrue(ok, second)
        sid = second["image_id"]
        self.assertEqual([i["cover"] for i in second["images"]], [False, True])
        ok, upd = self.tool(token, "update_image", image_id=sid, position=0, caption="Copie")
        self.assertEqual([i["id"] for i in upd["images"]], [sid, first])
        self.assertEqual(upd["images"][0]["caption"], "Copie")
        ok, entry = self.tool(token, "get_entry", kind="npc", id=npc["id"])
        self.assertEqual([(i["id"], i["cover"]) for i in entry["images"]], [(sid, True), (first, False)])

        # Retouche : pivot puis recadrage ; la retouche prend la place de l'originale, gardée en fin.
        ok, edited = self.tool(token, "edit_image", image_id=sid, rotate=90,
                               crop={"left": 0, "top": 0, "right": 100, "bottom": 50})
        self.assertTrue(ok, edited)
        self.assertEqual((edited["width"], edited["height"]), (400, 300))
        self.assertEqual([i["id"] for i in edited["images"]], [edited["image_id"], first, sid])
        self.assertTrue(edited["images"][0]["cover"])
        ok, err = self.tool(token, "edit_image", image_id=first, crop={"left": 50, "top": 0, "right": 40, "bottom": 10})
        self.assertFalse(ok)
        ok, err = self.tool(token, "edit_image", image_id=first)
        self.assertIn("Rien à faire", err)
        ok, err = self.tool(token, "edit_image", image_id=first, flip={"x": 1})
        self.assertIn("flip", err)
        ok, replaced = self.tool(token, "edit_image", image_id=first, flip="horizontal", keep_original=False)
        self.assertNotIn(first, [i["id"] for i in replaced["images"]])

        # Suppression : la couverture passe à l'image suivante.
        ok, gone = self.tool(token, "delete_image", image_id=edited["image_id"])
        self.assertTrue(ok, gone)
        self.assertTrue(gone["images"][0]["cover"])
        self.assertFalse(self.tool(token, "view_image", image_id=edited["image_id"])[0])

        # Fiche rédigée : illustration puis insertion dans le contenu.
        ok, doc = self.tool(token, "create_document", campaign_id=w, title="Illustré", content="<p>Avant</p>")
        ok, pic = self.tool(token, "add_document_image", document_id=doc["id"], url=f"{base}/portrait.png",
                            alt='Le "port"')
        self.assertTrue(ok, pic)
        self.assertIn(f'data-doc-image="{pic["document_image_id"]}"', pic["html"])
        self.assertIn("&quot;port&quot;", pic["html"])
        ok, read = self.tool(token, "get_document", document_id=doc["id"])
        ok, upd = self.tool(token, "update_document", document_id=doc["id"], base_revision=read["revision"],
                            content="<p>Avant</p>" + pic["html"])
        self.assertTrue(ok, upd)
        code, _, reply = self.rpc(token, "tools/call", {"name": "view_image",
                                                        "arguments": {"document_image_id": pic["document_image_id"]}})
        self.assertEqual(reply["result"]["content"][1]["type"], "image")
        ok, copy = self.tool(token, "add_image", kind="npc", id=npc["id"], from_document_image_id=pic["document_image_id"])
        self.assertTrue(ok, copy)

        # Campagne non ouverte : rien de visible.
        self.assertFalse(self.tool(token, "add_image", kind="npc", id=self.hidden_npc["id"],
                                   url=f"{base}/portrait.png")[0])
        # Bibliothèque de la campagne (déposée par le MJ dans l'administration).
        body, ctype = multipart([("carte.png", png_bytes((90, 60, 30), (500, 300)))])
        self.assertEqual(self.s.call("POST", f"/api/admin/campaigns/{w}/library", raw=body, content_type=ctype)[0], 201)
        ok, lib = self.tool(token, "list_library", campaign_id=w)
        lib_id = lib["library"][0]["id"]
        self.s.call("PATCH", f"/api/admin/library/{lib_id}", {"caption": "Carte du port"})
        code, _, reply = self.rpc(token, "tools/call", {"name": "view_image", "arguments": {"library_image_id": lib_id}})
        self.assertEqual(reply["result"]["content"][1]["type"], "image")
        ok, from_lib = self.tool(token, "add_image", kind="npc", id=npc["id"], from_library_image_id=lib_id)
        self.assertTrue(ok, from_lib)
        self.assertEqual(next(i for i in from_lib["images"] if i["id"] == from_lib["image_id"])["caption"], "Carte du port")
        self.assertTrue(self.tool(token, "delete_library_image", library_image_id=lib_id)[0])
        self.assertEqual(self.tool(token, "list_library", campaign_id=w)[1]["library"], [])

        # Images de campagnes en lecture seule ou non ouvertes.
        ok, ro_img = self.tool(token, "add_image", kind="npc", id=npc["id"], url=f"{base}/portrait.png")
        _, ro = self.s.call("POST", "/api/campaigns", {"name": "Lecture images"})
        _, ro_npc = self.s.call("POST", f"/api/campaigns/{ro['id']}/npcs", {"name": "Lu"})
        _, closed = self.s.call("POST", "/api/campaigns", {"name": "Fermée images"})
        _, closed_npc = self.s.call("POST", f"/api/campaigns/{closed['id']}/npcs", {"name": "Caché"})
        self.s.call("PUT", "/api/admin/claude", {"grant": {"campaign_id": ro["id"], "mode": "read"}})
        self.addCleanup(self.s.call, "PUT", "/api/admin/claude", {"grant": {"campaign_id": ro["id"], "mode": "none"}})
        mp = self.tool(token, "add_image", kind="npc", id=ro_npc["id"], from_image_id=ro_img["image_id"])
        self.assertIn("lecture seule", mp[1])
        with sqlite3.connect(Path(self.s.tmp.name) / "maze.db") as db:
            for owner in (ro_npc["id"], closed_npc["id"]):
                camp = ro["id"] if owner == ro_npc["id"] else closed["id"]
                db.execute("INSERT INTO images (campaign_id, owner_kind, owner_id, file, thumb, width, height) "
                           "SELECT ?, 'npc', ?, file, thumb, width, height FROM images WHERE id = ?",
                           (camp, owner, ro_img["image_id"]))
            ro_id, closed_id = [r[0] for r in db.execute(
                "SELECT id FROM images WHERE owner_id IN (?, ?) ORDER BY owner_id", (ro_npc["id"], closed_npc["id"]))]
        self.assertTrue(self.tool(token, "view_image", image_id=ro_id)[0])
        for name, extra in (("update_image", {"caption": "x"}), ("edit_image", {"rotate": 90}),
                            ("delete_image", {})):
            self.assertIn("lecture seule", self.tool(token, name, image_id=ro_id, **extra)[1])
            self.assertIn("introuvable", self.tool(token, name, image_id=closed_id, **extra)[1])
        self.assertIn("introuvable", self.tool(token, "view_image", image_id=closed_id)[1])
        self.assertIn("introuvable", self.tool(token, "add_image", kind="npc", id=npc["id"],
                                               from_image_id=closed_id)[1])
        body, ctype = multipart([("x.png", png_bytes())])
        for camp_id in (ro["id"], closed["id"]):
            self.s.call("POST", f"/api/admin/campaigns/{camp_id}/library", raw=body, content_type=ctype)
        with sqlite3.connect(Path(self.s.tmp.name) / "maze.db") as db:
            ro_lib, closed_lib = [r[0] for r in db.execute(
                "SELECT id FROM library_images WHERE campaign_id IN (?, ?) ORDER BY campaign_id", (ro["id"], closed["id"]))]
        self.assertTrue(self.tool(token, "view_image", library_image_id=ro_lib)[0])
        self.assertIn("lecture seule", self.tool(token, "delete_library_image", library_image_id=ro_lib)[1])
        self.assertIn("introuvable", self.tool(token, "list_library", campaign_id=closed["id"])[1])
        self.assertIn("introuvable", self.tool(token, "view_image", library_image_id=closed_lib)[1])

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
            ok, data = self.tool(token, "get_entry", kind="npc", id=npc["id"])
            self.assertTrue(ok, data)
            self.assertLess(time.perf_counter() - start, 1.5)
            ok, err = self.tool(token, "update_entry", kind="npc", id=npc["id"], fields={"summary": "pendant le verrou"})
            self.assertFalse(ok)
            self.assertIn("réessayez", err)
        ok, data = self.tool(token, "update_entry", kind="npc", id=npc["id"], fields={"summary": "après"})
        self.assertTrue(ok, data)
        self.assertEqual(self.s.call("DELETE", f"/api/npcs/{npc['id']}")[0], 204)   # les autres tests comptent les PNJ

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


    def test_maps(self):
        from PIL import Image
        token = self.connect()["access_token"]
        _, camp = self.s.call("POST", "/api/campaigns", {"name": "Cartes Claude"})
        w = camp["id"]
        self.s.call("PUT", "/api/admin/claude", {"grant": {"campaign_id": w, "mode": "write"}})
        self.addCleanup(self.s.call, "PUT", "/api/admin/claude", {"grant": {"campaign_id": w, "mode": "none"}})
        _, sector = self.s.call("POST", f"/api/campaigns/{w}/places", {"name": "Morvane"})
        _, valdis = self.s.call("POST", f"/api/campaigns/{w}/places", {"name": "Valdis", "parent_id": sector["id"]})
        _, ebrane = self.s.call("POST", f"/api/campaigns/{w}/places", {"name": "Ebrane", "parent_id": sector["id"]})
        self.s.call("POST", f"/api/campaigns/{w}/places", {"name": "Sorn", "parent_id": valdis["id"]})
        body, ctype = multipart([("fond.png", png_bytes((20, 20, 60), (1200, 800)))])
        self.s.call("POST", f"/api/admin/campaigns/{w}/library", raw=body, content_type=ctype)
        lib_id = self.tool(token, "list_library", campaign_id=w)[1]["library"][0]["id"]

        ok, created = self.tool(token, "create_map", campaign_id=w, place_id=sector["id"], from_library_image_id=lib_id)
        self.assertTrue(ok, created)
        mid = created["map_id"]
        self.assertEqual((created["name"], created["place"]["id"], created["width"]), ("Carte de Morvane", sector["id"], 1200))
        self.assertEqual([u["name"] for u in created["unplaced"]], ["Ebrane", "Valdis"])
        self.assertIn("flou", created["note"])
        ok, out = self.tool(token, "place_on_map", map_id=mid, place_id=valdis["id"], x=25, y=75.5)
        self.assertEqual((ok, out["y"]), (True, 75.5))
        ok, err = self.tool(token, "place_on_map", map_id=mid, place_id=ebrane["id"], x=120, y=10)
        self.assertIn("0 à 100", err)
        _, hidden_place = self.s.call("POST", f"/api/campaigns/{self.closed_camp['id']}/places", {"name": "Caché"})
        ok, err = self.tool(token, "place_on_map", map_id=mid, place_id=hidden_place["id"], x=10, y=10)
        self.assertIn("n'existe pas dans cette campagne", err)
        ok, maps = self.tool(token, "list_maps", campaign_id=w)
        self.assertEqual([(m["id"], m["pins"]) for m in maps["maps"]], [(mid, 1)])

        code, _, reply = self.rpc(token, "tools/call", {"name": "view_map", "arguments": {"map_id": mid}})
        content = reply["result"]["content"]
        meta = json.loads(content[0]["text"])
        self.assertEqual(meta["pins"], [{"n": 1, "place_id": valdis["id"], "name": "Valdis", "x": 25.0, "y": 75.5,
                                         "children": 1}])
        self.assertEqual([u["name"] for u in meta["unplaced"]], ["Ebrane"])
        with Image.open(io.BytesIO(base64.b64decode(content[1]["data"]))) as seen:
            self.assertEqual(seen.size, (1024, 683))

        ok, upd = self.tool(token, "update_map", map_id=mid, name="Secteur", place_id=None)
        self.assertEqual((upd["name"], upd["place_id"]), ("Secteur", None))
        ok, err = self.tool(token, "update_map", map_id=mid)
        self.assertIn("Rien à modifier", err)
        ok, upd = self.tool(token, "update_map", map_id=mid, pin_style="hidden")
        self.assertEqual(upd["pin_style"], "hidden")
        self.assertIn("pin_style", self.tool(token, "update_map", map_id=mid, pin_style="huge")[1])
        _, planet = self.s.call("POST", f"/api/campaigns/{w}/place-kinds", {"label": "Planète", "icon": "planet"})
        ok, typed = self.tool(token, "set_place_kind", place_ids=[valdis["id"], ebrane["id"]], kind=planet["key"])
        self.assertTrue(ok, typed)
        self.assertEqual(self.s.call("GET", f"/api/places/{ebrane['id']}")[1]["kind"], planet["key"])
        ok, err = self.tool(token, "set_place_kind", place_ids=[valdis["id"]], kind="inconnu")
        self.assertIn("Types :", err)
        ok, err = self.tool(token, "set_place_kind", place_ids=[valdis["id"], hidden_place["id"]], kind=planet["key"])
        self.assertFalse(ok)
        self.assertTrue(self.tool(token, "remove_from_map", map_id=mid, place_id=valdis["id"])[0])
        self.assertFalse(self.tool(token, "remove_from_map", map_id=mid, place_id=valdis["id"])[0])

        # Lecture seule et campagne fermée.
        _, rmap_place = self.s.call("POST", f"/api/campaigns/{self.read_camp['id']}/places", {"name": "Lu"})
        from tests.test_maps import form
        body, ctype = form({}, ("r.png", png_bytes()))
        _, rmap = self.s.call("POST", f"/api/campaigns/{self.read_camp['id']}/maps", raw=body, content_type=ctype)
        code, _, reply = self.rpc(token, "tools/call", {"name": "view_map", "arguments": {"map_id": rmap["id"]}})
        self.assertFalse(reply["result"]["isError"])
        ok, err = self.tool(token, "place_on_map", map_id=rmap["id"], place_id=rmap_place["id"], x=1, y=1)
        self.assertIn("lecture seule", err)
        ok, err = self.tool(token, "list_maps", campaign_id=self.closed_camp["id"])
        self.assertIn("introuvable", err)

if __name__ == "__main__":
    unittest.main()
