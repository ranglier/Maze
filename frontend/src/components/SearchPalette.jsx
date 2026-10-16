import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api.js';
import { NPC_STATUS, placeKind } from '../constants.js';
import { N_, t } from '../i18n/index.js';
import { initialsAvatar, storage } from '../util.js';
import { Icon, KindImage, useDialogLayer } from './ui.jsx';

/*
 * Recherche globale (Ctrl+K) : PNJ, factions, fiches rédigées et commentaires, dans la campagne en
 * cours ou dans toutes ses campagnes. Le serveur renvoie les passages trouvés entourés des
 * marqueurs \u0002 et \u0003, transformés ici en <mark> (aucun HTML interprété).
 */

// Libellés traduits à l'affichage (t(g.label)) : N_ les signale seulement à l'extraction.
const GROUPS = [
  { kind: 'npc', label: N_('PNJ') },
  { kind: 'faction', label: N_('Factions') },
  { kind: 'place', label: N_('Lieux') },
  { kind: 'document', label: N_('Fiches rédigées') },
  { kind: 'comment', label: N_('Commentaires') },
];

export function Highlight({ text }) {
  const parts = [];
  let marked = false;
  let buffer = '';
  for (const ch of text || '') {
    if (ch === '\u0002' || ch === '\u0003') {
      if (buffer) parts.push(marked ? <mark key={parts.length}>{buffer}</mark> : buffer);
      buffer = '';
      marked = ch === '\u0002';
    } else {
      buffer += ch;
    }
  }
  if (buffer) parts.push(marked ? <mark key={parts.length}>{buffer}</mark> : buffer);
  return <>{parts}</>;
}

// Commentaire d'une fiche PNJ, faction ou lieu : phrase entière par sorte de fiche.
const ENTRY_COMMENT = {
  npc: (name) => t('sur le PNJ « {name} »', { name }),
  faction: (name) => t('sur la faction « {name} »', { name }),
  place: (name) => t('sur le lieu « {name} »', { name }),
};

const plain = (text) => (text || '').replace(/[\u0002\u0003]/g, '');

/** Adresse où ouvrir un résultat. */
export function resultHref(r, query) {
  if (r.kind === 'npc' || r.kind === 'faction') return `#/c/${r.campaign_id}/personnages/${r.kind}-${r.id}`;
  if (r.kind === 'place') return `#/c/${r.campaign_id}/lieux/${r.id}`;
  if (r.kind === 'comment') return `#/c/${r.campaign_id}/scenarios/${r.doc_id}?comment=${r.id}`;
  if (r.kind === 'entry_comment') {
    return r.entry_kind === 'place' ? `#/c/${r.campaign_id}/lieux/${r.entry_id}?comment=${r.id}`
      : `#/c/${r.campaign_id}/personnages/${r.entry_kind}-${r.entry_id}?comment=${r.id}`;
  }
  return `#/c/${r.campaign_id}/scenarios/${r.id}?find=${encodeURIComponent(query)}`;
}

function docLabel(r) {
  return r.doc_kind === 'campaign' ? t('Fiche de campagne') : (plain(r.doc_title) || t('Sans titre'));
}

export default function SearchPalette({ campaign, multiple, onClose, onNavigate }) {
  const [query, setQuery] = useState('');
  const [scope, setScope] = useState(() => storage.get('maze:search:scope') || 'campaign');
  const [results, setResults] = useState(null);
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  useDialogLayer((e) => {
    if (e.key === 'Escape') { e.stopImmediatePropagation(); onClose(); }
  });

  // Recherche au fil de la frappe (légèrement différée), la dernière réponse seule compte.
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) { setResults(null); setLoading(false); return undefined; }
    let cancelled = false;
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const data = await api.search(q, scope === 'all' || !campaign ? 'all' : campaign.id);
        if (!cancelled) { setResults(data.results); setActive(0); }
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 160);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [query, scope, campaign]);

  // Résultats regroupés par type, dans l'ordre d'affichage (pour la navigation au clavier).
  const groups = useMemo(() => GROUPS.map((g) => ({ ...g, items: (results || []).filter((r) => r.kind === g.kind || (g.kind === 'comment' && r.kind === 'entry_comment')) }))
    .filter((g) => g.items.length), [results]);
  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);

  useEffect(() => {
    listRef.current?.querySelector('.is-active')?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  const open = (r) => {
    if (!r) return;
    onClose();
    onNavigate(resultHref(r, query.trim()));
  };

  const setScopeSaved = (s) => { setScope(s); storage.set('maze:search:scope', s); };

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, flat.length - 1)); }
    if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    if (e.key === 'Enter') { e.preventDefault(); open(flat[active]); }
  };

  let index = -1;
  return createPortal(
    <div className="palette-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="palette" role="dialog" aria-modal="true" aria-label={t('Recherche globale')}>
        <div className="palette__field">
          <Icon name="search" size={18} />
          <input ref={inputRef} type="search" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={onKeyDown}
            placeholder={t('Rechercher un PNJ, une faction, un lieu, un scénario…')} aria-label={t('Rechercher')}
            aria-controls="palette-results" autoComplete="off" spellCheck={false} />
          {loading && <span className="palette__spinner" aria-hidden="true" />}
          <button type="button" className="icon-btn palette__close" onClick={onClose} aria-label={t('Fermer la recherche')}>
            <Icon name="close" />
          </button>
        </div>
        {campaign && multiple && (
          <div className="palette__scope segmented" role="radiogroup" aria-label={t('Portée de la recherche')}>
            <button type="button" role="radio" aria-checked={scope !== 'all'} className={scope !== 'all' ? 'is-active' : ''}
              onClick={() => setScopeSaved('campaign')}>{campaign.name}</button>
            <button type="button" role="radio" aria-checked={scope === 'all'} className={scope === 'all' ? 'is-active' : ''}
              onClick={() => setScopeSaved('all')}>{t('Toutes mes campagnes')}</button>
          </div>
        )}
        <div className="palette__results" id="palette-results" ref={listRef} role="listbox">
          {query.trim().length < 2 ? (
            <p className="palette__hint">
              {t('Accents et majuscules sont ignorés, un début de mot suffit. {up} {down} pour choisir, {enter} pour ouvrir.', {
                up: <kbd>↑</kbd>, down: <kbd>↓</kbd>, enter: <kbd>{t('Entrée')}</kbd>,
              })}
            </p>
          ) : results && flat.length === 0 && !loading ? (
            <p className="palette__hint">{t('Aucun résultat pour « {query} ».', { query: query.trim() })}</p>
          ) : groups.map((g) => (
            <section key={g.kind} className="palette__group">
              <h3>{t(g.label)}</h3>
              <ul>
                {g.items.map((r) => {
                  index += 1;
                  const i = index;
                  const isEntity = r.kind === 'npc' || r.kind === 'faction';
                  return (
                    <li key={`${r.kind}-${r.id}`} role="option" aria-selected={i === active}>
                      <a href={resultHref(r, query.trim())} className={`palette__item${i === active ? ' is-active' : ''}`}
                        onMouseMove={() => { if (i !== active) setActive(i); }}
                        onClick={(e) => { e.preventDefault(); open(r); }}>
                        {isEntity ? (
                          r.thumb
                            ? <img src={r.thumb} alt="" className={r.kind === 'faction' ? 'is-faction' : ''} />
                            : r.kind === 'npc'
                              ? <img src={initialsAvatar(plain(r.title), '#5d6b80')} alt="" />
                              : <span className="palette__seal" style={{ background: r.color }} />
                        ) : r.kind === 'place' ? (
                          r.thumb ? <img src={r.thumb} alt="" className="is-faction" />
                            : <span className="palette__icon palette__icon--place">{r.place_kind_image ? <KindImage src={r.place_kind_image} size={17} />
                              : <Icon name={r.place_kind_icon || placeKind(r.place_kind).icon} size={17} />}</span>
                        ) : (
                          <span className="palette__icon"><Icon name={r.kind === 'comment' || r.kind === 'entry_comment' ? 'comment' : 'page'} size={17} /></span>
                        )}
                        <span className="palette__text">
                          <span className="palette__title">
                            {r.kind === 'comment' || r.kind === 'entry_comment' ? t('Commentaire de {name}', { name: plain(r.title) || '?' })
                              : r.kind === 'document' && r.doc_kind === 'campaign' ? t('Fiche de campagne')
                                : <Highlight text={r.title || t('Sans titre')} />}
                            {r.status === 'mort' && <span className="palette__tag">{NPC_STATUS.mort.label.toLowerCase()}</span>}
                          </span>
                          <span className="palette__sub">
                            {r.kind === 'comment' ? t('sur « {name} »', { name: docLabel(r) })
                              : r.kind === 'entry_comment' ? (ENTRY_COMMENT[r.entry_kind] || ((name) => t('sur « {name} »', { name })))(r.entry_name || '?')
                              : r.kind === 'place' ? `${r.place_kind_label || placeKind(r.place_kind).label}${r.sub ? `, ${r.sub}` : ''}`
                                : isEntity ? (r.sub || (r.kind === 'npc' ? t('PNJ') : t('Faction'))) : ''}
                            {scope === 'all' && multiple && <span className="palette__campaign">{r.campaign}</span>}
                          </span>
                          {r.snippet && plain(r.snippet).trim() && (
                            <span className="palette__snippet"><Highlight text={r.snippet} /></span>
                          )}
                        </span>
                      </a>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}
