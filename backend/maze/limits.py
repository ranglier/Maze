"""Taille maximale des corps de requête (audit M19).

Le middleware coupe à la lecture : un corps annoncé trop gros (Content-Length) est refusé avant d'être lu,
un corps envoyé par morceaux est compté au fil de la lecture et refusé dès qu'il dépasse ; réponse 413.
Limites (de la plus spécifique à la plus générale) :
- imports d'archives (campagne, sauvegarde) : MAZE_MAX_IMPORT_MB ;
- envois d'images (multipart) : MAZE_MAX_UPLOAD_MB par fichier, fois le nombre de fichiers admis, plus 1 Mio ;
- textes longs (fiches rédigées, versions, modèles, PNJ, factions, lieux) : 16 Mio ;
- /mcp : mcp.MAX_BODY (4 Mio) ;
- routes publiques (/api/auth/*, /oauth/*) : 64 Kio ;
- toute autre route : 2 Mio (JSON).
Le WebSocket du temps réel n'est pas concerné : uvicorn limite chaque message (--ws-max-size, 16 Mio).
Les appels internes du connecteur Claude (Context.api) passent par le routeur, pas par ce middleware :
leur taille est déjà bornée par celle de la requête /mcp.
"""

from __future__ import annotations

import re

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .i18n import msg

KIB = 1024
MIB = 1024 * KIB
JSON_LIMIT = 2 * MIB
TEXT_LIMIT = 16 * MIB
PUBLIC_LIMIT = 64 * KIB
GALLERY_FILES = 10      # images d'un envoi vers la galerie d'un PNJ, d'une faction ou d'un lieu (api._upload_images)
LIBRARY_FILES = 20      # images d'un envoi vers la bibliothèque (media_admin.MAX_FILES)

IMPORT_ROUTES = {"/api/campaigns/import", "/api/admin/backups/import"}
UPLOAD_ROUTES = [
    (re.compile(r"/api/(npcs|factions|places)/\d+/images"), GALLERY_FILES),
    (re.compile(r"/api/admin/campaigns/\d+/library"), LIBRARY_FILES),
    (re.compile(r"/api/documents/\d+/images"), 1),
    (re.compile(r"/api/campaigns/\d+/maps"), 1),
    (re.compile(r"/api/maps/\d+/image"), 1),
    (re.compile(r"/api/place-kinds/\d+/image"), 1),
]
TEXT_ROUTES = re.compile(
    r"/api/(campaigns/\d+/(documents|npcs|factions|places)|documents/\d+(/versions(/\d+(/restore)?)?)?"
    r"|templates(/\d+)?|(npcs|factions|places)/\d+)")


def limit_for(path: str, max_upload_mb: int, max_import_mb: int, mcp_max: int) -> int:
    if path in IMPORT_ROUTES:
        return max_import_mb * MIB
    for pattern, files in UPLOAD_ROUTES:
        if pattern.fullmatch(path):
            return files * max_upload_mb * MIB + MIB
    if TEXT_ROUTES.fullmatch(path):
        return TEXT_LIMIT
    if path == "/mcp":
        return mcp_max
    if path.startswith(("/api/auth/", "/oauth/")):
        return PUBLIC_LIMIT
    return JSON_LIMIT


def _readable(size: int) -> str:
    return f"{size // MIB} Mo" if size >= MIB else f"{size // KIB} Ko"


class BodyTooLarge(HTTPException):
    def __init__(self, limit: int):
        # msg : traduit à la réponse (maze.i18n), y compris quand BodyLimitMiddleware écrit lui-même la réponse.
        super().__init__(413, msg("Requête trop volumineuse (max {size})", size=_readable(limit)))


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_upload_mb: int, max_import_mb: int, mcp_max: int):
        self.app = app
        self.max_upload_mb = max_upload_mb
        self.max_import_mb = max_import_mb
        self.mcp_max = mcp_max

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = limit_for(scope["path"], self.max_upload_mb, self.max_import_mb, self.mcp_max)
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > limit:
                    response = JSONResponse({"detail": BodyTooLarge(limit).detail}, status_code=413,
                                            headers={"Connection": "close"})
                    await response(scope, receive, send)
                    return
        received = 0

        async def limited() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # Levée dans la route qui lit le corps : convertie en réponse 413 par Starlette.
                    raise BodyTooLarge(limit)
            return message

        await self.app(scope, limited, send)
