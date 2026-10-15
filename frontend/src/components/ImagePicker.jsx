import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import { Icon, Modal, useAction } from './ui.jsx';

const FILTERS = [
  { key: 'all', label: 'Toutes' },
  { key: 'library', label: 'Bibliothèque' },
  { key: 'gallery', label: 'Fiches' },
  { key: 'document', label: 'Scénarios' },
  { key: 'map', label: 'Cartes' },
];

/**
 * Choix d'une image déjà dans la campagne (bibliothèque, galeries, illustrations des fiches rédigées).
 * onPick({ source, id, set_cover }) fait la copie ; renvoie une valeur vraie si elle a réussi.
 * coverLabel : propose « en faire le portrait / l'emblème / l'illustration » (galerie d'une fiche).
 * onUpload(files) : propose aussi l'envoi depuis l'ordinateur (éditeur des fiches rédigées).
 */
export default function ImagePicker({ campaignId, title = 'Choisir une image de Maze', coverLabel, onPick, onUpload, onClose }) {
  const [images, setImages] = useState(null);
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(null);
  const [asCover, setAsCover] = useState(false);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef(null);
  const run = useAction();

  useEffect(() => {
    (async () => {
      const out = await run(() => api.campaignImages(campaignId));
      setImages(out ? out.images : []);
    })();
  }, [campaignId, run]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (images || []).filter((img) => (filter === 'all' || img.source === filter)
      && (!q || `${img.caption} ${img.origin}`.toLowerCase().includes(q)));
  }, [images, filter, query]);

  const choice = images?.find((i) => `${i.source}-${i.id}` === selected);

  const pick = async () => {
    if (!choice) return;
    setBusy(true);
    const ok = await onPick({ source: choice.source, id: choice.id, set_cover: Boolean(coverLabel && asCover) });
    setBusy(false);
    if (ok) onClose();
  };

  return (
    <Modal title={title} onClose={onClose} wide
      footer={(
        <>
          {onUpload && (
            <>
              <button type="button" className="btn btn--ghost image-picker__upload" onClick={() => fileRef.current?.click()} disabled={busy}>
                <Icon name="upload" size={16} /> Depuis l’ordinateur…
              </button>
              <input ref={fileRef} type="file" accept="image/*" hidden
                onChange={async (e) => {
                  const files = [...e.target.files];
                  e.target.value = '';
                  if (!files.length) return;
                  setBusy(true);
                  const ok = await onUpload(files);
                  setBusy(false);
                  if (ok) onClose();
                }} />
            </>
          )}
          <button type="button" className="btn btn--primary" onClick={pick} disabled={!choice || busy}>
            <Icon name="check" size={16} /> {busy ? 'Ajout…' : 'Utiliser cette image'}
          </button>
        </>
      )}>
      <div className="image-picker">
        <div className="image-picker__bar">
          <div className="segmented segmented--compact" role="tablist" aria-label="Provenance">
            {FILTERS.map((f) => (
              <button key={f.key} type="button" role="tab" aria-selected={filter === f.key}
                className={filter === f.key ? 'is-active' : ''} onClick={() => setFilter(f.key)}>{f.label}</button>
            ))}
          </div>
          <input type="search" placeholder="Rechercher…" value={query} onChange={(e) => setQuery(e.target.value)}
            aria-label="Rechercher une image" />
        </div>
        {!images ? <p className="muted">Chargement…</p> : shown.length === 0 ? (
          <p className="muted image-picker__empty">
            {images.length === 0
              ? 'Aucune image dans cette campagne. Un administrateur peut en déposer dans la bibliothèque (Administration > Images).'
              : 'Aucune image ne correspond.'}
          </p>
        ) : (
          <ul className="image-picker__grid" role="listbox" aria-label="Images de la campagne">
            {shown.map((img) => {
              const key = `${img.source}-${img.id}`;
              return (
                <li key={key} role="option" aria-selected={selected === key}>
                  <button type="button" className={`image-picker__item${selected === key ? ' is-selected' : ''}`}
                    onClick={() => setSelected(key)} onDoubleClick={() => { setSelected(key); }}
                    title={[img.caption, img.origin].filter(Boolean).join(' — ')}>
                    <img src={img.thumb_url} alt="" loading="lazy" />
                    <span className="image-picker__label">{img.caption || img.origin}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        {choice && (
          <div className="image-picker__choice">
            <img src={choice.url} alt="" />
            <div>
              <strong>{choice.caption || 'Sans légende'}</strong>
              <span className="muted">{choice.origin} · {choice.width}×{choice.height}</span>
              {coverLabel && (
                <label className="check"><input type="checkbox" checked={asCover} onChange={(e) => setAsCover(e.target.checked)} />
                  En faire {coverLabel}</label>
              )}
              <span className="muted">Une copie est ajoutée : l’image d’origine ne change pas.</span>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
