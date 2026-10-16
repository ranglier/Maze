import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { placeKind } from '../constants.js';
import { OTHER_KINDS, ancestorsOf, childrenMap, groupsOf, matchPlaces } from '../places.js';
import { t } from '../i18n/index.js';
import { useSheet } from '../viewport.js';
import { PlaceIcon, useKinds } from './PlaceIcon.jsx';
import { Icon } from './ui.jsx';

/*
 * Fil d'Ariane d'un lieu, à la manière d'un explorateur de fichiers : chaque séparateur › ouvre la liste des
 * lieux contenus dans l'élément qui le précède. On passe ainsi de Valdis à Corvel (séparateur après le
 * sous-secteur) ou d'une salle à une autre sans repasser par l'arbre.
 */
export default function PlaceCrumbs({ place, places, onOpen }) {
  const kinds = useKinds();
  const byId = useMemo(() => new Map(places.map((p) => [p.id, p])), [places]);
  const kids = useMemo(() => childrenMap(places), [places]);
  const [menu, setMenu] = useState(null); // { parentId, rect, trigger, activeId }
  const chain = [...ancestorsOf(byId, place), place];

  const toggle = (parentId, activeId) => (e) => {
    const trigger = e.currentTarget;
    setMenu((m) => (m && m.parentId === parentId ? null : { parentId, activeId, trigger, rect: trigger.getBoundingClientRect() }));
  };
  const sep = (parentId, activeId, name) => (
    <button type="button" className={`crumb-sep${menu && menu.parentId === parentId ? ' is-open' : ''}`}
      aria-haspopup="menu" aria-expanded={Boolean(menu && menu.parentId === parentId)}
      title={name ? t('Lieux dans {name}', { name }) : t('Lieux de premier niveau')}
      aria-label={name ? t('Lieux dans {name}', { name }) : t('Lieux de premier niveau')}
      onClick={toggle(parentId, activeId)}>
      <Icon name="right" size={12} />
    </button>
  );

  return (
    <nav className="doc__crumbs place-crumbs" aria-label={t("Fil d'Ariane")}>
      <button type="button" onClick={() => onOpen(null)}>{t('Lieux')}</button>
      {sep(null, chain[0].id, null)}
      {chain.map((p, i) => (
        <span key={p.id} className="place-crumbs__seg">
          {p.id === place.id
            ? <span aria-current="page">{p.name}</span>
            : <button type="button" onClick={() => onOpen(p.id)}>{p.name}</button>}
          {i < chain.length - 1 && sep(p.id, chain[i + 1].id, p.name)}
          {i === chain.length - 1 && (kids.get(p.id)?.length > 0) && sep(p.id, null, p.name)}
        </span>
      ))}
      {menu && (
        <CrumbMenu menu={menu} items={kids.get(menu.parentId) || []} kids={kids} kinds={kinds}
          title={menu.parentId ? byId.get(menu.parentId)?.name : t('Premier niveau')}
          onClose={(refocus) => { const trigger = menu.trigger; setMenu(null); if (refocus) trigger?.focus(); }}
          onPick={(id) => { setMenu(null); onOpen(id); }} />
      )}
    </nav>
  );
}

function CrumbMenu({ menu, items, kids, kinds, title, onClose, onPick }) {
  const ref = useRef(null);
  const [query, setQuery] = useState('');
  useSheet();
  const searchable = items.length > 10;
  const shown = query.trim() ? matchPlaces(items, query, '') : items;
  const groups = !query.trim() ? groupsOf(menu.parentId, items, kinds) : null;

  useEffect(() => {
    const onDown = (e) => { if (!ref.current?.contains(e.target) && !menu.trigger.contains(e.target)) onClose(false); };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [onClose, menu.trigger]);
  // Ouverture : le lieu du chemin (ou le premier) reçoit le focus, ou le champ de recherche des longues listes.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (searchable && !window.matchMedia('(pointer: coarse)').matches) el.querySelector('input')?.focus();
    else (el.querySelector('[aria-current="true"]') || el.querySelector('[role="menuitem"]'))?.focus();
    el.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [searchable]);

  const onKey = (e) => {
    const els = [...ref.current.querySelectorAll('[role="menuitem"]')];
    const i = els.indexOf(document.activeElement);
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose(true); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); els[Math.min(els.length - 1, i + 1)]?.focus(); return; }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (i <= 0 && searchable) ref.current.querySelector('input')?.focus();
      else els[Math.max(0, i - 1)]?.focus();
      return;
    }
    if (e.key === 'Enter' && e.target.tagName === 'INPUT' && shown.length) { e.preventDefault(); onPick(shown[0].id); }
  };

  const item = (p) => {
    const n = kids.get(p.id)?.length || 0;
    return (
      <button key={p.id} type="button" role="menuitem" aria-current={p.id === menu.activeId ? 'true' : undefined}
        onClick={() => onPick(p.id)}>
        <span className="place-tree__icon"><PlaceIcon kind={p.kind} size={15} /></span>
        <span className="crumb-menu__name">{p.name}</span>
        {n > 0 && <span className="doc-tree__count">{n}</span>}
      </button>
    );
  };

  const left = Math.max(8, Math.min(menu.rect.left - 12, window.innerWidth - 300));
  return createPortal(
    <div className="popover popover--menu crumb-menu" ref={ref} role="menu" aria-label={title} onKeyDown={onKey}
      style={{ position: 'fixed', top: menu.rect.bottom + 6, left, right: 'auto' }}>
      <p className="popover__title">{title} · {items.length}</p>
      {searchable && (
        <label className="tree-filter__box crumb-menu__filter">
          <Icon name="search" size={14} />
          <input type="search" value={query} placeholder={t('Filtrer')} aria-label={t('Filtrer')} autoComplete="off"
            onChange={(e) => setQuery(e.target.value)} />
        </label>
      )}
      <div className="popover__group popover__group--actions crumb-menu__list">
        {groups ? groups.map((g) => (
          <div key={g.key} className="crumb-menu__group">
            <p className="crumb-menu__heading">{g.kind === OTHER_KINDS ? t('Autres types') : placeKind(g.kind, kinds).label}</p>
            {g.items.map(item)}
          </div>
        )) : shown.map(item)}
        {shown.length === 0 && <p className="muted crumb-menu__empty">{t('Aucun lieu ne correspond.')}</p>}
      </div>
    </div>,
    document.body,
  );
}
