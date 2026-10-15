"""Balises <img> du texte d'une fiche rédigée ou d'un modèle (audit PERF-5, contre-revue du lot 6).

Découpage au sens du tokeniseur HTML : une valeur d'attribut entre guillemets va jusqu'au guillemet fermant, même
si elle contient « < » ou « > » (les navigateurs d'avant 2025 ne les échappaient pas dans un attribut : un
alt="Carte <ancienne>" enregistré alors n'était pas reconnu par le motif <img\\b[^<>]*>, et l'image supprimée restait
affichée cassée). Chaque caractère n'est lu qu'une fois, sans retour arrière : coût linéaire, même sur un texte
piégé (« <img " » répété sans guillemet fermant, « <img » sans « > ») ; une balise jamais fermée s'étend, comme dans
le navigateur, jusqu'à la fin du texte, et n'est pas retirée. Toutes les balises et les commentaires sont découpés de
la même façon, pour ne pas prendre pour une image un « <img » écrit dans un attribut ou un commentaire.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

# Début de balise (ouvrante ou fermante, nom commençant par une lettre), de commentaire ou de déclaration.
_OPEN = re.compile(r"<(?:(/?)([A-Za-z][^\t\n\f\r />]*)|(!--)|[!?])")
_SKIP = re.compile(r"[\t\n\f\r /]*")              # avant un nom d'attribut (« / » isolé ignoré)
_NAME = re.compile(r"[^\t\n\f\r />][^\t\n\f\r />=]*")
_SPACE = re.compile(r"[\t\n\f\r ]*")
_UNQUOTED = re.compile(r"[^\t\n\f\r >]*")


def img_tags(html: str) -> Iterator[tuple[int, int, dict[str, str]]]:
    """(début, fin, attributs) de chaque balise <img> complète ; noms d'attributs en minuscules, première valeur
    gardée (comme le navigateur), valeurs telles qu'écrites (entités non décodées)."""
    pos, size = 0, len(html)
    while (found := _OPEN.search(html, pos)) is not None:
        if found.group(3):   # commentaire : jusqu'à « --> »
            end = html.find("-->", found.end())
            if end < 0:
                return
            pos = end + 3
            continue
        if found.group(2) is None:   # <!…> ou <?…> : jusqu'au « > » suivant
            end = html.find(">", found.end())
            if end < 0:
                return
            pos = end + 1
            continue
        # Toute balise est découpée (un « <img » dans la valeur d'un attribut d'une autre balise n'en est pas une).
        start, i, attrs = found.start(), found.end(), {}
        is_img = not found.group(1) and found.group(2).lower() == "img"
        while True:
            i = _SKIP.match(html, i).end()
            if i >= size:
                return   # balise jamais fermée : tout le reste du texte en fait partie
            if html[i] == ">":
                if is_img:
                    yield start, i + 1, attrs
                pos = i + 1
                break
            name = _NAME.match(html, i)
            i = _SPACE.match(html, name.end()).end()
            value = ""
            if i < size and html[i] == "=":
                i = _SPACE.match(html, i + 1).end()
                if i < size and html[i] in "\"'":
                    end = html.find(html[i], i + 1)
                    if end < 0:
                        return   # guillemet jamais fermé : de même
                    value, i = html[i + 1:end], end + 1
                else:
                    unquoted = _UNQUOTED.match(html, i)
                    value, i = unquoted.group(), unquoted.end()
            attrs.setdefault(name.group().lower(), value)


def remove_imgs(html: str, drop: Callable[[dict[str, str]], bool] = lambda attrs: True) -> str:
    """Le texte sans les balises <img> pour lesquelles drop(attributs) est vrai (toutes par défaut)."""
    parts, last = [], 0
    for start, end, attrs in img_tags(html):
        if drop(attrs):
            parts.append(html[last:start])
            last = end
    if not last:
        return html
    parts.append(html[last:])
    return "".join(parts)


def doc_image_id(attrs: dict[str, str]) -> int | None:
    """Numéro de l'illustration (data-doc-image) d'une balise <img>, s'il est valide."""
    value = attrs.get("data-doc-image", "")
    return int(value) if value.isascii() and value.isdigit() else None
