import { useEffect, useImperativeHandle, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { baseKeymap } from 'prosemirror-commands';
import { dropCursor } from 'prosemirror-dropcursor';
import { gapCursor } from 'prosemirror-gapcursor';
import { keymap } from 'prosemirror-keymap';
import { EditorState, Plugin, PluginKey, TextSelection } from 'prosemirror-state';
import {
  addColumnAfter, addColumnBefore, addRowAfter, addRowBefore, columnResizing, deleteColumn, deleteRow, deleteTable,
  mergeCells, splitCell, tableEditing, toggleHeaderRow,
} from 'prosemirror-tables';
import { Decoration, DecorationSet, EditorView } from 'prosemirror-view';
import {
  absolutePositionToRelativePosition, redoCommand, relativePositionToAbsolutePosition, undoCommand,
  yCursorPlugin, ySyncPlugin, ySyncPluginKey, yUndoPlugin,
} from 'y-prosemirror';
import { normalize } from '../util.js';
import { Field, Icon, Modal } from '../components/ui.jsx';
import {
  activeState, applyLink, currentLinkHref, editorKeymap, findWrapper, insertImage, insertRule, insertTable, markdownRules,
  setAlign, setBlock, toggle, toggleList, toggleWrapper,
} from './commands.js';
import { docToHtml, schema, templateHtml } from './schema.js';
import { slugify } from './sanitize.js';
import { TOUCH, isPhone } from '../viewport.js';

/*
 * Éditeur des fiches rédigées : ProseMirror relié au document Yjs partagé (LiveDocument).
 * Chaque frappe modifie le document Yjs, diffusé aux autres MJ connectés à la fiche ;
 * leurs curseurs apparaissent avec leur nom.
 */

const BLOCKS = [
  { key: 'p', label: 'Paragraphe' },
  { key: 'h1', label: 'Titre 1' },
  { key: 'h2', label: 'Titre 2' },
  { key: 'h3', label: 'Titre 3' },
];

// Commandes du menu « / » (début de ligne).
const SLASH_ITEMS = [
  { key: 'h1', label: 'Titre 1', hint: '#', icon: 'heading' },
  { key: 'h2', label: 'Titre 2', hint: '##', icon: 'heading' },
  { key: 'h3', label: 'Titre 3', hint: '###', icon: 'heading' },
  { key: 'ul', label: 'Liste à puces', hint: '-', icon: 'list' },
  { key: 'ol', label: 'Liste numérotée', hint: '1.', icon: 'olist' },
  { key: 'quote', label: 'Citation', hint: '>', icon: 'quote' },
  { key: 'read', label: 'Accroche (texte à lire aux joueurs)', icon: 'scroll' },
  { key: 'secret', label: 'Secret (réservé au MJ)', icon: 'lock' },
  { key: 'hr', label: 'Séparateur', hint: '---', icon: 'minus' },
  { key: 'table', label: 'Tableau', icon: 'table' },
  { key: 'image', label: 'Image', icon: 'image' },
];

/**
 * tableEditing de prosemirror-tables, sans la réparation automatique des tableaux (fixTables) sur les
 * modifications venues des autres MJ : réparées ici et là-bas en même temps, elles se dupliqueraient
 * à la fusion Yjs. Chacun ne répare que ce qu'il modifie lui-même.
 */
function localTableEditing() {
  const plugin = tableEditing();
  const repair = plugin.spec.appendTransaction;
  plugin.spec.appendTransaction = (trs, oldState, state) => (
    trs.some((tr) => tr.getMeta(ySyncPluginKey)?.isChangeOrigin) ? null : repair(trs, oldState, state));
  return plugin;
}

// Actions sur le tableau sous le curseur (barre flottante au-dessus du tableau).
const TABLE_ACTIONS = [
  [
    { label: 'Ligne au-dessus', short: '↑ Ligne', command: addRowBefore },
    { label: 'Ligne en dessous', short: 'Ligne ↓', command: addRowAfter },
    { label: 'Supprimer la ligne', short: 'Suppr.', command: deleteRow, danger: true },
  ],
  [
    { label: 'Colonne à gauche', short: '← Colonne', command: addColumnBefore },
    { label: 'Colonne à droite', short: 'Colonne →', command: addColumnAfter },
    { label: 'Supprimer la colonne', short: 'Suppr.', command: deleteColumn, danger: true },
  ],
  [
    { label: "Ligne d'en-tête", short: 'En-tête', command: toggleHeaderRow },
    { label: 'Fusionner les cellules', short: 'Fusionner', command: mergeCells },
    { label: 'Scinder la cellule', short: 'Scinder', command: splitCell },
  ],
];

const SAFE_COLOR = /^#[0-9a-f]{6}$/i;
const colorOf = (user) => (SAFE_COLOR.test(user?.color || '') ? user.color : '#e8a33d');

/** Curseur d'un autre MJ : trait coloré et étiquette avec son nom. */
function cursorBuilder(user) {
  const color = colorOf(user);
  const caret = document.createElement('span');
  caret.className = 'remote-caret';
  caret.style.borderColor = color;
  const label = document.createElement('span');
  label.className = 'remote-caret__label';
  label.style.backgroundColor = color;
  label.textContent = String(user?.name || 'MJ').slice(0, 40);
  caret.append('⁠', label, '⁠');
  return caret;
}

const selectionBuilder = (user) => ({ style: `background-color: ${colorOf(user)}38`, class: 'remote-selection' });

/** Suggestion en cours sous le curseur : « @nom » ou « /commande » en début de ligne. */
function detectSuggest(state) {
  const { $from, empty } = state.selection;
  if (!empty || !$from.parent.isTextblock) return null;
  const before = $from.parent.textBetween(0, $from.parentOffset, undefined, '￼');
  const mention = before.match(/(?:^|[\s( ])@([^\s@￼]{0,30}(?:[  ][^\s@￼]{0,30}){0,3})$/u);
  if (mention) {
    return { type: 'mention', query: mention[1].replace(/ /g, ' '), from: $from.pos - mention[1].length - 1, to: $from.pos };
  }
  const slash = before.match(/^\/([\p{L}\d ]{0,20})$/u);
  if (slash && $from.parent.textContent === before) {
    return { type: 'slash', query: slash[1], from: $from.pos - before.length, to: $from.pos };
  }
  return null;
}

// --- Titres : identifiants pour le sommaire --------------------------------------------------

const headingsKey = new PluginKey('maze-headings');

function computeHeadings(doc) {
  const used = new Set();
  const list = [];
  const decorations = [];
  doc.descendants((node, pos) => {
    if (node.type !== schema.nodes.heading) return true;
    const text = node.textContent.trim();
    if (text) {
      const base = slugify(text);
      let id = base;
      let n = 2;
      while (used.has(id)) id = `${base}-${n++}`;
      used.add(id);
      list.push({ id, level: node.attrs.level, text });
      decorations.push(Decoration.node(pos, pos + node.nodeSize, { id }));
    }
    return false;
  });
  return { list, decorations: DecorationSet.create(doc, decorations) };
}

const headingsPlugin = new Plugin({
  key: headingsKey,
  state: {
    init: (_, state) => computeHeadings(state.doc),
    apply: (tr, value) => (tr.docChanged ? computeHeadings(tr.doc) : value),
  },
  props: { decorations: (state) => headingsKey.getState(state).decorations },
});

// --- Positions stables -------------------------------------------------------------------------
// Une modification venue d'un autre MJ (ou une annulation) remplace tout le document ProseMirror :
// les positions ordinaires ne s'y retrouvent plus. Les positions relatives Yjs, elles, restent
// attachées au texte.

function toRelative(state, pos) {
  const ys = ySyncPluginKey.getState(state);
  return ys?.binding ? absolutePositionToRelativePosition(pos, ys.type, ys.binding.mapping) : null;
}

function fromRelative(state, rel) {
  const ys = ySyncPluginKey.getState(state);
  if (!rel || !ys?.binding) return null;
  const pos = relativePositionToAbsolutePosition(ys.doc, ys.type, rel, ys.binding.mapping);
  return pos === null || pos > state.doc.content.size ? null : pos;
}

// --- Commentaires : ancres (marques), surlignage des fils ouverts, brouillon en cours ----------

const commentsKey = new PluginKey('maze-comments');

function computeComments(doc, openIds, activeId, pending) {
  const anchors = {};
  const decorations = [];
  doc.descendants((node, pos) => {
    if (!node.isText) return true;
    for (const mark of node.marks) {
      if (mark.type !== schema.marks.comment) continue;
      const id = Number(mark.attrs.id);
      const a = anchors[id] || (anchors[id] = { pos, text: '' });
      if (a.text.length < 160) a.text += node.text;
      if (openIds.has(id)) {
        decorations.push(Decoration.inline(pos, pos + node.nodeSize, {
          class: `comment-hl${id === activeId ? ' is-active' : ''}`, 'data-thread': String(id),
        }));
      }
    }
    return false;
  });
  if (pending) decorations.push(Decoration.inline(pending.from, pending.to, { class: 'comment-hl is-draft' }));
  return { anchors, decorations: DecorationSet.create(doc, decorations) };
}

const commentsPlugin = new Plugin({
  key: commentsKey,
  state: {
    init: (_, state) => ({ openIds: new Set(), activeId: null, pending: null, ...computeComments(state.doc, new Set(), null, null) }),
    apply: (tr, value, _old, state) => {
      const meta = tr.getMeta(commentsKey);
      if (!meta && !tr.docChanged) return value;
      let { pending } = value;
      if (pending && tr.docChanged) {
        // Remplacement complet (modification distante, annulation) : on repart des positions Yjs.
        const remote = tr.getMeta(ySyncPluginKey)?.isChangeOrigin;
        const from = remote ? fromRelative(state, pending.relFrom) : tr.mapping.map(pending.from, 1);
        const to = remote ? fromRelative(state, pending.relTo) : tr.mapping.map(pending.to, -1);
        pending = from !== null && to !== null && from < to ? { ...pending, from, to } : null;
      }
      const next = { ...value, pending, ...(meta || {}) };
      return { ...next, ...computeComments(tr.doc, next.openIds, next.activeId, next.pending) };
    },
  },
  props: { decorations: (state) => commentsKey.getState(state).decorations },
});

// --- Passage trouvé par la recherche globale : surligné quelques secondes ----------------------

const flashKey = new PluginKey('maze-flash');
const flashPlugin = new Plugin({
  key: flashKey,
  state: {
    init: () => DecorationSet.empty,
    apply: (tr, set) => {
      const meta = tr.getMeta(flashKey);
      if (meta !== undefined) {
        return meta ? DecorationSet.create(tr.doc, [Decoration.inline(meta.from, meta.to, { class: 'search-flash' })])
          : DecorationSet.empty;
      }
      return set.map(tr.mapping, tr.doc);
    },
  },
  props: { decorations: (state) => flashKey.getState(state) },
});

const fold = (s) => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();

/** Premier passage du document contenant un mot de la recherche (accents et casse ignorés). */
function findText(doc, query) {
  const words = fold(query).match(/[\p{L}\p{N}]+/gu) || [];
  if (!words.length) return null;
  let found = null;
  doc.descendants((node, pos) => {
    if (found) return false;
    if (!node.isTextblock) return true;
    // Texte du bloc, replié caractère par caractère, avec la position d'origine de chaque caractère.
    let folded = '';
    const at = [];
    node.forEach((child, offset) => {
      const text = child.isText ? child.text : child.type.name === 'mention' ? String(child.attrs.label || '') : '\ufffc';
      for (let i = 0; i < text.length; i++) {
        const f = fold(text[i]);
        for (let k = 0; k < f.length; k++) at.push(child.isText ? pos + 1 + offset + i : pos + 1 + offset);
        folded += f;
      }
    });
    for (const word of words) {
      const re = new RegExp(`(^|[^\\p{L}\\p{N}])${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`, 'u');
      const m = re.exec(folded);
      if (m) {
        const start = m.index + m[1].length;
        let end = start + word.length;
        while (end < folded.length && /[\p{L}\p{N}]/u.test(folded[end])) end += 1;  // jusqu'à la fin du mot
        found = { from: at[start], to: (at[end - 1] ?? at[start]) + 1 };
        break;
      }
    }
    return false;
  });
  return found;
}

// --- Mentions : nom et couleur à jour, même après un renommage ---------------------------------

class MentionView {
  constructor(node, registry) {
    this.node = node;
    this.registry = registry;
    this.dom = document.createElement('a');
    this.dom.className = 'mention';
    this.dom.href = '#mention';
    this.dom.contentEditable = 'false';
    registry.views.add(this);
    this.render();
  }

  render() {
    const { kind, id, label } = this.node.attrs;
    const key = `${kind}:${id}`;
    const ent = this.registry.byKey.get(key);
    this.dom.dataset.mention = key;
    this.dom.dataset.kind = kind;
    this.dom.textContent = ent?.name || label || '?';
    this.dom.classList.toggle('is-missing', this.registry.loaded && !ent);
    if (ent?.color) this.dom.style.setProperty('--mention', ent.color);
    else this.dom.style.removeProperty('--mention');
  }

  update(node) {
    if (node.type !== this.node.type) return false;
    this.node = node;
    this.render();
    return true;
  }

  ignoreMutation() { return true; }

  destroy() { this.registry.views.delete(this); }
}

// --- Composant -----------------------------------------------------------------------------------

export default function Editor({
  ref, live, readOnly, entities, entitiesLoaded, placeholder, simple = false,
  onHeadings, onMentionClick, onCreateEntity, onUploadImage, onPickImage,
  onStartComment, onCommentClick, onCommentAnchors,
}) {
  const mountRef = useRef(null);
  const viewRef = useRef(null);
  const fileRef = useRef(null);
  const [active, setActive] = useState({});
  const [suggest, setSuggest] = useState(null);
  const [suggestIndex, setSuggestIndex] = useState(0);
  const [linkDialog, setLinkDialog] = useState(null);
  const suggestRef = useRef({ current: null, items: [], index: 0, dismissed: null });
  const callbacks = useRef({});
  callbacks.current = { onHeadings, onMentionClick, onCreateEntity, onUploadImage, onPickImage, onStartComment, onCommentClick, onCommentAnchors };
  const [commentFab, setCommentFab] = useState(null); // position du bouton « Commenter » près de la sélection
  const [tableBar, setTableBar] = useState(null);
  const tableBarRef = useRef(null);     // position de la barre du tableau sous le curseur
  const readOnlyRef = useRef(readOnly);
  readOnlyRef.current = readOnly;
  const placeholderRef = useRef(placeholder);
  placeholderRef.current = placeholder;
  const registry = useRef({ views: new Set(), byKey: new Map(), loaded: false }).current;

  // Fiches citables, pour le nom affiché des mentions.
  useEffect(() => {
    registry.byKey = new Map(entities.map((e) => [`${e.kind}:${e.id}`, e]));
    registry.loaded = entitiesLoaded;
    registry.views.forEach((v) => v.render());
  }, [entities, entitiesLoaded, registry]);

  const uploadAndInsert = async (files) => {
    for (const file of [...files].filter((f) => f.type.startsWith('image/'))) {
      const img = await callbacks.current.onUploadImage?.(file);
      if (img && viewRef.current) insertImage(viewRef.current, img);
    }
  };

  /** Bouton Image : choix dans Maze ou envoi depuis l'ordinateur (si la page le propose), sinon fichier. */
  const chooseImage = async () => {
    if (!callbacks.current.onPickImage) { fileRef.current?.click(); return; }
    // La sélection de l'éditeur est conservée (et suit les modifications des autres MJ) pendant le choix.
    const img = await callbacks.current.onPickImage();
    if (img && viewRef.current) {
      insertImage(viewRef.current, img);
      viewRef.current.focus();
    }
  };

  const openLinkDialog = () => {
    const view = viewRef.current;
    if (!view) return;
    setLinkDialog({ url: currentLinkHref(view.state) || 'https://' });
  };

  // --- Création de la vue (une fois par fiche) ---------------------------------------------
  useEffect(() => {
    let lastHeadings = '';
    let lastAnchors = '';
    const notify = (view) => {
      setActive(activeState(view.state));
      const found = view.hasFocus() || suggestRef.current.current ? detectSuggest(view.state) : null;
      const dismissed = suggestRef.current.dismissed;
      const s = found && !(dismissed && dismissed.from === found.from && dismissed.type === found.type) ? found : null;
      if (!found) suggestRef.current.dismissed = null;
      if (s) {
        const coords = view.coordsAtPos(s.from);
        s.rect = { left: coords.left, bottom: coords.bottom };
      }
      const prev = suggestRef.current.current;
      if (!s || !prev || prev.type !== s.type || prev.from !== s.from) setSuggestIndex(0);
      suggestRef.current.current = s;
      setSuggest(s);
      const { list } = headingsKey.getState(view.state);
      const json = JSON.stringify(list);
      if (json !== lastHeadings) {
        lastHeadings = json;
        callbacks.current.onHeadings?.(list);
      }
      const { anchors } = commentsKey.getState(view.state);
      const anchorsJson = JSON.stringify(anchors);
      if (anchorsJson !== lastAnchors) {
        lastAnchors = anchorsJson;
        callbacks.current.onCommentAnchors?.(anchors);
      }
      // Bouton « Commenter » dans la marge, à hauteur de la sélection.
      const sel = view.state.selection;
      if (!readOnlyRef.current && !sel.empty && sel instanceof TextSelection && view.hasFocus()) {
        const box = mountRef.current?.parentElement.getBoundingClientRect();
        const at = view.coordsAtPos(sel.from);
        setCommentFab(box ? { top: at.top - box.top } : null);
      } else {
        setCommentFab(null);
      }
      // Barre du tableau, au-dessus du tableau qui contient le curseur.
      const table = !readOnlyRef.current && findWrapper(view.state, [schema.nodes.table]);
      const tableDom = table && view.nodeDOM(table.pos);
      const box = mountRef.current?.parentElement.getBoundingClientRect();
      if (tableDom && box) {
        const rect = (tableDom.closest?.('.tableWrapper') || tableDom).getBoundingClientRect();
        // Au-dessus du tableau ; si la barre d'outils (collante) l'aurait cachée — tableau en haut de fiche
        // ou remonté sous la barre — elle se place juste sous la barre d'outils, sur le haut du tableau.
        const toolbar = mountRef.current.parentElement.querySelector('.rich__toolbar')?.getBoundingClientRect();
        const height = tableBarRef.current?.offsetHeight || 36;
        const minTop = toolbar ? toolbar.bottom - box.top + 6 : 0;
        const above = rect.top - box.top - height - 6;
        const left = Math.max(0, rect.left - box.left);
        if (above >= minTop) setTableBar({ top: above, left });
        else if (rect.bottom - box.top - height > minTop) setTableBar({ top: minTop, left, pinned: true });
        else setTableBar(null);
      } else {
        setTableBar(null);
      }
    };

    const uiPlugin = new Plugin({
      view: (view) => {
        setTimeout(() => notify(view), 0);
        // Défilement : la barre du tableau suit (elle se cale sous la barre d'outils collante).
        let frame = 0;
        const onScroll = () => {
          if (frame) return;
          frame = requestAnimationFrame(() => { frame = 0; if (!view.isDestroyed) notify(view); });
        };
        window.addEventListener('scroll', onScroll, true);
        return {
          update: (v) => notify(v),
          destroy: () => { window.removeEventListener('scroll', onScroll, true); cancelAnimationFrame(frame); },
        };
      },
      props: {
        attributes: (state) => {
          const first = state.doc.firstChild;
          const empty = state.doc.childCount === 1 && first.isTextblock && first.content.size === 0;
          return {
            class: `rich__content doc-content${empty ? ' is-empty' : ''}`,
            'data-placeholder': placeholderRef.current || '',
            role: 'textbox', 'aria-multiline': 'true', 'aria-label': 'Contenu de la fiche', spellcheck: 'true',
          };
        },
        handlePaste: (view, e) => {
          const files = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith('image/'));
          if (!files.length) return false;
          uploadAndInsert(files);
          return true;
        },
        handleDrop: (view, e, slice, moved) => {
          const files = [...(e.dataTransfer?.files || [])].filter((f) => f.type.startsWith('image/'));
          if (moved || !files.length) return false;
          const at = view.posAtCoords({ left: e.clientX, top: e.clientY });
          if (at) view.dispatch(view.state.tr.setSelection(TextSelection.near(view.state.doc.resolve(at.pos))));
          uploadAndInsert(files);
          return true;
        },
        handleDOMEvents: {
          click: (view, e) => {
            const thread = e.target.closest?.('[data-thread]');
            if (thread && view.state.selection.empty) callbacks.current.onCommentClick?.(Number(thread.dataset.thread));
            const mention = e.target.closest?.('a.mention');
            if (mention) {
              e.preventDefault();
              const [kind, id] = (mention.dataset.mention || '').split(':');
              callbacks.current.onMentionClick?.({ kind, id: Number(id) }, mention.getBoundingClientRect());
              return true;
            }
            const link = e.target.closest?.('a[href]');
            if (link) {
              e.preventDefault();
              // En édition, un clic place le curseur ; Ctrl+clic (ou mode lecture) ouvre le lien.
              if ((readOnlyRef.current || e.ctrlKey || e.metaKey) && /^(https?:|mailto:)/i.test(link.getAttribute('href'))) {
                window.open(link.href, '_blank', 'noopener,noreferrer');
              }
              return true;
            }
            return false;
          },
        },
      },
    });

    // Navigation dans les suggestions : avant les raccourcis clavier (Entrée, Tab…).
    const suggestKeys = new Plugin({
      props: {
        handleKeyDown: (view, e) => {
          const s = suggestRef.current;
          if (s.current && s.items.length) {
            if (e.key === 'ArrowDown') { setSuggestIndex((i) => (i + 1) % s.items.length); return true; }
            if (e.key === 'ArrowUp') { setSuggestIndex((i) => (i - 1 + s.items.length) % s.items.length); return true; }
            if (e.key === 'Enter' || e.key === 'Tab') { pickRef.current(s.items[s.index]); return true; }
          }
          if (s.current && e.key === 'Escape') {
            s.dismissed = { from: s.current.from, type: s.current.type };
            notify(view);
            e.stopPropagation();
            return true;
          }
          return false;
        },
      },
    });

    const state = EditorState.create({
      schema,
      plugins: [
        suggestKeys,
        ySyncPlugin(live.fragment),
        yCursorPlugin(live.awareness, { cursorBuilder, selectionBuilder }),
        yUndoPlugin(),
        markdownRules,
        keymap({ 'Mod-Alt-m': () => { startCommentRef.current(); return true; } }),
        keymap(editorKeymap({ onLink: openLinkDialog })),
        keymap(baseKeymap),
        dropCursor({ color: '#d9a441', width: 2 }),
        gapCursor(),
        columnResizing({ cellMinWidth: 60 }),
        localTableEditing(),
        headingsPlugin,
        commentsPlugin,
        flashPlugin,
        uiPlugin,
      ],
    });
    // Téléphone : la barre de mise en forme couvre le bas de l'écran (au-dessus du clavier) ;
    // le curseur reste au-dessus d'elle.
    const margin = isPhone() ? { top: 12, bottom: 72, left: 5, right: 5 } : 5;
    const view = new EditorView(mountRef.current, {
      state,
      scrollMargin: margin,
      scrollThreshold: margin,
      editable: () => !readOnlyRef.current,
      nodeViews: { mention: (node) => new MentionView(node, registry) },
    });
    viewRef.current = view;
    return () => {
      viewRef.current = null;
      // y-prosemirror diffère certaines transactions (curseurs) et ne les annule que si la liaison
      // est marquée détruite, ce que sa propre destruction ne fait pas.
      const binding = ySyncPluginKey.getState(view.state)?.binding;
      if (binding) binding.isDestroyed = true;
      view.destroy();
    };
  }, [live]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    viewRef.current?.setProps({ editable: () => !readOnly });
    if (readOnly) setSuggest(null);
  }, [readOnly]);

  /** Commence un commentaire sur la sélection : l'extrait reste surligné pendant la rédaction. */
  const startComment = () => {
    const view = viewRef.current;
    if (!view) return;
    const { from, to, empty } = view.state.selection;
    if (empty) {
      callbacks.current.onStartComment?.(null);
      return;
    }
    const quote = view.state.doc.textBetween(from, to, ' ', ' ').replace(/\s+/g, ' ').trim().slice(0, 300);
    const { state } = view;
    view.dispatch(state.tr.setMeta(commentsKey, {
      pending: { from, to, relFrom: toRelative(state, from), relTo: toRelative(state, to) },
    }));
    setCommentFab(null);
    callbacks.current.onStartComment?.({ quote });
  };
  const startCommentRef = useRef(startComment);
  startCommentRef.current = startComment;

  useImperativeHandle(ref, () => ({
    /** Ancre le fil `id` sur l'extrait du brouillon. */
    applyComment(id) {
      const view = viewRef.current;
      const pending = view && commentsKey.getState(view.state).pending;
      if (!pending) return false;
      const tr = view.state.tr.addMark(pending.from, pending.to, schema.marks.comment.create({ id }));
      view.dispatch(tr.setMeta(commentsKey, { pending: null }));
      return true;
    },
    cancelComment() {
      const view = viewRef.current;
      if (view) view.dispatch(view.state.tr.setMeta(commentsKey, { pending: null }));
    },
    /** Retire les ancres d'un fil supprimé (le texte reste). */
    removeComment(id) {
      const view = viewRef.current;
      if (!view) return;
      const tr = view.state.tr;
      view.state.doc.descendants((node, pos) => {
        if (!node.isText) return true;
        node.marks.filter((m) => m.type === schema.marks.comment && Number(m.attrs.id) === id)
          .forEach((m) => tr.removeMark(pos, pos + node.nodeSize, m));
        return false;
      });
      if (tr.docChanged) view.dispatch(tr);
    },
    /** Fils ouverts (surlignés) et fil sélectionné. */
    setCommentState(openIds, activeId) {
      const view = viewRef.current;
      if (view) view.dispatch(view.state.tr.setMeta(commentsKey, { openIds, activeId }));
    },
    /** Recherche globale : fait défiler jusqu'au passage trouvé et le surligne. */
    revealText(query) {
      const view = viewRef.current;
      if (!view) return false;
      const range = findText(view.state.doc, query);
      if (!range) return false;
      view.dispatch(view.state.tr.setMeta(flashKey, range));
      const dom = view.domAtPos(range.from)?.node;
      (dom?.nodeType === 3 ? dom.parentElement : dom)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setTimeout(() => { if (viewRef.current === view) view.dispatch(view.state.tr.setMeta(flashKey, null)); }, 3500);
      return true;
    },
    revealComment(id) {
      const view = viewRef.current;
      const anchor = view && commentsKey.getState(view.state).anchors[id];
      if (!anchor) return;
      const dom = view.domAtPos(anchor.pos + 1)?.node;
      (dom?.nodeType === 3 ? dom.parentElement : dom)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    },
    scrollToHeading(id) {
      document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },
    focus() {
      viewRef.current?.focus();
    },
    /** HTML du contenu tel qu'affiché (versions nommées, restauration). */
    getHtml() {
      return viewRef.current ? docToHtml(viewRef.current.state.doc) : null;
    },
    /** Contenu réutilisable comme modèle (sans mentions, images ni commentaires). */
    getTemplateHtml() {
      return viewRef.current ? templateHtml(viewRef.current.state.doc) : null;
    },
  }));

  // --- Commandes -------------------------------------------------------------------------------
  const run = (command) => {
    const view = viewRef.current;
    if (!view) return;
    command(view.state, view.dispatch, view);
    view.focus();
  };

  const runKey = (key) => {
    if (BLOCKS.some((b) => b.key === key)) run(setBlock(key));
    else if (key === 'ul') run(toggleList(schema.nodes.bullet_list));
    else if (key === 'ol') run(toggleList(schema.nodes.ordered_list));
    else if (['quote', 'read', 'secret'].includes(key)) run(toggleWrapper(key));
    else if (key === 'hr') run(insertRule);
    else if (key === 'table') run(insertTable);
    else if (key === 'image') chooseImage();
  };

  // --- Suggestions (@mention et /commande) -------------------------------------------------------
  const suggestItems = (() => {
    if (!suggest) return [];
    const q = normalize(suggest.query);
    if (suggest.type === 'slash') return SLASH_ITEMS.filter((it) => !q || normalize(it.label).includes(q));
    const list = entities
      .filter((e) => !q || normalize(e.name).includes(q))
      .sort((a, b) => Number(!normalize(a.name).startsWith(q)) - Number(!normalize(b.name).startsWith(q))
        || a.name.localeCompare(b.name, 'fr'))
      .slice(0, 8)
      .map((e) => ({ ...e, key: `${e.kind}:${e.id}` }));
    const name = suggest.query.trim();
    const exact = entities.some((e) => normalize(e.name) === q);
    if (name.length >= 2 && !exact && callbacks.current.onCreateEntity) {
      list.push({ key: 'new-npc', create: 'npc', name, label: `Créer le PNJ « ${name} »` });
      list.push({ key: 'new-faction', create: 'faction', name, label: `Créer la faction « ${name} »` });
      list.push({ key: 'new-place', create: 'place', name, label: `Créer le lieu « ${name} »` });
    }
    return list;
  })();
  const index = Math.min(suggestIndex, Math.max(0, suggestItems.length - 1));
  suggestRef.current.items = suggestItems;
  suggestRef.current.index = index;

  const pickSuggestion = async (item) => {
    const view = viewRef.current;
    const current = suggestRef.current.current;
    if (!item || !view || !current) return;
    if (current.type === 'slash') {
      view.dispatch(view.state.tr.delete(current.from, current.to));
      runKey(item.key);
      return;
    }
    let target = item;
    let from = current.from;
    let to = current.to;
    if (item.create) {
      // Création de la fiche (requête) : le texte peut bouger entre-temps (frappe, autres MJ).
      const typed = view.state.doc.textBetween(current.from, current.to, undefined, '\ufffc');
      const rel = [toRelative(view.state, current.from), toRelative(view.state, current.to)];
      suggestRef.current.dismissed = { from: current.from, type: current.type };
      setSuggest(null);
      const created = await callbacks.current.onCreateEntity(item.create, item.name);
      if (!created || !viewRef.current) return;
      target = created;
      from = fromRelative(view.state, rel[0]);
      to = fromRelative(view.state, rel[1]);
      // « @recherche » intact : on le remplace ; sinon, mention insérée au curseur sans rien effacer.
      if (from === null || to === null || from > to
          || view.state.doc.textBetween(from, to, undefined, '\ufffc') !== typed) {
        ({ from, to } = view.state.selection);
      }
    }
    const { state } = view;
    const node = schema.nodes.mention.create({ kind: target.kind, id: target.id, label: target.name });
    const tr = state.tr.replaceWith(from, to, [node, schema.text(' ')]);
    tr.setSelection(TextSelection.create(tr.doc, from + 2));
    suggestRef.current.dismissed = null;
    view.dispatch(tr.scrollIntoView());
    view.focus();
  };
  const pickRef = useRef(pickSuggestion);
  pickRef.current = pickSuggestion;

  // --- Rendu -------------------------------------------------------------------------------------
  const tool = (key, title, content, onAct, isActive) => (
    <button key={key} type="button" className={`tool${isActive ? ' is-active' : ''}`} title={title} aria-label={title}
      aria-pressed={isActive === undefined ? undefined : Boolean(isActive)}
      onMouseDown={(e) => e.preventDefault()} onClick={onAct}>
      {content}
    </button>
  );

  return (
    <div className={`rich${readOnly ? ' rich--readonly' : ''}${simple ? ' rich--field' : ''}`}>
      {!readOnly && (
        <div className="rich__toolbar" role="toolbar" aria-label="Mise en forme">
          {/* Groupes insécables : à la ligne, un groupe passe entier (jamais un bouton isolé). */}
          <span className="tool-group">
            <select className="tool-select" value={active.block || 'p'} aria-label="Type de bloc"
              onChange={(e) => runKey(e.target.value)}>
              {BLOCKS.map((b) => <option key={b.key} value={b.key}>{b.label}</option>)}
            </select>
          </span>
          <span className="tool-group">
            {tool('b', 'Gras (Ctrl+B)', <strong>G</strong>, () => run(toggle.bold), active.bold)}
            {tool('i', 'Italique (Ctrl+I)', <em>I</em>, () => run(toggle.italic), active.italic)}
            {tool('u', 'Souligné (Ctrl+U)', <u>S</u>, () => run(toggle.underline), active.underline)}
            {tool('s', 'Barré (Ctrl+Maj+X)', <s>B</s>, () => run(toggle.strike), active.strike)}
          </span>
          <span className="tool-group">
            <AlignMenu value={active.align} onPick={(value) => run(setAlign(value))} />
            {tool('ul', 'Liste à puces', <Icon name="list" size={17} />, () => runKey('ul'), active.ul)}
            {tool('ol', 'Liste numérotée', <Icon name="olist" size={17} />, () => runKey('ol'), active.ol)}
            {tool('quote', 'Citation', <Icon name="quote" size={17} />, () => runKey('quote'), active.quote)}
          </span>
          <span className="tool-group">
            {tool('read', 'Encadré Accroche : texte à lire aux joueurs', <><Icon name="scroll" size={16} /><span className="tool__label">À lire</span></>, () => runKey('read'), active.read)}
            {tool('secret', 'Encadré Secret : réservé au MJ', <><Icon name="lock" size={15} /><span className="tool__label">Secret</span></>, () => runKey('secret'), active.secret)}
          </span>
          <span className="tool-group">
            {tool('hr', 'Séparateur', <Icon name="minus" size={17} />, () => runKey('hr'))}
            {tool('link', 'Lien (Ctrl+K)', <Icon name="link" size={17} />, openLinkDialog, active.link)}
            {!simple && tool('img', 'Image (depuis Maze ou l’ordinateur)', <Icon name="image" size={17} />, chooseImage)}
            {tool('table', 'Tableau', <Icon name="table" size={17} />, () => run(insertTable), active.table)}
          </span>
          <span className="tool-group">
            {tool('undo', 'Annuler (Ctrl+Z)', <Icon name="undo" size={17} />, () => run(undoCommand))}
            {tool('redo', 'Rétablir (Ctrl+Y)', <Icon name="redo" size={17} />, () => run(redoCommand))}
          </span>
          <input ref={fileRef} type="file" accept="image/*" hidden
            onChange={(e) => { uploadAndInsert(e.target.files); e.target.value = ''; }} />
        </div>
      )}
      <div ref={mountRef} className="rich__mount" />
      {tableBar && !readOnly && viewRef.current && (
        <div ref={tableBarRef} className={`table-bar${tableBar.pinned ? ' is-pinned' : ''}`} role="toolbar" aria-label="Tableau"
          style={{ top: tableBar.top, left: tableBar.left }}
          onMouseDown={(e) => e.preventDefault()}>
          {TABLE_ACTIONS.map((group, i) => (
            <span key={i} className="table-bar__group">
              {group.map((a) => (
                <button key={a.label} type="button" title={a.label} aria-label={a.label}
                  className={`table-bar__btn${a.danger ? ' is-danger' : ''}`}
                  disabled={!a.command(viewRef.current.state)} onClick={() => run(a.command)}>
                  {a.short || a.label}
                </button>
              ))}
            </span>
          ))}
          <button type="button" className="table-bar__btn is-danger" title="Supprimer le tableau" aria-label="Supprimer le tableau"
            onClick={() => run(deleteTable)}>
            <Icon name="trash" size={15} />
          </button>
        </div>
      )}
      {commentFab && !readOnly && onStartComment && (
        <button type="button" className="comment-fab" style={{ top: commentFab.top }}
          title="Commenter la sélection (Ctrl+Alt+M)" aria-label="Commenter la sélection"
          onMouseDown={(e) => e.preventDefault()} onClick={startComment}>
          <Icon name="comment" size={17} />
        </button>
      )}
      {suggest && suggestItems.length > 0 && !readOnly && createPortal(
        <ul className="suggest" role="listbox" style={{ left: suggest.rect.left, top: suggest.rect.bottom + 6 }}
          aria-label={suggest.type === 'mention' ? 'Fiches à citer' : 'Blocs à insérer'}>
          {suggestItems.map((it, i) => (
            <li key={it.key} role="option" aria-selected={i === index}>
              <button type="button" className={i === index ? 'is-active' : ''}
                onMouseDown={(e) => { e.preventDefault(); pickSuggestion(it); }}
                onMouseMove={() => { if (index !== i) setSuggestIndex(i); }}>
                {suggest.type === 'slash' ? (
                  <>
                    <span className="suggest__icon"><Icon name={it.icon} size={16} /></span>
                    <span className="suggest__name">{it.label}</span>
                    {it.hint && <kbd>{it.hint}</kbd>}
                  </>
                ) : it.create ? (
                  <>
                    <span className="suggest__icon"><Icon name="plus" size={16} /></span>
                    <span className="suggest__name">{it.label}</span>
                  </>
                ) : (
                  <>
                    {it.image
                      ? <img src={it.image} alt="" className={it.kind === 'faction' ? 'is-faction' : ''} />
                      : it.icon
                        ? <span className="suggest__icon" style={{ color: it.color }}><Icon name={it.icon} size={16} /></span>
                        : <span className="suggest__seal" style={{ background: it.color }} />}
                    <span className="suggest__name">{it.name}</span>
                    <span className="suggest__sub">{it.sub}</span>
                  </>
                )}
              </button>
            </li>
          ))}
        </ul>,
        document.body,
      )}
      {linkDialog && (
        <LinkDialog initial={linkDialog} onClose={() => { setLinkDialog(null); viewRef.current?.focus(); }}
          onApply={(url) => { setLinkDialog(null); if (viewRef.current) applyLink(viewRef.current, url); }} />
      )}
    </div>
  );
}

function LinkDialog({ initial, onClose, onApply }) {
  const [url, setUrl] = useState(initial.url);
  return (
    <Modal title="Lien" onClose={onClose} footer={(
      <>
        <button type="button" className="btn btn--ghost btn--danger-text modal__foot-left" onClick={() => onApply('')}>Retirer le lien</button>
        <button type="button" className="btn btn--ghost" onClick={onClose}>Annuler</button>
        <button type="submit" form="link-form" className="btn btn--primary">Appliquer</button>
      </>
    )}>
      <form id="link-form" onSubmit={(e) => { e.preventDefault(); onApply(/^https?:\/\/.+/.test(url) || url.startsWith('mailto:') ? url : ''); }}>
        <Field label="Adresse" htmlFor="link-url" hint={TOUCH ? "Les liens s'ouvrent dans un nouvel onglet, en mode Lecture." : "Les liens s'ouvrent dans un nouvel onglet. En édition : Ctrl+clic pour les suivre."}>
          <input id="link-url" value={url} onChange={(e) => setUrl(e.target.value)} />
        </Field>
      </form>
    </Modal>
  );
}

const ALIGN_CHOICES = [
  ['left', 'alignLeft', 'Aligner à gauche'], ['center', 'alignCenter', 'Centrer'],
  ['right', 'alignRight', 'Aligner à droite'], ['justify', 'alignJustify', 'Justifier'],
];

/** Alignement du paragraphe : un seul bouton (alignement courant) qui ouvre les quatre choix. */
function AlignMenu({ value, onPick }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);
  const current = ALIGN_CHOICES.find(([v]) => v === value) || ALIGN_CHOICES[0];
  return (
    <span className="align-menu" ref={ref}>
      <button type="button" className={`tool${value && value !== 'left' ? ' is-active' : ''}`}
        title={`Alignement : ${current[2].toLowerCase()}`} aria-label="Alignement du texte" aria-haspopup="menu" aria-expanded={open}
        disabled={value === null} onMouseDown={(e) => e.preventDefault()} onClick={() => setOpen((o) => !o)}>
        <Icon name={current[1]} size={17} />
      </button>
      {open && (
        <span className="align-menu__pop" role="menu" aria-label="Alignement du texte">
          {ALIGN_CHOICES.map(([v, icon, title]) => (
            <button key={v} type="button" role="menuitemradio" aria-checked={current[0] === v} title={title} aria-label={title}
              className={`tool${current[0] === v ? ' is-active' : ''}`}
              onMouseDown={(e) => e.preventDefault()} onClick={() => { setOpen(false); onPick(v); }}>
              <Icon name={icon} size={17} />
            </button>
          ))}
        </span>
      )}
    </span>
  );
}
