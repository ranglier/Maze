import { useRef, useState } from 'react';
import { api } from '../api.js';
import { Icon, useAction, useConfirm } from './ui.jsx';
import Lightbox from './Lightbox.jsx';
import ImagePicker from './ImagePicker.jsx';
import { TOUCH } from '../viewport.js';

export default function Gallery({ kind, entity, onChanged }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [viewer, setViewer] = useState(null);
  const [picking, setPicking] = useState(false);
  const run = useAction();
  const confirm = useConfirm();
  const coverField = { npc: 'portrait_image_id', faction: 'emblem_image_id', place: 'image_id' }[kind];
  const coverLabel = { npc: 'portrait', faction: 'emblème', place: 'illustration' }[kind];
  const coverArticle = { npc: 'le portrait', faction: 'l’emblème', place: 'l’illustration' }[kind];

  const upload = async (fileList) => {
    const files = [...fileList].filter((f) => f.type.startsWith('image/'));
    if (!files.length) return;
    setBusy(true);
    await run(() => api.uploadImages(kind, entity.id, files),
      files.length > 1 ? `${files.length} images ajoutées` : 'Image ajoutée');
    setBusy(false);
    onChanged();
  };

  const setCover = async (img) => {
    await run(() => api.updateEntity(kind, entity.id, { [coverField]: img.id }), kind === 'place' ? 'Nouvelle illustration définie' : `Nouvel ${coverLabel} défini`);
    onChanged();
  };

  const remove = async (img) => {
    const isCover = img.id === entity[coverField];
    const ok = await confirm({
      title: 'Supprimer l’image',
      message: isCover && entity.images.length > 1
        ? `L’image sera définitivement supprimée. Elle sert de ${coverLabel} : l’image suivante prendra sa place.`
        : 'L’image sera définitivement supprimée.',
    });
    if (!ok) return;
    await run(() => api.deleteImage(img.id), 'Image supprimée');
    setViewer(null);
    onChanged();
  };

  return (
    <section
      className={`gallery${dragging ? ' gallery--drop' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false); }}
      onDrop={(e) => { e.preventDefault(); setDragging(false); upload(e.dataTransfer.files); }}
    >
      <div className="panel-section__head">
        <h3>Images</h3>
        {entity.images.length > 0 && <span className="count">{entity.images.length}</span>}
        <button type="button" className="btn btn--small btn--ghost" onClick={() => setPicking(true)} disabled={busy}
          title="Réutiliser une image déjà dans la campagne">
          <Icon name="image" size={15} /> Choisir dans Maze
        </button>
        <button type="button" className="btn btn--small btn--ghost" onClick={() => inputRef.current.click()} disabled={busy}>
          <Icon name="upload" size={15} /> {busy ? 'Envoi…' : 'Ajouter'}
        </button>
        <input ref={inputRef} type="file" accept="image/*" multiple hidden
          onChange={(e) => { upload(e.target.files); e.target.value = ''; }} />
      </div>
      {entity.images.length === 0 ? (
        <button type="button" className="gallery__empty" onClick={() => inputRef.current.click()}>
          {TOUCH ? 'Touchez pour choisir des images.' : 'Glissez des images ici ou cliquez pour en choisir.'} La première sert de {coverLabel}.
        </button>
      ) : (
        <ul className="gallery__grid">
          {entity.images.map((img, index) => (
            <li key={img.id} className={img.id === entity[coverField] ? 'is-cover' : ''}>
              <button type="button" className="gallery__thumb" onClick={() => setViewer(index)}
                aria-label={img.caption || `Image ${index + 1}`}>
                <img src={img.thumb_url} alt="" loading="lazy" />
              </button>
              {img.id === entity[coverField] ? (
                <span className="gallery__badge" title={`Image utilisée comme ${coverLabel}`}>
                  <Icon name="star" size={13} />
                </span>
              ) : (
                <button type="button" className="gallery__action" onClick={() => setCover(img)}
                  title={`Utiliser comme ${coverLabel}`} aria-label={`Utiliser comme ${coverLabel}`}>
                  <Icon name="star" size={13} />
                </button>
              )}
              <button type="button" className="gallery__action gallery__action--delete" onClick={() => remove(img)}
                title="Supprimer l'image" aria-label={`Supprimer l'image ${index + 1}`}>
                <Icon name="trash" size={13} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {picking && (
        <ImagePicker campaignId={entity.campaign_id} coverLabel={coverArticle}
          title={`Ajouter une image de Maze à « ${entity.name} »`}
          onClose={() => setPicking(false)}
          onPick={async (data) => {
            const out = await run(() => api.copyImageToEntity(kind, entity.id, data), 'Image ajoutée');
            if (out) onChanged();
            return Boolean(out);
          }} />
      )}
      {viewer !== null && entity.images[viewer] && (
        <Lightbox
          images={entity.images}
          index={viewer}
          coverId={entity[coverField]}
          coverLabel={coverLabel}
          onIndex={setViewer}
          onClose={() => setViewer(null)}
          onSetCover={setCover}
          onDelete={remove}
          onCaption={async (img, caption) => {
            await run(() => api.updateImage(img.id, { caption }));
            onChanged();
          }}
        />
      )}
    </section>
  );
}
