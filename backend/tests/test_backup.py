"""Sauvegardes et restauration : aller-retour réaliste (grande carte en JPEG), extensions des médias
(mediafiles.py), en ligne de commande comme depuis l'administration.

Lancer : cd backend && python -m unittest -q tests.test_backup
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

from PIL import Image

from tests.test_api import (
    Server, multipart, png_bytes, rewind_schema, undo_entry_revision, undo_nested_factions, undo_user_locale,
)
from tests.test_maps import form

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND_SRC = BACKEND.parent / "frontend" / "src"


def rgb_jpeg(size: tuple[int, int]) -> bytes:
    """Fond de carte réaliste : RGB (pas de transparence), plus grand que la version d'affichage (4096 px)."""
    img = Image.new("RGB", size, (30, 40, 60))
    for x in range(0, size[0], 500):
        img.paste((200, 180, 90), (x, 0, x + 40, size[1]))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return buf.getvalue()


class LargeMapRoundTripTest(unittest.TestCase):
    """Audit H1 (OPS-1, DATA-1, QUAL-1) : une sauvegarde qui contient le fond JPEG d'une grande carte doit se
    restaurer, depuis l'administration et en ligne de commande (python -m maze.backup --restore)."""

    def test_backup_and_restore_with_large_map(self):
        s = Server()
        try:
            media_dir = Path(s.tmp.name) / "media"
            _, camp = s.call("POST", "/api/campaigns", {"name": "Morvane"})
            cid = camp["id"]
            body, ctype = form({"name": "Secteur Morvane"}, ("morvane.jpg", rgb_jpeg((5000, 3000))))
            code, big = s.call("POST", f"/api/campaigns/{cid}/maps", raw=body, content_type=ctype)
            self.assertEqual(code, 201, big)
            self.assertTrue(big["url"].endswith(".jpg"), big["url"])          # pleine définition en JPEG
            self.assertTrue(big["preview_url"].endswith("_p.webp"))            # version d'affichage
            _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Halloran"})
            body, ctype = multipart([("portrait.png", png_bytes())])
            _, gallery = s.call("POST", f"/api/npcs/{npc['id']}/images", raw=body, content_type=ctype)
            image_url = (gallery if isinstance(gallery, list) else gallery["images"])[0]["url"]
            _, kind = s.call("POST", f"/api/campaigns/{cid}/place-kinds", {"label": "Cité-forteresse", "icon": "planet"})
            body, ctype = form({}, ("forteresse.png", png_bytes(size=(300, 300))))
            code, icon = s.call("POST", f"/api/place-kinds/{kind['id']}/image", raw=body, content_type=ctype)
            self.assertEqual(code, 200, icon)
            urls = [big["url"], big["preview_url"], image_url, icon["image_url"]]
            originals = {url: s.call("GET", url)[1] for url in urls}
            self.assertEqual(originals[big["url"]][:2], b"\xff\xd8")

            # Tout ce que Maze a produit sous media/ a une extension de la liste unique.
            from maze import mediafiles
            produced = [p for p in media_dir.rglob("*") if p.is_file()]
            self.assertTrue(produced)
            self.assertEqual({p.suffix for p in produced} - set(mediafiles.MEDIA_TYPES), set())
            self.assertTrue(all(mediafiles.FILE_NAME_RE.match(p.name) for p in produced))

            code, entry = s.call("POST", "/api/admin/backups")
            self.assertEqual(code, 201, entry)
            archive = Path(s.tmp.name) / "backups" / entry["name"]
            from maze import backup
            self.assertEqual(backup.inspect_archive(archive)["campaigns"], 1)

            # Après la sauvegarde : campagne supprimée, fichiers effacés.
            self.assertEqual(s.call("DELETE", f"/api/campaigns/{cid}")[0], 204)
            self.assertEqual(s.call("GET", big["url"])[0], 404)

            # Import de la même archive (Administration > Sauvegardes > Importer) : acceptée.
            _, raw = s.call("GET", f"/api/admin/backups/{entry['name']}")
            code, imported = s.call("POST", "/api/admin/backups/import", raw=raw, content_type="application/gzip")
            self.assertEqual(code, 201, imported)

            code, result = s.call("POST", f"/api/admin/backups/{entry['name']}/restore")
            self.assertEqual(code, 200, result)
            for url, content in originals.items():
                self.assertEqual(s.call("GET", url), (200, content), url)
            _, listing = s.call("GET", f"/api/campaigns/{cid}/maps")
            self.assertEqual([m["url"] for m in listing["maps"]], [big["url"]])

            # Ligne de commande, sur l'archive importée (copie de la même sauvegarde).
            s.call("DELETE", f"/api/campaigns/{cid}")
            self.assertFalse((media_dir / big["url"].removeprefix("/media/")).exists())
            copy = Path(s.tmp.name) / "backups" / imported["name"]
            out = subprocess.run([sys.executable, "-m", "maze.backup", "--restore", str(copy)], cwd=BACKEND,
                                 capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            for url, content in originals.items():
                self.assertEqual((media_dir / url.removeprefix("/media/")).read_bytes(), content, url)
        finally:
            s.stop()

    def test_archive_members_filtered_by_shared_list(self):
        """Seuls les fichiers que Maze produit (mediafiles) sont admis sous media/ ; le reste est refusé."""
        import tarfile
        import tempfile
        from maze import backup
        with tempfile.TemporaryDirectory() as tmp:
            for name, ok in (("media/1/0123abcd.jpg", True), ("media/1/0123abcd_p.webp", True),
                             ("media/1/0123abcd_i.webp", True), ("media/1/page.html", False),
                             ("media/1/x.png", False), ("media/1", False), ("media/x/a.webp", False),
                             ("media/1/a/b.webp", False)):
                path = Path(tmp) / "a.tar.gz"
                with tarfile.open(path, "w:gz") as tar:
                    for member, data in (("maze.db", b"x"), (name, b"y")):
                        info = tarfile.TarInfo(member)
                        info.size = len(data)
                        tar.addfile(info, io.BytesIO(data))
                with tarfile.open(path, "r:gz") as tar:
                    if ok:
                        backup._check_members(tar)
                    else:
                        with self.assertRaises(backup.ArchiveError, msg=name):
                            backup._check_members(tar)

    def test_illustrations_stay_webp_for_frontend_filters(self):
        """Les filtres de l'interface (sanitize.js, schema.js, markdown.js) n'admettent dans le texte des fiches
        que /media/<campagne>/<nom>.webp : les illustrations, même grandes, doivent rester en WebP."""
        s = Server()
        try:
            _, camp = s.call("POST", "/api/campaigns", {"name": "Illustrations"})
            _, doc = s.call("POST", f"/api/campaigns/{camp['id']}/documents", {"title": "Scène"})
            body, ctype = multipart([("grande.jpg", rgb_jpeg((5000, 3000)))])
            body = body.replace(b'name="files"', b'name="file"')
            code, img = s.call("POST", f"/api/documents/{doc['id']}/images", raw=body, content_type=ctype)
            self.assertEqual(code, 201, img)
            self.assertRegex(img["url"], r"^/media/\d+/[\w-]+\.webp$")
        finally:
            s.stop()
        for rel in ("editor/sanitize.js", "editor/schema.js", "markdown.js"):
            source = (FRONTEND_SRC / rel).read_text(encoding="utf-8")
            self.assertTrue(re.search(r"/\^\\/media\\/\\d\+\\/\[\\w-\]\+\\\.webp\$/", source), rel)



class AtomicImageTest(unittest.TestCase):
    """Contre-revue du lot 1 : une image n'apparaît sous son nom définitif qu'une fois complète (temporaire puis
    renommage) ; le temporaire d'une image en cours d'écriture n'entre pas dans une sauvegarde, qui resterait sinon
    impossible à restaurer (élément inattendu sous media/)."""

    def test_written_then_renamed(self):
        import tempfile
        from unittest import mock
        from maze import imaging, mediafiles
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "media"
            media.mkdir()
            seen: list[str] = []
            real_save = Image.Image.save
            raw = png_bytes()

            def save(img, target, *args, **kwargs):
                seen.append(Path(target).name)
                real_save(img, target, *args, **kwargs)
            with mock.patch.object(Image.Image, "save", save):
                stored = imaging.process_upload(raw, media, 4)
            self.assertTrue(seen and all(mediafiles.is_temp(name) for name in seen), seen)
            self.assertEqual(sorted(p.name for p in (media / "4").iterdir()),
                             sorted(Path(r).name for r in (stored.file, stored.thumb)))
            # Échec en cours d'écriture (disque plein, arrêt) : ni fichier tronqué sous le nom définitif, ni reste.
            def broken(img, target, *args, **kwargs):
                Path(target).write_bytes(b"RIFF incomplet")
                raise OSError("No space left on device")
            with mock.patch.object(Image.Image, "save", broken), self.assertRaises(imaging.ImageError):
                imaging.process_upload(raw, media, 5)
            self.assertEqual(list((media / "5").iterdir()), [])
            copy = imaging.copy_stored(media, stored.file, stored.thumb, 6, stored.width, stored.height)
            self.assertEqual(sorted(p.name for p in (media / "6").iterdir()),
                             sorted(Path(r).name for r in (copy.file, copy.thumb)))

    def test_backup_skips_image_being_written(self):
        from maze import backup, mediafiles
        s = Server()
        try:
            _, camp = s.call("POST", "/api/campaigns", {"name": "Envoi en cours"})
            _, npc = s.call("POST", f"/api/campaigns/{camp['id']}/npcs", {"name": "N"})
            body, ctype = multipart([("p.png", png_bytes())])
            self.assertEqual(s.call("POST", f"/api/npcs/{npc['id']}/images", raw=body, content_type=ctype)[0], 201)
            pending = Path(s.tmp.name) / "media" / str(camp["id"]) / mediafiles.temp_name("0123abcd.webp")
            pending.write_bytes(b"RIFF en cours")
            code, entry = s.call("POST", "/api/admin/backups")
            self.assertEqual(code, 201, entry)
            archive = Path(s.tmp.name) / "backups" / entry["name"]
            self.assertEqual(backup.inspect_archive(archive)["campaigns"], 1)   # restaurable
            import tarfile
            with tarfile.open(archive) as tar:
                self.assertFalse([n for n in tar.getnames() if n.endswith(mediafiles.TEMP_SUFFIX)])
        finally:
            s.stop()


class PartialBackupTest(unittest.TestCase):
    """Audit M9 (DATA-6) : une sauvegarde qui échoue ne doit laisser aucune archive listée ni restaurable ;
    une image supprimée pendant le parcours de media/ ne fait pas échouer la sauvegarde ; les fichiers .part
    abandonnés (arrêt brutal) sont retirés à la sauvegarde suivante, jamais celui d'une sauvegarde en cours."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server()
        from maze.config import load_settings
        cls.cfg = load_settings()
        cls.backups = Path(cls.s.tmp.name) / "backups"
        _, camp = cls.s.call("POST", "/api/campaigns", {"name": "Images"})
        _, npc = cls.s.call("POST", f"/api/campaigns/{camp['id']}/npcs", {"name": "N"})
        body, ctype = multipart([(f"{i}.png", png_bytes((i * 40, 10, 10))) for i in range(4)])
        cls.s.call("POST", f"/api/npcs/{npc['id']}/images", raw=body, content_type=ctype)
        cls.media = Path(cls.s.tmp.name) / "media" / str(camp["id"])

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def tearDown(self):
        for path in self.backups.glob("*"):
            path.unlink()

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.backups.iterdir())

    def test_failed_backup_leaves_nothing(self):
        import tarfile
        from unittest import mock
        from maze import backup
        original = tarfile.TarFile.addfile
        calls = []

        def addfile(tar, tarinfo, fileobj=None, **kwargs):
            calls.append(tarinfo.name)
            if len(calls) == 4:
                raise OSError(28, "No space left on device")    # disque plein en cours d'écriture
            return original(tar, tarinfo, fileobj, **kwargs)
        with mock.patch.object(tarfile.TarFile, "addfile", addfile):
            with self.assertRaises(OSError):
                backup.backup(14, self.cfg)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.s.call("GET", "/api/admin/backups")[1], [])
        # La suivante réussit normalement.
        archive = backup.backup(14, self.cfg)
        self.assertEqual(self.leftovers(), [archive.name])
        backup.inspect_archive(archive)

    def test_vanished_image_skipped(self):
        import tarfile
        from unittest import mock
        from maze import backup
        files = sorted(p for p in self.media.iterdir())
        victim = files[3]
        kept = victim.read_bytes()
        original = tarfile.TarFile.gettarinfo

        def gettarinfo(tar, name=None, arcname=None, fileobj=None):
            if name and Path(name) == victim and victim.exists():
                victim.unlink()   # un MJ supprime une image pendant que la sauvegarde parcourt media/
            return original(tar, name, arcname, fileobj)
        try:
            with mock.patch.object(tarfile.TarFile, "gettarinfo", gettarinfo):
                archive = backup.backup(14, self.cfg)
        finally:
            victim.write_bytes(kept)
        self.assertEqual(self.leftovers(), [archive.name])
        with tarfile.open(archive, "r:gz") as tar:
            names = set(tar.getnames())
        expected = {f"media/{self.media.name}/{p.name}" for p in files if p != victim}
        self.assertLessEqual(expected, names)
        self.assertNotIn(f"media/{self.media.name}/{victim.name}", names)

    def test_same_second_same_nature(self):
        """Deux sauvegardes de même nature dans la même seconde (deux administrateurs, double clic) : la seconde
        prend le nom suivant au lieu d'échouer sur le .part de la première (contre-revue)."""
        from unittest import mock
        from maze import backup
        original = backup.new_archive_path
        taken = []

        def racing(target_dir, suffix=""):
            path = original(target_dir, suffix)
            if not taken:   # l'autre sauvegarde réserve ce nom entre le choix du nom et la création du .part
                taken.append(path.with_suffix(".part"))
                taken[0].write_bytes(b"autre sauvegarde en cours")
            return path
        with mock.patch.object(backup, "new_archive_path", racing):
            archive = backup.backup(0, self.cfg, "manuelle")
        self.assertTrue(archive.name.endswith("-manuelle-2.tar.gz"), archive.name)
        self.assertEqual(taken[0].read_bytes(), b"autre sauvegarde en cours")
        backup.inspect_archive(archive)

    def test_abandoned_parts_removed(self):
        import fcntl
        import os
        import time
        from maze import backup
        self.backups.mkdir(exist_ok=True)
        old = time.time() - 3600
        abandoned = self.backups / "maze-20260101-031500.tar.part"
        abandoned.write_bytes(b"tronque")
        running = self.backups / "maze-20260101-031501.tar.part"   # sauvegarde en cours dans un autre processus
        running.write_bytes(b"en cours")
        recent = self.backups / "maze-20260101-031502.tar.part"    # tout juste créée, pas encore verrouillée
        recent.write_bytes(b"")
        importing = self.backups / ".import-12-34.part"            # import en cours depuis le navigateur
        importing.write_bytes(b"import")
        for path in (abandoned, running, importing):
            os.utime(path, (old, old))
        with running.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            archive = backup.backup(14, self.cfg)
        self.assertEqual(self.leftovers(), sorted([importing.name, running.name, recent.name, archive.name]))



class RotationTest(unittest.TestCase):
    """Audit M8 (DATA-5) : chaque nature d'archive tourne séparément ; les archives « -avant-restauration » et
    « -import » ne sont supprimées qu'au-delà de leur propre nombre ; tri sur la date et le numéro analysés
    (réserve de X1 : « -2 » se triait avant l'archive sans suffixe de la même seconde)."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server()
        from maze.config import load_settings
        cls.cfg = load_settings()
        cls.backups = Path(cls.s.tmp.name) / "backups"

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def setUp(self):
        self.backups.mkdir(exist_ok=True)
        for path in self.backups.glob("*"):
            path.unlink()

    def fake(self, *names: str) -> None:
        for name in names:
            (self.backups / name).write_bytes(b"x")

    def names(self) -> set[str]:
        return {p.name for p in self.backups.glob("maze-*.tar.gz")}

    def test_each_nature_rotates_alone(self):
        from maze import backup
        daily = [f"maze-202601{d:02d}-031500.tar.gz" for d in range(1, 6)]
        specials = ([f"maze-202602{d:02d}-120000-avant-restauration.tar.gz" for d in range(1, 4)]
                    + [f"maze-202602{d:02d}-130000-import.tar.gz" for d in range(1, 3)]
                    + [f"maze-202603{d:02d}-090000-maj.tar.gz" for d in range(1, 8)]
                    + [f"maze-202603{d:02d}-100000-manuelle.tar.gz" for d in range(1, 8)])
        self.fake(*daily, *specials, "maze-autre.tar.gz")
        new = backup.backup(3, self.cfg)                       # quotidienne (cron) : 3 gardées
        self.assertEqual(self.names(), {new.name, *daily[-2:], *specials, "maze-autre.tar.gz"})
        maj = backup.backup(5, self.cfg, suffix="maj")       # avant mise à jour : 5 gardées
        self.assertEqual(len([n for n in self.names() if n.endswith("-maj.tar.gz")]), 5)
        self.assertIn(maj.name, self.names())
        # Bouton « Sauvegarder maintenant » : nature « manuelle », 5 gardées, rien d'autre ne bouge.
        before = self.names()
        code, entry = self.s.call("POST", "/api/admin/backups")
        self.assertEqual(code, 201)
        self.assertTrue(entry["name"].endswith("-manuelle.tar.gz"), entry["name"])
        manual = sorted(n for n in self.names() if n.endswith("-manuelle.tar.gz"))
        self.assertEqual(len(manual), backup.keep_for("manuelle"))
        self.assertEqual(self.names() - set(manual), {n for n in before if not n.endswith("-manuelle.tar.gz")})
        self.assertEqual(len([n for n in self.names() if "-avant-restauration" in n]), 3)
        self.assertEqual(len([n for n in self.names() if n.endswith("-import.tar.gz")]), 2)

    def test_same_second_sorted_on_counter(self):
        from maze import backup
        same = ["maze-20261014-010203.tar.gz", "maze-20261014-010203-2.tar.gz", "maze-20261014-010203-3.tar.gz",
                "maze-20261014-010203-import-2.tar.gz", "maze-20261014-010203-import.tar.gz"]
        self.fake(*same)
        backup.rotate(self.backups, "", 2)
        self.assertEqual(self.names(), set(same[1:]))           # la plus ancienne (sans numéro) part
        backup.rotate(self.backups, "import", 1)
        self.assertEqual(self.names(), set(same[1:4]))
        self.assertEqual(backup.archive_nature("maze-20261014-010203-avant-restauration-2.tar.gz"),
                         "avant-restauration")
        # Liste de l'administration : la plus récente d'abord, numéro compris.
        self.fake("maze-20261014-010204.tar.gz")
        listed = [b["name"] for b in self.s.call("GET", "/api/admin/backups")[1]]
        self.assertEqual(listed[0], "maze-20261014-010204.tar.gz")
        self.assertLess(listed.index("maze-20261014-010203-3.tar.gz"), listed.index("maze-20261014-010203-2.tar.gz"))

    def test_safety_and_imports_kept_apart(self):
        import shutil
        from maze import backup
        real = backup.backup(0, self.cfg)
        # L'archive restaurée est la plus ancienne « avant-restauration » : jamais supprimée par la rotation.
        restored = self.backups / "maze-20200101-000000-avant-restauration.tar.gz"
        shutil.copyfile(real, restored)
        real.unlink()
        older = [f"maze-202{y}0101-000000-avant-restauration.tar.gz" for y in range(1, 6)]
        self.fake(*older)
        code, result = self.s.call("POST", f"/api/admin/backups/{restored.name}/restore")
        self.assertEqual(code, 200, result)
        self.s.admin.login("admin", "admin-test-pass")
        safety = {n for n in self.names() if "-avant-restauration" in n}
        self.assertEqual(backup.keep_for("avant-restauration"), 3)
        self.assertEqual(safety, {restored.name, result["safety"]["name"], *older[-2:]})
        # Quotidiennes : n'effacent jamais les archives de sécurité ni les importées.
        for _ in range(3):
            backup.backup(1, self.cfg)
        self.assertEqual({n for n in self.names() if "-avant-restauration" in n}, safety)
        # Imports : 5 gardés.
        _, raw = self.s.call("GET", f"/api/admin/backups/{restored.name}")
        self.fake(*[f"maze-2025010{d}-000000-import.tar.gz" for d in range(1, 6)])
        code, imported = self.s.call("POST", "/api/admin/backups/import", raw=raw, content_type="application/gzip")
        self.assertEqual(code, 201, imported)
        imports = sorted(n for n in self.names() if n.endswith("-import.tar.gz"))
        self.assertEqual(len(imports), backup.keep_for("import"))
        self.assertIn(imported["name"], imports)
        self.assertNotIn("maze-20250101-000000-import.tar.gz", imports)

    def test_command_line_nature(self):
        out = subprocess.run([sys.executable, "-m", "maze.backup", "--suffix", "maj", "--keep", "2"], cwd=BACKEND,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.strip().endswith("-maj.tar.gz"), out.stdout)
        bad = subprocess.run([sys.executable, "-m", "maze.backup", "--suffix", "../x"], cwd=BACKEND,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(bad.returncode, 2)



class QuotaAndSpaceTest(unittest.TestCase):
    """Contre-revue : quotas de conservation réglables (MAZE_BACKUP_KEEP), refus clair quand le volume n'a plus la
    place d'une sauvegarde ou d'une restauration, taille des archives et espace libre dans l'administration."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server()
        from maze.config import load_settings
        cls.cfg = load_settings()
        cls.backups = Path(cls.s.tmp.name) / "backups"

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def setUp(self):
        self.backups.mkdir(exist_ok=True)
        for path in self.backups.glob("*"):
            path.unlink()

    def test_keep_from_environment(self):
        import os
        from unittest import mock
        from maze import backup
        with mock.patch.dict(os.environ, {"MAZE_BACKUP_KEEP": ""}):
            self.assertEqual(backup.keep_counts(), {"quotidienne": 14, "manuelle": 3, "maj": 3,
                                                    "avant-restauration": 3, "import": 2})
        env = {"MAZE_BACKUP_KEEP": "manuelle=2, import=1,maj=x,essai=4,quotidienne=0,=3"}
        with mock.patch.dict(os.environ, env), self.assertLogs("maze", "WARNING") as logs:
            counts = backup.keep_counts()
        self.assertEqual(counts, {"quotidienne": 0, "manuelle": 2, "maj": 3, "avant-restauration": 3, "import": 1,
                                  "essai": 4})
        self.assertEqual(len(logs.output), 2, logs.output)      # « maj=x » et « =3 » ignorés, signalés
        with mock.patch.dict(os.environ, env):
            for _ in range(4):
                self.assertEqual(self.s.call("POST", "/api/admin/backups")[0], 201)
        self.assertEqual(len(list(self.backups.glob("maze-*-manuelle*.tar.gz"))), 2)
        # Ligne de commande sans --keep : quota de la variable pour la nature demandée.
        for _ in range(2):
            out = subprocess.run([sys.executable, "-m", "maze.backup", "--suffix", "maj"], cwd=BACKEND,
                                 env={**os.environ, "MAZE_BACKUP_KEEP": "maj=1"}, capture_output=True, text=True,
                                 timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(list(self.backups.glob("maze-*-maj*.tar.gz"))), 1)

    def test_not_enough_space(self):
        from unittest import mock
        from maze import backup
        _, entry = self.s.call("POST", "/api/admin/backups")
        before = sorted(p.name for p in self.backups.iterdir())
        with mock.patch.object(backup, "free_space", return_value=10 * 1024 * 1024):
            code, err = self.s.call("POST", "/api/admin/backups")
            self.assertEqual(code, 507, err)
            self.assertIn("Espace disque insuffisant", err["detail"])
            code, err = self.s.call("POST", f"/api/admin/backups/{entry['name']}/restore")
            self.assertEqual(code, 507, err)
            self.assertIn("Espace disque insuffisant", err["detail"])
            with self.assertRaises(backup.SpaceError):
                backup.backup(14, self.cfg)
        self.assertEqual(sorted(p.name for p in self.backups.iterdir()), before)   # ni archive, ni .part
        self.assertTrue(self.s.call("GET", "/api/auth/status")[1]["authenticated"])  # rien n'a été restauré

    def test_usage(self):
        from maze import backup
        self.assertEqual(self.s.call("POST", "/api/admin/backups")[0], 201)
        self.assertEqual(self.s.call("POST", "/api/admin/backups")[0], 201)
        code, usage = self.s.call("GET", "/api/admin/backups/usage")
        self.assertEqual(code, 200, usage)
        archives = sorted(self.backups.glob("maze-*.tar.gz"), key=backup.sort_key)
        self.assertEqual((usage["count"], usage["total"]), (2, sum(p.stat().st_size for p in archives)))
        self.assertEqual(usage["latest"], archives[-1].stat().st_size)
        self.assertEqual(usage["needed"], 2 * usage["latest"] + backup.SPACE_MARGIN)
        self.assertGreater(usage["free"], 0)
        self.assertEqual(usage["keep"], backup.keep_counts())
        self.assertEqual(self.s.client().call("GET", "/api/admin/backups/usage")[0], 401)

    def test_last_daily_backup(self):
        """Re-revue : date de la dernière sauvegarde quotidienne (sans suffixe) et alerte au-delà de 48 h, pour
        qu'un arrêt silencieux du cron se voie."""
        import os
        import time
        from maze import backup
        self.assertEqual(self.s.call("POST", "/api/admin/backups")[0], 201)      # manuelle : ne compte pas
        usage = self.s.call("GET", "/api/admin/backups/usage")[1]
        self.assertEqual((usage["last_daily"], usage["daily_stale"]), (None, True))
        old = self.backups / "maze-20260101-031500.tar.gz"
        old.write_bytes(b"x")
        three_days = time.time() - 3 * 24 * 3600
        os.utime(old, (three_days, three_days))
        usage = self.s.call("GET", "/api/admin/backups/usage")[1]
        self.assertTrue(usage["daily_stale"])
        self.assertEqual(usage["last_daily"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(three_days)))
        backup.backup(14, self.cfg)
        usage = self.s.call("GET", "/api/admin/backups/usage")[1]
        self.assertFalse(usage["daily_stale"])


class RestoreRollbackTest(unittest.TestCase):
    """Audit DATA-11 : une restauration qui échoue au remplacement des images ne doit pas laisser la base
    restaurée sans images : base et images sont remises en l'état, avec un message clair."""

    def test_media_swap_failure_rolls_back(self):
        import pathlib
        from unittest import mock
        from maze import backup
        from maze.config import load_settings
        s = Server()
        try:
            cfg = load_settings()
            _, camp = s.call("POST", "/api/campaigns", {"name": "Sauvegardée"})
            _, npc = s.call("POST", f"/api/campaigns/{camp['id']}/npcs", {"name": "N"})
            body, ctype = multipart([("p.png", png_bytes())])
            _, gallery = s.call("POST", f"/api/npcs/{npc['id']}/images", raw=body, content_type=ctype)
            image_url = (gallery if isinstance(gallery, list) else gallery["images"])[0]["url"]
            archive = backup.backup(0, cfg)
            s.call("POST", "/api/campaigns", {"name": "Après"})
            stray = cfg.media_dir / str(camp["id"]) / "0123456789abcdef.webp"
            original = pathlib.Path.rename

            def rename(path, target):
                if pathlib.Path(target) == cfg.media_dir and path.name == "media":
                    # Envoi d'image concurrent : media/<campagne> recréé entre les deux renommages.
                    stray.parent.mkdir(parents=True)
                    stray.write_bytes(b"envoi concurrent")
                    raise OSError(39, "Directory not empty")
                return original(path, target)
            with mock.patch.object(pathlib.Path, "rename", rename):
                with self.assertRaises(backup.ArchiveError) as caught:
                    backup.restore(archive, cfg)
            self.assertIn("remises en l'état", str(caught.exception))
            names = sorted(c["name"] for c in s.call("GET", "/api/campaigns")[1])
            self.assertEqual(names, ["Après", "Sauvegardée"])               # base d'avant la restauration
            self.assertEqual(s.call("GET", image_url)[0], 200)               # images d'avant
            self.assertEqual(stray.read_bytes(), b"envoi concurrent")        # l'envoi concurrent est gardé
            self.assertEqual([p.name for p in cfg.data_dir.iterdir() if p.name.startswith((".media", ".rest"))], [])
            # Sans incident, la même archive se restaure.
            backup.restore(archive, cfg)
            s.admin.login("admin", "admin-test-pass")
            self.assertEqual([c["name"] for c in s.call("GET", "/api/campaigns")[1]], ["Sauvegardée"])
        finally:
            s.stop()


    def test_media_put_back_failure_gives_paths(self):
        """Contre-revue : si la remise en place de l'ancien media/ échoue aussi, le message le dit et donne les
        chemins exacts ; la base est remise en l'état."""
        import pathlib
        from unittest import mock
        from maze import backup
        from maze.config import load_settings
        s = Server()
        try:
            cfg = load_settings()
            s.call("POST", "/api/campaigns", {"name": "Sauvegardée"})
            archive = backup.backup(0, cfg)
            s.call("POST", "/api/campaigns", {"name": "Après"})
            (cfg.media_dir / "1").mkdir(parents=True, exist_ok=True)
            original = pathlib.Path.rename

            def rename(path, target):
                if pathlib.Path(target) == cfg.media_dir:   # ni la nouvelle, ni l'ancienne ne peuvent revenir
                    raise OSError(5, "Input/output error")
                return original(path, target)
            with mock.patch.object(pathlib.Path, "rename", rename):
                with self.assertRaises(backup.ArchiveError) as caught:
                    backup.restore(archive, cfg)
            message = str(caught.exception)
            stuck = [p for p in cfg.data_dir.iterdir() if p.name.startswith(".media-remplace-")]
            self.assertEqual(len(stuck), 1)
            self.assertIn(str(stuck[0]), message)
            self.assertIn(str(cfg.media_dir), message)
            self.assertNotIn("images remises en l'état", message)
            self.assertIn("base remise en l'état", message)
            self.assertFalse(cfg.media_dir.exists())
            names = sorted(c["name"] for c in s.call("GET", "/api/campaigns")[1])
            self.assertEqual(names, ["Après", "Sauvegardée"])
            stuck[0].rename(cfg.media_dir)                 # la consigne du message
        finally:
            s.stop()


class AccountsRestoreTest(unittest.TestCase):
    """Audit M11 (X1-M2) : une restauration ne fait revivre ni mot de passe, ni compte désactivé, ni rôle retiré,
    ni session ; l'administrateur qui restaure garde son accès, même s'il n'existe pas dans l'archive."""

    def setUp(self):
        self.s = Server()
        self.addCleanup(self.s.stop)

    def user(self, username: str, role: str = "mj", password: str | None = None) -> dict:
        code, out = self.s.call("POST", "/api/admin/users",
                                {"username": username, "role": role, "password": password or f"mdp-{username}-1"})
        self.assertEqual(code, 201, out)
        return out

    def logged(self, username: str, password: str):
        client = self.s.client()
        self.assertEqual(client.login(username, password), 200)
        return client

    def authenticated(self, client) -> bool:
        return client.call("GET", "/api/auth/status")[1]["authenticated"]

    def test_accounts_keep_current_state(self):
        s = self.s
        mja, mjb, admin2 = self.user("mja"), self.user("mjb"), self.user("admin2", "admin")
        thief = self.logged("mja", "mdp-mja-1")                    # cookie volé avant la sauvegarde
        _, entry = s.call("POST", "/api/admin/backups")
        # Après la sauvegarde : réponse à l'incident.
        s.call("PATCH", f"/api/admin/users/{mja['id']}", {"password": "mdp-mja-2", "active": False})
        s.call("PATCH", f"/api/admin/users/{admin2['id']}", {"role": "mj"})
        s.call("PATCH", f"/api/admin/users/{mjb['id']}", {"password": "mdp-mjb-2"})
        self.assertFalse(self.authenticated(thief))
        mjb_now = self.logged("mjb", "mdp-mjb-2")
        other_admin_tab = self.logged("admin", "admin-test-pass")
        self.user("mjc")                                            # créé après la sauvegarde

        code, result = s.call("POST", f"/api/admin/backups/{entry['name']}/restore")
        self.assertEqual(code, 200, result)
        self.assertTrue(self.authenticated(s.admin), "l'administrateur qui restaure reste connecté")
        self.assertFalse(self.authenticated(thief))
        self.assertEqual(s.client().login("mja", "mdp-mja-1"), 401)   # ni l'ancien mot de passe
        self.assertEqual(s.client().login("mja", "mdp-mja-2"), 401)   # ni le compte, toujours désactivé
        self.assertFalse(self.authenticated(mjb_now))                 # toutes les sessions sont fermées
        self.assertFalse(self.authenticated(other_admin_tab))
        self.assertEqual(s.client().login("mjb", "mdp-mjb-1"), 401)
        self.assertEqual(s.client().login("mjb", "mdp-mjb-2"), 200)
        demoted = self.logged("admin2", "mdp-admin2-1")
        self.assertEqual(demoted.call("GET", "/api/admin/users")[0], 403)  # rôle retiré : pas rendu
        self.assertEqual(s.client().login("mjc", "mdp-mjc-1"), 401)   # comptes : ceux de l'archive
        users = {u["username"]: u for u in s.call("GET", "/api/admin/users")[1]}
        self.assertEqual(sorted(users), ["admin", "admin2", "mja", "mjb"])
        self.assertEqual((users["mja"]["active"], users["admin2"]["role"]), (False, "mj"))

    def test_restorer_absent_from_archive(self):
        s = self.s
        self.user("filler")
        old_boss2 = self.user("boss2", "admin", "ancien-mdp-boss2")
        _, entry = s.call("POST", "/api/admin/backups")
        # boss : créé après la sauvegarde ; boss2 : supprimé puis recréé (autre identifiant, autre mot de passe).
        self.user("boss", "admin", "mdp-boss")
        s.call("DELETE", f"/api/admin/users/{old_boss2['id']}")
        self.user("filler2")
        new_boss2 = self.user("boss2", "admin", "nouveau-mdp-boss2")
        self.assertNotEqual(new_boss2["id"], old_boss2["id"])
        s.call("PATCH", f"/api/admin/users/{new_boss2['id']}", {"display_name": "Boss deux (actuel)"})
        # boss2 restaure : le compte boss2 de l'archive (ancien id) prend son mot de passe actuel.
        client = self.logged("boss2", "nouveau-mdp-boss2")
        code, result = client.call("POST", f"/api/admin/backups/{entry['name']}/restore")
        self.assertEqual(code, 200, result)
        self.assertTrue(self.authenticated(client))
        self.assertEqual(client.call("GET", "/api/admin/users")[0], 200)
        self.assertEqual(s.client().login("boss2", "nouveau-mdp-boss2"), 200)
        self.assertEqual(s.client().login("boss2", "ancien-mdp-boss2"), 401)
        names = [u["username"] for u in client.call("GET", "/api/admin/users")[1]]
        self.assertEqual(sorted(names), ["admin", "boss2", "filler"])     # boss, créé après, n'y est pas
        self.assertEqual(result["disabled"], [])       # le boss2 de l'archive est repris par le restaurateur
        me = client.call("GET", "/api/auth/status")[1]["user"]
        self.assertEqual(me["display_name"], "Boss deux (actuel)")      # nom affiché actuel repris aussi
        # Annulation (archive de sécurité de cette restauration) : les comptes créés entre les deux dates (boss,
        # filler2), retirés par la restauration, reviennent actifs (.derniere-restauration).
        code, undo = client.call("POST", f"/api/admin/backups/{result['safety']['name']}/restore")
        self.assertEqual((code, undo["disabled"]), (200, []))
        # boss (absent de l'archive) restaure : il est ajouté, administrateur.
        client = self.logged("boss", "mdp-boss")
        code, result = client.call("POST", f"/api/admin/backups/{entry['name']}/restore")
        self.assertEqual(code, 200, result)
        self.assertTrue(self.authenticated(client))
        self.assertEqual(client.call("GET", "/api/admin/users")[0], 200)
        self.assertEqual(s.client().login("boss", "mdp-boss"), 200)
        names = [u["username"] for u in client.call("GET", "/api/admin/users")[1]]
        self.assertEqual(sorted(names), ["admin", "boss", "boss2", "filler"])

    def test_command_line_restore(self):
        """En ligne de commande (python -m maze.backup --restore) : pas d'administrateur qui restaure ; comptes des
        deux côtés repris de la base en service, toutes les sessions fermées ; sans administrateur actif : refus."""
        s = self.s
        mj = self.user("mj1")
        _, entry = s.call("POST", "/api/admin/backups")
        s.call("PATCH", f"/api/admin/users/{mj['id']}", {"password": "mdp-mj1-2"})
        archive = Path(s.tmp.name) / "backups" / entry["name"]
        out = subprocess.run([sys.executable, "-m", "maze.backup", "--restore", str(archive)], cwd=BACKEND,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(self.authenticated(s.admin))
        self.assertEqual(s.client().login("mj1", "mdp-mj1-1"), 401)
        self.assertEqual(s.client().login("mj1", "mdp-mj1-2"), 200)
        self.assertEqual(s.admin.login("admin", "admin-test-pass"), 200)
        # Archive dont le seul administrateur est désactivé depuis, par un administrateur créé après elle :
        # refusée en ligne de commande (aucun administrateur actif), acceptée depuis l'interface (qui restaure).
        _, entry = s.call("POST", "/api/admin/backups")
        archive = Path(s.tmp.name) / "backups" / entry["name"]
        self.user("tiers", "admin", "mdp-tiers")
        tiers = self.logged("tiers", "mdp-tiers")
        admin_id = next(u["id"] for u in tiers.call("GET", "/api/admin/users")[1] if u["username"] == "admin")
        self.assertEqual(tiers.call("PATCH", f"/api/admin/users/{admin_id}", {"active": False})[0], 200)
        out = subprocess.run([sys.executable, "-m", "maze.backup", "--restore", str(archive)], cwd=BACKEND,
                             capture_output=True, text=True, timeout=120)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("aucun administrateur actif", out.stderr)
        self.assertTrue(self.authenticated(tiers), "refus : rien n'a changé")
        code, result = tiers.call("POST", f"/api/admin/backups/{entry['name']}/restore")
        self.assertEqual(code, 200, result)
        self.assertTrue(self.authenticated(tiers))
        self.assertEqual(s.client().login("admin", "admin-test-pass"), 401)   # toujours désactivé

    def test_command_line_exact_restore(self):
        """Contre-revue (audit M20) : --exact, réservé à la ligne de commande (retour automatique d'une mise à jour
        ratée avec la sauvegarde -maj de quelques minutes) : base remise telle qu'archivée, sans fusion des comptes
        ni révocation : sessions ouvertes et jetons Foundry gardés."""
        import urllib.request
        s = self.s
        self.user("mj1")
        mj_tab = self.logged("mj1", "mdp-mj1-1")
        _, camp = s.call("POST", "/api/campaigns", {"name": "Exacte"})
        _, created = s.call("POST", "/api/admin/foundry/tokens", {"name": "Monde", "campaigns": [camp["id"]]})
        _, entry = s.call("POST", "/api/admin/backups", {})
        s.call("POST", "/api/campaigns", {"name": "Créée après"})
        archive = Path(s.tmp.name) / "backups" / entry["name"]
        for bad in (["--exact"], ["--restore", str(archive), "--exact", "--keep-archive-accounts"]):
            out = subprocess.run([sys.executable, "-m", "maze.backup", *bad], cwd=BACKEND,
                                 capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 2, out.stderr)
        out = subprocess.run([sys.executable, "-m", "maze.backup", "--restore", str(archive), "--exact"], cwd=BACKEND,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("telle qu'archivée", out.stdout)
        self.assertTrue(self.authenticated(s.admin), "sessions gardées")
        self.assertTrue(self.authenticated(mj_tab))
        names = [c["name"] for c in s.call("GET", "/api/campaigns")[1]]
        self.assertIn("Exacte", names)
        self.assertNotIn("Créée après", names)
        req = urllib.request.Request(f"{s.base}/api/foundry/v1/campaigns",
                                     headers={"Authorization": f"Bearer {created['token']}"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200, "jeton Foundry gardé")
        self.assertIn("-avant-restauration", " ".join(p.name for p in archive.parent.iterdir()))
        # Sans --exact (comportement par défaut) : jetons supprimés, sessions fermées.
        out = subprocess.run([sys.executable, "-m", "maze.backup", "--restore", str(archive)], cwd=BACKEND,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(self.authenticated(mj_tab))

    def test_command_line_restore_without_current_database(self):
        """Base en service absente (volume neuf, base endommagée retirée avant de restaurer) : rien à reprendre,
        les comptes sont ceux de l'archive (contre-revue : plantait sur « no such table: actuelle.users »)."""
        s = self.s
        self.user("mj1")
        _, entry = s.call("POST", "/api/admin/backups")
        archive = Path(s.tmp.name) / "backups" / entry["name"]
        for name in ("maze.db", "maze.db-wal", "maze.db-shm"):
            (Path(s.tmp.name) / name).unlink(missing_ok=True)
        out = subprocess.run([sys.executable, "-m", "maze.backup", "--restore", str(archive)], cwd=BACKEND,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(s.client().login("mj1", "mdp-mj1-1"), 200)
        self.assertEqual(s.admin.login("admin", "admin-test-pass"), 200)

    def test_deleted_accounts_come_back_disabled(self):
        """Contre-revue : un compte supprimé depuis l'archive (y compris quand son id a été réutilisé par un compte
        créé depuis) revient désactivé, listé dans la réponse et le journal, et non avec son ancien mot de passe."""
        s = self.s
        self.user("ancien", "admin", "mdp-ancien")
        bob = self.user("bob", "mj", "mdp-bob-1")
        _, entry = s.call("POST", "/api/admin/backups")
        ancien_id = next(u["id"] for u in s.call("GET", "/api/admin/users")[1] if u["username"] == "ancien")
        s.call("DELETE", f"/api/admin/users/{ancien_id}")
        s.call("DELETE", f"/api/admin/users/{bob['id']}")
        alice = self.user("alice", "mj", "mdp-alice")
        self.assertEqual(alice["id"], ancien_id, "identifiant de l'administrateur supprimé réutilisé par SQLite")
        with self.assertLogs("maze", "WARNING") as logs:
            code, result = s.call("POST", f"/api/admin/backups/{entry['name']}/restore")
        self.assertEqual(code, 200, result)
        self.assertEqual(result["disabled"], ["ancien", "bob"])
        self.assertTrue(any("ancien" in line and "bob" in line for line in logs.output), logs.output)
        self.assertEqual(s.client().login("ancien", "mdp-ancien"), 401)
        self.assertEqual(s.client().login("bob", "mdp-bob-1"), 401)
        users = {u["username"]: u["active"] for u in s.call("GET", "/api/admin/users")[1]}
        self.assertEqual(users, {"admin": True, "ancien": False, "bob": False})

    def test_command_line_disaster_recovery(self):
        """Instance neuve (seul son compte admin de démarrage) et archive d'une autre instance dont l'administrateur
        s'appelle autrement : sans option, ses comptes reviendraient désactivés, donc refus avec la consigne ; avec
        --keep-archive-accounts, les comptes de l'archive sont repris tels quels."""
        s = self.s
        self.user("ranglier", "admin", "mdp-ranglier")
        self.user("mj2", "mj", "mdp-mj2-1")
        _, entry = s.call("POST", "/api/admin/backups")
        archive = Path(s.tmp.name) / "backups" / entry["name"]
        # Simule l'instance neuve : seuls les comptes « admin » (démarrage) existent dans la base en service,
        # et l'archive ne contient pas « admin » (retiré de la copie de l'archive).
        stripped = Path(s.tmp.name) / "backups" / "maze-20200101-000000-import.tar.gz"
        _strip_account(archive, stripped, "admin")
        for name in ("ranglier", "mj2"):
            uid = next(u["id"] for u in s.call("GET", "/api/admin/users")[1] if u["username"] == name)
            s.call("DELETE", f"/api/admin/users/{uid}")
        out = subprocess.run([sys.executable, "-m", "maze.backup", "--restore", str(stripped)], cwd=BACKEND,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("--keep-archive-accounts", out.stderr)
        self.assertEqual(s.client().login("ranglier", "mdp-ranglier"), 401)
        out = subprocess.run([sys.executable, "-m", "maze.backup", "--restore", str(stripped),
                              "--keep-archive-accounts"], cwd=BACKEND, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(s.client().login("ranglier", "mdp-ranglier"), 200)
        self.assertEqual(s.client().login("mj2", "mdp-mj2-1"), 200)

    def test_command_line_lists_disabled_accounts(self):
        s = self.s
        old = self.user("parti", "mj", "mdp-parti")
        _, entry = s.call("POST", "/api/admin/backups")
        s.call("DELETE", f"/api/admin/users/{old['id']}")
        out = subprocess.run([sys.executable, "-m", "maze.backup", "--restore",
                              str(Path(s.tmp.name) / "backups" / entry["name"])], cwd=BACKEND,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("désactivés", out.stdout)
        self.assertIn("parti", out.stdout)
        self.assertEqual(s.client().login("parti", "mdp-parti"), 401)



class AccessSettingsRestoreTest(unittest.TestCase):
    """Contre-revue : les réglages d'accès de l'instance (connecteur Claude : identifiant et secret du client OAuth,
    accès activé, campagnes ouvertes ; journal de ses actions) sont repris de la base en service, comme les comptes ;
    --keep-archive-accounts garde ceux de l'archive (reprise après sinistre)."""

    def test_connector_settings_from_current(self):
        import sqlite3
        s = Server()
        try:
            def state():
                return s.call("GET", "/api/admin/claude")[1]
            ids = {}
            for name in ("Ouverte", "Lecture", "Remplacée"):
                ids[name] = s.call("POST", "/api/campaigns", {"name": name})[1]["id"]
            s.call("POST", "/api/admin/claude/credentials", raw=b"{}", content_type="application/json")
            old_client = state()["client_id"]
            s.call("PUT", "/api/admin/claude", {"enabled": True, "grants": [
                {"campaign_id": ids["Ouverte"], "mode": "write"}, {"campaign_id": ids["Lecture"], "mode": "read"},
                {"campaign_id": ids["Remplacée"], "mode": "write"}]})
            _, entry = s.call("POST", "/api/admin/backups")
            # Après la sauvegarde : secret renouvelé, accès suspendu, droits réduits ; « Remplacée » supprimée et son
            # numéro repris par une nouvelle campagne ouverte en écriture ; une action de Claude journalisée.
            s.call("POST", "/api/admin/claude/credentials", raw=b"{}", content_type="application/json")
            new_client = state()["client_id"]
            s.call("DELETE", f"/api/campaigns/{ids['Remplacée']}")
            # Numéro repris : plus par SQLite depuis la migration 026 (AUTOINCREMENT, audit M5), mais toujours possible
            # entre deux archives (restauration d'une plus ancienne, puis de celle-ci) : posé à la main.
            other = ids["Remplacée"]
            with sqlite3.connect(Path(s.tmp.name) / "maze.db") as conn:
                conn.execute("INSERT INTO campaigns (id, name) VALUES (?, 'Nouvelle')", (other,))
            s.call("PUT", "/api/admin/claude", {"enabled": False, "grants": [
                {"campaign_id": ids["Ouverte"], "mode": "read"}, {"campaign_id": other, "mode": "write"}]})
            with sqlite3.connect(Path(s.tmp.name) / "maze.db") as conn:
                conn.execute("INSERT INTO mcp_log (tool, target) VALUES ('update_entry', 'après la sauvegarde')")
            code, result = s.call("POST", f"/api/admin/backups/{entry['name']}/restore")
            self.assertEqual(code, 200, result)
            after = state()
            self.assertEqual((after["client_id"], after["enabled"]), (new_client, False))
            self.assertEqual(after["grants"], [{"campaign_id": ids["Ouverte"], "mode": "read"}])  # pas « Remplacée »
            self.assertIn("après la sauvegarde", [e["target"] for e in after["log"]])
            # Reprise après sinistre en ligne de commande : réglages de l'archive.
            out = subprocess.run([sys.executable, "-m", "maze.backup", "--restore",
                                  str(Path(s.tmp.name) / "backups" / entry["name"]), "--keep-archive-accounts"],
                                 cwd=BACKEND, capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(s.admin.login("admin", "admin-test-pass"), 200)
            archived = state()
            self.assertEqual((archived["client_id"], archived["enabled"]), (old_client, True))
            self.assertEqual(len(archived["grants"]), 3)
        finally:
            s.stop()

    def test_login_addresses_from_current(self):
        """Vérification finale R1 : adresses connues des comptes (limitation des essais, migration 029), données de
        sécurité de l'instance : reprises de la base en service à la restauration, pour les comptes présents des deux
        côtés ; jamais ramenées par l'archive (adresse oubliée depuis, compte supprimé) ; celles de l'archive avec
        --keep-archive-accounts ; absentes de l'export d'une campagne."""
        import io
        import sqlite3
        import zipfile
        s = Server()
        db_file = Path(s.tmp.name) / "maze.db"

        def rows():
            with sqlite3.connect(db_file) as conn:
                return sorted(conn.execute("SELECT u.username, a.address FROM login_addresses AS a "
                                           "JOIN users AS u ON u.id = a.user_id").fetchall())
        try:
            s.call("POST", "/api/admin/users", {"username": "ancien", "display_name": "Ancien", "password": "mdp-ancien-1"})
            with sqlite3.connect(db_file) as conn:
                conn.execute("INSERT INTO login_addresses SELECT id, '10.0.0.1', strftime('%Y-%m-%dT%H:%M:%fZ') "
                             "FROM users WHERE username IN ('admin', 'ancien')")
            _, camp = s.call("POST", "/api/campaigns", {"name": "Adresses"})
            _, entry = s.call("POST", "/api/admin/backups")
            # Après la sauvegarde : 10.0.0.1 oubliée pour admin, 10.0.0.2 ajoutée, « ancien » supprimé.
            ancien = next(u["id"] for u in s.call("GET", "/api/admin/users")[1] if u["username"] == "ancien")
            self.assertEqual(s.call("DELETE", f"/api/admin/users/{ancien}")[0], 204)
            with sqlite3.connect(db_file) as conn:
                conn.execute("DELETE FROM login_addresses WHERE address = '10.0.0.1'")
                conn.execute("INSERT INTO login_addresses SELECT id, '10.0.0.2', strftime('%Y-%m-%dT%H:%M:%fZ') "
                             "FROM users WHERE username = 'admin'")
            self.assertEqual(rows(), [("admin", "10.0.0.2"), ("admin", "127.0.0.1")])
            code, result = s.call("POST", f"/api/admin/backups/{entry['name']}/restore")
            self.assertEqual(code, 200, result)
            self.assertEqual(result["disabled"], ["ancien"])
            self.assertEqual(rows(), [("admin", "10.0.0.2"), ("admin", "127.0.0.1")])
            code, archive = s.call("GET", f"/api/campaigns/{camp['id']}/export")
            self.assertEqual(code, 200)
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                exported = b"".join(zf.read(n) for n in zf.namelist() if n.endswith(".json"))
            self.assertNotIn(b"10.0.0.", exported)
            self.assertNotIn(b"login_addresses", exported)
            # Reprise après sinistre : celles de l'archive.
            out = subprocess.run([sys.executable, "-m", "maze.backup", "--restore",
                                  str(Path(s.tmp.name) / "backups" / entry["name"]), "--keep-archive-accounts"],
                                 cwd=BACKEND, capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(rows(), [("admin", "10.0.0.1"), ("admin", "127.0.0.1"), ("ancien", "10.0.0.1")])
        finally:
            s.stop()

    def test_connector_log_keeps_campaign_name(self):
        """Re-revue : le journal du connecteur garde le nom de la campagne au moment de l'action (migration 024),
        juste même après une restauration qui rend son numéro à une autre campagne ; repli sur le nom actuel pour
        une entrée sans nom (antérieure à la migration)."""
        import sqlite3
        from maze import connector, db
        s = Server()
        try:
            db_path = Path(s.tmp.name) / "maze.db"
            first = s.call("POST", "/api/campaigns", {"name": "Archivée"})[1]["id"]
            _, entry = s.call("POST", "/api/admin/backups")
            s.call("DELETE", f"/api/campaigns/{first}")
            # Numéro repris (entre deux archives depuis la migration 026, voir plus haut) : posé à la main.
            second = first
            with sqlite3.connect(db_path) as conn:
                conn.execute("INSERT INTO campaigns (id, name) VALUES (?, 'Créée depuis')", (second,))
            with db.transaction(db_path) as conn:
                connector.log_action(conn, None, "update_entry", second, "PNJ 3", True)
                conn.execute("INSERT INTO mcp_log (tool, campaign_id, target) VALUES ('get_campaign', ?, 'ancienne')",
                             (second,))   # entrée sans nom : repli sur la jointure
            code, _ = s.call("POST", f"/api/admin/backups/{entry['name']}/restore")
            self.assertEqual(code, 200)
            log = {e["target"]: e["campaign"] for e in s.call("GET", "/api/admin/claude")[1]["log"]}
            self.assertEqual(log, {"PNJ 3": "Créée depuis", "ancienne": "Archivée"})
            # Migration 024 depuis une base 023 : noms des entrées existantes renseignés.
            with sqlite3.connect(db_path) as conn:
                conn.execute("ALTER TABLE mcp_log DROP COLUMN campaign_name")
                undo_entry_revision(conn)   # migration 025
                undo_user_locale(conn)   # migration 030
                undo_nested_factions(conn)   # migration 031
                rewind_schema(conn, 23)
            db.migrate(db_path)
            with sqlite3.connect(db_path) as conn:
                names = {r[0]: r[1] for r in conn.execute("SELECT target, campaign_name FROM mcp_log")}
            self.assertEqual(names, {"PNJ 3": "Archivée", "ancienne": "Archivée"})
        finally:
            s.stop()


class SequenceRestoreTest(unittest.TestCase):
    """Contre-revue (M5 = DATA-8) : une restauration ne ramène pas le compteur d'identifiants de l'archive ; les
    numéros des fiches créées depuis ne sont pas redonnés, restauration exacte (--exact) comprise, dont les données
    restent celles de l'archive."""

    def test_counters_kept_after_restore(self):
        import sqlite3
        s = Server()
        try:
            cid = s.call("POST", "/api/campaigns", {"name": "Compteurs"})[1]["id"]
            first = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Avant"})[1]["id"]
            _, entry = s.call("POST", "/api/admin/backups")
            later = [s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": f"Après {i}"})[1]["id"] for i in range(3)]
            doc = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Après"})[1]["id"]
            self.assertEqual(s.call("POST", f"/api/admin/backups/{entry['name']}/restore")[0], 200)
            self.assertEqual([n["name"] for n in s.call("GET", f"/api/campaigns/{cid}/graph")[1]["npcs"]], ["Avant"])
            again = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Nouveau"})[1]["id"]
            self.assertGreater(again, max(later))
            self.assertGreater(s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Nouvelle"})[1]["id"], doc)
            # Restauration exacte (retour automatique d'update.sh) : données de l'archive, compteur gardé.
            db_path = Path(s.tmp.name) / "maze.db"
            out = subprocess.run([sys.executable, "-m", "maze.backup", "--restore",
                                  str(Path(s.tmp.name) / "backups" / entry["name"]), "--exact"],
                                 cwd=BACKEND, capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual([r[0] for r in conn.execute("SELECT name FROM npcs ORDER BY id")], ["Avant"])
                self.assertEqual(conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'npcs'").fetchone()[0],
                                 again)
            self.assertEqual(first, 1)
        finally:
            s.stop()


class UndoRestoreTest(unittest.TestCase):
    """Re-revue : annuler une restauration (restaurer l'archive « -avant-restauration » qu'elle vient de créer) ne
    désactive pas les comptes créés entre la date de l'archive restaurée et la restauration ; règle inchangée pour
    toute autre archive, même une copie de celle-là."""

    def setUp(self):
        self.s = Server()
        self.addCleanup(self.s.stop)
        s = self.s
        s.call("POST", "/api/admin/users", {"username": "avant", "role": "mj", "password": "mdp-avant-1"})
        _, self.entry = s.call("POST", "/api/admin/backups")
        s.call("POST", "/api/admin/users", {"username": "nouveau", "role": "mj", "password": "mdp-nouveau-1"})
        self.backups = Path(s.tmp.name) / "backups"

    def cli(self, archive: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "maze.backup", "--restore", str(archive)], cwd=BACKEND,
                              capture_output=True, text=True, timeout=120)

    def test_undo_keeps_accounts_created_since(self):
        import json
        s = self.s
        code, result = s.call("POST", f"/api/admin/backups/{self.entry['name']}/restore")
        self.assertEqual((code, result["disabled"]), (200, []))
        self.assertEqual(s.client().login("nouveau", "mdp-nouveau-1"), 401)       # absent de l'archive
        state = json.loads((self.backups / ".derniere-restauration").read_text())
        self.assertEqual(state["safety"], result["safety"]["name"])
        self.assertEqual([a["username"] for a in state["removed"]], ["nouveau"])
        code, undo = s.call("POST", f"/api/admin/backups/{result['safety']['name']}/restore")
        self.assertEqual((code, undo["disabled"]), (200, []))
        self.assertEqual(s.client().login("nouveau", "mdp-nouveau-1"), 200)

    def test_undo_on_command_line(self):
        s = self.s
        out = self.cli(self.backups / self.entry["name"])
        self.assertEqual(out.returncode, 0, out.stderr)
        safety = next(p for p in self.backups.glob("maze-*-avant-restauration.tar.gz"))
        out = self.cli(safety)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("désactivés", out.stdout)
        self.assertEqual(s.client().login("nouveau", "mdp-nouveau-1"), 200)

    def test_other_archive_keeps_rule(self):
        import shutil
        s = self.s
        _, result = s.call("POST", f"/api/admin/backups/{self.entry['name']}/restore")
        copy = self.backups / "maze-20200101-000000-import.tar.gz"     # même contenu, autre archive
        shutil.copyfile(self.backups / result["safety"]["name"], copy)
        code, other = s.call("POST", f"/api/admin/backups/{copy.name}/restore")
        self.assertEqual((code, other["disabled"]), (200, ["nouveau"]))
        self.assertEqual(s.client().login("nouveau", "mdp-nouveau-1"), 401)


def _strip_account(source: Path, target: Path, username: str) -> None:
    """Copie d'une archive dont la base n'a plus le compte `username`."""
    import sqlite3 as _sqlite3
    import tarfile
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(source, "r:gz") as tar:
            tar.extractall(tmp, filter="data")
        with _sqlite3.connect(Path(tmp) / "maze.db") as conn:
            conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.close()
        with tarfile.open(target, "w:gz") as tar:
            for path in sorted(Path(tmp).rglob("*")):
                tar.add(path, arcname=path.relative_to(tmp).as_posix(), recursive=False)


class HotRestoreTest(unittest.TestCase):
    """Audit M7 (DATA-4) : pendant une restauration, les éditeurs temps réel restés ouverts n'écrivent plus rien
    dans la base restaurée. Les salles sont fermées (le navigateur se reconnecte plus tard, code 4000) et les
    connexions refusées avant la copie, depuis l'administration comme en ligne de commande (fichier
    <data>/.restauration-en-cours verrouillé pendant toute la restauration)."""

    def setUp(self):
        self.s = Server()
        self.addCleanup(self.s.stop)
        self.db_path = Path(self.s.tmp.name) / "maze.db"
        _, camp = self.s.call("POST", "/api/campaigns", {"name": "Restauration à chaud"})
        _, doc = self.s.call("POST", f"/api/campaigns/{camp['id']}/documents", {"title": "Scène"})
        self.did = doc["id"]

    def state(self):
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            content = conn.execute("SELECT content FROM documents WHERE id = ?", (self.did,)).fetchone()[0]
            log = [bytes(r[0]) for r in conn.execute(
                "SELECT data FROM document_updates WHERE document_id = ? ORDER BY id", (self.did,))]
        return content, log

    def editor(self, client: int):
        from tests.test_api import recv, recv_until
        ws = self.s.admin.live(self.did, client)
        welcome, _ = recv_until(ws, "welcome")
        self.assertTrue(welcome["leader"])
        ws.send(bytes([0]) + b"etat-1")
        ack = recv(ws)
        ws.send(json.dumps({"t": "content", "html": "<p>Version sauvegardée</p>", "upto": int.from_bytes(ack[1:9], "big")}))
        ws.send(bytes([0]) + b"etat-1b")
        recv(ws)
        return ws

    def write_during(self, ws, step: str):
        """Pendant l'étape `step` de la restauration (remplacement des images, après la copie de la base), l'éditeur
        resté ouvert envoie une frappe puis son HTML (comme le meneur), et attend que le serveur les traite."""
        from maze import backup
        real = getattr(backup, step)
        outcome = {}

        def wrapped(*args, **kwargs):
            try:
                ws.send(bytes([0]) + b"etat-pendant")
                ws.send(json.dumps({"t": "content", "html": "<p>Écrit PENDANT la restauration</p>", "upto": 10**6}))
                ws.send(json.dumps({"t": "content", "html": "<p>Écrit PENDANT la restauration</p>", "upto": 3}))
            except Exception as exc:  # noqa: BLE001 - connexion déjà fermée par le serveur : c'est le but
                outcome["send"] = exc
            time.sleep(0.5)
            return real(*args, **kwargs)

        setattr(backup, step, wrapped)
        self.addCleanup(setattr, backup, step, real)
        return outcome

    def closed_code(self, ws) -> int | None:
        try:
            while True:
                ws.recv(timeout=3)
        except Exception as exc:  # noqa: BLE001
            rcvd = getattr(exc, "rcvd", None)
            return rcvd.code if rcvd else None

    def test_restore_from_admin_with_open_editor(self):
        ws = self.editor(11)
        _, entry = self.s.call("POST", "/api/admin/backups")
        ws.send(bytes([0]) + b"etat-2-apres-sauvegarde")
        self.write_during(ws, "_swap_media")
        code, _ = self.s.call("POST", f"/api/admin/backups/{entry['name']}/restore")
        self.assertEqual(code, 200)
        self.assertEqual(self.closed_code(ws), 4000)   # se reconnectera, et verra la nouvelle époque
        content, log = self.state()
        self.assertEqual(content, "<p>Version sauvegardée</p>")
        self.assertNotIn(b"etat-pendant", log)
        self.assertNotIn(b"etat-2-apres-sauvegarde", log)
        # Après la restauration, l'éditeur se reconnecte normalement.
        from tests.test_api import recv_until
        again = self.s.admin.live(self.did, 12)
        self.assertIn("epoch", recv_until(again, "welcome")[0])
        again.close()

    def test_admin_restore_suspends_before_copy(self):
        """Contre-revue M7 : depuis l'administration, le hub est suspendu avant même l'appel de backup.restore (donc
        avant le verrou de .restauration-en-cours) : salles fermées, connexions et écritures REST refusées (503),
        puis tout est rouvert après. Sans hub.suspend(), rien de cela n'est vrai à l'entrée de backup.restore."""
        from maze import backup
        hub = self.s.server.config.app.state.hub
        ws = self.editor(31)
        _, npc = self.s.call("POST", "/api/campaigns", {"name": "Autre"})
        _, entry = self.s.call("POST", "/api/admin/backups")
        real = backup.restore
        seen = {}

        def spy(*args, **kwargs):
            seen["marker"] = backup.restore_in_progress(Path(self.s.tmp.name))
            seen["suspended"], seen["rooms"] = hub.suspended, len(hub.rooms)
            seen["patch"] = self.s.call("PATCH", f"/api/documents/{self.did}", {"title": "Pendant"})
            return real(*args, **kwargs)

        backup.restore = spy
        self.addCleanup(setattr, backup, "restore", real)
        code, _ = self.s.call("POST", f"/api/admin/backups/{entry['name']}/restore")
        self.assertEqual(code, 200)
        self.assertEqual((seen["marker"], seen["suspended"], seen["rooms"]), (False, True, 0))
        self.assertEqual(seen["patch"][0], 503)
        self.assertIn("Restauration en cours", seen["patch"][1]["detail"])
        self.assertEqual(self.closed_code(ws), 4000)
        self.assertFalse(hub.suspended)
        self.assertEqual(self.s.call("PATCH", f"/api/documents/{self.did}", {"title": "Après"})[0], 200)

    def test_rest_writes_refused_during_command_line_restore(self):
        """Contre-revue M7 : pendant une restauration (verrou tenu, ici comme en ligne de commande), toute écriture
        REST est refusée en 503 au lieu d'être acceptée puis perdue ; les lectures passent."""
        from maze import backup
        url = f"/api/documents/{self.did}"
        with backup.restore_marker(Path(self.s.tmp.name)):
            code, body = self.s.call("PATCH", url, {"title": "Perdu"})
            self.assertEqual((code, body["detail"]), (503, "Restauration en cours : réessayez dans un instant"))
            self.assertEqual(self.s.call("POST", "/api/campaigns", {"name": "Perdue"})[0], 503)
            self.assertEqual(self.s.call("GET", url)[0], 200)
            # Déconnexion (rien d'écrit en base) : toujours possible, la session est bien fermée.
            other = self.s.client()
            from tests.test_api import ADMIN_PASSWORD
            self.assertEqual(other.login("admin", ADMIN_PASSWORD), 503)
        other = self.s.client()
        self.assertEqual(other.login("admin", ADMIN_PASSWORD), 200)
        with backup.restore_marker(Path(self.s.tmp.name)):
            self.assertEqual(other.call("POST", "/api/auth/logout")[0], 200)
            self.assertEqual(other.call("GET", "/api/campaigns")[0], 401)
        self.assertEqual(self.s.call("PATCH", url, {"title": "Gardé"})[0], 200)

    def test_restore_from_command_line_while_server_runs(self):
        from maze import backup
        from maze.config import load_settings
        from tests.test_api import recv_until
        ws = self.editor(21)
        _, entry = self.s.call("POST", "/api/admin/backups")
        self.write_during(ws, "_swap_media")
        # Même processus que le serveur, mais sans passer par lui : comme python -m maze.backup --restore.
        backup.restore(Path(self.s.tmp.name) / "backups" / entry["name"], load_settings(), grace=0.2)
        self.assertEqual(self.closed_code(ws), 4000)
        content, log = self.state()
        self.assertEqual(content, "<p>Version sauvegardée</p>")
        self.assertNotIn(b"etat-pendant", log)
        # Restauration ordinaire : sessions fermées, reconnexion.
        from tests.test_api import ADMIN_PASSWORD
        self.assertEqual(self.s.admin.login("admin", ADMIN_PASSWORD), 200)
        # Restauration en cours dans un autre processus (verrou tenu) : connexion refusée, nouvel essai plus tard.
        with backup.restore_marker(Path(self.s.tmp.name)):
            refused = self.s.admin.live(self.did, 22)
            self.assertEqual(self.closed_code(refused), 4000)
        # Fichier laissé par une restauration interrompue (verrou libre) : ignoré.
        (Path(self.s.tmp.name) / backup.RESTORE_MARKER).write_text("ancien")
        ok = self.s.admin.live(self.did, 23)
        self.assertEqual(recv_until(ok, "welcome")[0]["t"], "welcome")
        ok.close()

    def test_failed_restore_releases_lock_and_reopens_editing(self):
        """Contre-revue M7 : une restauration qui échoue (archive refusée, ou images impossibles à remplacer) libère
        le verrou et rouvre l'édition temps réel."""
        from maze import backup
        from tests.test_api import recv_until
        data = Path(self.s.tmp.name)
        bad = data / "backups" / "maze-20200101-000000-manuelle.tar.gz"
        bad.parent.mkdir(exist_ok=True)
        bad.write_bytes(b"pas une archive")
        self.assertEqual(self.s.call("POST", f"/api/admin/backups/{bad.name}/restore")[0], 400)
        self.assertFalse((data / backup.RESTORE_MARKER).exists())
        self.assertFalse(backup.restore_in_progress(data))
        ws = self.s.admin.live(self.did, 31)
        self.assertEqual(recv_until(ws, "welcome")[0]["t"], "welcome")
        ws.close()
        # Échec au remplacement des images, après la copie de la base : base remise en l'état, verrou libéré.
        _, entry = self.s.call("POST", "/api/admin/backups")
        real = backup._swap_media

        def broken(*args, **kwargs):
            raise OSError("disque plein (simulé)")

        backup._swap_media = broken
        self.addCleanup(setattr, backup, "_swap_media", real)
        code, body = self.s.call("POST", f"/api/admin/backups/{entry['name']}/restore")
        self.assertEqual(code, 400, body)
        backup._swap_media = real
        self.assertFalse(backup.restore_in_progress(data))
        ws = self.s.admin.live(self.did, 32)
        self.assertEqual(recv_until(ws, "welcome")[0]["t"], "welcome")
        ws.close()

    def test_second_restore_refused_while_another_holds_the_lock(self):
        """Contre-revue M7 : une restauration lancée pendant qu'une autre tient le verrou (autre processus, par
        exemple la ligne de commande pendant une restauration depuis l'administration) est refusée tout de suite.
        Attendre le verrou la faisait tourner ensuite sur un fichier supprimé par la première : le serveur ne voyait
        plus de restauration en cours."""
        import threading
        from maze import backup
        from maze.config import load_settings
        data = Path(self.s.tmp.name)
        _, entry = self.s.call("POST", "/api/admin/backups")
        outcome: dict = {}

        def second():
            try:
                backup.restore(data / "backups" / entry["name"], load_settings())
                outcome["result"] = "restaurée"
            except backup.ArchiveError as exc:
                outcome["result"] = str(exc)

        with backup.restore_marker(data):
            worker = threading.Thread(target=second, daemon=True)
            worker.start()
            worker.join(5)
            self.assertFalse(worker.is_alive(), "seconde restauration bloquée sur le verrou")
            self.assertIn("déjà en cours", outcome["result"])
            self.assertTrue(backup.restore_in_progress(data))   # la première tient toujours le verrou
        self.assertFalse(backup.restore_in_progress(data))
        worker.join(30)


if __name__ == "__main__":
    unittest.main()
