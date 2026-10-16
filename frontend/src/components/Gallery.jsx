import { useRef, useState } from 'react';
import { api } from '../api.js';
import { Icon, useAction, useConfirm } from './ui.jsx';
import Lightbox from './Lightbox.jsx';
import ImagePicker from './ImagePicker.jsx';
import { TOUCH } from '../viewport.js';
import { N_, t } from '../i18n/index.js';

// Image de couverture (portrait, emblème ou illustration) : phrases entières par sorte de fiche, traduites à l'affichage.
const COVER_TEXT = {
  npc: {
    set: N_('Nouveau portrait défini'),
    first: N_('La première sert de portrait.'),
    used: N_('Image utilisée comme portrait'),
    use: N_('Utiliser comme portrait'),
    replaced: N_('L’image sera définitivement supprimée. Elle sert de portrait : l’image suivante prendra sa place.'),
    pick: N_('En faire le portrait'),
  },
  faction: {
    set: N_('Nouvel emblème défini'),
    first: N_('La première sert d’emblème.'),
    used: N_('Image utilisée comme emblème'),
    use: N_('Utiliser comme emblème'),
    replaced: N_('L’image sera définitivement supprimée. Elle sert d’emblème : l’image suivante prendra sa place.'),
    pick: N_('En faire l’emblème'),
  },
  place: {
    set: N_('Nouvelle illustration définie'),
    first: N_('La première sert d’illustration.'),
    used: N_('Image utilisée comme illustration'),
    use: N_('Utiliser comme illustration'),
    replaced: N_('L’image sera définitivement supprimée. Elle sert d’illustration : l’image suivante prendra sa place.'),
    pick: N_('En faire l’illustration'),
  },
};

export default function Gallery({ kind, entity, onChanged }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [viewer, setViewer] = useState(null);
  const [picking, setPicking] = useState(false);
  const run = useAction();
  const confirm = useConfirm();
  const coverField = { npc: 'portrait_image_id', faction: 'emblem_image_id', place: 'image_id' }[kind];
  const cover = COVER_TEXT[kind];

  const upload = async (fileList) => {
    const files = [...fileList].filter((f) => f.type.startsWith('image/'));
    if (!files.length) return;
    setBusy(true);
    await run(() => api.uploadImages(kind, entity.id, files),
      files.length > 1 ? t('{n} images ajoutées', { n: files.length }) : t('Image ajoutée'));
    setBusy(false);
    onChanged();
  };

  const setCover = async (img) => {
    await run(() => api.updateEntity(kind, entity.id, { [coverField]: img.id }), t(cover.set));
    onChanged();
  };

  const remove = async (img) => {
    const isCover = img.id === entity[coverField];
    const ok = await confirm({
      title: t('Supprimer l’image'),
      message: isCover && entity.images.length > 1 ? t(cover.replaced) : t('L’image sera définitivement supprimée.'),
    });
    if (!ok) return;
    await run(() => api.deleteImage(img.id), t('Image supprimée'));
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
        <h3>{t('Images')}</h3>
        {entity.images.length > 0 && <span className="count">{entity.images.length}</span>}
        <button type="button" className="btn btn--small btn--ghost" onClick={() => setPicking(true)} disabled={busy}
          title={t('Réutiliser une image déjà dans la campagne')}>
          <Icon name="image" size={15} /> {t('Choisir dans Maze')}
        </button>
        <button type="button" className="btn btn--small btn--ghost" onClick={() => inputRef.current.click()} disabled={busy}>
          <Icon name="upload" size={15} /> {busy ? t('Envoi…') : t('Ajouter')}
        </button>
        <input ref={inputRef} type="file" accept="image/*" multiple hidden
          onChange={(e) => { upload(e.target.files); e.target.value = ''; }} />
      </div>
      {entity.images.length === 0 ? (
        <button type="button" className="gallery__empty" onClick={() => inputRef.current.click()}>
          {TOUCH ? t('Touchez pour choisir des images.') : t('Glissez des images ici ou cliquez pour en choisir.')} {t(cover.first)}
        </button>
      ) : (
        <ul className="gallery__grid">
          {entity.images.map((img, index) => (
            <li key={img.id} className={img.id === entity[coverField] ? 'is-cover' : ''}>
              <button type="button" className="gallery__thumb" onClick={() => setViewer(index)}
                aria-label={img.caption || t('Image {n}', { n: index + 1 })}>
                <img src={img.thumb_url} alt="" loading="lazy" />
              </button>
              {img.id === entity[coverField] ? (
                <span className="gallery__badge" title={t(cover.used)}>
                  <Icon name="star" size={13} />
                </span>
              ) : (
                <button type="button" className="gallery__action" onClick={() => setCover(img)}
                  title={t(cover.use)} aria-label={t(cover.use)}>
                  <Icon name="star" size={13} />
                </button>
              )}
              <button type="button" className="gallery__action gallery__action--delete" onClick={() => remove(img)}
                title={t('Supprimer l’image')} aria-label={t('Supprimer l’image {n}', { n: index + 1 })}>
                <Icon name="trash" size={13} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {picking && (
        <ImagePicker campaignId={entity.campaign_id} coverLabel={t(cover.pick)}
          title={t('Ajouter une image de Maze à « {name} »', { name: entity.name })}
          onClose={() => setPicking(false)}
          onPick={async (data) => {
            const out = await run(() => api.copyImageToEntity(kind, entity.id, data), t('Image ajoutée'));
            if (out) onChanged();
            return Boolean(out);
          }} />
      )}
      {viewer !== null && entity.images[viewer] && (
        <Lightbox
          images={entity.images}
          index={viewer}
          coverId={entity[coverField]}
          useAsCoverLabel={t(cover.use)}
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
