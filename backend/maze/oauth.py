"""Serveur d'autorisation OAuth 2.1 du connecteur Claude (spécification d'autorisation MCP).

Parcours : claude.ai découvre les adresses (/.well-known/…), ouvre /oauth/authorize dans le navigateur ;
un administrateur de Maze saisit son mot de passe et autorise ; claude.ai reçoit un code à usage unique,
qu'il échange (avec le secret du client et la preuve PKCE) contre un jeton d'accès sur /oauth/token.

Un seul client, créé dans l'administration (identifiant + secret saisis dans les paramètres OAuth avancés
du connecteur personnalisé de claude.ai) : pas d'enregistrement dynamique de clients.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import secrets
import sqlite3
import time
from typing import Any
from urllib.parse import unquote_plus, urlencode, urlparse

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import auth, connector, db, i18n, throttle
from .api import in_tx, tx

CODE_SECONDS = 300
MAX_PENDING_CODES = 100
# Codes en attente : connector.PENDING_CODES (un redémarrage entre l'autorisation et l'échange oblige
# simplement à recommencer). Essais de mot de passe : mêmes compteurs que la connexion à Maze (throttle).
_codes = connector.PENDING_CODES
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


async def not_found(request: Request) -> Response:
    return JSONResponse({"detail": "Introuvable"}, status_code=404)


def _metadata(request: Request) -> dict[str, Any]:
    base = connector.public_base(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "scopes_supported": ["maze"],
        "authorization_response_iss_parameter_supported": True,
        "service_documentation": f"{base}/#/admin/claude",
    }


async def authorization_server(request: Request) -> Response:
    return JSONResponse(_metadata(request))


async def protected_resource(request: Request) -> Response:
    base = connector.public_base(request)
    return JSONResponse({
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["maze"],
        "resource_name": "Maze",
    })


# --- Page d'autorisation -------------------------------------------------------------------------

# Page affichée dans le navigateur de l'administrateur : dans la langue de la requête (Accept-Language, puis
# MAZE_DEFAULT_LOCALE ; maze.i18n, catalogue oauth.json). Les réponses du point de jeton (error_description), lues
# par Claude, restent en français.
PAGE = """<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>{title}</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #161d29; color: #e8e2d4;
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; padding: 16px; box-sizing: border-box; }}
main {{ width: 100%; max-width: 440px; background: #1f2836; border: 1px solid #374359; border-radius: 14px; padding: 28px; }}
h1 {{ font: 600 22px/1.3 "Iowan Old Style", Palatino, Georgia, serif; margin: 0 0 6px; }}
p {{ margin: 0 0 14px; color: #9aa5b5; }}
ul {{ margin: 0 0 18px; padding-left: 20px; }} li {{ margin: 2px 0; }}
.mode {{ color: #c9a45c; font-size: 13px; }}
.note {{ background: rgba(201,164,92,.12); border: 1px solid rgba(201,164,92,.35); color: #e8e2d4; border-radius: 8px; padding: 10px 12px; }}
.error {{ background: rgba(210,85,90,.14); border: 1px solid rgba(210,85,90,.5); color: #f3c9cb; border-radius: 8px; padding: 10px 12px; }}
label {{ display: block; font-size: 13px; color: #9aa5b5; margin: 12px 0 4px; }}
input[type=text], input[type=password] {{ width: 100%; box-sizing: border-box; padding: 9px 11px; border-radius: 8px;
  border: 1px solid #374359; background: #10151e; color: #e8e2d4; font: inherit; }}
.actions {{ display: flex; gap: 10px; margin-top: 20px; }}
button {{ flex: 1; padding: 10px; border-radius: 8px; border: 1px solid #374359; background: #283344; color: #e8e2d4; font: inherit; cursor: pointer; }}
button[value=allow] {{ background: #c9a45c; border-color: #c9a45c; color: #161d29; font-weight: 600; }}
</style></head><body><main>{body}</main></body></html>"""


def _page(request: Request, body: str, status: int = 200, headers: dict[str, str] | None = None) -> HTMLResponse:
    locale = i18n.request_locale(request.scope)
    page = PAGE.format(lang=locale, title=html.escape(i18n.tr("Autoriser Claude | Maze", locale)), body=body)
    return HTMLResponse(page, status_code=status, headers={**NO_STORE, **(headers or {})})


def _error_page(request: Request, message: str) -> HTMLResponse:
    locale = i18n.request_locale(request.scope)
    return _page(request, f"<h1>{html.escape(i18n.tr('Autorisation impossible', locale))}</h1>"
                          f"<p class=\"error\">{html.escape(i18n.translate(message, locale))}</p>", 400)


def _redirect(uri: str, params: dict[str, str]) -> RedirectResponse:
    sep = "&" if "?" in uri else "?"
    return RedirectResponse(f"{uri}{sep}{urlencode({k: v for k, v in params.items() if v})}", status_code=303)


REQUEST_FIELDS = ("client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method", "scope", "resource")


def _check_request(request: Request, params: dict[str, str]) -> Response | None:
    """Vérifie une demande d'autorisation : réponse d'erreur, ou None si elle est valable.
    Client ou adresse de retour invalides : page d'erreur (jamais de redirection vers une adresse douteuse)."""
    redirect_uri = params.get("redirect_uri", "")
    with tx(request) as conn:
        known_client = connector.check_client(conn, params.get("client_id", ""), None)
    if not known_client:
        return _error_page(request, "Client inconnu. Vérifiez l'identifiant client saisi dans claude.ai (Maze > Administration > Connecteur Claude).")  # noqa: E501
    if not connector.redirect_allowed(redirect_uri):
        return _error_page(request, "Adresse de retour non autorisée.")
    state = params.get("state", "")
    if params.get("response_type", "code") != "code":
        return _redirect(redirect_uri, {"error": "unsupported_response_type", "state": state})
    if not params.get("code_challenge") or params.get("code_challenge_method") != "S256":
        return _redirect(redirect_uri, {"error": "invalid_request", "state": state,
                                        "error_description": "PKCE (S256) obligatoire"})
    resource = params.get("resource", "")
    base = connector.public_base(request)
    if resource and resource.rstrip("/") not in (base, f"{base}/mcp"):
        return _redirect(redirect_uri, {"error": "invalid_target", "state": state})
    return None


def _consent_body(request: Request, params: dict[str, str], error: str = "", username: str = "") -> str:
    # Page servie avant toute authentification : des effectifs, jamais les noms des campagnes.
    locale = i18n.request_locale(request.scope)

    def tr(text: str, **values: object) -> str:
        """Texte de la page, échappé (les valeurs en gras sont insérées après l'échappement)."""
        bold = {k: f"<strong>{html.escape(str(v))}</strong>" for k, v in values.items()}
        out = html.escape(i18n.tr(text, locale, **{k: f"\0{k}\0" for k in values}))
        for k, v in bold.items():
            out = out.replace(f"\0{k}\0", v)
        return out

    with tx(request) as conn:
        row = connector.load_settings_row(conn)
        modes = list(connector.grants(conn).values())
    writable = modes.count("write")
    if modes:
        campaigns = "<p>" + tr("Campagnes ouvertes à Claude : {count}, dont {writable} en lecture et écriture (liste dans Administration > Connecteur Claude).",  # noqa: E501
                               count=len(modes), writable=writable) + "</p>"
    else:
        campaigns = ("<p class=\"note\">" + tr("Aucune campagne n'est encore ouverte à Claude : choisissez-les dans Administration > Connecteur Claude.")  # noqa: E501
                     + "</p>")
    target = urlparse(params.get("redirect_uri", ""))
    destination = i18n.tr("Claude Code sur cet ordinateur", locale) if target.scheme == "http" else (target.hostname or "")
    paused = "" if row["enabled"] else (
        "<p class=\"note\">" + tr("L'accès est suspendu : Claude ne pourra rien lire ni modifier avant que vous ne l'activiez dans Administration > Connecteur Claude.")  # noqa: E501
        + "</p>")
    hidden = "".join(f"<input type=\"hidden\" name=\"{k}\" value=\"{html.escape(params.get(k, ''))}\">"
                     for k in REQUEST_FIELDS)
    err = f"<p class=\"error\">{html.escape(i18n.translate(error, locale))}</p>" if error else ""
    return f"""<h1>{tr("Autoriser Claude")}</h1>
<p>{tr("Claude demande à se connecter à Maze en votre nom. Il ne verra que les campagnes que vous lui avez ouvertes.")}</p>
{campaigns}{paused}<p>{tr("Retour après autorisation : {destination}", destination=destination)}</p>{err}
<form method="post" action="/oauth/authorize">{hidden}
<label for="u">{tr("Identifiant administrateur")}</label>
<input id="u" type="text" name="username" autocomplete="username" required value="{html.escape(username)}">
<label for="p">{tr("Mot de passe")}</label>
<input id="p" type="password" name="password" autocomplete="current-password" required>
<div class="actions"><button type="submit" name="action" value="deny" formnovalidate>{tr("Refuser")}</button>
<button type="submit" name="action" value="allow">{tr("Autoriser")}</button></div>
</form>"""


async def authorize(request: Request) -> Response:
    if request.method == "GET":
        params = dict(request.query_params)
        failure = await db.run_db(_check_request, request, params, write=False)
        return failure or _page(request, await db.run_db(_consent_body, request, params, write=False))

    form = await request.form(max_files=0, max_fields=20)
    params = {k: str(form.get(k, ""))[:2000] for k in (*REQUEST_FIELDS, "username", "password", "action")}
    failure = await db.run_db(_check_request, request, params)
    if failure:
        return failure
    if params["action"] != "allow":
        return _redirect(params["redirect_uri"], {"error": "access_denied", "state": params["state"]})

    client = request.client.host if request.client else "?"
    # Mêmes limites que la connexion à Maze, mêmes compteurs (throttle).
    refused = await throttle.admit(client, params["username"])
    if refused:
        return _page(request, await db.run_db(_consent_body, request, params, refused.message), 429, refused.headers)

    user = await in_tx(request, lambda conn: db.row_to_dict(conn.execute(
        "SELECT * FROM users WHERE username = ? AND active = 1", (params["username"].strip(),)).fetchone()), write=False)
    if user is None:
        await run_in_threadpool(auth.verify_dummy, params["password"])
        ok = False
    else:
        ok = await run_in_threadpool(auth.verify_password, params["password"], user["password_hash"])
    if not ok:
        await asyncio.sleep(1)
        return _page(request, await db.run_db(_consent_body, request, params, "Identifiant ou mot de passe incorrect.",
                                     params["username"]), 401)
    if user["role"] != "admin":
        return _page(request, await db.run_db(_consent_body, request, params, "Seul un administrateur peut autoriser Claude.",
                                   params["username"]), 403)
    throttle.succeeded(client, params["username"])
    await in_tx(request, lambda conn: throttle.remember(conn, user["id"], client))

    code = secrets.token_urlsafe(32)
    expired = [k for k, v in _codes.items() if v["expires"] < time.time()]
    for k in expired:
        _codes.pop(k, None)
    if len(_codes) >= MAX_PENDING_CODES:
        _codes.pop(next(iter(_codes)))
    _codes[connector.digest(code)] = {
        "user_id": user["id"], "session_key": user["session_key"], "client_id": params["client_id"],
        "redirect_uri": params["redirect_uri"], "challenge": params["code_challenge"],
        "expires": time.time() + CODE_SECONDS,
    }
    return _redirect(params["redirect_uri"], {"code": code, "state": params["state"],
                                              "iss": connector.public_base(request)})


# --- Point de jeton ----------------------------------------------------------------------------

def _token_error(error: str, description: str, status: int = 400) -> JSONResponse:
    headers = dict(NO_STORE)
    if error == "invalid_client":
        headers["WWW-Authenticate"] = 'Basic realm="Maze"'
    return JSONResponse({"error": error, "error_description": description}, status_code=status, headers=headers)


def _client_credentials(request: Request, form) -> tuple[str, str]:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(header[6:].strip()).decode()
            client_id, _, secret = decoded.partition(":")
            return unquote_plus(client_id), unquote_plus(secret)
        except (ValueError, UnicodeDecodeError):
            return "", ""
    return str(form.get("client_id", "")), str(form.get("client_secret", ""))


def _pkce_ok(verifier: str, challenge: str) -> bool:
    if not 43 <= len(verifier) <= 128:
        return False
    computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(computed, challenge)


async def token(request: Request) -> Response:
    form = await request.form(max_files=0, max_fields=20)
    client_id, secret = _client_credentials(request, form)
    if not secret or not await in_tx(request, lambda conn: connector.check_client(conn, client_id, secret)):
        return _token_error("invalid_client", "Identifiant ou secret du client incorrect", 401)
    grant = str(form.get("grant_type", ""))

    if grant == "authorization_code":
        pending = _codes.pop(connector.digest(str(form.get("code", ""))), None)
        if (pending is None or pending["expires"] < time.time() or pending["client_id"] != client_id
                or pending["redirect_uri"] != str(form.get("redirect_uri", ""))):
            return _token_error("invalid_grant", "Code d'autorisation invalide ou expiré")
        if not _pkce_ok(str(form.get("code_verifier", "")), pending["challenge"]):
            return _token_error("invalid_grant", "Preuve PKCE incorrecte")
        def issue(conn: sqlite3.Connection) -> dict[str, Any] | None:
            user = connector.valid_admin(conn, pending["user_id"], pending["session_key"])
            return None if user is None else connector.issue_tokens(conn, user)
        tokens = await in_tx(request, issue)
        if tokens is None:
            return _token_error("invalid_grant", "Ce compte n'est plus administrateur actif")
        return JSONResponse(tokens, headers=NO_STORE)

    if grant == "refresh_token":
        refresh = str(form.get("refresh_token", ""))
        tokens = await in_tx(request, lambda conn: connector.refresh_tokens(conn, refresh))
        if tokens is None:
            return _token_error("invalid_grant", "Jeton de rafraîchissement invalide ou révoqué")
        return JSONResponse(tokens, headers=NO_STORE)

    return _token_error("unsupported_grant_type", "Type d'autorisation non pris en charge")


routes = [
    Route("/.well-known/oauth-authorization-server", authorization_server),
    Route("/.well-known/oauth-authorization-server/mcp", authorization_server),
    Route("/.well-known/oauth-protected-resource", protected_resource),
    Route("/.well-known/oauth-protected-resource/mcp", protected_resource),
    Route("/oauth/authorize", authorize, methods=["GET", "POST"]),
    Route("/oauth/token", token, methods=["POST"]),
    # Le reste de ces espaces : 404 (et non la page de l'interface, que des clients OAuth prendraient pour du JSON).
    Route("/.well-known/{path:path}", not_found, methods=["GET", "POST"]),
    Route("/oauth/{path:path}", not_found, methods=["GET", "POST"]),
]
