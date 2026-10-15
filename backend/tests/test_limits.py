"""Entrées hostiles : taille des corps de requête (audit M19), essais de connexion (M13, M14), création du premier
compte (M18), import d'une campagne (M16, M17), taille des messages du temps réel.

Lancer : cd backend && python -m unittest -q tests.test_limits
"""

from __future__ import annotations

import asyncio
import http.client
import io
import json
import random
import sqlite3
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request
import zipfile
from contextlib import closing
from pathlib import Path

from PIL import Image
from tests.test_api import ADMIN_PASSWORD, Server, multipart, recv_until

from maze import auth, limits, throttle, transfer

REPO = Path(__file__).resolve().parents[2]
MIB = 1024 * 1024


def raw(base: str, method: str, path: str, body: bytes = b"", headers: dict | None = None,
        chunked: bool = False) -> tuple[int, bytes]:
    """Requête HTTP brute. Corps de plus de 16 Ko : seuls les en-têtes partent (Content-Length annoncé), le serveur
    doit répondre sans l'attendre. chunked : corps envoyé par morceaux sans être annoncé, d'un seul envoi (pour
    que le serveur l'ait reçu en entier quand il répond et ferme ; sinon la fermeture peut couper la réponse)."""
    host, port = base.removeprefix("http://").split(":")
    for attempt in range(3):
        conn = http.client.HTTPConnection(host, int(port), timeout=30)
        try:
            conn.putrequest(method, path)
            for k, v in (headers or {}).items():
                conn.putheader(k, v)
            if chunked:
                conn.putheader("Transfer-Encoding", "chunked")
                conn.endheaders()
                blocks = [body[i:i + 256 * 1024] for i in range(0, len(body), 256 * 1024)]
                conn.send(b"".join(f"{len(b):x}\r\n".encode() + b + b"\r\n" for b in blocks) + b"0\r\n\r\n")
            else:
                conn.putheader("Content-Length", str(len(body)))
                conn.endheaders()
                if len(body) <= 16 * 1024:
                    conn.send(body)
            resp = conn.getresponse()
            return resp.status, resp.read()
        except (BrokenPipeError, ConnectionResetError):
            if attempt == 2:
                raise
        finally:
            conn.close()
    raise AssertionError("inaccessible")


def zip_with(payload: bytes, name: str = "campaign.json", **info) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, payload)
    return buf.getvalue()


class BodyLimitTest(unittest.TestCase):
    """Audit M19 : corps coupés à la lecture, selon la route, réponse 413 ; pas d'écho de la valeur reçue (422)."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server({"MAZE_MAX_UPLOAD_MB": "1"})
        _, cls.camp = cls.s.call("POST", "/api/campaigns", {"name": "Limites"})

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def setUp(self):
        throttle.reset()

    def test_limit_table(self):
        lim = lambda path: limits.limit_for(path, 20, 4096, 4 * MIB)  # noqa: E731
        self.assertEqual(lim("/api/auth/login"), 64 * 1024)
        self.assertEqual(lim("/oauth/token"), 64 * 1024)
        self.assertEqual(lim("/api/campaigns"), 2 * MIB)
        self.assertEqual(lim("/api/campaigns/3/maps/copy"), 2 * MIB)
        self.assertEqual(lim("/api/documents/3"), 16 * MIB)
        self.assertEqual(lim("/api/documents/3/versions/9/restore"), 16 * MIB)
        self.assertEqual(lim("/api/npcs/3"), 16 * MIB)
        self.assertEqual(lim("/api/npcs/3/images"), 10 * 20 * MIB + MIB)
        self.assertEqual(lim("/api/admin/campaigns/3/library"), 20 * 20 * MIB + MIB)
        self.assertEqual(lim("/api/campaigns/3/maps"), 21 * MIB)
        self.assertEqual(lim("/api/campaigns/import"), 4096 * MIB)
        self.assertEqual(lim("/api/admin/backups/import"), 4096 * MIB)
        self.assertEqual(lim("/mcp"), 4 * MIB)
        from maze import media_admin
        self.assertEqual(media_admin.MAX_FILES, limits.LIBRARY_FILES)

    def test_public_routes_without_account(self):
        base = self.s.base
        big = json.dumps({"username": "x", "password": "y" * (100 * 1024)}).encode()
        code, body = raw(base, "POST", "/api/auth/login", big, {"Content-Type": "application/json"})
        self.assertEqual(code, 413)
        self.assertIn("64 Ko", json.loads(body)["detail"])
        just_over = json.dumps({"username": "x", "password": "y" * (64 * 1024)}).encode()   # 64 Ko + quelques octets
        code, _ = raw(base, "POST", "/api/auth/login", just_over, {"Content-Type": "application/json"}, chunked=True)
        self.assertEqual(code, 413)
        self.assertEqual(len(throttle.by_address), 0, "un corps refusé ne compte pas d'essai")
        code, _ = raw(base, "POST", "/oauth/token", b"a=" + b"b" * (70 * 1024),
                      {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(code, 413)

    def test_json_routes(self):
        s = self.s
        for chunked, size in ((False, 3 * MIB), (True, 2 * MIB + 1024)):
            with self.subTest(chunked=chunked):
                big = json.dumps({"name": "x" * size}).encode()
                code, _ = raw(s.base, "POST", "/api/campaigns", big,
                              {"Content-Type": "application/json", "Cookie": s.admin.cookie_header()}, chunked)
                self.assertEqual(code, 413)
        # Sans compte : refusé (401) sans lire le corps.
        code, _ = raw(s.base, "POST", "/api/campaigns", b"{}" + b" " * MIB, {"Content-Type": "application/json"})
        self.assertEqual(code, 401)
        # 422 sans la valeur reçue.
        code, out = s.call("POST", "/api/campaigns", {"name": "x" * 100_000})
        self.assertEqual(code, 422)
        self.assertNotIn("input", out["errors"][0])
        self.assertLess(len(json.dumps(out)), 1000)

    def test_long_text_and_uploads_still_accepted(self):
        s = self.s
        cid = self.camp["id"]
        _, doc = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Très longue fiche"})
        content = "<p>" + "é" * 1_900_000 + "</p>"          # 3,8 Mo de JSON : au-delà de la limite générale
        code, out = s.call("PATCH", f"/api/documents/{doc['id']}", {"content": content})
        self.assertEqual(code, 200, str(out)[:200])
        _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {"name": "Galerie"})
        files = []
        for i in range(3):   # bruit : PNG presque aussi gros que l'image brute (0,9 Mo, sous MAZE_MAX_UPLOAD_MB)
            buf = io.BytesIO()
            Image.frombytes("RGB", (540, 540), random.Random(i).randbytes(540 * 540 * 3)).save(buf, "PNG")
            files.append((f"{i}.png", buf.getvalue()))
        body, ctype = multipart(files)
        self.assertGreater(len(body), 2 * MIB)
        code, out = s.call("POST", f"/api/npcs/{npc['id']}/images", raw=body, content_type=ctype)
        self.assertEqual(code, 201, out)
        # Au-delà de 10 images de MAZE_MAX_UPLOAD_MB (1 Mo ici) : 413 avant lecture.
        code, _ = raw(s.base, "POST", f"/api/npcs/{npc['id']}/images", b"x" * (12 * MIB),
                      {"Content-Type": ctype, "Cookie": s.admin.cookie_header()})
        self.assertEqual(code, 413)
        # Import : limite propre (MAZE_MAX_IMPORT_MB), pas celle du JSON.
        code, out = s.call("POST", "/api/campaigns/import", raw=b"x" * (3 * MIB), content_type="application/zip")
        self.assertEqual(code, 400, out)

    def test_websocket_message_size(self):
        """Temps réel : message de plus de 16 Mio refusé par uvicorn (fermeture 1009), comme dans le Dockerfile."""
        self.assertIn('"--ws-max-size", "16777216"', (REPO / "Dockerfile").read_text())
        from maze import collab
        self.assertLess(collab.MAX_SNAPSHOT, 16 * MIB)
        s = self.s
        _, doc = s.call("POST", f"/api/campaigns/{self.camp['id']}/documents", {"title": "Temps réel"})
        ws = s.admin.live(doc["id"], 7)
        recv_until(ws, "welcome")
        from websockets.exceptions import ConnectionClosed
        with self.assertRaises(ConnectionClosed) as closed:
            ws.send(bytes([1]) + b"\0" * (16 * MIB + 1))
            while True:
                ws.recv(timeout=5)
        # Trame de fermeture 1009 (« message trop grand ») ; elle peut se perdre si le serveur ferme pendant l'envoi.
        if closed.exception.rcvd is not None:
            self.assertEqual(closed.exception.rcvd.code, 1009)


class ThrottleTest(unittest.TestCase):
    """Audits M13 et M14 : adresse non usurpable, délai par compte sans blocage, mémoire bornée."""

    def setUp(self):
        throttle.reset()
        self.addCleanup(throttle.reset)

    @staticmethod
    def parallel_logins(s: Server, forwarded: list[str]) -> list[int]:
        """Essais ratés simultanés (comptes différents), un en-tête X-Forwarded-For chacun ; codes triés."""
        import concurrent.futures

        def attempt(i: int) -> int:
            c = s.client()
            c.opener.addheaders = [("X-Forwarded-For", forwarded[i])]
            return c.login(f"inconnu{i}", "mauvais-mot-de-passe")
        with concurrent.futures.ThreadPoolExecutor(len(forwarded)) as pool:
            return sorted(pool.map(attempt, range(len(forwarded))))

    def test_attempts_bounded(self):
        a = throttle.Attempts(window=600, max_keys=100)
        for i in range(1000):
            a.add(f"10.0.{i // 256}.{i % 256}", now=float(i))
        self.assertEqual(len(a), 100)
        self.assertEqual(a.count("10.0.3.231", now=1000.0), 1)        # le plus récent est gardé
        self.assertEqual(a.count("10.0.0.0", now=1000.0), 0)          # les plus anciens sont oubliés
        self.assertEqual(a.count("10.0.3.231", now=2000.0), 0)        # sorti de la fenêtre : clé retirée
        self.assertEqual(len(a), 0)
        for _ in range(100):
            a.add("x", now=1.0)
        self.assertEqual(a.count("x", now=2.0), throttle.MAX_PER_KEY)

    def fake_time(self, advance: bool) -> list[float]:
        """Horloge simulée de throttle (départ à 1000 s) ; attentes relevées dans la liste renvoyée. advance=True :
        une attente fait avancer l'horloge (essais l'un après l'autre) ; False : elle rend la main aussitôt, l'horloge
        reste figée (essais envoyés au même instant, en parallèle)."""
        now = [1000.0]
        waits: list[float] = []

        async def sleep(d: float) -> None:
            waits.append(d)
            if advance:
                now[0] += d
            await asyncio.sleep(0)
        patches = [mock.patch.object(throttle, "clock", lambda: now[0]), mock.patch.object(throttle, "sleep", sleep)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.now = now
        return waits

    def test_account_delay_never_blocks(self):
        self.assertEqual([throttle.account_delay(n) for n in range(10)],
                         [0, 0, 0, 0.5, 1, 2, 4, 8, 10, 10])
        delays = self.fake_time(advance=True)

        async def scenario():
            for i in range(12):   # adresses différentes, essais l'un après l'autre : seule la règle par compte joue
                self.assertIsNone(await throttle.admit(f"203.0.113.{i}", "admin"))
        asyncio.run(scenario())
        self.assertEqual(delays, [0.5, 1, 2, 4, 8, 10, 10, 10, 10])

    def test_account_key_ignores_case_like_login(self):
        """Contre-revue : l'identifiant est comparé sans casse (COLLATE NOCASE) : « Admin », « ADMIN », « admin  »
        visent le même compte et partagent son délai (sinon chaque variante aurait ses essais gratuits)."""
        delays = self.fake_time(advance=True)

        async def scenario():
            for i, name in enumerate(["admin", "Admin", "ADMIN", " aDmIn ", "AdMiN"]):
                self.assertIsNone(await throttle.admit(f"203.0.113.{i}", name))
        asyncio.run(scenario())
        self.assertEqual(delays, [0.5, 1])
        throttle.succeeded("203.0.113.9", "ADMIN")
        self.assertEqual(throttle.by_account.count("admin"), 0)

    @staticmethod
    async def burst(count: int, username: str = "admin", first: int = 0, pool: int | None = None) -> list[bool]:
        """`count` essais lancés ensemble, comme des requêtes simultanées ; True = admis. Adresses toutes neuves, ou
        prises dans un lot de `pool` adresses."""
        def address(i: int) -> str:
            n = first + (i % pool if pool else i)
            return f"198.18.{n // 250}.{n % 250}"
        return [r is None for r in await asyncio.gather(*(throttle.admit(address(i), username)
                                                          for i in range(count)))]

    def test_parallel_attempts_take_account_slots(self):
        """Vérification finale D1 (M13) : des essais simultanés sur un compte n'attendent plus chacun leur délai en
        même temps (60 essais en 14 s au lieu de 600). Chacun prend le créneau suivant du compte ; un essai dont le
        créneau tomberait au-delà du délai maximal est refusé aussitôt (429), sans être compté, sauf l'essai unique hors
        file (R1) : 2 au plus en attente par compte, quel que soit le nombre d'adresses."""
        waits = self.fake_time(advance=False)

        async def scenario():
            admitted = await self.burst(60)
            # File : 3 essais sans délai, puis 0,5, 1, 2, 4 s d'écart (fin à 7,5 s) ; le suivant tomberait à 15,5 s :
            # hors file, deux essais uniques (délai du compte : 8 puis 10 s), puis refus pour tous les autres.
            self.assertEqual(admitted.count(True), 9)
            self.assertEqual(sorted(waits), [0.5, 1.5, 3.5, 7.5, 8, 10])
            self.assertEqual(throttle.by_account.count("admin"), 9, "les refus ne comptent pas d'échec")
            self.assertEqual(throttle.by_address.count("198.18.0.59"), 0)
            # Flot continu : 10 essais par seconde pendant 10 minutes, depuis des adresses toujours neuves (adresses
            # inventées, X-Forwarded-For) : une vérification par la file et deux hors file toutes les 10 s au plus.
            verified = 9
            for step in range(6000):
                self.now[0] += 0.1
                verified += (await self.burst(1, first=1000 + step)).count(True)
            return verified
        verified = asyncio.run(scenario())
        self.assertLessEqual(verified, 9 + 3 * 600 / throttle.MAX_DELAY + 3)   # au lieu de 6060
        self.assertGreaterEqual(verified, 9 + 3 * 600 / throttle.MAX_DELAY - 6)

    def test_single_attempt_outside_queue(self):
        """Vérification finale R1 : file du compte pleine, une adresse sans essai récent sur ce compte obtient un seul
        essai hors file, compté avant toute attente : des essais simultanés depuis cette adresse n'en obtiennent
        qu'un ; il compte comme un échec ; une adresse déjà passée par la file n'y a pas droit pendant 10 minutes."""
        waits = self.fake_time(advance=False)

        async def scenario():
            self.assertEqual((await self.burst(7)).count(True), 7)                  # file pleine
            self.assertEqual((await self.burst(10, first=500, pool=1)).count(True), 1)   # une adresse, 10 essais
            self.assertEqual(waits[-1], 8)                                          # délai du compte, hors file
            self.assertEqual(throttle.by_account.count(("admin", "198.18.2.0")), 1)
            self.assertEqual(throttle.by_account.count("admin"), 8, "compté comme un échec")
            self.assertEqual(await self.burst(1, first=3), [False], "adresse déjà passée par la file")
            self.assertEqual(await self.burst(1, first=600), [True])                # second essai unique en attente
            self.assertEqual(await self.burst(1, first=601), [False])               # 2 en attente au plus
            # Le premier essai unique a commencé sa vérification (8 s) : une place se libère (la file, libre à cet
            # instant, est d'abord reprise par une adresse déjà passée par elle).
            self.now[0] += 8
            self.assertEqual(await self.burst(1, first=4), [True])
            self.assertEqual(await self.burst(1, first=602), [True])
            self.assertEqual(await self.burst(1, first=603), [False])
            # Dix minutes après son essai, l'adresse y a de nouveau droit.
            self.now[0] += throttle.WINDOW
            await self.burst(7, first=700)
            self.assertEqual(await self.burst(1, first=500, pool=1), [True])
        asyncio.run(scenario())

    def test_refusals_say_how_long_to_wait(self):
        """Vérification finale R4 : refus pour l'adresse (minutes, jusqu'à la sortie de son plus ancien essai de la
        fenêtre) et refus pour créneau occupé (secondes) : messages distincts, Retry-After juste."""
        self.fake_time(advance=False)

        async def scenario():
            for i in range(throttle.MAX_PER_ADDRESS):
                self.assertIsNone(await throttle.admit("192.0.2.7", f"compte{i}"))
                self.now[0] += 30
            refused = await throttle.admit("192.0.2.7", "autre")
            self.assertEqual(refused.retry_after, throttle.WINDOW - 30 * throttle.MAX_PER_ADDRESS)   # 360 s
            self.assertEqual(refused.message, "Trop de tentatives depuis cette adresse. Réessayez dans 6 minutes.")
            self.assertEqual(refused.headers, {"Retry-After": "360"})
            await self.burst(20)    # 7 par la file, 2 hors file
            busy = await throttle.admit("192.0.2.8", "admin")
            # Premier essai unique vérifié dans 8 s : une place hors file se libère alors.
            self.assertEqual((busy.retry_after, busy.message),
                             (8, "Trop de tentatives en cours sur ce compte. Réessayez dans 8 secondes."))
            self.now[0] += 8
            self.assertIsNone(await throttle.admit("192.0.2.8", "admin"))
            # Adresse déjà passée par la file : pas d'essai unique, seul compte le prochain créneau de la file (pris
            # par l'essai d'avant pour dans 10 s : libre pour un essai suivant dans 10 s).
            late = await throttle.admit("198.18.0.0", "admin")
            self.assertEqual(late.retry_after, throttle.MAX_DELAY)
        asyncio.run(scenario())

    def test_flood_never_holds_a_known_address(self):
        """Règle de non-blocage, vérification finale D1 : un flot d'essais faux ne retient pas l'administrateur.
        Une adresse qui s'est déjà connectée à ce compte a ses propres créneaux : son essai passe au pire après son
        délai normal (0 tant qu'elle n'a pas raté 3 essais, 10 s au plus), quoi que fassent les autres adresses."""
        waits = self.fake_time(advance=False)
        throttle.succeeded("192.0.2.1", "admin")      # connexion habituelle de l'administrateur

        async def scenario():
            self.assertEqual((await self.burst(200)).count(True), 9)
            for step in range(300):                   # le flot continue pendant 30 s, 50 adresses neuves par seconde
                self.now[0] += 0.1
                await self.burst(5, first=1000 + 5 * step)
                if step % 50 == 0:                    # l'administrateur se connecte toutes les 5 s
                    waits.clear()
                    self.assertIsNone(await throttle.admit("192.0.2.1", "ADMIN"))
                    self.assertEqual(waits, [], "aucune attente pour l'adresse connue")
                    throttle.succeeded("192.0.2.1", "ADMIN")
                if step == 150:
                    # Un flot d'adresses toujours neuves occupe aussi les essais hors file : une adresse nouvelle
                    # (le même administrateur sur un autre appareil) est refusée...
                    self.assertEqual(await self.burst(1, first=9000), [False])
            # ... et admise au plus tard MAX_DELAY après la fin du flot, son essai vérifié dans les MAX_DELAY suivantes.
            self.now[0] += throttle.MAX_DELAY
            waits.clear()
            self.assertEqual(await self.burst(1, first=9001), [True])
            self.assertLessEqual(waits[0], throttle.MAX_DELAY)
            # Erreurs de frappe depuis l'adresse connue : son délai normal à elle, jamais au-delà de MAX_DELAY
            # (8 essais : la limite par adresse reste de 8 en 10 minutes).
            for _ in range(8):
                waits.clear()
                self.assertIsNone(await throttle.admit("192.0.2.1", "admin"))
                self.assertLessEqual(max(waits, default=0), throttle.MAX_DELAY)
                self.now[0] += max(waits, default=0) + 1
            self.assertEqual(throttle.by_account.count(("admin", "192.0.2.1")), 8)
        asyncio.run(scenario())

    def test_new_device_gets_a_chance_against_few_addresses(self):
        """Vérification finale R1 : un attaquant sur Internet avec 8 adresses occupe la file du compte en continu.
        L'administrateur, depuis un appareil jamais utilisé (ou avant toute connexion depuis son adresse), obtient
        son essai unique hors file : vérifié au plus MAX_DELAY après son arrivée."""
        waits = self.fake_time(advance=False)

        async def scenario():
            refused_new = 0
            for step in range(600):                  # 60 s, 8 adresses, 10 essais par seconde au total
                self.now[0] += 0.1
                await self.burst(1, first=step % 8)
                if step % 60 == 30:
                    waits.clear()
                    ok = await self.burst(1, first=5000 + step)   # appareil neuf de l'administrateur
                    refused_new += ok.count(False)
                    if ok == [True]:
                        self.assertLessEqual(max(waits, default=0), throttle.MAX_DELAY + 1e-6)
                    self.now[0] += 0.05
            return refused_new
        self.assertEqual(asyncio.run(scenario()), 0)

    def test_known_address_is_per_account_and_success_clears_its_lane(self):
        self.fake_time(advance=False)
        throttle.succeeded("192.0.2.1", "admin")

        async def scenario():
            await self.burst(20, username="lea")
            # 192.0.2.1 n'est connue que pour « admin » : pour « lea », elle passe par la file du compte et ses essais
            # hors file, tous pris.
            self.assertIsNotNone(await throttle.admit("192.0.2.1", "lea"))
            # Ses propres échecs sur « admin » ne touchent pas les créneaux communs du compte, et inversement.
            await self.burst(20)
            for _ in range(3):
                self.assertIsNone(await throttle.admit("192.0.2.1", "admin"))
            self.assertEqual(throttle.by_account.count(("admin", "192.0.2.1")), 3)
            self.assertEqual(throttle.by_account.count("admin"), 9)
            # Succès depuis l'adresse connue : ses compteurs à elle sont effacés, pas ceux des autres adresses
            # (le flot ne regagne pas ses essais gratuits à chaque connexion de l'administrateur).
            throttle.succeeded("192.0.2.1", "admin")
            self.assertEqual(throttle.by_account.count(("admin", "192.0.2.1")), 0)
            self.assertEqual(throttle.by_account.count("admin"), 9)
        asyncio.run(scenario())

    def test_known_addresses_bounded_and_forgotten(self):
        """Vérification finale R3 : 32 adresses connues par compte (les plus anciennes du compte oubliées d'abord),
        10 000 en tout, 30 jours."""
        known = throttle.Known(window=100, max_keys=10, per_account=4)
        for i in range(50):
            known.add(("admin", f"192.0.2.{i}"), now=float(i))
        self.assertEqual(len(known), 4)                                       # plafond du compte
        self.assertTrue(known.contains(("admin", "192.0.2.49"), now=60.0))
        self.assertFalse(known.contains(("admin", "192.0.2.45"), now=60.0))
        known.add(("lea", "192.0.2.1"), now=61.0)
        known.add(("admin", "192.0.2.50"), now=62.0)   # remplace la plus ancienne d'« admin », pas celle de « lea »
        self.assertTrue(known.contains(("lea", "192.0.2.1"), now=62.0))
        self.assertFalse(known.contains(("admin", "192.0.2.46"), now=62.0))
        self.assertTrue(known.contains(("admin", "192.0.2.47"), now=62.0))
        for i in range(20):                                                    # plafond global
            known.add((f"compte{i}", "192.0.2.1"), now=70.0 + i)
        self.assertEqual(len(known), 10)
        self.assertFalse(known.contains(("lea", "192.0.2.1"), now=90.0))
        self.assertFalse(known.contains(("compte19", "192.0.2.1"), now=200.0))   # plus de 100 s : oubliée
        self.assertEqual(len(known), 0)
        self.assertEqual(throttle.Known().per_account, throttle.KNOWN_PER_ACCOUNT)
        known.add(("admin", "192.0.2.1"), now=0.0)
        throttle.known.add(("admin", "192.0.2.1"))
        throttle.forget_account(" ADMIN ")
        self.assertFalse(throttle.known.contains(("admin", "192.0.2.1")))

    @staticmethod
    def login_raw(base: str, username: str, password: str, forwarded: str) -> tuple[int, str | None, str]:
        """Connexion sans cookie : (code, Retry-After, message)."""
        req = urllib.request.Request(base + "/api/auth/login", method="POST",
                                     data=json.dumps({"username": username, "password": password}).encode(),
                                     headers={"Content-Type": "application/json", "X-Forwarded-For": forwarded})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.headers.get("Retry-After"), json.loads(resp.read()).get("detail", "")
        except urllib.error.HTTPError as err:
            return err.code, err.headers.get("Retry-After"), json.loads(err.read()).get("detail", "")

    def test_parallel_flood_on_server(self):
        """Vérification finale D1 rejouée sur un vrai serveur : 40 essais faux simultanés, X-Forwarded-For différent
        à chacun (proxy de confiance), compte « admin ». Avant : tous vérifiés (401) en un seul délai. Après : un
        créneau à la fois et deux essais uniques hors file, les autres en 429 immédiat (message et Retry-After de
        R4) ; pendant ce temps, l'administrateur se connecte depuis son adresse habituelle sans attendre. Règle
        raccourcie pour le test : délai dès le 2e essai, 1 s au plus."""
        import concurrent.futures
        s = Server({"FORWARDED_ALLOW_IPS": "127.0.0.1"})
        patches = [mock.patch.object(throttle, "FREE_FAILURES", 1), mock.patch.object(throttle, "MAX_DELAY", 1.0)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        try:
            usual = s.client()
            usual.opener.addheaders = [("X-Forwarded-For", "192.0.2.1")]
            self.assertEqual(usual.login("admin", ADMIN_PASSWORD), 200)

            def attempt(i: int) -> tuple[int, str | None, str, float]:
                start = time.monotonic()
                return (*self.login_raw(s.base, "admin", "mauvais-mot-de-passe", f"198.18.0.{i}"),
                        time.monotonic() - start)
            start = time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(41) as pool:
                flood = [pool.submit(attempt, i) for i in range(40)]
                time.sleep(0.3)
                admin_start = time.monotonic()
                self.assertEqual(usual.login("admin", ADMIN_PASSWORD), 200)
                admin_time = time.monotonic() - admin_start
                results = [f.result() for f in flood]
            elapsed = time.monotonic() - start
            codes = sorted(r[0] for r in results)
            verified = codes.count(401)
            self.assertEqual(verified + codes.count(429), 40, codes)
            # File : 1 essai sans délai, puis 0,5 s ; hors file : 2 essais uniques ; un peu plus si les requêtes
            # arrivent étalées. Avant : 40.
            self.assertLessEqual(verified, 6, codes)
            refused = [r for r in results if r[0] == 429]
            self.assertLess(max(r[3] for r in refused), 1.0, "refus immédiat")
            for _, retry, message, _ in refused:
                self.assertEqual(message, "Trop de tentatives en cours sur ce compte. Réessayez dans 1 seconde.")
                self.assertEqual(retry, "1")
            self.assertLess(admin_time, 1.0 + 1.0, f"administrateur retenu {admin_time:.1f} s (vol {elapsed:.1f} s)")
        finally:
            s.stop()

    def test_known_addresses_survive_restart(self):
        """Vérification finale R1 et R3 : adresses connues gardées en base (migration 029), remises en mémoire au
        démarrage ; 32 par compte et 30 jours au plus, purgées à chaque connexion réussie et au démarrage ; supprimées
        avec le compte ; rien d'écrit par un échec ou une adresse refusée."""
        s = Server({"FORWARDED_ALLOW_IPS": "127.0.0.1"})
        try:
            def rows():
                with closing(sqlite3.connect(s.tmp.name + "/maze.db")) as conn:
                    return conn.execute("SELECT u.username, a.address, a.last_login_at FROM login_addresses AS a "
                                        "JOIN users AS u ON u.id = a.user_id ORDER BY a.address").fetchall()
            self.assertEqual([r[:2] for r in rows()], [("admin", "127.0.0.1")])   # connexion du serveur de test
            for i in range(throttle.KNOWN_PER_ACCOUNT + 3):
                self.assertEqual(self.login_raw(s.base, "admin", ADMIN_PASSWORD, f"10.1.0.{i}")[0], 200)
            self.assertEqual(self.login_raw(s.base, "admin", "faux", "10.2.0.1")[0], 401)
            kept = rows()
            self.assertEqual(len(kept), throttle.KNOWN_PER_ACCOUNT)
            self.assertNotIn("127.0.0.1", {r[1] for r in kept})     # plus anciennes du compte retirées
            self.assertNotIn("10.1.0.0", {r[1] for r in kept})
            self.assertIn("10.1.0.34", {r[1] for r in kept})
            self.assertNotIn("10.2.0.1", {r[1] for r in kept})
            with closing(sqlite3.connect(s.tmp.name + "/maze.db")) as conn, conn:
                conn.execute("UPDATE login_addresses SET last_login_at = '2020-01-01T00:00:00.000Z' "
                             "WHERE address = '10.1.0.3'")
                conn.execute("INSERT INTO users (username, display_name, password_hash, role) "
                             "VALUES ('lea', 'Léa', 'x', 'mj')")
                lea = conn.execute("SELECT id FROM users WHERE username = 'lea'").fetchone()[0]
                conn.execute("INSERT INTO login_addresses VALUES (?, '10.3.0.1', strftime('%Y-%m-%dT%H:%M:%fZ'))",
                             (lea,))
            # « Redémarrage » : mémoire vidée, puis rechargée depuis la base (purge comprise).
            throttle.reset()
            self.assertEqual(throttle.load_known(Path(s.tmp.name) / "maze.db"), 32)   # 31 d'admin, 1 de lea
            self.assertTrue(throttle.known.contains(("admin", "10.1.0.34")))
            self.assertTrue(throttle.known.contains(("lea", "10.3.0.1")))
            self.assertFalse(throttle.known.contains(("admin", "10.1.0.3")))
            self.assertNotIn("10.1.0.3", {r[1] for r in rows()})
            self.assertEqual(s.call("DELETE", f"/api/admin/users/{lea}")[0], 204)
            self.assertNotIn("10.3.0.1", {r[1] for r in rows()})     # ON DELETE CASCADE
            self.assertFalse(throttle.known.contains(("lea", "10.3.0.1")))
        finally:
            s.stop()

    def test_forwarded_for_from_untrusted_peer_is_ignored(self):
        """Preuve de work-SEC/run_rl.py : X-Forwarded-For changé à chaque essai. Le client (127.0.0.1) n'est pas
        dans FORWARDED_ALLOW_IPS : son en-tête est ignoré, la limite par adresse joue."""
        s = Server({"FORWARDED_ALLOW_IPS": "10.9.9.9"})
        try:
            codes = self.parallel_logins(s, [f"10.0.0.{i}" for i in range(10)])
            self.assertEqual(codes, [401] * 8 + [429] * 2)
        finally:
            s.stop()

    def test_trusted_proxy_appending_forwarded_for(self):
        """Preuve de work-PERF/xff.py : proxy de confiance (127.0.0.1) qui ajoute l'adresse réelle à droite
        (nginx $proxy_add_x_forwarded_for) ; l'adresse de gauche, inventée par le client, est ignorée."""
        s = Server({"FORWARDED_ALLOW_IPS": "127.0.0.1"})
        try:
            codes = self.parallel_logins(s, [f"198.51.100.{i}, 203.0.113.9" for i in range(10)])
            self.assertEqual(codes, [401] * 8 + [429] * 2)
            self.assertIn("203.0.113.9", throttle.by_address._keys)
            # Un autre client réel n'est pas bloqué.
            c = s.client()
            c.opener.addheaders = [("X-Forwarded-For", "203.0.113.10")]
            self.assertEqual(c.login("admin", ADMIN_PASSWORD), 200)
        finally:
            s.stop()

    def test_default_trusts_private_proxy_without_configuration(self):
        """Contre-revue (M13) : sans FORWARDED_ALLOW_IPS dans .env, la valeur par défaut (compose et Dockerfile)
        croit le reverse proxy là où il est réellement vu par uvicorn : Caddy d'une autre machine (son IP, gardée par
        la redirection de port de Docker), Caddy de la même machine (passerelle du réseau Docker, via docker-proxy),
        Caddy dans un réseau Docker ; jamais une adresse d'Internet. La mise à jour ne demande ainsi aucune
        intervention : clients distincts pour la limite des essais, adresses OAuth en https."""
        import re
        from uvicorn.middleware.proxy_headers import _TrustedHosts
        compose = re.search(r"FORWARDED_ALLOW_IPS: \$\{FORWARDED_ALLOW_IPS:-([^}]+)\}",
                            (REPO / "docker-compose.yml").read_text()).group(1)
        dockerfile = re.search(r"^ENV FORWARDED_ALLOW_IPS=(\S+)$", (REPO / "Dockerfile").read_text(), re.M).group(1)
        self.assertEqual(compose, dockerfile)
        trusted = _TrustedHosts(compose)
        for peer in ("10.0.0.20", "127.0.0.1", "172.17.0.1", "172.18.0.1", "192.168.1.20", "::1"):
            self.assertIn(peer, trusted)
        for peer in ("203.0.113.9", "8.8.8.8", "172.32.0.1", "100.64.0.1", "2001:db8::1"):
            self.assertNotIn(peer, trusted)
        s = Server({"FORWARDED_ALLOW_IPS": compose, "MAZE_PUBLIC_URL": None})
        try:
            meta = s.client()
            meta.opener.addheaders = [("X-Forwarded-Proto", "https"), ("X-Forwarded-For", "203.0.113.9")]
            code, out = meta.call("GET", "/.well-known/oauth-authorization-server")
            self.assertEqual(code, 200)
            self.assertTrue(out["issuer"].startswith("https://"), out["issuer"])
            self.assertIsNone(s.call("GET", "/api/admin/system")[1]["untrusted_proxy"])
            codes = self.parallel_logins(s, [f"198.51.100.{i}" for i in range(10)])
            self.assertEqual(codes, [401] * 10, "chaque client réel a sa propre limite")
        finally:
            s.stop()

    def test_invalid_body_not_counted_and_account_slowed_not_blocked(self):
        s = Server({"FORWARDED_ALLOW_IPS": "127.0.0.1"})
        try:
            for _ in range(50):
                code, _ = raw(s.base, "POST", "/api/auth/login", b"{", {"Content-Type": "application/json",
                                                                        "X-Forwarded-For": "198.51.100.1"})
                self.assertEqual(code, 400)
            self.assertEqual((len(throttle.by_address), len(throttle.by_account)), (0, 0))
            # Essais ratés sur « admin » depuis plusieurs adresses : la connexion de l'admin reste possible,
            # seulement retardée (règle raccourcie pour le test : délai dès le 2e essai).
            throttle.FREE_FAILURES, saved = 1, throttle.FREE_FAILURES
            try:
                for i in range(2):
                    c = s.client()
                    c.opener.addheaders = [("X-Forwarded-For", f"198.51.100.{10 + i}")]
                    self.assertEqual(c.login("admin", "mauvais-mot-de-passe"), 401)
                c = s.client()
                c.opener.addheaders = [("X-Forwarded-For", "192.0.2.1")]
                start = time.monotonic()
                self.assertEqual(c.login("admin", ADMIN_PASSWORD), 200)
                self.assertGreaterEqual(time.monotonic() - start, 1.0)
                self.assertNotIn("admin", throttle.by_account._keys)   # effacé par la connexion réussie
            finally:
                throttle.FREE_FAILURES = saved
        finally:
            s.stop()


class SetupTest(unittest.TestCase):
    def test_setup_refused_before_scrypt(self):
        """Audit M18 : un compte existe : 409 immédiat, sans calcul scrypt."""
        s = Server()
        calls = []
        original = auth.hash_password
        auth.hash_password = lambda password: calls.append(1) or original(password)
        try:
            c = s.client()
            for _ in range(20):
                code, _ = c.call("POST", "/api/auth/setup", {"username": "pirate", "password": "mot-de-passe-2"})
                self.assertEqual(code, 409)
            code, _ = c.call("POST", "/api/auth/setup", raw=b"{", content_type="application/json")
            self.assertEqual(code, 409)
            self.assertEqual(calls, [])
        finally:
            auth.hash_password = original
            s.stop()


class ImportLimitsTest(unittest.TestCase):
    """Audit M16 (bombe de décompression, preuves work-PERF/import_bomb.py et work-X3/e_import_bomb.py) et M17
    (coût cubique, preuve work-DATA/tests_data/test_import_docs_slow.py)."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def import_(self, archive: bytes):
        return self.s.call("POST", "/api/campaigns/import", raw=archive, content_type="application/zip")

    def test_limits_constants(self):
        self.assertEqual(transfer.MAX_JSON, 25 * MIB)   # la limite que l'audit permettait (contre-revue)

    def test_export_too_big_to_reimport_is_flagged(self):
        """Contre-revue (audit M16) : une campagne dont campaign.json dépasserait la limite d'import : l'export le
        signale (vérification préalable pour l'interface, en-tête X-Maze-Export-Warning pour les scripts)."""
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Volumineuse"})
        s.call("POST", f"/api/campaigns/{camp['id']}/npcs", {"name": "PNJ", "notes": "x" * 5000})
        code, check = s.call("GET", f"/api/campaigns/{camp['id']}/export/check")
        self.assertEqual((code, check["importable"], check["max_bytes"]), (200, True, transfer.MAX_JSON))
        self.assertGreater(check["json_bytes"], 5000)
        req = urllib.request.Request(f"{s.base}/api/campaigns/{camp['id']}/export",
                                     headers={"Cookie": s.admin.cookie_header()})
        with urllib.request.urlopen(req) as resp:
            self.assertIsNone(resp.headers.get("X-Maze-Export-Warning"))
        saved, transfer.MAX_JSON = transfer.MAX_JSON, 4000
        try:
            code, check = s.call("GET", f"/api/campaigns/{camp['id']}/export/check")
            self.assertFalse(check["importable"])
            self.assertIn("ne pourra pas être réimportée", check["message"])
            with urllib.request.urlopen(req) as resp:
                self.assertIn("ne pourra pas être réimportée", resp.headers.get("X-Maze-Export-Warning"))
                archive = resp.read()
            self.assertIn("trop volumineux", self.import_(archive)[1]["detail"])
        finally:
            transfer.MAX_JSON = saved
        other = s.client()
        self.assertEqual(other.call("GET", f"/api/campaigns/{camp['id']}/export/check")[0], 401)

    def test_export_size_estimated_in_sql(self):
        """Contre-revue du lot 2 : export/check estime la taille de campaign.json en SQL (json_object), sans faire le
        travail de l'export ; l'estimation reste à moins de 1 % de la taille réelle."""
        from unittest import mock
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Mesurée", "description": "Port « brumeux »\nsur deux lignes"})
        cid = camp["id"]
        _, guild = s.call("POST", f"/api/campaigns/{cid}/factions", {"name": "Guilde", "color": "#336699",
                                                                   "tags": ["port", "cité"]})
        for i in range(40):
            _, npc = s.call("POST", f"/api/campaigns/{cid}/npcs", {
                "name": f"Maëlis {i}", "summary": 'Elle dit : "non"\t\\ ' * 3, "notes": "<p>été</p>" * 50,
                "tags": ["été", f"n{i}"]})
            s.call("PATCH", f"/api/npcs/{npc['id']}", {"pos_x": i * 12.375, "pos_y": -3.5})
            s.call("POST", "/api/memberships", {"npc_id": npc["id"], "faction_id": guild["id"], "role": "Membre"})
        _, scen = s.call("POST", f"/api/campaigns/{cid}/documents", {"title": "Acte", "content": "<p>Ça 😀</p>" * 300})
        s.call("POST", f"/api/campaigns/{cid}/places", {"name": "Ys", "summary": "Ville\u2028basse"})
        actual = len(transfer._export_json(transfer._export_data(Path(s.tmp.name) / "maze.db", cid, "dev")))
        with mock.patch.object(transfer, "_export_data", side_effect=AssertionError("export complet")):
            code, check = s.call("GET", f"/api/campaigns/{cid}/export/check")
        self.assertEqual(code, 200, check)
        self.assertLess(abs(check["json_bytes"] - actual), actual / 100, (check["json_bytes"], actual))

    def test_compression_bomb_refused(self):
        payload = b'{"format":"maze-campaign","version":1,"campaign":{"name":"x"},"npcs":[' + b"{}," * (6 * MIB) + b"{}]}"
        archive = zip_with(payload)
        self.assertLess(len(archive), 100 * 1024)
        start = time.monotonic()
        code, out = self.import_(archive)
        self.assertEqual(code, 400, out)
        self.assertIn("anormalement compressé", out["detail"])
        self.assertLess(time.monotonic() - start, 5)

    def test_oversized_json_refused(self):
        filler = random.Random(1).randbytes(transfer.MAX_JSON // 2 + 10).hex()   # peu compressible
        payload = json.dumps({"format": "maze-campaign", "version": 1, "campaign": {"name": filler}}).encode()
        code, out = self.import_(zip_with(payload))
        self.assertEqual(code, 400)
        self.assertIn("trop volumineux", out["detail"])

    def test_declared_size_not_trusted(self):
        """Taille décompressée annoncée fausse (plus petite que la réalité) : lecture bornée, archive refusée
        proprement (400, pas 500)."""
        payload = json.dumps({"format": "maze-campaign", "version": 1, "campaign": {"name": "x" * 5000}}).encode()
        archive = bytearray(zip_with(payload))
        # Taille décompressée de l'en-tête local (offset 22) et du répertoire central (offset 24) : 100 octets.
        archive[22:26] = (100).to_bytes(4, "little")
        central = archive.rfind(b"PK\x01\x02")
        archive[central + 24:central + 28] = (100).to_bytes(4, "little")
        code, out = self.import_(bytes(archive))
        self.assertEqual(code, 400, out)

    def test_too_many_objects(self):
        rng = random.Random(2)
        rows = ",".join(f'{{"k{rng.randrange(10**6)}":{rng.randrange(10**9)}}}' for _ in range(transfer.MAX_OBJECTS + 1))
        payload = ('{"format":"maze-campaign","version":1,"campaign":{"name":"x"},"npcs":[' + rows + "]}").encode()
        code, out = self.import_(zip_with(payload))
        self.assertEqual(code, 400, out)
        self.assertIn("trop d'éléments", out["detail"])

    def test_reversed_page_chain_is_linear(self):
        """3000 pages en chaîne, rangées à l'envers dans l'export : l'ancienne boucle prenait 16 s pour 1200."""
        n = 3000
        # Après inversion : pages de la plus profonde à la scénario, fiche de campagne, puis les cas à ignorer.
        docs = [{"id": n, "parent_id": 999_999, "kind": "page", "title": "Orpheline"},
                 {"id": n + 1, "kind": "scenario", "title": "Sans parent"},
                 {"id": n + 2, "parent_id": n + 1, "kind": "page", "title": "Sous un orphelin"},
                 {"id": n + 3, "parent_id": n + 4, "kind": "page", "title": "Cycle A"},
                 {"id": n + 4, "parent_id": n + 3, "kind": "page", "title": "Cycle B"},
                 {"id": n + 5, "kind": "campaign", "title": "Seconde fiche de campagne"},
                 {"id": n + 6, "parent_id": n + 5, "kind": "scenario", "title": "Sous la seconde"}]
        docs += [{"id": 1, "kind": "campaign", "title": ""}, {"id": 2, "parent_id": 1, "kind": "scenario", "title": "S"}]
        docs += [{"id": i, "parent_id": i - 1, "kind": "page", "title": f"P{i}"} for i in range(3, n)]
        docs.reverse()
        payload = json.dumps({"format": "maze-campaign", "version": 1, "campaign": {"name": "Chaîne"},
                              "documents": docs}).encode()
        start = time.monotonic()
        code, camp = self.import_(zip_with(payload))
        elapsed = time.monotonic() - start
        self.assertEqual(code, 201, camp)
        self.assertLess(elapsed, 10)
        self.assertEqual(camp["document_count"], n - 1)
        _, tree = self.s.call("GET", f"/api/campaigns/{camp['id']}/documents")
        flat = tree if isinstance(tree, list) else tree.get("documents", [])
        titles = {d["title"] for d in flat}
        self.assertIn(f"P{n - 1}", titles)
        self.assertFalse(titles & {"Orpheline", "Sans parent", "Sous un orphelin", "Cycle A", "Cycle B",
                                   "Seconde fiche de campagne", "Sous la seconde"})
        by_id = {d["id"]: d for d in flat}
        deepest = next(d for d in flat if d["title"] == f"P{n - 1}")
        depth = 0
        while deepest.get("parent_id"):
            deepest = by_id[deepest["parent_id"]]
            depth += 1
        # Toutes les pages importées ; au-delà de 32 niveaux, rangées au dernier niveau admis (audit DATA-10).
        from maze.documents import MAX_DEPTH
        self.assertEqual(depth, MAX_DEPTH)


class InvalidInputTest(unittest.TestCase):
    """Audit PERF-14 : entrées invalides refusées en 4xx, jamais en 500 (journal pollué de traces, réponse
    incompréhensible) : JSON imbriqué au-delà de la limite de récursion, NaN et Infinity, archive au CRC faux."""

    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.stop()

    def test_invalid_inputs(self):
        s = self.s
        _, camp = s.call("POST", "/api/campaigns", {"name": "Entrées"})
        _, npc = s.call("POST", f"/api/campaigns/{camp['id']}/npcs", {"name": "N"})
        nested = b"[" * 20_000 + b"]" * 20_000   # 40 Ko : sous la limite des routes publiques (64 Ko)
        for method, path, client in [("POST", "/api/campaigns", s.admin),            # route threaded (parse_body)
                                     ("POST", "/api/auth/login", s.client()),         # route dans la boucle (parse)
                                     ("PATCH", f"/api/npcs/{npc['id']}", s.admin)]:
            code, body = client.call(method, path, raw=nested, content_type="application/json")
            self.assertEqual((code, body["detail"]), (400, "Corps de requête JSON invalide"), path)
        for constant in (b"NaN", b"Infinity", b"-Infinity"):
            code, body = s.call("PATCH", f"/api/npcs/{npc['id']}", raw=b'{"pos_x": ' + constant + b"}",
                                content_type="application/json")
            self.assertEqual(code, 400, constant)
        self.assertEqual(s.call("PATCH", f"/api/npcs/{npc['id']}", {"pos_x": 12.5})[0], 200)
        # Archive d'import au CRC faux : 400 « Archive corrompue ».
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            z.writestr("campaign.json", b'{"format":"maze-campaign","version":1,"campaign":{"name":"x"}}')
        damaged = bytearray(buf.getvalue())
        damaged[damaged.find(b'"maze-campaign"') + 1] ^= 1
        code, body = s.call("POST", "/api/campaigns/import", raw=bytes(damaged), content_type="application/zip")
        self.assertEqual((code, body["detail"]), (400, "Archive corrompue (campaign.json)"))
        self.assertEqual(s.call("GET", "/api/health")[0], 200)


class HostileHtmlTest(unittest.TestCase):
    """Audit PERF-5 : expressions régulières sur le HTML (<img …) en temps linéaire. Avant : 4 s pour un modèle de
    200 Ko de « <img » sans « > » (route ouverte à tout MJ), plusieurs minutes pour retirer une illustration d'une
    fiche piégée ou restaurer une version qui la cite."""

    def test_linear_and_unchanged_on_normal_html(self):
        import sqlite3
        import tempfile
        from maze import db, media_admin, templates, versions
        hostile = "<img " * (2 * MIB // 5)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "maze.db"
            db.migrate(path)
            conn = sqlite3.connect(path)
            try:
                for label, work in [("modèle", lambda: templates._clean(hostile)),
                                    ("illustration retirée", lambda: media_admin.strip_doc_image(hostile, 7)),
                                    ("version restaurée", lambda: versions.drop_missing_images(
                                        conn, 1, hostile + '<img data-doc-image="7">')),
                                    ("guillemets jamais fermés", lambda: templates._clean('<img a="' * (2 * MIB // 8))),
                                    ("balises fermées", lambda: templates._clean('<p a="<">x<img alt=">"></p>' * (MIB // 25)))]:
                    started = time.perf_counter()
                    work()
                    self.assertLess(time.perf_counter() - started, 2, label)
                html = ('<p>a</p><img src="/media/1/x.webp" alt="" data-doc-image="7"><p>b</p>'
                        '<img src="/media/1/y.webp" data-doc-image="8" alt="vue">')
                self.assertEqual(media_admin.strip_doc_image(html, 7),
                                 '<p>a</p><p>b</p><img src="/media/1/y.webp" data-doc-image="8" alt="vue">')
                self.assertEqual(versions.drop_missing_images(conn, 1, html), "<p>a</p><p>b</p>")
                self.assertEqual(templates._clean('<p>x<IMG SRC="/media/1/x.webp" data-doc-image="3"></p>'), "<p>x</p>")
                # Contre-revue du lot 6 : « < » ou « > » non échappé dans un attribut (HTML enregistré par un navigateur
                # d'avant 2025) : la balise est quand même reconnue ; ailleurs, rien ne bouge.
                legacy = ('<p>a</p><img src="/media/1/x.webp" alt="Carte <ancienne>" data-doc-image="7"><p>b</p>'
                          "<img alt='x > y' src=\"/media/1/z.webp\" data-doc-image=7><p>c</p>"
                          '<img src="/media/1/y.webp" alt="a<b" data-doc-image="8"><p title="<img data-doc-image=7>">d</p>')
                kept = '<img src="/media/1/y.webp" alt="a<b" data-doc-image="8">'
                self.assertEqual(media_admin.strip_doc_image(legacy, 7),
                                 '<p>a</p><p>b</p><p>c</p>' + kept + '<p title="<img data-doc-image=7>">d</p>')
                self.assertEqual(versions.drop_missing_images(conn, 1, legacy.replace('data-doc-image="8"', "")),
                                 '<p>a</p><p>b</p><p>c</p><img src="/media/1/y.webp" alt="a<b" ><p title="<img data-doc-image=7>">d</p>')
                self.assertEqual(templates._clean('<p>x<img alt="1 < 2 > 0" src="/media/1/x.webp"></p>'), "<p>x</p>")
                self.assertEqual(templates._clean('<!-- <img src=x> --><p>y</p><img src="/media/1/x.webp">'),
                                 '<!-- <img src=x> --><p>y</p>')
                for unclosed in ('<p>a</p><img alt="jamais fermé>', '<p>a</p><img src=x', '<p>a<img</p>'):
                    self.assertEqual(templates._clean(unclosed), unclosed)
            finally:
                conn.close()


class FakeWS:
    """WebSocket simulé pour collab.Hub.endpoint (audit PERF-9, PERF-11). `stuck` : le navigateur ne lit plus, chaque
    envoi reste bloqué."""

    def __init__(self, doc_id: int, session: dict, client: int, stuck: bool = False):
        from maze.collab import SCHEMA_VERSION
        self.path_params = {"id": doc_id}
        self.headers = {"host": "maze"}
        self.query_params = {"client": str(client), "v": str(SCHEMA_VERSION)}
        self.scope = {"session": session}
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list = []
        self.closed: tuple | None = None
        self.gate = asyncio.Event()
        if not stuck:
            self.gate.set()

    async def accept(self):
        pass

    async def receive(self):
        return await self.inbox.get()

    async def send_bytes(self, data):
        await self.gate.wait()
        self.sent.append(data)

    async def send_text(self, text):
        await self.gate.wait()
        self.sent.append(json.loads(text))

    async def close(self, code=1000, reason=None):
        self.closed = (code, reason)
        self.inbox.put_nowait({"type": "websocket.disconnect"})

    def push(self, data: bytes):
        self.inbox.put_nowait({"type": "websocket.receive", "bytes": data})

    def welcomed(self) -> bool:
        return any(isinstance(m, dict) and m.get("t") == "welcome" for m in self.sent)


async def settle(rooms) -> None:
    """Attend la fin du travail des salles (écritures dans des fils, version de fin de séance) : sinon un fil écrit
    encore dans la base pendant que le test efface son dossier."""
    for room in rooms:
        if room.worker is not None:
            await asyncio.wait([room.worker])


async def until(condition, timeout: float = 10):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition toujours fausse")
        await asyncio.sleep(0.01)


class LiveLimitsTest(unittest.TestCase):
    """Temps réel : nombre de connexions borné par compte et au total (audit PERF-9) ; navigateur qui ne lit plus
    coupé au lieu de garder sa file (64 Mo) et sa place dans la salle indéfiniment (PERF-11)."""

    def setUp(self):
        import tempfile
        from maze import db
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "maze.db"
        db.migrate(self.db_path)
        with db.transaction(self.db_path) as conn:
            for name in ("alice", "bob"):
                conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?, 'x', 'admin')", (name,))
            conn.execute("INSERT INTO campaigns (name) VALUES ('C')")
            for title in ("A", "B"):
                conn.execute("INSERT INTO documents (campaign_id, kind, title) VALUES (1, 'scenario', ?)", (title,))
            self.sessions = {r["username"]: {"uid": r["id"], "epoch": r["session_epoch"], "key": r["session_key"]}
                             for r in conn.execute("SELECT * FROM users")}
            self.docs = [r[0] for r in conn.execute("SELECT id FROM documents ORDER BY id")]

    def open(self, hub, user: str, doc: int, client: int, stuck: bool = False):
        ws = FakeWS(doc, self.sessions[user], client, stuck)
        return ws, asyncio.ensure_future(hub.endpoint(ws))

    def test_connection_limits(self):
        from maze.collab import CLOSE_BUSY, Hub

        async def scenario():
            hub = Hub(self.db_path, max_per_user=2, max_total=3)
            a1, t1 = self.open(hub, "alice", self.docs[0], 1)
            a2, t2 = self.open(hub, "alice", self.docs[1], 2)
            await until(lambda: a1.welcomed() and a2.welcomed())
            a3, t3 = self.open(hub, "alice", self.docs[0], 3)
            await t3   # refusée tout de suite, sans lire le journal
            self.assertEqual(a3.closed, (CLOSE_BUSY, "Trop de fiches ouvertes en temps réel par ce compte"))
            b1, u1 = self.open(hub, "bob", self.docs[0], 4)
            await until(b1.welcomed)
            b2, u2 = self.open(hub, "bob", self.docs[0], 5)
            await u2
            self.assertEqual(b2.closed, (CLOSE_BUSY, "Trop de connexions temps réel sur ce serveur"))
            self.assertEqual(dict(hub.connections), {self.sessions["alice"]["uid"]: 2, self.sessions["bob"]["uid"]: 1})
            # Une connexion qui se termine libère sa place.
            a1.inbox.put_nowait({"type": "websocket.disconnect"})
            await t1
            a4, t4 = self.open(hub, "alice", self.docs[0], 6)
            await until(a4.welcomed)
            rooms = list(hub.rooms.values())
            for ws in (a2, a4, b1):
                ws.inbox.put_nowait({"type": "websocket.disconnect"})
            await asyncio.gather(t2, t4, u1)
            await settle(rooms)
            self.assertEqual(dict(hub.connections), {})
        asyncio.run(scenario())

    def test_limits_settings(self):
        from maze.config import load_settings
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for env, expected in [({}, (12, 100)), ({"MAZE_LIVE_MAX_PER_USER": "3", "MAZE_LIVE_MAX_TOTAL": "40"}, (3, 40)),
                                  ({"MAZE_LIVE_MAX_PER_USER": "0", "MAZE_LIVE_MAX_TOTAL": "beaucoup"}, (12, 100))]:
                saved = {k: os.environ.get(k) for k in ("MAZE_DATA_DIR", "MAZE_LIVE_MAX_PER_USER", "MAZE_LIVE_MAX_TOTAL")}
                os.environ.update({"MAZE_DATA_DIR": tmp, **env})
                try:
                    cfg = load_settings()
                    self.assertEqual((cfg.live_max_per_user, cfg.live_max_total), expected, env)
                finally:
                    for k, v in saved.items():
                        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_slow_client_cut(self):
        from maze import collab

        async def scenario():
            hub = collab.Hub(self.db_path)
            doc = self.docs[0]
            slow, ts = self.open(hub, "alice", doc, 1, stuck=True)
            fast, tf = self.open(hub, "bob", doc, 2)
            await until(lambda: len(hub.rooms.get(doc, collab.Room(0, 0)).peers) == 2 and fast.welcomed())
            rooms = [hub.rooms[doc]]
            peer = next(p for p in hub.rooms[doc].peers.values() if p.ws is slow)
            # L'autre MJ colle 40 fois 1,9 Mo : la file du navigateur bloqué dépasse MAX_QUEUE_BYTES.
            for _ in range(40):
                fast.push(bytes([collab.UPDATE]) + random.randbytes(1_900_000))
            await until(lambda: slow.closed is not None, 60)
            self.assertEqual(slow.closed[0], collab.CLOSE_RETRY)
            await until(lambda: peer.id not in hub.rooms[doc].peers)
            self.assertEqual((peer.queue.qsize(), peer.queued_bytes), (0, 0))
            await ts   # connexion terminée côté serveur
            self.assertIs(hub.rooms[doc].leader.ws, fast)   # le meneur bloqué a été remplacé
            await until(lambda: sum(1 for m in fast.sent if isinstance(m, bytes) and m[0] == collab.ACK) == 40, 60)

            # Fermeture demandée (fiche supprimée) à un navigateur qui ne lit plus : coupé après CLOSE_GRACE.
            other = self.docs[1]
            stuck, tu = self.open(hub, "alice", other, 3, stuck=True)
            await until(lambda: other in hub.rooms and len(hub.rooms[other].peers) == 1)
            rooms.append(hub.rooms[other])
            grace, collab.CLOSE_GRACE = collab.CLOSE_GRACE, 0.2
            try:
                hub.close_documents([other], "deleted")
                await until(lambda: stuck.closed is not None, 5)
            finally:
                collab.CLOSE_GRACE = grace
            self.assertEqual(stuck.closed[0], collab.CLOSE_GONE)
            await tu
            fast.inbox.put_nowait({"type": "websocket.disconnect"})
            await tf
            await settle(rooms)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
