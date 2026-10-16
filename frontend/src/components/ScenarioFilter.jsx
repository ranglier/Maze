import { useEffect, useMemo, useRef, useState } from 'react';
import { normalize, nodeId } from '../util.js';
import { castOf, scenariosOf } from '../cast.js';
import { Icon } from './ui.jsx';
import { useSheet } from '../viewport.js';
import { compareText, t, tn, useLocale } from '../i18n/index.js';

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
        onClick={() => setOpen((o) => !o)} title={t('Changer de carte')}>
        <Icon name={selected ? 'book' : 'banner'} size={16} />
        <span className="map-pick__label">{label}</span>
        <Icon name="chevron" size={14} />
      </button>
      {open && (
        <div className="popover scenario-pick" role="menu" aria-label={t('Choisir la carte')}>
          <p className="popover__title">{t('Carte de la campagne')}</p>
          <ul>
            <li>
              <button type="button" role="menuitemradio" aria-checked={!selected}
                className={`scenario-pick__item${!selected ? ' is-active' : ''}`} style={{ '--depth': 0 }}
                onClick={() => { setOpen(false); onPick(null); }}>
                <span className="scenario-pick__title">{t('Carte des factions')}</span>
                <span className="scenario-pick__count">{t('factions et liens entre elles')}</span>
              </button>
            </li>
          </ul>
          <p className="popover__title">{t('Cartes des scénarios')}</p>
          {!data ? <p className="muted">{t('Chargement…')}</p> : (
            <ul>
              {scenarios.map((d) => {
                const count = castOf(data, d.id).nodes.size;
                return (
                  <li key={d.id}>
                    <button type="button" role="menuitemradio" aria-checked={selected === d.id}
                      className={`scenario-pick__item${selected === d.id ? ' is-active' : ''}`} style={{ '--depth': 0 }}
                      onClick={() => { setOpen(false); onPick(d.id); }}>
                      <span className="scenario-pick__title">{d.title || t('Sans titre')}</span>
                      <span className="scenario-pick__count">{count === 0 ? t('carte vide') : tn('{n} fiche', '{n} fiches', count)}</span>
                    </button>
                  </li>
                );
              })}
              {scenarios.length === 0 && <li className="muted scenario-pick__empty">{t('Aucun scénario pour l’instant.')}</li>}
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
  const locale = useLocale();
  const items = useMemo(() => {
    if (!graph) return [];
    const all = [
      ...graph.npcs.map((n) => ({ id: nodeId('npc', n.id), name: n.name, sub: n.title, kind: 'npc' })),
      ...graph.factions.map((f) => ({ id: nodeId('faction', f.id), name: f.name, sub: f.kind || t('Faction'), kind: 'faction' })),
    ].filter((i) => !cast.has(i.id));
    const q = normalize(query.trim());
    const found = q ? all.filter((i) => normalize(`${i.name} ${i.sub || ''}`).includes(q)) : all;
    return found.sort((a, b) => Number(excluded.has(b.id)) - Number(excluded.has(a.id)) || compareText(a.name, b.name))
      .slice(0, 60);
  }, [graph, cast, excluded, query, locale]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="popover-anchor" ref={ref}>
      <button type="button" className="btn btn--ghost" aria-expanded={open} onClick={() => { setOpen((o) => !o); setQuery(''); }}
        title={t('Ajouter à cette carte un PNJ ou une faction de la campagne')}>
        <Icon name="plus" size={16} /><span className="btn__label">{t('Ajouter')}</span>
      </button>
      {open && (
        <div className="popover add-to-map" role="dialog" aria-label={t('Ajouter à la carte')}>
          <input type="search" className="input" placeholder={t('PNJ ou faction de la campagne…')} value={query} autoFocus
            onChange={(e) => setQuery(e.target.value)} aria-label={t('Rechercher une fiche')} />
          <ul>
            {items.map((i) => (
              <li key={i.id}>
                <button type="button" onClick={() => { onAdd(i.id); setOpen(false); }}>
                  <Icon name={i.kind === 'npc' ? 'person' : 'banner'} size={15} />
                  <span className="add-to-map__name">{i.name}</span>
                  <span className="add-to-map__sub">{excluded.has(i.id) ? t('citée, retirée de la carte') : i.sub}</span>
                </button>
              </li>
            ))}
            {items.length === 0 && <li className="muted add-to-map__empty">{query ? t('Aucune fiche trouvée.') : t('Toutes les fiches sont déjà sur la carte.')}</li>}
          </ul>
        </div>
      )}
    </div>
  );
}

// Infobulle de l'encart selon `verb` (identifiant, jamais affiché tel quel) : phrase entière, accord compris.
function bannerTitle(verb, title, count) {
  if (!verb) return tn('{title} : {n} fiche', '{title} : {n} fiches', count, { title });
  if (verb === 'présente') return tn('{title} : {n} fiche présente', '{title} : {n} fiches présentes', count, { title });
  return tn('{title} : {n} fiche citée', '{title} : {n} fiches citées', count, { title });
}

/**
 * Encart de la carte affichée (scénario ou lieu), replié par défaut en haut à droite, comme la légende :
 * replié, le nombre de fiches ; déplié, les voisins directs, l'ouverture de la fiche et le retour aux factions.
 * `verb` choisit l'infobulle : 'citée' (défaut, fiches citées), 'présente' (lieu : fiches présentes) ou '' (fiches) ;
 * c'est un identifiant, passé tel quel (non traduit) : le texte affiché est traduit ici.
 */
export function ScenarioBanner({
  title, count, hiddenCount, neighbors, onNeighbors, onOpenDoc, onClear,
  icon = 'book', verb = 'citée', // i18n-ignore (identifiant de l'infobulle, voir bannerTitle)
  openLabel = t('Ouvrir la fiche'), clearLabel = t('Retirer le filtre'),
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`scenario-banner${open ? ' is-open' : ''}`} role="status">
      {open && (
        <div className="scenario-banner__body" id="map-info-body">
          {hiddenCount > 0 && (
            <span className="muted scenario-banner__hidden">
              {tn('{n} masquée par l’affichage', '{n} masquées par l’affichage', hiddenCount)}
            </span>
          )}
          {onNeighbors && (
            <label className="check scenario-banner__check">
              <input type="checkbox" checked={neighbors} onChange={(e) => onNeighbors(e.target.checked)} />
              {t('Voisins directs')}
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
        title={bannerTitle(verb, title, count)} onClick={() => setOpen((o) => !o)}>
        <span className="scenario-banner__chevron"><Icon name="chevron" size={14} /></span>
        <Icon name={icon} size={15} />
        <span className="scenario-banner__text">
          {neighbors ? tn('{n} fiche + voisins', '{n} fiches + voisins', count) : tn('{n} fiche', '{n} fiches', count)}
        </span>
      </button>
    </div>
  );
}
