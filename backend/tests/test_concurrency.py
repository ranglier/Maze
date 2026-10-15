"""Concurrence et boucle d'événements (audit M15 = PERF-1, réserve de la contre-revue X3, DATA-15).

- db.transaction : BEGIN IMMEDIATE, un « vérifier puis écrire » exécuté dans deux fils reste atomique (X3,
  work-X3/j_tx_race.py : setup() créait deux administrateurs) ; write=False : instantané cohérent en lecture.
- Travail SQLite hors de la boucle d'événements (db.run_db, routes « threaded », file par salle du temps réel) :
  un verrou d'écriture tenu longtemps par une autre connexion (import, suppression d'une campagne, restauration ;
  work-X3/g_lock_freeze.py, work-PERF/freeze.py) ne fige plus /api/health ni les lectures des autres MJ ;
  l'enregistrement attend le verrou dans un fil, puis 503 « réessayez » au-delà de l'attente au lieu d'une 500.

Lancer : cd backend && python -m unittest -q tests.test_concurrency
"""

from __future__ import annotations

import asyncio
import functools
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import anyio
import anyio.to_thread

from tests.test_api import ADMIN_PASSWORD, Server, recv, recv_until, seq_of

from maze import db
from maze.collab import in_loop


class TransactionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "maze.db"
        db.migrate(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_then_write_is_atomic_across_threads(self):
        """Repris de work-X3/j_tx_race.py : la vérification de setup() (aucun compte, donc créer l'administrateur)
        exécutée dans deux fils. Avant : la transaction ne s'ouvrait qu'à l'INSERT, les deux lisaient 0 et deux
        administrateurs étaient créés."""
        barrier = threading.Barrier(2)
        out: list[tuple[str, str]] = []

        def setup(name: str) -> None:
            try:
                with db.transaction(self.path) as conn:
                    n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                    try:   # laisse l'autre fil lire à son tour (il attend le verrou : barrière rompue)
                        barrier.wait(timeout=0.5)
                    except threading.BrokenBarrierError:
                        pass
                    time.sleep(0.05)
                    if n:
                        out.append((name, "409"))
                        return
                    db.insert(conn, "users", {"username": name, "display_name": name, "password_hash": "x",
                                              "role": "admin"})
                out.append((name, "créé"))
            except Exception as exc:  # noqa: BLE001
                out.append((name, f"{type(exc).__name__}: {exc}"))

        threads = [threading.Thread(target=setup, args=(n,)) for n in ("alice", "mallory")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(sorted(r for _, r in out), ["409", "créé"], out)
        with db.transaction(self.path, write=False) as conn:
            admins = [r[0] for r in conn.execute("SELECT username FROM users WHERE role = 'admin'")]
        self.assertEqual(len(admins), 1, admins)

    def test_read_transaction_is_a_snapshot(self):
        """DATA-15 : deux lectures d'un même bloc voient la même base, même si une écriture est validée entre les
        deux (avant : « 0 puis 1 », chaque SELECT dans sa propre transaction implicite)."""
        with db.transaction(self.path, write=False) as conn:
            first = conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
            with db.transaction(self.path) as other:   # autre connexion : elle n'attend pas le lecteur (WAL)
                other.execute("INSERT INTO campaigns (name) VALUES ('Entre-temps')")
            second = conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
            self.assertTrue(conn.in_transaction)
        self.assertEqual((first, second), (0, 0))
        with db.transaction(self.path, write=False) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0], 1)

    def test_write_transaction_rolls_back_on_error(self):
        with self.assertRaises(RuntimeError):
            with db.transaction(self.path) as conn:
                conn.execute("INSERT INTO campaigns (name) VALUES ('Annulée')")
                raise RuntimeError("échec")
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0], 0)


class InLoopTest(unittest.TestCase):
    """collab.in_loop (contre-revue) : appel direct depuis un fil étranger à anyio, renvoi dans la boucle depuis un
    fil de travail ; une erreur de la méthode elle-même remonte telle quelle, sans second appel (avant : reconnue au
    texte du message, une RuntimeError « … worker thread … » relançait la méthode hors de la boucle)."""

    class Fake:
        def __init__(self):
            self.calls: list[bool] = []

        @in_loop
        def touch(self, fail: bool = False) -> str:
            try:
                asyncio.get_running_loop()
                in_loop_now = True
            except RuntimeError:
                in_loop_now = False
            self.calls.append(in_loop_now)
            if fail:
                raise RuntimeError("Not running inside an AnyIO worker thread")
            return "ok"

    def test_foreign_thread_direct_call(self):
        fake = self.Fake()
        out: list[str] = []
        thread = threading.Thread(target=lambda: out.append(fake.touch()))
        thread.start()
        thread.join(5)
        self.assertEqual((out, fake.calls), (["ok"], [False]))

    def test_worker_thread_runs_in_loop_and_errors_propagate(self):
        fake = self.Fake()

        async def main():
            self.assertEqual(await anyio.to_thread.run_sync(fake.touch), "ok")
            with self.assertRaises(RuntimeError):
                await anyio.to_thread.run_sync(functools.partial(fake.touch, fail=True))
        anyio.run(main)
        self.assertEqual(fake.calls, [True, True])   # chaque fois dans la boucle, une seule fois


class LockHolder:
    """Autre connexion qui tient le verrou d'écriture `hold` secondes (comme un import dans le pool)."""

    def __init__(self, path: Path, hold: float):
        self.path, self.hold = path, hold
        self.taken = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("BEGIN IMMEDIATE")
        self.taken.set()
        time.sleep(self.hold)
        conn.rollback()
        conn.close()

    def __enter__(self):
        self.thread.start()
        self.taken.wait(5)
        return self

    def __exit__(self, *exc):
        self.thread.join(30)


class Probe:
    """Latence maximale de GET `path` (sonde toutes les 50 ms) pendant le bloc."""

    def __init__(self, client, path: str):
        self.client, self.path, self.worst, self.codes = client, path, 0.0, set()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop.is_set():
            start = time.perf_counter()
            self.codes.add(self.client.call("GET", self.path)[0])
            self.worst = max(self.worst, time.perf_counter() - start)
            time.sleep(0.05)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(30)


class LoopFreeTest(unittest.TestCase):
    """Avant (code du lot 3, mesuré) : verrou de 5 s, PATCH 200 en 4,6 s mais /api/health et la lecture d'un autre MJ
    figés 5 s ; verrou de 17 s, tout figé 15 s."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server()
        cls.db = Path(cls.s.tmp.name) / "maze.db"
        _, cls.camp = cls.s.call("POST", "/api/campaigns", {"name": "Verrou"})
        cls.other = cls.s.client()
        assert cls.other.login("admin", ADMIN_PASSWORD) == 200

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def patch_during_lock(self, hold: float) -> tuple[int, dict, float, Probe, Probe]:
        _, npc = self.s.call("POST", f"/api/campaigns/{self.camp['id']}/npcs", {"name": "Ombre"})
        with Probe(self.s.client(), "/api/health") as health, \
                Probe(self.other, f"/api/campaigns/{self.camp['id']}") as reader:
            with LockHolder(self.db, hold):
                start = time.perf_counter()
                code, body = self.s.call("PATCH", f"/api/npcs/{npc['id']}", {"title": "pendant le verrou"})
                took = time.perf_counter() - start
            time.sleep(0.2)
        return code, body, took, health, reader

    def test_long_lock_freezes_nothing(self):
        code, body, took, health, reader = self.patch_during_lock(2.0)
        self.assertEqual(code, 200, body)
        self.assertEqual(body["title"], "pendant le verrou")
        self.assertGreater(took, 1.5)              # l'écriture a bien attendu le verrou, dans un fil
        self.assertLess(health.worst, 0.5, "/api/health figé")
        self.assertLess(reader.worst, 0.5, "lecture d'un autre MJ figée")
        self.assertEqual(reader.codes, {200})

    def test_lock_longer_than_wait_gives_503(self):
        with mock.patch.object(db, "BUSY_TIMEOUT", 0.5):
            code, body, took, health, reader = self.patch_during_lock(2.0)
        self.assertEqual(code, 503, body)
        self.assertIn("réessayez", body["detail"])
        self.assertLess(took, 1.5)
        self.assertLess(health.worst, 0.5)
        self.assertEqual(reader.codes, {200})

    def test_reads_fast_while_write_threads_saturated(self):
        """Contre-revue : 20 enregistrements bloqués sur un verrou occupent tous les fils d'écriture (5) ; les lectures
        ont leurs propres fils et restent immédiates (avant, un seul pool de 16 : la lecture attendait le verrou)."""
        _, npc = self.s.call("POST", f"/api/campaigns/{self.camp['id']}/npcs", {"name": "File"})
        codes: list[int] = []

        def save(i: int) -> None:
            codes.append(self.s.call("PATCH", f"/api/npcs/{npc['id']}", {"title": f"essai {i}"})[0])
        writers = [threading.Thread(target=save, args=(i,)) for i in range(20)]
        with LockHolder(self.db, 2.5):
            for t in writers:
                t.start()
            # Tous les fils d'écriture pris par des enregistrements qui attendent le verrou (au lieu d'une attente
            # fixe, audit QUAL-9).
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and max(
                    p[True].borrowed_tokens for p in list(db._limiters.values())) < db.DB_WRITE_THREADS:
                time.sleep(0.02)
            worst = 0.0
            for _ in range(5):
                start = time.perf_counter()
                self.assertEqual(self.other.call("GET", f"/api/campaigns/{self.camp['id']}")[0], 200)
                self.assertEqual(self.other.call("GET", f"/api/npcs/{npc['id']}")[0], 200)
                worst = max(worst, time.perf_counter() - start)
        for t in writers:
            t.join(30)
        self.assertLess(worst, 0.5, "lecture bloquée derrière les écritures")
        self.assertEqual(codes, [200] * 20)

    def test_thread_pools_configurable(self):
        with mock.patch.dict("os.environ", {"MAZE_DB_WRITE_THREADS": "2", "MAZE_DB_READ_THREADS": "abc"}):
            self.assertEqual(db._threads("MAZE_DB_WRITE_THREADS", db.DB_WRITE_THREADS), 2)
            with self.assertLogs("maze", "WARNING"):
                self.assertEqual(db._threads("MAZE_DB_READ_THREADS", db.DB_READ_THREADS), db.DB_READ_THREADS)

    def test_live_edits_wait_in_order_without_freezing(self):
        """Temps réel : les trames reçues pendant le verrou attendent dans la file de la salle, sans figer la boucle ;
        ensuite numéros, accusés de réception et relais dans l'ordre d'arrivée."""
        _, doc = self.s.call("POST", f"/api/campaigns/{self.camp['id']}/documents", {"title": "File"})
        a = self.s.admin.live(doc["id"], 501)
        b = self.other.live(doc["id"], 502)
        try:
            recv_until(a, "welcome")
            recv_until(b, "welcome")
            recv_until(a, "peers")
            with Probe(self.s.client(), "/api/health") as health:
                with LockHolder(self.db, 1.5):
                    a.send(bytes([0]) + b"un")
                    b.send(bytes([0]) + b"deux")
                    a.send(bytes([1]) + b"curseur")
                    time.sleep(0.3)
                    self.assertEqual(self.s.call("GET", f"/api/documents/{doc['id']}")[0], 200)
                acks_a = [recv(a, 5)]
                relayed_b = recv(b, 5)
                ack_b = recv(b, 5)
                relayed_a = recv(a, 5)
                cursor = recv(b, 5)
            self.assertLess(health.worst, 0.5, "/api/health figé")
            self.assertEqual(acks_a[0][0], 3)
            first = seq_of(acks_a[0])
            self.assertEqual((relayed_b[0], seq_of(relayed_b), relayed_b[9:]), (0, first, b"un"))
            self.assertEqual((ack_b[0], seq_of(ack_b)), (3, first + 1))
            self.assertEqual((relayed_a[0], seq_of(relayed_a), relayed_a[9:]), (0, first + 1, b"deux"))
            self.assertEqual(cursor, bytes([1]) + b"curseur")
            with sqlite3.connect(self.db) as conn:
                rows = conn.execute("SELECT id, data FROM document_updates WHERE document_id = ? ORDER BY id",
                                    (doc["id"],)).fetchall()
            self.assertEqual(rows, [(first, b"un"), (first + 1, b"deux")])
        finally:
            a.close()
            b.close()

    def test_foundry_reads_do_not_wait_for_the_write_lock(self):
        """Contre-revue : le contrôle du jeton Foundry (chaque appel du module, une requête par image) prenait le
        verrou d'écriture (BEGIN IMMEDIATE) pour une simple lecture : pendant un import, une synchronisation de
        Foundry attendait, et ses requêtes occupaient les fils réservés à SQLite au détriment des lectures des MJ."""
        _, token = self.s.call("POST", "/api/admin/foundry/tokens", {"name": "Monde", "campaigns": [self.camp["id"]]})
        foundry = self.s.client()
        auth = {"Authorization": f"Bearer {token['token']}"}
        self.assertEqual(foundry.call("GET", "/api/foundry/v1/campaigns", headers=auth)[0], 200)   # last_used écrit
        with LockHolder(self.db, 1.5):
            start = time.perf_counter()
            code, body = foundry.call("GET", "/api/foundry/v1/campaigns", headers=auth)
            took = time.perf_counter() - start
        self.assertEqual(code, 200, body)
        self.assertEqual([c["id"] for c in body["campaigns"]], [self.camp["id"]])
        self.assertLess(took, 0.5, "lecture de Foundry en attente du verrou d'écriture")

    def test_foundry_stale_token_does_not_wait_for_the_write_lock(self):
        """Re-revue : jeton dont la date de dernier usage a plus d'une minute (première requête de chaque
        synchronisation ; ici, jeton neuf) : chaque requête parallèle du module voulait l'écrire et attendait le verrou
        d'écriture, dans les fils des lectures (30 requêtes pendant un verrou de 4 s : lecture d'un autre MJ 2,9 s).
        La date est écrite au mieux, sans attendre ; la réponse ne dépend pas d'elle."""
        _, token = self.s.call("POST", "/api/admin/foundry/tokens", {"name": "Neuf", "campaigns": [self.camp["id"]]})
        foundry = self.s.client()
        auth = {"Authorization": f"Bearer {token['token']}"}
        with LockHolder(self.db, 1.5):
            start = time.perf_counter()
            code, body = foundry.call("GET", "/api/foundry/v1/campaigns", headers=auth)
            took = time.perf_counter() - start
        self.assertEqual(code, 200, body)
        self.assertLess(took, 1.0, "date de dernier usage écrite en attendant le verrou")
        self.assertEqual(foundry.call("GET", "/api/foundry/v1/campaigns", headers=auth)[0], 200)   # écrite ensuite
        with closing(sqlite3.connect(self.db)) as conn:
            used = conn.execute("SELECT last_used_at FROM foundry_tokens WHERE name = ?", ("Neuf",)).fetchone()[0]
        self.assertIsNotNone(used)

    def test_frames_after_content_replaced_are_not_written(self):
        """Contre-revue : contenu remplacé par l'API (restauration d'une version, connecteur Claude) pendant qu'un MJ
        tape. La transaction de l'API est validée dans un fil, puis la salle est fermée dans la boucle : entre les
        deux, une trame qui attendait le verrou était écrite dans le journal tout juste vidé (une modification de
        l'ancien document Yjs, sans initialiseur pour la nouvelle époque), et le HTML du meneur écrasait le contenu
        remplacé. Fenêtre élargie ici en retardant la fermeture de la salle."""
        from websockets.exceptions import ConnectionClosed
        _, doc = self.s.call("POST", f"/api/campaigns/{self.camp['id']}/documents", {"title": "Remplacée"})
        hub = self.s.server.config.app.state.hub
        original = hub.close_documents
        committed = threading.Event()

        def slow_close(doc_ids, reason):
            committed.set()
            time.sleep(0.8)
            original(doc_ids, reason)

        a = self.s.admin.live(doc["id"], 601)
        out: list = []
        try:
            recv_until(a, "welcome")
            a.send(bytes([0]) + b"avant")
            first = seq_of(recv(a, 5))
            with mock.patch.object(hub, "close_documents", slow_close):
                patch = threading.Thread(target=lambda: out.append(self.s.call(
                    "PATCH", f"/api/documents/{doc['id']}", {"content": "<p>Remplacé</p>"})))
                patch.start()
                self.assertTrue(committed.wait(5))
                a.send(bytes([0]) + b"ancienne epoque")
                a.send('{"t": "content", "html": "<p>Ancien état</p>", "upto": %d}' % first)
                patch.join(10)
            self.assertEqual(out[0][0], 200, out)
            with self.assertRaises(ConnectionClosed):   # le navigateur est renvoyé vers la nouvelle époque
                for _ in range(20):
                    recv(a, 2)
        finally:
            a.close()
        with closing(sqlite3.connect(self.db)) as conn:
            rows = conn.execute("SELECT data FROM document_updates WHERE document_id = ?", (doc["id"],)).fetchall()
            content = conn.execute("SELECT content FROM documents WHERE id = ?", (doc["id"],)).fetchone()[0]
        self.assertEqual(rows, [])
        self.assertEqual(content, "<p>Remplacé</p>")


class SlowWorkPoolTest(unittest.TestCase):
    """Contre-revue du lot 4 : scrypt (création ou modification d'un compte) et la sauvegarde manuelle ne tiennent
    plus un fil d'écriture de db.run_db (5 par défaut), dont ils n'ont pas besoin : pool commun d'anyio."""

    def test_scrypt_and_backup_outside_write_threads(self):
        from maze import auth, backup
        s = Server()
        self.addCleanup(s.stop)
        held: dict[str, int] = {}

        def watch(name, fn):
            def wrapper(*args, **kwargs):
                held[name] = max(p[True].borrowed_tokens for p in list(db._limiters.values()))
                return fn(*args, **kwargs)
            return wrapper
        with mock.patch.object(auth, "hash_password", watch("scrypt", auth.hash_password)), \
                mock.patch.object(backup, "backup", watch("sauvegarde", backup.backup)):
            code, user = s.call("POST", "/api/admin/users", {"username": "pool", "role": "mj",
                                                             "password": "mot-de-passe-long", "campaign_ids": []})
            self.assertEqual(code, 201, user)
            self.assertEqual(held.pop("scrypt"), 0)
            code, out = s.call("PATCH", f"/api/admin/users/{user['id']}", {"password": "autre-mot-de-passe"})
            self.assertEqual(code, 200, out)
            self.assertEqual(held.pop("scrypt"), 0)
            code, entry = s.call("POST", "/api/admin/backups")
            self.assertEqual(code, 201, entry)
            self.assertEqual(held.pop("sauvegarde"), 0)
        self.assertEqual(s.client().login("pool", "autre-mot-de-passe"), 200)


if __name__ == "__main__":
    unittest.main()
