"""deploy/update.sh et deploy/maze-updater.sh exécutés pour de vrai, dans un dépôt git jetable (origine locale),
avec une commande docker simulée (aucun Docker requis) : sauvegarde avant mise à jour, dossier control/,
version en service, retour arrière automatique (audit M20), dossiers control/ et status/ à sens unique et
attaque par lien symbolique (audit H2), transition depuis les scripts de production d'avant le lot 2.

Lancer : cd backend && python -m unittest -q tests.test_deploy
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BEFORE_LOT2 = Path(__file__).resolve().parent / "fixtures" / "deploy-avant-lot2"
ARCHIVE = "/data/backups/maze-20261014-101500-maj.tar.gz"

# docker simulé. Phase du conteneur dans $FAKE/phase : old (ancienne version en service), new (nouvelle version
# démarrée), down (arrêté), back (version précédente relancée). Réponses réglées par variables d'environnement :
# MIGRATING_CHECKS = nombre de contrôles pendant lesquels la nouvelle version migre (fichier .migration-en-cours,
# santé en échec), NEW_STATE = état Docker de la nouvelle version (« restarting 3 »…), BACK_EXACT = l'image
# précédente connaît maze.backup --exact.
DOCKER = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS"
phase=$(cat "$FAKE/phase" 2>/dev/null || echo old)
healthy() {
    case "$phase" in
        old) [ "${OLD_HEALTHY:-1}" = 1 ] ;;
        new) [ "${NEW_HEALTHY:-1}" = 1 ] && [ "$(cat "$FAKE/migration-checks" 2>/dev/null || echo 0)" -ge "${MIGRATING_CHECKS:-0}" ] ;;
        back) [ "${BACK_HEALTHY:-1}" = 1 ] ;;
        *) return 1 ;;
    esac
}
case "$*" in
    "info "*) echo "$PWD" ;;
    "compose exec -T maze python -c import urllib"*) healthy || exit 1 ;;
    "compose exec -T maze printenv MAZE_VERSION")
        healthy || exit 1
        case "$phase" in old) echo "${RUNNING_VERSION:-dev}" ;; *) echo "${NEW_VERSION:-dev}" ;; esac ;;
    "compose exec -T maze python -c import os, sqlite3"*) echo "${SCHEMA_BEFORE:-24}" ;;
    "compose exec -T maze sh -c test -e "*".migration-en-cours"*)
        [ "$phase" = new ] || exit 1
        n=$(cat "$FAKE/migration-checks" 2>/dev/null || echo 0)
        [ "$n" -lt "${MIGRATING_CHECKS:-0}" ] || exit 1
        echo $((n + 1)) > "$FAKE/migration-checks" ;;
    "compose ps -a -q maze") echo "cid-$phase" ;;
    "inspect -f {{.State.Status}} {{.RestartCount}} cid-new") echo "${NEW_STATE:-running 0}" ;;
    "inspect -f {{.State.Status}} {{.RestartCount}} "*) echo "running 0" ;;
    "compose run --rm --no-deps -T --name "*" maze python -m maze.backup --help")
        if [ "${BACK_EXACT:-1}" = 1 ]; then echo "usage: backup.py [--restore ARCHIVE] [--keep-archive-accounts] [--exact]"
        else echo "usage: backup.py [--restore ARCHIVE] [--keep-archive-accounts]"; fi ;;
    *"maze.backup --help"*)
        if [ "${NEW_BACKUP:-1}" = 1 ]; then echo "usage: backup.py [-h] [--keep KEEP] [--suffix SUFFIX] [--restore ARCHIVE]"
        else echo "usage: backup.py [-h] [--keep KEEP] [--restore ARCHIVE]"; fi ;;
    "compose exec -T maze python -m maze.backup "*)
        [ "${BACKUP_FAIL:-0}" = 1 ] && { echo "Sauvegarde refusée : Espace disque insuffisant" >&2; exit 1; }
        echo "/data/backups/maze-20261014-101500-maj.tar.gz" ;;
    "compose ps -q maze") [ "$phase" = old ] && echo cid-ancien ;;
    "inspect -f {{.Image}} cid-ancien") echo sha256:ancienne ;;
    "compose build --pull") [ "${BUILD_FAIL:-0}" = 1 ] && { echo "build : erreur" >&2; exit 1; } ;;
    "compose up -d --remove-orphans")
        # Dossier monté par le compose : il doit exister (et appartenir à ops) avant le démarrage.
        [ -d status ] && stat -c '%u' status > "$FAKE/status-at-up"
        echo new > "$FAKE/phase"
        [ "${UP_FAIL:-0}" = 1 ] && exit 1 ;;
    "compose stop maze") echo down > "$FAKE/phase" ;;
    "compose run --rm --no-deps -T --name "*" maze python -c import os, sqlite3"*)
        [ "${SCHEMA_AFTER_FAIL:-0}" = 1 ] && { echo "sqlite3.DatabaseError: file is not a database" >&2; exit 1; }
        echo "${SCHEMA_AFTER:-24}" ;;
    "compose run --rm --no-deps -T --name "*" maze python -m maze.backup --restore "*)
        [ "${RESTORE_FAIL:-0}" = 1 ] && exit 1
        echo "État précédent sauvegardé dans /data/backups/maze-20261014-101700-avant-restauration.tar.gz" ;;
    "compose up -d --no-build --force-recreate --remove-orphans") echo back > "$FAKE/phase" ;;
    "compose logs"*) echo "(journaux du conteneur)" ;;
esac
exit 0
"""

# install simulé : refuse -g (ops qui n'est pas membre du groupe du conteneur), comme le vrai install sans droits.
INSTALL_NO_GROUP = r"""#!/usr/bin/env bash
for a in "$@"; do [ "$a" = "-g" ] && { echo "install: changement de groupe refusé" >&2; exit 1; }; done
exec "$REAL_INSTALL" "$@"
"""

GIT_ENV = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "Test",
           "GIT_COMMITTER_EMAIL": "t@example.org", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env={**os.environ, **GIT_ENV}, check=True,
                          capture_output=True, text=True).stdout.strip()


class DeployRepo:
    """Dépôt de production jetable : clone d'une origine locale ; `publish` ajoute un commit à l'origine."""

    def __init__(self, tmp: Path, scripts: Path | None = None):
        self.origin = tmp / "origin.git"
        self.work = tmp / "auteur"
        self.repo = tmp / "maze"
        git(tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(tmp, "clone", "-q", str(self.origin), str(self.work))
        self.write_scripts(scripts)
        (self.work / ".gitignore").write_text("secrets/\n.env\ncontrol/\n")
        self.commit("Version en service")
        git(tmp, "clone", "-q", str(self.origin), str(self.repo))
        (self.repo / "secrets").mkdir()
        (self.repo / "secrets" / "maze_password.txt").write_text("x")

    def write_scripts(self, scripts: Path | None) -> None:
        """Scripts de `scripts` (ceux du dépôt par défaut) ; status/ suivi par git comme dans le dépôt."""
        deploy = self.work / "deploy"
        deploy.mkdir(exist_ok=True)
        for name in ("update.sh", "maze-updater.sh"):
            shutil.copy((scripts or REPO / "deploy") / name, deploy / name)
            (deploy / name).chmod(0o755)
        if scripts is None:
            (self.work / "status").mkdir(exist_ok=True)
            shutil.copy(REPO / "status" / ".gitignore", self.work / "status" / ".gitignore")

    def commit(self, message: str) -> str:
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "--allow-empty", "-m", message)
        git(self.work, "push", "-q", "origin", "HEAD:main")
        return git(self.work, "rev-parse", "--short", "HEAD")

    def publish(self, message: str = "Nouvelle version", scripts: Path | None = None) -> str:
        if scripts is not None or not (self.work / "status").exists():
            self.write_scripts(scripts)
        (self.work / "CHANGELOG").write_text(message)
        return self.commit(message)

    def head(self) -> str:
        return git(self.repo, "rev-parse", "--short", "HEAD")


class DeployTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        (self.bin / "docker").write_text(DOCKER)
        (self.bin / "docker").chmod(0o755)
        self.fake = self.tmp / "fake"
        self.fake.mkdir()
        self.calls = self.tmp / "calls.log"

    def env(self, **extra) -> dict[str, str]:
        # Lancés sous systemd (runner de GitHub Actions, service), les tests hériteraient d'INVOCATION_ID : update.sh
        # se croirait lancé par maze-updater et lirait le TimeoutStartSec d'une unité absente (90 s par défaut).
        inherited = {k: v for k, v in os.environ.items() if k not in ("INVOCATION_ID", "UPDATE_BUDGET")}
        return {**inherited, **GIT_ENV, "PATH": f"{self.bin}:{os.environ['PATH']}", "CALLS": str(self.calls),
                "FAKE": str(self.fake), "MIN_FREE_MB": "0", "HEALTH_INTERVAL": "0", "HEALTH_TIMEOUT": "0", **extra}

    def calls_of(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def phase(self) -> str:
        return (self.fake / "phase").read_text().strip() if (self.fake / "phase").exists() else "old"


class UpdateScriptTest(DeployTestCase):
    def setUp(self):
        super().setUp()
        self.d = DeployRepo(self.tmp)
        self.old = self.d.head()

    def run_update(self, **env) -> subprocess.CompletedProcess:
        env = {"RUNNING_VERSION": self.old, **env}
        return subprocess.run(["bash", str(self.d.repo / "deploy" / "update.sh")], cwd=self.d.repo,
                              env=self.env(**env), capture_output=True, text=True, timeout=120)

    def test_backup_before_update(self):
        """Audit M8 : archive « -maj » à rotation séparée si le Maze en service le permet, sinon comme avant."""
        out = self.run_update(FORCE="1")
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertIn("compose exec -T maze python -m maze.backup --suffix maj", self.calls_of())
        self.calls.unlink()
        (self.fake / "phase").unlink()
        out = self.run_update(FORCE="1", NEW_BACKUP="0")      # l'ancienne version (sans --suffix) est en service
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertIn("compose exec -T maze python -m maze.backup --keep 14", self.calls_of())
        self.assertFalse([c for c in self.calls_of() if "--suffix maj" in c])

    def test_control_dir_group(self):
        """Audit X2-M2 : control/ recréé avec le groupe du conteneur (MAZE_GID, 1000 par défaut), pas celui d'ops."""
        gid = 4243 if os.geteuid() == 0 else os.getgid()   # root : un groupe différent du sien
        out = self.run_update(FORCE="1", MAZE_GID=str(gid))
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        control = self.d.repo / "control"
        self.assertEqual((control.stat().st_gid, control.stat().st_mode & 0o7777), (gid, 0o2775))
        # Dossier existant : laissé tel quel.
        control.chmod(0o2770)
        (self.fake / "phase").unlink()
        self.assertEqual(self.run_update(FORCE="1", MAZE_GID=str(gid)).returncode, 0)
        self.assertEqual(control.stat().st_mode & 0o7777, 0o2770)
        # Groupe impossible à donner : dossier créé quand même, mise à jour poursuivie, consigne affichée.
        control.rmdir()
        (self.fake / "phase").unlink()
        (self.bin / "install").write_text(INSTALL_NO_GROUP)
        (self.bin / "install").chmod(0o755)
        out = self.run_update(FORCE="1", MAZE_GID="4242", REAL_INSTALL=shutil.which("install"))
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertTrue(control.is_dir())
        self.assertIn("sudo chgrp 4242", out.stderr)
        self.assertIn("compose up -d --remove-orphans", self.calls_of())

    def test_default_group_matches_dockerfile(self):
        script = (REPO / "deploy" / "update.sh").read_text()
        self.assertIn('install -d -m 2775 -g "$gid" control', script)
        self.assertIn('gid="${MAZE_GID:-1000}"', script)
        self.assertIn("--gid 1000", (REPO / "Dockerfile").read_text())

    def test_existing_control_with_wrong_group(self):
        """Contre-revue (X2-M2) : control/ existant avec un autre groupe que celui du conteneur : signalé, avec la
        commande chgrp ; dossier laissé tel quel, mise à jour poursuivie."""
        control = self.d.repo / "control"
        control.mkdir()
        control.chmod(0o2775)
        expected = os.getgid() + 4300            # groupe attendu différent de celui du dossier
        out = self.run_update(FORCE="1", MAZE_GID=str(expected))
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertIn(f"sudo chgrp {expected} {control}", out.stderr)
        self.assertEqual(control.stat().st_gid, os.getgid())
        self.assertIn("compose up -d --remove-orphans", self.calls_of())
        # Bon groupe : rien à signaler.
        (self.fake / "phase").unlink()
        out = self.run_update(FORCE="1", MAZE_GID=str(os.getgid()))
        self.assertNotIn("chgrp", out.stderr)

    def test_update_to_new_version(self):
        new = self.d.publish()
        out = self.run_update()
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertEqual(self.d.head(), new)
        calls = self.calls_of()
        # Image en service gardée pour le retour arrière, avant la construction (audit M20).
        self.assertLess(calls.index("image tag sha256:ancienne maze:previous"), calls.index("compose build --pull"))
        # Sauvegarde juste avant le redémarrage : rien n'est écrit entre elles par l'ancienne version.
        self.assertLess(calls.index("compose build --pull"),
                        calls.index("compose exec -T maze python -m maze.backup --suffix maj"))
        self.assertLess(calls.index("compose exec -T maze python -m maze.backup --suffix maj"),
                        calls.index("compose up -d --remove-orphans"))
        self.assertIn(f"Maze est opérationnel (version {new})", out.stdout)
        self.assertTrue((self.d.repo / "status").is_dir())

    def test_up_to_date_compares_running_version(self):
        """Contre-revue du lot 1 : « Déjà à jour » compare la version en service (MAZE_VERSION) au commit."""
        out = self.run_update()
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertIn("Déjà à jour", out.stdout)
        self.assertNotIn("compose build --pull", self.calls_of())
        # Code déjà fusionné (sauvegarde refusée, build raté par une version d'avant ce correctif) mais ancienne
        # version en service : on reconstruit.
        new = self.d.publish()
        git(self.d.repo, "pull", "-q", "--ff-only")
        self.calls.unlink()
        out = self.run_update()
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertNotIn("Déjà à jour", out.stdout)
        self.assertIn(f"mais la version en service est {self.old}", out.stdout)
        self.assertIn("compose build --pull", self.calls_of())
        self.assertEqual(self.d.head(), new)

    def test_failure_before_restart_restores_code(self):
        """Build raté ou sauvegarde refusée : ancienne version toujours en service, code remis à sa version, et la
        relance reconstruit (pas de « Déjà à jour »)."""
        new = self.d.publish()
        for failure in ("BUILD_FAIL", "BACKUP_FAIL"):
            with self.subTest(failure):
                self.calls.unlink(missing_ok=True)
                out = self.run_update(**{failure: "1"})
                self.assertEqual(out.returncode, 1, out.stderr + out.stdout)
                self.assertIn(f"la version en service ({self.old}) est inchangée", out.stderr)
                self.assertEqual(self.d.head(), self.old)
                self.assertNotIn("compose up -d --remove-orphans", self.calls_of())
                self.assertEqual(self.phase(), "old")
        out = self.run_update()
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertEqual(self.d.head(), new)
        self.assertIn("compose up -d --remove-orphans", self.calls_of())

    def test_local_change_conflicting_with_update(self):
        """Contre-revue (M20) : modification locale d'un fichier que la mise à jour change (git merge --ff-only
        refusé) : arrêt avant la construction, ancienne version en service, code et modification locale intacts."""
        (self.d.work / "docker-compose.yml").write_text("services: {}\n")
        self.d.commit("compose")
        git(self.d.repo, "pull", "-q", "--ff-only")
        base = self.d.head()
        (self.d.work / "docker-compose.yml").write_text("services: {maze: {}}\n")
        self.d.publish()
        (self.d.repo / "docker-compose.yml").write_text("services: {local: {}}\n")   # réglage fait à la main
        out = self.run_update(RUNNING_VERSION=base)
        self.assertEqual(out.returncode, 1, out.stderr + out.stdout)
        self.assertIn(f"la version en service ({base}) est inchangée", out.stderr)
        self.assertEqual(self.d.head(), base)
        self.assertEqual((self.d.repo / "docker-compose.yml").read_text(), "services: {local: {}}\n")
        self.assertNotIn("compose build --pull", self.calls_of())
        self.assertEqual(self.phase(), "old")

    def test_rollback_when_new_version_does_not_answer(self):
        """Audit M20 : la nouvelle version ne répond pas : image précédente relancée, code au commit d'avant ;
        base non migrée : pas de restauration."""
        self.d.publish()
        out = self.run_update(NEW_HEALTHY="0")
        self.assertEqual(out.returncode, 3, out.stderr + out.stdout)
        calls = self.calls_of()
        tail = calls[calls.index("compose up -d --remove-orphans"):]
        self.assertEqual([c for c in tail if not c.startswith(("compose exec", "compose logs", "compose run",
                                                               "compose ps", "inspect"))],
                         ["compose up -d --remove-orphans", "compose stop maze", "image tag maze:previous maze:latest",
                          "compose up -d --no-build --force-recreate --remove-orphans"])
        self.assertFalse([c for c in calls if "--restore" in c])
        self.assertEqual(self.d.head(), self.old)
        self.assertEqual(self.phase(), "back")
        self.assertIn(f"Version précédente ({self.old}) de nouveau en service", out.stdout)
        self.assertIn("Base non migrée", out.stdout)

    def test_rollback_restores_backup_when_database_was_migrated(self):
        """Audit M20 : la nouvelle version a migré la base (schéma 24 > 25) : l'ancien code ne la connaît pas, la
        sauvegarde « -maj » faite juste avant est restaurée, application arrêtée, avant de relancer l'ancienne."""
        self.d.publish()
        out = self.run_update(UP_FAIL="1", SCHEMA_AFTER="25")
        self.assertEqual(out.returncode, 3, out.stderr + out.stdout)
        calls = self.calls_of()
        restore = [c for c in calls if "--restore" in c]
        self.assertEqual(len(restore), 1)
        # Image précédente qui connaît --exact : base remise telle qu'archivée (ni sessions fermées, ni jetons
        # supprimés : la sauvegarde n'a que quelques minutes).
        self.assertTrue(restore[0].endswith(f"maze python -m maze.backup --restore {ARCHIVE} --exact"), restore[0])
        self.assertLess(calls.index("compose stop maze"), calls.index(restore[0]))
        self.assertLess(calls.index(restore[0]), calls.index("compose up -d --no-build --force-recreate --remove-orphans"))
        self.assertIn("schéma 24 > 25", out.stdout)
        self.assertEqual(self.d.head(), self.old)

    def test_rollback_without_exact(self):
        """Image précédente qui ne connaît pas --exact : restauration complète, comme avant."""
        self.d.publish()
        out = self.run_update(NEW_HEALTHY="0", SCHEMA_AFTER="25", BACK_EXACT="0")
        self.assertEqual(out.returncode, 3, out.stderr + out.stdout)
        restore = [c for c in self.calls_of() if "--restore" in c]
        self.assertTrue(restore[0].endswith(f"--restore {ARCHIVE}"), restore[0])

    def test_restore_failure_leaves_maze_stopped(self):
        """Contre-revue : base migrée par la nouvelle version et restauration en échec : l'ancien code n'est pas
        relancé sur une base qu'il ne connaît pas. Maze reste arrêté (code 5), marche à suivre affichée."""
        new = self.d.publish()
        out = self.run_update(NEW_HEALTHY="0", SCHEMA_AFTER="25", RESTORE_FAIL="1")
        self.assertEqual(out.returncode, 5, out.stderr + out.stdout)
        self.assertEqual(self.phase(), "down")
        self.assertNotIn("compose up -d --no-build --force-recreate --remove-orphans", self.calls_of())
        self.assertIn("ÉCHEC, INTERVENTION REQUISE : Maze est laissé ARRÊTÉ", out.stderr)
        self.assertIn(f"maze.backup --restore {ARCHIVE} --exact", out.stderr)
        self.assertIn("git reset --keep", out.stderr)
        self.assertIn(new, out.stderr)
        self.assertEqual(self.d.head(), self.old)

    def test_unreadable_schema_leaves_maze_stopped(self):
        """Contre-revue du lot 2 : version de la base illisible après l'échec de la nouvelle version, alors que celle
        d'avant était connue : la base a peut-être été migrée, l'ancien code n'est pas relancé dessus (code 5)."""
        self.d.publish()
        out = self.run_update(NEW_HEALTHY="0", SCHEMA_AFTER_FAIL="1")
        self.assertEqual(out.returncode, 5, out.stderr + out.stdout)
        self.assertEqual(self.phase(), "down")
        self.assertNotIn("compose up -d --no-build --force-recreate --remove-orphans", self.calls_of())
        self.assertFalse([c for c in self.calls_of() if "--restore" in c])
        self.assertIn("Version de la base illisible après la mise à jour (avant : schéma 24)", out.stderr)
        self.assertIn("La base est au schéma illisible (avant la mise à jour : 24)", out.stderr)
        self.assertIn(f"maze.backup --restore {ARCHIVE} --exact", out.stderr)
        self.assertEqual(self.d.head(), self.old)

    def test_restart_loop_fails_at_once(self):
        """Contre-revue : conteneur qui redémarre en boucle : échec sans attendre HEALTH_TIMEOUT."""
        self.d.publish()
        start = time.monotonic()
        out = self.run_update(NEW_HEALTHY="0", NEW_STATE="restarting 3", HEALTH_TIMEOUT="600")
        self.assertEqual(out.returncode, 3, out.stderr + out.stdout)
        self.assertLess(time.monotonic() - start, 30)
        self.assertIn("redémarre en boucle (état : restarting 3)", out.stderr)

    def test_migration_time_not_counted(self):
        """Contre-revue : une migration plus longue que HEALTH_TIMEOUT n'est pas prise pour une panne
        (fichier <data>/.migration-en-cours), mais reste bornée par MIGRATION_TIMEOUT."""
        self.d.publish()
        out = self.run_update(MIGRATING_CHECKS="8", HEALTH_TIMEOUT="1", HEALTH_INTERVAL="0.3")
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertIn("Migration de la base en cours", out.stdout)
        self.assertEqual(self.phase(), "new")
        (self.fake / "phase").unlink()
        (self.fake / "migration-checks").unlink()
        git(self.d.repo, "reset", "-q", "--hard", self.old)
        out = self.run_update(MIGRATING_CHECKS="1000", MIGRATION_TIMEOUT="1", HEALTH_TIMEOUT="1",
                              HEALTH_INTERVAL="0.2")
        self.assertEqual(out.returncode, 3, out.stderr + out.stdout)
        self.assertIn("Migration de la base toujours en cours après 1 s", out.stderr)

    def test_migration_wait_keeps_time_for_rollback_under_systemd(self):
        """Re-revue : sous systemd (INVOCATION_ID), l'attente d'une migration s'arrête assez tôt pour laisser
        ROLLBACK_RESERVE (600 s) au retour arrière avant le TimeoutStartSec de l'unité installée (lu avec
        systemctl) : sinon systemd tuerait le script au milieu de la restauration. Lancé à la main : pas de limite."""
        (self.bin / "systemctl").write_text('#!/bin/sh\n[ "$*" = "show -P TimeoutStartUSec maze-updater.service" ] '
                                            '&& echo "10min 1s"\n')
        (self.bin / "systemd-analyze").write_text('#!/bin/sh\n[ "$*" = "timespan 10min 1s" ] && '
                                                  'printf "Original: 10min 1s\\n      us: 601000000\\n"\n')
        for name in ("systemctl", "systemd-analyze"):
            (self.bin / name).chmod(0o755)
        self.d.publish()
        out = self.run_update(INVOCATION_ID="abc", MIGRATING_CHECKS="1000", HEALTH_TIMEOUT="1",
                              HEALTH_INTERVAL="0.2")
        self.assertEqual(out.returncode, 3, out.stderr + out.stdout)
        self.assertIn("le reste des 601 s accordés par systemd (TimeoutStartSec) est gardé pour le retour arrière",
                      out.stderr)
        # À la main (sans INVOCATION_ID) : seule MIGRATION_TIMEOUT borne l'attente.
        for f in ("phase", "migration-checks"):
            (self.fake / f).unlink(missing_ok=True)
        git(self.d.repo, "reset", "-q", "--hard", self.old)
        out = self.run_update(MIGRATING_CHECKS="12", HEALTH_TIMEOUT="1", HEALTH_INTERVAL="0.2")
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)

    def test_left_stopped_commands(self):
        """Re-revue : commandes du code 5 exactes : relance de la nouvelle version avec sa MAZE_VERSION (sinon
        « dev » affichée et version en service inconnue de update.sh), liste des archives."""
        new = self.d.publish()
        out = self.run_update(NEW_HEALTHY="0", SCHEMA_AFTER="25", RESTORE_FAIL="1")
        self.assertEqual(out.returncode, 5, out.stderr + out.stdout)
        full = git(self.d.repo, "rev-parse", new)
        self.assertIn(f"git reset --keep {full} && MAZE_VERSION={new} docker compose up -d --build", out.stderr)
        self.assertIn("docker compose run --rm --no-deps -T maze ls -l /data/backups", out.stderr)

    def test_health_timeout_default(self):
        script = (REPO / "deploy" / "update.sh").read_text()
        self.assertIn('timeout="${HEALTH_TIMEOUT:-180}"', script)

    def test_rollback_failures(self):
        self.d.publish()
        cases = [({"NEW_HEALTHY": "0", "BACK_HEALTHY": "0"}, "ne répond pas non plus"),
                 ({"NEW_HEALTHY": "0", "OLD_HEALTHY": "0"}, "pas de retour automatique possible")]
        for env, message in cases:
            with self.subTest(message):
                (self.fake / "phase").unlink(missing_ok=True)
                git(self.d.repo, "reset", "-q", "--hard", self.old)
                out = self.run_update(**env)
                self.assertEqual(out.returncode, 4, out.stderr + out.stdout)
                self.assertIn(message, out.stderr)

    def test_concurrent_updates_refused(self):
        """Contre-revue (M20) : une mise à jour déjà en cours (bouton pendant ./deploy/update.sh, ou l'inverse) : la
        seconde s'arrête aussitôt, sans toucher au code, aux images ni au conteneur."""
        import fcntl
        self.d.publish()
        with (self.d.repo / ".git" / "maze-update.lock").open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            out = self.run_update()
        self.assertEqual(out.returncode, 1, out.stderr + out.stdout)
        self.assertIn("Une autre mise à jour est déjà en cours", out.stderr)
        self.assertEqual(self.calls_of(), [])
        self.assertEqual(self.d.head(), self.old)
        # Verrou libéré : la mise à jour passe.
        out = self.run_update()
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertNotEqual(self.d.head(), self.old)

    def test_status_dir(self):
        """Audit H2 : status/ (suivi par git, donc à ops) existe avant le démarrage ; recréé s'il a disparu (sinon
        Docker le créerait en root, et le service ne pourrait plus y écrire)."""
        out = self.run_update(FORCE="1")
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertEqual((self.fake / "status-at-up").read_text().strip(), str(os.getuid()))
        shutil.rmtree(self.d.repo / "status")
        (self.fake / "phase").unlink()
        (self.fake / "status-at-up").unlink()
        self.assertEqual(self.run_update(FORCE="1").returncode, 0)
        self.assertEqual((self.fake / "status-at-up").read_text().strip(), str(os.getuid()))


STUB_UPDATE = """#!/usr/bin/env bash
echo "update.sh simulé ($*)"
exit "${STUB_CODE:-0}"
"""


class UpdaterScriptTest(DeployTestCase):
    """deploy/maze-updater.sh : demandes lues dans control/ sans suivre de lien, état publié dans status/."""

    def setUp(self):
        super().setUp()
        self.d = DeployRepo(self.tmp)
        self.repo = self.d.repo
        (self.repo / "deploy" / "update.sh").write_text(STUB_UPDATE)   # modification locale : update.sh simulé
        self.control = self.repo / "control"
        self.control.mkdir(mode=0o2775)
        self.status = self.repo / "status"

    def run_updater(self, script: Path | None = None, **env) -> subprocess.CompletedProcess:
        script = script or self.repo / "deploy" / "maze-updater.sh"
        return subprocess.run(["bash", str(script)], cwd=self.repo, env=self.env(**env),
                              capture_output=True, text=True, timeout=60)

    def info(self) -> dict[str, str]:
        out = {}
        for line in (self.status / "info").read_text().splitlines():
            key, _, value = line.partition("=")
            out[key] = value
        return out

    def request(self, action: str, who: str = "ranglier") -> None:
        (self.control / "request").write_text(f"{action}\n{who}\n2026-10-14T10:00:00Z\n")

    def test_symlink_attack_from_container(self):
        """Audit H2 (OPS-2, preuve de work-OPS et work-X2) : le conteneur pose des liens dans control/ vers des
        fichiers de l'hôte. L'ancien service écrivait à travers (control/info.tmp -> deploy/update.sh : update.sh
        remplacé par la charge, exécuté en ops à la mise à jour suivante). Le nouveau n'écrit plus dans control/
        et n'y suit aucun lien."""
        update_sh = self.repo / "deploy" / "update.sh"
        original = update_sh.read_text()
        secret = self.tmp / "secret-hote.txt"
        secret.write_text("update\nclé privée de l'hôte\n")
        payload = "#!/bin/sh\ntouch PWNED\n"
        (self.control / "info").write_text(payload)
        for name in ("info.tmp", "state.tmp", "commits.tsv.tmp"):
            (self.control / name).symlink_to("../deploy/update.sh")
        (self.control / ".lock").symlink_to(secret)
        (self.control / "fetch.err").symlink_to(secret)
        (self.control / "request").symlink_to(secret)    # demande qui est un lien : refusée, cible ni lue ni effacée
        out = self.run_updater()
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertEqual(update_sh.read_text(), original, "update.sh écrasé à travers un lien de control/")
        self.assertEqual(secret.read_text(), "update\nclé privée de l'hôte\n")
        self.assertFalse((self.repo / "PWNED").exists())
        self.assertFalse((self.control / "request").exists() or (self.control / "request").is_symlink())
        self.assertNotIn("update.sh simulé", (self.status / "update.log").read_text()
                         if (self.status / "update.log").exists() else "")
        self.assertIn("pas un fichier ordinaire", self.info()["message"])
        # Rien d'écrit dans control/ : les noms de l'ancien service y sont retirés (les liens eux-mêmes, sans les
        # suivre), le contenu de control/info n'est repris que pour les clés connues.
        self.assertEqual(list(self.control.iterdir()), [])
        self.assertNotIn("PWNED", (self.status / "info").read_text())
        # Une demande ordinaire est ensuite traitée, et l'état publié dans status/ seulement.
        self.request("check")
        out = self.run_updater()
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertEqual((self.status / "state").read_text().strip(), "idle")
        self.assertEqual(self.info()["check_message"], "Maze est à jour.")
        self.assertEqual(update_sh.read_text(), original)
        self.assertEqual(secret.read_text(), "update\nclé privée de l'hôte\n")

    def test_symlink_attack_succeeds_on_previous_script(self):
        """Contrôle du test précédent : le service d'avant le lot 2 se fait bien piéger (update.sh remplacé)."""
        update_sh = self.repo / "deploy" / "update.sh"
        (self.control / "info").write_text("#!/bin/sh\ntouch PWNED\n")
        (self.control / "info.tmp").symlink_to("../deploy/update.sh")
        shutil.copy(BEFORE_LOT2 / "maze-updater.sh", self.repo / "deploy" / "ancien-maze-updater.sh")
        self.run_updater(self.repo / "deploy" / "ancien-maze-updater.sh")
        self.assertIn("touch PWNED", update_sh.read_text())

    def test_request_not_a_regular_file(self):
        for kind in ("dossier", "fifo"):
            with self.subTest(kind):
                inbox = self.control / "request"
                if kind == "dossier":
                    inbox.mkdir()
                    (inbox / "x").write_text("update\n")
                else:
                    os.mkfifo(inbox)
                out = self.run_updater()
                self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
                self.assertFalse(inbox.exists())
                self.assertIn("pas un fichier ordinaire", self.info()["message"])
                self.assertFalse((self.status / "update.log").exists())

    def test_update_outcomes(self):
        """Résultat de update.sh (codes 0, 1, 3, 4) : état et message clairs pour l'interface."""
        cases = [("0", "success", "Mise à jour terminée"),
                 ("1", "failed", "la version précédente est toujours en service"),
                 ("3", "rolledback", "retour automatique à la version précédente"),
                 ("4", "failed", "Intervention nécessaire"),
                 ("5", "failed", "Échec, intervention requise")]
        for code, state, message in cases:
            with self.subTest(code):
                self.request("update", "ranglier")
                out = self.run_updater(STUB_CODE=code)
                self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
                self.assertEqual((self.status / "state").read_text().strip(), state)
                self.assertIn(message, self.info()["message"])
                # Aussi dans le journal du service (journalctl -u maze-updater) : seul lisible si Maze est arrêté.
                self.assertIn(f"Résultat ({state}) : {self.info()['message']}", out.stdout)
                self.assertIn("update.sh simulé (main)", (self.status / "update.log").read_text())
                self.assertFalse((self.control / "request").exists())
                self.assertEqual(list(self.control.iterdir()), [])
        # Une vérification garde le résultat de la dernière mise à jour.
        self.request("check")
        self.run_updater()
        self.assertEqual((self.status / "state").read_text().strip(), "failed")

    def test_unknown_request(self):
        self.request("$(touch PWNED)", "x" * 500)
        self.run_updater()
        self.assertEqual(self.info()["message"], "Demande inconnue ignorée.")
        self.assertFalse((self.repo / "PWNED").exists())
        self.assertFalse((self.status / "update.log").exists())

    def test_legacy_state_removed(self):
        """Fichiers d'état laissés dans control/ par l'ancien service : retirés une fois status/ écrit."""
        for name in ("state", "info", "commits.tsv", "update.log", ".lock"):
            (self.control / name).write_text("ancien\n")
        self.request("check")
        self.run_updater()
        self.assertEqual(list(self.control.iterdir()), [])
        self.assertTrue((self.status / "state").exists())


class MigrationMarkerTest(unittest.TestCase):
    """Contre-revue (audit M20) : <data>/.migration-en-cours présent pendant les migrations, que update.sh lit pour
    ne pas prendre une longue migration pour une panne ; retiré à la fin, même en cas d'échec ou d'arrêt brutal."""

    def test_marker(self):
        from maze import db
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        base = tmp / "maze.db"
        marker = tmp / db.MIGRATING_MARKER
        seen = []

        class Log:
            def info(self, *args):
                seen.append(marker.exists())
        saved = db.log
        db.log = Log()
        try:
            db.migrate(base)
        finally:
            db.log = saved
        self.assertTrue(seen and all(seen))
        self.assertFalse(marker.exists())
        marker.write_text("arrêt brutal\n")
        db.migrate(base)                       # rien à migrer : marque périmée retirée
        self.assertFalse(marker.exists())
        bad = tmp / "migrations"   # migrations du dépôt plus une suivante en échec (numérotation contiguë exigée)
        shutil.copytree(db.MIGRATIONS_DIR, bad)
        (bad / f"{len(db.migration_files()) + 1:03d}_casse.sql").write_text("CREATE TABLE;")
        saved_dir = db.MIGRATIONS_DIR
        db.MIGRATIONS_DIR = bad
        try:
            with self.assertRaises(sqlite3.Error):
                db.migrate(base)
        finally:
            db.MIGRATIONS_DIR = saved_dir
        self.assertFalse(marker.exists())


class TransitionTest(DeployTestCase):
    """Première mise à jour vers cette version, depuis la production d'avant le lot 2 : l'ancien maze-updater.sh
    (déjà lu par bash) lance l'ancien update.sh, dont « git merge » apporte les nouveaux scripts et le nouveau
    compose (qui monte status/ en lecture seule) ; l'ancien conteneur lit l'état dans control/ jusqu'au
    redémarrage. Aucune intervention manuelle : status/ existe (créé par git, donc par ops) avant le démarrage,
    l'interface lit l'état laissé dans control/ par l'ancien service, puis le nouveau service prend le relais."""

    def test_button_update_then_new_service(self):
        d = DeployRepo(self.tmp, scripts=BEFORE_LOT2)
        old = d.head()
        new = d.publish("Lot 2", scripts=None)
        control = d.repo / "control"
        control.mkdir(mode=0o2775)
        (control / "request").write_text("update\nranglier\n2026-10-14T10:00:00Z\n")
        env = self.env(RUNNING_VERSION=old, NEW_VERSION=new, NEW_BACKUP="0")
        out = subprocess.run(["bash", str(d.repo / "deploy" / "maze-updater.sh")], cwd=d.repo, env=env,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertEqual(d.head(), new)
        # Le nouveau compose monte ./status : le dossier existait au démarrage et appartient à ops.
        self.assertEqual((self.fake / "status-at-up").read_text().strip(), str(os.getuid()))
        # L'ancien service a écrit son résultat dans control/ : le nouveau conteneur l'y lit (repli de transition).
        self.assertEqual((control / "state").read_text().strip(), "success")
        from maze.admin import _updater_status
        up = _updater_status(control, d.repo / "status")
        self.assertTrue(up["installed"])
        self.assertEqual(up["state"], "success")
        self.assertIn("Mise à jour terminée", up["message"])
        # Demande suivante (bouton « Vérifier » du nouveau conteneur) : nouveau service, état dans status/,
        # anciens fichiers retirés de control/.
        (control / "request").write_text("check\nranglier\n2026-10-14T10:05:00Z\n")
        out = subprocess.run(["bash", str(d.repo / "deploy" / "maze-updater.sh")], cwd=d.repo, env=env,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertEqual(sorted(p.name for p in control.iterdir()), [])
        up = _updater_status(control, d.repo / "status")
        self.assertEqual(up["state"], "success")
        self.assertEqual(up["current"], new)
        # Mise à jour suivante, entièrement avec les nouveaux scripts : version en service comparée au commit.
        out = subprocess.run(["bash", str(d.repo / "deploy" / "update.sh")], cwd=d.repo, env=env,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertIn("Déjà à jour", out.stdout)

    def test_manual_update_sh(self):
        """Même transition lancée à la main (./deploy/update.sh, ancienne version sur le disque)."""
        d = DeployRepo(self.tmp, scripts=BEFORE_LOT2)
        old = d.head()
        new = d.publish("Lot 2", scripts=None)
        out = subprocess.run(["bash", str(d.repo / "deploy" / "update.sh")], cwd=d.repo,
                             env=self.env(RUNNING_VERSION=old, NEW_BACKUP="0"), capture_output=True, text=True,
                             timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
        self.assertEqual(d.head(), new)
        self.assertEqual((self.fake / "status-at-up").read_text().strip(), str(os.getuid()))
        self.assertTrue((d.repo / "status" / ".gitignore").exists())
        self.assertEqual(git(d.repo, "status", "--porcelain"), "")


class BuildDependenciesTest(unittest.TestCase):
    """Audit M21 : l'interface est construite avec npm ci dès que frontend/package-lock.json est dans le dépôt
    (Dockerfile) ; sans lui, npm install avec un avertissement (verrou pas encore produit). La commande RUN
    du Dockerfile est exécutée telle quelle avec un npm simulé."""

    def run_step(self, with_lock: bool) -> subprocess.CompletedProcess:
        lines = (REPO / "Dockerfile").read_text(encoding="utf-8").replace("\\\n", " ").splitlines()
        copy = lines.index("COPY frontend/package*.json ./")
        step = next(line for line in lines[copy + 1:] if line.startswith("RUN ")).removeprefix("RUN ")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "bin").mkdir()
        (tmp / "bin" / "npm").write_text('#!/bin/sh\necho "npm $*" >> "$NPM_CALLS"\n')
        (tmp / "bin" / "npm").chmod(0o755)
        (tmp / "package.json").write_text("{}")
        if with_lock:
            (tmp / "package-lock.json").write_text("{}")
        env = {**os.environ, "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}", "NPM_CALLS": str(tmp / "calls")}
        out = subprocess.run(["sh", "-c", step], cwd=tmp, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        out.calls = (tmp / "calls").read_text().splitlines()
        return out

    def test_dockerfile_uses_npm_ci_with_lockfile(self):
        out = self.run_step(with_lock=True)
        self.assertEqual(out.calls, ["npm ci --no-audit --no-fund"])
        self.assertEqual(out.stderr, "")
        out = self.run_step(with_lock=False)
        self.assertEqual(out.calls, ["npm install --no-audit --no-fund"])
        self.assertIn("package-lock.json absent", out.stderr)


if __name__ == "__main__":
    unittest.main()
