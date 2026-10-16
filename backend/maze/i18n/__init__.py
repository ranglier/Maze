"""Traduction des messages du serveur (style gettext : la phrase française sert de clé).

Catalogues : `maze/i18n/<langue>/<zone>.json` (zones `server` : messages d'erreur de l'API ; `seeds` : données créées
avec chaque campagne ; tout autre fichier .json du dossier est lu de la même façon). Un objet JSON par fichier :
clé = texte français, valeur = traduction, ou pour un message au pluriel (msgn) un objet
`{"one": "...", "other": "..."}` avec les catégories de pluriel de la langue. Aucun catalogue pour le français, langue
source : une clé absente retombe sur le texte français. Ajouter une langue = ajouter un dossier.

Messages construits avec des valeurs : `msg("Fiche {name} introuvable", name=...)` au lieu d'une f-string. Le résultat
est une chaîne (le texte français, pour les journaux, les tests et le connecteur Claude) qui garde le modèle et les
paramètres : `translate(message, "en")` cherche le modèle dans le catalogue puis le remplit. Un paramètre qui est
lui-même un `msg(...)` est traduit aussi ; une chaîne ordinaire (nom de fiche saisi par un MJ) jamais.

Langue d'une requête (`request_locale`) : celle du compte connecté (`users.locale`, migration 030), sinon l'en-tête
Accept-Language (langues que le serveur connaît), sinon `MAZE_DEFAULT_LOCALE` (défaut `fr`). Les gestionnaires
d'erreurs de app.py traduisent `detail` à la réponse (HTTPException, 422) ; `I18nMiddleware` traduit les réponses
d'erreur JSON écrites directement (middlewares, `JSONResponse({"detail": ...})`), par la clé exacte ou en
reconnaissant un modèle du catalogue.
"""

from __future__ import annotations

import functools
import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("maze")

SOURCE_LOCALE = "fr"
CATALOG_DIR = Path(__file__).resolve().parent
# Paramètres d'un modèle : {nom} (lettres, chiffres, tiret bas), comme dans l'interface (frontend/src/i18n).
PLACEHOLDER = re.compile(r"\{(\w+)\}")
# Langue valable pour un compte ou MAZE_DEFAULT_LOCALE : code de deux ou trois lettres minuscules.
LOCALE_CODE = re.compile(r"^[a-z]{2,3}$")
PLURAL_CATEGORIES = ("zero", "one", "two", "few", "many", "other")


# --- Catalogues ---------------------------------------------------------------

@functools.cache
def available_locales() -> tuple[str, ...]:
    """Langues connues du serveur : le français (source) et chaque dossier de catalogues."""
    found = sorted(p.name for p in CATALOG_DIR.iterdir()
                   if p.is_dir() and LOCALE_CODE.match(p.name) and p.name != SOURCE_LOCALE)
    return (SOURCE_LOCALE, *found)


@functools.cache
def catalog(locale: str) -> dict[str, Any]:
    """Toutes les zones d'une langue, fusionnées (vide pour le français ou une langue sans dossier)."""
    merged: dict[str, Any] = {}
    folder = CATALOG_DIR / locale
    if locale == SOURCE_LOCALE or not LOCALE_CODE.match(locale) or not folder.is_dir():
        return merged
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Catalogue de traduction illisible %s : %s (ignoré)", path, exc)
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged


@functools.cache
def _patterns(locale: str) -> tuple[tuple[re.Pattern[str], str], ...]:
    """Modèles à paramètres du catalogue, en expressions régulières : retrouver le modèle d'un texte déjà composé."""
    out = []
    for key, value in catalog(locale).items():
        if not isinstance(value, str) or not PLACEHOLDER.search(key):
            continue
        parts, seen, pos = [], set(), 0
        for m in PLACEHOLDER.finditer(key):
            parts.append(re.escape(key[pos:m.start()]))
            name = m.group(1)
            parts.append(f"(?P={name})" if name in seen else f"(?P<{name}>.+?)")
            seen.add(name)
            pos = m.end()
        parts.append(re.escape(key[pos:]))
        out.append((re.compile("".join(parts), re.DOTALL), key))
    return tuple(out)


# --- Pluriels -----------------------------------------------------------------

def plural_category(locale: str, n: Any) -> str:
    """Catégorie de pluriel CLDR d'un nombre entier, pour les langues courantes (repli : one pour 1, sinon other)."""
    try:
        i = abs(int(n))
    except (TypeError, ValueError):
        return "other"
    if locale in ("fr", "pt"):
        if i in (0, 1):
            return "one"
        return "many" if i % 1_000_000 == 0 else "other"
    if locale in ("ja", "ko", "zh", "vi", "th", "id"):
        return "other"
    return "one" if i == 1 else "other"


# --- Messages -----------------------------------------------------------------

def _fill(template: str, params: dict[str, Any], locale: str) -> str:
    def value(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in params:
            return m.group(0)
        return translate(params[name], locale) if isinstance(params[name], Msg) else str(params[name])
    return PLACEHOLDER.sub(value, template)


class Msg(str):
    """Message traduisible : la chaîne vaut le texte français composé ; `template`, `plural`, `count` et `params`
    servent à le traduire au moment de la réponse (translate)."""

    template: str
    plural: str | None
    count: Any
    params: dict[str, Any]

    def __new__(cls, template: str, params: dict[str, Any], plural: str | None = None, count: Any = None) -> Msg:
        source = template
        if plural is not None and plural_category(SOURCE_LOCALE, count) != "one":
            source = plural
        obj = super().__new__(cls, _fill(source, params, SOURCE_LOCALE))
        obj.template, obj.plural, obj.count, obj.params = template, plural, count, params
        return obj

    def __reduce__(self):
        return (Msg, (self.template, self.params, self.plural, self.count))


def msg(template: str, /, **params: Any) -> Msg:
    """Message à paramètres, à la place d'une f-string : msg("Fiche {name} introuvable", name=nom)."""
    return Msg(template, params)


def msgn(singular: str, plural: str, n: Any, /, **params: Any) -> Msg:
    """Message au pluriel : msgn("{n} lien utilise ce type", "{n} liens utilisent ce type", n). `n` est aussi un
    paramètre. Clé du catalogue : la forme au singulier ; valeur : {"one": ..., "other": ...}."""
    return Msg(singular, {"n": n, **params}, plural, n)


def lookup(text: str, locale: str) -> str | None:
    """Traduction exacte d'un texte sans paramètres, ou None."""
    value = catalog(locale).get(text)
    return value if isinstance(value, str) else None


def translate(text: Any, locale: str | None) -> Any:
    """Traduit un message (Msg ou chaîne) dans `locale` ; texte français si la clé manque. Autre valeur (None,
    liste…) : rendue telle quelle."""
    if not isinstance(text, str):
        return text
    locale = locale or SOURCE_LOCALE
    if isinstance(text, Msg):
        if locale == SOURCE_LOCALE:
            return str(text)
        entry = catalog(locale).get(text.template)
        if text.plural is not None:
            if isinstance(entry, dict):
                form = entry.get(plural_category(locale, text.count)) or entry.get("other")
                if isinstance(form, str):
                    return _fill(form, text.params, locale)
            return _fill(text.plural if plural_category(SOURCE_LOCALE, text.count) != "one" else text.template,
                         text.params, locale)
        return _fill(entry if isinstance(entry, str) else text.template, text.params, locale)
    if locale == SOURCE_LOCALE:
        return text
    return lookup(text, locale) or text


def translate_text(text: str, locale: str) -> str:
    """Traduit un texte déjà composé (réponse écrite sans passer par translate) : clé exacte, sinon modèle reconnu
    dans le catalogue (« Requête trop volumineuse (max 2 Mo) » par « Requête trop volumineuse (max {size}) »). Les
    paramètres retrouvés sont recopiés tels quels."""
    if locale == SOURCE_LOCALE or not isinstance(text, str):
        return text
    exact = lookup(text, locale)
    if exact is not None:
        return exact
    entries = catalog(locale)
    for pattern, key in _patterns(locale):
        m = pattern.fullmatch(text)
        if m:
            return _fill(entries[key], m.groupdict(), locale)
    return text


# --- Langue d'une requête -----------------------------------------------------

def normalize(value: Any) -> str:
    """Code de langue à deux ou trois lettres (« en-US » donne « en ») ; chaîne vide si invalide."""
    if not isinstance(value, str):
        return ""
    code = value.strip().replace("_", "-").split("-")[0].lower()
    return code if LOCALE_CODE.match(code) else ""


def negotiate(accept_language: str | None, known: tuple[str, ...] | None = None) -> str | None:
    """Première langue de l'en-tête Accept-Language (par qualité décroissante) que le serveur connaît."""
    if not accept_language:
        return None
    known = known or available_locales()
    choices: list[tuple[float, int, str]] = []
    for index, part in enumerate(accept_language.split(",")[:20]):
        tag, _, rest = part.strip().partition(";")
        quality = 1.0
        for param in rest.split(";"):
            name, _, val = param.strip().partition("=")
            if name.strip() == "q":
                try:
                    quality = float(val)
                except ValueError:
                    quality = 0.0
        code = normalize(tag)
        if code and quality > 0:
            choices.append((-quality, index, code))
    for _, _, code in sorted(choices):
        if code in known:
            return code
    return None


def default_locale(scope: dict[str, Any] | None = None) -> str:
    app = (scope or {}).get("app")
    settings = getattr(getattr(app, "state", None), "settings", None)
    return getattr(settings, "default_locale", None) or SOURCE_LOCALE


def request_locale(scope: dict[str, Any]) -> str:
    """Langue des messages d'une requête : compte connecté, puis Accept-Language, puis MAZE_DEFAULT_LOCALE."""
    user = (scope.get("state") or {}).get("user")
    if isinstance(user, dict) and user.get("locale"):
        return user["locale"]
    for name, value in scope.get("headers") or ():
        if name == b"accept-language":
            found = negotiate(value.decode("latin-1"))
            if found:
                return found
            break
    return default_locale(scope)


# --- Réponses d'erreur ----------------------------------------------------------

TRANSLATED = "maze.i18n.translated"   # marque posée dans la portée ASGI par les gestionnaires d'erreurs de app.py


class I18nMiddleware:
    """Traduit `detail` des réponses d'erreur JSON (statut 400 et plus) qui ne sont pas passées par les gestionnaires
    d'erreurs de app.py : refus des middlewares (session absente, base occupée, restauration, version de
    l'interface, corps trop volumineux) et `JSONResponse({"detail": ...})` des routes. Placé juste sous la compression
    (corps lu en clair) ; la langue est lue après la réponse, une fois le compte chargé par AuthMiddleware."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start: dict[str, Any] | None = None
        chunks: list[bytes] = []

        async def wrapped(message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers") or [])
                if (message["status"] >= 400 and not scope.get(TRANSLATED)
                        and headers.get(b"content-type", b"").startswith(b"application/json")
                        and request_locale(scope) != SOURCE_LOCALE):
                    start = message   # corps à lire en entier avant de répondre
                    return
                await send(message)
                return
            if start is None or message["type"] != "http.response.body":
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                return
            body = b"".join(chunks)
            try:
                data = json.loads(body)
                if isinstance(data, dict) and isinstance(data.get("detail"), str):
                    data["detail"] = translate_text(data["detail"], request_locale(scope))
                    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            except ValueError:
                pass
            headers = [(k, v) for k, v in start.get("headers") or [] if k != b"content-length"]
            headers.append((b"content-length", str(len(body)).encode()))
            await send({**start, "headers": headers})
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, wrapped)
