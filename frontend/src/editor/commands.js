import { chainCommands, exitCode, setBlockType, splitBlockAs, toggleMark, wrapIn } from 'prosemirror-commands';
import { InputRule, inputRules, textblockTypeInputRule, wrappingInputRule } from 'prosemirror-inputrules';
import { liftListItem, sinkListItem, splitListItem, wrapInList } from 'prosemirror-schema-list';
import { NodeSelection, TextSelection } from 'prosemirror-state';
import { liftTarget } from 'prosemirror-transform';
import { addRowAfter, goToNextCell, isInTable } from 'prosemirror-tables';
import { redoCommand, undoCommand } from 'y-prosemirror';
import { schema } from './schema.js';

const { nodes, marks } = schema;

/** Ancêtre le plus proche de la sélection dont le type est dans `types`. */
export function findWrapper(state, types) {
  const { $from } = state.selection;
  for (let d = $from.depth; d > 0; d--) {
    const node = $from.node(d);
    if (types.includes(node.type)) return { node, depth: d, pos: $from.before(d) };
  }
  return null;
}

function markActive(state, type) {
  const { from, $from, to, empty } = state.selection;
  if (empty) return Boolean(type.isInSet(state.storedMarks || $from.marks()));
  return state.doc.rangeHasMark(from, to, type);
}

/** État de la barre d'outils pour la sélection courante. */
export function activeState(state) {
  const { $from } = state.selection;
  const parent = $from.parent;
  const wrapper = findWrapper(state, [nodes.blockquote, nodes.callout]);
  const list = findWrapper(state, [nodes.bullet_list, nodes.ordered_list]);
  return {
    bold: markActive(state, marks.strong),
    italic: markActive(state, marks.em),
    underline: markActive(state, marks.underline),
    strike: markActive(state, marks.strike),
    link: markActive(state, marks.link),
    block: parent.type === nodes.heading ? `h${parent.attrs.level}` : 'p',
    align: 'align' in parent.attrs ? (parent.attrs.align || 'left') : null,
    ul: list?.node.type === nodes.bullet_list,
    ol: list?.node.type === nodes.ordered_list,
    quote: wrapper?.node.type === nodes.blockquote,
    read: wrapper?.node.type === nodes.callout && wrapper.node.attrs.variant === 'read',
    secret: wrapper?.node.type === nodes.callout && wrapper.node.attrs.variant === 'secret',
    table: isInTable(state),
  };
}

// Le changement de type garde l'alignement du bloc courant.
export const setBlock = (key) => (state, dispatch) => {
  const align = state.selection.$from.parent.attrs.align || null;
  return key === 'p'
    ? setBlockType(nodes.paragraph, { align })(state, dispatch)
    : setBlockType(nodes.heading, { level: Number(key[1]), align })(state, dispatch);
};

/**
 * Alignement des paragraphes et titres de la sélection : 'left' (par défaut), 'center', 'right', 'justify'.
 * Redonner l'alignement déjà appliqué partout revient à gauche.
 */
export const setAlign = (value) => (state, dispatch) => {
  const { from, to } = state.selection;
  const blocks = [];
  state.doc.nodesBetween(from, to, (node, pos) => {
    if (node.type === nodes.paragraph || node.type === nodes.heading) blocks.push({ node, pos });
    return !node.isTextblock;
  });
  if (!blocks.length) return false;
  const wanted = value === 'left' ? null : value;
  const next = wanted && blocks.every((b) => b.node.attrs.align === wanted) ? null : wanted;
  if (dispatch) {
    const tr = state.tr;
    for (const { node, pos } of blocks) tr.setNodeMarkup(pos, null, { ...node.attrs, align: next });
    dispatch(tr.scrollIntoView());
  }
  return true;
};

// Entrée en fin de paragraphe aligné : le paragraphe suivant garde l'alignement.
// (Un paragraphe vide dans une citation, un encadré ou une liste en sort, comme d'habitude.)
const splitKeepAlign = splitBlockAs((node, atEnd) => (
  atEnd && node.type === nodes.paragraph && node.attrs.align
    ? { type: nodes.paragraph, attrs: { align: node.attrs.align } } : null));
const keepAlignOnEnter = (state, dispatch) => {
  const { $from } = state.selection;
  const { parent } = $from;
  if (!parent.attrs.align || (!parent.content.size && $from.depth > 1)) return false;
  return splitKeepAlign(state, dispatch);
};

export const toggle = {
  bold: toggleMark(marks.strong),
  italic: toggleMark(marks.em),
  underline: toggleMark(marks.underline),
  strike: toggleMark(marks.strike),
};

/** Liste à puces ou numérotée ; retire la liste si elle est déjà de ce type. */
export const toggleList = (type) => (state, dispatch) => {
  const list = findWrapper(state, [nodes.bullet_list, nodes.ordered_list]);
  if (list && list.node.type === type) return liftListItem(nodes.list_item)(state, dispatch);
  if (list) {
    if (dispatch) dispatch(state.tr.setNodeMarkup(list.pos, type));
    return true;
  }
  return wrapInList(type)(state, dispatch);
};

/** Citation, texte à lire, secret : encadre, change le type d'encadré, ou le retire. */
export const toggleWrapper = (key) => (state, dispatch) => {
  const type = key === 'quote' ? nodes.blockquote : nodes.callout;
  const attrs = key === 'quote' ? null : { variant: key };
  const current = findWrapper(state, [nodes.blockquote, nodes.callout]);
  if (current) {
    const same = current.node.type === type && (key === 'quote' || current.node.attrs.variant === key);
    if (!dispatch) return true;
    if (!same) {
      dispatch(state.tr.setNodeMarkup(current.pos, type, attrs));
      return true;
    }
    // Retire l'encadré autour de tout son contenu.
    const $start = state.doc.resolve(current.pos + 1);
    const $end = state.doc.resolve(current.pos + current.node.nodeSize - 1);
    const range = $start.blockRange($end);
    const target = range && liftTarget(range);
    if (target != null) dispatch(state.tr.lift(range, target).scrollIntoView());
    return true;
  }
  return wrapIn(type, attrs)(state, dispatch);
};

export const insertRule = (state, dispatch) => {
  if (dispatch) {
    const tr = state.tr.replaceSelectionWith(nodes.horizontal_rule.create());
    // Position juste après le séparateur (sélection sur le séparateur, ou dans le bloc suivant).
    const sel = tr.selection;
    const after = sel instanceof NodeSelection ? sel.to : sel.$from.before();
    const next = tr.doc.resolve(after).nodeAfter;
    if (next?.isTextblock) {
      tr.setSelection(TextSelection.create(tr.doc, after + 1));
    } else {
      // Rien derrière : un paragraphe, pour pouvoir continuer à écrire.
      tr.insert(after, nodes.paragraph.create());
      tr.setSelection(TextSelection.create(tr.doc, after + 1));
    }
    dispatch(tr.scrollIntoView());
  }
  return true;
};

/** Tableau (3 colonnes, ligne d'en-tête + 2 lignes) : remplace un paragraphe vide, sinon inséré après le bloc. */
export const insertTable = (state, dispatch) => {
  if (isInTable(state)) return false;
  if (dispatch) {
    const cell = (type) => type.createAndFill();
    const row = (type) => nodes.table_row.create(null, [cell(type), cell(type), cell(type)]);
    const table = nodes.table.create(null, [row(nodes.table_header), row(nodes.table_cell), row(nodes.table_cell)]);
    const { $from } = state.selection;
    // Bloc de premier niveau qui contient la sélection (ou bloc sélectionné : image, séparateur).
    const block = $from.depth > 0 ? $from.before(1) : $from.pos;
    const top = state.doc.nodeAt(block);
    if (!top) {
      const end = state.doc.content.size;
      const tr = state.tr.insert(end, [table, nodes.paragraph.create()]);
      tr.setSelection(TextSelection.near(tr.doc.resolve(end + 4)));
      dispatch(tr.scrollIntoView());
      return true;
    }
    let tr = state.tr;
    let at;
    if (top.type === nodes.paragraph && top.content.size === 0) {
      tr = tr.replaceWith(block, block + top.nodeSize, table);
      at = block;
    } else {
      at = block + top.nodeSize;
      tr = tr.insert(at, table);
    }
    // Un paragraphe après le tableau en fin de fiche, pour pouvoir continuer à écrire.
    const end = at + table.nodeSize;
    if (end >= tr.doc.content.size) tr = tr.insert(end, nodes.paragraph.create());
    tr.setSelection(TextSelection.near(tr.doc.resolve(at + 4)));
    dispatch(tr.scrollIntoView());
  }
  return true;
};

/** Tab dans un tableau : cellule suivante ; depuis la dernière cellule, ajoute une ligne. */
export const nextCell = (state, dispatch, view) => {
  if (!isInTable(state)) return false;
  if (goToNextCell(1)(state, dispatch)) return true;
  if (!dispatch || !view) return true;
  addRowAfter(state, dispatch);
  goToNextCell(1)(view.state, view.dispatch);
  return true;
};

export function insertImage(view, image, pos) {
  const node = nodes.image.create({ src: image.url, alt: '', docImage: String(image.id) });
  const tr = pos === undefined ? view.state.tr.replaceSelectionWith(node) : view.state.tr.insert(pos, node);
  view.dispatch(tr.scrollIntoView());
}

/** Étendue du lien sous le curseur (positions absolues), ou null. */
function linkRangeAt($pos) {
  const offset = $pos.parentOffset;
  let pos = 0;
  let run = null;
  let found = null;
  $pos.parent.forEach((child, childOffset) => {
    pos = childOffset;
    const mark = marks.link.isInSet(child.marks);
    if (mark && run && run.mark.eq(mark)) run.end = pos + child.nodeSize;
    else run = mark ? { mark, start: pos, end: pos + child.nodeSize } : null;
    if (run && run.start <= offset && offset <= run.end) found = { ...run };
  });
  return found && { from: $pos.start() + found.start, to: $pos.start() + found.end };
}

/** Lien : sur la sélection, ou sur le lien sous le curseur ; adresse vide = retirer. */
export function applyLink(view, href) {
  const { state } = view;
  let { from, to, empty } = state.selection;
  if (empty) {
    const range = linkRangeAt(state.selection.$from);
    if (range) {
      ({ from, to } = range);
      empty = false;
    }
  }
  let tr = state.tr;
  if (!href) {
    if (!empty) tr = tr.removeMark(from, to, marks.link);
  } else if (empty) {
    tr = tr.insertText(href, from, to).addMark(from, from + href.length, marks.link.create({ href }));
  } else {
    tr = tr.removeMark(from, to, marks.link).addMark(from, to, marks.link.create({ href }));
  }
  // Curseur après le lien : la suite de la frappe ne remplace pas le texte lié.
  tr.setSelection(TextSelection.create(tr.doc, tr.mapping.map(to)));
  view.dispatch(tr.scrollIntoView());
  view.focus();
}

export function currentLinkHref(state) {
  const range = linkRangeAt(state.selection.$from);
  if (!range) return '';
  let href = '';
  state.doc.nodesBetween(range.from, range.to, (node) => {
    const mark = marks.link.isInSet(node.marks);
    if (mark) href = mark.attrs.href;
  });
  return href;
}
// --- Raccourcis clavier et Markdown -------------------------------------------------------

/** « --- » puis Entrée : séparateur. */
const ruleOnEnter = (state, dispatch) => {
  const { $from, empty } = state.selection;
  if (!empty || $from.parent.type !== nodes.paragraph || $from.parent.textContent !== '---') return false;
  if (dispatch) {
    const start = $from.before();
    const tr = state.tr.replaceWith(start, $from.after(), [nodes.horizontal_rule.create(), nodes.paragraph.create()]);
    tr.setSelection(TextSelection.create(tr.doc, start + 2));
    dispatch(tr.scrollIntoView());
  }
  return true;
};

export function editorKeymap({ onLink, onSave }) {
  const hardBreak = chainCommands(exitCode, (state, dispatch) => {
    if (dispatch) dispatch(state.tr.replaceSelectionWith(nodes.hard_break.create()).scrollIntoView());
    return true;
  });
  return {
    'Mod-z': undoCommand,
    'Mod-y': redoCommand,
    'Mod-Shift-z': redoCommand,
    'Mod-b': toggle.bold,
    'Mod-i': toggle.italic,
    'Mod-u': toggle.underline,
    'Mod-Shift-x': toggle.strike,
    'Mod-k': () => { onLink(); return true; },
    'Mod-s': () => { onSave?.(); return true; },
    'Shift-Enter': hardBreak,
    Enter: chainCommands(ruleOnEnter, splitListItem(nodes.list_item), keepAlignOnEnter),
    Tab: chainCommands(nextCell, sinkListItem(nodes.list_item)),
    'Shift-Tab': chainCommands(goToNextCell(-1), liftListItem(nodes.list_item)),
    'Mod-[': liftListItem(nodes.list_item),
    'Mod-]': sinkListItem(nodes.list_item),
    Backspace: (state, dispatch, view) => {
      // Retour arrière au début d'un titre : redevient un paragraphe.
      const { $from, empty } = state.selection;
      if (empty && $from.parentOffset === 0 && $from.parent.type === nodes.heading) {
        return setBlockType(nodes.paragraph)(state, dispatch);
      }
      void view;
      return false;
    },
  };
}

export const markdownRules = inputRules({
  rules: [
    textblockTypeInputRule(/^(#{1,3})\s$/, nodes.heading, (m) => ({ level: m[1].length })),
    wrappingInputRule(/^\s*([-*•])\s$/, nodes.bullet_list),
    wrappingInputRule(/^(\d+)[.)]\s$/, nodes.ordered_list, (m) => ({ order: Number(m[1]) }),
      (m, node) => node.childCount + node.attrs.order === Number(m[1])),
    wrappingInputRule(/^\s*>\s$/, nodes.blockquote),
    // **gras** et _italique_ à la volée.
    new InputRule(/\*\*([^*\s][^*]*[^*\s]|[^*\s])\*\*$/, (state, match, start, end) => {
      const text = match[1];
      return state.tr.replaceWith(start, end, schema.text(text, [marks.strong.create()])).removeStoredMark(marks.strong);
    }),
  ],
});
