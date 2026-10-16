"""Traduction des messages du serveur (maze.i18n) : catalogues backend/maze/i18n/<langue>/*.json, messages à
paramètres (msg, msgn), langue d'une requête (compte, Accept-Language, MAZE_DEFAULT_LOCALE), traduction des erreurs à
la réponse, langue du compte (migration 030, PUT /api/auth/locale) ; catalogues complets (tout texte renvoyé à
l'interface a sa traduction anglaise), données de départ d'une campagne, campagne de démonstration, page
d'autorisation OAuth, export de Foundry dans les deux langues.

Lancer : cd backend && python -m unittest -q tests.test_i18n
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from maze import defaults, foundry, i18n
from tests.test_api import Client, Server

PACKAGE = Path(__file__).resolve().parents[1] / "maze"
CATALOGS = PACKAGE / "i18n"


# Contexte d'une clé « contexte|texte » (i18n.ptr), comme pt() de l'interface.
CONTEXT = re.compile(r"[a-z][\w-]*")


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
        """Une clé qui ne figure nulle part dans le code (faute de frappe, message supprimé) ne servirait jamais. Clé à
        contexte (« foundry|Fiche », i18n.ptr) : le contexte et le texte doivent y figurer."""
        text = sources()

        def used(key: str) -> bool:
            context, sep, rest = key.partition("|")
            if sep and CONTEXT.fullmatch(context):
                return f'"{context}"' in text and rest in text
            return key in text

        unused = [f"{p.parent.name}/{p.name} : « {key} »" for p in catalog_files()
                  for key in json.loads(p.read_text(encoding="utf-8")) if not used(key)]
        self.assertEqual(unused, [])

    def test_context(self):
        """ptr : clé « contexte|texte » (même texte français, autre traduction), texte français si elle manque."""
        self.assertEqual(i18n.translate("Fiche", "en"), "Entry")
        self.assertEqual(i18n.ptr("foundry", "Fiche", "en"), "Sheet")
        self.assertEqual(i18n.ptr("foundry", "Fiche", "fr"), "Fiche")
        self.assertEqual(i18n.ptr("foundry", "Fiche de {name} (Maze)", "en"), "{name}'s sheet (Maze)")
        self.assertEqual(i18n.ptr("inconnu", "Fiche", "en"), "Fiche")
        self.assertEqual(i18n.ptr("foundry", "Fiche", None), "Fiche")


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
        self.assertEqual(i18n.translate(m, "en"), "The relationship type “Campagne” already exists")

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

    def test_unknown_account_locale_is_ignored(self):
        """Code admis par PUT /api/auth/locale mais sans catalogue sur ce serveur (« de ») : la suite du choix
        s'applique (Accept-Language, puis MAZE_DEFAULT_LOCALE), comme pour « Automatique »."""
        user = Client(self.s.base)
        self.assertEqual(self.s.call("POST", "/api/admin/users", {"username": "germaniste", "password": "motdepasse1"})[0],
                         201)
        self.assertEqual(user.login("germaniste", "motdepasse1"), 200)
        self.assertEqual(user.call("PUT", "/api/auth/locale", {"locale": "de"}), (200, {"locale": "de"}))
        self.assertEqual(user.call("GET", "/api/auth/status")[1]["user"]["locale"], "de")   # réglage gardé tel quel
        code, body = user.call("GET", "/api/campaigns/999999", headers=EN)
        self.assertEqual((code, body["detail"]), (404, "Campaign not found"))
        code, body = user.call("GET", "/api/campaigns/999999", headers={"Accept-Language": "de"})
        self.assertEqual((code, body["detail"]), (404, "Campagne introuvable"))
        self.assertIsNone(i18n.account_locale({"locale": "de"}))
        self.assertIsNone(i18n.account_locale({"locale": ""}))
        self.assertIsNone(i18n.account_locale(None))
        self.assertEqual(i18n.account_locale({"locale": "en"}), "en")


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



# --- Catalogues complets -------------------------------------------------------------------------------------

# Fonctions et exceptions dont le premier argument littéral est un texte renvoyé à l'interface (ou à l'administrateur
# dans la page d'autorisation, ou au MJ dans les pages de Foundry). Le connecteur Claude (mcp*.py) reste en français.
MESSAGE_CALLS = {"HTTPException", "msg", "msgn", "tr", "ImageError", "ArchiveError", "TransferError", "SpaceError",
                 "_error_page", "translate"}
# Deuxième argument : refuse(code, texte) et close(code, raison) des connexions temps réel (affichés par l'éditeur).
SECOND_ARG_CALLS = {"refuse", "close"}
WHAT_CALLS = {"get_or_404", "scoped"}   # dernier argument : sorte de fiche du message « {what} introuvable »


def code_messages() -> dict[str, str]:
    """Textes littéraux passés à ces appels dans le code du serveur (hors connecteur Claude) : {texte: fichier}."""
    found: dict[str, str] = {}
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name.startswith("mcp"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            args = (node.args[-1:] if name in WHAT_CALLS else node.args[1:2] if name in SECOND_ARG_CALLS
                    else node.args[:1] if name in MESSAGE_CALLS else [])
            for arg in args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and len(arg.value) > 1:
                    found.setdefault(arg.value, path.name)
    return found


class CompleteCatalogTest(unittest.TestCase):
    """Tout texte que le serveur renvoie à l'interface, crée dans une campagne ou écrit dans une page de Foundry a sa
    traduction anglaise (sinon il resterait en français dans l'interface anglaise)."""

    def assert_translated(self, texts, where: str) -> None:
        en = i18n.catalog("en")
        missing = sorted({t for t in texts if t and t not in en})
        self.assertEqual(missing, [], f"{where} : sans traduction anglaise")

    def test_messages_in_code(self):
        found = code_messages()
        self.assertGreater(len(found), 100)   # le relevé fonctionne
        en = i18n.catalog("en")
        # Clé d'un type de lieu (defaults.place_kind_label("lieu")) : pas un texte.
        missing = sorted(f"{where} : « {text} »" for text, where in found.items() if text not in en and text != "lieu")
        self.assertEqual(missing, [])

    def test_message_tables(self):
        from maze import admin, backup, documents, entries, foundry, image_copy, versions
        self.assert_translated(versions.SYSTEM_LABELS, "versions.SYSTEM_LABELS")
        self.assert_translated(admin.UPDATER_MESSAGES, "admin.UPDATER_MESSAGES")
        self.assert_translated(backup.SPACE_MESSAGES.values(), "backup.SPACE_MESSAGES")
        self.assert_translated(documents.TITLES.values(), "documents.TITLES")
        self.assert_translated([*image_copy.KIND_LABEL.values(), *image_copy.DOC_LABEL.values()], "image_copy")
        self.assert_translated(foundry.STATUS_LABELS.values(), "foundry.STATUS_LABELS")
        self.assert_translated([f"foundry|{text}" for text in foundry.WORLD_LABELS.values()], "foundry.WORLD_LABELS")
        self.assert_translated([f.foundry_label or f.label for kind in entries.KINDS for f in entries.fields(kind)
                                if f.foundry], "entries (Foundry)")
        self.assert_translated([label for _, label, *_ in defaults.DEFAULT_RELATION_TYPES], "natures de lien")
        self.assert_translated([hint for *_, hint in defaults.DEFAULT_RELATION_TYPES], "indications de sens")
        self.assert_translated([label for _, label, _ in defaults.DEFAULT_PLACE_KINDS], "types de lieux")
        for name, value in (("TOO_DEEP (fiches)", documents.TOO_DEEP), ("UNSAVED_EDITS", documents.UNSAVED_EDITS)):
            self.assertNotEqual(i18n.translate(value, "en"), str(value), name)

    def test_demo_texts(self):
        from maze import demo
        texts = [demo.__dict__[n] for n in ("FACTIONS", "NPCS", "MEMBERSHIPS", "RELATIONS", "PLACES", "PLACE_LINKS")]
        used = [f[i] for f in texts[0] for i in (1, 2, 4, 5, 6)]
        used += [n[i] for n in texts[1] for i in (1, 2, 7, 8, 9, 10, 11)]
        used += [m[2] for m in texts[2]] + [r[5] for r in texts[3]]
        used += [p[i] for p in texts[4] for i in (2, 4, 5, 6, 7)] + [link[3] for link in texts[5]]
        self.assert_translated(used, "demo.py")

    def test_specific_patterns_first(self):
        """« Échec de la vérification : {error} » reconnu avant « {name} : {error} »."""
        self.assertEqual(i18n.translate_text("Échec de la vérification : réseau", "en"), "Check failed: réseau")
        self.assertEqual(i18n.translate_text("photo.png : Impossible de traiter cette image", "en"),
                         "photo.png: Impossible de traiter cette image")
        self.assertEqual(i18n.translate_text("3 modification(s) disponible(s).", "en"), "Updates available: 3.")

    def test_exception_message_keeps_template(self):
        from maze.backup import ArchiveError
        exc = ArchiveError(i18n.msg("Base illisible : {error}", error="disk I/O error"))
        self.assertEqual(str(exc), "Base illisible : disk I/O error")
        self.assertEqual(i18n.translate(i18n.message(exc), "en"), "Unreadable database: disk I/O error")
        self.assertEqual(i18n.message(ValueError("brut")), "brut")

    def test_newer_backup_message_fully_translated(self):
        """Restauration d'une sauvegarde plus récente que le code : le détail (db.schema_problem) est un `msg`, traduit
        avec le message qui l'englobe, conseil de restauration compris ; texte français inchangé pour le journal."""
        from maze import backup, db
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "maze.db"
            with contextlib.closing(sqlite3.connect(path)) as conn:
                conn.executescript("CREATE TABLE campaigns (id INTEGER); CREATE TABLE npcs (id INTEGER); "
                                   "CREATE TABLE factions (id INTEGER); PRAGMA user_version = 999;")
            with self.assertRaises(backup.ArchiveError) as caught:
                backup._check_database(path)
        text = i18n.message(caught.exception)
        self.assertIn("Détail : base au schéma 999, plus récent que ce code", str(text))
        self.assertIn(db.RESTORE_HINT, str(text))
        en = i18n.translate(text, "en")
        self.assertTrue(en.startswith("Backup from a newer version of Maze (or another branch): update Maze first. "
                                      "Details: database at schema 999, newer than this code (latest known migration: "),
                        en)
        self.assertIn("restore the backup taken before the update", en)
        self.assertNotRegex(en, r"[éèàùç«»]|base au|restaurez")
        # Migration inconnue (autre branche) et user_version en avance : même traduction complète.
        with contextlib.closing(sqlite3.connect(":memory:")) as conn:
            conn.executescript(db.SCHEMA_TABLE + "; INSERT INTO schema_migrations (version, name, checksum) "
                               "VALUES (1, '001_autre_branche.sql', ''); PRAGMA user_version = 1;")
            unknown = db.schema_problem(conn)
            conn.execute("PRAGMA user_version = 5")
            conn.execute("DELETE FROM schema_migrations")
            conn.execute("INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, '')",
                         (1, db.migration_files()[0][1]))
            ahead = db.schema_problem(conn)
        self.assertIn("001_autre_branche.sql", unknown)
        self.assertTrue(i18n.translate(unknown, "en").startswith(
            "the database contains migrations unknown to this code (001_autre_branche.sql): it comes from a newer"))
        self.assertTrue(i18n.translate(ahead, "en").startswith(
            "database at schema 5 while the latest recorded migration is 001: a version of Maze"), ahead)


# --- Messages sur un vrai serveur ------------------------------------------------------------------------------

EN = {"Accept-Language": "en-GB,en;q=0.9"}


class ServerMessagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def campaign(self, client=None, headers=None, name="Traduction") -> dict:
        code, camp = (client or self.s.admin).call("POST", "/api/campaigns", {"name": name}, headers=headers)
        self.assertEqual(code, 201)
        return camp

    def test_messages_follow_accept_language(self):
        cid = self.campaign()["id"]
        self.assertEqual(self.s.call("POST", f"/api/campaigns/{cid}/relation-types", {"label": "Allié"}, headers=EN),
                         (409, {"detail": "The relationship type “Allié” already exists"}))
        _, kinds = self.s.call("GET", f"/api/campaigns/{cid}/places")
        lieu = next(k for k in kinds["kinds"] if k["key"] == "lieu")
        self.assertEqual(self.s.call("DELETE", f"/api/place-kinds/{lieu['id']}", headers=EN),
                         (400, {"detail": "The “Place” type is the default type: it cannot be deleted"}))
        # Pluriel : forme de la langue.
        _, a = self.s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "A"})
        _, b = self.s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "B"})
        _, types = self.s.call("GET", f"/api/campaigns/{cid}/relation-types")
        rival = next(t for t in types if t["key"] == "rival")
        rel = {"source_kind": "npc", "source_id": a["id"], "target_kind": "npc", "target_id": b["id"], "type": "rival"}
        self.assertEqual(self.s.call("POST", f"/api/campaigns/{cid}/relations", rel)[0], 201)
        code, body = self.s.call("DELETE", f"/api/relation-types/{rival['id']}", headers=EN)
        self.assertEqual((code, body["detail"]), (409, "1 relationship uses the type “Rival”. Change its type before deleting it."))
        code, body = self.s.call("DELETE", f"/api/relation-types/{rival['id']}")
        self.assertEqual(body["detail"], "1 lien utilise la nature « Rival ». Changez sa nature avant de la supprimer.")
        self.assertEqual(self.s.call("POST", f"/api/campaigns/{cid}/relations", rel)[0], 201)
        code, body = self.s.call("DELETE", f"/api/relation-types/{rival['id']}", headers=EN)
        self.assertEqual(body["detail"], "2 relationships use the type “Rival”. Change their type before deleting it.")
        # Message d'un validateur (schemas.py) : traduit dans le détail du champ.
        code, body = self.s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "F", "color": "rouge"}, headers=EN)
        self.assertEqual((code, body["detail"]), (422, "Invalid data"))
        self.assertEqual(body["errors"][0]["msg"], "Value error, Colour expected in #RRGGBB format")
        code, body = self.s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "F", "color": "rouge"})
        self.assertEqual(body["errors"][0]["msg"], "Value error, Couleur attendue au format #RRGGBB")

    def test_messages_follow_account_locale(self):
        self.assertEqual(self.s.call("POST", "/api/admin/users", {"username": "anglais", "password": "motdepasse1"})[0], 201)
        user = Client(self.s.base)
        self.assertEqual(user.login("anglais", "motdepasse1"), 200)
        self.assertEqual(user.call("PUT", "/api/auth/locale", {"locale": "en"})[0], 200)
        code, body = user.call("POST", "/api/auth/password", {"current_password": "mauvais1", "new_password": "nouveau12"},
                               headers={"Accept-Language": "fr"})
        self.assertEqual((code, body["detail"]), (400, "Current password is incorrect"))
        code, body = user.call("GET", "/api/documents/999999/versions", headers={"Accept-Language": "fr"})
        self.assertEqual((code, body["detail"]), (404, "Entry not found"))

    def test_version_labels_translated_on_read(self):
        cid = self.campaign()["id"]
        _, doc = self.s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Notes", "content": "<p>un</p>"})
        self.assertEqual(self.s.call("PATCH", f"/api/documents/{doc['id']}", {"content": "<p>deux</p>"})[0], 200)
        _, fr = self.s.call("GET", f"/api/documents/{doc['id']}/versions")
        _, en = self.s.call("GET", f"/api/documents/{doc['id']}/versions", headers=EN)
        self.assertEqual([v["label"] for v in fr["versions"]], ["Avant remplacement"])
        self.assertEqual([v["label"] for v in en["versions"]], ["Before replacement"])
        # Nom saisi par un MJ : jamais traduit, même s'il ressemble à un nom automatique.
        vid = en["versions"][0]["id"]
        self.assertEqual(self.s.call("PATCH", f"/api/documents/{doc['id']}/versions/{vid}", {"label": "État initial"})[0], 200)
        _, en = self.s.call("GET", f"/api/documents/{doc['id']}/versions", headers=EN)
        self.assertEqual(en["versions"][0]["label"], "État initial")
        # Titre par défaut : dans la langue de celui qui crée la fiche (données, enregistré tel quel).
        _, created = self.s.call("POST", f"/api/campaigns/{cid}/documents", {"title": ""}, headers=EN)
        self.assertEqual(created["title"], "New scenario")
        _, copy = self.s.call("POST", f"/api/documents/{doc['id']}/duplicate", headers=EN)
        self.assertEqual(copy.get("title"), "Notes (copy)")

    def test_live_refusal_follows_language(self):
        """Refus d'une connexion temps réel (message affiché par l'éditeur) : langue du navigateur, puis du compte."""
        from websockets.sync.client import connect
        from maze.collab import SCHEMA_VERSION
        cid = self.campaign()["id"]
        _, doc = self.s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Live"})
        url = f"{self.s.base.replace('http://', 'ws://')}/api/documents/{doc['id']}/live?client=1&v={SCHEMA_VERSION}"
        for headers, detail in ((EN, "Authentication required"), ({}, "Authentification requise")):
            with connect(url, additional_headers=headers, open_timeout=5) as ws:
                self.assertEqual(json.loads(ws.recv(timeout=5))["detail"], detail)
        # Langue de l'interface (paramètre lang, editor/live.js) : avant Accept-Language ; inconnue, ignorée.
        for lang, headers, detail in (("en", {"Accept-Language": "fr"}, "Authentication required"),
                                      ("fr", EN, "Authentification requise"), ("xx", EN, "Authentication required")):
            with connect(f"{url}&lang={lang}", additional_headers=headers, open_timeout=5) as ws:
                self.assertEqual(json.loads(ws.recv(timeout=5))["detail"], detail)
        self.assertEqual(self.s.call("POST", "/api/admin/users", {"username": "direct", "password": "motdepasse1"})[0], 201)
        user = Client(self.s.base)
        user.login("direct", "motdepasse1")
        user.call("PUT", "/api/auth/locale", {"locale": "en"})
        with connect(url.replace("client=1", "client=-5"), additional_headers={"Cookie": user.cookie_header()},
                     open_timeout=5) as ws:
            self.assertEqual(json.loads(ws.recv(timeout=5))["detail"], "Invalid client identifier")
        # La langue choisie dans le compte l'emporte sur celle que l'interface transmet.
        with connect(url.replace("client=1", "client=-5") + "&lang=fr",
                     additional_headers={"Cookie": user.cookie_header()}, open_timeout=5) as ws:
            self.assertEqual(json.loads(ws.recv(timeout=5))["detail"], "Invalid client identifier")
        # Langue du compte inconnue du serveur : celle de l'interface, puis le navigateur.
        user.call("PUT", "/api/auth/locale", {"locale": "de"})
        for query, headers, detail in (("&lang=en", {"Accept-Language": "fr"}, "Invalid client identifier"),
                                       ("", EN, "Invalid client identifier"),
                                       ("", {"Accept-Language": "fr"}, "Identifiant de client invalide")):
            with connect(url.replace("client=1", "client=-5") + query,
                         additional_headers={"Cookie": user.cookie_header(), **headers}, open_timeout=5) as ws:
                self.assertEqual(json.loads(ws.recv(timeout=5))["detail"], detail)

    def test_oauth_page_follows_accept_language(self):
        query = "/oauth/authorize?" + urllib.parse.urlencode({"client_id": "inconnu", "redirect_uri": "https://claude.ai/x"})
        for headers, lang, text in (({}, "fr", "Client inconnu"), (EN, "en", "Unknown client. Check the client ID")):
            req = urllib.request.Request(self.s.base + query, headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(req)
            page = caught.exception.read().decode()
            self.assertIn(f'<html lang="{lang}">', page)
            self.assertIn(text, page)
        self.assertIn("Authorisation failed", page)


# --- Données de départ -----------------------------------------------------------------------------------------

class SeedsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def seeds(self, cid: int, client=None) -> tuple[dict, dict, dict]:
        c = client or self.s.admin
        _, types = c.call("GET", f"/api/campaigns/{cid}/relation-types")
        _, places = c.call("GET", f"/api/campaigns/{cid}/places")
        return ({t["key"]: t["label"] for t in types}, {t["key"]: t["hint"] for t in types},
                {k["key"]: k["label"] for k in places["kinds"]})

    def test_french_by_default_english_for_english_request_or_account(self):
        _, fr = self.s.call("POST", "/api/campaigns", {"name": "FR"})
        labels, hints, kinds = self.seeds(fr["id"])
        self.assertEqual((labels["allie"], labels["autre"], kinds["batiment"], kinds["lieu"]),
                         ("Allié", "Autre", "Bâtiment", "Lieu"))
        _, en = self.s.call("POST", "/api/campaigns", {"name": "EN"}, headers=EN)
        en_labels, en_hints, en_kinds = self.seeds(en["id"])
        # Mêmes clés (stables) dans les deux langues ; seuls les libellés changent.
        self.assertEqual(set(en_labels), set(labels))
        self.assertEqual(set(en_kinds), set(kinds))
        self.assertEqual((en_labels["allie"], en_labels["ennemi"], en_labels["autre"]), ("Ally", "Enemy", "Other"))
        self.assertEqual(en_hints["mentor"], "the source is the target's mentor")
        self.assertEqual(en_hints["allie"], "")
        self.assertEqual((en_kinds["batiment"], en_kinds["lieu"], en_kinds["donjon"]),
                         ("Building", "Place", "Dungeon, underground"))
        # Compte en anglais : l'emporte sur l'en-tête du navigateur.
        self.assertEqual(self.s.call("POST", "/api/admin/users", {"username": "graine", "password": "motdepasse1"})[0], 201)
        user = Client(self.s.base)
        user.login("graine", "motdepasse1")
        user.call("PUT", "/api/auth/locale", {"locale": "en"})
        _, mine = user.call("POST", "/api/campaigns", {"name": "Mine"}, headers={"Accept-Language": "fr"})
        self.assertEqual(self.seeds(mine["id"], user)[0]["famille"], "Family")
        # Relation de type « autre » : clé inchangée, libellé anglais.
        _, a = self.s.call("POST", f"/api/campaigns/{en['id']}/npcs", {"name": "A"})
        _, b = self.s.call("POST", f"/api/campaigns/{en['id']}/npcs", {"name": "B"})
        code, rel = self.s.call("POST", f"/api/campaigns/{en['id']}/relations",
                                {"source_kind": "npc", "source_id": a["id"], "target_kind": "npc", "target_id": b["id"]})
        self.assertEqual((code, rel["type"]), (201, "autre"))
        # Les campagnes existantes ne sont jamais traduites.
        self.assertEqual(self.seeds(fr["id"])[0]["allie"], "Allié")

    def test_import_creates_in_importer_language(self):
        _, camp = self.s.call("POST", "/api/campaigns", {"name": "Exportée"})
        code, archive = self.s.call("GET", f"/api/campaigns/{camp['id']}/export")
        self.assertEqual(code, 200)
        code, imported = self.s.call("POST", "/api/campaigns/import", raw=archive, content_type="application/zip",
                                     headers=EN)
        self.assertEqual((code, imported["name"]), (201, "Exportée (imported)"))
        code, again = self.s.call("POST", "/api/campaigns/import", raw=archive, content_type="application/zip")
        self.assertEqual(again["name"], "Exportée (importée)")   # nom libre en français
        code, body = self.s.call("POST", "/api/campaigns/import", raw=b"pas un zip", content_type="application/zip",
                                 headers=EN)
        self.assertEqual((code, body["detail"]), (400, "Unreadable archive (a .zip file exported from Maze is expected)"))

    def test_default_locale_for_seeds(self):
        s = Server(env={"MAZE_DEFAULT_LOCALE": "en"})
        try:
            _, camp = s.call("POST", "/api/campaigns", {"name": "Instance anglaise"})
            _, types = s.call("GET", f"/api/campaigns/{camp['id']}/relation-types")
            self.assertEqual({t["key"]: t["label"] for t in types}["hierarchie"], "Hierarchy")
        finally:
            s.stop()


class DemoLanguageTest(unittest.TestCase):
    def test_demo_lang_option(self):
        from maze import demo
        with tempfile.TemporaryDirectory() as tmp:
            saved = os.environ.get("MAZE_DATA_DIR")
            os.environ["MAZE_DATA_DIR"] = tmp
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    fr_id = demo.main()
                    en_id = demo.main(["--lang", "en"])
                with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                    demo.main(["--lang", "klingon"])
            finally:
                if saved is None:
                    os.environ.pop("MAZE_DATA_DIR", None)
                else:
                    os.environ["MAZE_DATA_DIR"] = saved
            with contextlib.closing(sqlite3.connect(Path(tmp) / "maze.db")) as conn:
                name = lambda cid: conn.execute("SELECT name FROM campaigns WHERE id = ?", (cid,)).fetchone()[0]  # noqa: E731
                self.assertEqual(name(fr_id), "Les Brumes d'Ys (démo)")
                self.assertEqual(name(en_id), "The Mists of Ys (demo)")
                factions = {r[0] for r in conn.execute("SELECT name FROM factions WHERE campaign_id = ?", (en_id,))}
                self.assertIn("The Grey Hand", factions)
                kinds = dict(conn.execute("SELECT key, label FROM place_kinds WHERE campaign_id = ?", (en_id,)).fetchall())
                self.assertEqual(kinds["ville"], "City")
                # Types des lieux : clés inchangées, toujours connues de la campagne.
                orphans = conn.execute("""SELECT COUNT(*) FROM places p WHERE p.campaign_id = ? AND NOT EXISTS (
                                            SELECT 1 FROM place_kinds k WHERE k.campaign_id = p.campaign_id AND k.key = p.kind)""",
                                       (en_id,)).fetchone()[0]
                self.assertEqual(orphans, 0)
                secret = conn.execute("SELECT secrets FROM npcs WHERE campaign_id = ? AND name = 'Captain Hart'",
                                      (en_id,)).fetchone()[0]
                self.assertIn("[[Maëlis Corvin]]", secret)   # lien [[Nom]] : nom du personnage inchangé


# --- Export de Foundry ---------------------------------------------------------------------------------------------

class FoundryExportLanguageTest(unittest.TestCase):
    def test_export_in_both_languages(self):
        s = Server()
        try:
            _, camp = s.call("POST", "/api/campaigns", {"name": "Foundry"})
            cid = camp["id"]
            _, fac = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "La Vigie"})
            _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Bob", "title": "Guetteur",
                                                                   "secrets": "<p>Traître</p>"})
            s.call("POST", "/api/memberships", {"npc_id": npc["id"], "faction_id": fac["id"], "is_leader": True})
            _, created = s.call("POST", "/api/admin/foundry/tokens", {"name": "t", "campaigns": [cid]})

            def export(query: str = "") -> dict:
                req = urllib.request.Request(f"{s.base}/api/foundry/v1/campaigns/{cid}/export{query}",
                                             headers={"Authorization": f"Bearer {created['token']}", **EN})
                with urllib.request.urlopen(req) as resp:
                    return json.loads(resp.read())

            default, fr, en, other = export(), export("?lang=fr"), export("?lang=en"), export("?lang=de")
            bob = {name: next(n for n in data["npcs"] if n["id"] == npc["id"])
                   for name, data in (("default", default), ("fr", fr), ("en", en), ("other", other))}
            # Sans paramètre ni langue connue : MAZE_DEFAULT_LOCALE (fr), même si le navigateur est en anglais.
            self.assertEqual(bob["default"]["html"], bob["fr"]["html"])
            self.assertEqual(bob["other"]["hash"], bob["fr"]["hash"])
            self.assertIn("<strong>Titre :</strong> Guetteur", bob["fr"]["html"])
            self.assertIn("<strong>Statut :</strong> Vivant", bob["fr"]["html"])
            self.assertIn("- chef", bob["fr"]["html"])
            self.assertIn("<h2>Secrets</h2>", bob["fr"]["secret_html"])
            self.assertIn("<strong>Title:</strong> Guetteur", bob["en"]["html"])
            self.assertIn("<strong>Status:</strong> Alive", bob["en"]["html"])
            self.assertIn("<strong>Factions:</strong>", bob["en"]["html"])
            self.assertIn("La Vigie</a> - leader", bob["en"]["html"])
            self.assertIn("Traître", bob["en"]["secret_html"])          # texte du MJ : jamais traduit
            # Autre langue = autre HTML, donc autre empreinte : le module réécrit les pages non modifiées.
            self.assertNotEqual(bob["en"]["hash"], bob["fr"]["hash"])
            self.assertEqual(en["campaign"]["name"], "Foundry")
            # Langue des pages et noms à créer dans le monde (dossiers, pages) : ceux de la langue demandée, pas
            # celle du poste du MJ (que Foundry seul connaît) ; langue inconnue du serveur : MAZE_DEFAULT_LOCALE.
            self.assertEqual([d["locale"] for d in (default, fr, en, other)], ["fr", "fr", "en", "fr"])
            self.assertEqual(fr["labels"], foundry.WORLD_LABELS)
            self.assertEqual(other["labels"], foundry.WORLD_LABELS)
            self.assertEqual({k: en["labels"][k] for k in ("Page.Sheet", "Page.Secrets", "Kind.npc", "Folder.Root",
                                                          "Page.ActorLink")},
                             {"Page.Sheet": "Sheet", "Page.Secrets": "Secrets (GM)", "Kind.npc": "NPCs",
                              "Folder.Root": "{campaign} - {label}", "Page.ActorLink": "{name}'s sheet (Maze)"})
            self.assertEqual({k["key"]: k["label"] for k in en["place_kinds"]}["ville"], "Ville")   # données de la campagne
        finally:
            s.stop()


if __name__ == "__main__":
    unittest.main()
