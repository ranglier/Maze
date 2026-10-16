"""Traduction des messages du serveur (maze.i18n) : catalogues backend/maze/i18n/<langue>/*.json, messages à
paramètres (msg, msgn), langue d'une requête (compte, Accept-Language, MAZE_DEFAULT_LOCALE), traduction des erreurs à
la réponse, langue du compte (migration 030, PUT /api/auth/locale).

Lancer : cd backend && python -m unittest -q tests.test_i18n
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import unittest
from pathlib import Path

from maze import i18n
from tests.test_api import Client, Server

PACKAGE = Path(__file__).resolve().parents[1] / "maze"
CATALOGS = PACKAGE / "i18n"


def placeholders(text: str) -> set[str]:
    return set(i18n.PLACEHOLDER.findall(text))


def catalog_files() -> list[Path]:
    return sorted(p for p in CATALOGS.glob("*/*.json"))


def sources() -> str:
    """Tout le texte du serveur (hors catalogues) : chaque clé d'un catalogue doit y figurer telle quelle."""
    parts = []
    for path in sorted(PACKAGE.rglob("*")):
        if path.is_file() and CATALOGS not in path.parents and path.suffix in (".py", ".mjs", ".js", ".json", ".html"):
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


class CatalogFilesTest(unittest.TestCase):
    """Contrôle des catalogues, comme frontend/test/i18n.test.mjs pour l'interface : JSON valide, un objet par
    fichier, mêmes paramètres {x} des deux côtés, formes du pluriel complètes, clés présentes dans le code."""

    def test_layout(self):
        self.assertTrue(catalog_files(), "aucun catalogue")
        self.assertIn("en", i18n.available_locales())
        self.assertEqual(i18n.available_locales()[0], "fr")
        self.assertFalse((CATALOGS / "fr").exists(), "pas de catalogue pour le français, langue source")
        for path in catalog_files():
            self.assertRegex(path.parent.name, r"^[a-z]{2,3}$", path)
            self.assertRegex(path.stem, r"^[a-z][a-z0-9-]*$", path)

    def test_entries(self):
        problems = []
        for path in catalog_files():
            name = f"{path.parent.name}/{path.name}"
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                problems.append(f"{name} : JSON invalide ({exc})")
                continue
            if not isinstance(data, dict):
                problems.append(f"{name} : un objet JSON attendu")
                continue
            for key, value in data.items():
                where = f"{name} : « {key} »"
                if not key.strip():
                    problems.append(f"{name} : clé vide")
                if isinstance(value, str):
                    if not value.strip():
                        problems.append(f"{where} : traduction vide")
                    elif placeholders(value) != placeholders(key):
                        problems.append(f"{where} : paramètres {sorted(placeholders(value))} au lieu de "
                                        f"{sorted(placeholders(key))}")
                elif isinstance(value, dict):
                    unknown = set(value) - set(i18n.PLURAL_CATEGORIES)
                    if unknown or "other" not in value or not all(isinstance(v, str) and v.strip()
                                                                  for v in value.values()):
                        problems.append(f"{where} : pluriel attendu {{\"one\": ..., \"other\": ...}}")
                        continue
                    # Clé = forme au singulier ; la forme au pluriel française peut avoir les mêmes paramètres.
                    found = set().union(*(placeholders(v) for v in value.values()))
                    if found != placeholders(key) | {"n"} and found != placeholders(key):
                        problems.append(f"{where} : paramètres {sorted(found)} au lieu de {sorted(placeholders(key))}")
                else:
                    problems.append(f"{where} : texte ou pluriel attendu")
        self.assertEqual(problems, [])

    def test_same_key_same_translation_across_zones(self):
        seen: dict[tuple[str, str], tuple[str, object]] = {}
        problems = []
        for path in catalog_files():
            for key, value in json.loads(path.read_text(encoding="utf-8")).items():
                previous = seen.setdefault((path.parent.name, key), (path.name, value))
                if previous[1] != value:
                    problems.append(f"« {key} » : {previous[0]} et {path.name} ne la traduisent pas pareil")
        self.assertEqual(problems, [])

    def test_keys_used_in_code(self):
        """Une clé qui ne figure nulle part dans le code (faute de frappe, message supprimé) ne servirait jamais."""
        text = sources()
        unused = [f"{p.parent.name}/{p.name} : « {key} »" for p in catalog_files()
                  for key in json.loads(p.read_text(encoding="utf-8")) if key not in text]
        self.assertEqual(unused, [])


class MessagesTest(unittest.TestCase):
    def test_msg_is_french_text_and_translatable(self):
        m = i18n.msg("{what} introuvable", what=i18n.msg("Campagne"))
        self.assertIsInstance(m, str)
        self.assertEqual(m, "Campagne introuvable")
        self.assertEqual(json.dumps({"detail": m}, ensure_ascii=False), '{"detail": "Campagne introuvable"}')
        self.assertEqual(i18n.translate(m, "en"), "Campaign not found")
        self.assertEqual(i18n.translate(m, "fr"), "Campagne introuvable")
        # Langue sans catalogue, clé absente : texte français.
        self.assertEqual(i18n.translate(m, "de"), "Campagne introuvable")
        self.assertEqual(i18n.translate(i18n.msg("Phrase inconnue {x}", x=3), "en"), "Phrase inconnue 3")

    def test_plain_parameters_are_never_translated(self):
        """Valeur saisie par un MJ qui ressemble à une clé : recopiée telle quelle."""
        m = i18n.msg("La nature « {label} » existe déjà", label="Campagne")
        self.assertEqual(i18n.translate(m, "en"), "The relation type “Campagne” already exists")

    def test_plain_strings(self):
        self.assertEqual(i18n.translate("Données invalides", "en"), "Invalid data")
        self.assertEqual(i18n.translate("Texte sans traduction", "en"), "Texte sans traduction")
        self.assertIsNone(i18n.translate(None, "en"))
        self.assertEqual(i18n.translate({"a": 1}, "en"), {"a": 1})

    def test_composed_text_recognised(self):
        self.assertEqual(i18n.translate_text("Requête trop volumineuse (max 64 Ko)", "en"),
                         "Request too large (max 64 Ko)")
        self.assertEqual(i18n.translate_text("Authentification requise", "en"), "Authentication required")
        self.assertEqual(i18n.translate_text("Rien de connu", "en"), "Rien de connu")
        self.assertEqual(i18n.translate_text("Authentification requise", "fr"), "Authentification requise")

    def test_plurals(self):
        self.assertEqual(i18n.plural_category("fr", 0), "one")
        self.assertEqual(i18n.plural_category("fr", 1), "one")
        self.assertEqual(i18n.plural_category("fr", 2), "other")
        self.assertEqual(i18n.plural_category("en", 0), "other")
        self.assertEqual(i18n.plural_category("en", 1), "one")
        m = i18n.msgn("{n} lien utilise ce type", "{n} liens utilisent ce type", 3)
        self.assertEqual(m, "3 liens utilisent ce type")
        self.assertEqual(i18n.msgn("{n} lien utilise ce type", "{n} liens utilisent ce type", 0),
                         "0 lien utilise ce type")
        # Sans entrée dans le catalogue : forme française.
        self.assertEqual(i18n.translate(m, "en"), "3 liens utilisent ce type")
        original = i18n.catalog("en").copy()
        try:
            i18n.catalog("en")["{n} lien utilise ce type"] = {"one": "{n} link uses this type",
                                                            "other": "{n} links use this type"}
            self.assertEqual(i18n.translate(m, "en"), "3 links use this type")
            self.assertEqual(i18n.translate(i18n.msgn("{n} lien utilise ce type", "{n} liens utilisent ce type", 1),
                                            "en"), "1 link uses this type")
        finally:
            i18n.catalog("en").clear()
            i18n.catalog("en").update(original)

    def test_negotiate(self):
        known = ("fr", "en")
        self.assertEqual(i18n.negotiate("en-US,en;q=0.9,fr;q=0.8", known), "en")
        self.assertEqual(i18n.negotiate("de-DE,de;q=0.9,fr;q=0.8,en;q=0.7", known), "fr")
        self.assertEqual(i18n.negotiate("fr;q=0.5, en;q=0.9", known), "en")
        self.assertEqual(i18n.negotiate("de, es", known), None)
        self.assertEqual(i18n.negotiate("en;q=0, fr", known), "fr")
        self.assertEqual(i18n.negotiate("*", known), None)
        self.assertEqual(i18n.negotiate("", known), None)
        self.assertEqual(i18n.normalize("en_GB"), "en")
        self.assertEqual(i18n.normalize("français"), "")


class RequestLocaleTest(unittest.TestCase):
    """Sur un vrai serveur : langue du compte, puis Accept-Language, puis MAZE_DEFAULT_LOCALE."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def login_error(self, headers=None):
        client = Client(self.s.base)
        return client.call("POST", "/api/auth/login", {"username": "personne", "password": "mauvais"},
                           headers=headers)

    def test_status_gives_account_and_instance_locale(self):
        _, status = self.s.call("GET", "/api/auth/status")
        self.assertEqual(status["default_locale"], "fr")
        self.assertEqual(status["user"]["locale"], "")
        _, anonymous = Client(self.s.base).call("GET", "/api/auth/status")
        self.assertEqual(anonymous["default_locale"], "fr")
        self.assertIsNone(anonymous["user"])

    def test_error_follows_accept_language(self):
        self.assertEqual(self.login_error(), (401, {"detail": "Identifiant ou mot de passe incorrect"}))
        self.assertEqual(self.login_error({"Accept-Language": "en-US,en;q=0.9"}),
                         (401, {"detail": "Incorrect username or password"}))
        self.assertEqual(self.login_error({"Accept-Language": "de-DE,fr;q=0.5"}),
                         (401, {"detail": "Identifiant ou mot de passe incorrect"}))
        self.assertEqual(self.login_error({"Accept-Language": "de-DE"}),
                         (401, {"detail": "Identifiant ou mot de passe incorrect"}))

    def test_validation_error_heading(self):
        client = Client(self.s.base)
        code, body = client.call("POST", "/api/auth/login", {"username": "x"}, headers={"Accept-Language": "en"})
        self.assertEqual(code, 422)
        self.assertEqual(body["detail"], "Invalid data")
        self.assertEqual(body["errors"][0]["type"], "missing")   # détail des champs : composé par l'interface

    def test_middleware_responses(self):
        """Réponses écrites par les middlewares (session absente, corps trop volumineux annoncé) : traduites aussi."""
        anonymous = Client(self.s.base)
        self.assertEqual(anonymous.call("GET", "/api/campaigns", headers={"Accept-Language": "en"}),
                         (401, {"detail": "Authentication required"}))
        self.assertEqual(anonymous.call("GET", "/api/campaigns"), (401, {"detail": "Authentification requise"}))
        code, body = anonymous.call("POST", "/api/auth/login", raw=b"{}" + b" " * (70 * 1024),
                                    content_type="application/json", headers={"Accept-Language": "en"})
        self.assertEqual((code, body["detail"]), (413, "Request too large (max 64 Ko)"))

    def test_parameters_translated(self):
        code, body = self.s.call("GET", "/api/campaigns/999999", headers={"Accept-Language": "en"})
        self.assertEqual((code, body), (404, {"detail": "Campaign not found"}))
        code, body = self.s.call("GET", "/api/campaigns/999999")
        self.assertEqual((code, body), (404, {"detail": "Campagne introuvable"}))

    def test_account_locale(self):
        user = Client(self.s.base)
        self.assertEqual(self.s.call("POST", "/api/admin/users", {"username": "polyglotte", "password": "motdepasse1"})[0],
                         201)
        self.assertEqual(user.login("polyglotte", "motdepasse1"), 200)
        self.assertEqual(user.call("PUT", "/api/auth/locale", {"locale": "en"}), (200, {"locale": "en"}))
        self.assertEqual(user.call("GET", "/api/auth/status")[1]["user"]["locale"], "en")
        # Le compte l'emporte sur Accept-Language.
        code, body = user.call("GET", "/api/campaigns/999999", headers={"Accept-Language": "fr"})
        self.assertEqual((code, body["detail"]), (404, "Campaign not found"))
        code, body = user.call("GET", "/api/admin/users", headers={"Accept-Language": "fr"})
        self.assertEqual((code, body["detail"]), (403, "Administrators only"))
        # Langue à la connexion suivante.
        again = Client(self.s.base)
        _, logged = again.call("POST", "/api/auth/login", {"username": "polyglotte", "password": "motdepasse1"})
        self.assertEqual(logged["user"]["locale"], "en")
        # Automatique : de nouveau l'en-tête du navigateur.
        self.assertEqual(user.call("PUT", "/api/auth/locale", {"locale": ""}), (200, {"locale": ""}))
        code, body = user.call("GET", "/api/campaigns/999999", headers={"Accept-Language": "fr"})
        self.assertEqual(body["detail"], "Campagne introuvable")
        for bad in ("english", "EN", "en-US", "e", 3, None):
            self.assertEqual(user.call("PUT", "/api/auth/locale", {"locale": bad})[0], 422, bad)
        self.assertEqual(user.call("PUT", "/api/auth/locale", {"locale": "fr", "x": 1})[0], 422)
        self.assertEqual(Client(self.s.base).call("PUT", "/api/auth/locale", {"locale": "en"})[0], 401)


class DefaultLocaleTest(unittest.TestCase):
    def test_instance_default(self):
        s = Server(env={"MAZE_DEFAULT_LOCALE": "en"})
        try:
            self.assertEqual(s.call("GET", "/api/auth/status")[1]["default_locale"], "en")
            anonymous = Client(s.base)
            self.assertEqual(anonymous.call("GET", "/api/campaigns"), (401, {"detail": "Authentication required"}))
            # Accept-Language connu : il l'emporte sur la langue de l'instance.
            self.assertEqual(anonymous.call("GET", "/api/campaigns", headers={"Accept-Language": "fr-FR"}),
                             (401, {"detail": "Authentification requise"}))
        finally:
            s.stop()

    def test_invalid_value_falls_back_to_french(self):
        import os
        import tempfile
        from maze.config import load_settings
        saved = {k: os.environ.get(k) for k in ("MAZE_DATA_DIR", "MAZE_DEFAULT_LOCALE")}
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.environ["MAZE_DATA_DIR"] = tmp
                for raw, expected in (("en-US", "en"), ("EN", "en"), ("", "fr"), ("français", "fr"), ("e", "fr")):
                    os.environ["MAZE_DEFAULT_LOCALE"] = raw
                    self.assertEqual(load_settings().default_locale, expected, raw)
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


class MigrationTest(unittest.TestCase):
    def test_column_default_is_automatic(self):
        s = Server()
        try:
            with contextlib.closing(sqlite3.connect(Path(s.tmp.name) / "maze.db")) as conn:
                columns = {r[1]: r for r in conn.execute("PRAGMA table_info(users)")}
            self.assertIn("locale", columns)
            self.assertEqual(columns["locale"][4], "''")   # valeur par défaut : vide = automatique
            self.assertEqual(columns["locale"][3], 1)      # NOT NULL
        finally:
            s.stop()


if __name__ == "__main__":
    unittest.main()
