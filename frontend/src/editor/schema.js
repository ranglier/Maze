import { DOMParser as PMDOMParser, DOMSerializer, Schema } from 'prosemirror-model';
import { tableNodes } from 'prosemirror-tables';
import { sanitizeDoc } from './sanitize.js';

/*
 * Structure des fiches rédigées. Tout ce qui n'est pas décrit ici est écarté au collage et à la
 * conversion des anciennes fiches. Les attributs sont revérifiés à l'affichage (toDOM) : une
 * modification reçue d'un autre navigateur ne passe pas par les règles de lecture.
 */

const MEDIA_SRC = /^\/media\/\d+\/[\w-]+\.webp$/;
const MENTION = /^(npc|faction|place|doc):(\d+)$/;
export const CALLOUTS = ['read', 'secret'];
// Alignement des paragraphes et titres (à gauche par défaut, donc sans attribut).
export const ALIGNS = ['center', 'right', 'justify'];
const alignOf = (dom) => {
  const value = dom.getAttribute('data-align') || dom.style?.textAlign || '';
  return ALIGNS.includes(value) ? value : null;
};
const alignAttr = (node) => (ALIGNS.includes(node.attrs.align) ? { 'data-align': node.attrs.align } : {});

export const safeHref = (href) => (/^(https?:|mailto:)/i.test(href || '') || /^#[\w-]*$/.test(href || '') ? href : null);
const safeSrc = (src) => (MEDIA_SRC.test(src || '') ? src : '');

export const schema = new Schema({
  nodes: {
    doc: { content: 'block+' },
    paragraph: {
      attrs: { align: { default: null } },
      content: 'inline*', group: 'block',
      parseDOM: [{ tag: 'p', getAttrs: (dom) => ({ align: alignOf(dom) }) }],
      toDOM: (node) => ['p', alignAttr(node), 0],
    },
    heading: {
      attrs: { level: { default: 2 }, align: { default: null } },
      content: 'inline*', group: 'block', defining: true,
      parseDOM: [['h1', 1], ['h2', 2], ['h3', 3], ['h4', 3], ['h5', 3], ['h6', 3]].map(([tag, level]) => ({
        tag, getAttrs: (dom) => ({ level, align: alignOf(dom) }),
      })),
      toDOM: (node) => [`h${Math.min(3, Math.max(1, Number(node.attrs.level) || 2))}`, alignAttr(node), 0],
    },
    blockquote: {
      content: 'block+', group: 'block', defining: true,
      parseDOM: [{ tag: 'blockquote' }],
      toDOM: () => ['blockquote', 0],
    },
    callout: {
      attrs: { variant: { default: 'read' } },
      content: 'block+', group: 'block', defining: true,
      parseDOM: [{
        tag: 'div[data-callout]',
        getAttrs: (dom) => (CALLOUTS.includes(dom.getAttribute('data-callout')) ? { variant: dom.getAttribute('data-callout') } : false),
      }],
      toDOM: (node) => ['div', { class: 'callout', 'data-callout': CALLOUTS.includes(node.attrs.variant) ? node.attrs.variant : 'read' }, 0],
    },
    bullet_list: {
      content: 'list_item+', group: 'block',
      parseDOM: [{ tag: 'ul' }],
      toDOM: () => ['ul', 0],
    },
    ordered_list: {
      attrs: { order: { default: 1 } },
      content: 'list_item+', group: 'block',
      parseDOM: [{ tag: 'ol', getAttrs: (dom) => ({ order: dom.hasAttribute('start') ? Number(dom.getAttribute('start')) || 1 : 1 }) }],
      toDOM: (node) => (node.attrs.order === 1 ? ['ol', 0] : ['ol', { start: Number(node.attrs.order) || 1 }, 0]),
    },
    list_item: {
      content: 'paragraph block*', defining: true,
      parseDOM: [{ tag: 'li' }],
      toDOM: () => ['li', 0],
    },
    horizontal_rule: {
      group: 'block',
      parseDOM: [{ tag: 'hr' }],
      toDOM: () => ['hr'],
    },
    image: {
      attrs: { src: {}, alt: { default: '' }, docImage: { default: null } },
      group: 'block', draggable: true,
      parseDOM: [{
        tag: 'img[src]',
        // Images hébergées par Maze seulement : pas de pixel espion ni de contenu externe.
        getAttrs: (dom) => (MEDIA_SRC.test(dom.getAttribute('src') || '') ? {
          src: dom.getAttribute('src'), alt: dom.getAttribute('alt') || '',
          docImage: dom.getAttribute('data-doc-image') || null,
        } : false),
      }],
      toDOM: (node) => ['img', {
        src: safeSrc(node.attrs.src), alt: String(node.attrs.alt || ''),
        ...(node.attrs.docImage && /^\d+$/.test(String(node.attrs.docImage)) ? { 'data-doc-image': String(node.attrs.docImage) } : {}),
      }],
    },
    mention: {
      attrs: { kind: {}, id: {}, label: { default: '' } },
      group: 'inline', inline: true, atom: true, selectable: true,
      parseDOM: [{
        tag: 'a[data-mention]', priority: 60,
        getAttrs: (dom) => {
          const m = (dom.getAttribute('data-mention') || '').match(MENTION);
          return m ? { kind: m[1], id: Number(m[2]), label: dom.textContent } : false;
        },
      }],
      toDOM: (node) => {
        const ok = MENTION.test(`${node.attrs.kind}:${node.attrs.id}`);
        return ['a', { class: 'mention', href: '#mention', 'data-mention': ok ? `${node.attrs.kind}:${node.attrs.id}` : '' },
          String(node.attrs.label || '')];
      },
    },
    // Tableaux (rencontres, butin, PNJ d'une scène…) : cellules de blocs, ligne d'en-tête possible.
    ...tableNodes({ tableGroup: 'block', cellContent: 'block+', cellAttributes: {} }),
    hard_break: {
      group: 'inline', inline: true, selectable: false,
      parseDOM: [{ tag: 'br' }],
      toDOM: () => ['br'],
    },
    text: { group: 'inline' },
  },
  marks: {
    link: {
      attrs: { href: {} }, inclusive: false,
      parseDOM: [{ tag: 'a[href]', getAttrs: (dom) => (safeHref(dom.getAttribute('href')) ? { href: dom.getAttribute('href') } : false) }],
      toDOM: (mark) => {
        const href = safeHref(mark.attrs.href);
        if (!href) return ['a', 0];
        return href.startsWith('#') ? ['a', { href }, 0] : ['a', { href, target: '_blank', rel: 'noopener noreferrer' }, 0];
      },
    },
    strong: {
      parseDOM: [
        { tag: 'strong' },
        { tag: 'b', getAttrs: (dom) => dom.style.fontWeight !== 'normal' && null },
        { style: 'font-weight', getAttrs: (v) => /^(bold(er)?|[5-9]\d{2,})$/.test(v) && null },
      ],
      toDOM: () => ['strong', 0],
    },
    em: {
      parseDOM: [{ tag: 'i' }, { tag: 'em' }, { style: 'font-style=italic' }],
      toDOM: () => ['em', 0],
    },
    underline: {
      parseDOM: [{ tag: 'u' }, { style: 'text-decoration=underline' }],
      toDOM: () => ['u', 0],
    },
    strike: {
      parseDOM: [{ tag: 's' }, { tag: 'del' }, { tag: 'strike' }, { style: 'text-decoration=line-through' }],
      toDOM: () => ['s', 0],
    },
    code: {
      parseDOM: [{ tag: 'code' }],
      toDOM: () => ['code', 0],
    },
    // Ancre d'un fil de commentaires. Plusieurs fils peuvent se chevaucher (excludes vide) ;
    // le surlignage est ajouté par l'éditeur selon l'état des fils (ouverts, résolus, supprimés).
    comment: {
      attrs: { id: {} }, inclusive: false, excludes: '',
      parseDOM: [{
        tag: 'span[data-comment]',
        getAttrs: (dom) => (/^\d+$/.test(dom.getAttribute('data-comment') || '') ? { id: Number(dom.getAttribute('data-comment')) } : false),
      }],
      toDOM: (mark) => ['span', { class: 'comment-anchor', 'data-comment': String(Number(mark.attrs.id) || 0) }, 0],
    },
  },
});

const parser = PMDOMParser.fromSchema(schema);
const serializer = DOMSerializer.fromSchema(schema);

/** HTML (ancienne fiche) vers document ProseMirror. */
export function htmlToDoc(html) {
  const box = document.createElement('div');
  box.innerHTML = sanitizeDoc(html || '');
  return parser.parse(box);
}

/** Document ProseMirror vers HTML enregistré côté serveur (rétroliens, sauvegardes lisibles). */
export function docToHtml(doc) {
  const box = document.createElement('div');
  box.appendChild(serializer.serializeFragment(doc.content));
  return box.innerHTML.replace(/<p><\/p>/g, '');
}

/** Affiche un contenu enregistré (aperçu d'une version) tel que l'éditeur le rendrait. */
export function renderDocInto(element, html) {
  element.replaceChildren(serializer.serializeFragment(htmlToDoc(html).content));
}

/**
 * Contenu réutilisable comme modèle : les mentions deviennent du texte (elles désignent des fiches
 * d'une campagne), images et ancres de commentaires sont retirées.
 */
export function templateHtml(doc) {
  const clean = (node) => {
    if (node.isText) return node.mark(node.marks.filter((m) => m.type !== schema.marks.comment));
    if (node.type === schema.nodes.mention) return node.attrs.label ? schema.text(String(node.attrs.label)) : null;
    if (node.type === schema.nodes.image) return null;
    const children = [];
    node.forEach((child) => {
      const out = clean(child);
      if (out) children.push(out);
    });
    return node.type.createAndFill(node.attrs, children, node.marks);
  };
  return docToHtml(clean(doc) || schema.topNodeType.createAndFill());
}
