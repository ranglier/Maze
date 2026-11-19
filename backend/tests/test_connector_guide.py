"""Tests du guide d'usage du connecteur Claude : instructions du serveur, ressources et invites MCP (mcp_guide.py),
skill claude.ai fourni avec Maze (docs/claude/maze-skill/SKILL.md).

Lancer : cd backend && python -m unittest -q tests.test_connector_guide
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from maze import mcp, mcp_guide
from tests import test_connector

SKILL = Path(__file__).resolve().parents[2] / "docs" / "claude" / "maze-skill" / "SKILL.md"


class InstructionsTest(unittest.TestCase):
    def test_short(self):
        # Lues par Claude à chaque conversation : le détail va dans le guide.
        self.assertLess(len(mcp.INSTRUCTIONS), 3500)

    def test_every_tool_named(self):
        missing = [t["name"] for t in mcp.TOOLS
                   if not re.search(rf"(?<![\w]){re.escape(t['name'])}(?![\w])", mcp.INSTRUCTIONS)]
        self.assertEqual(missing, [], "outils absents des instructions du serveur")

    def test_rules_kept(self):
        for needle in ("base_revision", "data-mention", 'data-callout="read"', 'data-callout="secret"', "data-align",
                       "<span data-comment>", "<img data-doc-image>", "items", "100", "operations", "replace_section",
                       "replace_text", "corbeille", "30", "request_upload", "ticket", "Chronologie",
                       "lecture, écriture et suppression", "langue"):
            self.assertIn(needle, mcp.INSTRUCTIONS)


class SkillTest(unittest.TestCase):
    def test_skill_matches_guide(self):
        self.assertTrue(SKILL.is_file(), SKILL)
        self.assertEqual(SKILL.read_text(encoding="utf-8"), mcp_guide.skill(),
                         "SKILL.md en retard : cd backend && python -m maze.mcp_guide > ../docs/claude/maze-skill/SKILL.md")

    def test_frontmatter(self):
        head = mcp_guide.skill().split("---\n")[1]
        name = re.search(r"^name: (.+)$", head, re.M).group(1)
        description = re.search(r"^description: (.+)$", head, re.M).group(1)
        self.assertRegex(name, r"^[a-z0-9-]{1,64}$")
        self.assertEqual(name, SKILL.parent.name)   # claude.ai : dossier du zip = nom du skill
        self.assertLessEqual(len(description), 1024)

    def test_guide_follows_registry(self):
        from maze import entries
        for kind in entries.KINDS:
            for key in entries.connector_fields(kind):
                self.assertIn(f"`{key}`", mcp_guide.GUIDE)


class GuideProtocolTest(test_connector.ConnectorTest):
    """Méthodes resources/* et prompts/* sur un vrai serveur ; reprend les campagnes de ConnectorTest
    (écriture, lecture, fermée) sans rejouer ses tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.token = None

    def setUp(self):
        if type(self).token is None:
            type(self).token = self.connect()["access_token"]

    def ok(self, method, params=None):
        code, _, reply = self.rpc(self.token, method, params)
        self.assertEqual(code, 200, reply)
        self.assertNotIn("error", reply, reply)
        return reply["result"]

    def error(self, method, params=None):
        code, _, reply = self.rpc(self.token, method, params)
        self.assertEqual(code, 200, reply)
        self.assertIn("error", reply, reply)
        return reply["error"]

    def test_initialize_announces_resources_and_prompts(self):
        result = self.ok("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                        "clientInfo": {"name": "test", "version": "1"}})
        caps = result["capabilities"]
        self.assertIn("tools", caps)
        self.assertEqual(caps["resources"], {"subscribe": False, "listChanged": False})
        self.assertEqual(caps["prompts"], {"listChanged": False})
        self.assertEqual(result["instructions"], mcp.INSTRUCTIONS)
        # Méthodes inconnues : toujours -32601 (comportement attendu par claude.ai).
        self.assertEqual(self.error("resources/subscribe", {"uri": mcp_guide.GUIDE_URI})["code"], -32601)

    def test_resources(self):
        listed = self.ok("resources/list")["resources"]
        uris = [r["uri"] for r in listed]
        write_uri = f"maze://campaign/{self.write_camp['id']}/summary"
        read_uri = f"maze://campaign/{self.read_camp['id']}/summary"
        closed_uri = f"maze://campaign/{self.closed_camp['id']}/summary"
        self.assertEqual(uris[0], mcp_guide.GUIDE_URI)
        self.assertIn(write_uri, uris)
        self.assertIn(read_uri, uris)
        self.assertNotIn(closed_uri, uris)
        for r in listed:
            self.assertTrue(r["name"] and r["mimeType"], r)
        self.assertIn("Ouverte en lecture", next(r["title"] for r in listed if r["uri"] == read_uri))

        guide = self.ok("resources/read", {"uri": mcp_guide.GUIDE_URI})["contents"]
        self.assertEqual(guide, [{"uri": mcp_guide.GUIDE_URI, "mimeType": "text/markdown", "text": mcp_guide.GUIDE}])

        summary = self.ok("resources/read", {"uri": read_uri})["contents"][0]
        self.assertEqual((summary["uri"], summary["mimeType"]), (read_uri, "application/json"))
        data = json.loads(summary["text"])
        self.assertEqual(data["campaign"]["name"], "Ouverte en lecture")
        self.assertIn("Lecteur Ombrage", summary["text"])

        # Campagne fermée, inexistante, adresse inconnue : introuvable (-32002), sans rien dévoiler.
        for uri in (closed_uri, "maze://campaign/999999/summary", "maze://autre", "https://example.org/"):
            err = self.error("resources/read", {"uri": uri})
            self.assertEqual(err["code"], -32002, uri)
            self.assertEqual(err["data"], {"uri": uri})
            self.assertNotIn("Invisible Varnak", json.dumps(err, ensure_ascii=False))
        self.assertEqual(self.error("resources/read", {})["code"], -32602)
        self.assertEqual(self.error("resources/read", {"uri": 12})["code"], -32602)

        templates = self.ok("resources/templates/list")["resourceTemplates"]
        self.assertEqual(templates[0]["uriTemplate"], "maze://campaign/{campaign_id}/summary")

    def test_resources_follow_suspension(self):
        s = self.s
        code, _ = s.call("PUT", "/api/admin/claude", {"enabled": False})
        self.assertEqual(code, 200)
        try:
            uris = [r["uri"] for r in self.ok("resources/list")["resources"]]
            self.assertEqual(uris, [mcp_guide.GUIDE_URI])
            uri = f"maze://campaign/{self.read_camp['id']}/summary"
            self.assertEqual(self.error("resources/read", {"uri": uri})["code"], -32002)
        finally:
            self.assertEqual(s.call("PUT", "/api/admin/claude", {"enabled": True})[0], 200)

    def test_prompts(self):
        prompts = self.ok("prompts/list")["prompts"]
        names = [p["name"] for p in prompts]
        self.assertEqual(names, ["prepare_session", "populate_place", "summarize_campaign", "review_document",
                                 "tidy_campaign", "import_list"])
        for p in prompts:
            self.assertTrue(p["title"] and p["description"])
            self.assertNotIn("_build", p)
            self.assertEqual(p["arguments"][0], {"name": "campaign_id", "description": "Numéro de la campagne "
                                                 "(list_campaigns)", "required": True})

        result = self.ok("prompts/get", {"name": "populate_place",
                                         "arguments": {"campaign_id": "7", "place": "Port-Brume",
                                                       "brief": "trois tavernes"}})
        self.assertTrue(result["description"])
        [message] = result["messages"]
        self.assertEqual(message["role"], "user")
        self.assertEqual(message["content"]["type"], "text")
        text = message["content"]["text"]
        for needle in ("campagne 7", "Port-Brume", "trois tavernes", "create kind place", "link kind membership",
                       "items"):
            self.assertIn(needle, text)

        # Chaque invite, avec ses seuls arguments obligatoires, cite des outils du jeu final.
        for p in prompts:
            args = {a["name"]: "3" if a["name"] == "campaign_id" else "Valeur" for a in p["arguments"] if a["required"]}
            text = self.ok("prompts/get", {"name": p["name"], "arguments": args})["messages"][0]["content"]["text"]
            self.assertIn("get_campaign", text, p["name"])

        players = self.ok("prompts/get", {"name": "summarize_campaign",
                                          "arguments": {"campaign_id": "3", "audience": "joueurs"}})
        self.assertIn("secrets", players["messages"][0]["content"]["text"])

    def test_prompt_errors(self):
        cases = [
            {"name": "inconnue", "arguments": {"campaign_id": "1"}},
            {"arguments": {"campaign_id": "1"}},
            {"name": "populate_place", "arguments": {"campaign_id": "1"}},           # place manquant
            {"name": "populate_place", "arguments": {"campaign_id": "1", "place": "  "}},
            {"name": "tidy_campaign"},                                                # campaign_id manquant
            {"name": "tidy_campaign", "arguments": {"campaign_id": "un"}},
            {"name": "tidy_campaign", "arguments": ["1"]},
            {"name": "import_list", "arguments": {"campaign_id": "1", "text": {"a": 1}}},
        ]
        for params in cases:
            err = self.error("prompts/get", params)
            self.assertEqual(err["code"], -32602, params)
            self.assertTrue(err["message"])
        self.assertIn("place", self.error("prompts/get", cases[2])["message"])


# Les tests hérités de ConnectorTest tournent déjà dans tests.test_connector.
for _name in [n for n in vars(test_connector.ConnectorTest) if n.startswith("test_")]:
    setattr(GuideProtocolTest, _name, None)
del _name


if __name__ == "__main__":
    unittest.main()
