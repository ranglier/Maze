import DOMPurify from 'dompurify';

// Contenu autorisé dans les fiches rédigées (tout le reste est retiré).
const TAGS = ['p', 'br', 'h1', 'h2', 'h3', 'strong', 'b', 'em', 'i', 'u', 's', 'strike', 'del',
  'ul', 'ol', 'li', 'blockquote', 'a', 'hr', 'img', 'div', 'span', 'sub', 'sup', 'code',
  'table', 'thead', 'tbody', 'tr', 'th', 'td', 'colgroup', 'col'];
const ATTRS = ['href', 'class', 'id', 'data-mention', 'data-callout', 'data-doc-image', 'data-comment', 'src', 'alt',
  'contenteditable', 'target', 'rel', 'colspan', 'rowspan', 'data-colwidth', 'data-align'];
const CLASSES = new Set(['mention', 'callout', 'comment-anchor']);
const CALLOUTS = new Set(['read', 'secret']);
const MEDIA_SRC = /^\/media\/\d+\/[\w-]+\.webp$/;
const MENTION = /^(npc|faction|place|doc):\d+$/;
const ALIGNS = new Set(['center', 'right', 'justify']);
const ALIGNABLE = new Set(['P', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6']);

let hooked = false;
let active = false; // le hook ne s'applique qu'aux fiches rédigées, pas aux autres usages de DOMPurify
function installHooks() {
  if (hooked) return;
  hooked = true;
  // Collage depuis un traitement de texte : l'alignement (style text-align, retiré ensuite) devient data-align.
  DOMPurify.addHook('beforeSanitizeAttributes', (node) => {
    if (!active || node.nodeType !== 1 || !ALIGNABLE.has(node.tagName) || node.hasAttribute('data-align')) return;
    const align = node.style?.textAlign;
    if (ALIGNS.has(align)) node.setAttribute('data-align', align);
  });
  DOMPurify.addHook('afterSanitizeAttributes', (node) => {
    if (!active || node.nodeType !== 1) return;
    // Classes : uniquement celles de l'éditeur (le collage depuis le web en apporte beaucoup).
    if (node.hasAttribute('class')) {
      const kept = node.getAttribute('class').split(/\s+/).filter((c) => CLASSES.has(c));
      if (kept.length) node.setAttribute('class', kept.join(' '));
      else node.removeAttribute('class');
    }
    const tag = node.tagName;
    if (node.hasAttribute('data-align') && !(ALIGNABLE.has(tag) && ALIGNS.has(node.getAttribute('data-align')))) {
      node.removeAttribute('data-align');
    }
    if (tag === 'IMG') {
      // Images hébergées par Maze seulement : pas de pixel espion ni de contenu externe.
      if (!MEDIA_SRC.test(node.getAttribute('src') || '')) node.remove();
      return;
    }
    if (tag === 'DIV' && node.hasAttribute('data-callout') && !CALLOUTS.has(node.getAttribute('data-callout'))) {
      node.removeAttribute('data-callout');
    }
    if (tag === 'A') {
      const mention = node.getAttribute('data-mention');
      if (mention && MENTION.test(mention)) {
        node.setAttribute('href', '#mention');
        node.setAttribute('class', 'mention');
        node.setAttribute('contenteditable', 'false');
        node.removeAttribute('target');
        node.removeAttribute('rel');
        return;
      }
      node.removeAttribute('data-mention');
      node.removeAttribute('contenteditable');
      const href = node.getAttribute('href') || '';
      if (/^(https?:|mailto:)/i.test(href)) {
        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
      } else if (!href.startsWith('#')) {
        node.removeAttribute('href');
      }
      return;
    }
    node.removeAttribute('contenteditable');
  });
}

export function sanitizeDoc(html) {
  installHooks();
  active = true;
  try {
    return DOMPurify.sanitize(html || '', {
      ALLOWED_TAGS: TAGS,
      ALLOWED_ATTR: ATTRS,
      ALLOW_DATA_ATTR: false,
      // « npc:12 » ressemble à une URL avec un protocole inconnu : sans ceci, DOMPurify le retire.
      ADD_URI_SAFE_ATTR: ['data-mention', 'data-callout', 'data-doc-image', 'data-comment'],
    });
  } finally {
    active = false;
  }
}

export function slugify(text) {
  return (text || '')
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
    .slice(0, 60) || 'section';
}
