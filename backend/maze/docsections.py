"""Modification ciblée d'une fiche rédigée par sections (outil update_document du connecteur Claude, paramètre
operations) et plan de ses titres (outil get, kind document : outline). Module pur, sans base ni serveur.

Section : un titre <h1>-<h3> de premier niveau (hors encadré, citation, liste ou tableau) et tout ce qui le suit
jusqu'au prochain titre de premier niveau de niveau égal ou supérieur, sous-sections comprises. <h4>-<h6> comptent
comme <h3>, comme dans l'éditeur (editor/schema.js). Un titre se désigne par son texte (entités décodées), sans tenir
compte de la casse ni des espaces en trop ; refus s'il est introuvable ou ambigu (occurrence pour choisir), avec la
liste des titres de la fiche dans le message.

Les opérations découpent le HTML enregistré sans le réécrire : tout ce qui est hors des zones remplacées reste octet
pour octet (ancres de commentaires <span data-comment>, illustrations <img data-doc-image>, mentions). Le HTML fourni
est enregistré tel quel, comme content (le serveur ne le réécrit pas ; l'interface le filtre à l'affichage,
editor/sanitize.js), après une seule vérification : ses balises sont équilibrées, pour qu'il n'englobe pas la suite
de la fiche. Découpage au sens du tokeniseur
HTML, comme htmlimg.py : coût linéaire, sans retour arrière (piles d'indices par nom de balise : une fermeture
cherche son ouvrante sans parcourir la pile). Plus de MAX_DEPTH balises imbriquées : plan vide avec une note,
opérations refusées (un MJ peut enregistrer n'importe quel HTML).
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

# Début de balise (ouvrante ou fermante, nom commençant par une lettre), de commentaire ou de déclaration.
_OPEN = re.compile(r"<(?:(/?)([A-Za-z][^\t\n\f\r />]*)|(!--)|[!?])")
_SKIP = re.compile(r"[\t\n\f\r /]*")
_NAME = re.compile(r"[^\t\n\f\r />][^\t\n\f\r />=]*")
_SPACE = re.compile(r"[\t\n\f\r ]*")
_UNQUOTED = re.compile(r"[^\t\n\f\r >]*")
_COMMENT_ANCHOR = re.compile(r"\sdata-comment=\"\d+\"")
_DOC_IMAGE = re.compile(r"\sdata-doc-image=\"\d+\"")

HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 3, "h5": 3, "h6": 3}
VOID = frozenset("area base br col embed hr img input link meta source track wbr".split())
# Ouverture qui ferme un <p> resté ouvert (règle du navigateur, simplifiée).
_CLOSES_P = frozenset("address article aside blockquote details div dl fieldset figcaption figure footer form h1 h2 "
                      "h3 h4 h5 h6 header hr main nav ol p pre section table ul".split())
_P_SCOPE = frozenset("button caption table td th".split())
MAX_DEPTH = 200   # balises imbriquées au plus (plan des titres, opérations)

OPS = ("replace_section", "insert_after_section", "insert_before_section", "append", "prepend", "delete_section",
       "replace_text")
# Paramètres admis (obligatoires, facultatifs) de chaque opération.
_PARAMS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "replace_section": (("heading", "content"), ("include_heading", "occurrence")),
    "insert_after_section": (("heading", "content"), ("occurrence",)),
    "insert_before_section": (("heading", "content"), ("occurrence",)),
    "append": (("content",), ()),
    "prepend": (("content",), ()),
    "delete_section": (("heading",), ("occurrence",)),
    "replace_text": (("find", "replace"), ("max", "heading", "occurrence")),
}
_TYPES = {"heading": str, "content": str, "find": str, "replace": str, "include_heading": bool, "occurrence": int,
          "max": int}
MAX_OPERATIONS = 50
MAX_REPLACEMENTS = 1000
_LISTED = 40   # titres cités au plus dans un message

OPERATIONS_SCHEMA: dict[str, Any] = {
    "type": "array", "minItems": 1, "maxItems": MAX_OPERATIONS,
    "description": (
        "Modifications ciblées, appliquées dans l'ordre au contenu enregistré (exclusif avec content ; tout ou rien). "
        "Section : un titre <h1>-<h3> hors encadré, liste ou tableau, et ce qui le suit jusqu'au prochain titre de "
        "niveau égal ou supérieur (sous-sections comprises) ; heading : texte du titre (casse et espaces ignorés ; "
        "voir outline de get, kind document), occurrence (1, 2…) si plusieurs titres l'ont. replace_section {heading, "
        "content, include_heading?} : remplace le contenu sous le titre (le titre aussi si include_heading) ; "
        "insert_after_section / insert_before_section {heading, content} ; append / prepend {content} : fin / début "
        "de la fiche ; delete_section {heading} ; replace_text {find, replace, max?, heading?} : texte simple dans le "
        "texte affiché (pas les attributs, ni à cheval sur une mise en forme), refus si absent ou plus de max fois "
        "(1 par défaut). content : HTML de l'éditeur, balises équilibrées."),
    "items": {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": list(OPS)},
            "heading": {"type": "string"},
            "occurrence": {"type": "integer", "minimum": 1},
            "content": {"type": "string"},
            "include_heading": {"type": "boolean"},
            "find": {"type": "string"},
            "replace": {"type": "string"},
            "max": {"type": "integer", "minimum": 1, "maximum": MAX_REPLACEMENTS},
        },
        "required": ["op"], "additionalProperties": False,
    },
}


class SectionError(ValueError):
    """Opération impossible (message en français, montré à Claude) : rien n'est modifié."""


# --- Découpage -------------------------------------------------------------------------------------------------------

def _tokens(src: str) -> Iterator[tuple[str, int, int, str]]:
    """(genre, début, fin, nom) : text, open, close, comment (commentaire ou déclaration), broken (balise ou
    commentaire jamais fermé : jusqu'à la fin du texte). Noms de balises en minuscules."""
    pos, size = 0, len(src)
    while (found := _OPEN.search(src, pos)) is not None:
        start = found.start()
        if start > pos:
            yield "text", pos, start, ""
        if found.group(3) or found.group(2) is None:
            end = src.find("-->" if found.group(3) else ">", found.end())
            if end < 0:
                yield "broken", start, size, ""
                return
            pos = end + (3 if found.group(3) else 1)
            yield "comment", start, pos, ""
            continue
        i = found.end()
        while True:
            i = _SKIP.match(src, i).end()
            if i >= size:
                yield "broken", start, size, ""
                return
            if src[i] == ">":
                break
            i = _SPACE.match(src, _NAME.match(src, i).end()).end()
            if i < size and src[i] == "=":
                i = _SPACE.match(src, i + 1).end()
                if i < size and src[i] in "\"'":
                    end = src.find(src[i], i + 1)
                    if end < 0:
                        yield "broken", start, size, ""
                        return
                    i = end + 1
                else:
                    i = _UNQUOTED.match(src, i).end()
        pos = i + 1
        yield ("close" if found.group(1) else "open"), start, pos, found.group(2).lower()
    if pos < size:
        yield "text", pos, size, ""


@dataclass
class Heading:
    level: int
    text: str
    start: int        # début de la balise ouvrante
    end: int          # fin de la balise fermante (ou de ce qui ferme le titre)


@dataclass
class _Scan:
    headings: list[Heading]
    open: list[str]       # balises restées ouvertes à la fin
    strays: list[str]     # balises fermantes sans ouvrante
    broken: bool
    too_deep: bool = False      # plus de MAX_DEPTH balises imbriquées : découpage arrêté là


_LI_LIMITS = ("ul", "ol")
_CELL_LIMITS = ("tr", "table")
_TR_LIMITS = ("table", "thead", "tbody", "tfoot")


def _scan(src: str) -> _Scan:
    """Titres de premier niveau et équilibre des balises (fermetures implicites usuelles : <p>, <li>, <tr>, <td>).
    Coût linéaire : à côté de la pile, l'indice de chaque balise ouverte est rangé par nom (where), d'où la balise
    ouverte la plus proche d'un nom donné sans parcourir la pile."""
    stack: list[str] = []
    where: dict[str, list[int]] = {}
    heads: list[Heading] = []
    strays: list[str] = []
    broken = too_deep = False
    current: Heading | None = None
    parts: list[str] = []

    def nearest(names: tuple[str, ...] | frozenset[str]) -> int:
        """Indice dans la pile de la plus proche balise ouverte de l'un de ces noms (-1 : aucune)."""
        return max((where[n][-1] for n in names if where.get(n)), default=-1)

    def pop_to(index: int, at: int) -> None:
        nonlocal current
        while len(stack) > index:
            where[stack.pop()].pop()
        if not stack and current is not None:
            current.end = at
            current.text = " ".join(unicodedata.normalize("NFC", "".join(parts)).split())
            heads.append(current)
            current = None

    def close_nearest(names: tuple[str, ...], limits: tuple[str, ...] | frozenset[str], at: int) -> None:
        i = nearest(names)
        if i >= 0 and i > nearest(limits):
            pop_to(i, at)

    for kind, start, end, name in _tokens(src):
        if kind == "text":
            if current is not None:
                parts.append(html.unescape(src[start:end]))
        elif kind == "broken":
            broken = True
        elif kind == "open":
            if stack:
                if name in _CLOSES_P:
                    close_nearest(("p",), _P_SCOPE, start)
                if name in HEADINGS and stack and stack[-1] in HEADINGS:
                    pop_to(len(stack) - 1, start)
                elif name == "li":
                    close_nearest(("li",), _LI_LIMITS, start)
                elif name in ("td", "th"):
                    close_nearest(("td", "th"), _CELL_LIMITS, start)
                elif name == "tr":
                    close_nearest(("tr",), _TR_LIMITS, start)
            if name in VOID:
                if current is not None and name == "br":
                    parts.append(" ")
                continue
            if not stack and name in HEADINGS:
                current, parts = Heading(HEADINGS[name], "", start, len(src)), []
            if len(stack) >= MAX_DEPTH:
                too_deep = True
                break
            where.setdefault(name, []).append(len(stack))
            stack.append(name)
        elif kind == "close":
            if where.get(name):
                pop_to(where[name][-1], end)
            elif name not in VOID:
                strays.append(name)
    open_tags = list(stack)
    pop_to(0, len(src))
    return _Scan([] if too_deep else heads, open_tags, strays, broken, too_deep)


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def describe(src: str) -> tuple[list[dict[str, Any]], str | None]:
    """(titres de premier niveau de la fiche, dans l'ordre : ceux que les opérations par section désignent ; note
    si le plan n'a pas pu être établi)."""
    scan = _scan(src or "")
    if scan.too_deep:
        return [], (f"Plan non établi : la fiche a plus de {MAX_DEPTH} balises imbriquées ; operations "
                    "d'update_document impossibles, utiliser content (contenu complet).")
    return [{"level": h.level, "text": h.text} for h in scan.headings], None


def outline(src: str) -> list[dict[str, Any]]:
    return describe(src)[0]


# --- Sections --------------------------------------------------------------------------------------------------------

def _quote(text: str) -> str:
    return f"« {text} »"


def _listing(heads: list[Heading]) -> str:
    if not heads:
        return ("La fiche n'a aucun titre <h1>-<h3> hors encadré, liste ou tableau : utiliser append, prepend, "
                "replace_text ou content.")
    shown = ", ".join(f"{_quote(h.text)} (h{h.level})" for h in heads[:_LISTED])
    more = f" et {len(heads) - _LISTED} autres" if len(heads) > _LISTED else ""
    return f"Titres de la fiche : {shown}{more}."


def _parent(heads: list[Heading], index: int) -> Heading | None:
    for h in reversed(heads[:index]):
        if h.level < heads[index].level:
            return h
    return None


def _section(src: str, heads: list[Heading], query: str, occurrence: int | None) -> tuple[Heading, int, int]:
    """(titre, fin de la section, nombre de sous-sections) du titre désigné."""
    key = _norm(query)
    if not key:
        raise SectionError("heading vide : texte d'un titre de la fiche attendu. " + _listing(heads))
    found = [i for i, h in enumerate(heads) if _norm(h.text) == key]
    if not found:
        raise SectionError(f"Titre {_quote(query)} introuvable. " + _listing(heads))
    if occurrence is not None:
        if occurrence > len(found):
            raise SectionError(f"occurrence {occurrence} : le titre {_quote(query)} n'apparaît que {len(found)} fois")
        index = found[occurrence - 1]
    elif len(found) > 1:
        where = []
        for n, i in enumerate(found, 1):
            parent = _parent(heads, i)
            under = f", sous {_quote(parent.text)}" if parent else ""
            where.append(f"{n} : h{heads[i].level}{under}")
        raise SectionError(f"Titre {_quote(query)} ambigu ({len(found)} titres) : préciser occurrence "
                           f"({' ; '.join(where)}).")
    else:
        index = found[0]
    head = heads[index]
    end, subsections = len(src), 0
    for h in heads[index + 1:]:
        if h.level <= head.level:
            end = h.start
            break
        subsections += 1
    return head, end, subsections


def _check_fragment(content: str) -> None:
    scan = _scan(content)
    if scan.too_deep:
        raise SectionError(f"content : plus de {MAX_DEPTH} balises imbriquées")
    if scan.broken:
        raise SectionError("content : HTML incomplet (balise, guillemet ou commentaire jamais fermé)")
    if scan.strays:
        raise SectionError(f"content : balise </{scan.strays[0]}> sans balise ouvrante")
    if scan.open:
        raise SectionError(f"content : balise <{scan.open[-1]}> non fermée")


def _replace_text(src: str, find: str, replace: str, maximum: int, lo: int, hi: int) -> tuple[str, int]:
    """Remplace find par replace dans les nœuds texte compris entre lo et hi (entités décodées pour la recherche ;
    seuls les nœuds modifiés sont réécrits)."""
    edits: list[tuple[int, int, str]] = []
    count = 0
    stack_raw = 0   # profondeur dans <script>/<style> (texte brut) : jamais modifié
    for kind, start, end, name in _tokens(src):
        if kind in ("open", "close") and name in ("script", "style"):
            stack_raw = max(0, stack_raw + (1 if kind == "open" else -1))
        if kind != "text" or stack_raw or start < lo or end > hi:
            continue
        plain = html.unescape(src[start:end])
        n = plain.count(find)
        if n:
            count += n
            edits.append((start, end, html.escape(plain.replace(find, replace), quote=False)))
    if not count:
        raise SectionError(f"Texte {_quote(find)} introuvable (texte affiché, d'un seul tenant : pas à cheval sur une "
                           "mise en forme, un lien ou un paragraphe ; casse exacte)")
    if count > maximum:
        raise SectionError(f"Texte {_quote(find)} trouvé {count} fois (max {maximum}) : préciser find, ou passer "
                           f"max={count} pour tout remplacer")
    out, last = [], 0
    for start, end, text in edits:
        out += [src[last:start], text]
        last = end
    out.append(src[last:])
    return "".join(out), count


def _params(index: int, op: Any) -> tuple[str, dict[str, Any]]:
    where = f"operations[{index}]"
    if not isinstance(op, dict):
        raise SectionError(f"{where} : objet attendu, avec op parmi {', '.join(OPS)}")
    name = op.get("op")
    if name not in _PARAMS:
        raise SectionError(f"{where} : op parmi {', '.join(OPS)}")
    required, optional = _PARAMS[name]
    unknown = sorted(set(op) - {"op", *required, *optional})
    if unknown:
        raise SectionError(f"{where} ({name}) : paramètre(s) inconnu(s) {', '.join(unknown)} ; admis : "
                           f"{', '.join((*required, *optional))}")
    missing = [k for k in required if op.get(k) is None]
    if missing:
        raise SectionError(f"{where} ({name}) : paramètre(s) manquant(s) {', '.join(missing)}")
    values = {k: v for k, v in op.items() if k != "op" and v is not None}
    for key, value in values.items():
        expected = _TYPES[key]
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            kind = {str: "texte", bool: "booléen", int: "entier"}[expected]
            raise SectionError(f"{where} ({name}) : {key} doit être un {kind}")
    if values.get("occurrence", 1) < 1:
        raise SectionError(f"{where} ({name}) : occurrence à partir de 1")
    if not 1 <= values.get("max", 1) <= MAX_REPLACEMENTS:
        raise SectionError(f"{where} ({name}) : max entre 1 et {MAX_REPLACEMENTS}")
    return name, values


def _apply(src: str, name: str, p: dict[str, Any]) -> tuple[str, str]:
    """Une opération : (nouveau HTML, résumé)."""
    content = p.get("content")
    if content is not None:
        if not content.strip() and name != "replace_section":
            raise SectionError("content vide")
        _check_fragment(content)
    if name == "append":
        return src + content, "Ajout à la fin de la fiche"
    if name == "prepend":
        return content + src, "Ajout au début de la fiche"
    heads = _scan(src).headings
    if name == "replace_text":
        if not p["find"]:
            raise SectionError("find vide")
        lo, hi, scope = 0, len(src), ""
        if "heading" in p:
            head, hi, _ = _section(src, heads, p["heading"], p.get("occurrence"))
            lo, scope = head.start, f" dans la section {_quote(head.text)}"
        elif "occurrence" in p:
            raise SectionError("occurrence sans heading")
        out, count = _replace_text(src, p["find"], p["replace"], p.get("max", 1), lo, hi)
        return out, f"Texte {_quote(p['find'])} remplacé {count} fois{scope}"
    head, end, subsections = _section(src, heads, p["heading"], p.get("occurrence"))
    label = _quote(head.text)
    sub = f" ({subsections} sous-section{'s' if subsections > 1 else ''} comprise{'s' if subsections > 1 else ''})" \
        if subsections else ""
    if name == "replace_section":
        if p.get("include_heading"):
            return src[:head.start] + content + src[end:], f"Section {label} remplacée, titre compris{sub}"
        return src[:head.end] + content + src[end:], f"Contenu de la section {label} remplacé{sub}"
    if name == "insert_after_section":
        return src[:end] + content + src[end:], f"Ajout après la section {label}"
    if name == "insert_before_section":
        return src[:head.start] + content + src[head.start:], f"Ajout avant la section {label}"
    return src[:head.start] + src[end:], f"Section {label} supprimée{sub}"   # delete_section


def apply_operations(src: str, operations: Any) -> tuple[str, list[str]]:
    """Applique les opérations dans l'ordre (chacune voit le résultat des précédentes) : (nouveau HTML, résumés).
    SectionError à la première impossible : rien n'est appliqué."""
    if not isinstance(operations, list) or not operations:
        raise SectionError("operations : liste d'au moins une opération attendue")
    if len(operations) > MAX_OPERATIONS:
        raise SectionError(f"operations : {MAX_OPERATIONS} au plus par appel")
    steps = [_params(i, op) for i, op in enumerate(operations)]   # tout vérifier avant d'appliquer
    if _scan(src or "").too_deep:
        raise SectionError(f"La fiche a plus de {MAX_DEPTH} balises imbriquées : modification par sections "
                           "impossible, utiliser content (contenu complet).")
    out, changes = src or "", []
    for i, (name, params) in enumerate(steps):
        try:
            out, change = _apply(out, name, params)
        except SectionError as exc:
            raise SectionError(f"operations[{i}] ({name}) : {exc}") from None
        changes.append(change)
    for pattern, what in ((_COMMENT_ANCHOR, "passage(s) commenté(s) retiré(s) du texte (les fils restent dans le "
                                            "panneau Commentaires)"),
                          (_DOC_IMAGE, "illustration(s) retirée(s) du texte")):
        lost = len(pattern.findall(src or "")) - len(pattern.findall(out))
        if lost > 0:
            changes.append(f"{lost} {what}")
    return out, changes
