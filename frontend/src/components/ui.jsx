import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { isRichHtml, renderMarkdown } from '../markdown.js';
import { renderDocInto } from '../editor/schema.js';
import { t } from '../i18n/index.js';

// --- Icônes (traits 1.75, 20 px) --------------------------------------------
const PATHS = {
  plus: 'M10 4v12M4 10h12',
  code: 'M7 6l-4 4 4 4M13 6l4 4-4 4M11 4.5l-2 11',
  link: 'M8.5 11.5l3-3M7 13.5l-1.2 1.2a2.5 2.5 0 01-3.5-3.5L5.5 8a2.5 2.5 0 013.5 0M13 6.5l1.2-1.2a2.5 2.5 0 013.5 3.5L14.5 12a2.5 2.5 0 01-3.5 0',
  layout: 'M5 5h3v3H5zM12 4h3v3h-3zM12 13h3v3h-3zM5 12h3v3H5zM8 6.5h4M13.5 7v6M8 13.5h4',
  fit: 'M3 7V3h4M13 3h4v4M17 13v4h-4M7 17H3v-4',
  filter: 'M3 5h14M6 10h8M8.5 15h3',
  search: 'M9 15A6 6 0 109 3a6 6 0 000 12zM13.5 13.5L17 17',
  close: 'M5 5l10 10M15 5L5 15',
  edit: 'M12.5 4.5l3 3L8 15H5v-3l7.5-7.5z',
  trash: 'M4 6h12M8 6V4h4v2M6 6l1 10h6l1-10',
  lock: 'M6 9V7a4 4 0 018 0v2M5 9h10v8H5z',
  image: 'M3 4h14v12H3zM3 13l4-4 3 3 2-2 5 5M13 8a1 1 0 100-2 1 1 0 000 2z',
  download: 'M10 3v10M6 9l4 4 4-4M4 16h12',
  star: 'M10 3l2.1 4.4 4.9.6-3.6 3.3 1 4.8L10 13.8 5.6 16l1-4.8L3 8l4.9-.6z',
  target: 'M10 17a7 7 0 100-14 7 7 0 000 14zM10 13a3 3 0 100-6 3 3 0 000 6zM10 1v3M10 16v3M1 10h3M16 10h3',
  left: 'M12.5 4l-6 6 6 6',
  right: 'M7.5 4l6 6-6 6',
  upload: 'M10 13V3M6 7l4-4 4 4M4 16h12',
  logout: 'M8 4H4v12h4M12 6l4 4-4 4M16 10H8',
  swap: 'M4 7h11l-3-3M16 13H5l3 3',
  person: 'M10 10a3.5 3.5 0 100-7 3.5 3.5 0 000 7zM3.5 17a6.5 6.5 0 0113 0',
  banner: 'M10 2.5l6.5 3.75v7.5L10 17.5l-6.5-3.75v-7.5z',
  chevron: 'M5 8l5 5 5-5',
  users: 'M7.5 9.5a3 3 0 100-6 3 3 0 000 6zM2 16.5a5.5 5.5 0 0111 0M13 4a3 3 0 010 5.5M15 16.5a5 5 0 00-2.5-4.3',
  archive: 'M3 4h14v3H3zM4.5 7v9h11V7M8 10.5h4',
  refresh: 'M16 10a6 6 0 11-1.8-4.3M16 3.5v3.2h-3.2',
  back: 'M8.5 5l-5 5 5 5M3.5 10H16',
  campaign: 'M4 3.5h9l3 3V16.5H4zM13 3.5V6.5h3M7 10h6M7 13h4',
  heading: 'M5 4v12M15 4v12M5 10h10',
  list: 'M8 5.5h9M8 10h9M8 14.5h9M3.8 5.5h.4M3.8 10h.4M3.8 14.5h.4',
  olist: 'M8 5.5h9M8 10h9M8 14.5h9M3 4.5l1-.5v3.5M3 9.5h2l-2 2.5h2M3 14h2v3H3M3 15.5h1.5',
  quote: 'M4 6h5v4.5c0 2-1.2 3.3-3 3.8M11 6h5v4.5c0 2-1.2 3.3-3 3.8',
  scroll: 'M6 3.5h9.5v11a2 2 0 01-2 2H5M6 3.5a1.5 1.5 0 00-1.5 1.5v1.5H6M6 3.5v12a1 1 0 01-1 1M9 7.5h4M9 10.5h4',
  minus: 'M3.5 10h13',
  fold: 'M6 3.5l4 4 4-4M6 16.5l4-4 4 4',
  alignLeft: 'M3.5 5h13M3.5 8.3h8M3.5 11.7h13M3.5 15h8',
  alignCenter: 'M3.5 5h13M6 8.3h8M3.5 11.7h13M6 15h8',
  alignRight: 'M3.5 5h13M8.5 8.3h8M3.5 11.7h13M8.5 15h8',
  alignJustify: 'M3.5 5h13M3.5 8.3h13M3.5 11.7h13M3.5 15h13',
  undo: 'M7 5L3.5 8.5 7 12M4 8.5h7.5a4.5 4.5 0 010 9H9',
  redo: 'M13 5l3.5 3.5L13 12M16 8.5H8.5a4.5 4.5 0 000 9H11',
  book: 'M10 5.5C8.5 4.3 6.5 4 3.5 4v11c3 0 5 .3 6.5 1.5M10 5.5c1.5-1.2 3.5-1.5 6.5-1.5v11c-3 0-5 .3-6.5 1.5M10 5.5v11',
  page: 'M5 3h7l3 3v11H5zM12 3v3h3',
  more: 'M5 10h.01M10 10h.01M15 10h.01',
  eye: 'M2 10s3-5.5 8-5.5 8 5.5 8 5.5-3 5.5-8 5.5S2 10 2 10zM10 12.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5z',
  up: 'M10 16V4M5 9l5-5 5 5',
  down: 'M10 4v12M5 11l5 5 5-5',
  map: 'M3 5l4.5-1.5 5 2L17 4v11l-4.5 1.5-5-2L3 16zM7.5 3.5v11M12.5 5.5v11',
  // Carte des relations : fiches (nœuds) reliées.
  graph: 'M3 5.5a2 2 0 1 0 4 0a2 2 0 1 0 -4 0M13 4.5a2 2 0 1 0 4 0a2 2 0 1 0 -4 0M8 15a2 2 0 1 0 4 0a2 2 0 1 0 -4 0M7 5.3L13 4.7M6 7.2L9 13.2M14 6.3L11 13.2',
  comment: 'M4 4.5h12v8.5H9l-4 3.5v-3.5H4zM7 8h6M7 10.5h4',
  check: 'M4.5 10.5l3.5 3.5 7.5-8',
  spark: 'M10 2.5c.6 3.9 3.6 6.9 7.5 7.5-3.9.6-6.9 3.6-7.5 7.5-.6-3.9-3.6-6.9-7.5-7.5 3.9-.6 6.9-3.6 7.5-7.5z',
  table: 'M3 4h14v12H3zM3 8h14M3 12h14M8 4v12M12.5 4v12',
  history: 'M3.5 10a6.5 6.5 0 102-4.7M3.5 3.5v2.8h2.8M10 6.5V10l2.5 2',
  copy: 'M7.5 7.5h9v9h-9zM4 12.5V3.5h9',
  template: 'M4 3.5h12v4H4zM4 10h5v6.5H4zM11.5 10H16M11.5 13H16M11.5 16H16',
  // Chronologie : axe horizontal, événements alternés au-dessus et en dessous.
  grip: 'M7.5 5h.01M12.5 5h.01M7.5 10h.01M12.5 10h.01M7.5 15h.01M12.5 15h.01',
  timeline: 'M2.5 10h15M5.5 10V5.5M10 10v4.5M14.5 10V5.5M4 5.5h3M8.5 14.5h3M13 5.5h3',
  pin: 'M10 17.5s-5.5-5.2-5.5-9.5a5.5 5.5 0 0111 0c0 4.3-5.5 9.5-5.5 9.5zM10 10a2 2 0 100-4 2 2 0 000 4z',
  region: 'M2.5 16l5-8 3 4.5 2-3 5 6.5z',
  city: 'M3 17V8l3-2 3 2v9M9 17V5l3-2 3 2v12M15 17v-6h2.5v6M2 17h16',
  district: 'M3.5 3.5h5v5h-5zM11.5 3.5h5v5h-5zM3.5 11.5h5v5h-5zM11.5 11.5h5v5h-5z',
  house: 'M3.5 9L10 3.5 16.5 9M5 8v8.5h10V8M8.5 16.5v-4.5h3v4.5',
  door: 'M5 17V3.5h10V17M3 17h14M12 10.5h.01',
  cave: 'M2.5 16.5h15M4.5 16.5V10a5.5 5.5 0 0111 0v6.5M8 16.5v-4a2 2 0 014 0v4',
  tree: 'M10 17v-4M10 3l5 7h-3l3 4H5l3-4H5z',
  castle: 'M3 17V6h2v2h2V6h2v2h2V6h2v2h2V6h2v11zM8.5 17v-3.5a1.5 1.5 0 013 0V17',
  tower: 'M7 17V7.5L6 4h8l-1 3.5V17M5 17h10M9 10.5h2',
  temple: 'M3 7.5l7-4 7 4M4 7.5h12M5.5 7.5v7M8.5 7.5v7M11.5 7.5v7M14.5 7.5v7M3 16.5h14',
  tavern: 'M5 5.5h8v10a1 1 0 01-1 1H6a1 1 0 01-1-1zM13 8h1.5a2 2 0 012 2v1.5a2 2 0 01-2 2H13M8 8.5v5M10.5 8.5v5',
  shop: 'M3.5 8L5 3.5h10L16.5 8zM3.5 8a2.2 2.2 0 004.3 0 2.2 2.2 0 004.4 0 2.2 2.2 0 004.3 0M4.5 10.5v6h11v-6M8.5 16.5v-4h3v4',
  ruins: 'M3 16.5h14M4.5 16.5V9H7v7.5M9 16.5V6h2.5v10.5M13.5 16.5v-5H16v5M4 9h3.5M8.5 6H12',
  bridge: 'M2 9.5h16M3 9.5v7M17 9.5v7M5 16.5a5 5 0 0110 0',
  tent: 'M2.5 16.5L10 4l7.5 12.5zM8 16.5l2-4 2 4',
  signpost: 'M10 17.5V3M10 4.5h5.5l1.5 2-1.5 2H10M10 10.5H4.5L3 12.5l1.5 2H10',
  ship: 'M3 13.5h14l-2 3.5H5zM10 3v10.5M10 4l5 7h-5M10 6l-4 5h4',
  anchor: 'M10 6.5V17M7 9.5h6M4 12a6 6 0 0012 0M10 6.5a1.75 1.75 0 100-3.5 1.75 1.75 0 000 3.5z',
  mountain: 'M2 16.5l5-9 3 5 2-3 6 7zM5.5 10.2l1.5 1.3 1.3-1',
  island: 'M2.5 16c2.5-1.5 5-1.5 7.5 0s5 1.5 7.5 0M6 14a4 4 0 018 0M11 14V7M11 7c-1.5-2-3.5-2.3-5-1.5M11 7c1-2 3-2.5 4.5-2M11 7c2 .2 3.3 1.3 4 3',
  water: 'M2.5 7c2.5-2 5-2 7.5 0s5 2 7.5 0M2.5 11c2.5-2 5-2 7.5 0s5 2 7.5 0M2.5 15c2.5-2 5-2 7.5 0s5 2 7.5 0',
  factory: 'M3 17V9l4 2.5V9l4 2.5V9l4 2.5V4h2v13zM6 14h1M10 14h1M14 14h1',
  planet: 'M10 15a5 5 0 100-10 5 5 0 000 10zM4.6 12.6C2.4 14.4 1.7 15.9 2.3 16.5c1.1 1 5.8-1.5 10.3-5.4s7.2-7.9 6.1-8.9c-.6-.5-2 0-3.9 1.4',
  globe: 'M10 17.5a7.5 7.5 0 100-15 7.5 7.5 0 000 15zM2.5 10h15M10 2.5c2 2 3 4.5 3 7.5s-1 5.5-3 7.5c-2-2-3-4.5-3-7.5s1-5.5 3-7.5z',
  skull: 'M10 3a6 6 0 00-6 6c0 2 1 3.3 2.5 4v3h7v-3c1.5-.7 2.5-2 2.5-4a6 6 0 00-6-6zM7.5 10h.01M12.5 10h.01M8.5 16v-2M11.5 16v-2',
  crown: 'M3 7l3.5 3L10 4.5l3.5 5.5L17 7l-1.5 8h-11zM4.5 17h11',
  shield: 'M10 3l6 2.5V10c0 3.5-2.5 6-6 7.5C6.5 16 4 13.5 4 10V5.5z',
  flame: 'M10 17.5c3 0 5-2 5-5 0-3.5-3-5-3.5-9-2 1.5-3.5 3.5-3.5 6-1-.8-1.5-1.8-1.5-3C4.5 8.5 5 11 5 12.5c0 3 2 5 5 5z',
};

/** Lien vers le code source de la version en service, proposé à tout utilisateur de l'instance, adresse donnée par /api/auth/status (MAZE_SOURCE_URL côté serveur). Ouvert dans un nouvel onglet. */
export function SourceLink({ url, className, role, onClick, children }) {
  if (!url) return null;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className={className} role={role} onClick={onClick}>
      {children}{t('Code source')}
    </a>
  );
}

export function Icon({ name, size = 18, title }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor"
      strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden={title ? undefined : true} role={title ? 'img' : undefined}>
      {title && <title>{title}</title>}
      <path d={PATHS[name]} />
    </svg>
  );
}

/** Icône importée d'un type de lieu (symbole de légende), un peu plus grande qu'une icône intégrée : symbole rond. */
export function KindImage({ src, size = 16, title }) {
  const px = Math.round(size * 1.2);
  return <img className="kind-image" src={src} alt="" title={title} width={px} height={px} draggable={false} />;
}

// --- Notifications ------------------------------------------------------------
const ToastContext = createContext(() => {});
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const push = useCallback((message, tone = 'info') => {
    const id = Math.random().toString(36).slice(2);
    setToasts((list) => [...list, { id, message, tone }]);
    setTimeout(() => setToasts((list) => list.filter((x) => x.id !== id)), tone === 'error' ? 6000 : 2800);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast--${toast.tone}`}>{toast.message}</div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** Exécute une action asynchrone et affiche l'erreur éventuelle. */
export function useAction() {
  const toast = useToast();
  return useCallback(async (fn, success) => {
    try {
      const result = await fn();
      if (success) toast(success, 'success');
      return result;
    } catch (err) {
      toast(err.message || String(err), 'error');
      return undefined;
    }
  }, [toast]);
}

// --- Fenêtres modales ---------------------------------------------------------

// Pile des fenêtres ouvertes : seule celle du dessus réagit au clavier (Échap ferme une seule fenêtre).
const layers = [];

/** Inscrit une fenêtre dans la pile ; `onKey(e)` n'est appelé que si elle est au-dessus. */
export function useDialogLayer(onKey) {
  const handler = useRef(onKey);
  handler.current = onKey;
  useEffect(() => {
    const layer = {};
    layers.push(layer);
    const listener = (e) => {
      if (layers[layers.length - 1] !== layer) return;
      handler.current(e);
    };
    window.addEventListener('keydown', listener, true);
    return () => {
      window.removeEventListener('keydown', listener, true);
      layers.splice(layers.indexOf(layer), 1);
    };
  }, []);
}

export function Modal({ title, onClose, children, footer, wide }) {
  const ref = useRef(null);
  const titleId = useId();
  const close = useRef(onClose);
  close.current = onClose;
  // Focus à l'ouverture seulement (pas à chaque rendu du parent), rendu à l'élément d'origine à la fermeture.
  useEffect(() => {
    const previous = document.activeElement;
    const first = ref.current?.querySelector('input, textarea, select, button:not(.modal__close):not(:disabled)');
    first?.focus();
    return () => previous?.focus?.();
  }, []);
  useDialogLayer((e) => {
    // Un sous-formulaire peut gérer Échap lui-même (data-own-escape).
    if (e.key === 'Escape' && !e.target.closest?.('[data-own-escape]')) {
      e.stopImmediatePropagation();
      close.current();
    }
  });
  return createPortal(
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`modal${wide ? ' modal--wide' : ''}`} role="dialog" aria-modal="true"
        aria-labelledby={titleId} ref={ref}>
        <header className="modal__head">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="icon-btn modal__close" onClick={onClose} aria-label={t('Fermer')}>
            <Icon name="close" />
          </button>
        </header>
        <div className="modal__body">{children}</div>
        {footer && <footer className="modal__foot">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}

export function ConfirmDialog({ title, message, confirmLabel, onConfirm, onCancel }) {
  return (
    <Modal title={title} onClose={onCancel} footer={(
      <>
        <button type="button" className="btn btn--ghost" onClick={onCancel}>{t('Annuler')}</button>
        <button type="button" className="btn btn--danger" onClick={onConfirm}>{confirmLabel}</button>
      </>
    )}>
      <p className="confirm-text">{message}</p>
    </Modal>
  );
}

const ConfirmContext = createContext(async () => false);
export const useConfirm = () => useContext(ConfirmContext);

export function ConfirmProvider({ children }) {
  const [state, setState] = useState(null);
  const confirm = useCallback((options) => new Promise((resolve) => {
    setState({ ...options, resolve });
  }), []);
  const close = (value) => { state?.resolve(value); setState(null); };
  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {state && (
        <ConfirmDialog title={state.title} message={state.message}
          confirmLabel={state.confirmLabel || t('Supprimer')}
          onConfirm={() => close(true)} onCancel={() => close(false)} />
      )}
    </ConfirmContext.Provider>
  );
}

// --- Champs -------------------------------------------------------------------
export function Field({ label, hint, children, htmlFor }) {
  return (
    <div className="field">
      <label className="field__label" htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && <p className="field__hint">{hint}</p>}
    </div>
  );
}

export function TagInput({ id, value, onChange }) {
  const [draft, setDraft] = useState('');
  const add = () => {
    const tag = draft.trim().replace(/,$/, '');
    if (tag && !value.some((v) => v.toLowerCase() === tag.toLowerCase())) onChange([...value, tag]);
    setDraft('');
  };
  return (
    <div className="tag-input">
      {value.map((tag) => (
        <span key={tag} className="tag">
          {tag}
          <button type="button" aria-label={t('Retirer {tag}', { tag })}
            onClick={() => onChange(value.filter((x) => x !== tag))}>
            <Icon name="close" size={12} />
          </button>
        </span>
      ))}
      <input id={id} value={draft} placeholder={value.length ? '' : t('Ajouter puis Entrée')}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={add}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(); }
          if (e.key === 'Backspace' && !draft && value.length) onChange(value.slice(0, -1));
        }} />
    </div>
  );
}

/** Contenu écrit avec l'éditeur (HTML) : rendu comme dans les fiches, mentions cliquables. */
function RichHtml({ html, onOpenNode, className }) {
  const ref = useRef(null);
  useEffect(() => { if (ref.current) renderDocInto(ref.current, html); }, [html]);
  return (
    <div
      ref={ref}
      className={`doc-content rich-view ${className}`}
      onClick={(e) => {
        const mention = e.target.closest('a.mention');
        if (!mention) return;
        e.preventDefault();
        const [kind, id] = (mention.dataset.mention || '').split(':');
        if (kind === 'doc') {
          const cid = window.location.hash.match(/^#\/c\/(\d+)/)?.[1];
          if (cid) window.location.hash = `#/c/${cid}/scenarios/${id}`;
        } else if (kind && id) {
          onOpenNode?.(`${kind}-${id}`);
        }
      }}
    />
  );
}

/** Bloc Markdown avec liens [[Nom]] cliquables (ou contenu de l'éditeur, s'il est en HTML). */
export function Markdown({ text, resolve, onOpenNode, className = '' }) {
  if (isRichHtml(text)) return <RichHtml html={text} onOpenNode={onOpenNode} className={className} />;
  return (
    <div
      className={`md ${className}`}
      onClick={(e) => {
        const link = e.target.closest('a.wikilink');
        if (link) { e.preventDefault(); onOpenNode?.(link.dataset.node); }
      }}
      // Contenu assaini par DOMPurify dans renderMarkdown.
      dangerouslySetInnerHTML={{ __html: renderMarkdown(text, resolve) }}
    />
  );
}
