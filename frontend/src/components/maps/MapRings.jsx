import { t, tn } from '../../i18n/index.js';
import { Icon } from '../ui.jsx';
import { PlaceIcon } from '../PlacesView.jsx';
import { arcPath } from './rings.js';
import { childrenOf } from './mapTree.js';

/** Anneaux ouverts (dessin) : carte assombrie, voile sous chaque anneau, cercles ou arcs, rayons vers les puces. */
export function RingLines({ rings }) {
  if (!rings.length) return null;
  return (
    <svg className="map-rings" width="100%" height="100%" aria-hidden="true">
      {/* Carte assombrie pendant qu'un anneau est ouvert : les anneaux et leurs puces ressortent du fond. */}
      <rect x="0" y="0" width="100%" height="100%" className="map-rings__dim" />
      {rings.map((r) => (
        <circle key={`veil-${r.placeId}`} cx={r.cx} cy={r.cy} r={r.radius + (r.full ? 40 : 30)} className="map-rings__veil" />
      ))}
      {rings.map((r) => (
        <g key={r.placeId} className={r.level ? 'map-rings__level map-rings__level--sub' : 'map-rings__level'}>
          {r.items.map((it, i) => (
            <line key={i} x1={r.cx} y1={r.cy} x2={it.x} y2={it.y} className="map-rings__spoke" />
          ))}
          {r.full ? (
            <>
              <circle cx={r.cx} cy={r.cy} r={r.radius} className="map-rings__halo" />
              <circle cx={r.cx} cy={r.cy} r={r.radius} className="map-rings__ring" />
            </>
          ) : (
            <>
              <path d={arcPath(r.cx, r.cy, r.radius, r.from, r.to)} className="map-rings__halo" />
              <path d={arcPath(r.cx, r.cy, r.radius, r.from, r.to)} className="map-rings__ring" />
            </>
          )}
        </g>
      ))}
    </svg>
  );
}

/** Puces des anneaux : sous-lieux (choisir, entrer dans sa carte), « +N » (page suivante), « +N autres » (frères
 * repliés). Les « +N autres » sont rendues en dernier, au-dessus des anneaux plus profonds. */
export function RingChips({ rings, path, expanded, places, byId, ownMaps, onPath, onEnter, onExpand, onPage }) {
  const inPath = new Set(path);
  return rings.flatMap((r) => r.items.map((it) => [r, it])).sort((a, b) => Boolean(a[1].expand) - Boolean(b[1].expand)).map(([r, it]) => {
    if (it.expand) {
      const parentName = byId.get(it.expand)?.name || '';
      return (
        <button key={`${r.placeId}-expand`} type="button" className="map-chip map-chip--more" style={{ left: it.x, top: it.y }}
          onPointerDown={(e) => e.stopPropagation()} onPointerUp={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            onExpand(it.expand, true);
            onPage(it.expand, it.page);   // la page du lieu choisi
          }}
          title={t('Afficher les autres sous-lieux de {name}', { name: parentName })}>{tn('+{n} autre', '+{n} autres', it.count)}</button>
      );
    }
    if (it.more) {
      return (
        <button key={`${r.placeId}-more`} type="button" className="map-chip map-chip--more" style={{ left: it.x, top: it.y }}
          onPointerDown={(e) => e.stopPropagation()} onPointerUp={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onPage(r.placeId, it.nextPage); }}
          title={t('Afficher les autres sous-lieux')}>+{it.more}</button>
      );
    }
    const p = it.place;
    const kids = childrenOf(places, p.id).length;
    const own = ownMaps(p.id);
    const isSel = inPath.has(p.id);
    return (
      <button key={p.id} type="button" className={`map-chip${r.level ? ' map-chip--sub' : ''}${isSel ? ' is-open' : ''}`}
        style={{ left: it.x, top: it.y }}
        onPointerDown={(e) => e.stopPropagation()} onPointerUp={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          const base = path.slice(0, r.level + 1);
          // Lieu choisi alors que ses frères sont redéployés : rouvrir la chaîne, repliée.
          if (isSel && expanded[r.placeId]) { onExpand(r.placeId, false); return; }
          onPath(isSel && path[path.length - 1] === p.id ? base : [...base, p.id]);
        }}
        onDoubleClick={(e) => { e.stopPropagation(); if (own.length) onEnter(own[0]); }}
        title={[p.name, kids > 0 && tn('{n} sous-lieu', '{n} sous-lieux', kids),
          own.length > 0 && t('double-clic : entrer dans sa carte')].filter(Boolean).join(' · ')}>
        <PlaceIcon kind={p.kind} size={14} />
        <span className="map-chip__name">{p.name}</span>
        {kids > 0 && <span className="map-chip__count">{kids}</span>}
        {own.length > 0 && <Icon name="map" size={13} title={t('A sa propre carte')} />}
      </button>
    );
  });
}
