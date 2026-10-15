"""Connecteur Claude : réglages, droits par campagne, jetons et journal (migration 015).

Claude se connecte au serveur MCP de Maze (/mcp, voir mcp.py) avec un jeton OAuth (oauth.py), délivré
après qu'un administrateur a saisi son mot de passe. Il agit alors au nom de cet administrateur, mais
seulement sur les campagnes ouvertes ici (lecture seule, ou lecture et écriture), et seulement tant que
l'accès est activé : le bouton de l'administration suffit à tout suspendre.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, db
from .api import parse_body, settings, threaded, tx
from .schemas import ConnectorUpdate

ACCESS_SECONDS = 3600              # jeton d'accès : 1 h, renouvelé par Claude avec le jeton de rafraîchissement
REFRESH_SECONDS = 60 * 86400       # jeton de rafraîchissement : 60 jours, remplacé à chaque usage
KEEP_LOG = 2000                    # lignes du journal conservées

# Adresses de retour acceptées après l'autorisation : claude.ai, plus les adresses locales de
# Claude Code (application native, RFC 8252 : port libre sur l'interface de bouclage).
CLAUDE_CALLBACKS = ("https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback")
LOOPBACK = re.compile(r"http://(localhost|127\.0\.0\.1|\[::1\])(:\d{1,5})?/[\w./-]*")

# Codes d'autorisation en attente d'échange (oauth.py) : {empreinte: détails}. En mémoire (un seul
# processus) ; vidés quand l'administration déconnecte Claude ou change le secret.
PENDING_CODES: dict[str, dict[str, Any]] = {}


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_base(request: Request) -> str:
    """Adresse publique de Maze, sans barre finale (émetteur OAuth et adresse du serveur MCP)."""
    configured = settings(request).public_url
    if configured:
        return configured
    return f"{request.url.scheme}://{request.url.netloc}"


def redirect_allowed(uri: str) -> bool:
    return uri in CLAUDE_CALLBACKS or bool(LOOPBACK.fullmatch(uri))


# --- Réglages et droits ------------------------------------------------------------------------

def load_settings_row(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM mcp_settings WHERE id = 1").fetchone()
    if row is None:  # base restaurée d'une ancienne version puis migrée : la ligne existe toujours
        if conn.execute("PRAGMA query_only").fetchone()[0]:
            # Transaction de lecture (route GET, outil du connecteur) : ligne créée à part, dans une transaction
            # d'écriture ; relue là (l'instantané de la lecture ne la verrait pas).
            path = next(r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main")
            with db.transaction(Path(path)) as write:
                write.execute("INSERT OR IGNORE INTO mcp_settings (id) VALUES (1)")
                row = write.execute("SELECT * FROM mcp_settings WHERE id = 1").fetchone()
        else:
            conn.execute("INSERT OR IGNORE INTO mcp_settings (id) VALUES (1)")
            row = conn.execute("SELECT * FROM mcp_settings WHERE id = 1").fetchone()
    out = dict(row)
    out["enabled"] = bool(out["enabled"])
    return out


def grants(conn: sqlite3.Connection) -> dict[int, str]:
    """Campagnes ouvertes à Claude : {id: "read" | "write"}."""
    return {r[0]: r[1] for r in conn.execute("SELECT campaign_id, mode FROM mcp_grants")}


def check_client(conn: sqlite3.Connection, client_id: str, secret: str | None) -> bool:
    """Identifiant du client OAuth, et son secret quand il est fourni (vérifié au point de jeton)."""
    row = load_settings_row(conn)
    if not row["client_id"] or not hmac.compare_digest(row["client_id"].encode(), (client_id or "").encode()):
        return False
    if secret is None:
        return True
    return hmac.compare_digest(row["secret_hash"].encode(), digest(secret).encode())


def valid_admin(conn: sqlite3.Connection, user_id: int, session_key: str) -> dict[str, Any] | None:
    """Administrateur toujours actif, dont la clé de session n'a pas changé depuis l'autorisation."""
    user = db.row_to_dict(conn.execute(
        "SELECT * FROM users WHERE id = ? AND active = 1 AND role = 'admin'", (user_id,)).fetchone())
    if user is None or not user["session_key"] or not hmac.compare_digest(user["session_key"].encode(),
                                                                          session_key.encode()):
        return None
    return user


# --- Jetons ------------------------------------------------------------------------------------

def issue_tokens(conn: sqlite3.Connection, user: dict[str, Any], authorized_at: str | None = None) -> dict[str, Any]:
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = int(time.time())
    row = {
        "user_id": user["id"], "session_key": user["session_key"],
        "access_hash": digest(access), "refresh_hash": digest(refresh),
        "access_expires_at": now + ACCESS_SECONDS, "refresh_expires_at": now + REFRESH_SECONDS,
    }
    if authorized_at:
        row["authorized_at"] = authorized_at
    db.insert(conn, "mcp_tokens", row)
    # Jetons expirés : ménage au passage.
    conn.execute("DELETE FROM mcp_tokens WHERE refresh_expires_at < ?", (now,))
    return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_SECONDS,
            "refresh_token": refresh, "scope": "maze"}


def refresh_tokens(conn: sqlite3.Connection, refresh: str) -> dict[str, Any] | None:
    """Échange un jeton de rafraîchissement contre une nouvelle paire (l'ancienne est supprimée)."""
    row = conn.execute("SELECT * FROM mcp_tokens WHERE refresh_hash = ?", (digest(refresh),)).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM mcp_tokens WHERE id = ?", (row["id"],))
    if row["refresh_expires_at"] < time.time():
        return None
    user = valid_admin(conn, row["user_id"], row["session_key"])
    if user is None:
        return None
    return issue_tokens(conn, user, row["authorized_at"])


def authenticate(conn: sqlite3.Connection, access: str) -> dict[str, Any] | None:
    """Administrateur au nom duquel agit le porteur de ce jeton d'accès, ou None."""
    row = conn.execute("SELECT * FROM mcp_tokens WHERE access_hash = ?", (digest(access),)).fetchone()
    if row is None or row["access_expires_at"] < time.time():
        return None
    user = valid_admin(conn, row["user_id"], row["session_key"])
    if user is not None and not conn.execute("PRAGMA query_only").fetchone()[0]:
        touch_token(conn, access)
    return user


def touch_token(conn: sqlite3.Connection, access: str) -> None:
    """Date de dernier usage du jeton (écrite à part quand le jeton est vérifié en lecture seule, /mcp)."""
    conn.execute("UPDATE mcp_tokens SET last_used_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE access_hash = ?",
                 (digest(access),))


def acting_user(user: dict[str, Any], scope: set[int]) -> dict[str, Any]:
    """Identité utilisée pour les appels de Claude : celle de l'administrateur, restreinte aux campagnes
    `scope` (auth.can_access) et signée « Claude » (commentaires, historique des versions)."""
    return {**user, "role": "mj", "display_name": "Claude", "mcp_scope": frozenset(scope)}


# --- Journal -------------------------------------------------------------------------------------

def log_action(conn: sqlite3.Connection, user_id: int | None, tool: str, campaign_id: int | None,
               target: str, ok: bool, detail: str = "") -> None:
    # Nom de la campagne gardé avec l'action : le journal survit aux restaurations, pas forcément le numéro.
    name = conn.execute("SELECT name FROM campaigns WHERE id = ?", (campaign_id,)).fetchone() if campaign_id else None
    db.insert(conn, "mcp_log", {"user_id": user_id, "tool": tool[:60], "campaign_id": campaign_id,
                                "campaign_name": name[0] if name else "",
                                "target": target[:200], "ok": int(ok), "detail": detail[:500]})
    conn.execute("DELETE FROM mcp_log WHERE id <= (SELECT MAX(id) FROM mcp_log) - ?", (KEEP_LOG,))


# --- Administration --------------------------------------------------------------------------------

def _state(request: Request, conn: sqlite3.Connection) -> dict[str, Any]:
    row = load_settings_row(conn)
    base = public_base(request)
    sessions = db.rows_to_dicts(conn.execute(
        """SELECT t.id, t.authorized_at, t.last_used_at, u.username, COALESCE(NULLIF(u.display_name, ''), u.username) AS user
             FROM mcp_tokens t JOIN users u ON u.id = t.user_id
            WHERE t.refresh_expires_at >= ? ORDER BY t.id DESC""", (int(time.time()),)).fetchall())
    log = db.rows_to_dicts(conn.execute(
        """SELECT l.id, l.at, l.tool, l.campaign_id, COALESCE(NULLIF(l.campaign_name, ''), c.name) AS campaign,
                  l.target, l.ok, l.detail
             FROM mcp_log l LEFT JOIN campaigns c ON c.id = l.campaign_id
            ORDER BY l.id DESC LIMIT 100""").fetchall())
    for entry in log:
        entry["ok"] = bool(entry["ok"])
    return {
        "enabled": row["enabled"],
        "client_id": row["client_id"],
        "has_secret": bool(row["secret_hash"]),
        "updated_at": row["updated_at"],
        "mcp_url": f"{base}/mcp",
        "callbacks": list(CLAUDE_CALLBACKS),
        "grants": [{"campaign_id": cid, "mode": mode} for cid, mode in sorted(grants(conn).items())],
        "sessions": sessions,
        "log": log,
    }


@threaded
def get_connector(request: Request) -> Response:
    auth.require_admin(request)
    with tx(request) as conn:
        return JSONResponse(_state(request, conn))


def _require_json(request: Request) -> None:
    """Actions sans corps utile : un en-tête JSON exigé, qu'un formulaire ou une requête simple d'un autre
    site (même sous-domaine, cookie SameSite=Lax envoyé) ne peut pas poser."""
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(415, "Requête JSON attendue")


@threaded
def update_connector(request: Request) -> Response:
    auth.require_admin(request)
    data = parse_body(request, ConnectorUpdate)
    with tx(request) as conn:
        if data.enabled is not None:
            conn.execute("UPDATE mcp_settings SET enabled = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                         "WHERE id = 1", (int(data.enabled),))
        if data.grants is not None:
            wanted = {g.campaign_id: g.mode for g in data.grants if g.mode != "none"}
            known = {r[0] for r in conn.execute("SELECT id FROM campaigns")}
            if not set(wanted) <= known:
                raise HTTPException(400, "Campagne inconnue")
            conn.execute("DELETE FROM mcp_grants")
            conn.executemany("INSERT INTO mcp_grants (campaign_id, mode) VALUES (?, ?)", sorted(wanted.items()))
        if data.grant is not None:
            if not conn.execute("SELECT 1 FROM campaigns WHERE id = ?", (data.grant.campaign_id,)).fetchone():
                raise HTTPException(400, "Campagne inconnue")
            conn.execute("DELETE FROM mcp_grants WHERE campaign_id = ?", (data.grant.campaign_id,))
            if data.grant.mode != "none":
                conn.execute("INSERT INTO mcp_grants (campaign_id, mode) VALUES (?, ?)",
                             (data.grant.campaign_id, data.grant.mode))
        out = _state(request, conn)
    return JSONResponse(out)


@threaded
def new_credentials(request: Request) -> Response:
    """Identifiant et secret du client OAuth à saisir dans claude.ai. Le secret n'est montré qu'ici :
    en générer un nouveau déconnecte Claude (il faudra le saisir à nouveau dans claude.ai)."""
    auth.require_admin(request)
    _require_json(request)
    client_id = f"maze-{secrets.token_hex(8)}"
    secret = secrets.token_urlsafe(32)
    with tx(request) as conn:
        conn.execute("UPDATE mcp_settings SET client_id = ?, secret_hash = ?, "
                     "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = 1", (client_id, digest(secret)))
        conn.execute("DELETE FROM mcp_tokens")
        out = _state(request, conn)
    PENDING_CODES.clear()
    return JSONResponse({**out, "client_secret": secret}, status_code=201)


@threaded
def disconnect(request: Request) -> Response:
    """Révoque tous les jetons : Claude devra repasser par l'écran d'autorisation."""
    auth.require_admin(request)
    _require_json(request)
    with tx(request) as conn:
        conn.execute("DELETE FROM mcp_tokens")
        out = _state(request, conn)
    PENDING_CODES.clear()
    return JSONResponse(out)


routes = [
    Route("/api/admin/claude", get_connector),
    Route("/api/admin/claude", update_connector, methods=["PUT"]),
    Route("/api/admin/claude/credentials", new_credentials, methods=["POST"]),
    Route("/api/admin/claude/disconnect", disconnect, methods=["POST"]),
]
