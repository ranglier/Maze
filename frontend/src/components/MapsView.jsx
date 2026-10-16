import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';
import { pt, t } from '../i18n/index.js';
import { KindsContext, PlacesTabs } from './PlacesView.jsx';
import { Icon, useAction, useConfirm } from './ui.jsx';
import { isPhone } from '../viewport.js';
import { closeDrawer } from './maps/constants.js';
import { focusPath, mapTrail } from './maps/mapTree.js';
import { useMapPins, useMapsData } from './maps/useMapsData.js';
import MapCanvas from './maps/MapCanvas.jsx';
import PlacePanel from './maps/PlacePanel.jsx';
import PinList from './maps/PinList.jsx';
import MapDialog from './maps/MapDialog.jsx';

/*
 * Lieux > Carte interactive. Une image de fond (pleine définition jusqu'à 8192 px, affichée d'abord dans sa
 * version de 4096 px) sur laquelle les lieux sont épinglés.
 * Un clic sur une épingle ouvre un anneau avec ses sous-lieux ; un clic sur un sous-lieu ouvre un second
 * anneau avec les siens, et ainsi de suite. Une carte peut représenter un lieu : « Entrer » ouvre la carte
 * de ce lieu ; le fil d'Ariane remonte vers les cartes plus larges. La fiche du lieu choisi s'affiche à côté.
 * Mode « Modifier » : glisser les lieux de la liste sur la carte (ou les choisir puis cliquer), déplacer
 * les épingles, les retirer.
 * Découpage (audit QUAL-10) : maps/MapCanvas.jsx (carte, épingles, anneaux ; vue : useMapViewport, anneaux :
 * rings.js et MapRings.jsx, commandes : MapZoom.jsx), maps/PlacePanel.jsx (fiche du lieu), maps/PinList.jsx (lieux
 * à placer), maps/MapDialog.jsx (création et réglages), maps/useMapsData.js (données et épingles).
 */

export default function MapsView({ campaign, header, trailing, mapId, focusPlace, onNavigate, onFocusDone }) {
  const cid = campaign.id;
  const run = useAction();
  const confirm = useConfirm();
  const { maps, setMaps, tree, loadMaps, places, byId, currentId } = useMapsData({ cid, run, mapId, focusPlace });
  const [detail, setDetail] = useState(null);     // carte affichée, avec ses épingles
  const [path, setPath] = useState([]);           // anneaux ouverts : [lieu épinglé, sous-lieu, …]
  const [editing, setEditing] = useState(false);
  const [armed, setArmed] = useState(null);       // lieu à poser au prochain clic sur la carte
  const [dialog, setDialog] = useState(null);     // 'new' | 'settings'
  const [center, setCenter] = useState(null);     // demande de recentrage { x, y, seq }

  useEffect(() => {
    setPath([]);
    setArmed(null);
    if (!currentId) { setDetail(null); return undefined; }
    let live = true;
    (async () => {
      const d = await run(() => api.getMap(currentId));
      if (live && d) setDetail(d);
    })();
    return () => { live = false; };
  }, [currentId, run]);

  // Arrivée depuis une fiche (?place=) : anneaux ouverts jusqu'au lieu, carte centrée sur son épingle.
  useEffect(() => {
    if (!focusPlace || !tree || !detail || detail.id !== currentId) return;
    const found = focusPath(detail.pins, byId, focusPlace);
    if (found) {
      setPath(found);
      const pin = detail.pins.find((p) => p.place_id === found[0]);
      setCenter({ x: pin.x, y: pin.y, seq: Date.now() });
    }
    onFocusDone(currentId);
  }, [focusPlace, tree, detail, currentId, byId, onFocusDone]);

  const go = (id, placeId) => onNavigate(`#/c/${cid}/cartes/${id}${placeId ? `?place=${placeId}` : ''}`);
  const current = maps?.find((m) => m.id === currentId) || null;
  const trail = useMemo(() => (current && tree ? mapTrail(maps, byId, current) : []), [maps, byId, current, tree]);
  const ownMaps = useCallback((placeId) => (maps || []).filter((m) => m.place_id === placeId && m.id !== currentId),
    [maps, currentId]);

  const { savePin, removePin, reloadDetail } = useMapPins({ currentId, run, setDetail, setMaps, setPath, loadMaps });

  const place = (placeId, x, y) => {
    setArmed(null);
    savePin(placeId, x, y);
  };

  /** Choix d'un lieu (liste, panneau) : anneaux ouverts jusqu'à lui s'il est sur la carte. */
  const reveal = (placeId) => {
    const found = detail && focusPath(detail.pins, byId, placeId);
    if (found) {
      setPath(found);
      const pin = detail.pins.find((p) => p.place_id === found[0]);
      setCenter({ x: pin.x, y: pin.y, seq: Date.now() });
      return true;
    }
    return false;
  };

  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape' || e.target.closest?.('input, textarea, select, .modal')) return;
      if (armed) setArmed(null);
      else setPath((p) => p.slice(0, -1));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [armed]);

  const removeMap = async () => {
    const yes = await confirm({
      title: t('Supprimer la carte « {name} »', { name: current.name }),
      message: t('L’image de fond et les épingles seront supprimées. Les lieux eux-mêmes ne sont pas touchés.'),
      confirmLabel: t('Supprimer'),
    });
    if (!yes) return;
    if (await run(() => api.deleteMap(current.id), t('Carte supprimée')) === undefined) return;
    setDialog(null);
    setEditing(false);
    await loadMaps();
    onNavigate(`#/c/${cid}/cartes`);
  };

  const selected = path[path.length - 1] ?? null;
  const pinned = useMemo(() => new Set((detail?.pins || []).map((p) => p.place_id)), [detail]);

  return (
    <KindsContext.Provider value={tree?.kinds || null}>
      <div className="scenarios places maps">
        <header className="topbar">
          {header}
          <PlacesTabs campaignId={cid} active="cartes" />
          <div className="topbar__right">
          <div className="toolbar">
            {maps?.length > 0 && (
              <select className="input maps__select" value={currentId || ''} aria-label={t('Carte affichée')}
                onChange={(e) => go(Number(e.target.value))}>
                {maps.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            )}
            {current && (
              <button type="button" className={`btn btn--ghost${editing ? ' is-active' : ''}`} aria-pressed={editing}
                onClick={() => {
                  // Téléphone : la liste des lieux à placer s'ouvre en tiroir.
                  if (!editing && isPhone()) window.dispatchEvent(new CustomEvent('maze:drawer', { detail: { open: true } }));
                  if (editing) closeDrawer();
                  setEditing((v) => !v); setArmed(null);
                }}
                title={editing ? t('Terminer la mise en place des lieux') : t('Placer, déplacer ou retirer des lieux sur la carte')}>
                <Icon name={editing ? 'check' : 'edit'} size={16} /><span className="btn__label">{editing ? t('Terminer') : t('Placer les lieux')}</span>
              </button>
            )}
            {current && (
              <button type="button" className="btn btn--ghost" onClick={() => setDialog('settings')}
                title={t('Réglages de la carte : nom, lieu représenté, aspect des épingles, image de fond')}>
                <Icon name="image" size={16} /><span className="btn__label">{t('Réglages')}</span>
              </button>
            )}
            <button type="button" className="btn btn--ghost" onClick={() => setDialog('new')} disabled={!tree}>
              <Icon name="plus" size={16} /><span className="btn__label">{pt('maps', 'Carte')}</span>
            </button>
            <span className="toolbar__sep" />
          </div>
          {trailing && <div className="topbar__end">{trailing}</div>}
          </div>
        </header>
        <div className={`maps__body${editing ? ' is-editing' : ''}`}>
          {editing && tree && detail && (
            <PinList places={places} pinned={pinned} represented={detail.place_id} armed={armed} selected={selected}
              onArm={(id) => { setArmed((a) => (a === id ? null : id)); if (armed !== id) closeDrawer(); }}
              onReveal={(id) => { closeDrawer(); reveal(id); }} campaignId={cid} />
          )}
          <main className="maps__main">
            {!maps || !tree ? <div className="doc-loading" aria-busy="true">{t('Chargement…')}</div>
              : maps.length === 0 ? (
                <div className="maps__empty">
                  <Icon name="map" size={34} />
                  <h2>{t('Aucune carte pour l’instant')}</h2>
                  <p>{t('Importez une image de fond (carte d’un secteur, d’une région, d’une ville) puis placez-y vos lieux. Choisir un lieu montre ses sous-lieux en anneau ; une carte peut aussi représenter un lieu, pour y « entrer ».')}</p>
                  <button type="button" className="btn btn--primary" onClick={() => setDialog('new')}>
                    <Icon name="plus" size={16} /> {t('Créer une carte')}
                  </button>
                </div>
              ) : !detail || detail.id !== currentId ? <div className="doc-loading" aria-busy="true">{t('Chargement de la carte…')}</div> : (
                <MapCanvas key={`${detail.id}-${detail.url}`} map={detail} byId={byId} places={places} path={path} editing={editing}
                  armed={armed} center={center} ownMaps={ownMaps} trail={trail}
                  onPath={setPath} onMovePin={savePin} onPlace={place} onCancelArm={() => setArmed(null)}
                  onPinStyle={async (style) => {
                    setDetail((d) => d && ({ ...d, pin_style: style }));
                    if (await run(() => api.updateMap(detail.id, { pin_style: style })) === undefined) reloadDetail();
                    else loadMaps();
                  }}
                  onEnter={(m) => go(m.id)} onTrail={(m, via) => go(m.id, via)} />
              )}
            {detail && detail.id === currentId && selected != null && byId.has(selected) && (
              <PlacePanel key={selected} placeId={selected} campaignId={cid} byId={byId} places={places} path={path}
                pinned={pinned.has(selected)} editing={editing} ownMaps={ownMaps(selected)}
                onPath={setPath} onEnter={(m) => go(m.id)} onClose={() => setPath([])}
                onArm={() => setArmed(selected)} onRemovePin={() => removePin(selected)} />
            )}
          </main>
        </div>
        {dialog === 'new' && tree && (
          <MapDialog campaignId={cid} places={places} onClose={() => setDialog(null)}
            onCreated={async (m) => { setDialog(null); await loadMaps(); go(m.id); setEditing(true); }} />
        )}
        {dialog === 'settings' && current && tree && (
          <MapDialog campaignId={cid} places={places} map={current} onClose={() => setDialog(null)} onDelete={removeMap}
            onSaved={async () => { await loadMaps(); await reloadDetail(); }} />
        )}
      </div>
    </KindsContext.Provider>
  );
}
