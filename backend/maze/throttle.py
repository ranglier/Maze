"""Limitation des essais de mot de passe : connexion (api.login) et autorisation du connecteur Claude
(oauth.authorize), audits M13 et M14 (et vérification finale D1).

- Par adresse : 8 essais en 10 minutes, puis refus (429) jusqu'à ce que les plus anciens sortent de la fenêtre.
  L'adresse est celle que donne uvicorn : l'adresse réelle du client si FORWARDED_ALLOW_IPS contient celle du
  reverse proxy (par défaut, les réseaux privés et locaux ; sans quoi tous les clients ont l'adresse du proxy).
- Par compte (identifiant saisi, qu'il existe ou non, sans distinction de casse comme la connexion) : délai croissant
  avant la vérification à partir du 3e essai récent sans succès (0,5 s, 1, 2, 4, 8, puis 10 s au plus), compté en
  créneaux : chaque essai prend le créneau suivant du compte (au plus tôt à son arrivée), puis attend son délai à
  partir de là. Des essais envoyés en même temps sont donc vérifiés l'un après l'autre, comme s'ils avaient été
  envoyés en série. Un essai dont la vérification tomberait plus de MAX_DELAY après son arrivée (créneaux déjà pris)
  est refusé aussitôt (429), sans être compté : la file d'un compte ne s'allonge pas, un flot d'essais n'obtient
  qu'une vérification toutes les 10 s, d'où qu'il vienne.
- Non-blocage : une adresse qui s'est déjà connectée à ce compte (dans les KNOWN_WINDOW dernières secondes) a ses
  propres compteurs et créneaux pour ce compte, hors de ceux des autres adresses : un flot d'essais faux venu
  d'ailleurs ne la retarde ni ne la refuse ; son essai passe au pire après son propre délai (0 tant qu'elle n'a pas
  raté 3 essais, 10 s au plus). Ces adresses sont gardées en base (table login_addresses, migration 029 : remember à
  chaque connexion réussie, load_known au démarrage), 32 par compte au plus (KNOWN_PER_ACCOUNT).
- Essai unique hors file (vérification finale R1) : quand la file du compte est pleine, une adresse sans essai récent
  sur ce compte (aucun dans la fenêtre de 10 minutes) obtient un essai hors file, après le délai du compte. Il est
  compté avant la vérification, comme un échec tant qu'une connexion réussie ne l'efface pas : des essais simultanés
  depuis cette adresse n'en obtiennent qu'un, et un flot n'obtient en plus qu'un essai par adresse et par 10 minutes.
  Au plus SINGLE_IN_FLIGHT (2) de ces essais attendent en même temps par compte : sans cette borne, des adresses
  inventées (X-Forwarded-For d'un proxy approuvé par la valeur par défaut de FORWARDED_ALLOW_IPS) rouvriraient le
  contournement par parallélisme ; avec elle, un flot obtient au plus 3 vérifications par délai maximal (une par la
  file, deux hors file), et il lui faut deux adresses neuves toutes les 10 s pour occuper les essais hors file.
  L'administrateur sur un appareil jamais utilisé garde ainsi sa chance pendant une attaque venue de peu d'adresses.
- Un essai est compté dès son arrivée, avant toute attente, mais seulement une fois le corps de la requête validé : un
  corps invalide ne crée aucun état. Une connexion réussie efface les essais de son adresse, ceux de sa file (celle
  du compte, ou celle de l'adresse connue) et ceux de l'adresse sur ce compte, et rend l'adresse connue pour ce compte.
- Refus : Refused (message et Retry-After juste) : limite de l'adresse (temps avant que son plus ancien essai sorte de
  la fenêtre) ou file du compte pleine (quelques secondes : temps avant qu'un créneau se libère).
- Mémoire bornée : au plus MAX_KEYS adresses, files et adresses connues suivies (les moins récentes oubliées au-delà),
  une clé dont tous les essais sont sortis de la fenêtre est retirée, et 32 essais au plus par clé.
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
import time
from collections import OrderedDict
from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path

from . import db
from .i18n import msgn

WINDOW = 600.0
MAX_PER_ADDRESS = 8
MAX_KEYS = 10_000
MAX_PER_KEY = 32
FREE_FAILURES = 3
MAX_DELAY = 10.0
KNOWN_DAYS = 30
KNOWN_WINDOW = KNOWN_DAYS * 86400.0
KNOWN_PER_ACCOUNT = 32
SINGLE_IN_FLIGHT = 2

# Horloge et attente, remplacées par les tests.
clock = time.monotonic
sleep = asyncio.sleep


def _now(now: float | None) -> float:
    return clock() if now is None else now


class Attempts:
    """Essais récents par clé, dans une fenêtre glissante, avec un nombre de clés borné."""

    def __init__(self, window: float = WINDOW, max_keys: int = MAX_KEYS):
        self.window = window
        self.max_keys = max_keys
        # Rangées par dernier essai, le plus ancien d'abord.
        self._keys: OrderedDict[Hashable, list[float]] = OrderedDict()

    def _forget_stale(self, now: float) -> None:
        while self._keys:
            times = next(iter(self._keys.values()))
            if times and now - times[-1] < self.window:
                break
            self._keys.popitem(last=False)

    def count(self, key: Hashable, now: float | None = None) -> int:
        now = _now(now)
        self._forget_stale(now)
        times = self._keys.get(key)
        if times is None:
            return 0
        times[:] = [t for t in times if now - t < self.window]
        if not times:
            del self._keys[key]
        return len(times)

    def add(self, key: Hashable, now: float | None = None) -> None:
        now = _now(now)
        times = self._keys.setdefault(key, [])
        times.append(now)
        del times[:-MAX_PER_KEY]
        self._keys.move_to_end(key)
        while len(self._keys) > self.max_keys:
            self._keys.popitem(last=False)

    def oldest(self, key: Hashable) -> float | None:
        times = self._keys.get(key)
        return times[0] if times else None

    def clear(self, key: Hashable) -> None:
        self._keys.pop(key, None)

    def reset(self) -> None:
        self._keys.clear()

    def __len__(self) -> int:
        return len(self._keys)


class Known:
    """Adresses qui se sont connectées à un compte (clé : (compte, adresse)), oubliées après `window` secondes sans
    nouvelle connexion ; `per_account` par compte et `max_keys` en tout au plus, les moins récentes (du compte, puis de
    l'ensemble) oubliées au-delà. Seule une connexion réussie en ajoute (et load_known, depuis la base)."""

    def __init__(self, window: float = KNOWN_WINDOW, max_keys: int = MAX_KEYS, per_account: int = KNOWN_PER_ACCOUNT):
        self.window = window
        self.max_keys = max_keys
        self.per_account = per_account
        self._keys: OrderedDict[tuple[str, str], float] = OrderedDict()   # rangées par dernière connexion

    def add(self, key: tuple[str, str], now: float | None = None) -> None:
        self._keys[key] = _now(now)
        self._keys.move_to_end(key)
        same = [k for k in self._keys if k[0] == key[0]]   # du plus ancien au plus récent
        for old in same[:max(0, len(same) - self.per_account)]:
            del self._keys[old]
        while len(self._keys) > self.max_keys:
            self._keys.popitem(last=False)

    def forget_account(self, account: str) -> None:
        for key in [k for k in self._keys if k[0] == account]:
            del self._keys[key]

    def contains(self, key: Hashable, now: float | None = None) -> bool:
        now = _now(now)
        while self._keys and now - next(iter(self._keys.values())) >= self.window:
            self._keys.popitem(last=False)
        return key in self._keys

    def reset(self) -> None:
        self._keys.clear()

    def __len__(self) -> int:
        return len(self._keys)


by_address = Attempts()
by_account = Attempts()   # clés : compte (file commune) ou (compte, adresse connue)
known = Known()
# Heure de la dernière vérification réservée par file ; une file dont ce créneau est passé n'impose plus rien.
_slots: OrderedDict[Hashable, float] = OrderedDict()
# Heures de vérification des essais uniques hors file en attente, par compte.
_singles: dict[str, list[float]] = {}


def _account(username: str) -> str:
    """Clé du compte : comme la recherche du compte (identifiant sans espaces autour, COLLATE NOCASE de SQLite,
    qui ne replie que l'ASCII). Sinon « Admin », « ADMIN »… auraient chacun leurs essais sans délai."""
    return username.strip()[:200].translate(_ASCII_LOWER)


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _lane(account: str, address: str, now: float) -> Hashable:
    """File de l'essai : celle de l'adresse si elle s'est déjà connectée à ce compte, sinon celle du compte. Un tuple
    ne peut pas valoir un identifiant (chaîne) : aucun identifiant saisi ne tombe dans la file d'une adresse connue.
    by_account[(compte, adresse)] compte aussi les essais d'une adresse inconnue sur ce compte (essai unique)."""
    pair = (account, address)
    return pair if known.contains(pair, now) else account


@dataclass(frozen=True)
class Refused:
    """Essai refusé (429) : message à afficher et attente en secondes (en-tête Retry-After)."""
    message: str
    retry_after: int

    @property
    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after)}


def _seconds(value: float) -> int:
    return max(1, math.ceil(value - 1e-6))


def _address_refusal(address: str, now: float) -> Refused:
    oldest = by_address.oldest(address)
    wait = _seconds((oldest if oldest is not None else now) + WINDOW - now)
    minutes = math.ceil(wait / 60)
    return Refused(msgn("Trop de tentatives depuis cette adresse. Réessayez dans {n} minute.",
                        "Trop de tentatives depuis cette adresse. Réessayez dans {n} minutes.", minutes), wait)


def _busy_refusal(wait: float) -> Refused:
    seconds = _seconds(wait)
    return Refused(msgn("Trop de tentatives en cours sur ce compte. Réessayez dans {n} seconde.",
                        "Trop de tentatives en cours sur ce compte. Réessayez dans {n} secondes.", seconds), seconds)


def account_delay(attempts: int) -> float:
    """Attente avant de vérifier le mot de passe d'un compte qui a déjà `attempts` essais récents sans succès."""
    if attempts < FREE_FAILURES:
        return 0.0
    return min(MAX_DELAY, 0.5 * 2 ** (attempts - FREE_FAILURES))


def _pending_singles(account: str, now: float) -> list[float]:
    pending = [t for t in _singles.get(account, ()) if t > now]
    if pending:
        _singles[account] = pending
    else:
        _singles.pop(account, None)
    return pending


def _reserve(lane: Hashable, at: float, now: float) -> None:
    _slots[lane] = at
    _slots.move_to_end(lane)
    if len(_slots) > MAX_KEYS:
        for key in [k for k, t in _slots.items() if t <= now]:
            del _slots[key]
        while len(_slots) > MAX_KEYS:
            _slots.popitem(last=False)


async def admit(address: str, username: str) -> Refused | None:
    """À appeler avant de vérifier un mot de passe (corps déjà validé). Refused : trop d'essais depuis cette adresse,
    ou créneaux du compte pris au-delà de MAX_DELAY sans droit à l'essai unique (répondre 429 avec son message et
    son Retry-After ; rien n'est compté). None : l'essai est compté, son créneau réservé, puis attendu."""
    now = clock()
    if by_address.count(address, now) >= MAX_PER_ADDRESS:
        return _address_refusal(address, now)
    account = _account(username)
    pair = (account, address)
    lane = _lane(account, address, now)
    delay = account_delay(by_account.count(lane, now))
    verify_at = max(now, _slots.get(lane, now)) + delay
    if verify_at - now <= MAX_DELAY + 1e-6:
        by_address.add(address, now)
        by_account.add(lane, now)
        if lane != pair:
            by_account.add(pair, now)   # essai de cette adresse sur ce compte : plus d'essai unique pendant 10 min
        _reserve(lane, verify_at, now)
    elif lane != pair and by_account.count(pair, now) == 0 \
            and len(singles := _pending_singles(account, now)) < SINGLE_IN_FLIGHT:
        # File du compte pleine, adresse sans essai récent sur ce compte : essai unique hors file, compté tout de
        # suite (avant toute attente) comme un échec ; aucun créneau de la file réservé.
        verify_at = now + delay
        by_address.add(address, now)
        by_account.add(account, now)
        by_account.add(pair, now)
        _singles[account] = [*singles, verify_at]
        if len(_singles) > MAX_KEYS:
            for key in [k for k, times in _singles.items() if max(times) <= now]:
                del _singles[key]
    else:
        wait = verify_at - now - MAX_DELAY
        if lane != pair and by_account.count(pair, now) == 0:   # essai unique possible dès qu'un se libère
            wait = min(wait, min(_pending_singles(account, now)) - now)
        return _busy_refusal(wait)
    if verify_at > now:
        await sleep(verify_at - now)
    return None


def succeeded(address: str, username: str) -> None:
    now = clock()
    account = _account(username)
    by_address.clear(address)
    by_account.clear(_lane(account, address, now))
    by_account.clear((account, address))
    known.add((account, address), now)


def forget_account(username: str) -> None:
    """Compte supprimé : ses adresses connues sont oubliées (en base : ON DELETE CASCADE)."""
    known.forget_account(_account(username))


# --- Adresses connues en base (table login_addresses, migration 029) -----------------------------------------

_STAMP = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def remember(conn: sqlite3.Connection, user_id: int, address: str) -> None:
    """Connexion réussie par mot de passe : adresse enregistrée (ou rafraîchie) pour ce compte, dans la transaction
    d'écriture de l'appelant ; puis purge : plus de KNOWN_DAYS jours, au-delà de KNOWN_PER_ACCOUNT pour ce compte."""
    if not address or address == "?":
        return
    conn.execute(f"INSERT INTO login_addresses (user_id, address, last_login_at) VALUES (?, ?, {_STAMP}) "
                 "ON CONFLICT (user_id, address) DO UPDATE SET last_login_at = excluded.last_login_at",
                 (user_id, address))
    purge(conn)
    conn.execute("""DELETE FROM login_addresses WHERE user_id = ? AND address NOT IN (
                        SELECT address FROM login_addresses WHERE user_id = ?
                         ORDER BY last_login_at DESC, address LIMIT ?)""", (user_id, user_id, KNOWN_PER_ACCOUNT))


def purge(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM login_addresses WHERE last_login_at < strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)",
                 (f"-{KNOWN_DAYS} days",))


def load_known(db_path: Path) -> int:
    """Au démarrage : purge, puis adresses connues des comptes actifs remises en mémoire, avec leur âge. Renvoie leur
    nombre."""
    with db.transaction(db_path) as conn:
        purge(conn)
        rows = conn.execute("""SELECT u.username, a.address, (julianday('now') - julianday(a.last_login_at)) * 86400
                                 FROM login_addresses AS a JOIN users AS u ON u.id = a.user_id
                                WHERE u.active = 1 ORDER BY a.last_login_at""").fetchall()
    now = clock()
    for username, address, age in rows:
        if age is not None and age < KNOWN_WINDOW:
            known.add((_account(username), address), now - max(0.0, age))
    return len(rows)


def reset() -> None:
    """Tests : oublie tous les essais."""
    by_address.reset()
    by_account.reset()
    known.reset()
    _slots.clear()
    _singles.clear()
