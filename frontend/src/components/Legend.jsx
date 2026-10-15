import { useEffect, useRef, useState } from 'react';
import { Icon } from './ui.jsx';
import { useSheet } from '../viewport.js';

/** Légende cliquable, repliée par défaut : chaque type de relation peut être masqué. */
export function Legend({ graph, filters, onFilters }) {
  // Toujours repliée à l'ouverture de la carte.
  const [open, setOpen] = useState(false);
  const used = new Set(graph.relations.map((r) => r.type));
  const types = (graph.relation_types || []).filter((t) => used.has(t.key)).map((t) => [t.key, t]);
  if (!types.length) return null;

  const hiddenCount = types.filter(([k]) => filters.hiddenTypes.includes(k)).length;
  const toggleOpen = () => {
    setOpen((o) => !o);
  };
  const toggleType = (key) => {
    const hidden = new Set(filters.hiddenTypes);
    if (hidden.has(key)) hidden.delete(key); else hidden.add(key);
    onFilters({ ...filters, hiddenTypes: [...hidden] });
  };

  return (
    <div className={`legend${open ? ' is-open' : ''}`}>
      <button type="button" className="legend__toggle" aria-expanded={open} aria-controls="legend-items"
        onClick={toggleOpen} title={open ? 'Replier la légende' : 'Afficher la légende des liens'}>
        <span className="legend__dots" aria-hidden="true">
          {types.slice(0, 4).map(([key, t]) => <span key={key} style={{ background: t.color }} />)}
        </span>
        Légende
        {hiddenCount > 0 && (
          <span className="badge" title={`${hiddenCount} type(s) de lien masqué(s)`}>{hiddenCount}</span>
        )}
        <span className="legend__chevron"><Icon name="chevron" size={14} /></span>
      </button>
      {open && (
        <div className="legend__items" id="legend-items" aria-label="Types de liens">
          {types.map(([key, t]) => {
            const off = filters.hiddenTypes.includes(key);
            return (
              <button key={key} type="button" className={`legend__item${off ? ' is-off' : ''}`}
                style={{ '--rel': t.color }} aria-pressed={!off} onClick={() => toggleType(key)}
                title={off ? `Afficher les liens « ${t.label} »` : `Masquer les liens « ${t.label} »`}>
                <span className="legend__swatch" />{t.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function FilterMenu({ graph, filters, onFilters }) {
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

  const activeCount = filters.hiddenFactions.length + filters.hiddenTypes.length
    + Number(!filters.showDead) + Number(!filters.showSecret) + Number(!filters.showMemberships);
  const toggleFaction = (id) => {
    const hidden = new Set(filters.hiddenFactions);
    if (hidden.has(id)) hidden.delete(id); else hidden.add(id);
    onFilters({ ...filters, hiddenFactions: [...hidden] });
  };
  const flag = (key, label) => (
    <label className="check">
      <input type="checkbox" checked={filters[key]} onChange={(e) => onFilters({ ...filters, [key]: e.target.checked })} />
      {label}
    </label>
  );

  return (
    <div className="popover-anchor" ref={ref}>
      <button type="button" className={`btn btn--ghost${activeCount ? ' has-badge' : ''}`} aria-expanded={open}
        onClick={() => setOpen((o) => !o)} title="Options d’affichage" aria-label="Options d’affichage">
        <Icon name="filter" size={16} /><span className="btn__label">Affichage</span>
        {activeCount > 0 && <span className="badge">{activeCount}</span>}
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="Options d'affichage">
          <div className="popover__group">
            {flag('showMemberships', 'Liens d’appartenance aux factions')}
            {flag('showDead', 'PNJ morts')}
            {flag('showSecret', 'Relations secrètes')}
          </div>
          {graph.factions.length > 0 && (
            <div className="popover__group">
              <p className="popover__title">Factions affichées</p>
              {graph.factions.map((f) => (
                <label key={f.id} className="check" style={{ '--chip': f.color }}>
                  <input type="checkbox" checked={!filters.hiddenFactions.includes(f.id)} onChange={() => toggleFaction(f.id)} />
                  <span className="chip__dot" />{f.name}
                </label>
              ))}
            </div>
          )}
          {activeCount > 0 && (
            <button type="button" className="btn btn--small btn--ghost popover__reset"
              onClick={() => onFilters({ hiddenTypes: [], hiddenFactions: [], showMemberships: true, showDead: true, showSecret: true })}>
              Tout afficher
            </button>
          )}
        </div>
      )}
    </div>
  );
}
