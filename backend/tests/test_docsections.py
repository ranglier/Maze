"""Modification d'une fiche rédigée par sections : module pur docsections.py et outil update_document du connecteur
Claude avec operations (vérification par l'API REST).

Lancer : cd backend && python -m unittest -q tests.test_docsections
"""

from __future__ import annotations

import asyncio
import unittest

from maze import db, docsections, mcp
from maze.docsections import SectionError, apply_operations, outline
from tests.test_api import Server, recv, recv_until

DOC = ('<h1>Acte I</h1><p>Intro <span data-comment="3">surlignée</span></p>'
       '<h2>Scène 1</h2><p>Le port.</p><img data-doc-image="7" src="/media/1/a.webp">'
       '<h2>Scène 2</h2><p>La taverne &amp; ses clients.</p>'
       '<h1>Acte II</h1><p>Final.</p>')


def ops(src: str, *operations: dict) -> str:
    return apply_operations(src, list(operations))[0]


class OutlineTest(unittest.TestCase):
    def test_top_level_headings(self):
        self.assertEqual(outline(DOC), [{"level": 1, "text": "Acte I"}, {"level": 2, "text": "Scène 1"},
                                        {"level": 2, "text": "Scène 2"}, {"level": 1, "text": "Acte II"}])
        self.assertEqual(outline(""), [])

    def test_text_levels_and_nesting(self):
        src = ('<h2 data-align="center">  Les   <em>Ombres</em> &amp; <span data-comment="1">co</span><br>suite </h2>'
               '<h5>Petit</h5>'
               '<div data-callout="secret"><h2>Dans un encadré</h2><p>x</p></div>'
               '<blockquote><h1>Citée</h1></blockquote><ul><li><h3>En liste</h3></li></ul>'
               '<table><tr><td><h2>En tableau</h2></td></tr></table><p>fin</p>')
        self.assertEqual(outline(src), [{"level": 2, "text": "Les Ombres & co suite"}, {"level": 3, "text": "Petit"}])

    def test_sloppy_html(self):
        # <p> non fermés, <li> implicites : les titres de premier niveau restent trouvés.
        src = "<p>un<p>deux<h2>A</h2><ul><li>x<li>y</ul><h2>B</h2><p>fin"
        self.assertEqual([h["text"] for h in outline(src)], ["A", "B"])


class OperationsTest(unittest.TestCase):
    def test_replace_section_keeps_heading_and_subsections_go(self):
        out = ops(DOC, {"op": "replace_section", "heading": "acte  i", "content": "<p>Nouveau</p>"})
        self.assertEqual(out, "<h1>Acte I</h1><p>Nouveau</p><h1>Acte II</h1><p>Final.</p>")
        out, changes = apply_operations(DOC, [{"op": "replace_section", "heading": "Scène 1", "content": "<p>N</p>"}])
        self.assertIn("<h2>Scène 1</h2><p>N</p><h2>Scène 2</h2>", out)
        self.assertIn('<span data-comment="3">surlignée</span>', out)   # hors zone : intact
        self.assertEqual(changes, ["Contenu de la section « Scène 1 » remplacé", "1 illustration(s) retirée(s) du texte"])

    def test_replace_section_with_heading(self):
        out = ops(DOC, {"op": "replace_section", "heading": "Scène 2", "include_heading": True,
                        "content": "<h2>Scène deux</h2><p>Autre</p>"})
        self.assertIn('a.webp"><h2>Scène deux</h2><p>Autre</p><h1>Acte II</h1>', out)
        # Dernière section : jusqu'à la fin de la fiche.
        self.assertEqual(ops(DOC, {"op": "replace_section", "heading": "Acte II", "content": ""}),
                         DOC.removesuffix("<p>Final.</p>"))

    def test_insert_append_prepend(self):
        out = ops(DOC, {"op": "insert_after_section", "heading": "Scène 1", "content": "<h2>Scène 1 bis</h2>"})
        self.assertIn('a.webp"><h2>Scène 1 bis</h2><h2>Scène 2</h2>', out)
        out = ops(DOC, {"op": "insert_after_section", "heading": "Acte I", "content": "<h1>Interlude</h1>"})
        self.assertIn("clients.</p><h1>Interlude</h1><h1>Acte II</h1>", out)
        out = ops(DOC, {"op": "insert_before_section", "heading": "Acte II", "content": "<hr>"})
        self.assertIn("clients.</p><hr><h1>Acte II</h1>", out)
        self.assertTrue(ops(DOC, {"op": "insert_after_section", "heading": "Acte II", "content": "<p>Z</p>"})
                        .endswith("<p>Final.</p><p>Z</p>"))
        self.assertEqual(ops(DOC, {"op": "append", "content": "<p>Fin</p>"}, {"op": "prepend", "content": "<p>D</p>"}),
                         "<p>D</p>" + DOC + "<p>Fin</p>")
        self.assertEqual(ops("", {"op": "append", "content": "<p>Seul</p>"}), "<p>Seul</p>")

    def test_delete_section(self):
        out, changes = apply_operations(DOC, [{"op": "delete_section", "heading": "Acte I"}])
        self.assertEqual(out, "<h1>Acte II</h1><p>Final.</p>")
        self.assertEqual(changes[0], "Section « Acte I » supprimée (2 sous-sections comprises)")
        self.assertIn("1 passage(s) commenté(s) retiré(s)", changes[1])
        self.assertIn("1 illustration(s) retirée(s)", changes[2])
        out = ops(DOC, {"op": "delete_section", "heading": "Scène 2"})
        self.assertIn('a.webp"><h1>Acte II</h1>', out)

    def test_nested_levels(self):
        src = "<h2>A</h2><p>a</p><h3>A.1</h3><p>a1</p><h3>A.2</h3><p>a2</p><h2>B</h2><h3>B.1</h3><p>b1</p>"
        self.assertEqual(ops(src, {"op": "delete_section", "heading": "A.1"}),
                         "<h2>A</h2><p>a</p><h3>A.2</h3><p>a2</p><h2>B</h2><h3>B.1</h3><p>b1</p>")
        self.assertEqual(ops(src, {"op": "replace_section", "heading": "A", "content": "<p>x</p>"}),
                         "<h2>A</h2><p>x</p><h2>B</h2><h3>B.1</h3><p>b1</p>")
        # Titre dans un encadré : fait partie de la section qui le contient.
        src = '<h2>A</h2><div data-callout="read"><h2>Lu</h2><p>t</p></div><h2>B</h2>'
        self.assertEqual(ops(src, {"op": "delete_section", "heading": "A"}), "<h2>B</h2>")

    def test_empty_sections(self):
        src = "<h2>A</h2><h2>B</h2>"
        self.assertEqual(ops(src, {"op": "replace_section", "heading": "A", "content": "<p>a</p>"}),
                         "<h2>A</h2><p>a</p><h2>B</h2>")
        self.assertEqual(ops(src, {"op": "insert_after_section", "heading": "A", "content": "<p>a</p>"}),
                         "<h2>A</h2><p>a</p><h2>B</h2>")
        self.assertEqual(ops("<h2></h2><p>x</p>", {"op": "append", "content": "<p>y</p>"}), "<h2></h2><p>x</p><p>y</p>")

    def test_not_found_and_ambiguous(self):
        with self.assertRaises(SectionError) as err:
            apply_operations(DOC, [{"op": "delete_section", "heading": "Scène 3"}])
        self.assertIn("« Scène 3 » introuvable", str(err.exception))
        self.assertIn("« Acte I » (h1), « Scène 1 » (h2), « Scène 2 » (h2), « Acte II » (h1)", str(err.exception))
        with self.assertRaises(SectionError) as err:
            apply_operations("<p>x</p>", [{"op": "delete_section", "heading": "A"}])
        self.assertIn("aucun titre", str(err.exception))
        src = "<h1>Acte I</h1><h2>Lieux</h2><p>1</p><h1>Acte II</h1><h2>lieux </h2><p>2</p>"
        with self.assertRaises(SectionError) as err:
            apply_operations(src, [{"op": "delete_section", "heading": "Lieux"}])
        self.assertIn("ambigu (2 titres)", str(err.exception))
        self.assertIn("1 : h2, sous « Acte I » ; 2 : h2, sous « Acte II »", str(err.exception))
        self.assertEqual(ops(src, {"op": "delete_section", "heading": "Lieux", "occurrence": 2}),
                         "<h1>Acte I</h1><h2>Lieux</h2><p>1</p><h1>Acte II</h1>")
        with self.assertRaises(SectionError) as err:
            apply_operations(src, [{"op": "delete_section", "heading": "Lieux", "occurrence": 3}])
        self.assertIn("que 2 fois", str(err.exception))

    def test_preserves_bytes_outside(self):
        # Rien n'est réécrit hors de la zone visée : attributs, entités, ancres et images restent tels quels.
        src = ('<p data-align="right">A&nbsp;: <span data-comment="12">x</span></p><h2>Zone</h2><p>old</p>'
               '<h2>Garde</h2><p><img data-doc-image="4" alt="Carte <ancienne>" src="/media/2/b.webp"></p>')
        out = ops(src, {"op": "replace_section", "heading": "zone", "content": "<p>new</p>"})
        self.assertEqual(out, src.replace("<p>old</p>", "<p>new</p>"))

    def test_replace_text(self):
        src = '<h2>Le Duc</h2><p title="Duc">Le duc &amp; sa <strong>garde</strong>. Le duc rit.</p><h2>Autre</h2>'
        out, changes = apply_operations(src, [{"op": "replace_text", "find": "duc & sa", "replace": "comte <et> sa"}])
        self.assertIn('<p title="Duc">Le comte &lt;et&gt; sa <strong>garde</strong>. Le duc rit.</p>', out)
        self.assertEqual(changes, ["Texte « duc & sa » remplacé 1 fois"])
        with self.assertRaises(SectionError) as err:
            apply_operations(src, [{"op": "replace_text", "find": "duc", "replace": "comte"}])
        self.assertIn("trouvé 2 fois (max 1)", str(err.exception))
        out = ops(src, {"op": "replace_text", "find": "duc", "replace": "comte", "max": 2})
        self.assertEqual(out.count("comte"), 2)
        self.assertIn('title="Duc"', out)   # attribut jamais modifié
        with self.assertRaises(SectionError):
            apply_operations(src, [{"op": "replace_text", "find": "sa garde", "replace": "x"}])   # à cheval
        with self.assertRaises(SectionError):
            apply_operations(src, [{"op": "replace_text", "find": "title", "replace": "x"}])      # attribut
        # Limité à une section (titre compris).
        out = ops(src, {"op": "replace_text", "find": "Duc", "replace": "Comte", "heading": "le duc"})
        self.assertTrue(out.startswith('<h2>Le Comte</h2><p title="Duc">'))
        with self.assertRaises(SectionError):
            apply_operations(src, [{"op": "replace_text", "find": "Le", "replace": "", "heading": "Autre"}])

    def test_sequential_and_all_or_nothing(self):
        out = ops(DOC, {"op": "insert_after_section", "heading": "Acte II", "content": "<h2>Épilogue</h2>"},
                  {"op": "replace_section", "heading": "Épilogue", "content": "<p>Fin</p>"})
        self.assertTrue(out.endswith("<h2>Épilogue</h2><p>Fin</p>"))
        with self.assertRaises(SectionError) as err:
            apply_operations(DOC, [{"op": "append", "content": "<p>x</p>"},
                                   {"op": "delete_section", "heading": "Absent"}])
        self.assertTrue(str(err.exception).startswith("operations[1] (delete_section) : "))

    def test_fragment_checks(self):
        for bad, why in (("<p>ouvert", "<p> non fermée"), ("<div data-callout=\"read\"><p>x</p>", "<div> non fermée"),
                         ("<p>x</p></div>", "</div> sans balise ouvrante"), ("<p>x</p><img src=\"a", "incomplet"),
                         ("<p>x</p><!-- note", "incomplet"), ("  ", "content vide")):
            with self.assertRaises(SectionError, msg=bad) as err:
                apply_operations(DOC, [{"op": "append", "content": bad}])
            self.assertIn(why, str(err.exception))
        ok = '<p>un<p>deux</p><ul><li>a<li>b</ul><hr><br><table><tr><td>c<td>d</table><img src="x">'
        self.assertTrue(ops(DOC, {"op": "append", "content": ok}).endswith(ok))

    def test_parameters(self):
        for bad, why in (([], "au moins une"), ("x", "au moins une"), ([1], "objet attendu"),
                         ([{"op": "move"}], "op parmi"),
                         ([{"op": "append", "content": "<p>x</p>", "heading": "A"}], "inconnu(s) heading"),
                         ([{"op": "replace_section", "heading": "A"}], "manquant(s) content"),
                         ([{"op": "append", "content": 3}], "content doit être un texte"),
                         ([{"op": "replace_text", "find": "a", "replace": "b", "max": True}], "max doit être un entier"),
                         ([{"op": "replace_text", "find": "a", "replace": "b", "max": 0}], "max entre"),
                         ([{"op": "delete_section", "heading": "A", "occurrence": 0}], "occurrence à partir de 1"),
                         ([{"op": "replace_text", "find": "", "replace": "b"}], "find vide"),
                         ([{"op": "append", "content": "<p>x</p>"}] * (docsections.MAX_OPERATIONS + 1), "au plus")):
            with self.assertRaises(SectionError, msg=repr(bad)) as err:
                apply_operations(DOC, bad)
            self.assertIn(why, str(err.exception))


class UpdateDocumentOperationsTest(unittest.TestCase):
    """Outil update_document du connecteur avec operations, sur un serveur réel ; vérification par l'API REST."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server()
        code, camp = cls.s.call("POST", "/api/campaigns", {"name": "Sections"})
        assert code == 201, camp
        cls.cid = camp["id"]
        with db.transaction(cls.s.server.config.app.state.settings.db_path, write=False) as conn:
            admin = dict(conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone())
        cls.ctx = lambda cls_=cls: mcp.Context(app=cls_.s.server.config.app, user=admin, grants={cls_.cid: "write"})

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def tool(self, handler, **args):
        return asyncio.run(handler(self.ctx(), args))

    def document(self, content: str = DOC) -> dict:
        code, doc = self.s.call("POST", f"/api/campaigns/{self.cid}/documents", {"title": "Scénario"})
        self.assertEqual(code, 201, doc)
        # Contenu posé par PATCH : la création retire les ancres de commentaires (contenu copié).
        code, doc = self.s.call("PATCH", f"/api/documents/{doc['id']}", {"content": content})
        self.assertEqual((code, doc["content"]), (200, content))
        return doc

    def test_operations(self):
        doc = self.document()
        read = self.tool(mcp.get_document, document_id=doc["id"])
        self.assertEqual([h["text"] for h in read["outline"]], ["Acte I", "Scène 1", "Scène 2", "Acte II"])
        out = self.tool(mcp.update_document, document_id=doc["id"], base_revision=read["revision"], status="pret",
                        operations=[{"op": "replace_section", "heading": "scène 2", "content": "<p>Le marché.</p>"},
                                    {"op": "replace_text", "find": "Final.", "replace": "Dénouement."}])
        self.assertEqual(out["revision"], read["revision"] + 1)
        self.assertEqual(out["status"], "pret")
        self.assertEqual(out["changes"], ["Contenu de la section « Scène 2 » remplacé", "Texte « Final. » remplacé 1 fois"])
        _, now = self.s.call("GET", f"/api/documents/{doc['id']}")
        self.assertEqual(now["content"], DOC.replace("<p>La taverne &amp; ses clients.</p>", "<p>Le marché.</p>")
                         .replace("Final.", "Dénouement."))
        _, history = self.s.call("GET", f"/api/documents/{doc['id']}/versions")
        labels = [v["label"] for v in history["versions"]]
        self.assertIn("Avant modification par Claude", labels)
        self.assertIn("Modification par Claude", labels)

    def test_refusals_change_nothing(self):
        doc = self.document()
        rev = doc["revision"]
        cases = (
            ({"content": "<p>x</p>", "operations": [{"op": "append", "content": "<p>y</p>"}]}, "s'excluent"),
            ({"operations": [{"op": "delete_section", "heading": "Absent"}]}, "« Absent » introuvable"),
            ({"operations": [{"op": "append", "content": "<p>y</p>"}], "base_revision": rev - 1}, "modifiée depuis"),
            ({"operations": "append"}, "au moins une opération"),
        )
        for extra, why in cases:
            args = {"document_id": doc["id"], "base_revision": rev, **extra}
            with self.assertRaises(mcp.ToolError, msg=why) as err:
                self.tool(mcp.update_document, **args)
            self.assertIn(why, str(err.exception))
        _, now = self.s.call("GET", f"/api/documents/{doc['id']}")
        self.assertEqual((now["content"], now["revision"]), (DOC, rev))

    def test_unsaved_live_edits(self):
        doc = self.document("<h2>A</h2><p>Départ</p>")
        ws = self.s.admin.live(doc["id"], 921)
        recv_until(ws, "welcome")
        ws.send(bytes([0]) + b"frappe")   # modification reçue, pas encore dans le HTML enregistré
        self.assertEqual(recv(ws)[0], 3)
        with self.assertRaises(mcp.ToolError) as err:
            self.tool(mcp.update_document, document_id=doc["id"], base_revision=doc["revision"],
                      operations=[{"op": "append", "content": "<p>Par Claude</p>"}])
        self.assertIn("La fiche est ouverte", str(err.exception))
        ws.close()

    def test_schema(self):
        schema = mcp.TOOLS_BY_NAME["update_document"]["inputSchema"]["properties"]["operations"]
        self.assertEqual(schema["items"]["properties"]["op"]["enum"], list(docsections.OPS))
        self.assertNotIn("operations", mcp.TOOLS_BY_NAME["update_document"]["inputSchema"]["required"])


if __name__ == "__main__":
    unittest.main()
