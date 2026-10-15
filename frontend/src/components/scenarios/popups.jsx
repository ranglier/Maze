import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '../ui.jsx';
import { useSheet } from '../../viewport.js';

/** Menu contextuel simple, ancré sous un bouton. */
export function PopupMenu({ rect, align, items, onClose, anchorSelector }) {
  const ref = useRef(null);
  useSheet();
  useEffect(() => {
    // Le bouton d'ouverture gère lui-même la fermeture (sinon le clic rouvrirait le menu).
    const onDown = (e) => {
      if (!ref.current?.contains(e.target) && !(anchorSelector && e.target.closest?.(anchorSelector))) onClose();
    };
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    ref.current?.querySelector('button')?.focus();
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [onClose, anchorSelector]);
  const width = 250;
  const left = align === 'right' ? Math.max(8, rect.right - width) : Math.min(rect.left, window.innerWidth - width - 8);
  return createPortal(
    <div className="popover popover--menu" ref={ref} role="menu"
      style={{ position: 'fixed', top: rect.bottom + 4, left, right: 'auto', width }}>
      <div className="popover__group popover__group--actions">
        {items.filter(Boolean).map((it) => (
          <button key={it.key} type="button" role="menuitem" onClick={() => { onClose(); it.onClick(); }}>
            <Icon name={it.icon} size={15} /> {it.label}
            {it.hint && <span className="popover__hint">{it.hint}</span>}
          </button>
        ))}
      </div>
    </div>,
    document.body,
  );
}

// --- Aperçu d'une mention ----------------------------------------------------------------

export function MentionPreview({ target, entity, onClose, onOpen }) {
  const ref = useRef(null);
  useSheet();
  useEffect(() => {
    const onDown = (e) => { if (!ref.current?.contains(e.target)) onClose(); };
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [onClose]);
  const top = Math.min(target.rect.bottom + 8, window.innerHeight - 220);
  const left = Math.min(Math.max(12, target.rect.left), window.innerWidth - 332);
  return createPortal(
    <div className="mention-card" ref={ref} role="dialog" aria-label="Aperçu de la fiche" style={{ top, left }}>
      {!entity ? (
        <p className="muted">Cette fiche a été supprimée.</p>
      ) : (
        <>
          <div className="mention-card__head" style={{ '--ring': entity.color }}>
            {entity.image
              ? <img src={entity.image} alt="" className={entity.kind === 'faction' ? 'is-faction' : ''} />
              : <span className="mention-card__seal" style={{ background: entity.color }} />}
            <div>
              <strong>{entity.status === 'mort' ? `† ${entity.name}` : entity.name}</strong>
              <span>{entity.sub}</span>
            </div>
          </div>
          {entity.summary && <p className="mention-card__summary">{entity.summary}</p>}
          <button type="button" className="btn btn--small btn--ghost" onClick={onOpen}>
            {entity.kind === 'place'
              ? <><Icon name="pin" size={14} /> Ouvrir le lieu</>
              : <><Icon name="person" size={14} /> Ouvrir la fiche</>}
          </button>
        </>
      )}
    </div>,
    document.body,
  );
}
