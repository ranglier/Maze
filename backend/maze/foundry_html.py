"""Texte des fiches de Maze converti en HTML pour les journaux de Foundry VTT (voir foundry.py).

Les champs longs des PNJ, factions et lieux sont en HTML (éditeur de Maze) ou, pour les plus anciens,
en Markdown. Les deux sont ramenés à un HTML simple, sans attribut libre, que l'éditeur ProseMirror de
Foundry accepte tel quel :

- mentions @ (`<a class="mention" data-mention="npc:12">`) et liens [[Nom]] du Markdown : `<a data-maze="npc:12">`,
  que le module maze-sync remplace par un lien `@UUID[...]` vers le journal de la fiche ;
- encadré « Secret » : bloc secret de Foundry (`<section class="secret" id="secret-…">`), visible du MJ seul,
  que le MJ peut révéler aux joueurs ; dans un champ public (page « Fiche », biographie de l'acteur), retiré et
  rendu à part (`secrets`), car le bloc secret de Foundry n'est qu'un masquage d'affichage (audit M6) ;
- encadré « Accroche » (texte à lire) : `<blockquote class="maze-read">` ;
- titres décalés d'un niveau (h1 > h3…) : les rubriques de la page sont en h2 ;
- commentaires, alignements, styles et éléments inconnus retirés (leur texte est gardé), scripts supprimés.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from html.parser import HTMLParser

MENTION = re.compile(r"^(npc|faction|place):(\d{1,12})$")
DOC_MENTION = re.compile(r"^doc:\d{1,12}$")
RICH_HTML = re.compile(r"^\s*<(p|h[1-6]|ul|ol|div|table|blockquote|hr|img|a)[\s>]", re.I)

# Balises gardées telles quelles (sans attribut, sauf colspan / rowspan des cellules).
KEEP = {"p", "br", "strong", "em", "u", "s", "code", "pre", "blockquote", "ul", "ol", "li", "hr",
        "table", "thead", "tbody", "tfoot", "tr", "th", "td", "sub", "sup"}
RENAME = {"b": "strong", "i": "em", "strike": "s", "del": "s", "ins": "u",
          "h1": "h3", "h2": "h4", "h3": "h5", "h4": "h6", "h5": "h6", "h6": "h6"}
VOID = {"br", "hr", "img", "input", "wbr", "col", "area", "source", "meta", "link"}
# Contenu supprimé avec la balise.
DROP = {"script", "style", "iframe", "object", "embed", "template", "svg", "math", "noscript", "select",
        "textarea", "button", "form", "head", "title"}
SAFE_HREF = re.compile(r"^(https?://|mailto:)", re.I)


def is_rich_html(text: str | None) -> bool:
    """Même règle que l'interface (`isRichHtml` de markdown.js) : HTML de l'éditeur, sinon Markdown."""
    return bool(RICH_HTML.match(text or ""))


# Fermeture d'un passage secret mis de côté (champ public, voir _Cleaner.secrets) : jamais écrite.
_END_MOVED = "\x00fin-du-passage-secret"
EMPTY_P = re.compile(r"<p>(\s|<br>)*</p>")


def _is_secret(tag: str, attr: dict[str, str]) -> bool:
    """Encadré « Secret » de l'éditeur, ou bloc secret déjà au format de Foundry (texte collé, import)."""
    if tag == "div":
        return attr.get("data-callout") == "secret"
    return tag == "section" and "secret" in attr.get("class", "").split()


class _Cleaner(HTMLParser):
    def __init__(self, secret_id: Callable[[], str], in_secret: bool, secrets: list[str] | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        # Pile des balises ouvertes dans la source : (balise source, fermeture à écrire ou "").
        self.stack: list[tuple[str, str]] = []
        self.secret_id = secret_id
        self.secret_depth = 1 if in_secret else 0
        self.dropping = 0
        # Champ public (audit M6) : les passages secrets ne sont pas écrits dans la sortie mais rangés ici, déjà
        # nettoyés (sans bloc secret imbriqué) ; le bloc secret de Foundry n'est qu'un masquage d'affichage.
        self.secrets = secrets
        self.outside: list[str] | None = None

    # Ouverture -------------------------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.dropping:
            if tag in DROP:
                self.dropping += 1
            return
        if tag in DROP:
            self.dropping = 1
            return
        attr = {k: (v or "") for k, v in attrs}
        opened, closing = self._open(tag, attr)
        if tag in VOID:
            self.out.append(opened)
            return
        self.out.append(opened)
        self.stack.append((tag, closing))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.dropping or tag in DROP:
            return
        if tag in VOID:
            self.handle_starttag(tag, attrs)
        else:  # <p/> : élément vide
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def _open(self, tag: str, attr: dict[str, str]) -> tuple[str, str]:
        if tag in KEEP:
            if tag in ("td", "th"):
                extra = "".join(f' {k}="{int(attr[k])}"' for k in ("colspan", "rowspan")
                                if attr.get(k, "").isdigit() and 1 < int(attr[k]) <= 100)
                return f"<{tag}{extra}>", f"</{tag}>"
            return (f"<{tag}>", "" if tag in VOID else f"</{tag}>")
        if tag in RENAME:
            new = RENAME[tag]
            return f"<{new}>", f"</{new}>"
        if tag == "a":
            if any(t == "a" and closing for t, closing in self.stack):
                return "", ""  # lien dans un lien : seul le premier est gardé
            mention = attr.get("data-mention", "")
            if MENTION.match(mention):
                return f'<a data-maze="{mention}">', "</a>"
            if DOC_MENTION.match(mention):  # renvoi vers une fiche rédigée : pas exportée, texte en italique
                return "<em>", "</em>"
            href = attr.get("href", "").strip()
            if SAFE_HREF.match(href):
                return f'<a href="{html.escape(href, quote=True)}" rel="noopener noreferrer">', "</a>"
            return "", ""
        if _is_secret(tag, attr):
            self.secret_depth += 1
            if self.secret_depth == 1:
                if self.secrets is not None:  # champ public : passage mis de côté
                    self.outside, self.out = self.out, []
                    return "", _END_MOVED
                return f'<section class="secret" id="{self.secret_id()}">', "</section>"
            return "<div>", "</div>"  # déjà dans un passage secret : pas de bloc imbriqué
        if tag == "div" and attr.get("data-callout") == "read":
            return '<blockquote class="maze-read">', "</blockquote>"
        # div, span (commentaires), img, mark… : balise retirée, contenu gardé.
        return "", ""

    # Fermeture -------------------------------------------------------------------------------
    def handle_endtag(self, tag: str) -> None:
        if self.dropping:
            if tag in DROP:
                self.dropping -= 1
            return
        if tag in VOID or not any(t == tag for t, _ in self.stack):
            return
        while self.stack:
            open_tag, closing = self.stack.pop()
            self._close(closing)
            if open_tag == tag:
                break

    def _close(self, closing: str) -> None:
        if closing == _END_MOVED:
            self.secret_depth -= 1
            moved = EMPTY_P.sub("", "".join(self.out)).strip()
            self.out, self.outside = self.outside or [], None
            if moved and self.secrets is not None:
                self.secrets.append(moved)
            return
        if closing == "</section>" or closing == "</div>" and self.secret_depth > 1:
            self.secret_depth -= 1
        self.out.append(closing)

    def handle_data(self, data: str) -> None:
        if not self.dropping:
            self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self._close(self.stack.pop()[1])
        return "".join(self.out)


def clean(fragment: str, secret_id: Callable[[], str], in_secret: bool = False,
          secrets: list[str] | None = None) -> str:
    """HTML de Maze ramené au HTML accepté par les journaux de Foundry (voir l'en-tête du module).
    `secret_id` fournit l'identifiant de chaque bloc secret ; `in_secret` : texte placé dans un passage
    déjà secret, dont les encadrés « Secret » ne créent pas de bloc imbriqué. `secrets` (champ public d'une
    page que les joueurs peuvent voir, audit M6) : les encadrés « Secret » sont retirés de la sortie et leur
    contenu nettoyé est ajouté à cette liste (à placer dans la page réservée au MJ, ou à jeter)."""
    if not fragment or not fragment.strip():
        return ""
    parser = _Cleaner(secret_id, in_secret, secrets)
    parser.feed(fragment)
    parser.close()
    out = parser.result()
    # Paragraphes vides laissés par l'éditeur.
    return EMPTY_P.sub("", out).strip()


# --- Markdown des anciennes fiches -------------------------------------------------------------

LIST_ITEM = re.compile(r"^\s*([-*+]|\d{1,3}[.)])\s+(.*)$")
# Titre : le texte jusqu'au bout de la ligne, sa séquence fermante retirée en Python (_heading_text). L'ancien motif
# (.*?)\s*#*\s*$ était cubique sur « # » suivi d'espaces : 9 s pour une ligne de 2 Ko (audit PERF-6).
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
QUOTE = re.compile(r"^\s*>\s?(.*)$")
RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
WIKILINK = re.compile(r"\[\[([^\]\n]{1,120})\]\]")


def _heading_text(text: str) -> str:
    """Texte d'un titre sans sa séquence fermante de « # », retirée seulement si elle suit une espace (CommonMark,
    comme marked dans l'interface : « # C# » reste « C# », « # C# # » donne « C# », « # Titre # » donne « Titre »).
    Coût linéaire, contrairement à un rstrip(" #") qui couperait « C# # » en « C » (contre-revue X3)."""
    text = text.rstrip()
    bare = text.rstrip("#")
    if bare != text and (not bare or bare[-1].isspace()):
        text = bare.rstrip()
    return text


# Gras et italique (audit PERF-6) : le contenu ne peut pas franchir le délimiteur qui commencerait ou fermerait une
# autre emphase (sinon chaque ouvrant sans fermant relisait toute la ligne, coût quadratique : 32 s pour 99 000
# caractères « *a »). Contenu admis : tout sauf le délimiteur, plus « * » seul (gras), « * » suivi d'une espace
# (italique) et « _ » entre deux lettres (snake_case), qui ne peuvent ni ouvrir ni fermer.
STRONG_STAR = re.compile(r"\*\*(?=\S)((?:[^*\n]|\*(?!\*))+?)(?<=\S)\*\*")
STRONG_UNDER = re.compile(r"__(?=\S)((?:[^_\n]|_(?!_))+?)(?<=\S)__")
EM_STAR = re.compile(r"(?<![\w*])\*(?=\S)((?:[^*\n]|\*(?=\s))+?)(?<=\S)\*(?![\w*])")
EM_UNDER = re.compile(r"(?<!\w)_(?=\S)((?:[^_\n]|(?<=\w)_(?=\w))+?)(?<=\S)_(?!\w)")


def _inline(text: str, resolve: Callable[[str], str | None]) -> str:
    """Gras, italique, code, liens et [[Nom]] d'une ligne de Markdown (le reste est échappé)."""
    holes: list[str] = []

    def hole(value: str) -> str:
        holes.append(value)
        return f"\x00{len(holes) - 1}\x00"

    def wikilink(m: re.Match[str]) -> str:
        name = m.group(1).strip()
        target = resolve(name.lower())
        label = html.escape(name, quote=False)
        return hole(f'<a class="mention" data-mention="{target}">{label}</a>' if target else label)

    def code(m: re.Match[str]) -> str:
        return hole(f"<code>{html.escape(m.group(1), quote=False)}</code>")

    def link(m: re.Match[str]) -> str:
        return hole(f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1), quote=False)}</a>')

    text = text.replace("\x00", "")
    text = WIKILINK.sub(wikilink, text)
    text = re.sub(r"`([^`\n]+)`", code, text)
    text = re.sub(r"\[([^\]\n]{1,300})\]\((https?://[^\s)]{1,2000})\)", link, text)
    text = html.escape(text, quote=False)
    text = STRONG_STAR.sub(r"<strong>\1</strong>", text)
    text = STRONG_UNDER.sub(r"<strong>\1</strong>", text)
    text = EM_STAR.sub(r"<em>\1</em>", text)
    text = EM_UNDER.sub(r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: holes[int(m.group(1))], text)


def markdown_to_html(text: str, resolve: Callable[[str], str | None]) -> str:
    """Markdown simple (paragraphes, retours à la ligne, listes, titres, citations, gras, italique,
    liens, [[Nom]]) en HTML de l'éditeur, comme l'interface le convertit à la première modification.
    `resolve(nom en minuscules)` renvoie « npc:12 », « faction:3 » ou « place:7 », ou None."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag = ""
    quote: list[str] = []

    def flush() -> None:
        nonlocal para, items, list_tag, quote
        if para:
            out.append("<p>" + "<br>".join(_inline(line.strip(), resolve) for line in para) + "</p>")
            para = []
        if items:
            out.append(f"<{list_tag}>" + "".join(f"<li><p>{_inline(i, resolve)}</p></li>" for i in items)
                       + f"</{list_tag}>")
            items, list_tag = [], ""
        if quote:
            out.append("<blockquote><p>" + "<br>".join(_inline(q, resolve) for q in quote) + "</p></blockquote>")
            quote = []

    for line in lines:
        if not line.strip():
            flush()
            continue
        if RULE.match(line):
            flush()
            out.append("<hr>")
            continue
        heading = HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(_heading_text(heading.group(2)), resolve)}</h{level}>")
            continue
        item = LIST_ITEM.match(line)
        if item:
            tag = "ol" if item.group(1)[0].isdigit() else "ul"
            if para or quote or (items and tag != list_tag):
                flush()
            list_tag = tag
            items.append(item.group(2))
            continue
        quoted = QUOTE.match(line)
        if quoted:
            if para or items:
                flush()
            quote.append(quoted.group(1))
            continue
        if items and line[:1].isspace():  # suite d'un élément de liste
            items[-1] += " " + line.strip()
            continue
        if items or quote:
            flush()
        para.append(line)
    flush()
    return "".join(out)


def field_html(text: str | None, resolve: Callable[[str], str | None], secret_id: Callable[[], str],
               in_secret: bool = False, secrets: list[str] | None = None) -> str:
    """Champ long d'une fiche de Maze (HTML ou ancien Markdown) en HTML pour Foundry (`secrets` : voir clean)."""
    if not text or not text.strip():
        return ""
    source = text if is_rich_html(text) else markdown_to_html(text, resolve)
    return clean(source, secret_id, in_secret, secrets)
