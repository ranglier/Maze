import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Icon, useDialogLayer } from './ui.jsx';
import { t } from '../i18n/index.js';

/** `useAsCoverLabel` : libellé traduit du bouton « Utiliser comme portrait / emblème / illustration ». */

export default function Lightbox({
  images, index, coverId, useAsCoverLabel, onIndex, onClose, onSetCover, onDelete, onCaption,
}) {
  const img = images[index];
  const [caption, setCaption] = useState(img.caption);
  useEffect(() => setCaption(img.caption), [img.id, img.caption]);

  // Clavier ignoré quand une fenêtre (confirmation de suppression…) est ouverte par-dessus.
  useDialogLayer((e) => {
    if (e.target.tagName === 'INPUT') return;
    if (e.key === 'Escape') { e.stopImmediatePropagation(); onClose(); }
    if (e.key === 'ArrowRight') onIndex((index + 1) % images.length);
    if (e.key === 'ArrowLeft') onIndex((index - 1 + images.length) % images.length);
  });

  return createPortal(
    <div className="lightbox" role="dialog" aria-modal="true" aria-label={t('Visionneuse d’images')}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <button type="button" className="icon-btn lightbox__close" onClick={onClose} aria-label={t('Fermer')}>
        <Icon name="close" size={22} />
      </button>
      {images.length > 1 && (
        <>
          <button type="button" className="icon-btn lightbox__nav lightbox__nav--prev" aria-label={t('Image précédente')}
            onClick={() => onIndex((index - 1 + images.length) % images.length)}>
            <Icon name="left" size={26} />
          </button>
          <button type="button" className="icon-btn lightbox__nav lightbox__nav--next" aria-label={t('Image suivante')}
            onClick={() => onIndex((index + 1) % images.length)}>
            <Icon name="right" size={26} />
          </button>
        </>
      )}
      <figure className="lightbox__figure">
        <img src={img.url} alt={img.caption || ''} />
        <figcaption>
          <input
            className="lightbox__caption"
            value={caption}
            placeholder={t('Ajouter une légende')}
            onChange={(e) => setCaption(e.target.value)}
            onBlur={() => { if (caption !== img.caption) onCaption(img, caption); }}
            onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
          />
          <div className="lightbox__tools">
            <span className="lightbox__count">{index + 1} / {images.length}</span>
            {img.id !== coverId && (
              <button type="button" className="btn btn--small btn--ghost" onClick={() => onSetCover(img)}>
                <Icon name="star" size={14} /> {useAsCoverLabel}
              </button>
            )}
            <button type="button" className="btn btn--small btn--ghost btn--danger-text" onClick={() => onDelete(img)}>
              <Icon name="trash" size={14} /> {t('Supprimer')}
            </button>
          </div>
        </figcaption>
      </figure>
    </div>,
    document.body,
  );
}
