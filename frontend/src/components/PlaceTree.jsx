import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { placeKind } from '../constants.js';
import {
  GROUP_MIN, OTHER_KINDS, childrenMap, groupParent, groupsOf, isGroupKey, matchPlaces, pathIds, splitMatch, subtreeIds,
} from '../places.js';
import { storage } from '../util.js';
import { useSheet } from '../viewport.js';
import MoveDialog, { flattenTree } from './MoveDialog.jsx';
import { PlaceIcon, useKinds } from './PlaceIcon.jsx';
import { useTreeDrag } from './treeDrag.js';
import { Icon } from './ui.jsx';

/*
 * Arbre de l'index des lieux.
 * - Replié : à l'arrivée, le premier niveau seul ; à chaque changement de lieu, son chemin et ses sous-lieux.
 *   Un seul frère ouvert à la fois (accordéon).
 * - Longues listes (GROUP_MIN sous-lieux et plus) regroupées par type, sous des intertitres repliables.
 * - Filtre (texte, type) : lieux trouvés et leurs ancêtres, tout déplié.
 * - Clavier : ↑ ↓ Début Fin, → déplier ou entrer, ← replier ou remonter, Entrée ouvrir, / filtrer.
 */

const GROUPING_KEY = 'maze:places:group';
const isTyping = (el) => Boolean(el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)));
const rowKey = (raw) => (raw.startsWith('g:') ? raw : Number(raw));

/** Arborescence et règles d'ouverture, recalculées quand les lieux, les types ou le regroupement changent. */
function makeTree(places, kinds, grouped) {
  const kidsMap = childrenMap(places);
  const byId = new Map(places.map((p) => [p.id, p]));
  const kidsOf = (id) => kidsMap.get(id ?? null) || [];
  const cache = new Map();
  const groupsFor = (parentId) => {
    if (!grouped) return null;
    const k = parentId ?? null;
    if (!cache.has(k)) cache.set(k, groupsOf(k, kidsOf(k), kinds));
    return cache.get(k);
  };
  const groupOf = (p) => groupsFor(p.parent_id ?? null)?.find((g) => g.items.includes(p))?.key ?? null;
  const parentOfKey = (key) => (isGroupKey(key) ? groupParent(key) : byId.get(key)?.parent_id ?? null);

  /** Branches ouvertes au repos : chemin du lieu affiché (avec ses groupes) et le lieu lui-même. */
  const focusOpen = (currentId) => {
    const out = new Set();
    if (currentId && byId.has(currentId)) {
      for (const id of pathIds(byId, currentId)) {
        out.add(id);
        const g = groupOf(byId.get(id));
        if (g) out.add(g);
      }
    } else {
      const roots = kidsOf(null);
      if (roots.length === 1) out.add(roots[0].id);
    }
    return out;
  };

  /** Test « clé située sous `key` (elle comprise) » : lieux de son sous-arbre et groupes de ces lieux. */
  const under = (key) => {
    const roots = isGroupKey(key)
      ? (groupsFor(groupParent(key))?.find((g) => g.key === key)?.items || []).map((p) => p.id) : [key];
    const ids = new Set();
    for (const r of roots) for (const id of subtreeIds(places, r)) ids.add(id);
    return (k) => k === key || (isGroupKey(k) ? ids.has(groupParent(k)) : ids.has(k));
  };

  return { byId, kidsOf, groupsFor, groupOf, parentOfKey, focusOpen, under };
}

export default function PlaceTree({ data, campaignName, currentId, onOpen, onCreate, onMove, onMoveTo, onDelete, onPin }) {
  const kinds = useKinds();
  const places = data?.places;
  const [grouped, setGroupedState] = useState(() => storage.get(GROUPING_KEY) !== '0');
  const tree = useMemo(() => (places ? makeTree(places, kinds, grouped) : null), [places, kinds, grouped]);
  const [open, setOpen] = useState(() => new Set());
  const [query, setQuery] = useState('');
  const [kindFilter, setKindFilter] = useState('');
  const [tabKey, setTabKey] = useState(null);   // ligne qui reçoit la tabulation (navigation au clavier)
  const [menu, setMenu] = useState(null);
  const [moving, setMoving] = useState(null);   // lieu à déplacer (« Déplacer vers… »)
  const asideRef = useRef(null);
  const listRef = useRef(null);
  const filterRef = useRef(null);
  const focused = useRef(undefined);            // lieu pour lequel l'arbre a été replié
  const pathSig = useRef('');                   // chemin ouvert pour ce lieu (rouvert seulement s'il change)
  const reveal = useRef(null);                  // lieu déplacé à montrer après le rechargement
  const scrolled = useRef('');                  // dernier lieu ramené dans la partie visible
  const filtering = query.trim() !== '' || kindFilter !== '';

  const setGrouped = (v) => { setGroupedState(v); storage.set(GROUPING_KEY, v ? '1' : '0'); };

  // Changement de lieu (arbre, fil d'Ariane, recherche, lien) : arbre replié sur son chemin.
  // Arbre rechargé ou regroupement changé : ouvertures gardées (clés disparues retirées) ; le chemin du lieu
  // affiché n'est rouvert que s'il a changé (lieu déplacé, regroupement), pas après un simple rechargement.
  useEffect(() => {
    if (!tree) return;
    const rest = tree.focusOpen(currentId);
    const sig = [...rest].sort().join(',');
    if (focused.current !== currentId) {
      focused.current = currentId;
      pathSig.current = sig;
      setOpen(rest);
      return;
    }
    const moved = reveal.current && tree.byId.has(reveal.current) ? reveal.current : null;
    reveal.current = null;
    const changed = currentId && sig !== pathSig.current;
    pathSig.current = sig;
    setOpen((s) => {
      const valid = (k) => (isGroupKey(k)
        ? Boolean(tree.groupsFor(groupParent(k))?.some((g) => g.key === k)) : tree.byId.has(k));
      const n = new Set([...s].filter(valid));
      if (changed) for (const k of rest) n.add(k);
      if (moved) for (const k of tree.focusOpen(moved)) if (k !== moved) n.add(k);
      return n.size === s.size && [...n].every((k) => s.has(k)) ? s : n;
    });
  }, [tree, currentId]);

  // Lieu affiché ramené dans la partie visible de l'arbre (défilement de l'arbre seul, pas de la page), une fois par
  // lieu ou par passage au filtre : déplier une autre branche ne fait pas revenir l'arbre en arrière.
  useEffect(() => {
    const aside = asideRef.current;
    const item = aside?.querySelector('.doc-tree__item.is-active');
    const key = `${currentId}|${filtering}`;
    if (!item || scrolled.current === key) return;
    scrolled.current = key;
    const a = aside.getBoundingClientRect();
    const r = item.getBoundingClientRect();
    if (r.top < a.top + 8) aside.scrollTop -= a.top + 8 - r.top + 40;
    else if (r.bottom > a.bottom - 8) aside.scrollTop += r.bottom - a.bottom + 8 + 40;
  }, [currentId, open, filtering]);

  // « / » : filtrer (arbre visible, hors saisie).
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey || isTyping(document.activeElement)) return;
      if (!asideRef.current || asideRef.current.offsetParent === null) return;
      e.preventDefault();
      filterRef.current?.focus();
      filterRef.current?.select();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const expand = useCallback((key) => setOpen((s) => {
    // Accordéon : un seul lieu ouvert parmi les sous-lieux d'un même parent, un seul groupe ouvert par liste.
    const parent = tree.parentOfKey(key);
    const siblings = isGroupKey(key)
      ? (tree.groupsFor(parent) || []).map((g) => g.key)
      : tree.kidsOf(parent).map((p) => p.id);
    const n = new Set(s);
    for (const sib of siblings) {
      if (sib === key || !n.has(sib)) continue;
      const inside = tree.under(sib);
      for (const k of [...n]) if (inside(k)) n.delete(k);
    }
    n.add(key);
    return n;
  }), [tree]);
  const collapse = useCallback((key) => setOpen((s) => {
    const inside = tree.under(key);
    return new Set([...s].filter((k) => !inside(k)));
  }), [tree]);
  const toggle = (key) => (open.has(key) ? collapse(key) : expand(key));
  const clearFilter = () => { setQuery(''); setKindFilter(''); };

  // Après le rechargement : chemin et groupe du lieu déplacé ouverts, sauf si le serveur a refusé le déplacement.
  const moveRevealed = async (id, parentId, index) => {
    reveal.current = id;
    if (!await onMoveTo(id, parentId, index) && reveal.current === id) reveal.current = null;
  };
  const dnd = useTreeDrag((dragged, target, where) => {
    if (target.key === 'root') { onMoveTo(dragged.id, null, 100000); return; }
    const t = tree.byId.get(target.id);
    if (where === 'inside') {
      moveRevealed(dragged.id, t.id, 100000);
      return;
    }
    const parent = t.parent_id ?? null;
    const siblings = tree.kidsOf(parent).filter((p) => p.id !== dragged.id);
    moveRevealed(dragged.id, parent, siblings.findIndex((p) => p.id === t.id) + (where === 'after' ? 1 : 0));
  });

  // Filtre : lieux trouvés et leurs ancêtres.
  const matches = useMemo(() => (places && filtering ? matchPlaces(places, query, kindFilter) : []),
    [places, filtering, query, kindFilter]);
  const shown = useMemo(() => {
    if (!filtering || !tree) return null;
    const ids = new Set();
    for (const p of matches) for (const id of pathIds(tree.byId, p.id)) ids.add(id);
    return ids;
  }, [filtering, matches, tree]);
  const usedKinds = useMemo(() => {
    const count = new Map();
    for (const p of places || []) count.set(p.kind, (count.get(p.kind) || 0) + 1);
    return kinds.filter((k) => count.has(k.key)).map((k) => ({ ...k, count: count.get(k.key) }));
  }, [places, kinds]);

  // Type filtré disparu (type supprimé, plus aucun lieu de ce type) : filtre de type retiré.
  useEffect(() => {
    if (kindFilter && places && !usedKinds.some((k) => k.key === kindFilter)) setKindFilter('');
  }, [kindFilter, usedKinds, places]);

  if (!tree) return <aside className="doc-tree" aria-busy="true" />;

  // --- Lignes affichées --------------------------------------------------------------------------
  const matchSet = new Set(matches.map((p) => p.id));
  const pathSet = currentId ? new Set(pathIds(tree.byId, currentId)) : new Set();
  const rows = [];
  const placeNode = (p, depth) => {
    const all = tree.kidsOf(p.id);
    const kids = shown ? all.filter((k) => shown.has(k.id)) : all;
    const isOpen = kids.length > 0 && (Boolean(shown) || open.has(p.id));
    rows.push(p.id);
    const groups = isOpen && !shown ? tree.groupsFor(p.id) : null;
    return {
      type: 'place', key: p.id, place: p, depth, hasKids: kids.length > 0, count: all.length, isOpen,
      onPath: !isOpen && p.id !== currentId && pathSet.has(p.id), context: Boolean(shown) && !matchSet.has(p.id),
      children: !isOpen ? [] : groups ? groups.map((g) => groupNode(g, depth + 1)) : kids.map((k) => placeNode(k, depth + 1)),
    };
  };
  const groupNode = (g, depth) => {
    const isOpen = open.has(g.key);
    rows.push(g.key);
    return {
      type: 'group', key: g.key, group: g, depth, hasKids: true, isOpen,
      onPath: !isOpen && g.items.some((p) => pathSet.has(p.id)),
      children: isOpen ? g.items.map((p) => placeNode(p, depth + 1)) : [],
    };
  };
  const roots = tree.kidsOf(null).filter((p) => !shown || shown.has(p.id));
  const rootGroups = shown ? null : tree.groupsFor(null);
  const top = rootGroups ? rootGroups.map((g) => groupNode(g, 0)) : roots.map((p) => placeNode(p, 0));
  const tabbable = rows.includes(tabKey) ? tabKey : rows.includes(currentId) ? currentId : rows[0];

  const inside = (id, ancestorId) => pathIds(tree.byId, id).includes(ancestorId);
  const resting = tree.focusOpen(currentId);
  const canFold = !filtering && [...open].some((k) => !resting.has(k));

  // Menu ⋯ d'un lieu ; `row` reprend le focus à la fermeture (ouverture au clavier).
  const openMenu = (p, anchor, row = anchor) => {
    const siblings = tree.kidsOf(p.parent_id ?? null);
    const index = siblings.findIndex((s) => s.id === p.id);
    setMenu({ item: p, rect: anchor.getBoundingClientRect(), row, first: index === 0, last: index === siblings.length - 1 });
  };

  // --- Clavier -----------------------------------------------------------------------------------
  const onTreeKey = (e) => {
    const els = [...listRef.current.querySelectorAll('[data-row]')];
    const i = els.indexOf(document.activeElement);
    if (i < 0) return;
    const el = els[i];
    const key = rowKey(el.dataset.row);
    const focusAt = (j) => els[Math.max(0, Math.min(els.length - 1, j))]?.focus();
    const handled = () => { e.preventDefault(); e.stopPropagation(); };
    if (e.key === 'ArrowDown') { handled(); focusAt(i + 1); return; }
    if (e.key === 'ArrowUp') { handled(); if (i === 0) filterRef.current?.focus(); else focusAt(i - 1); return; }
    if (e.key === 'Home') { handled(); focusAt(0); return; }
    if (e.key === 'End') { handled(); focusAt(els.length - 1); return; }
    if (e.key === 'ArrowRight') {
      handled();
      if (el.dataset.kids !== '1') return;
      if (el.dataset.open === '1') focusAt(i + 1);
      else expand(key);
      return;
    }
    if (e.key === 'ArrowLeft') {
      handled();
      if (el.dataset.open === '1' && !filtering) { collapse(key); return; }
      el.closest('li')?.parentElement?.closest('li')?.querySelector('[data-row]')?.focus();
      return;
    }
    if (e.key === 'Escape' && filtering) { handled(); clearFilter(); filterRef.current?.focus(); return; }
    // Menu ⋯ au clavier : touche Menu ou Maj+F10.
    if ((e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) && !isGroupKey(key)) {
      handled();
      openMenu(tree.byId.get(key), el);
      return;
    }
    // Saisie d'une lettre : filtre (complété s'il est déjà en cours).
    if (e.key.length === 1 && e.key !== ' ' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      handled();
      setQuery((q) => (q.trim() ? q + e.key : e.key));
      filterRef.current?.focus();
    }
  };

  const onFilterKey = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      listRef.current?.querySelector('[data-row]')?.focus();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const first = rows.find((k) => !isGroupKey(k) && (!filtering || matchSet.has(k)));
      if (first) onOpen(first);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      if (filtering) { e.stopPropagation(); clearFilter(); } // sur téléphone, le tiroir reste ouvert
      else e.currentTarget.blur();
    }
  };

  // --- Rendu -------------------------------------------------------------------------------------
  const rowProps = (node) => ({
    'data-row': String(node.key),
    'data-kids': node.hasKids ? '1' : '0',
    'data-open': node.isOpen ? '1' : '0',
    tabIndex: node.key === tabbable ? 0 : -1,
    onFocus: () => setTabKey(node.key),
  });

  const label = (p) => {
    const parts = filtering ? splitMatch(p.name, query) : null;
    return parts ? <>{parts[0]}<mark>{parts[1]}</mark>{parts[2]}</> : p.name;
  };

  const renderNode = (node) => {
    if (node.type === 'group') return renderGroup(node);
    const p = node.place;
    return (
      <li key={p.id}>
        <div className={`doc-tree__item${p.id === currentId ? ' is-active' : ''}${node.onPath ? ' is-on-path' : ''}`
          + `${node.context ? ' is-context' : ''}${dnd.className(p.id)}`}
          style={{ '--depth': node.depth }} title={filtering ? undefined : 'Glisser pour déplacer'}
          {...dnd.props({ key: p.id, id: p.id, label: p.name },
            { inside: true, enabled: !filtering, accepts: (d) => !inside(p.id, d.id) })}>
          {node.hasKids && !filtering ? (
            <button type="button" className="doc-tree__caret" tabIndex={-1} aria-hidden="true"
              onClick={() => toggle(p.id)}>
              <Icon name={node.isOpen ? 'chevron' : 'right'} size={13} />
            </button>
          ) : <span className="doc-tree__caret">{node.hasKids && <Icon name="chevron" size={13} />}</span>}
          <button type="button" className="doc-tree__link" onClick={() => onOpen(p.id)} title={p.name}
            aria-current={p.id === currentId ? 'page' : undefined}
            aria-expanded={node.hasKids ? node.isOpen : undefined} {...rowProps(node)}>
            <span className="place-tree__icon"><PlaceIcon kind={p.kind} size={15} /></span>
            <span className="doc-tree__label">{label(p)}</span>
            {p.pinned && <span className="doc-tree__pin" title="Épinglé"><Icon name="star" size={11} /></span>}
            {node.hasKids && !node.isOpen && (
              <span className="doc-tree__count" title={`${node.count} sous-lieu${node.count > 1 ? 'x' : ''}`}>{node.count}</span>
            )}
          </button>
          <button type="button" className="doc-tree__more" tabIndex={-1} aria-label={`Actions pour ${p.name}`}
            onClick={(e) => openMenu(p, e.currentTarget, e.currentTarget.parentElement.querySelector('[data-row]'))}>
            <Icon name="more" size={16} />
          </button>
        </div>
        {node.isOpen && <ul style={{ '--guide': node.depth }}>{node.children.map(renderNode)}</ul>}
      </li>
    );
  };

  const renderGroup = (node) => {
    const g = node.group;
    const name = g.kind === OTHER_KINDS ? 'Autres types' : placeKind(g.kind, kinds).label;
    return (
      <li key={g.key}>
        <div className={`doc-tree__item doc-tree__group${node.onPath ? ' is-on-path' : ''}`} style={{ '--depth': node.depth }}>
          <button type="button" className="doc-tree__caret" tabIndex={-1} aria-hidden="true" onClick={() => toggle(g.key)}>
            <Icon name={node.isOpen ? 'chevron' : 'right'} size={13} />
          </button>
          <button type="button" className="doc-tree__link" onClick={() => toggle(g.key)} aria-expanded={node.isOpen}
            title={`${name} : ${g.items.length} lieux`} {...rowProps(node)}>
            {g.kind !== OTHER_KINDS && <span className="place-tree__icon"><PlaceIcon kind={g.kind} size={14} /></span>}
            <span className="doc-tree__label">{name}</span>
            <span className="doc-tree__count">{g.items.length}</span>
          </button>
        </div>
        {node.isOpen && <ul style={{ '--guide': node.depth }}>{node.children.map(renderNode)}</ul>}
      </li>
    );
  };

  return (
    <aside className="doc-tree place-tree" aria-label="Lieux de la campagne" ref={asideRef}>
      <button type="button" className={`doc-tree__root${currentId ? '' : ' is-active'}${dnd.className('root')}`}
        onClick={() => onOpen(null)} {...dnd.props({ key: 'root', fixed: true }, { inside: 'only', enabled: !filtering })}>
        <Icon name="pin" size={17} />
        <span>
          <strong>Lieux</strong>
          <span className="doc-tree__sub">{campaignName}</span>
        </span>
      </button>
      <div className="doc-tree__section">
        <span>Tous les lieux{places.length > 0 && <span className="doc-tree__total"> · {places.length}</span>}</span>
        <span className="doc-tree__section-actions">
          <button type="button" className={`icon-btn icon-btn--small${grouped ? ' is-active' : ''}`} aria-pressed={grouped}
            title={`Regrouper par type les listes de ${GROUP_MIN} lieux et plus`} aria-label="Regrouper par type"
            onClick={() => setGrouped(!grouped)}>
            <Icon name="district" size={15} />
          </button>
          <button type="button" className="icon-btn icon-btn--small" disabled={!canFold}
            title="Tout replier (garder le chemin du lieu affiché)" aria-label="Tout replier"
            onClick={() => setOpen(resting)}>
            <Icon name="fold" size={15} />
          </button>
          <button type="button" className="icon-btn icon-btn--small" title="Nouveau lieu" aria-label="Nouveau lieu"
            onClick={() => onCreate(null)}>
            <Icon name="plus" size={15} />
          </button>
        </span>
      </div>
      {places.length > 0 && (
        <div className="tree-filter" role="search">
          <label className="tree-filter__box">
            <Icon name="search" size={14} />
            <input ref={filterRef} type="search" value={query} placeholder="Filtrer les lieux" aria-label="Filtrer les lieux"
              aria-keyshortcuts="/" enterKeyHint="go" autoComplete="off" spellCheck={false}
              onChange={(e) => setQuery(e.target.value)} onKeyDown={onFilterKey} />
            {query && (
              <button type="button" className="tree-filter__clear" aria-label="Effacer le filtre"
                onClick={() => { setQuery(''); filterRef.current?.focus(); }}>
                <Icon name="close" size={13} />
              </button>
            )}
          </label>
          <select className={`tree-filter__kind${kindFilter ? ' is-set' : ''}`} value={kindFilter} aria-label="Type de lieu"
            onChange={(e) => setKindFilter(e.target.value)}>
            <option value="">Tous types</option>
            {usedKinds.map((k) => <option key={k.key} value={k.key}>{k.label} ({k.count})</option>)}
          </select>
        </div>
      )}
      {filtering && (
        <p className="tree-filter__summary" role="status">
          {matches.length === 0 ? 'Aucun lieu ne correspond.'
            : `${matches.length} lieu${matches.length > 1 ? 'x' : ''} trouvé${matches.length > 1 ? 's' : ''}`}
          <button type="button" className="link-btn" onClick={clearFilter}>Effacer</button>
        </p>
      )}
      <div ref={listRef} onKeyDown={onTreeKey}>
        <ul className="doc-tree__list" aria-label="Arborescence des lieux">
          {top.map(renderNode)}
        </ul>
      </div>
      {places.length === 0 && (
        <button type="button" className="doc-tree__empty" onClick={() => onCreate(null)}>
          <Icon name="plus" size={14} /> Créer le premier lieu
        </button>
      )}
      {menu && (
        <TreeMenu menu={menu} onClose={(refocus) => { const row = menu.row; setMenu(null); if (refocus) row?.focus(); }}
          onAction={(action) => {
          setMenu(null);
          menu.row?.focus();
          if (action === 'child') onCreate(menu.item.id);
          if (action === 'pin') onPin(menu.item, !menu.item.pinned);
          if (action === 'up') onMove(menu.item, -1);
          if (action === 'down') onMove(menu.item, 1);
          if (action === 'move') setMoving(menu.item);
          if (action === 'delete') onDelete(menu.item);
        }} />
      )}
      {moving && (
        <MoveDialog title={`Déplacer « ${moving.name} »`} intro="Choisissez le lieu qui le contiendra."
          options={[
            { id: null, label: 'Aucun (lieu de premier niveau)', icon: 'pin', depth: 0, current: (moving.parent_id ?? null) === null },
            ...flattenTree((id) => tree.kidsOf(id), null, moving.id).map(({ item, depth }) => ({
              id: item.id, label: item.name, icon: 'pin', depth: depth + 1, current: moving.parent_id === item.id,
            })),
          ]}
          onClose={() => setMoving(null)}
          onPick={(parentId) => {
            setMoving(null);
            moveRevealed(moving.id, parentId, 100000);
          }} />
      )}
    </aside>
  );
}

function TreeMenu({ menu, onClose, onAction }) {
  const ref = useRef(null);
  useSheet();
  useEffect(() => {
    const onDown = (e) => { if (!ref.current?.contains(e.target)) onClose(false); };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [onClose]);
  useEffect(() => { ref.current?.querySelector('[role="menuitem"]:not(:disabled)')?.focus(); }, []);
  const onKey = (e) => {
    const items = [...ref.current.querySelectorAll('[role="menuitem"]:not(:disabled)')];
    const i = items.indexOf(document.activeElement);
    if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); onClose(true); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
    else if (e.key === 'Home') { e.preventDefault(); items[0]?.focus(); }
    else if (e.key === 'End') { e.preventDefault(); items[items.length - 1]?.focus(); }
  };
  return createPortal(
    <div className="popover popover--menu" ref={ref} role="menu" onKeyDown={onKey}
      style={{ position: 'fixed', top: menu.rect.bottom + 4, left: Math.min(menu.rect.left, window.innerWidth - 240), right: 'auto' }}>
      <div className="popover__group popover__group--actions">
        <button type="button" role="menuitem" onClick={() => onAction('child')}><Icon name="plus" size={15} /> Ajouter un sous-lieu</button>
        <button type="button" role="menuitem" onClick={() => onAction('pin')}>
          <Icon name="star" size={15} /> {menu.item.pinned ? 'Retirer des épinglés' : 'Épingler sur l’accueil'}
        </button>
        <button type="button" role="menuitem" disabled={menu.first} onClick={() => onAction('up')}><Icon name="up" size={15} /> Monter</button>
        <button type="button" role="menuitem" disabled={menu.last} onClick={() => onAction('down')}><Icon name="down" size={15} /> Descendre</button>
        <button type="button" role="menuitem" onClick={() => onAction('move')}><Icon name="swap" size={15} /> Déplacer vers…</button>
        <button type="button" role="menuitem" className="danger-text" onClick={() => onAction('delete')}><Icon name="trash" size={15} /> Supprimer</button>
      </div>
    </div>,
    document.body,
  );
}
