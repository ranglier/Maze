"""Comptes : hachage des mots de passe, utilisateur courant, droits sur les campagnes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request

from . import db

log = logging.getLogger("maze")

# scrypt : N=2^15, r=8, p=1 (~32 Mo de mémoire par vérification).
_N, _R, _P = 2**15, 8, 1
_MAXMEM = 64 * 1024 * 1024


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r),
                                p=int(p), maxmem=_MAXMEM, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# Empreinte factice : même coût de calcul quand l'identifiant n'existe pas.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def verify_dummy(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


# --- Utilisateur courant -----------------------------------------------------

PUBLIC_USER_FIELDS = ("id", "username", "display_name", "role")


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {k: user[k] for k in PUBLIC_USER_FIELDS}


def load_session_user(db_path: Path, session: dict[str, Any]) -> dict[str, Any] | None:
    user_id = session.get("uid")
    if not user_id:
        return None
    # Appelée dans la boucle d'événements à chaque requête : lecture (WAL, sans attendre les écritures), attente
    # courte au pire (audit M15) ; db.is_busy -> 503 « réessayez » (app.AuthMiddleware).
    conn = db.connect(db_path, db.LOOP_TIMEOUT)
    try:
        user = db.row_to_dict(conn.execute(
            "SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)).fetchone())
    finally:
        conn.close()
    if user is None or user["session_epoch"] != session.get("epoch"):
        return None
    # Clé aléatoire du compte : un identifiant réutilisé ne réactive pas une ancienne session.
    if not user.get("session_key") or not hmac.compare_digest(user["session_key"], str(session.get("key", ""))):
        return None
    return user


def current_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Authentification requise")
    return user


def require_admin(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if user["role"] != "admin":
        raise HTTPException(403, "Réservé aux administrateurs")
    return user


def visible_campaign_ids(conn: sqlite3.Connection, user: dict[str, Any]) -> list[int] | None:
    """Campagnes visibles par ce compte, triées ; None : toutes (administrateur). Seule écriture de la règle
    (audit QUAL-6) : can_access, GET /api/campaigns, la recherche et l'outil list_campaigns du connecteur en
    dérivent. Appels du connecteur Claude (connector.acting_user) : uniquement les campagnes qui lui sont
    ouvertes (mcp_scope), même au nom d'un administrateur."""
    scope = user.get("mcp_scope")
    if scope is not None:
        return sorted(scope)
    if user["role"] == "admin":
        return None
    return [r[0] for r in conn.execute(
        "SELECT campaign_id FROM campaign_members WHERE user_id = ? ORDER BY campaign_id", (user["id"],))]


def can_access(conn: sqlite3.Connection, user: dict[str, Any], campaign_id: int | None) -> bool:
    if campaign_id is None:
        return False
    visible = visible_campaign_ids(conn, user)
    return visible is None or campaign_id in visible


# --- Initialisation ------------------------------------------------------------

def count_users(db_path: Path) -> int:
    conn = db.connect(db_path, db.LOOP_TIMEOUT)
    try:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()


def bootstrap_admin(db_path: Path, password: str | None) -> None:
    """Sans aucun compte, crée « admin » avec le mot de passe historique (MAZE_PASSWORD)."""
    if not password or count_users(db_path):
        return
    with db.transaction(db_path) as conn:
        db.insert(conn, "users", {
            "username": "admin", "display_name": "Administrateur",
            "password_hash": hash_password(password), "role": "admin",
        })
    log.info("Compte « admin » créé avec le mot de passe MAZE_PASSWORD.")
