import { useContext, useEffect, useRef, useState } from 'react';
import { placeKind } from '../../constants.js';
import { t, tn } from '../../i18n/index.js';
import { storage } from '../../util.js';
import { KindsContext, PlaceIcon } from '../PlacesView.jsx';
import { Icon, KindImage } from '../ui.jsx';
import { TOUCH } from '../../viewport.js';
import { DRAG_TYPE, PREVIEW_SIDE, panelBeside } from './constants.js';
import { childrenOf, clamp } from './mapTree.js';
import { NO_EXPANDED, layoutRings } from './rings.js';
import { RingChips, RingLines } from './MapRings.jsx';
import MapZoom from './MapZoom.jsx';
import useMapViewport from './useMapViewport.js';

// --- Carte : déplacement, zoom, épingles et anneaux ------------------------------------------------

export default function MapCanvas({ map, byId, places, path, editing, armed, center, ownMaps, trail,
  onPath, onMovePin, onPlace, onCancelArm, onEnter, onTrail, onPinStyle }) {
  const kinds = useContext(KindsContext);
  const ref = useRef(null);
  const [loaded, setLoaded] = useState(false);
  // Version d'affichage d'abord (légère) ; pleine définition chargée en arrière-plan dès qu'un zoom la rend
  // utile, puis gardée.
  const [src, setSrc] = useState(map.preview_url || map.url);
  const [hdLoading, setHdLoading] = useState(false);
  const hdAsked = useRef(!map.preview_url);
  const [drag, setDrag] = useState(null);           // épingle déplacée { place_id, x, y }
  const [pages, setPages] = useState({});           // page affichée de chaque anneau (lieu → rang)
  // Anneaux dont les frères du lieu choisi sont redéployés ; valable pour le chemin courant seulement.
  const [exp, setExp] = useState({ path, ids: NO_EXPANDED });
  const expanded = exp.path === path ? exp.ids : NO_EXPANDED;
  const setExpanded = (placeId, on) => setExp((x) => ({ path, ids: { ...(x.path === path ? x.ids : {}), [placeId]: on } }));
  useEffect(() => {   // Échap avec des frères redéployés : les replier (avant de refermer un anneau)
    if (!Object.values(expanded).some(Boolean)) return undefined;
    const onKey = (e) => {
      if (e.key !== 'Escape' || e.target.closest?.('input, textarea, select, .modal, .map-zoom')) return;
      e.stopImmediatePropagation();
      setExp({ path, ids: NO_EXPANDED });
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [expanded, path]);
  const [dropping, setDropping] = useState(false);
  // Noms des épingles masqués par défaut (le fond porte souvent déjà les noms) ; choix retenu par carte,
  // dans ce navigateur. Masqués, ils restent visibles au survol et sur l'épingle ouverte.
  const labelsKey = `maze:map-labels:${map.id}`;
  const [labels, setLabels] = useState(() => storage.get(labelsKey) === '1');
  const toggleLabels = () => setLabels((v) => { storage.set(labelsKey, v ? '0' : '1'); return !v; });
  const pointers = useRef(new Map());
  const gesture = useRef(null);
  const W = map.width;
  const H = map.height;
  const { size, view, setView, limit, fit, animateTo, zoomAt, anim } = useMapViewport({ ref, W, H, center });

  const toImage = (clientX, clientY) => {
    const r = ref.current.getBoundingClientRect();
    return { x: clamp((clientX - r.left - view.x) / (W * view.k), 0, 1), y: clamp((clientY - r.top - view.y) / (H * view.k), 0, 1) };
  };
  const inImage = (clientX, clientY) => {
    const r = ref.current.getBoundingClientRect();
    const x = (clientX - r.left - view.x) / (W * view.k);
    const y = (clientY - r.top - view.y) / (H * view.k);
    return x >= 0 && x <= 1 && y >= 0 && y <= 1;
  };

  // Fond : glisser pour se déplacer, pincer pour zoomer ; un simple clic ferme les anneaux (ou pose le lieu choisi).
  const onPointerDown = (e) => {
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    cancelAnimationFrame(anim.current);
    ref.current.setPointerCapture(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    gesture.current = { moved: pointers.current.size > 1 ? 99 : 0, last: { x: e.clientX, y: e.clientY }, pinch: null };
  };
  const onPointerMove = (e) => {
    if (!pointers.current.has(e.pointerId) || !gesture.current) return;
    const g = gesture.current;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const pts = [...pointers.current.values()];
    if (pts.length >= 2) {
      const [a, b] = pts;
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      const r = ref.current.getBoundingClientRect();
      const mid = { x: (a.x + b.x) / 2 - r.left, y: (a.y + b.y) / 2 - r.top };
      if (g.pinch) {
        zoomAt(dist / g.pinch.dist, mid.x, mid.y);
        setView((v) => v && limit({ ...v, x: v.x + mid.x - g.pinch.mid.x, y: v.y + mid.y - g.pinch.mid.y }));
      }
      g.pinch = { dist, mid };
      g.moved = 99;
      return;
    }
    const dx = e.clientX - g.last.x;
    const dy = e.clientY - g.last.y;
    g.last = { x: e.clientX, y: e.clientY };
    g.moved += Math.abs(dx) + Math.abs(dy);
    if (g.moved > 4) setView((v) => v && limit({ ...v, x: v.x + dx, y: v.y + dy }));
  };
  const onPointerUp = (e) => {
    const g = gesture.current;
    pointers.current.delete(e.pointerId);
    if (pointers.current.size === 0) gesture.current = null;
    else if (g) { g.pinch = null; g.last = [...pointers.current.values()][0]; }
    if (!g || g.moved > 4 || pointers.current.size > 0 || e.type === 'pointercancel') return;
    if (armed != null) {
      if (inImage(e.clientX, e.clientY)) { const p = toImage(e.clientX, e.clientY); onPlace(armed, p.x, p.y); }
      return;
    }
    onPath([]);
  };

  // Épingles : clic pour ouvrir l'anneau ; en mode « Placer les lieux », glisser pour déplacer.
  const pinDown = (e, pin) => {
    e.stopPropagation();
    if (!editing) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag({ place_id: pin.place_id, x: pin.x, y: pin.y, sx: e.clientX, sy: e.clientY, moved: false });
  };
  const pinMove = (e) => {
    if (!drag) return;
    const moved = drag.moved || Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 4;
    const p = toImage(e.clientX, e.clientY);
    setDrag({ ...drag, ...(moved ? p : {}), moved });
  };
  const pinUp = (e, pin) => {
    e.stopPropagation();
    if (drag?.moved) onMovePin(pin.place_id, drag.x, drag.y);
    else togglePin(pin.place_id);
    setDrag(null);
  };
  const togglePin = (placeId) => onPath(path[0] === placeId && path.length === 1 ? [] : [placeId]);

  const onDrop = (e) => {
    setDropping(false);
    const id = Number(e.dataTransfer.getData(DRAG_TYPE));
    if (!id || !view || !inImage(e.clientX, e.clientY)) return;
    e.preventDefault();
    const p = toImage(e.clientX, e.clientY);
    onPlace(id, p.x, p.y);
  };

  const screen = (px, py) => ({ x: view.x + px * W * view.k, y: view.y + py * H * view.k });
  // Style des épingles : pastille, icône du type (image importée en grand), ou discrète (zone cliquable posée
  // sur le symbole imprimé du fond, à sa taille approximative : elle suit le zoom).
  const pinStyle = map.pin_style || 'dot';
  const hitSize = view ? clamp(view.k * Math.max(W, H) * 0.009, 22, 96) : 30;
  const pins = map.pins.map((p) => (drag && drag.place_id === p.place_id ? { ...p, x: drag.x, y: drag.y } : p))
    .filter((p) => byId.has(p.place_id));
  const extras = (p) => (childrenOf(places, p.id).length ? 22 : 0) + (ownMaps(p.id).length ? 19 : 0);
  const rings = view ? layoutRings(path, pins, places, screen, pages, extras, expanded) : [];

  useEffect(() => {
    if (hdAsked.current || !view || view.k * Math.max(W, H) <= PREVIEW_SIDE * 1.1) return;
    hdAsked.current = true;
    setHdLoading(true);
    const full = new Image();
    full.onload = () => { setSrc(map.url); setHdLoading(false); };
    full.onerror = () => setHdLoading(false);
    full.src = map.url;
  }, [view, W, H, map.url]);

  // Anneau ouvert hors de l'écran (ou sous la fiche du lieu) : la carte glisse pour le montrer. Sauf si un recentrage
  // vient d'être demandé avec ces anneaux (lieu choisi, arrivée par ?place=) : il montre déjà l'épingle, et ce
  // glissement, parti de l'ancienne vue, l'interromprait (la carte perdait le zoom du recentrage).
  const recentered = useRef(center);
  useEffect(() => {
    if (center !== recentered.current) { recentered.current = center; return; }
    if (!view || !size || !path.length || !rings.length) return;
    let x0 = Infinity; let x1 = -Infinity; let y0 = Infinity; let y1 = -Infinity;
    const add = (x, y, w, h) => { x0 = Math.min(x0, x - w / 2); x1 = Math.max(x1, x + w / 2); y0 = Math.min(y0, y - h / 2); y1 = Math.max(y1, y + h / 2); };
    add(rings[0].cx, rings[0].cy, 40, 60);
    for (const r of rings) for (const it of r.items) add(it.x, it.y, it.w, 34);
    const wide = panelBeside();
    const left = 16;
    const right = size.w - (wide ? 354 : 0) - 16;
    const top = trail.length > 1 ? 58 : 16;
    const bottom = size.h - (wide ? 16 : size.h * 0.5);
    const shift = (lo, hi, a, b) => (b - a > hi - lo ? (lo + hi) / 2 - (a + b) / 2 : a < lo ? lo - a : b > hi ? hi - b : 0);
    const dx = shift(left, right, x0, x1);
    const dy = shift(top, bottom, y0, y1);
    if (Math.abs(dx) > 1 || Math.abs(dy) > 1) animateTo(limit({ ...view, x: view.x + dx, y: view.y + dy }));
  }, [path, pages, size, expanded]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { recentered.current = center; }, [center]);   // recentrage seul : rien à sauter ensuite

  return (
    <div className={`map-view${armed != null ? ' is-armed' : ''}${dropping ? ' is-dropping' : ''}${editing ? ' is-editing' : ''}${labels ? '' : ' is-labels-hidden'}`}
      ref={ref} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
      onDragOver={(e) => { if (editing && e.dataTransfer.types.includes(DRAG_TYPE)) { e.preventDefault(); setDropping(true); } }}
      onDragLeave={() => setDropping(false)} onDrop={onDrop}
      role="application" aria-label={t('Carte {name}', { name: map.name })}>
      {view && (
        <img className="map-view__image" src={src} alt="" draggable={false} onLoad={() => setLoaded(true)}
          style={{ width: W, height: H, transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }} />
      )}
      {!loaded && <div className="map-view__loading">{t('Chargement de l’image…')}</div>}
      {hdLoading && <div className="map-hd" aria-live="polite">{t('Chargement de la pleine définition…')}</div>}
      {view && loaded && (
        <div className="map-view__overlay">
          <RingLines rings={rings} />
          {pins.map((p) => {
            const at = screen(p.x, p.y);
            const pl = byId.get(p.place_id);
            const kids = childrenOf(places, p.place_id).length;
            const open = path[0] === p.place_id;
            const kind = placeKind(pl.kind, kinds);
            return (
              <button key={p.place_id} type="button"
                className={`map-pin map-pin--${pinStyle}${open ? ' is-open' : ''}${drag?.place_id === p.place_id ? ' is-dragging' : ''}`}
                style={{ left: at.x, top: at.y, '--hit': `${hitSize}px` }}
                onPointerDown={(e) => pinDown(e, p)} onPointerMove={pinMove} onPointerUp={(e) => pinUp(e, p)}
                onClick={(e) => { e.stopPropagation(); if (e.detail === 0) togglePin(p.place_id); }}
                onDoubleClick={(e) => { e.stopPropagation(); const own = ownMaps(p.place_id); if (own.length) onEnter(own[0]); }}
                aria-expanded={kids ? open : undefined}
                title={[pl.name, kids > 0 && tn('{n} sous-lieu', '{n} sous-lieux', kids),
                  ownMaps(p.place_id).length > 0 && t('double-clic : entrer dans sa carte')].filter(Boolean).join(' · ')}>
                <span className="map-pin__dot">
                  {pinStyle === 'hidden' ? null
                    : pinStyle === 'icon' && kind.image ? <KindImage src={kind.image} size={24} />
                      : <PlaceIcon kind={pl.kind} size={15} />}
                </span>
                <span className="map-pin__label">{pl.name}</span>
              </button>
            );
          })}
          <RingChips rings={rings} path={path} expanded={expanded} places={places} byId={byId} ownMaps={ownMaps}
            onPath={onPath} onEnter={onEnter} onExpand={setExpanded}
            onPage={(placeId, page) => setPages((pg) => ({ ...pg, [placeId]: page }))} />
        </div>
      )}
      {trail.length > 1 && (
        <nav className="map-trail" aria-label={t('Cartes')} onPointerDown={(e) => e.stopPropagation()} onPointerUp={(e) => e.stopPropagation()}>
          {trail.map((m, i) => (i < trail.length - 1 ? (
            <span key={m.id}>
              <button type="button" onClick={() => onTrail(m, trail[i + 1].place_id)}>{m.name}</button>
              <Icon name="right" size={13} />
            </span>
          ) : <span key={m.id} aria-current="page">{m.name}</span>))}
        </nav>
      )}
      {armed != null && byId.has(armed) && (
        <div className="map-hint" onPointerDown={(e) => e.stopPropagation()} onPointerUp={(e) => e.stopPropagation()}>
          <Icon name="pin" size={15} /> {TOUCH ? t('Touchez la carte pour placer « {name} »', { name: byId.get(armed).name })
            : t('Cliquez sur la carte pour placer « {name} »', { name: byId.get(armed).name })}
          <button type="button" className="btn btn--small btn--ghost" onClick={onCancelArm}>{t('Annuler')}</button>
        </div>
      )}
      <MapZoom size={size} zoomAt={zoomAt} fit={fit} labels={labels} onToggleLabels={toggleLabels} pinStyle={pinStyle}
        onPinStyle={onPinStyle} />
    </div>
  );
}
