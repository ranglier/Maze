"""Application ASGI : assemble l'API, les médias, l'interface et la sécurité."""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import IO

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from . import auth, backup, db, i18n, limits, throttle
from .mediafiles import MEDIA_TYPES, TEMP_SUFFIX
from .collab import RESTORING, SCHEMA_VERSION, STALE_CLIENT, Hub
from .admin import MAX_IMPORT_MB, routes as admin_routes
from .connector import routes as connector_routes
from . import foundry
from .media_admin import routes as media_admin_routes
from .image_copy import routes as image_copy_routes
from .maps import routes as map_routes
from .mcp import MAX_BODY as MCP_MAX_BODY, routes as mcp_routes
from .oauth import routes as oauth_routes
from .documents import routes as document_routes
from .transfer import routes as transfer_routes
from .comments import routes as comment_routes
from .search import routes as search_routes
from .places import routes as place_routes
from .timeline import routes as timeline_routes
from .scenemap import routes as scenemap_routes
from .templates import routes as template_routes
from .versions import routes as version_routes
from .entry_history import routes as entry_history_routes
from .api import RequestValidationError, routes as api_routes, threaded
from .config import Settings, load_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("maze")

PUBLIC_PATHS = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/logout", "/api/auth/setup"}


class AuthMiddleware:
    """Charge l'utilisateur de la session et protège /api et /media."""

    def __init__(self, app: ASGIApp, db_path):
        self.app = app
        self.db_path = db_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path: str = scope["path"]
            protected = (path.startswith("/api/") or path.startswith("/media/")) and path not in PUBLIC_PATHS
            # Routes de Foundry : authentifiées par leur propre jeton (foundry.py), sans session.
            protected = protected and not path.startswith(foundry.PREFIX)
            if protected:
                try:
                    user = auth.load_session_user(self.db_path, scope.get("session", {}))
                except sqlite3.OperationalError as exc:
                    if not db.is_busy(exc):
                        raise
                    response = JSONResponse({"detail": db.BUSY_MESSAGE}, status_code=503, headers={"Retry-After": "2"})
                    await response(scope, receive, send)
                    return
                if user is None:
                    response = JSONResponse({"detail": "Authentification requise"}, status_code=401)
                    await response(scope, receive, send)
                    return
                scope.setdefault("state", {})["user"] = user
        await self.app(scope, receive, send)


class ProxyCheckMiddleware:
    """FORWARDED_ALLOW_IPS qui ne contient pas l'adresse du reverse proxy (audit M13) : une requête annoncée en
    HTTPS par un proxy dont uvicorn n'a pas cru les en-têtes arrive en http. Tous les clients ont alors l'adresse
    du proxy (limite des essais de connexion commune) et les adresses du connecteur Claude passent en http.
    Signalé une fois dans le journal, et dans Administration > Mise à jour (app.state.untrusted_proxy)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("scheme") == "http":
            state = scope["app"].state
            if getattr(state, "untrusted_proxy", None) is None:
                for name, value in scope["headers"]:
                    if name == b"x-forwarded-proto" and value.strip().lower() == b"https":
                        peer = scope["client"][0] if scope.get("client") else "?"
                        state.untrusted_proxy = peer
                        log.warning("Requête HTTPS relayée par %s, absent de FORWARDED_ALLOW_IPS : ajoutez "
                                    "FORWARDED_ALLOW_IPS=%s dans .env (README, Configuration)", peer, peer)
                        break
        await self.app(scope, receive, send)


class SchemaVersionMiddleware:
    """Audit H3 : une écriture envoyée par un onglet resté sur une autre version du schéma de l'éditeur (en-tête
    X-Maze-Version, posé par l'interface) est refusée (409, `code` « version ») ; l'interface recharge la page.
    L'en-tête est facultatif : le connecteur Claude (appels internes) et les scripts ne l'envoient pas."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            for name, value in scope["headers"]:
                if name == b"x-maze-version":
                    if value.decode("latin-1").strip() != str(SCHEMA_VERSION):
                        response = JSONResponse({"detail": STALE_CLIENT, "code": "version", "version": SCHEMA_VERSION},
                                                status_code=409)
                        await response(scope, receive, send)
                        return
                    break
        await self.app(scope, receive, send)


class RestoringMiddleware:
    """Audit M7 (contre-revue) : pendant une restauration (hub.restoring() : depuis l'administration, ou verrou de
    .restauration-en-cours tenu par la ligne de commande), toute écriture REST est refusée (503) : acceptée, elle
    serait écrite dans la base que la copie remplace, donc perdue. Lectures (GET, HEAD, OPTIONS) admises. Exceptions :
    la route de restauration elle-même, la déconnexion, et /mcp, dont les outils sont contrôlés un par un (mcp.Context.api : les
    outils de lecture continuent de marcher)."""

    # Déconnexion : n'écrit rien en base (cookie de session vidé) ; refusée, elle laisserait la session ouverte sur
    # un poste partagé, en silence (et --exact garde les sessions).
    EXEMPT = re.compile(r"^/(mcp$|api/auth/logout$|api/admin/backups/[^/]+/restore$)")

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope["type"] == "http" and scope["method"] not in ("GET", "HEAD", "OPTIONS")
                and not self.EXEMPT.match(scope["path"])):
            hub = getattr(scope["app"].state, "hub", None)
            if hub is not None and hub.restoring():
                await JSONResponse({"detail": RESTORING}, status_code=503, headers={"Retry-After": "5"})(
                    scope, receive, send)
                return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"same-origin"),
                    (b"content-security-policy",
                     b"default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                     b"script-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"),
                ])
            await send(message)

        await self.app(scope, receive, send_with_headers)


# Un seul serveur par dossier de données (audit DATA-16) : les salles du temps réel sont en mémoire, et la compaction
# d'un processus effacerait du journal les modifications reçues par un autre. Verrou fcntl.flock tenu tant que le
# serveur tourne (libéré par le système même après un arrêt brutal). La ligne de commande (python -m maze.backup,
# sauvegarde et restauration) ne le prend pas : elle reste possible pendant que Maze tourne.
LOCK_FILE = ".maze.lock"


class DataDirLocked(RuntimeError):
    """Un autre serveur Maze utilise déjà ce dossier de données."""


def lock_data_dir(data_dir: Path) -> IO[str]:
    path = data_dir / LOCK_FILE
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.seek(0)
        holder = handle.read().strip() or "?"
        handle.close()
        raise DataDirLocked(
            f"Un autre serveur Maze utilise déjà le dossier de données {data_dir} (verrou {path}, processus {holder}). "
            "Un seul serveur par dossier : pas d'uvicorn --workers 2 ni de second conteneur sur le même volume. "
            "La sauvegarde et la restauration en ligne de commande (python -m maze.backup) restent possibles "
            "pendant que Maze tourne.") from None
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def clean_leftovers(data_dir: Path) -> None:
    """Fichiers temporaires laissés dans le volume par un arrêt brutal (SIGKILL, mémoire épuisée ; audit PERF-15),
    retirés au démarrage, verrou du dossier pris (aucun autre serveur ne peut être en train de les écrire) :
    import de campagne (.import-* : images en attente, tmp*.zip : archive reçue), archive de sauvegarde en cours
    d'envoi (backups/.import-*.part), image en cours d'écriture (media/<campagne>/.<nom>.tmp), dossier de travail
    d'une restauration (.restauration-*, sauf restauration en cours en ligne de commande ; images d'une restauration
    arrêtée pendant leur remplacement : remises en place si la base en service est la base restaurée, sinon dossier
    gardé si media/ manque, backup.settle_interrupted_restores). Les .part des sauvegardes ont déjà leur ménage
    (backup.remove_abandoned_parts).
    .media-remplace-* (images d'avant une restauration interrompue, peut-être les seules) : jamais supprimé,
    signalé dans le journal et dans Administration > Sauvegardes (backup.replaced_media)."""
    found = [*data_dir.glob(".import-*"), *data_dir.glob("tmp*.zip"), *(data_dir / "backups").glob(".import-*.part"),
             *(data_dir / "media").glob(f"*/.*{TEMP_SUFFIX}")]
    if not backup.restore_in_progress(data_dir):
        # Restauration arrêtée pendant le remplacement des images : images de la base restaurée remises en place, ou
        # dossier gardé et signalé (settle_interrupted_restores). Jamais le fichier verrouillé d'une restauration
        # (RESTORE_MARKER, même motif) : une restauration commencée juste après la vérification le tient déjà, et le
        # supprimer la rendrait invisible au serveur.
        keep = backup.settle_interrupted_restores(data_dir)
        found += [p for p in data_dir.glob(".restauration-*") if p.name != backup.RESTORE_MARKER and p.name not in keep]
    for path in found:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
            log.info("Fichier temporaire d'un arrêt brutal retiré : %s", path)
        except OSError as exc:
            log.warning("Fichier temporaire %s non retiré : %s", path, exc)
    for leftover in backup.replaced_media(data_dir):
        log.warning("Images d'avant une restauration interrompue gardées dans %s (%s fichiers, rien n'est supprimé)%s",
                    leftover["path"], leftover["files"],
                    " : media/ est absent, ce sont peut-être les seules images de Maze"
                    " (docs/fr/guide.md, Restauration)" if leftover["media_missing"]
                    else " : à vérifier puis supprimer à la main (docs/fr/guide.md, Restauration)")


def create_app(cfg: Settings | None = None) -> Starlette:
    cfg = cfg or load_settings()
    lock = lock_data_dir(cfg.data_dir)
    try:
        clean_leftovers(cfg.data_dir)
        db.migrate(cfg.db_path)
        auth.bootstrap_admin(cfg.db_path, cfg.password)
        throttle.load_known(cfg.db_path)   # adresses connues des comptes (limitation des essais, migration 029)
        return _build_app(cfg, lock)
    except BaseException:
        lock.close()
        raise


def _build_app(cfg: Settings, lock: IO[str]) -> Starlette:
    media_root = cfg.media_dir.resolve()

    @threaded
    def media(request: Request) -> Response:
        target = (media_root / request.path_params["path"]).resolve()
        # Extensions des fichiers produits par imaging.py (mediafiles : WebP, et JPEG des grands fonds de cartes).
        if media_root not in target.parents or target.suffix not in MEDIA_TYPES or not target.is_file():
            raise HTTPException(404, "Fichier introuvable")
        # Les fichiers sont rangés par campagne : media/<id campagne>/...
        campaign = target.relative_to(media_root).parts[0]
        with db.transaction(cfg.db_path) as conn:
            allowed = campaign.isdigit() and auth.can_access(conn, auth.current_user(request), int(campaign))
        if not allowed:
            raise HTTPException(404, "Fichier introuvable")
        # Les noms de fichiers sont uniques (uuid) : cache long sans risque.
        return FileResponse(target, media_type=MEDIA_TYPES[target.suffix],
                            headers={"Cache-Control": "private, max-age=31536000, immutable"})

    index_file = cfg.static_dir / "index.html"

    async def spa(request: Request) -> Response:
        path = request.path_params.get("path", "")
        if path.startswith("api/"):
            raise HTTPException(404, "Route inconnue")
        candidate = (cfg.static_dir / path).resolve()
        if path and cfg.static_dir in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        if index_file.is_file():
            return FileResponse(index_file, headers={"Cache-Control": "no-cache"})
        return HTMLResponse("<p>Interface non construite : lancez le build du frontend.</p>", 503)

    hub = Hub(cfg.db_path, max_per_user=cfg.live_max_per_user, max_total=cfg.live_max_total)
    routes = [
        WebSocketRoute("/api/documents/{id:int}/live", hub.endpoint),
        *api_routes,
        *admin_routes,
        *document_routes,
        *transfer_routes,
        *comment_routes,
        *search_routes,
        *template_routes,
        *place_routes,
        *timeline_routes,
        *scenemap_routes,
        *version_routes,
        *entry_history_routes,
        *connector_routes,
        *foundry.routes,
        *media_admin_routes,
        *image_copy_routes,
        *map_routes,
        *oauth_routes,
        *mcp_routes,
        Route("/media/{path:path}", media),
    ]
    assets_dir = cfg.static_dir / "assets"
    if assets_dir.is_dir():
        routes.append(Mount("/assets", StaticFiles(directory=assets_dir), name="assets"))
    routes.append(Route("/{path:path}", spa))

    VALUE_ERROR = "Value error, "   # préfixe de Pydantic devant le message d'un validateur (ValueError)

    # Messages traduits à la réponse (maze.i18n) : langue du compte, puis Accept-Language, puis MAZE_DEFAULT_LOCALE.
    # `detail` peut être un i18n.msg(...) (modèle et paramètres) ; marque posée pour que I18nMiddleware n'y revienne pas.
    # Chaîne ordinaire : clé exacte, sinon modèle reconnu (message d'une exception composé sans msg, par sécurité).
    def translated(request: Request, detail):
        request.scope[i18n.TRANSLATED] = True
        locale = i18n.request_locale(request.scope)
        if isinstance(detail, str) and not isinstance(detail, i18n.Msg):
            return i18n.translate_text(detail, locale)
        return i18n.translate(detail, locale)

    async def http_error(request: Request, exc: HTTPException) -> Response:
        return JSONResponse({"detail": translated(request, exc.detail)}, status_code=exc.status_code,
                            headers=exc.headers)

    async def validation_error(request: Request, exc: RequestValidationError) -> Response:
        # Message d'en-tête ; l'interface compose le détail des champs elle-même (api.js, formatDetail), sauf le texte
        # des validateurs de schemas.py (« Value error, Commentaire vide »), qu'elle affiche tel quel : traduit ici.
        locale = i18n.request_locale(request.scope)
        errors = exc.errors
        if locale != i18n.SOURCE_LOCALE:
            errors = [{**e, "msg": VALUE_ERROR + i18n.translate_text(e["msg"][len(VALUE_ERROR):], locale)}
                      if isinstance(e.get("msg"), str) and e["msg"].startswith(VALUE_ERROR) else e for e in errors]
        return JSONResponse({"detail": translated(request, "Données invalides"), "errors": errors},
                            status_code=422)

    async def database_error(request: Request, exc: sqlite3.OperationalError) -> Response:
        # Verrou d'écriture toujours pris après l'attente (import, suppression ou restauration en cours, audit M15) :
        # « réessayez » plutôt qu'une erreur 500. Toute autre erreur SQLite reste une erreur interne.
        if db.is_busy(exc):
            log.warning("%s %s : base occupée (%s)", request.method, request.url.path, exc)
            return JSONResponse({"detail": db.BUSY_MESSAGE}, status_code=503, headers={"Retry-After": "2"})
        raise exc

    async def overflow_error(request: Request, exc: OverflowError) -> Response:
        # Nombre trop grand pour SQLite (identifiant dans l'adresse ou champ numérique).
        return JSONResponse({"detail": "Nombre hors limites"}, status_code=400)

    middleware = [
        # Niveau 6 : presque aussi compact que 9 pour deux fois moins de CPU (graphe de 1000 PNJ : 17 ms au lieu de 35).
        Middleware(GZipMiddleware, minimum_size=1024, compresslevel=6),
        # Réponses d'erreur JSON écrites hors des gestionnaires d'erreurs : `detail` traduit (corps lu avant compression).
        Middleware(i18n.I18nMiddleware),
        # CORS des routes lues par le module Foundry (préflight compris, avant la session).
        Middleware(foundry.FoundryCors),
        Middleware(SecurityHeadersMiddleware),
        # Taille des corps de requête, coupée à la lecture (413) : avant toute autre lecture (audit M19).
        Middleware(limits.BodyLimitMiddleware, max_upload_mb=cfg.max_upload_mb, max_import_mb=MAX_IMPORT_MB,
                   mcp_max=MCP_MAX_BODY),
        Middleware(ProxyCheckMiddleware),
        Middleware(SchemaVersionMiddleware),
        Middleware(RestoringMiddleware),
        Middleware(
            SessionMiddleware,
            secret_key=cfg.secret_key,
            session_cookie="maze_session",
            max_age=cfg.session_days * 86400,
            same_site="lax",
            https_only=cfg.cookie_secure,
        ),
        Middleware(AuthMiddleware, db_path=cfg.db_path),
    ]

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        try:
            yield
        finally:
            lock.close()   # verrou du dossier de données rendu à l'arrêt (DATA-16)

    app = Starlette(
        routes=routes,
        middleware=middleware,
        exception_handlers={HTTPException: http_error, RequestValidationError: validation_error,
                            OverflowError: overflow_error, sqlite3.OperationalError: database_error},
        lifespan=lifespan,
    )
    app.state.settings = cfg
    app.state.hub = hub
    app.state.data_lock = lock
    app.state.untrusted_proxy = None
    log.info("Maze prêt — données : %s", cfg.data_dir)
    return app


def app_factory() -> Starlette:
    """Point d'entrée uvicorn : `uvicorn maze.app:app_factory --factory`."""
    return create_app()
