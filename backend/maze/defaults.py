"""Valeurs par défaut créées avec chaque nouvelle campagne."""

from __future__ import annotations

import re
import sqlite3
import unicodedata

from . import db

# (clé, libellé, couleur, orienté, indication de sens)
DEFAULT_RELATION_TYPES = [
    ("allie", "Allié", "#5fb49c", False, ""),
    ("ennemi", "Ennemi", "#d2555a", False, ""),
    ("famille", "Famille", "#e0a33b", False, ""),
    ("amour", "Amour", "#d77ba6", False, ""),
    ("rival", "Rival", "#e07b39", False, ""),
    ("mentor", "Mentor", "#6fa8dc", True, "la source est mentor de la cible"),
    ("hierarchie", "Hiérarchie", "#9c88d8", True, "la source commande la cible"),
    ("affaires", "Affaires", "#b8b25a", False, ""),
    ("dette", "Dette", "#a8825e", True, "la source doit quelque chose à la cible"),
    ("autre", "Autre", "#8a93a3", False, ""),
]


# Types de lieux fournis (clé, libellé, icône) ; « lieu » est le type par défaut et ne se supprime pas.
DEFAULT_PLACE_KINDS = [
    ("region", "Région", "region"),
    ("ville", "Ville", "city"),
    ("quartier", "Quartier", "district"),
    ("batiment", "Bâtiment", "house"),
    ("salle", "Salle", "door"),
    ("donjon", "Donjon, souterrain", "cave"),
    ("nature", "Nature", "tree"),
    ("lieu", "Lieu", "pin"),
]
DEFAULT_PLACE_KIND = "lieu"


def seed_place_kinds(conn: sqlite3.Connection, campaign_id: int) -> None:
    for order, (key, label, icon) in enumerate(DEFAULT_PLACE_KINDS):
        conn.execute("INSERT OR IGNORE INTO place_kinds (campaign_id, key, label, icon, sort_order) VALUES (?, ?, ?, ?, ?)",
                     (campaign_id, key, label, icon, order))


def seed_relation_types(conn: sqlite3.Connection, campaign_id: int) -> None:
    for order, (key, label, color, directed, hint) in enumerate(DEFAULT_RELATION_TYPES):
        db.insert(conn, "relation_types", {
            "campaign_id": campaign_id, "key": key, "label": label, "color": color,
            "directed": directed, "hint": hint, "sort_order": order,
        })


def slugify(text: str, max_length: int = 30) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:max_length].strip("-") or "nature"


def unique_type_key(conn: sqlite3.Connection, campaign_id: int, label: str, table: str = "relation_types") -> str:
    base = slugify(label)
    existing = {r[0] for r in conn.execute(
        f"SELECT key FROM {table} WHERE campaign_id = ?", (campaign_id,))}
    key, n = base, 2
    while key in existing:
        key = f"{base}-{n}"
        n += 1
    return key
