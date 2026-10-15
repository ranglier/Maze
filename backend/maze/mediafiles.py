"""Fichiers produits par Maze sous media/ : seule liste des extensions admises.

imaging.py n'enregistre que ces formats, toujours réencodés (jamais le fichier reçu) : WebP pour les images,
miniatures, versions d'affichage et icônes ; JPEG pour la pleine définition d'un fond de carte plus grand que
sa version d'affichage. Tout ce qui sert, exporte, sauvegarde ou restaure media/ s'y réfère : /media (app.py),
routes de Foundry (foundry.py), export et import d'une campagne (transfer.py), sauvegarde et restauration
(backup.py). Ajouter un format ici, et nulle part ailleurs côté serveur.

Côté interface, editor/sanitize.js, editor/schema.js et markdown.js n'admettent dans le texte des fiches que
/media/<campagne>/<nom>.webp : seules les illustrations y figurent, et elles restent en WebP (test_backup).
"""

from __future__ import annotations

import re

MEDIA_TYPES = {".webp": "image/webp", ".jpg": "image/jpeg"}   # extension > type servi
# Fichier en cours d'écriture (imaging.py : temporaire puis renommage, contre-revue du lot 1) : « .<nom>.tmp » dans
# media/<campagne>/, jamais servi ni sauvegardé, retiré au démarrage s'il a survécu à un arrêt brutal.
TEMP_SUFFIX = ".tmp"


def temp_name(name: str) -> str:
    return f".{name}{TEMP_SUFFIX}"


def is_temp(name: str) -> bool:
    return name.startswith(".") and name.endswith(TEMP_SUFFIX)
PIL_FORMATS = {".webp": "WEBP", ".jpg": "JPEG"}                # extension > format attendu par Pillow
# Nom d'un fichier de media/<campagne>/ : uuid hexadécimal, suffixe _t, _p ou _i éventuel, extension admise.
FILE_NAME = r"[\w-]{1,80}\.(?:" + "|".join(sorted(ext.lstrip(".") for ext in MEDIA_TYPES)) + ")"
FILE_NAME_RE = re.compile(rf"^{FILE_NAME}$")
