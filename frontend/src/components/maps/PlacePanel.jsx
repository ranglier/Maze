import { useContext, useEffect, useState } from 'react';
import { api } from '../../api.js';
import { NPC_STATUS, placeKind } from '../../constants.js';
import { initialsAvatar } from '../../util.js';
import { KindsContext, PlaceIcon } from '../PlacesView.jsx';
import { Icon } from '../ui.jsx';
import { childrenOf } from './mapTree.js';

// --- Fiche du lieu choisi ---------------------------------------------------------------------------

export default function PlacePanel({ placeId, campaignId, byId, places, path, pinned, editing, ownMaps, onPath, onEnter, onClose, onArm, onRemovePin }) {
  const [place, setPlace] = useState(null);
  const kinds = useContext(KindsContext);
  useEffect(() => {
    let live = true;
    api.getEntity('place', placeId).then((d) => { if (live) setPlace(d); }).catch((err) => { if (live) setPlace({ error: err.message }); });
    return () => { live = false; };
  }, [placeId]);
  const base = byId.get(placeId);
  const kids = childrenOf(places, placeId);
  const cover = place?.images?.find((i) => i.id === place.image_id);
  const kind = placeKind(base.kind, kinds);
  const lineage = path.slice(0, -1).map((id) => byId.get(id)).filter(Boolean);
  const avatar = (l) => l.thumb || (l.entity_kind === 'npc' ? initialsAvatar(l.name, '#5d6b80') : null);

  return (
    <aside className="map-panel" aria-label={`Lieu : ${base.name}`}
      onPointerDown={(e) => e.stopPropagation()} onPointerUp={(e) => e.stopPropagation()}>
      <div className="map-panel__head">
        {lineage.length > 0 && (
          <nav className="map-panel__lineage" aria-label="Lieux parents">
            {lineage.map((p, i) => (
              <span key={p.id}>
                <button type="button" onClick={() => onPath(path.slice(0, i + 1))}>{p.name}</button>
                <Icon name="right" size={12} />
              </span>
            ))}
          </nav>
        )}
        <button type="button" className="icon-btn icon-btn--small map-panel__close" onClick={onClose} aria-label="Fermer" title="Fermer (Échap)">
          <Icon name="close" size={16} />
        </button>
        <p className="place__kind"><PlaceIcon kind={base.kind} size={14} /> {kind.label}</p>
        <h2 className="map-panel__title">{base.name}</h2>
      </div>
      <div className="map-panel__body">
        {cover && <img className="map-panel__cover" src={cover.url} alt="" />}
        {base.summary && <p className="map-panel__summary">{base.summary}</p>}
        {ownMaps.map((m) => (
          <button key={m.id} type="button" className="btn btn--primary btn--block" onClick={() => onEnter(m)}>
            <Icon name="map" size={16} /> Entrer : {m.name}
          </button>
        ))}
        {kids.length > 0 && (
          <section className="map-panel__section">
            <h3>Sous-lieux <span className="count">{kids.length}</span></h3>
            <ul className="map-panel__list">
              {kids.map((k) => (
                <li key={k.id}>
                  <button type="button" onClick={() => onPath([...path, k.id])}>
                    <PlaceIcon kind={k.kind} size={14} /><span>{k.name}</span>
                    {childrenOf(places, k.id).length > 0 && <span className="count">{childrenOf(places, k.id).length}</span>}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        {place && !place.error && place.links.length + place.nested_links.length > 0 && (
          <section className="map-panel__section">
            <h3>Présents ici <span className="count">{place.links.length}</span></h3>
            <ul className="map-panel__list map-panel__people">
              {place.links.map((l) => (
                <li key={l.id}>
                  <a href={`#/c/${campaignId}/personnages/${l.entity_kind}-${l.entity_id}`}>
                    {avatar(l) ? <img src={avatar(l)} alt="" className={l.entity_kind === 'faction' ? 'is-faction' : ''} />
                      : <span className="place-links__seal" style={{ background: l.color }} />}
                    <span>{l.status === 'mort' ? `† ${l.name}` : l.name}</span>
                    <span className="muted">{[l.role, l.entity_kind === 'npc' && l.status !== 'vivant' && NPC_STATUS[l.status]?.label].filter(Boolean).join(' · ')}</span>
                  </a>
                </li>
              ))}
            </ul>
            {place.nested_links.length > 0 && (
              <p className="muted map-panel__nested">et {place.nested_links.length} dans ses sous-lieux</p>
            )}
          </section>
        )}
        {place?.error && <p className="panel__error">{place.error}</p>}
      </div>
      <div className="map-panel__foot">
        <a className="btn btn--ghost" href={`#/c/${campaignId}/lieux/${placeId}`}><Icon name="page" size={16} /> Ouvrir la fiche</a>
        {editing && (pinned ? (
          <button type="button" className="btn btn--ghost btn--danger-text" onClick={onRemovePin}>
            <Icon name="trash" size={16} /> Retirer l’épingle
          </button>
        ) : (
          <button type="button" className="btn btn--ghost" onClick={onArm} title="Lui donner sa propre épingle sur cette carte">
            <Icon name="pin" size={16} /> Épingler
          </button>
        ))}
      </div>
    </aside>
  );
}
