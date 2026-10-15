"""Démarrage du serveur : un seul serveur par dossier de données (audit DATA-16), ménage des fichiers temporaires
laissés par un arrêt brutal (audit PERF-15).

Lancer : cd backend && python -m unittest tests.test_startup -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.test_api import ADMIN_PASSWORD, Server, _free_port

BACKEND = Path(__file__).resolve().parents[1]


def run_maze(data_dir: str, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    env = {**os.environ, "MAZE_DATA_DIR": data_dir, "MAZE_STATIC_DIR": data_dir, "MAZE_PASSWORD": ADMIN_PASSWORD}
    return subprocess.run([sys.executable, *args], cwd=BACKEND, env=env, capture_output=True, text=True,
                          timeout=timeout)


class SingleServerTest(unittest.TestCase):
    """Deux serveurs sur le même volume se marchaient dessus à la compaction du journal temps réel (DATA-16)."""

    def test_second_server_refused_cli_allowed(self):
        s = Server()
        try:
            # Second serveur (autre processus) sur le même dossier : refus immédiat, message clair.
            started = time.monotonic()
            second = run_maze(s.tmp.name, "-m", "uvicorn", "maze.app:app_factory", "--factory",
                              "--host", "127.0.0.1", "--port", str(_free_port()))
            self.assertNotEqual(second.returncode, 0)
            self.assertLess(time.monotonic() - started, 30)
            self.assertIn("Un autre serveur Maze utilise déjà le dossier de données", second.stderr)
            self.assertIn(f"processus {os.getpid()}", second.stderr)
            # Même chose dans le même processus (verrou par fichier ouvert).
            from maze.app import DataDirLocked, create_app
            from maze.config import load_settings
            with self.assertRaises(DataDirLocked):
                create_app(load_settings())
            # Le premier serveur fonctionne toujours.
            self.assertEqual(s.call("GET", "/api/health")[0], 200)
            # Ligne de commande : sauvegarde puis restauration, serveur en marche.
            done = run_maze(s.tmp.name, "-m", "maze.backup", "--suffix", "test")
            self.assertEqual(done.returncode, 0, done.stderr)
            archive = done.stdout.strip().splitlines()[-1]
            self.assertTrue(archive.endswith(".tar.gz") and Path(archive).is_file(), done.stdout)
            restored = run_maze(s.tmp.name, "-m", "maze.backup", "--restore", archive)
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertEqual(s.call("GET", "/api/health")[0], 200)
            # Verrou rendu à l'arrêt : un nouveau serveur démarre sur le même dossier.
            s.server.should_exit = True
            s.thread.join(5)
            app = create_app(load_settings())
            app.state.data_lock.close()
        finally:
            s.stop()


class LeftoversTest(unittest.TestCase):
    """PERF-15 : temporaires d'un import ou d'une restauration tués en cours de route, retirés au démarrage ; les
    images d'avant une restauration interrompue jamais supprimées, signalées."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        for key, value in {"MAZE_DATA_DIR": self.tmp.name, "MAZE_STATIC_DIR": self.tmp.name,
                           "MAZE_PASSWORD": ADMIN_PASSWORD}.items():
            self.addCleanup(os.environ.pop, key, None)
            os.environ[key] = value

    def start(self):
        from maze.app import create_app
        from maze.config import load_settings
        app = create_app(load_settings())
        self.addCleanup(app.state.data_lock.close)
        return app

    def leftovers(self) -> dict[str, Path]:
        (self.data / "backups").mkdir(exist_ok=True)
        paths = {"staging": self.data / ".import-z_0fuj0c", "upload": self.data / "tmpccr03l5v.zip",
                 "backup_upload": self.data / "backups" / ".import-12-3456.part",
                 "restore_work": self.data / ".restauration-k2j4h1", "old_media": self.data / ".media-remplace-1760000000",
                 "image": self.data / "media" / "1" / ".0123abcd.webp.tmp"}
        paths["image"].parent.mkdir(parents=True, exist_ok=True)
        paths["image"].write_bytes(b"RIFF")
        for key in ("staging", "restore_work", "old_media"):
            (paths[key] / "1").mkdir(parents=True)
            (paths[key] / "1" / "image.webp").write_bytes(b"x" * 100)
        paths["upload"].write_bytes(b"PK")
        paths["backup_upload"].write_bytes(b"\x1f\x8b")
        return paths

    def test_cleaned_at_startup(self):
        self.start().state.data_lock.close()   # base créée, puis « arrêt brutal »
        paths = self.leftovers()
        keep = self.data / "notes.txt"
        keep.write_text("à garder")
        with self.assertLogs("maze", "INFO") as logs:
            self.start()
        for key in ("staging", "upload", "backup_upload", "restore_work", "image"):
            self.assertFalse(paths[key].exists(), key)
        self.assertTrue((paths["old_media"] / "1" / "image.webp").is_file())   # jamais supprimé
        self.assertTrue(keep.is_file() and (self.data / "maze.db").is_file() and (self.data / "media").is_dir())
        warning = next(m for m in logs.output if ".media-remplace-1760000000" in m)
        self.assertIn("WARNING", warning)
        self.assertIn("rien n'est supprimé", warning)

    def test_restore_in_progress_and_report(self):
        from maze import backup
        paths = self.leftovers()
        # Restauration en cours en ligne de commande : son dossier de travail reste.
        with backup.restore_marker(self.data):
            with self.assertLogs("maze", "INFO") as logs:
                app = self.start()
            app.state.data_lock.close()
        self.assertTrue(paths["restore_work"].is_dir())
        self.assertFalse(paths["staging"].exists())
        report = backup.replaced_media(self.data)
        self.assertEqual([(r["name"], r["files"], r["size"]) for r in report], [(".media-remplace-1760000000", 1, 100)])
        self.assertTrue(any("restauration interrompue" in m for m in logs.output))

    def test_restore_marker_kept(self):
        """Restauration en ligne de commande commencée juste après la vérification du démarrage
        (restore_in_progress lu faux) : son fichier verrouillé .restauration-en-cours, qui correspond au motif
        .restauration-*, ne doit pas être supprimé, sinon le serveur ne verrait plus la restauration en cours et
        accepterait des écritures pendant la copie."""
        from unittest import mock
        from maze import backup
        self.start().state.data_lock.close()
        with backup.restore_marker(self.data), mock.patch.object(backup, "restore_in_progress", return_value=False):
            self.start().state.data_lock.close()
            self.assertTrue((self.data / backup.RESTORE_MARKER).is_file())
        self.assertFalse((self.data / backup.RESTORE_MARKER).exists())   # retiré par la restauration à sa fin

    # --- Restauration arrêtée net pendant le remplacement des images (contre-revue du lot 6) -----------------------

    def _swap_crash(self, when: str) -> None:
        """État d'avant : base avec une campagne de plus que l'archive, images a.webp et b.webp ; archive : a.webp.
        Restauration en ligne de commande tuée (os._exit, comme un SIGKILL) juste après la copie de la base
        (« before »), ou entre les deux renommages de _swap_media (« between »)."""
        import sqlite3
        from maze import backup
        from maze.config import load_settings
        self.start().state.data_lock.close()
        folder = self.data / "media" / "1"
        folder.mkdir(parents=True)
        (folder / "a.webp").write_bytes(b"A" * 10)
        archive = backup.backup(0, load_settings(), suffix="test")
        (folder / "b.webp").write_bytes(b"B" * 10)
        with sqlite3.connect(self.data / "maze.db") as conn:
            conn.execute("INSERT INTO campaigns (name) VALUES ('Après la sauvegarde')")
        script = f"""
import os
from pathlib import Path
from maze import backup
def swap(cfg, new_media):
    if {when!r} == "between":
        cfg.media_dir.rename(cfg.data_dir / ".media-remplace-1700000000")
    os._exit(9)
backup._swap_media = swap
backup.restore(Path({str(archive)!r}))
"""
        crashed = run_maze(self.tmp.name, "-c", script)
        self.assertEqual(crashed.returncode, 9, crashed.stderr)
        self.assertEqual(len(self.works()), 1)   # et le fichier .restauration-en-cours, sans verrou

    def works(self) -> list[Path]:
        return [p for p in self.data.glob(".restauration-*") if p.name != ".restauration-en-cours"]

    def campaigns(self) -> int:
        import sqlite3
        with sqlite3.connect(self.data / "maze.db") as conn:
            return conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]

    def test_swap_interrupted_between_renames(self):
        """media/ absent, images d'avant dans .media-remplace-*, images restaurées dans le dossier de travail : la base
        en service est la base restaurée, ses images sont remises en place (avant : dossier de travail supprimé, et
        l'alerte proposait de remettre les images d'avant avec la base restaurée)."""
        from maze import backup
        self._swap_crash("between")
        self.assertEqual(self.campaigns(), 0)   # base restaurée
        self.assertFalse((self.data / "media").exists())
        with self.assertLogs("maze", "INFO") as logs:
            self.start()
        self.assertEqual(sorted(p.name for p in (self.data / "media" / "1").iterdir()), ["a.webp"])
        old = self.data / ".media-remplace-1700000000" / "1"
        self.assertEqual(sorted(p.name for p in old.iterdir()), ["a.webp", "b.webp"])   # gardées
        self.assertEqual(len(list(self.data.glob(".media-remplace-*"))), 1)   # media/ vide recréé : pas gardé
        self.assertEqual(self.works(), [])
        self.assertEqual(backup.interrupted_restores(self.data), [])
        self.assertTrue(any("ses images remises en place" in m for m in logs.output), logs.output)

    def test_swap_interrupted_after_database_copy(self):
        """Arrêt juste après la copie de la base : media/ (images d'avant) gardé dans un .media-remplace-*, images de la
        base restaurée remises en place."""
        self._swap_crash("before")
        self.assertEqual(sorted(p.name for p in (self.data / "media" / "1").iterdir()), ["a.webp", "b.webp"])
        self.start()
        self.assertEqual(sorted(p.name for p in (self.data / "media" / "1").iterdir()), ["a.webp"])
        replaced = list(self.data.glob(".media-remplace-*"))
        self.assertEqual(len(replaced), 1)
        self.assertEqual(sorted(p.name for p in (replaced[0] / "1").iterdir()), ["a.webp", "b.webp"])
        self.assertEqual(self.works(), [])

    def test_swap_interrupted_other_database_kept(self):
        """La base en service n'est pas celle du dossier de travail (ici modifiée après l'arrêt) et media/ manque :
        rien n'est remis en place ni supprimé, le dossier est signalé (journal, Administration > Sauvegardes)."""
        import sqlite3
        from maze import backup
        self._swap_crash("between")
        with sqlite3.connect(self.data / "maze.db") as conn:
            conn.execute("INSERT INTO campaigns (name) VALUES ('Autre')")
        with self.assertLogs("maze", "INFO") as logs:
            self.start()
        self.assertTrue(backup.media_empty(self.data))   # media/ recréé vide par load_settings
        work = self.works()[0]
        self.assertTrue((work / "media" / "1" / "a.webp").is_file())
        self.assertEqual([(r["name"], r["files"], r["media_missing"]) for r in backup.interrupted_restores(self.data)],
                         [(work.name, 1, True)])
        self.assertTrue(any("rien n'est remis en place" in m for m in logs.output), logs.output)

    def test_reported_in_admin(self):
        s = Server()
        try:
            old = Path(s.tmp.name) / ".media-remplace-1760000000" / "3"
            old.mkdir(parents=True)
            (old / "a.webp").write_bytes(b"x" * 10)
            # media/ vide (recréé par load_settings à chaque démarrage) : compte comme absent (contre-revue du lot 6).
            code, usage = s.call("GET", "/api/admin/backups/usage")
            self.assertEqual(code, 200, usage)
            self.assertTrue(usage["replaced_media"][0]["media_missing"])
            (Path(s.tmp.name) / "media" / "3").mkdir(parents=True)
            (Path(s.tmp.name) / "media" / "3" / "c.webp").write_bytes(b"y")
            code, usage = s.call("GET", "/api/admin/backups/usage")
            self.assertEqual([(m["name"], m["files"], m["size"], m["media_missing"]) for m in usage["replaced_media"]],
                             [(".media-remplace-1760000000", 1, 10, False)])
            self.assertNotIn("path", usage["replaced_media"][0])
            # Images d'une restauration interrompue gardées (contre-revue du lot 6).
            kept = Path(s.tmp.name) / ".restauration-abc123" / "media" / "2"
            kept.mkdir(parents=True)
            (kept / "b.webp").write_bytes(b"x" * 7)
            code, usage = s.call("GET", "/api/admin/backups/usage")
            self.assertEqual([(m["name"], m["files"], m["size"], m["media_missing"]) for m in usage["interrupted_restores"]],
                             [(".restauration-abc123", 1, 7, False)])
            self.assertNotIn("path", usage["interrupted_restores"][0])
        finally:
            s.stop()


if __name__ == "__main__":
    unittest.main()
