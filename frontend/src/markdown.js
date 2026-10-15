import { marked } from 'marked';
import DOMPurify from 'dompurify';

marked.setOptions({ gfm: true, breaks: true });

const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

// Liste blanche des anciens champs (audit FRONT-5) : ce que produit marked (GFM) et ce qu'enregistre l'éditeur
// (editor/schema.js), parfois collé au milieu du Markdown. Rien d'autre : ni id, ni data-* quelconques, ni style,
// ni dialog, details, section, video, formulaire… (un bloc aux classes de l'interface recouvrait tout l'écran).
const TAGS = ['p', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b', 'em', 'i', 'u', 's', 'strike', 'del',
  'code', 'pre', 'sub', 'sup', 'ul', 'ol', 'li', 'blockquote', 'hr', 'a', 'img', 'span', 'div',
  'table', 'thead', 'tbody', 'tr', 'th', 'td', 'colgroup', 'col',
  // Balises sémantiques sans risque, parfois collées dans d'anciennes notes (contre-revue). center et font
  // (obsolètes) restent du texte simple.
  'mark', 'kbd', 'small', 'abbr', 'dl', 'dt', 'dd', 'caption', 'tfoot', 'ins', 'q', 'cite', 'figure', 'figcaption'];
// Attributs data-* de l'éditeur et des liens [[Nom]], avec leurs valeurs admises.
const DATA_VALUES = {
  'data-node': /^(npc|faction|place)-\d+$/,
  'data-mention': /^(npc|faction|place|doc):\d+$/,
  'data-callout': /^(read|secret)$/,
  'data-doc-image': /^\d+$/,
  'data-comment': /^\d+$/,
  'data-align': /^(center|right|justify)$/,
  'data-colwidth': /^\d+(,\d+)*$/,
};
// width et height : images seulement, en pixels (hook ci-dessous). Pas d'id, même sur un titre : il permet d'écraser
// un élément que l'application cherche par document.getElementById (DOM clobbering ; le sommaire de l'éditeur retrouve
// ainsi ses titres, aujourd'hui sur un autre écran que les anciens champs), et une ancre #… ne mènerait nulle part
// (l'adresse après # est celle de la page de Maze) : préfixé, il ne servirait à rien.
const ATTRS = ['href', 'rel', 'title', 'src', 'alt', 'class', 'align', 'start', 'colspan', 'rowspan', 'width', 'height',
  ...Object.keys(DATA_VALUES)];
const PIXELS = /^\d{1,4}$/;
// Classes gardées : liens [[Nom]] et éléments de l'éditeur, pas celles de l'interface (lightbox, modal-backdrop…).
const CLASSES = new Set(['wikilink', 'missing', 'mention', 'callout', 'comment-anchor']);
const CODE_CLASS = /^language-[\w+#-]{1,40}$/;   // bloc de code de marked (```js)
const MEDIA_SRC = /^\/media\/\d+\/[\w-]+\.webp$/;
// « //hôte/… » (ou avec des barres obliques inverses) : lien vers un autre site qui ne le dit pas.
const PROTOCOL_RELATIVE = /^[\s\u0000-\u001f]*[\\/][\\/]/;

/**
 * Rend du Markdown en HTML assaini. Les références [[Nom]] deviennent des liens
 * vers la fiche correspondante quand `resolve(nom)` trouve un nœud.
 */
export function renderMarkdown(text, resolve) {
  if (!text) return '';
  const withLinks = text.replace(/\[\[([^\]\n]{1,120})\]\]/g, (_, raw) => {
    const name = raw.trim();
    const target = resolve?.(name);
    if (!target) return `<span class="wikilink missing" title="Aucune fiche à ce nom">${escapeHtml(name)}</span>`;
    return `<a href="#" class="wikilink" data-node="${target}">${escapeHtml(name)}</a>`;
  });
  active = true;
  try {
    return DOMPurify.sanitize(marked.parse(withLinks), {
      ALLOWED_TAGS: TAGS,
      ALLOWED_ATTR: ATTRS,
      ALLOW_DATA_ATTR: false,
      ALLOW_ARIA_ATTR: false,
      // « npc:12 » ressemble à une URL au protocole inconnu : ces valeurs sont vérifiées par le hook ci-dessous.
      ADD_URI_SAFE_ATTR: Object.keys(DATA_VALUES),
    });
  } finally {
    active = false;
  }
}

// Classes et data-* : valeurs admises seulement. Images : seulement celles hébergées par Maze (pas de pixel
// espion). Liens externes : nouvel onglet ; liens « //hôte » : sans adresse.
let active = false;
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (!active || node.nodeType !== 1) return;
  if (node.hasAttribute('class')) {
    const kept = node.getAttribute('class').split(/\s+/)
      .filter((c) => CLASSES.has(c) || (node.tagName === 'CODE' && CODE_CLASS.test(c)));
    if (kept.length) node.setAttribute('class', kept.join(' '));
    else node.removeAttribute('class');
  }
  for (const [name, valid] of Object.entries(DATA_VALUES)) {
    if (node.hasAttribute(name) && !valid.test(node.getAttribute(name))) node.removeAttribute(name);
  }
  for (const name of ['width', 'height']) {
    if (node.hasAttribute(name) && (node.tagName !== 'IMG' || !PIXELS.test(node.getAttribute(name)))) node.removeAttribute(name);
  }
  if (node.tagName === 'IMG' && !MEDIA_SRC.test(node.getAttribute('src') || '')) node.remove();
  if (node.tagName === 'A') {
    // Tabulations et retours à la ligne : retirés de l'adresse par le navigateur (« /\t/hôte » ouvre « //hôte »).
    const href = (node.getAttribute('href') || '').replace(/[\t\n\r]/g, '');
    if (PROTOCOL_RELATIVE.test(href)) {
      node.removeAttribute('href');
    } else if (/^https?:/i.test(href)) {
      node.setAttribute('target', '_blank');
      node.setAttribute('rel', 'noopener noreferrer');
    }
  }
});

/** Texte enregistré par l'éditeur (HTML) plutôt qu'en Markdown (fiches plus anciennes). */
export const isRichHtml = (text) => /^\s*<(p|h[1-6]|ul|ol|div|table|blockquote|hr|img|a)[\s>]/i.test(text || '');

/**
 * Contenu de départ de l'éditeur pour un champ : HTML tel quel ; Markdown converti, les références
 * [[Nom]] trouvées devenant des mentions @.
 */
export function markdownToEditorHtml(text, resolve) {
  if (!text) return '';
  if (isRichHtml(text)) return text;
  const box = document.createElement('div');
  box.innerHTML = renderMarkdown(text, resolve);
  for (const a of box.querySelectorAll('a.wikilink[data-node]')) {
    const m = a.dataset.node.match(/^(npc|faction|place)-(\d+)$/);
    if (!m) continue;
    const mention = document.createElement('a');
    mention.className = 'mention';
    mention.setAttribute('data-mention', `${m[1]}:${m[2]}`);
    mention.textContent = a.textContent;
    a.replaceWith(mention);
  }
  return box.innerHTML;
}
