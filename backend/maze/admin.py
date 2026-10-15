"""API d'administration : comptes, campagnes, sauvegardes, mise à jour (réservée aux admins)."""

from __future__ import annotations

import fcntl
import logging
import os
import time
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from . import auth, backup, db, throttle
from .api import _open_session, get_or_404, in_tx, parse, parse_body, path_id, settings, threaded, tx
from .schemas import MembersUpdate, UserCreate, UserUpdate

USER_FIELDS = "id, username, display_name, role, active, created_at, last_login_at"
log = logging.getLogger("maze")
MAX_IMPORT_MB = int(os.environ.get("MAZE_MAX_IMPORT_MB", "4096"))
# Au-delà, la dernière sauvegarde quotidienne (timer maze-backup) est signalée comme en retard dans l'administration.
DAILY_STALE_HOURS = 48


# --- Comptes -------------------------------------------------------------------

def _recheck_live(request: Request) -> None:
    """Coupe les sessions d'édition en temps réel de qui vient de perdre l'accès."""
    hub = getattr(request.app.state, "hub", None)
    if hub is not None:
        hub.recheck()


def _user_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    users = db.rows_to_dicts(conn.execute(f"SELECT {USER_FIELDS} FROM users ORDER BY username COLLATE NOCASE"))
    members: dict[int, list[int]] = {}
    for row in conn.execute("SELECT user_id, campaign_id FROM campaign_members"):
        members.setdefault(row[0], []).append(row[1])
    for u in users:
        u["active"] = bool(u["active"])
        u["campaign_ids"] = sorted(members.get(u["id"], []))
    return users


def _user_out(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    return next(u for u in _user_rows(conn) if u["id"] == user_id)


def _active_admins(conn: sqlite3.Connection, excluding: int | None = None) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id <> ?",
        (excluding or 0,)).fetchone()[0]


def _set_memberships(conn: sqlite3.Connection, user_id: int, campaign_ids: list[int]) -> None:
    for cid in set(campaign_ids):
        get_or_404(conn, "campaigns", cid, "Campagne")
        conn.execute("INSERT OR IGNORE INTO campaign_members (campaign_id, user_id) VALUES (?, ?)", (cid, user_id))


@threaded
def list_users(request: Request) -> Response:
    auth.require_admin(request)
    with tx(request) as conn:
        return JSONResponse(_user_rows(conn))


# Comptes créés ou modifiés (contre-revue du lot 4) : scrypt (32 Mo, des dizaines de ms) calculé dans le pool commun
# d'anyio, pas dans un fil d'écriture de SQLite (db.run_db), qu'il occupait sans même avoir besoin du verrou ; seule
# la transaction passe ensuite par les fils d'écriture (in_tx).
async def create_user(request: Request) -> Response:
    auth.require_admin(request)
    data = await parse(request, UserCreate)
    password_hash = await run_in_threadpool(auth.hash_password, data.password)

    def work(conn: sqlite3.Connection) -> dict[str, Any]:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (data.username,)).fetchone():
            raise HTTPException(409, f"L'identifiant « {data.username} » est déjà utilisé")
        uid = db.insert(conn, "users", {
            "username": data.username, "display_name": data.display_name or data.username,
            "password_hash": password_hash, "role": data.role,
        })
        _set_memberships(conn, uid, data.campaign_ids)
        return _user_out(conn, uid)
    return JSONResponse(await in_tx(request, work), status_code=201)


async def update_user(request: Request) -> Response:
    me = auth.require_admin(request)
    uid = path_id(request)
    data = await parse(request, UserUpdate)
    values = data.model_dump(exclude_unset=True, exclude={"password"})
    if data.password:
        values["password_hash"] = await run_in_threadpool(auth.hash_password, data.password)
    out = await in_tx(request, lambda conn: _update_user(conn, me, uid, values))
    _recheck_live(request)
    return JSONResponse(out)


def _update_user(conn: sqlite3.Connection, me: dict[str, Any], uid: int, values: dict[str, Any]) -> dict[str, Any]:
    target = get_or_404(conn, "users", uid, "Compte")
    loses_admin = target["role"] == "admin" and target["active"] and (
        values.get("role", "admin") != "admin" or values.get("active", True) is False)
    if uid == me["id"] and loses_admin:
        raise HTTPException(400, "Vous ne pouvez pas retirer vos propres droits d'administrateur")
    if loses_admin and _active_admins(conn, excluding=uid) == 0:
        raise HTTPException(400, "Il doit rester au moins un administrateur actif")
    assignments = [f"{k} = ?" for k in values]
    params = [int(v) if isinstance(v, bool) else v for v in values.values()]
    if "password_hash" in values or values.get("active") is False:
        assignments.append("session_epoch = session_epoch + 1")  # déconnecte ses sessions
    if assignments:
        conn.execute(f"UPDATE users SET {', '.join(assignments)} WHERE id = ?", [*params, uid])
    if values.get("role", "admin") != "admin":
        # Plus administrateur : le connecteur Claude qu'il avait autorisé est déconnecté.
        conn.execute("DELETE FROM mcp_tokens WHERE user_id = ?", (uid,))
    if values.get("role", target["role"]) != "admin" or not values.get("active", bool(target["active"])):
        # Plus administrateur actif : les jetons Foundry qu'il a créés sont révoqués (droits en cascade).
        conn.execute("DELETE FROM foundry_tokens WHERE created_by = ?", (uid,))
    return _user_out(conn, uid)


@threaded
def delete_user(request: Request) -> Response:
    me = auth.require_admin(request)
    uid = path_id(request)
    with tx(request) as conn:
        target = get_or_404(conn, "users", uid, "Compte")
        if uid == me["id"]:
            raise HTTPException(400, "Vous ne pouvez pas supprimer votre propre compte")
        if target["role"] == "admin" and _active_admins(conn, excluding=uid) == 0:
            raise HTTPException(400, "Il doit rester au moins un administrateur actif")
        conn.execute("DELETE FROM foundry_tokens WHERE created_by = ?", (uid,))  # sinon gardés sans créateur
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))   # adresses connues : ON DELETE CASCADE
    throttle.forget_account(target["username"])
    _recheck_live(request)
    return Response(status_code=204)


# --- Campagnes -----------------------------------------------------------------

@threaded
def list_all_campaigns(request: Request) -> Response:
    auth.require_admin(request)
    with tx(request) as conn:
        campaigns = db.rows_to_dicts(conn.execute(
            """SELECT c.*,
                      (SELECT COUNT(*) FROM npcs n WHERE n.campaign_id = c.id) AS npc_count,
                      (SELECT COUNT(*) FROM factions f WHERE f.campaign_id = c.id) AS faction_count,
                      (SELECT COUNT(*) FROM images i WHERE i.campaign_id = c.id) AS image_count
                 FROM campaigns c ORDER BY c.name COLLATE NOCASE"""))
        members: dict[int, list[int]] = {}
        for row in conn.execute("SELECT campaign_id, user_id FROM campaign_members"):
            members.setdefault(row[0], []).append(row[1])
    for c in campaigns:
        c["member_ids"] = sorted(members.get(c["id"], []))
    return JSONResponse(campaigns)


@threaded
def set_campaign_members(request: Request) -> Response:
    auth.require_admin(request)
    cid = path_id(request)
    data = parse_body(request, MembersUpdate)
    with tx(request) as conn:
        get_or_404(conn, "campaigns", cid, "Campagne")
        for uid in set(data.user_ids):
            get_or_404(conn, "users", uid, "Compte")
        conn.execute("DELETE FROM campaign_members WHERE campaign_id = ?", (cid,))
        conn.executemany("INSERT INTO campaign_members (campaign_id, user_id) VALUES (?, ?)",
                         [(cid, uid) for uid in set(data.user_ids)])
    _recheck_live(request)
    return JSONResponse({"campaign_id": cid, "member_ids": sorted(set(data.user_ids))})


# --- Sauvegardes -----------------------------------------------------------------

def _backup_dir(request: Request) -> Path:
    path = settings(request).data_dir / "backups"
    path.mkdir(exist_ok=True)
    return path


def _backup_entry(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "size": stat.st_size,
        "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _backup_path(request: Request) -> Path:
    name = request.path_params["name"]
    if not backup.ARCHIVE_NAME.match(name):
        raise HTTPException(404, "Sauvegarde introuvable")
    path = _backup_dir(request) / name
    if not path.is_file():
        raise HTTPException(404, "Sauvegarde introuvable")
    return path


async def list_backups(request: Request) -> Response:
    auth.require_admin(request)
    files = sorted(_backup_dir(request).glob(backup.ARCHIVE_PATTERN), key=backup.sort_key, reverse=True)
    return JSONResponse([_backup_entry(p) for p in files])


@threaded
def backups_usage(request: Request) -> Response:
    """Place prise par les archives et espace libre du volume (Administration > Sauvegardes)."""
    auth.require_admin(request)
    cfg = settings(request)
    target = _backup_dir(request)

    def measure() -> dict[str, Any]:
        archives = list(target.glob(backup.ARCHIVE_PATTERN))
        daily = sorted((p for p in archives if backup.archive_nature(p.name) == ""), key=backup.sort_key)
        last = daily[-1].stat().st_mtime if daily else None
        return {"count": len(archives), "total": sum(p.stat().st_size for p in archives),
                "free": backup.free_space(target), "latest": backup.latest_size(cfg),
                "needed": backup.space_needed(cfg), "keep": backup.keep_counts(),
                # Dernière sauvegarde quotidienne (sans suffixe, celle du timer quotidien) : un arrêt silencieux doit se voir.
                "last_daily": _backup_entry(daily[-1])["created_at"] if daily else None,
                "daily_stale": last is None or time.time() - last > DAILY_STALE_HOURS * 3600,
                # Images d'avant une restauration interrompue (.media-remplace-*), jamais supprimées (PERF-15).
                "replaced_media": [{k: v for k, v in m.items() if k != "path"}
                                   for m in backup.replaced_media(cfg.data_dir)],
                # Images d'une restauration interrompue, gardées faute de base restaurée (contre-revue du lot 6).
                "interrupted_restores": [{k: v for k, v in m.items() if k != "path"}
                                         for m in backup.interrupted_restores(cfg.data_dir)]}
    return JSONResponse(measure())


async def create_backup(request: Request) -> Response:
    auth.require_admin(request)
    # Copie de la base (API de sauvegarde de SQLite, sans verrou d'écriture) et des images : pool commun d'anyio, pas
    # un fil d'écriture de db.run_db, qu'elle occupait pendant toute la sauvegarde (contre-revue du lot 4).
    try:
        path = await run_in_threadpool(backup.backup, None, settings(request), "manuelle")
    except backup.SpaceError as exc:
        raise HTTPException(507, str(exc)) from exc
    return JSONResponse(_backup_entry(path), status_code=201)


async def download_backup(request: Request) -> Response:
    auth.require_admin(request)
    path = _backup_path(request)
    return FileResponse(path, filename=path.name, media_type="application/gzip")


async def delete_backup(request: Request) -> Response:
    auth.require_admin(request)
    _backup_path(request).unlink()
    return Response(status_code=204)


async def import_backup(request: Request) -> Response:
    """Dépose une archive envoyée depuis le navigateur (corps brut, .tar.gz) parmi les sauvegardes."""
    auth.require_admin(request)
    target = _backup_dir(request)
    limit = MAX_IMPORT_MB * 1024 * 1024
    partial = target / f".import-{os.getpid()}-{id(request)}.part"
    size = 0
    try:
        with partial.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Archive trop volumineuse (max {MAX_IMPORT_MB} Mo)")
                await run_in_threadpool(out.write, chunk)
        if size == 0:
            raise HTTPException(400, "Aucun fichier reçu")
        try:
            info = await run_in_threadpool(backup.inspect_archive, partial)
        except backup.ArchiveError as exc:
            raise HTTPException(400, str(exc)) from exc
        final = backup.new_archive_path(target, "import")
        partial.rename(final)
    finally:
        partial.unlink(missing_ok=True)
    backup.rotate(target, "import", backup.keep_for("import"))
    return JSONResponse({**_backup_entry(final), **info}, status_code=201)


async def restore_backup(request: Request) -> Response:
    me = auth.require_admin(request)
    path = _backup_path(request)
    hub = getattr(request.app.state, "hub", None)
    # Éditeurs temps réel fermés avant la copie, et rien d'écrit pendant (audit M7) : ils se reconnectent après.
    if hub is not None:
        hub.suspend()
    try:
        result = await run_in_threadpool(backup.restore, path, settings(request), me["username"])
    except backup.ArchiveError as exc:
        log.error("Restauration de %s refusée ou annulée : %s", path.name, exc)
        raise HTTPException(507 if isinstance(exc, backup.SpaceError) else 400, str(exc)) from exc
    finally:
        if hub is not None:
            hub.resume()
    # Toutes les sessions viennent d'être fermées (backup._merge_accounts) : seule celle de l'administrateur
    # qui restaure est rouverte, sur son compte de la base restaurée (toujours présent et administrateur).

    def _work(conn: sqlite3.Connection):
        restored = conn.execute("SELECT * FROM users WHERE username = ? AND role = 'admin' AND active = 1",
                                (me["username"],)).fetchone()
        return restored
    restored = await in_tx(request, _work)
    if restored is not None:
        _open_session(request, dict(restored))
    if hub is not None:
        hub.close_documents(list(hub.rooms), "reset")  # les éditeurs ouverts rechargent la fiche restaurée
        hub.recheck()
    log.warning("Restauration de %s par %s (état précédent : %s)%s", path.name, me["username"], result.safety.name,
                f" ; comptes absents de la base en service, restaurés désactivés : {', '.join(result.disabled)}"
                if result.disabled else "")
    return JSONResponse({"restored": path.name, "safety": _backup_entry(result.safety), "disabled": result.disabled})


# --- Mise à jour (via le service maze-updater de l'hôte) ---------------------------
#
# Le conteneur n'a pas accès à Docker. Il dépose une demande dans le dossier de contrôle
# partagé avec l'hôte (fichier « request », seule chose qu'il y écrit), et le service systemd maze-updater
# l'exécute. Fichiers écrits par l'hôte dans le dossier d'état (MAZE_STATUS_DIR, monté en lecture seule,
# audit H2) : state, info (CLÉ=valeur), commits.tsv, update.log, .lock. Les versions précédentes du service
# les écrivaient dans le dossier de contrôle : lus là tant que le dossier d'état est vide (transition).

BUSY_STATES = {"checking", "updating"}
# Au-delà, une opération « en cours » est forcément morte (le service est limité à 30 min).
STALE_SECONDS = 40 * 60


def _read(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default


def _operation_alive(status: Path) -> bool:
    """Le service maze-updater tient un verrou (flock sur status/.lock) pendant tout son travail.
    S'il est libre alors que l'état indique une opération en cours, le service s'est arrêté
    sans pouvoir l'écrire (disque plein, redémarrage du serveur, arrêt forcé)."""
    lock = status / ".lock"
    try:
        fd = os.open(lock, os.O_RDONLY)
    except OSError:
        # Pas de verrou lisible : on se fie à l'âge du fichier d'état.
        try:
            return time.time() - (status / "state").stat().st_mtime < STALE_SECONDS
        except OSError:
            return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        return True  # verrou tenu : le service travaille
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _status_source(control: Path, status: Path | None) -> Path:
    """Dossier où lire l'état : le dossier d'état dès que le service y a écrit, sinon (service d'avant
    l'audit H2, encore en place pendant la mise à jour qui l'installe) le dossier de contrôle."""
    if status is not None and any((status / name).exists() for name in ("state", "info")):
        return status
    if any((control / name).exists() for name in ("state", "info")):
        return control
    return status if status is not None else control


def _updater_status(control: Path | None, status_dir: Path | None = None) -> dict[str, Any]:
    if control is None or not control.is_dir():
        return {"installed": False}
    source = _status_source(control, status_dir)
    info: dict[str, str] = {}
    for line in _read(source / "info").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            info[key.strip()] = value.strip()
    commits = []
    for line in _read(source / "commits.tsv").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append({"hash": parts[0], "date": parts[1], "subject": parts[2]})
    log_lines = _read(source / "update.log").splitlines()
    state = _read(source / "state", "").strip() or None
    message = info.get("message")
    if state in BUSY_STATES and not _operation_alive(source):
        state = "interrupted"
        message = ("L'opération précédente s'est arrêtée sans pouvoir enregistrer son résultat "
                   "(disque plein, redémarrage du serveur…). Consultez le journal, puis relancez.")
    return {
        "installed": state is not None or bool(info),
        "state": state or "idle",
        "pending": (control / "request").exists(),
        "current": info.get("current"),
        "available": info.get("available"),
        "branch": info.get("branch"),
        "checked_at": info.get("checked_at"),
        "started_at": info.get("started_at"),
        "finished_at": info.get("finished_at"),
        "message": message,
        "check_message": info.get("check_message"),
        "commits": commits[:50],
        "log": "\n".join(log_lines[-200:]),
    }


async def system_status(request: Request) -> Response:
    auth.require_admin(request)
    cfg = settings(request)
    return JSONResponse({"version": cfg.version, "updater": _updater_status(cfg.control_dir, cfg.status_dir),
                         "untrusted_proxy": getattr(request.app.state, "untrusted_proxy", None)})


def _request_action(action: str):
    async def handler(request: Request) -> Response:
        user = auth.require_admin(request)
        cfg = settings(request)
        control = cfg.control_dir
        status = _updater_status(control, cfg.status_dir)
        if not status["installed"]:
            raise HTTPException(503, "Le service de mise à jour n'est pas installé sur le serveur")
        if status["state"] in BUSY_STATES or status["pending"]:
            raise HTTPException(409, "Une opération est déjà en cours")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tmp = control / ".request.tmp"
        try:
            tmp.write_text(f"{action}\n{user['username']}\n{stamp}\n", encoding="utf-8")
            os.replace(tmp, control / "request")  # écriture atomique : le service voit un fichier complet
        except OSError as exc:
            raise HTTPException(503, f"Dossier de contrôle non accessible en écriture : {exc.strerror}") from exc
        return JSONResponse({"requested": action}, status_code=202)
    return handler


routes = [
    Route("/api/admin/users", list_users),
    Route("/api/admin/users", create_user, methods=["POST"]),
    Route("/api/admin/users/{id:int}", update_user, methods=["PATCH"]),
    Route("/api/admin/users/{id:int}", delete_user, methods=["DELETE"]),
    Route("/api/admin/campaigns", list_all_campaigns),
    Route("/api/admin/campaigns/{id:int}/members", set_campaign_members, methods=["PUT"]),
    Route("/api/admin/backups", list_backups),
    Route("/api/admin/backups", create_backup, methods=["POST"]),
    Route("/api/admin/backups/usage", backups_usage),
    Route("/api/admin/backups/{name}", download_backup),
    Route("/api/admin/backups/{name}", delete_backup, methods=["DELETE"]),
    Route("/api/admin/backups/import", import_backup, methods=["POST"]),
    Route("/api/admin/backups/{name}/restore", restore_backup, methods=["POST"]),
    Route("/api/admin/system", system_status),
    Route("/api/admin/system/check", _request_action("check"), methods=["POST"]),
    Route("/api/admin/system/update", _request_action("update"), methods=["POST"]),
]
