"""Édition à plusieurs en temps réel : relais WebSocket des modifications Yjs.

Le serveur ne fusionne pas les documents : chaque navigateur tient une copie Yjs (CRDT) et lui
envoie ses modifications. Le serveur les numérote, les enregistre, puis les rediffuse aux autres
personnes connectées à la même fiche. Les modifications Yjs se fusionnent dans n'importe quel ordre,
ce qui suffit à garantir que tout le monde converge vers le même texte.

Rôles attribués par le serveur dans chaque salle (une salle = une fiche ouverte) :
- « meneur » (le premier arrivé) : envoie régulièrement le HTML dérivé de la fiche (rétroliens,
  sauvegardes lisibles) et compacte le journal à la demande ;
- « initialiseur » : à la première ouverture d'une fiche rédigée avant le temps réel, un seul
  navigateur convertit l'ancien HTML en document Yjs ; les autres attendent qu'il ait fini.

Trames binaires (premier octet = type) :
  0 UPDATE    client > serveur : [0][modif]          serveur > client : [0][n° 8 octets][modif]
  1 AWARENESS curseurs et sélections, relayés tels quels aux autres
  2 SNAPSHOT  meneur > serveur : [2][n° couvert 8 octets][état complet]  (compaction)
  3 ACK       serveur > client : [3][n° 8 octets]  (numéro attribué à sa propre modification)
Trames texte : messages de contrôle JSON (voir les champs "t").
"""

from __future__ import annotations

import asyncio
import collections
import functools
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import anyio
import anyio.from_thread
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import auth, backup, db, versions
from .documents import link_images, sync_mentions

log = logging.getLogger("maze.collab")

UPDATE, AWARENESS, SNAPSHOT, ACK = 0, 1, 2, 3
COMPACT_EVERY = 300                # modifications enregistrées avant de demander une compaction
MAX_UPDATE = 2 * 1024 * 1024        # une modification (frappe, collage) : très largement suffisant
MAX_SNAPSHOT = 15 * 1024 * 1024     # état complet d'une fiche (sous la limite de 16 Mo d'uvicorn)
MAX_AWARENESS = 64 * 1024
MAX_HTML = 4 * 1024 * 1024
MAX_QUEUE_BYTES = 64 * 1024 * 1024  # en attente pour un navigateur trop lent : au-delà, on le déconnecte
CLOSE_GRACE = 10                    # fermeture demandée : délai pour envoyer ce qui précède, puis coupure (PERF-11)
CLOSE_TIMEOUT = 5                   # coupure : attente au plus de la fermeture propre de la connexion
# Connexions temps réel admises (audit PERF-9), réglables (config.Settings) : MAZE_LIVE_MAX_PER_USER (12 par compte,
# toutes fiches), MAZE_LIVE_MAX_TOTAL (100 pour toute l'instance). Au-delà : CLOSE_BUSY, le navigateur réessaie.
LIVE_MAX_PER_USER = 12
LIVE_MAX_TOTAL = 100
RECHECK_SECONDS = 60                # revérification des droits des connexions ouvertes
INIT_TIMEOUT = 30                   # conversion d'une ancienne fiche : délai avant de la confier à un autre
WATCH_SECONDS = 10
MAX_CLIENT_ID = 2**53

# Couleurs des curseurs (lisibles sur le thème sombre), attribuées selon le compte.
COLORS = ["#e8a33d", "#5fb3d4", "#9b7fe0", "#6cc58a", "#e06c75", "#d98ad6", "#4fc2b5", "#c9b458"]

# Version du schéma de l'éditeur (audit H3) : même valeur que frontend/src/editor/schema-version.json, injectée
# dans l'interface à la construction (vérifié par test_api et, pour l'empreinte du schéma, par build.mjs). Un
# navigateur d'une autre version supprimerait du document partagé les blocs que son schéma ne connaît pas
# (y-prosemirror) : il est refusé et recharge la page. À n'augmenter que quand le schéma change.
SCHEMA_VERSION = 1

# Fermetures (codes 4000-4999 réservés aux applications). CLOSE_RETRY : le navigateur se reconnecte
# et renvoie ses modifications non confirmées ; les autres codes sont définitifs. CLOSE_VERSION : version du
# schéma différente (le navigateur garde son texte non envoyé comme version nommée, puis recharge la page) ;
# une interface d'avant ce contrôle (sans version) reçoit CLOSE_AUTH, qui lui fait déjà recharger la page.
CLOSE_RETRY = 4000
CLOSE_AUTH, CLOSE_FORBIDDEN, CLOSE_GONE, CLOSE_RESET, CLOSE_TOO_LARGE = 4401, 4403, 4404, 4409, 4413
CLOSE_VERSION = 4426
# Trop de connexions (audit PERF-9) : code non définitif pour l'interface (nouvel essai, délai croissant).
CLOSE_BUSY = 4429
STALE_CLIENT = "Maze a été mis à jour : rechargez la page"
# Écriture refusée pendant une restauration (REST : 503, connecteur : erreur de l'outil ; audit M7).
RESTORING = "Restauration en cours : réessayez dans un instant"


def seq_bytes(n: int) -> bytes:
    return n.to_bytes(8, "big")


@dataclass(eq=False)
class Peer:
    id: int
    user: dict[str, Any]
    ws: WebSocket
    client_id: int
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    checked_at: float = field(default_factory=time.monotonic)
    queued_bytes: int = 0  # trames en file ou en cours d'envoi (décomptées une fois envoyées : PERF-11)
    closing: bool = False  # fermeture demandée : les trames suivantes sont ignorées (le navigateur les renverra)
    joined_seq: int = 0    # dernier numéro du journal lu à l'arrivée : déjà reçu, pas renvoyé par broadcast
    writer: asyncio.Task | None = None
    aborted: bool = False
    on_abort: Any = None   # retrait de la salle (Hub._leave), appelé hors de toute boucle sur les pairs

    @property
    def name(self) -> str:
        return self.user["display_name"] or self.user["username"]

    @property
    def color(self) -> str:
        return COLORS[self.user["id"] % len(COLORS)]

    def info(self) -> dict[str, Any]:
        return {"id": self.id, "user_id": self.user["id"], "name": self.name, "color": self.color,
                "client": self.client_id}

    def send(self, message: bytes | dict) -> None:
        if self.closing:
            return
        if isinstance(message, dict):
            message = json.dumps(message)   # compté comme les trames binaires
        self.queued_bytes += len(message)
        self.queue.put_nowait(message)
        if self.queued_bytes > MAX_QUEUE_BYTES:
            # Navigateur qui ne lit plus : une fermeture mise en file attendrait derrière l'envoi bloqué (PERF-11).
            self.abort(CLOSE_RETRY)

    def close(self, code: int, detail: str | None = None) -> None:
        """Fermeture après l'envoi de ce qui est déjà en file ; au-delà de CLOSE_GRACE (navigateur qui ne lit
        plus), la connexion est coupée."""
        if self.closing:
            return
        if detail:
            self.send({"t": "error", "code": code, "detail": detail})
        self.queue.put_nowait(("close", code))
        self.closing = True
        try:
            asyncio.get_running_loop().call_later(CLOSE_GRACE, self.abort, code)
        except RuntimeError:
            pass   # hors de la boucle (aucun envoi possible de toute façon)

    def abort(self, code: int) -> None:
        """Coupure sans attendre les trames en file (audit PERF-11) : file vidée (mémoire rendue), envoi en cours
        annulé, pair retiré de sa salle (ni meneur, ni destinataire), fermeture bornée par CLOSE_TIMEOUT."""
        if self.aborted or (self.writer is not None and self.writer.done()):
            return
        self.aborted = self.closing = True
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queued_bytes = 0
        if self.writer is not None:
            self.writer.cancel()
        loop = asyncio.get_running_loop()
        if self.on_abort is not None:
            loop.call_soon(self.on_abort)

        async def shut() -> None:
            try:
                await asyncio.wait_for(self.ws.close(code), CLOSE_TIMEOUT)
            except Exception:  # noqa: BLE001 - connexion déjà fermée ou bloquée : abandonnée au serveur WebSocket
                pass
        loop.create_task(shut())


@dataclass(eq=False)
class Room:
    doc_id: int
    campaign_id: int
    max_seq: int = 0
    peers: dict[int, Peer] = field(default_factory=dict)
    leader: Peer | None = None
    initializer: Peer | None = None
    since_compact: int = 0
    last_editor: int | None = None
    init_since: float = 0.0
    closed: bool = False  # fiche supprimée ou réinitialisée : plus rien n'est accepté
    epoch: int = 0        # live_epoch de la fiche à l'ouverture de la salle (vérifié à chaque écriture)
    # Dernière compaction : son numéro, le dernier numéro qui la précédait et le meneur qui l'a faite.
    # Le HTML que ce meneur envoie ensuite contient tout l'état compacté (voir _save_content).
    snapshot_seq: int = 0
    snapshot_prev: int = 0
    snapshot_peer: Peer | None = None
    # Trames reçues, traitées une à une dans l'ordre d'arrivée par une tâche de la salle ; leur travail SQLite se
    # fait dans un fil (db.run_db) : la boucle reste libre pendant une attente de verrou (audit M15 = PERF-1).
    jobs: collections.deque = field(default_factory=collections.deque)
    worker: asyncio.Task | None = None

    @property
    def waiting(self) -> bool:
        return self.initializer is not None

    def broadcast(self, message: bytes | dict, exclude: Peer | None = None, seq: int = 0) -> None:
        for peer in self.peers.values():
            if peer is not exclude and not (seq and seq <= peer.joined_seq):
                peer.send(message)

    def peers_info(self) -> list[dict[str, Any]]:
        return [p.info() for p in sorted(self.peers.values(), key=lambda p: p.id)]


class _Skipped(Exception):
    """Écriture abandonnée dans le fil de travail : restauration commencée entre-temps."""


def in_loop(method):
    """Méthode du Hub appelée par les routes REST, qui s'exécutent dans des fils de travail (db.run_db, audit M15) :
    elle est exécutée dans la boucle d'événements (salles, files des navigateurs), et l'appelant attend sa fin."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                return anyio.from_thread.run_sync(functools.partial(method, self, *args, **kwargs))
            except anyio.NoEventLoopError:
                pass   # fil étranger à anyio (scripts, tests) : appel direct, comme avant
        return method(self, *args, **kwargs)
    return wrapper


class Hub:
    """Salles d'édition ouvertes, dans la boucle asyncio d'uvicorn (un seul processus). Le travail SQLite des trames
    se fait dans des fils (db.run_db), une trame à la fois par salle, dans l'ordre d'arrivée."""

    def __init__(self, db_path: Path, max_per_user: int = LIVE_MAX_PER_USER, max_total: int = LIVE_MAX_TOTAL):
        self.db_path = db_path
        self.rooms: dict[int, Room] = {}
        self._next_id = 0
        self._watchdog: asyncio.Task | None = None
        self.suspended = False  # restauration depuis l'administration (suspend / resume)
        # Connexions ouvertes, par compte et au total (audit PERF-9).
        self.max_per_user, self.max_total = max_per_user, max_total
        self.connections: collections.Counter = collections.Counter()

    # --- Restauration (audit M7) -----------------------------------------------------------

    def restoring(self) -> bool:
        """Restauration en cours, ici (administration) ou dans un autre processus (ligne de commande)."""
        return self.suspended or backup.restore_in_progress(self.db_path.parent)

    def _close_all(self) -> None:
        """Toutes les salles fermées sans rien enregistrer de plus : les navigateurs gardent leurs modifications non
        confirmées et se reconnectent plus tard (CLOSE_RETRY) ; une fois la restauration finie, ils voient la
        nouvelle époque et rechargent la fiche restaurée."""
        for doc_id in list(self.rooms):
            room = self.rooms.pop(doc_id)
            room.closed = True
            room.initializer = None
            for peer in room.peers.values():
                peer.close(CLOSE_RETRY)

    @in_loop
    def suspend(self) -> None:
        """Avant une restauration depuis l'administration : salles fermées, connexions et trames refusées."""
        self.suspended = True
        self._close_all()

    @in_loop
    def resume(self) -> None:
        self.suspended = False

    # --- Point d'entrée WebSocket --------------------------------------------------------

    async def endpoint(self, ws: WebSocket) -> None:
        doc_id = int(ws.path_params["id"])
        await ws.accept()

        def refuse(code: int, detail: str, **extra: Any):
            return self._refuse(ws, code, detail, **extra)

        # Une page d'un autre site ne doit pas pouvoir ouvrir de connexion avec le cookie de session.
        origin = ws.headers.get("origin")
        if origin and urlparse(origin).netloc != ws.headers.get("host"):
            return await refuse(CLOSE_FORBIDDEN, "Origine refusée")
        # Schéma de l'éditeur du navigateur (audit H3), vérifié avant tout envoi du journal.
        version = ws.query_params.get("v")
        if version is None:
            return await refuse(CLOSE_AUTH, STALE_CLIENT, version=SCHEMA_VERSION)
        if version != str(SCHEMA_VERSION):
            return await refuse(CLOSE_VERSION, STALE_CLIENT, version=SCHEMA_VERSION)
        user = auth.load_session_user(self.db_path, ws.scope.get("session") or {})
        if user is None:
            return await refuse(CLOSE_AUTH, "Authentification requise")
        if self.restoring():
            await ws.close(CLOSE_RETRY)  # nouvel essai du navigateur après la restauration
            return
        try:
            client_id = int(ws.query_params.get("client", ""))
            if not 0 <= client_id < MAX_CLIENT_ID:
                raise ValueError
        except ValueError:
            return await refuse(CLOSE_FORBIDDEN, "Identifiant de client invalide")
        # Nombre de connexions borné (audit PERF-9) ; compté jusqu'à la fin de celle-ci, quelle qu'en soit l'issue.
        if self.connections[user["id"]] >= self.max_per_user:
            await ws.close(CLOSE_BUSY, "Trop de fiches ouvertes en temps réel par ce compte")
            return
        if sum(self.connections.values()) >= self.max_total:
            await ws.close(CLOSE_BUSY, "Trop de connexions temps réel sur ce serveur")
            return
        self.connections[user["id"]] += 1
        try:
            await self._serve(ws, doc_id, user, client_id)
        finally:
            self.connections[user["id"]] -= 1
            if self.connections[user["id"]] <= 0:
                del self.connections[user["id"]]

    async def _serve(self, ws: WebSocket, doc_id: int, user: dict[str, Any], client_id: int) -> None:
        def refuse(code: int, detail: str, **extra: Any):
            return self._refuse(ws, code, detail, **extra)

        # Lecture du journal dans la boucle (lecture seule en WAL : elle n'attend pas les écritures), puis aucune
        # attente jusqu'à l'inscription dans la salle. Une modification en cours d'écriture dans un fil peut être
        # déjà dans cette lecture : elle n'est alors pas renvoyée à ce navigateur (joined_seq).
        try:
            with db.transaction(self.db_path, write=False, timeout=db.LOOP_TIMEOUT) as conn:
                doc = conn.execute("SELECT campaign_id, content, content_seq, live_epoch FROM documents WHERE id = ?",
                                   (doc_id,)).fetchone()
                allowed = doc is not None and auth.can_access(conn, user, doc["campaign_id"])
                rows = conn.execute("SELECT id, data FROM document_updates WHERE document_id = ? ORDER BY id",
                                    (doc_id,)).fetchall() if allowed else []
        except sqlite3.OperationalError as exc:
            if not db.is_busy(exc):
                raise
            await ws.close(CLOSE_RETRY)
            return
        if not allowed:
            return await refuse(CLOSE_GONE, "Fiche introuvable")

        self._next_id += 1
        peer = Peer(id=self._next_id, user=user, ws=ws, client_id=client_id, joined_seq=rows[-1]["id"] if rows else 0)
        room = self.rooms.get(doc_id)
        if room is None:
            # Compteur de compaction parti de la taille réelle du journal (audit PERF-12 = DATA-17) : sinon, une fiche
            # travaillée en séances de moins de COMPACT_EVERY modifications n'était jamais compactée.
            room = self.rooms[doc_id] = Room(doc_id=doc_id, campaign_id=doc["campaign_id"], epoch=doc["live_epoch"],
                                             since_compact=len(rows))
        room.max_seq = max(room.max_seq, peer.joined_seq)
        room.peers[peer.id] = peer
        if room.leader is None:
            room.leader = peer
        needs_init = not rows and bool(doc["content"].strip()) and room.initializer is None
        if needs_init:
            room.initializer = peer
            room.init_since = time.monotonic()
        for row in rows:
            peer.send(bytes([UPDATE]) + seq_bytes(row["id"]) + row["data"])
        rows = row = None   # journal en file : plus gardé en double pendant toute la connexion (audit PERF-9)
        peer.send({
            "t": "welcome", "you": peer.id, "color": peer.color, "seq": room.max_seq,
            "leader": room.leader is peer, "init": needs_init,
            "waiting": room.waiting and not needs_init,
            "stale": doc["content_seq"] < room.max_seq, "peers": room.peers_info(), "epoch": doc["live_epoch"],
        })
        room.broadcast({"t": "peers", "peers": room.peers_info(), "joined": peer.id}, exclude=peer)
        # Journal déjà long à l'ouverture : compaction demandée au meneur tout de suite, après le journal et le welcome
        # (ses trames sont traitées dans l'ordre : son état contient déjà tout le journal).
        if room.leader is peer and not room.waiting and room.since_compact >= COMPACT_EVERY:
            room.since_compact = 0
            peer.send({"t": "compact"})

        writer = peer.writer = asyncio.create_task(self._writer(peer))
        peer.on_abort = functools.partial(self._leave, room, peer)
        if self._watchdog is None or self._watchdog.done():
            self._watchdog = asyncio.create_task(self._watch())
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if time.monotonic() - peer.checked_at > RECHECK_SECONDS and not self._still_allowed(peer, room):
                    break
                if room.closed or peer.closing:
                    continue
                if self.restoring():
                    self._close_all()
                    continue
                # Traitée à son tour dans la salle ; ce navigateur attend la fin avant sa trame suivante.
                if message.get("bytes") is not None:
                    await self._submit(room, functools.partial(self._on_binary, room, peer, message["bytes"]))
                elif message.get("text") is not None:
                    await self._submit(room, functools.partial(self._on_text, room, peer, message["text"]))
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 - une erreur ne doit pas laisser une salle incohérente
            log.exception("Erreur dans la salle de la fiche %s", doc_id)
        finally:
            self._leave(room, peer)
            if not writer.done():
                peer.queue.put_nowait(None)
            # Tâche annulée par une coupure (abort) : rien à remonter ; envoi toujours bloqué (navigateur qui ne lit
            # plus) : abandonné après CLOSE_TIMEOUT.
            await asyncio.wait([writer], timeout=CLOSE_TIMEOUT)
            writer.cancel()

    # --- File de la salle et travail SQLite hors de la boucle ---------------------------------

    def _submit(self, room: Room, job) -> asyncio.Future:
        """job : coroutine sans argument, exécutée après celles déjà reçues dans cette salle."""
        future = asyncio.get_running_loop().create_future()
        room.jobs.append((job, future))
        if room.worker is None or room.worker.done():
            room.worker = asyncio.create_task(self._run_jobs(room))
        return future

    async def _run_jobs(self, room: Room) -> None:
        while room.jobs:
            job, future = room.jobs.popleft()
            try:
                await job()
            except Exception as exc:  # noqa: BLE001 - remise à celui qui attend la trame (endpoint)
                if not future.done():
                    future.set_exception(exc)
            else:
                if not future.done():
                    future.set_result(None)

    async def _write(self, room: Room, peer: Peer | None, work):
        """work(conn) dans un fil de travail, dans une transaction d'écriture. None si l'écriture n'a pas eu lieu :
        restauration commencée entre-temps (rien n'est écrit dans la base qu'elle remplace), fiche supprimée ou
        contenu remplacé par l'API entre-temps, ou verrou toujours pris après l'attente (le navigateur se reconnecte
        et renvoie ce qui n'a pas été confirmé ; après un remplacement, il voit la nouvelle époque)."""
        def run():
            with db.transaction(self.db_path) as conn:
                if self.restoring():
                    raise _Skipped
                # L'API valide le remplacement du contenu (journal vidé, époque suivante) dans son fil, puis ferme la
                # salle dans la boucle (close_documents) : une trame qui attendait le verrou passe entre les deux
                # (contre-revue du lot 4). Rien n'est écrit pour une autre époque que celle de la salle.
                current = conn.execute("SELECT live_epoch FROM documents WHERE id = ?", (room.doc_id,)).fetchone()
                if current is None or current[0] != room.epoch:
                    raise _Skipped
                return work(conn)
        try:
            return await db.run_db(run)
        except _Skipped:
            if peer is not None:
                peer.close(CLOSE_RETRY)
            return None
        except sqlite3.OperationalError as exc:
            if not db.is_busy(exc):
                raise
            log.warning("Fiche %s : base occupée, trame non enregistrée (reconnexion du navigateur)", room.doc_id)
            if peer is not None:
                peer.close(CLOSE_RETRY)
            return None

    async def _refuse(self, ws: WebSocket, code: int, detail: str, **extra: Any) -> None:
        await ws.send_text(json.dumps({"t": "error", "code": code, "detail": detail, **extra}))
        await ws.close(code)

    async def _writer(self, peer: Peer) -> None:
        """Envoie les trames d'un navigateur dans l'ordre où elles ont été produites."""
        while True:
            message = await peer.queue.get()
            if message is None:
                return
            try:
                if isinstance(message, bytes):
                    await peer.ws.send_bytes(message)
                elif isinstance(message, tuple):
                    await peer.ws.close(message[1])
                    return
                else:
                    await peer.ws.send_text(message)
            except Exception:  # noqa: BLE001 - connexion déjà fermée
                return
            # Décompté une fois envoyé : la trame bloquée chez un navigateur trop lent reste comptée (PERF-11).
            peer.queued_bytes -= len(message)
            message = None   # dernière trame (jusqu'à 15 Mo) : pas gardée jusqu'à la suivante (PERF-9)

    # --- Messages reçus ------------------------------------------------------------------

    async def _on_binary(self, room: Room, peer: Peer, data: bytes) -> None:
        if not data or room.closed or peer.closing:
            return
        kind = data[0]
        if kind == UPDATE:
            payload = data[1:]
            # Jamais d'abandon silencieux : le navigateur associe chaque accusé de réception à sa
            # plus ancienne modification en attente.
            if len(payload) > MAX_UPDATE:
                peer.close(CLOSE_TOO_LARGE, "Modification trop volumineuse")
                return
            if not payload or (room.waiting and peer is not room.initializer):
                peer.close(CLOSE_RETRY)  # il se reconnectera et renverra ses modifications
                return
            seq = await self._write(room, peer, lambda conn: db.insert(
                conn, "document_updates", {"document_id": room.doc_id, "data": payload, "user_id": peer.user["id"]}))
            if seq is None or room.closed:
                return
            room.max_seq = max(room.max_seq, seq)
            room.last_editor = peer.user["id"]
            room.since_compact += 1
            peer.send(bytes([ACK]) + seq_bytes(seq))
            room.broadcast(bytes([UPDATE]) + seq_bytes(seq) + payload, exclude=peer, seq=seq)
            if room.since_compact >= COMPACT_EVERY and room.leader is not None:
                room.since_compact = 0
                room.leader.send({"t": "compact"})
        elif kind == AWARENESS:
            if len(data) <= MAX_AWARENESS:
                room.broadcast(data, exclude=peer)
        elif kind == SNAPSHOT:
            if peer is not room.leader or len(data) < 10 or len(data) > MAX_SNAPSHOT:
                return
            upto = int.from_bytes(data[1:9], "big")
            if upto > room.max_seq:
                return
            def compact(conn: sqlite3.Connection) -> int:
                conn.execute("DELETE FROM document_updates WHERE document_id = ? AND id <= ?", (room.doc_id, upto))
                return db.insert(conn, "document_updates",
                                 {"document_id": room.doc_id, "data": data[9:], "user_id": peer.user["id"]})
            seq = await self._write(room, peer, compact)
            if seq is None or room.closed:
                return
            room.snapshot_prev, room.snapshot_seq, room.snapshot_peer = room.max_seq, seq, peer
            room.max_seq = max(room.max_seq, seq)
            room.since_compact = 0
            # Diffusé comme une modification ordinaire : il peut contenir des modifications du meneur
            # que les autres n'ont pas encore reçues, et que la compaction suivante supprimerait.
            room.broadcast(bytes([UPDATE]) + seq_bytes(seq) + data[9:], exclude=peer, seq=seq)

    async def _on_text(self, room: Room, peer: Peer, text: str) -> None:
        if room.closed or peer.closing:
            return
        try:
            message = json.loads(text)
        except (ValueError, RecursionError):
            return
        if not isinstance(message, dict):
            return
        kind = message.get("t")
        if kind == "init-done" and peer is room.initializer:
            room.initializer = None
            room.broadcast({"t": "ready"})
        elif kind == "content" and peer is room.leader:
            html, upto = message.get("html"), message.get("upto")
            if isinstance(html, str) and isinstance(upto, int) and len(html) <= MAX_HTML and 0 <= upto <= room.max_seq:
                await self._save_content(room, peer, html, upto)

    async def _save_content(self, room: Room, peer: Peer, html: str, upto: int) -> None:
        # Le numéro de la compaction n'est jamais accusé au meneur : son HTML, envoyé après sa propre
        # compaction et à jour de tout ce qui la précédait, la couvre pourtant. Sans cela, content_seq
        # resterait en deçà du journal et la fiche paraîtrait avoir des modifications non enregistrées.
        if room.snapshot_peer is peer and upto >= room.snapshot_prev:
            upto = max(upto, room.snapshot_seq)
        editor = room.last_editor or peer.user["id"]
        updated_at = await self._write(room, None, lambda conn: self._store_content(conn, room, html, upto, editor))
        if updated_at is not None and not room.closed:
            room.broadcast({"t": "meta", "updated_at": updated_at})

    @staticmethod
    def _store_content(conn: sqlite3.Connection, room: Room, html: str, upto: int, editor: int) -> str | None:
        """HTML du meneur enregistré (fil de travail) ; date de modification, ou None si rien n'a changé."""
        current = conn.execute("SELECT content, content_seq FROM documents WHERE id = ?",
                               (room.doc_id,)).fetchone()
        if current is None or upto < current["content_seq"]:
            return None
        if current["content"] == html:
            conn.execute("UPDATE documents SET content_seq = ? WHERE id = ?", (upto, room.doc_id))
            return None
        # Début d'une séance d'écriture : l'état d'avant est gardé dans l'historique.
        versions.snapshot(conn, room.doc_id, only_if_older=True)
        conn.execute(
            """UPDATE documents SET content = ?, content_seq = ?, revision = revision + 1,
                      updated_by = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                WHERE id = ?""",
            (html, upto, editor, room.doc_id))
        sync_mentions(conn, room.doc_id, html)
        link_images(conn, room.doc_id, room.campaign_id, html)
        conn.execute("UPDATE campaigns SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                     (room.campaign_id,))
        return conn.execute("SELECT updated_at FROM documents WHERE id = ?", (room.doc_id,)).fetchone()[0]

    # --- Départs ---------------------------------------------------------------------------

    async def _end_of_session(self, room: Room) -> None:
        """Dernier MJ parti : l'état final de la fiche rejoint l'historique (après les trames encore en attente)."""
        if self.restoring():
            return
        try:
            await self._write(room, None, lambda conn: versions.snapshot(conn, room.doc_id))
        except Exception:  # noqa: BLE001 - l'historique ne doit jamais empêcher un départ
            log.exception("Version de fin de séance impossible (fiche %s)", room.doc_id)

    def _leave(self, room: Room, peer: Peer) -> None:
        if room.peers.pop(peer.id, None) is None:
            return
        if not room.peers:
            if self.rooms.get(room.doc_id) is room:
                del self.rooms[room.doc_id]
            if not room.closed:
                self._submit(room, functools.partial(self._end_of_session, room))
            return
        if room.leader is peer:
            room.leader = min(room.peers.values(), key=lambda p: p.id)
            room.leader.send({"t": "leader"})
        if room.initializer is peer:
            if room.max_seq:  # sa conversion a été enregistrée avant son départ
                room.initializer = None
                room.broadcast({"t": "ready"})
            else:
                room.initializer = min(room.peers.values(), key=lambda p: p.id)
                room.init_since = time.monotonic()
                room.initializer.send({"t": "init"})
        room.broadcast({"t": "peers", "peers": room.peers_info(), "left_client": peer.client_id})

    def _still_allowed(self, peer: Peer, room: Room) -> bool:
        peer.checked_at = time.monotonic()
        # Lecture dans la boucle (WAL : sans attendre les écritures) ; base occupée malgré tout : revu plus tard.
        try:
            with db.transaction(self.db_path, write=False, timeout=db.LOOP_TIMEOUT) as conn:
                user = conn.execute("SELECT active, session_epoch, session_key, role FROM users WHERE id = ?",
                                    (peer.user["id"],)).fetchone()
                ok = (user is not None and user["active"]
                      and auth.can_access(conn, {**peer.user, "role": user["role"]}, room.campaign_id))
        except sqlite3.OperationalError as exc:
            if not db.is_busy(exc):
                raise
            peer.checked_at -= RECHECK_SECONDS - WATCH_SECONDS
            return True
        if not ok:
            peer.close(CLOSE_FORBIDDEN, "Accès retiré")
            return False
        if user["session_epoch"] != peer.user["session_epoch"] or user["session_key"] != peer.user["session_key"]:
            # Mot de passe changé : on se reconnecte ; seule une session à jour sera acceptée.
            peer.close(CLOSE_RETRY)
            return False
        return True

    async def _watch(self) -> None:
        """Tâche de fond tant que des fiches sont ouvertes : droits et conversions bloquées."""
        while self.rooms:
            await asyncio.sleep(WATCH_SECONDS)
            if self.restoring():
                self._close_all()
                continue
            now = time.monotonic()
            for room in list(self.rooms.values()):
                if room.initializer is not None and now - room.init_since > INIT_TIMEOUT:
                    room.initializer.close(CLOSE_RETRY)  # son départ confie la conversion à un autre
                for peer in list(room.peers.values()):
                    if now - peer.checked_at > RECHECK_SECONDS:
                        self._still_allowed(peer, room)

    # --- Appels depuis l'API REST ------------------------------------------------------------

    @in_loop
    def recheck(self) -> None:
        """Après un changement de compte ou d'affectation : déconnecte qui n'a plus accès."""
        for room in list(self.rooms.values()):
            for peer in list(room.peers.values()):
                self._still_allowed(peer, room)

    @in_loop
    def notify_meta(self, doc_id: int, data: dict[str, Any]) -> None:
        self.notify(doc_id, {"t": "meta", **data})

    @in_loop
    def notify(self, doc_id: int, message: dict[str, Any]) -> None:
        room = self.rooms.get(doc_id)
        if room:
            room.broadcast(message)

    @in_loop
    def close_documents(self, doc_ids: list[int], reason: str) -> None:
        """Fiche supprimée ("deleted") ou contenu remplacé par l'API ("reset") : les éditeurs rechargent."""
        code = CLOSE_GONE if reason == "deleted" else CLOSE_RESET
        for doc_id in doc_ids:
            room = self.rooms.pop(doc_id, None)
            if room is None:
                continue
            room.closed = True
            room.initializer = None
            for peer in room.peers.values():
                peer.send({"t": reason})
                peer.close(code)
