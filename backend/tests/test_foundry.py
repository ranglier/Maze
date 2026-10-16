"""Accès de Foundry VTT : jetons, export des fiches, médias, CORS et module maze-sync (foundry.py,
foundry_html.py, migration 021).

Lancer : cd backend && python -m unittest -q tests.test_foundry
"""

from __future__ import annotations

import io
import json
import re
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from maze.foundry_html import clean, field_html, markdown_to_html
from tests.test_api import ADMIN_PASSWORD, Server, multipart, png_bytes, rewind_schema, undo_entry_revision
from tests.test_maps import form, jpeg


def counter():
    n = [0]

    def next_id() -> str:
        n[0] += 1
        return f"secret-{n[0]}"
    return next_id


class HtmlTest(unittest.TestCase):
    def test_clean(self):
        source = (
            '<h1 data-align="center">Titre</h1><p data-align="justify">Voir <a class="mention" href="#mention" '
            'data-mention="npc:12">Ferran</a>, <a class="mention" data-mention="doc:3">Acte 1</a> et '
            '<a href="javascript:alert(1)">ça</a> ou <a href="https://example.org/a?b=1&amp;c=2">là</a>.</p>'
            '<div class="callout" data-callout="secret"><p>Caché <span class="comment-anchor" data-comment="4">'
            'commenté</span></p><div data-callout="secret"><p>Imbriqué</p></div></div>'
            '<div class="callout" data-callout="read"><p>À lire</p></div>'
            '<script>alert(1)</script><p style="color:red" onclick="x()"><b>Gras</b> <i>it</i></p><p></p>'
            '<table><tr><td colspan="2" style="x">a</td></tr></table><img src="/media/1/a.webp">')
        out = clean(source, counter())
        self.assertEqual(out, (
            '<h3>Titre</h3><p>Voir <a data-maze="npc:12">Ferran</a>, <em>Acte 1</em> et ça ou '
            '<a href="https://example.org/a?b=1&amp;c=2" rel="noopener noreferrer">là</a>.</p>'
            '<section class="secret" id="secret-1"><p>Caché commenté</p><div><p>Imbriqué</p></div></section>'
            '<blockquote class="maze-read"><p>À lire</p></blockquote>'
            '<p><strong>Gras</strong> <em>it</em></p><table><tr><td colspan="2">a</td></tr></table>'))
        # Lien dans un lien : seul le premier est gardé.
        self.assertEqual(clean('<p><a data-mention="npc:1">A <a data-mention="npc:2">B</a></a></p>', counter()),
                         '<p><a data-maze="npc:1">A B</a></p>')
        # Dans un passage déjà secret : pas de bloc secret imbriqué.
        self.assertEqual(clean('<div data-callout="secret"><p>x</p></div>', counter(), in_secret=True),
                         "<div><p>x</p></div>")
        # Balises mal fermées : refermées, texte échappé.
        self.assertEqual(clean("<p><strong>a < b</p>", counter()), "<p><strong>a &lt; b</strong></p>")

    def test_clean_public_moves_secrets_out(self):
        """Audit M6 (FRONT-1) : dans un champ public, un encadré « Secret » (ou un section.secret) n'est pas écrit
        dans la sortie : il est rendu à part, pour la page réservée au MJ."""
        secrets: list[str] = []
        out = clean('<p>Grand.</p><div class="callout" data-callout="secret"><p>Est un <strong>démon</strong>.</p>'
                    '<div data-callout="secret"><p>Imbriqué</p></div></div><p>Voûté.</p>'
                    '<section class="secret" id="secret-x"><p>Déjà au format Foundry</p></section>',
                    counter(), secrets=secrets)
        self.assertEqual(out, "<p>Grand.</p><p>Voûté.</p>")
        self.assertNotIn("démon", out)
        self.assertEqual(secrets, ["<p>Est un <strong>démon</strong>.</p><div><p>Imbriqué</p></div>",
                                   "<p>Déjà au format Foundry</p>"])
        # Sans liste (bio de l'acteur) : passages secrets simplement retirés.
        secrets = []
        self.assertEqual(field_html('<p>a</p><div data-callout="secret"><p>b</p></div>', str, counter(),
                                    secrets=secrets), "<p>a</p>")
        self.assertEqual(secrets, ["<p>b</p>"])

    def test_markdown(self):
        resolve = {"aldo ferran": "npc:11", "cercle": "faction:6"}.get
        md = ("- Homme de conviction, **droit** et *intègre* ([[Aldo Ferran]]).\n- Croit au [[Conclave]].\n\n"
              "Paragraphe\nsur deux lignes avec [lien](https://example.org) et `code`.\n\n## Titre\n> cité")
        self.assertEqual(markdown_to_html(md, resolve), (
            '<ul><li><p>Homme de conviction, <strong>droit</strong> et <em>intègre</em> '
            '(<a class="mention" data-mention="npc:11">Aldo Ferran</a>).</p></li>'
            '<li><p>Croit au Conclave.</p></li></ul>'
            '<p>Paragraphe<br>sur deux lignes avec <a href="https://example.org">lien</a> et <code>code</code>.</p>'
            '<h2>Titre</h2><blockquote><p>cité</p></blockquote>'))
        # Passage par le nettoyage : mention, titre décalé, lien sûr.
        out = field_html("Voir [[cercle]]\n\n# Haut", resolve, counter())
        self.assertEqual(out, '<p>Voir <a data-maze="faction:6">cercle</a></p><h3>Haut</h3>')
        self.assertEqual(field_html("snake_case_name et 2 * 3 * 4", resolve, counter()),
                         "<p>snake_case_name et 2 * 3 * 4</p>")
        self.assertEqual(field_html("", resolve, counter()), "")

    def test_markdown_linear(self):
        """Audit PERF-6 : emphase et titres en temps linéaire (avant : 32 s pour 99 000 caractères « *a », 72 s pour un
        titre de 4000 caractères, serveur figé par le GIL à chaque synchronisation de Foundry)."""
        cases = {"emphase *": "*a " * 66_000, "gras **": "**a " * 50_000, "emphase _": "_a " * 66_000,
                 "gras __": "__a " * 50_000, "titre": "#" + " " * 100_000 + "a" + " " * 100_000 + "b",
                 "titre fermé": "# C" + " #" * 100_000}
        for label, text in cases.items():
            started = time.perf_counter()
            markdown_to_html(text, lambda name: None)
            self.assertLess(time.perf_counter() - started, 2, label)
        # Rendus attendus (CommonMark, comme marked dans l'interface) : séquence fermante après une espace seulement
        # (contre-revue X3 : un rstrip(" #") coupait « C# # » en « C »).
        for md, html in [("# C# #", "<h1>C#</h1>"), ("# C#", "<h1>C#</h1>"), ("## Titre ##", "<h2>Titre</h2>"),
                         ("# Titre #   ", "<h1>Titre</h1>"), ("###   Espaces   ", "<h3>Espaces</h3>"),
                         ("# #", "<h1></h1>"), ("**gras avec * dedans**", "<p><strong>gras avec * dedans</strong></p>"),
                         ("*a * b*", "<p><em>a * b</em></p>"), ("_mon_fichier_", "<p><em>mon_fichier</em></p>"),
                         ("**a** et __b__, *c* et _d_", "<p><strong>a</strong> et <strong>b</strong>, <em>c</em> et "
                                                        "<em>d</em></p>"),
                         ("*non fermé et **pas plus", "<p>*non fermé et **pas plus</p>")]:
            self.assertEqual(markdown_to_html(md, lambda name: None), html, md)


class FoundryApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def fetch(self, path: str, token: str | None = None, method: str = "GET", headers: dict | None = None):
        req = urllib.request.Request(self.s.base + path, method=method, headers=dict(headers or {}))
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers), err.read()

    def campaign(self, name: str) -> int:
        _, camp = self.s.call("POST", "/api/campaigns", {"name": name})
        self.addCleanup(self.s.call, "DELETE", f"/api/campaigns/{camp['id']}")
        return camp["id"]

    def test_tokens_export_media_and_cors(self):
        s = self.s
        cid = self.campaign("Les Ombres de Morvane")
        other = self.campaign("Autre")
        _, cercle = s.call("POST", f"/api/campaigns/{cid}/factions", {
            "name": "Cercle de Brume", "kind": "Conclave", "summary": "Cercle secret",
            "goals": "<ul><li><p>Éteindre le Phare</p></li></ul>", "secrets": "Nourrit la menace."})
        _, ferran = s.call("POST", f"/api/campaigns/{cid}/npcs", {
            "name": "Aldo Ferran", "title": "Haut-Veilleur", "status": "vivant", "tags": ["Pilier"],
            "summary": "Puritain.", "personality": "- Droit\n- Loyal envers [[Lise Ardel]]",
            "appearance": '<p>Quarantaine, ami de <a class="mention" data-mention="npc:999999">Inconnu</a>.</p>',
            "secrets": '<p>Bascule.</p><div data-callout="secret"><p>Déjà secret</p></div>',
            "notes": "<p>Inspiration : romans noirs</p>"})
        _, lise = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Lise Ardel"})
        s.call("POST", "/api/memberships", {"npc_id": ferran["id"], "faction_id": cercle["id"], "role": "Haut-Veilleur",
                                            "is_leader": True})
        s.call("POST", f"/api/campaigns/{cid}/relations", {
            "source_kind": "npc", "source_id": ferran["id"], "target_kind": "npc", "target_id": lise["id"],
            "type": "allie", "label": "Amis de longue date", "description": "Seule <personne> de confiance."})
        s.call("POST", f"/api/campaigns/{cid}/relations", {
            "source_kind": "npc", "source_id": lise["id"], "target_kind": "faction", "target_id": cercle["id"],
            "type": "mentor", "directed": True, "secret": True, "label": "Informatrice"})
        _, sector = s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Secteur Morvane"})
        _, valdis = s.call("POST", f"/api/campaigns/{cid}/places", {
            "name": "Valdis", "parent_id": sector["id"], "ambiance": "Cité immense.", "secrets": "Culte caché."})
        s.call("POST", f"/api/places/{valdis['id']}/links", {"entity_kind": "npc", "entity_id": ferran["id"],
                                                                 "role": "Vit ici"})
        body, ctype = multipart([("portrait.png", png_bytes())])
        self.assertEqual(s.call("POST", f"/api/npcs/{ferran['id']}/images", raw=body, content_type=ctype)[0], 201)
        _, vorne_full = s.call("GET", f"/api/npcs/{ferran['id']}")
        portrait = vorne_full["portrait_image_id"]
        self.assertIsNotNone(portrait)
        body, ctype = form({"name": "Secteur", "place_id": str(sector["id"])}, ("morvane.jpg", jpeg((6000, 3000))))
        code, sector_map = s.call("POST", f"/api/campaigns/{cid}/maps", raw=body, content_type=ctype)
        self.assertEqual(code, 201, sector_map)
        self.assertEqual(s.call("PUT", f"/api/maps/{sector_map['id']}/pins/{valdis['id']}",
                                {"x": 0.25, "y": 0.5})[0], 200)
        _, stranger = s.call("POST", f"/api/campaigns/{other}/npcs", {"name": "Étranger"})
        body, ctype = multipart([("x.png", png_bytes())])
        s.call("POST", f"/api/npcs/{stranger['id']}/images", raw=body, content_type=ctype)
        _, stranger_full = s.call("GET", f"/api/npcs/{stranger['id']}")

        # Jeton : affiché une fois, empreinte seulement en base, campagnes choisies.
        code, created = s.call("POST", "/api/admin/foundry/tokens", {"name": "Table du jeudi", "campaigns": [cid]})
        self.assertEqual(code, 201, created)
        token = created["token"]
        self.assertTrue(token.startswith("mzf_"))
        self.assertNotIn(token, json.dumps(s.call("GET", "/api/admin/foundry")[1]))
        state = s.call("GET", "/api/admin/foundry")[1]
        self.assertEqual([(t["name"], t["campaigns"], t["hint"]) for t in state["tokens"]],
                         [("Table du jeudi", [cid], token[4:8])])
        self.assertTrue(state["manifest_url"].endswith("/foundry/module.json"))
        self.assertEqual(s.call("POST", "/api/admin/foundry/tokens", {"name": "x", "campaigns": [999999]})[0], 400)
        # Réservé aux administrateurs ; un client sans session est refusé.
        self.assertEqual(s.client().call("GET", "/api/admin/foundry")[0], 401)

        # Préflight CORS et en-têtes, sans session ni jeton.
        code, headers, _ = self.fetch("/api/foundry/v1/campaigns", method="OPTIONS",
                                      headers={"Origin": "http://10.0.0.30:30000",
                                               "Access-Control-Request-Headers": "authorization"})
        self.assertEqual(code, 204)
        self.assertEqual(headers.get("access-control-allow-origin"), "*")
        self.assertIn("authorization", headers.get("access-control-allow-headers", ""))
        self.assertNotIn("access-control-allow-credentials", {k.lower() for k in headers})
        code, headers, _ = self.fetch("/api/foundry/v1/campaigns")
        self.assertEqual((code, headers.get("access-control-allow-origin")), (401, "*"))
        self.assertEqual(self.fetch("/api/foundry/v1/campaigns", token="mzf_faux")[0], 401)
        # La session de l'administrateur ne suffit pas : seul le jeton compte.
        self.assertEqual(s.call("GET", "/api/foundry/v1/campaigns")[0], 401)
        # Les autres routes de l'API ne sont pas ouvertes au jeton.
        self.assertEqual(self.fetch(f"/api/campaigns/{cid}", token=token)[0], 401)

        code, _, raw = self.fetch("/api/foundry/v1/campaigns", token=token)
        listing = json.loads(raw)
        self.assertEqual(code, 200)
        self.assertEqual([(c["id"], c["npcs"], c["factions"], c["places"], c["maps"]) for c in listing["campaigns"]],
                         [(cid, 2, 1, 2, 1)])
        self.assertEqual(self.fetch(f"/api/foundry/v1/campaigns/{other}/export", token=token)[0], 404)

        code, headers, raw = self.fetch(f"/api/foundry/v1/campaigns/{cid}/export", token=token)
        self.assertEqual((code, headers.get("access-control-allow-origin")), (200, "*"))
        data = json.loads(raw)
        self.assertEqual(data["format"], "maze-foundry/1")
        npc = {n["name"]: n for n in data["npcs"]}
        v = npc["Aldo Ferran"]
        self.assertEqual((v["group"], v["cover"], v["faction_names"]), (cercle["id"], portrait, ["Cercle de Brume"]))
        self.assertEqual([i["id"] for i in v["images"]], [portrait])
        html = v["html"]
        self.assertTrue(html.startswith(f'<p><img data-maze-image="{portrait}" alt="Aldo Ferran"></p>'))
        self.assertIn(f'<a data-maze="faction:{cercle["id"]}">Cercle de Brume</a> (Haut-Veilleur) - chef', html)
        self.assertIn(f'<a data-maze="place:{valdis["id"]}">Valdis</a> : Vit ici', html)
        self.assertIn(f'Loyal envers <a data-maze="npc:{lise["id"]}">Lise Ardel</a>', html)
        self.assertIn("ami de <a data-maze=\"npc:999999\">Inconnu</a>", html)  # mention orpheline : le module la rend en texte
        self.assertIn(f'<strong>Amis de longue date (Allié)</strong> : <a data-maze="npc:{lise["id"]}">Lise Ardel</a>. '
                      "Seule &lt;personne&gt; de confiance.", html)
        # Secrets et notes du MJ dans la page réservée au MJ, en blocs secrets ; l'encadré « Secret » des secrets
        # n'y crée pas de bloc imbriqué. Rien de secret dans la page publique.
        secret = v["secret_html"]
        secret_blocks = re.findall(r'<section class="secret" id="(secret-[0-9a-f]{16})"><h2>([^<]+)</h2>', secret)
        self.assertEqual([title for _, title in secret_blocks], ["Secrets", "Notes du MJ"])
        self.assertEqual(len({sid for sid, _ in secret_blocks}), 2)
        self.assertIn("<div><p>Déjà secret</p></div>", secret)
        self.assertNotIn("Bascule", html)
        self.assertNotIn("secret", html)
        self.assertEqual(data["instance"], listing["instance"])
        self.assertRegex(v["created"], r"^\d{4}-\d\d-\d\dT")
        self.assertRegex(data["instance"], r"^[0-9a-f]{12}$")
        self.assertNotIn("Secrets", v["actor_bio"])
        self.assertIn("<h2>Personnalité</h2>", v["actor_bio"])
        h = npc["Lise Ardel"]["secret_html"]
        self.assertIn(f'<h2>Relations secrètes</h2><ul><li><p><strong>Informatrice (Mentor)</strong> : Lise Ardel → '
                      f'<a data-maze="faction:{cercle["id"]}">Cercle de Brume</a></p></li></ul></section>', h)
        f = data["factions"][0]
        self.assertIn(f'<h2>Membres</h2><ul><li><p><a data-maze="npc:{ferran["id"]}">Aldo Ferran</a> : Haut-Veilleur (chef)',
                      f["html"])
        self.assertIn("<h2>Objectifs</h2><ul><li><p>Éteindre le Phare</p></li></ul>", f["html"])
        places = {p["name"]: p for p in data["places"]}
        self.assertEqual((places["Secteur Morvane"]["has_children"], places["Valdis"]["parent"]),
                         (True, sector["id"]))
        self.assertIn('<h2>Ambiance</h2><blockquote class="maze-read"><p>Cité immense.</p></blockquote>',
                      places["Valdis"]["html"])
        self.assertIn(f'<h2>Sous-lieux</h2><ul><li><p><a data-maze="place:{valdis["id"]}">Valdis</a>',
                      places["Secteur Morvane"]["html"])
        (m,) = data["maps"]
        self.assertEqual((m["place"], m["width"], m["height"], m["pins"]),
                         (sector["id"], 6000, 3000, [{"place": valdis["id"], "x": 0.25, "y": 0.5}]))
        self.assertTrue(m["preview"].endswith(".webp") and m["file"].endswith(".jpg"))
        # Export stable : mêmes empreintes d'un appel à l'autre.
        again = json.loads(self.fetch(f"/api/foundry/v1/campaigns/{cid}/export", token=token)[2])
        self.assertEqual([n["hash"] for n in again["npcs"]], [n["hash"] for n in data["npcs"]])

        # Médias : seulement ceux des campagnes du jeton.
        path = data["files"][str(portrait)]["path"]
        code, headers, raw = self.fetch(f"/api/foundry/v1/media/{path}", token=token)
        self.assertEqual((code, headers.get("content-type"), headers.get("access-control-allow-origin")),
                         (200, "image/webp", "*"))
        self.assertEqual(self.fetch(f"/api/foundry/v1/media/{m['file']}", token=token)[0], 200)
        self.assertEqual(self.fetch(f"/api/foundry/v1/media/{path}")[0], 401)
        self.assertEqual(self.fetch("/api/foundry/v1/media/1/inexistant.webp")[0], 401)  # jeton vérifié d'abord
        foreign = stranger_full["images"][0]["url"].removeprefix("/media/")
        self.assertEqual(self.fetch(f"/api/foundry/v1/media/{foreign}", token=token)[0], 404)
        self.assertEqual(self.fetch("/api/foundry/v1/media/../maze.db", token=token)[0], 404)

        # Campagnes modifiables ; jeton révoqué = refusé.
        tid = created["token_id"]
        code, state = s.call("PUT", f"/api/admin/foundry/tokens/{tid}", {"campaigns": [cid, other]})
        self.assertEqual((code, state["tokens"][0]["campaigns"]), (200, sorted([cid, other])))
        listing = json.loads(self.fetch("/api/foundry/v1/campaigns", token=token)[2])
        self.assertEqual(len(listing["campaigns"]), 2)
        self.assertEqual(self.fetch(f"/api/foundry/v1/media/{foreign}", token=token)[0], 200)
        self.assertEqual(s.call("DELETE", f"/api/admin/foundry/tokens/{tid}")[0], 200)
        self.assertEqual(self.fetch("/api/foundry/v1/campaigns", token=token)[0], 401)
        self.assertEqual(s.call("DELETE", f"/api/admin/foundry/tokens/{tid}")[0], 404)

    def test_secret_callout_in_public_field(self):
        """Audit M6 (FRONT-1) : un encadré « Secret » placé dans un champ public (apparence, description, accroche…)
        ne part ni dans la page « Fiche », ni dans la biographie de l'acteur ; il est déplacé dans la page
        « Secrets (MJ) »."""
        s = self.s
        cid = self.campaign("Fuites")
        box = '<div class="callout" data-callout="secret"><p>Est un démon.</p></div>'
        s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Ferran", "appearance": f"<p>Grand.</p>{box}",
                                                     "personality": f"<p>Calme.</p>{box}"})
        s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "Cercle", "description": f"<p>Conclave.</p>{box}"})
        s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Sorn", "ambiance": f"<p>Bruyante.</p>{box}",
                                                       "description": f"<p>Immense.</p>{box}"})
        _, created = s.call("POST", "/api/admin/foundry/tokens", {"name": "Fuites", "campaigns": [cid]})
        self.addCleanup(s.call, "DELETE", f"/api/admin/foundry/tokens/{created['token_id']}")
        code, _, raw = self.fetch(f"/api/foundry/v1/campaigns/{cid}/export", token=created["token"])
        self.assertEqual(code, 200)
        data = json.loads(raw)
        (npc,), (faction,), (place,) = data["npcs"], data["factions"], data["places"]
        for entry in (npc, faction, place):
            self.assertNotIn("démon", entry["html"], entry["name"])
            self.assertNotIn("secret", entry["html"], entry["name"])
            self.assertIn("Est un démon.", entry["secret_html"], entry["name"])
        self.assertNotIn("démon", npc["actor_bio"])
        self.assertIn("<p>Grand.</p>", npc["html"])
        self.assertIn("<p>Grand.</p>", npc["actor_bio"])
        titles = re.findall(r'<section class="secret" id="secret-[0-9a-f]{16}"><h2>([^<]+)</h2>', npc["secret_html"])
        self.assertEqual(titles, ["Apparence : passage secret", "Personnalité : passage secret"])
        self.assertEqual(re.findall(r"<h2>([^<]+)</h2>", place["secret_html"]),
                         ["Ambiance : passage secret", "Description : passage secret"])
        self.assertIn('<blockquote class="maze-read"><p>Bruyante.</p></blockquote>', place["html"])

    def test_scenario_selection(self):
        """Fiches d'un scénario : citées dans lui et ses pages, ajouts et retraits de sa carte, voisines à part."""
        s = self.s
        cid = self.campaign("Scénarios")
        mk = lambda path, data: s.call("POST", path, data)[1]  # noqa: E731
        cercle = mk(f"/api/campaigns/{cid}/factions", {"name": "Cercle"})
        ferran = mk(f"/api/campaigns/{cid}/npcs", {"name": "Ferran"})
        lise = mk(f"/api/campaigns/{cid}/npcs", {"name": "Lise"})
        added = mk(f"/api/campaigns/{cid}/npcs", {"name": "Ajouté"})
        dropped = mk(f"/api/campaigns/{cid}/npcs", {"name": "Retiré"})
        mk(f"/api/campaigns/{cid}/npcs", {"name": "Absent"})
        bar = mk(f"/api/campaigns/{cid}/places", {"name": "Bar"})
        sorn = mk(f"/api/campaigns/{cid}/places", {"name": "Sorn"})
        s.call("POST", "/api/memberships", {"npc_id": ferran["id"], "faction_id": cercle["id"]})
        s.call("POST", f"/api/places/{sorn['id']}/links", {"entity_kind": "npc", "entity_id": ferran["id"]})
        s.call("POST", f"/api/places/{bar['id']}/links", {"entity_kind": "npc", "entity_id": lise["id"]})
        mention = lambda kind, e: f'<a class="mention" data-mention="{kind}:{e["id"]}">{e["name"]}</a>'  # noqa: E731
        sc = mk(f"/api/campaigns/{cid}/documents", {"title": "Le bal",
                                                     "content": f"<p>{mention('npc', ferran)} {mention('npc', dropped)}</p>"})
        mk(f"/api/campaigns/{cid}/documents", {"parent_id": sc["id"], "title": "Acte I",
                                               "content": f"<p>Au {mention('place', bar)}.</p>"})
        other = mk(f"/api/campaigns/{cid}/documents", {"title": "Autre"})
        self.assertEqual(s.call("PUT", f"/api/documents/{sc['id']}/map/nodes/npc/{added['id']}", {"state": "added"})[0], 200)
        self.assertEqual(s.call("PUT", f"/api/documents/{sc['id']}/map/nodes/npc/{dropped['id']}",
                                {"state": "excluded"})[0], 200)
        _, created = s.call("POST", "/api/admin/foundry/tokens", {"name": "Scénarios", "campaigns": [cid]})
        self.addCleanup(s.call, "DELETE", f"/api/admin/foundry/tokens/{created['token_id']}")
        token = created["token"]

        listing = json.loads(self.fetch("/api/foundry/v1/campaigns", token=token)[2])
        (camp,) = listing["campaigns"]
        self.assertEqual([(x["id"], x["title"], x["status"]) for x in camp["scenarios"]],
                         [(sc["id"], "Le bal", "brouillon"), (other["id"], "Autre", "brouillon")])
        self.assertNotIn("cast", camp["scenarios"][0])
        data = json.loads(self.fetch(f"/api/foundry/v1/campaigns/{cid}/export", token=token)[2])
        bal = next(x for x in data["scenarios"] if x["id"] == sc["id"])
        self.assertEqual(bal["cast"], {"npc": sorted([ferran["id"], added["id"]]), "faction": [], "place": [bar["id"]]})
        self.assertEqual(bal["related"], {"npc": [lise["id"]], "faction": [cercle["id"]], "place": [sorn["id"]]})
        empty = next(x for x in data["scenarios"] if x["id"] == other["id"])
        self.assertEqual(empty["cast"], {"npc": [], "faction": [], "place": []})
        # Lise est présente au bar cité : voisine. L'export complet reste là (5 PNJ).
        self.assertEqual(len(data["npcs"]), 5)

    def test_grant_removed_with_campaign(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Éphémère"})
        _, created = s.call("POST", "/api/admin/foundry/tokens", {"name": "Temp", "campaigns": [camp["id"]]})
        self.addCleanup(s.call, "DELETE", f"/api/admin/foundry/tokens/{created['token_id']}")
        self.assertEqual(s.call("DELETE", f"/api/campaigns/{camp['id']}")[0], 204)
        state = s.call("GET", "/api/admin/foundry")[1]
        (tok,) = [t for t in state["tokens"] if t["id"] == created["token_id"]]
        self.assertEqual(tok["campaigns"], [])

    def test_restore_revokes_tokens(self):
        """Une sauvegarde restaurée ne ressuscite pas un jeton (révoqué depuis, ou simplement ancien)."""
        from maze import backup
        from maze.config import load_settings
        s = self.s
        _, created = s.call("POST", "/api/admin/foundry/tokens", {"name": "Avant", "campaigns": []})
        self.assertEqual(self.fetch("/api/foundry/v1/campaigns", token=created["token"])[0], 200)
        archive = backup.backup(0, load_settings())
        backup.restore(archive, load_settings())
        self.assertEqual(s.admin.login("admin", ADMIN_PASSWORD), 200)   # restauration : sessions fermées
        self.assertEqual(self.fetch("/api/foundry/v1/campaigns", token=created["token"])[0], 401)
        self.assertEqual(s.call("GET", "/api/admin/foundry")[1]["tokens"], [])

    def test_module_manifest_and_zip(self):
        code, headers, raw = self.fetch("/foundry/module.json")
        self.assertEqual(code, 200)
        manifest = json.loads(raw)
        self.assertEqual(manifest["id"], "maze-sync")
        self.assertEqual(manifest["manifest"], f"{self.s.base}/foundry/module.json")
        self.assertEqual(manifest["download"], f"{self.s.base}/foundry/maze-sync.zip")
        code, headers, raw = self.fetch("/foundry/maze-sync.zip")
        self.assertEqual((code, headers.get("content-type")), (200, "application/zip"))
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = set(zf.namelist())
            inner = json.loads(zf.read("module.json"))
            license_text = zf.read("LICENSE").decode("utf-8")
        self.assertEqual(names, {"module.json", "scripts/maze-sync.mjs", "styles/maze-sync.css", "LICENSE"})
        # Licence : chemin relatif au module (format du manifeste de Foundry), texte de l'AGPL joint à l'archive.
        self.assertEqual((manifest["license"], inner["license"]), ("LICENSE", "LICENSE"))
        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", license_text)
        self.assertEqual(license_text, (Path(__file__).resolve().parents[2] / "LICENSE").read_text(encoding="utf-8"))
        self.assertEqual(inner["download"], manifest["download"])
        self.assertEqual(inner["version"], manifest["version"])



class FoundryTokenLifecycleTest(unittest.TestCase):
    """Audit M12 (X1-M1) : un jeton Foundry ne survit pas à la rétrogradation, à la désactivation ni à la
    suppression de l'administrateur qui l'a créé (comme les jetons du connecteur Claude) ; token_campaigns exige un
    créateur administrateur actif ; la migration 023 purge les jetons orphelins."""

    def setUp(self):
        self.s = Server()
        self.addCleanup(self.s.stop)
        _, camp = self.s.call("POST", "/api/campaigns", {"name": "Morvane"})
        self.cid = camp["id"]
        _, self.admin2 = self.s.call("POST", "/api/admin/users",
                                     {"username": "admin2", "role": "admin", "password": "mdp-admin2"})
        self.as_admin2 = self.s.client()
        self.assertEqual(self.as_admin2.login("admin2", "mdp-admin2"), 200)

    def fetch(self, token: str) -> int:
        req = urllib.request.Request(self.s.base + "/api/foundry/v1/campaigns", headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status
        except urllib.error.HTTPError as err:
            return err.code

    def token(self, client, name: str) -> dict:
        code, created = client.call("POST", "/api/admin/foundry/tokens", {"name": name, "campaigns": [self.cid]})
        self.assertEqual(code, 201, created)
        self.assertEqual(self.fetch(created["token"]), 200)
        return created

    def listed(self) -> list[str]:
        return [t["name"] for t in self.s.call("GET", "/api/admin/foundry")[1]["tokens"]]

    def relog(self):
        self.assertEqual(self.as_admin2.login("admin2", "mdp-admin2"), 200)

    def test_revoked_with_creator(self):
        s, uid = self.s, self.admin2["id"]
        mine = self.token(s.admin, "Table du jeudi")
        for change in ({"role": "mj"}, {"active": False}):
            theirs = self.token(self.as_admin2, f"Monde de admin2 {change}")
            self.assertEqual(s.call("PATCH", f"/api/admin/users/{uid}", change)[0], 200)
            self.assertEqual(self.fetch(theirs["token"]), 401, change)
            self.assertEqual(self.listed(), ["Table du jeudi"], change)        # liste de l'administration cohérente
            s.call("PATCH", f"/api/admin/users/{uid}", {"role": "admin", "active": True})
            self.relog()
        # Un changement de mot de passe ne révoque pas les jetons (ils servent à un serveur Foundry, pas à une session).
        kept = self.token(self.as_admin2, "Gardé")
        s.call("PATCH", f"/api/admin/users/{uid}", {"password": "autre-mdp-admin2"})
        self.assertEqual(self.fetch(kept["token"]), 200)
        self.assertEqual(s.call("DELETE", f"/api/admin/users/{uid}")[0], 204)
        self.assertEqual(self.fetch(kept["token"]), 401)
        self.assertEqual(self.listed(), ["Table du jeudi"])
        self.assertEqual(self.fetch(mine["token"]), 200)

    def test_token_campaigns_checks_creator(self):
        """Défense en profondeur : un jeton dont le créateur n'est plus administrateur actif (base modifiée hors de
        l'administration) est refusé."""
        import sqlite3
        theirs = self.token(self.as_admin2, "admin2")
        db_path = Path(self.s.tmp.name) / "maze.db"
        for sql in ("UPDATE users SET role = 'mj' WHERE id = ?", "UPDATE users SET role = 'admin', active = 0 WHERE id = ?",
                    "UPDATE foundry_tokens SET created_by = NULL WHERE created_by = ?"):
            with sqlite3.connect(db_path) as conn:
                conn.execute("UPDATE users SET role = 'admin', active = 1 WHERE id = ?", (self.admin2["id"],))
                conn.execute(sql, (self.admin2["id"],))
            self.assertEqual(self.fetch(theirs["token"]), 401, sql)

    def test_migration_purges_orphans(self):
        import sqlite3
        from maze import db
        theirs = self.token(self.as_admin2, "admin2")
        mine = self.token(self.s.admin, "admin")
        _, mj = self.s.call("POST", "/api/admin/users", {"username": "mj", "role": "mj", "password": "mdp-mj-1"})
        db_path = Path(self.s.tmp.name) / "maze.db"
        with sqlite3.connect(db_path) as conn:   # état d'une base 022 : jetons laissés par des comptes changés
            conn.execute("UPDATE foundry_tokens SET created_by = NULL WHERE name = 'admin2'")
            for name, creator in (("de mj", mj["id"]), ("de admin2", self.admin2["id"])):
                conn.execute("INSERT INTO foundry_tokens (name, token_hash, created_by) VALUES (?, ?, ?)",
                             (name, f"empreinte-{name}", creator))
                conn.execute("INSERT INTO foundry_grants (token_id, campaign_id) VALUES (last_insert_rowid(), ?)",
                             (self.cid,))
            conn.execute("UPDATE users SET active = 0 WHERE id = ?", (self.admin2["id"],))
            conn.execute("ALTER TABLE mcp_log DROP COLUMN campaign_name")   # colonne de la migration 024
            undo_entry_revision(conn)   # migration 025
            rewind_schema(conn, 22)
        db.migrate(db_path)
        with sqlite3.connect(db_path) as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT name FROM foundry_tokens")], ["admin"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM foundry_grants").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(db.migration_files()))
        self.assertEqual(self.fetch(theirs["token"]), 401)
        self.assertEqual(self.fetch(mine["token"]), 200)


if __name__ == "__main__":
    unittest.main()
