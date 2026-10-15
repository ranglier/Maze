import { useEffect, useRef, useState } from 'react';
import { renderDocInto } from '../editor/schema.js';
import { Icon } from './ui.jsx';

/*
 * Historique des versions d'une fiche (panneau latéral) et aperçu d'une version (zone principale).
 */

const timeFmt = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit' });
const dayFmt = new Intl.DateTimeFormat('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
const fullFmt = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });

const parseDate = (iso) => new Date(iso);

function dayLabel(date) {
  const today = new Date();
  const start = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = Math.round((start(today) - start(date)) / 86400000);
  if (diff === 0) return 'Aujourd’hui';
  if (diff === 1) return 'Hier';
  const label = dayFmt.format(date);
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export function versionTitle(v) {
  if (v.label) return v.kind === 'restore' ? `Restauration : ${v.label}` : v.label;
  if (v.kind === 'restore') return 'Version restaurée';
  if (v.kind === 'manual') return 'Version enregistrée';
  return 'Enregistrement automatique';
}

export const versionDate = (v) => fullFmt.format(parseDate(v.created_at));

function sizeLabel(bytes) {
  return bytes < 1024 ? `${bytes} o` : `${Math.round(bytes / 1024)} ko`;
}

export function HistoryPanel({
  versions, activeId, readOnly, onSelect, onCurrent, onCreate, onRename, onDelete,
  // Textes propres aux fiches PNJ, factions et lieux (par défaut : fiches rédigées).
  currentMeta = 'en cours d’édition', emptyText, note, notice,
}) {
  const [naming, setNaming] = useState(false);
  const [label, setLabel] = useState('');
  const [renaming, setRenaming] = useState(null);   // { id, label }

  const submitNew = async (e) => {
    e.preventDefault();
    if (await onCreate(label.trim())) {
      setNaming(false);
      setLabel('');
    }
  };
  const submitRename = async (e) => {
    e.preventDefault();
    if (await onRename(renaming.id, renaming.label.trim())) setRenaming(null);
  };

  const groups = [];
  for (const v of versions || []) {
    const day = dayLabel(parseDate(v.created_at));
    if (!groups.length || groups[groups.length - 1].day !== day) groups.push({ day, items: [] });
    groups[groups.length - 1].items.push(v);
  }

  return (
    <div className="history">
      {!readOnly && (naming ? (
        <form className="history__new" onSubmit={submitNew} data-own-escape>
          <input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={120} autoFocus
            placeholder="Nom de la version (ex. : avant la séance 4)" aria-label="Nom de la version"
            onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setNaming(false); } }} />
          <div className="history__new-actions">
            <button type="button" className="btn btn--small btn--ghost" onClick={() => setNaming(false)}>Annuler</button>
            <button type="submit" className="btn btn--small btn--primary">Enregistrer</button>
          </div>
        </form>
      ) : (
        <button type="button" className="btn btn--small btn--ghost history__save" onClick={() => setNaming(true)}>
          <Icon name="star" size={14} /> Nommer l’état actuel
        </button>
      ))}
      {notice && <p className="history__notice">{notice}</p>}
      <button type="button" className={`history__item history__item--current${activeId == null ? ' is-active' : ''}`}
        onClick={onCurrent}>
        <span className="history__title">Version actuelle</span>
        <span className="history__meta">{currentMeta}</span>
      </button>
      {versions === null ? <p className="muted history__empty">Chargement…</p> : versions.length === 0 ? (
        <p className="muted history__empty">
          {emptyText || 'Aucune version pour l’instant. Maze en garde automatiquement à chaque séance d’écriture ; vous pouvez '
            + 'aussi nommer l’état actuel avant une modification importante.'}
        </p>
      ) : groups.map((g) => (
        <section key={g.day} className="history__group">
          <h3>{g.day}</h3>
          <ul>
            {g.items.map((v) => (
              <li key={v.id}>
                {renaming?.id === v.id ? (
                  <form className="history__new" onSubmit={submitRename} data-own-escape>
                    <input value={renaming.label} maxLength={120} autoFocus aria-label="Nom de la version"
                      onChange={(e) => setRenaming({ ...renaming, label: e.target.value })}
                      onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setRenaming(null); } }} />
                    <div className="history__new-actions">
                      <button type="button" className="btn btn--small btn--ghost" onClick={() => setRenaming(null)}>Annuler</button>
                      <button type="submit" className="btn btn--small btn--primary">Renommer</button>
                    </div>
                  </form>
                ) : (
                  <div className={`history__item${activeId === v.id ? ' is-active' : ''}`}>
                    <button type="button" className="history__open" onClick={() => onSelect(v)}>
                      <span className="history__title">
                        {v.kind === 'manual' && <Icon name="star" size={13} />}
                        {v.kind === 'restore' && <Icon name="refresh" size={13} />}
                        {timeFmt.format(parseDate(v.created_at))} · {versionTitle(v)}
                      </span>
                      <span className="history__meta">
                        {v.author_name ? `${v.author_name} · ` : ''}{v.is_current ? 'identique à l’actuelle' : sizeLabel(v.size)}
                      </span>
                    </button>
                    {!readOnly && (
                      <span className="history__actions">
                        <button type="button" className="icon-btn icon-btn--small" title="Nommer cette version"
                          aria-label="Nommer cette version" onClick={() => setRenaming({ id: v.id, label: v.label })}>
                          <Icon name="edit" size={13} />
                        </button>
                        <button type="button" className="icon-btn icon-btn--small" title="Supprimer cette version"
                          aria-label="Supprimer cette version" onClick={() => onDelete(v)}>
                          <Icon name="trash" size={13} />
                        </button>
                      </span>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
      {versions?.length > 0 && (
        <p className="muted history__note">
          {note || 'Les versions automatiques les plus anciennes sont effacées au-delà de 100 ; les versions nommées sont conservées.'}
        </p>
      )}
    </div>
  );
}

/** Aperçu d'une version, à la place de l'éditeur. */
export function VersionPreview({ version, content, readOnly, onRestore, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current && content != null) renderDocInto(ref.current, content);
  }, [content]);
  return (
    <div className="version-view">
      <div className="version-view__bar" role="status">
        <Icon name="history" size={17} />
        <span className="version-view__text">
          <strong>{versionTitle(version)}</strong>
          <span className="muted"> · {versionDate(version)}{version.author_name ? ` · ${version.author_name}` : ''}</span>
        </span>
        {!readOnly && (
          <button type="button" className="btn btn--small btn--primary" onClick={onRestore} disabled={content == null}>
            Restaurer cette version
          </button>
        )}
        <button type="button" className="btn btn--small btn--ghost" onClick={onClose}>Revenir à la version actuelle</button>
      </div>
      {content == null
        ? <div className="doc-connecting" aria-busy="true">Chargement de la version…</div>
        : <div ref={ref} className="doc-content version-view__content" />}
    </div>
  );
}
