import { useEffect, useMemo, useRef, useState } from 'react';
import { normalize, nodeId } from '../util.js';
import { castOf, scenariosOf } from '../cast.js';
import { Icon } from './ui.jsx';
import { useSheet } from '../viewport.js';

/*
 * Cartes des relations : une par scénario, plus la carte des factions de la campagne.
 * La distribution d'un scénario = fiches citées (@) dans le scénario et ses pages,
 * plus les ajouts manuels, moins les retraits (réglages enregistrés côté serveur) : cast.js.
 */

function usePopover() {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useSheet(open);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);
  return { open, setOpen, ref };
}

/** Choix de la carte : un scénario, ou la carte des factions. */
export function MapPicker({ data, selected, label, onPick }) {
  const { open, setOpen, ref } = usePopover();
  const scenarios = scenariosOf(data);
  return (
    <div className="popover-anchor" ref={ref}>
      <button type="button" className="btn btn--ghost map-pick__btn" aria-expanded={open} aria-haspopup="menu"
        onClick={() => setOpen((o) => !o)} title="Changer de carte">
        <Icon name={selected ? 'book' : 'banner'} size={16} />
        <span className="map-pick__label">{label}</span>
        <Icon name="chevron" size={14} />
      </button>
      {open && (
        <div className="popover scenario-pick" role="menu" aria-label="Choisir la carte">
          <p className="popover__title">Carte de la campagne</p>
          <ul>
            <li>
              <button type="button" role="menuitemradio" aria-checked={!selected}
                className={`scenario-pick__item${!selected ? ' is-active' : ''}`} style={{ '--depth': 0 }}
                onClick={() => { setOpen(false); onPick(null); }}>
                <span className="scenario-pick__title">Carte des factions</span>
                <span className="scenario-pick__count">factions et liens entre elles</span>
              </button>
            </li>
          </ul>
          <p className="popover__title">Cartes des scénarios</p>
          {!data ? <p className="muted">Chargement…</p> : (
            <ul>
              {scenarios.map((d) => {
                const count = castOf(data, d.id).nodes.size;
                return (
                  <li key={d.id}>
                    <button type="button" role="menuitemradio" aria-checked={selected === d.id}
                      className={`scenario-pick__item${selected === d.id ? ' is-active' : ''}`} style={{ '--depth': 0 }}
                      onClick={() => { setOpen(false); onPick(d.id); }}>
                      <span className="scenario-pick__title">{d.title || 'Sans titre'}</span>
                      <span className="scenario-pick__count">{count === 0 ? 'carte vide' : `${count} fiche${count > 1 ? 's' : ''}`}</span>
                    </button>
                  </li>
                );
              })}
              {scenarios.length === 0 && <li className="muted scenario-pick__empty">Aucun scénario pour l’instant.</li>}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/** Ajout d'une fiche de la campagne à la carte du scénario (fiches retirées d'abord). */
export function AddToMap({ graph, cast, excluded, onAdd }) {
  const { open, setOpen, ref } = usePopover();
  const [query, setQuery] = useState('');
  const items = useMemo(() => {
    if (!graph) return [];
    const all = [
      ...graph.npcs.map((n) => ({ id: nodeId('npc', n.id), name: n.name, sub: n.title, kind: 'npc' })),
      ...graph.factions.map((f) => ({ id: nodeId('faction', f.id), name: f.name, sub: f.kind || 'Faction', kind: 'faction' })),
    ].filter((i) => !cast.has(i.id));
    const q = normalize(query.trim());
    const found = q ? all.filter((i) => normalize(`${i.name} ${i.sub || ''}`).includes(q)) : all;
    return found.sort((a, b) => Number(excluded.has(b.id)) - Number(excluded.has(a.id)) || a.name.localeCompare(b.name, 'fr'))
      .slice(0, 60);
  }, [graph, cast, excluded, query]);
  return (
    <div className="popover-anchor" ref={ref}>
      <button type="button" className="btn btn--ghost" aria-expanded={open} onClick={() => { setOpen((o) => !o); setQuery(''); }}
        title="Ajouter à cette carte un PNJ ou une faction de la campagne">
        <Icon name="plus" size={16} /><span className="btn__label">Ajouter</span>
      </button>
      {open && (
        <div className="popover add-to-map" role="dialog" aria-label="Ajouter à la carte">
          <input type="search" className="input" placeholder="PNJ ou faction de la campagne…" value={query} autoFocus
            onChange={(e) => setQuery(e.target.value)} aria-label="Rechercher une fiche" />
          <ul>
            {items.map((i) => (
              <li key={i.id}>
                <button type="button" onClick={() => { onAdd(i.id); setOpen(false); }}>
                  <Icon name={i.kind === 'npc' ? 'person' : 'banner'} size={15} />
                  <span className="add-to-map__name">{i.name}</span>
                  <span className="add-to-map__sub">{excluded.has(i.id) ? 'citée, retirée de la carte' : i.sub}</span>
                </button>
              </li>
            ))}
            {items.length === 0 && <li className="muted add-to-map__empty">{query ? 'Aucune fiche trouvée.' : 'Toutes les fiches sont déjà sur la carte.'}</li>}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * Encart de la carte affichée (scénario ou lieu), replié par défaut en haut à droite, comme la légende :
 * replié, le nombre de fiches ; déplié, les voisins directs, l'ouverture de la fiche et le retour aux factions.
 */
export function ScenarioBanner({
  title, count, hiddenCount, neighbors, onNeighbors, onOpenDoc, onClear,
  icon = 'book', verb = 'citée', openLabel = 'Ouvrir la fiche', clearLabel = 'Retirer le filtre',
}) {
  const [open, setOpen] = useState(false);
  const plural = count > 1 ? 's' : '';
  return (
    <div className={`scenario-banner${open ? ' is-open' : ''}`} role="status">
      {open && (
        <div className="scenario-banner__body" id="map-info-body">
          {hiddenCount > 0 && (
            <span className="muted scenario-banner__hidden">{hiddenCount} masquée{hiddenCount > 1 ? 's' : ''} par l’affichage</span>
          )}
          {onNeighbors && (
            <label className="check scenario-banner__check">
              <input type="checkbox" checked={neighbors} onChange={(e) => onNeighbors(e.target.checked)} />
              Voisins directs
            </label>
          )}
          {onOpenDoc && <button type="button" className="btn btn--small btn--ghost" onClick={onOpenDoc}>{openLabel}</button>}
          {onClear && (
            <button type="button" className="icon-btn icon-btn--small" aria-label={clearLabel} title={clearLabel} onClick={onClear}>
              <Icon name="close" size={15} />
            </button>
          )}
        </div>
      )}
      <button type="button" className="scenario-banner__toggle" aria-expanded={open} aria-controls="map-info-body"
        title={`${title} : ${count} fiche${plural}${verb ? ` ${verb}${plural}` : ''}`} onClick={() => setOpen((o) => !o)}>
        <span className="scenario-banner__chevron"><Icon name="chevron" size={14} /></span>
        <Icon name={icon} size={15} />
        <span className="scenario-banner__text">{count} fiche{plural}{neighbors ? ' + voisins' : ''}</span>
      </button>
    </div>
  );
}
