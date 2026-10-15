import { useMemo, useState } from 'react';
import { PlaceIcon } from '../PlacesView.jsx';
import { Icon } from '../ui.jsx';
import { TOUCH } from '../../viewport.js';
import { DRAG_TYPE } from './constants.js';
import { childrenOf } from './mapTree.js';

// --- Liste des lieux à placer (mode « Placer les lieux ») ---------------------------------------------

export default function PinList({ places, pinned, represented, armed, selected, onArm, onReveal, campaignId }) {
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const rows = useMemo(() => {
    const out = [];
    const walk = (parentId, depth) => {
      for (const p of childrenOf(places, parentId)) {
        out.push({ p, depth });
        walk(p.id, depth + 1);
      }
    };
    // Carte d'un lieu : ses sous-lieux d'abord, puis les autres.
    if (represented != null && places.some((p) => p.id === represented)) {
      walk(represented, 0);
      const inside = new Set(out.map((r) => r.p.id));
      inside.add(represented);
      const rest = [];
      const walkRest = (parentId, depth) => {
        for (const p of childrenOf(places, parentId)) {
          if (!inside.has(p.id)) rest.push({ p, depth, other: true });
          walkRest(p.id, depth + 1);
        }
      };
      walkRest(null, 0);
      return { main: out, rest };
    }
    walk(null, 0);
    return { main: out, rest: [] };
  }, [places, represented]);
  const filter = (list) => (q ? list.filter((r) => r.p.name.toLowerCase().includes(q)).map((r) => ({ ...r, depth: 0 })) : list);
  const repName = places.find((p) => p.id === represented)?.name;

  const row = ({ p, depth }) => {
    const isPinned = pinned.has(p.id);
    return (
      <li key={p.id}>
        <button type="button" draggable={!isPinned}
          className={`pin-list__item${isPinned ? ' is-pinned' : ''}${armed === p.id ? ' is-armed' : ''}${selected === p.id ? ' is-active' : ''}`}
          style={{ '--depth': depth }}
          onDragStart={(e) => { e.dataTransfer.setData(DRAG_TYPE, String(p.id)); e.dataTransfer.effectAllowed = 'copy'; }}
          onClick={() => (isPinned ? onReveal(p.id) : onArm(p.id))}
          title={isPinned ? 'Sur la carte : cliquer pour la montrer' : 'Glisser sur la carte, ou cliquer puis cliquer sur la carte'}>
          <PlaceIcon kind={p.kind} size={14} />
          <span className="pin-list__name">{p.name}</span>
          {isPinned && <Icon name="pin" size={14} title="Épinglé sur cette carte" />}
        </button>
      </li>
    );
  };

  return (
    <aside className="doc-tree pin-list" aria-label="Lieux à placer">
      <p className="pin-list__help">
        {TOUCH
          ? 'Touchez un lieu, puis l’endroit de la carte où le poser. Faites glisser une épingle pour la déplacer.'
          : 'Glissez un lieu sur la carte, ou cliquez-le puis cliquez sur la carte. Glissez une épingle pour la déplacer.'}
        {' '}Les sous-lieux d’un lieu épinglé apparaissent dans son anneau : inutile de les épingler.</p>
      <input type="search" className="input" placeholder="Rechercher un lieu…" value={query} onChange={(e) => setQuery(e.target.value)}
        aria-label="Rechercher un lieu" />
      {places.length === 0 ? (
        <p className="muted pin-list__empty">Aucun lieu dans cette campagne. <a href={`#/c/${campaignId}/lieux`}>Créez-en dans l’index des lieux.</a></p>
      ) : (
        <>
          {repName && <div className="doc-tree__section"><span>Dans {repName}</span></div>}
          <ul className="doc-tree__list">{filter(rows.main).map(row)}</ul>
          {rows.rest.length > 0 && (
            <>
              <div className="doc-tree__section"><span>Autres lieux</span></div>
              <ul className="doc-tree__list">{filter(rows.rest).map(row)}</ul>
            </>
          )}
        </>
      )}
    </aside>
  );
}
