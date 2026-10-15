import { useRef, useState } from 'react';
import { api } from '../../api.js';
import ImagePicker from '../ImagePicker.jsx';
import { Field, Icon, Modal, useAction } from '../ui.jsx';
import { childrenOf } from './mapTree.js';

// --- Création et réglages d'une carte --------------------------------------------------------------

function PlaceOptions({ places }) {
  const out = [];
  const walk = (parentId, depth) => {
    for (const p of childrenOf(places, parentId)) {
      out.push(<option key={p.id} value={p.id}>{`${'  '.repeat(depth)}${p.name}`}</option>);
      walk(p.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

export default function MapDialog({ campaignId, places, map, onClose, onCreated, onSaved, onDelete }) {
  const [name, setName] = useState(map?.name || '');
  const [pinStyle, setPinStyle] = useState(map?.pin_style || 'dot');
  const [placeId, setPlaceId] = useState(map?.place_id ? String(map.place_id) : '');
  const [progress, setProgress] = useState(null);
  const [picking, setPicking] = useState(false);
  const fileRef = useRef(null);
  const run = useAction();
  const busy = progress !== null;
  const pid = placeId ? Number(placeId) : null;

  const upload = async (file) => {
    if (!file) return;
    setProgress(0);
    const out = await run(() => (map ? api.replaceMapImage(map.id, file, setProgress)
      : api.createMap(campaignId, { file, name: name.trim(), placeId: pid }, setProgress)), map ? 'Image de fond remplacée' : 'Carte créée');
    setProgress(null);
    if (!out) return;
    if (map) await onSaved();
    else onCreated(out);
  };
  const pick = async ({ source, id }) => {
    const out = await run(() => (map ? api.copyMapImage(map.id, { source, id })
      : api.copyMap(campaignId, { source, id, name: name.trim() || null, place_id: pid })), map ? 'Image de fond remplacée' : 'Carte créée');
    if (!out) return false;
    if (map) await onSaved();
    else onCreated(out);
    return true;
  };
  const save = async () => {
    const out = await run(() => api.updateMap(map.id, { name: name.trim() || map.name, place_id: pid, pin_style: pinStyle }),
      'Carte enregistrée');
    if (out) { await onSaved(); onClose(); }
  };

  return (
    <>
      <Modal title={map ? 'Réglages de la carte' : 'Nouvelle carte'} onClose={onClose}
        footer={map ? (
          <>
            <button type="button" className="btn btn--ghost btn--danger-text" onClick={onDelete} disabled={busy}>
              <Icon name="trash" size={16} /> Supprimer la carte
            </button>
            <span className="modal__foot-spacer" />
            <button type="button" className="btn btn--ghost" onClick={onClose}>Fermer</button>
            <button type="button" className="btn btn--primary" onClick={save} disabled={busy}>Enregistrer</button>
          </>
        ) : null}>
        <div className="map-dialog">
          <Field label="Nom" htmlFor="map-name" hint={map ? null : 'Facultatif : par défaut, « Carte de » suivi du lieu représenté, ou le nom du fichier.'}>
            <input id="map-name" className="input" value={name} maxLength={120} onChange={(e) => setName(e.target.value)}
              placeholder="Secteur Morvane" />
          </Field>
          <Field label="Cette carte représente" htmlFor="map-place"
            hint="Choisir un lieu permet d’« entrer » dans cette carte depuis une carte plus large où ce lieu (ou l’un de ses parents) est épinglé.">
            <select id="map-place" className="input" value={placeId} onChange={(e) => setPlaceId(e.target.value)}>
              <option value="">Aucun lieu (carte générale)</option>
              <PlaceOptions places={places} />
            </select>
          </Field>
          {map && (
            <Field label="Style des épingles" htmlFor="map-pins"
              hint="Discrète : pour une carte qui porte déjà ses symboles, une zone cliquable invisible se pose dessus (cercle au survol). Icône du type : l’image importée dans Types de lieux, en grand.">
              <select id="map-pins" className="input" value={pinStyle} onChange={(e) => setPinStyle(e.target.value)}>
                <option value="dot">Pastille</option>
                <option value="icon">Icône du type de lieu</option>
                <option value="hidden">Discrète (sur le symbole de la carte)</option>
              </select>
            </Field>
          )}
          <div className="field">
            <span className="field__label">{map ? 'Remplacer l’image de fond' : 'Image de fond'}</span>
            {map && <p className="field__hint">Les épingles restent en place (positions relatives à l’image).</p>}
            <div className="map-dialog__sources">
              <button type="button" className="btn btn--primary" onClick={() => fileRef.current?.click()} disabled={busy}>
                <Icon name="upload" size={16} /> {busy ? `Envoi… ${Math.round(progress * 100)} %` : 'Importer une image…'}
              </button>
              <button type="button" className="btn btn--ghost" onClick={() => setPicking(true)} disabled={busy}>
                <Icon name="image" size={16} /> Choisir dans Maze…
              </button>
            </div>
            <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp,image/gif" hidden
              onChange={(e) => { const f = e.target.files[0]; e.target.value = ''; upload(f); }} />
            <p className="field__hint">Image importée gardée en pleine définition jusqu’à 8192 px (20 Mo au plus ; un grand JPEG est le plus
              économe), affichée d’abord en 4096 px. Les images déjà dans Maze sont limitées à 1600 px.</p>
          </div>
        </div>
      </Modal>
      {picking && (
        <ImagePicker campaignId={campaignId} title="Image de fond de la carte" onPick={pick} onClose={() => setPicking(false)} />
      )}
    </>
  );
}
