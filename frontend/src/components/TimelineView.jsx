import { Fragment, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import * as TL from '../timeline.js';
import { storage } from '../util.js';
import { ConflictPanel, useEntryDraft } from './EntryDraft.jsx';
import MoveDialog from './MoveDialog.jsx';
import RichField from './RichField.jsx';
import useScenarioData from './scenarios/useScenarioData.js';
import { useTreeDrag } from './treeDrag.js';
import { Field, Icon, Markdown, TagInput, useAction, useConfirm, useToast } from './ui.jsx';
import { N_, compareText, t } from '../i18n/index.js';

/*
 * Onglet Chronologie (module facultatif d'une campagne, campaign.timeline_enabled) : événements dans un ordre
 * choisi par le MJ (aucune échelle de temps, la date est un texte libre), en deux modes :
 *   - liste verticale : édition directe, « + » entre deux événements, glisser-déposer et « Déplacer vers… » ;
 *   - frise ordinale horizontale : cartes alternées de part et d'autre d'un axe, panneau latéral (feuille basse sur
 *     téléphone) avec la description complète.
 * Logique pure (ordre, insertion, filtre, couleurs) : ../timeline.js, testée par test/timeline.test.mjs.
 */

const MODE_KEY = 'maze:timeline:mode';
const DENSITY_KEY = 'maze:timeline:density';
const EVENT_KEYS = ['date_label', 'title', 'tags', 'description'];
// Libellés du panneau de conflit (traduits par ConflictPanel, à l'affichage).
const LABELS = { date_label: N_('Date'), title: N_('Titre'), tags: N_('Étiquettes'), description: N_('Description') };
const RICH = new Set(['description']);
const phonePortrait = () => {
  try { return window.matchMedia('(max-width: 760px) and (orientation: portrait)').matches; } catch { return false; }
};
const markerColor = (ev) => (ev.tags?.length ? TL.tagColor(ev.tags[0]) : 'var(--brass)');

export default function TimelineView({ campaign, header, trailing, eventId, onNavigate, onTargetDone }) {
  const cid = campaign.id;
  const run = useAction();
  const confirm = useConfirm();
  const [events, setEvents] = useState(null);
  const [mode, setModeState] = useState(() => TL.initialMode(storage.get(MODE_KEY), phonePortrait()));
  const [density, setDensityState] = useState(() => (storage.get(DENSITY_KEY) === 'compact' ? 'compact' : 'normal'));
  const [tagFilter, setTagFilter] = useState('');
  const [form, setForm] = useState(null);          // { id } : modification ; { isNew, beforeId } : création
  const [selected, setSelected] = useState(null);  // frise : événement ouvert dans le panneau
  const [moving, setMoving] = useState(null);      // événement de « Déplacer vers… »
  const [highlight, setHighlight] = useState(null);
  const [expanded, setExpanded] = useState(() => new Set());

  const setMode = (m) => { setModeState(m); storage.set(MODE_KEY, m); };
  const setDensity = (d) => { setDensityState(d); storage.set(DENSITY_KEY, d); };

  const load = useCallback(async () => {
    const data = await run(() => api.events(cid));
    if (data) setEvents(data.events);
    return data;
  }, [cid, run]);
  useEffect(() => { load(); }, [load]);

  // Fiches citables avec @ (PNJ, factions, lieux, fiches rédigées) et pastilles des fiches citées.
  const { entities } = useScenarioData({ cid, campaignName: campaign.name, run });
  const byKey = useMemo(() => new Map(entities.map((e) => [`${e.kind}:${e.id}`, e])), [entities]);
  const byId = useMemo(() => new Map((events || []).map((e) => [e.id, e])), [events]);

  const tags = useMemo(() => TL.allTags(events, (a, b) => compareText(a, b, { sensitivity: 'base' })), [events]);
  const activeTag = tags.some((x) => x.toLowerCase() === tagFilter.toLowerCase()) ? tagFilter : '';
  const visible = useMemo(() => TL.filterByTag(events || [], activeTag), [events, activeTag]);

  // ?event=<id> (fiche, recherche) : événement mis en évidence, panneau ouvert dans la frise.
  useEffect(() => {
    if (!eventId || !events) return undefined;
    const target = events.find((e) => e.id === eventId);
    onTargetDone?.();
    if (!target) return undefined;
    setTagFilter('');
    setHighlight(eventId);
    if (mode === 'frieze') setSelected(eventId);
    else setExpanded((s) => new Set(s).add(eventId));
    requestAnimationFrame(() => {
      document.querySelector(`[data-event="${eventId}"]`)?.scrollIntoView({ block: 'center', inline: 'center' });
    });
    return undefined;
  }, [eventId, events]); // eslint-disable-line react-hooks/exhaustive-deps

  // Mise en évidence passagère (événement ouvert depuis un lien, ou tout juste créé).
  useEffect(() => {
    if (!highlight) return undefined;
    const timer = setTimeout(() => setHighlight(null), 3000);
    return () => clearTimeout(timer);
  }, [highlight]);

  const openMention = (nid) => {
    const m = /^(npc|faction|place|doc)-(\d+)$/.exec(nid || '');
    if (m) onNavigate(TL.mentionHref(cid, m[1], m[2]));
  };

  const reorder = async (ids) => {
    const before = events;
    if (ids.join() === TL.idsOf(events).join()) return;
    setEvents(ids.map((id) => byId.get(id)));
    const data = await run(() => api.orderEvents(cid, ids));
    if (data) setEvents(data.events);
    else setEvents(before);
  };

  const dnd = useTreeDrag((dragged, target, where) => reorder(TL.moveRelative(TL.idsOf(events), dragged.id, target.id, where)));

  const startCreate = (beforeId) => {
    setForm({ isNew: true, beforeId });
    if (mode === 'frieze') setSelected(null);
  };

  const onCreated = async (created) => {
    setForm(null);
    const data = await load();
    const id = created?.id;
    if (!id || !data) return;
    if (mode === 'frieze') setSelected(id);
    setHighlight(id);
    requestAnimationFrame(() => document.querySelector(`[data-event="${id}"]`)?.scrollIntoView({ block: 'nearest', inline: 'center' }));
  };

  const onSaved = async () => {
    setForm(null);
    await load();
  };

  const remove = async (ev) => {
    const ok = await confirm({
      title: t('Supprimer « {title} »', { title: ev.title }),
      message: t('L’événement sera retiré de la chronologie. Cette action est définitive.'),
      confirmLabel: t('Supprimer'),
    });
    if (!ok) return;
    if (await run(() => api.deleteEvent(ev.id), t('Événement supprimé')) === undefined) return;
    setEvents((list) => list.filter((e) => e.id !== ev.id));
    if (selected === ev.id) setSelected(null);
    if (form?.id === ev.id) setForm(null);
  };

  // Frise : Échap ferme le panneau (pas pendant une modification, ni sous une fenêtre).
  useEffect(() => {
    if (mode !== 'frieze' || !selected || form) return undefined;
    const onKey = (e) => {
      if (e.key !== 'Escape' || document.querySelector('.modal-backdrop, .palette-backdrop')) return;
      setSelected(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mode, selected, form]);

  const formProps = { entities, campaignId: cid };
  const newForm = form?.isNew && (
    <EventForm key="nouveau" {...formProps} isNew beforeId={form.beforeId} onCancel={() => setForm(null)}
      onSaved={onCreated} />
  );
  const selectedEvent = selected ? byId.get(selected) : null;
  const panelOpen = mode === 'frieze' && (form || selectedEvent);
  const editedEvent = form?.id ? byId.get(form.id) : null;

  const empty = events && events.length === 0 && !form?.isNew;

  return (
    <div className="scenarios timeline">
      <header className="topbar">
        {header}
        <div className="topbar__right">
          <div className="toolbar">
            {events && (
              <div className="segmented segmented--small timeline__modes" role="radiogroup" aria-label={t('Affichage de la chronologie')}>
                <button type="button" role="radio" aria-checked={mode === 'list'} className={mode === 'list' ? 'is-active' : ''}
                  aria-label={t('Liste')} title={t('Liste')} onClick={() => setMode('list')}>
                  <Icon name="list" size={15} /><span>{t('Liste')}</span>
                </button>
                <button type="button" role="radio" aria-checked={mode === 'frieze'} className={mode === 'frieze' ? 'is-active' : ''}
                  aria-label={t('Frise')} title={t('Frise')} onClick={() => setMode('frieze')}>
                  <Icon name="timeline" size={15} /><span>{t('Frise')}</span>
                </button>
              </div>
            )}
            {mode === 'frieze' && events?.length > 0 && (
              <button type="button" className={`icon-btn timeline__density${density === 'compact' ? ' is-active' : ''}`}
                aria-pressed={density === 'compact'} title={t('Affichage compact (date et titre)')}
                aria-label={t('Affichage compact (date et titre)')}
                onClick={() => setDensity(density === 'compact' ? 'normal' : 'compact')}>
                <Icon name="fold" size={17} />
              </button>
            )}
            <button type="button" className="btn btn--ghost" onClick={() => startCreate(null)} disabled={!events}>
              <Icon name="plus" size={16} /><span className="btn__label">{t('Événement')}</span>
            </button>
            <span className="toolbar__sep" />
          </div>
          {trailing && <div className="topbar__end">{trailing}</div>}
        </div>
      </header>

      <div className={`timeline__body timeline__body--${mode}`}>
        {tags.length > 0 && (
          <div className="timeline-filters" role="group" aria-label={t('Filtrer par étiquette')}>
            <button type="button" className={`timeline-filter${activeTag ? '' : ' is-active'}`} aria-pressed={!activeTag}
              onClick={() => setTagFilter('')}>{t('Tous')}</button>
            {tags.map((tag) => (
              <button key={tag} type="button" style={{ '--tag': TL.tagColor(tag) }}
                className={`timeline-filter${activeTag === tag ? ' is-active' : ''}`} aria-pressed={activeTag === tag}
                onClick={() => setTagFilter(activeTag === tag ? '' : tag)}>
                <span className="timeline-filter__dot" />{tag}
              </button>
            ))}
          </div>
        )}

        {!events ? <div className="doc-loading" aria-busy="true">{t('Chargement…')}</div>
          : empty ? (
            <div className="timeline-empty">
              <Icon name="timeline" size={34} />
              <h2>{t('Aucun événement')}</h2>
              <p>{t('Notez les événements marquants de la campagne dans leur ordre : date libre, titre, description avec @mentions des PNJ, factions et lieux.')}</p>
              <button type="button" className="btn btn--primary" onClick={() => startCreate(null)}>
                <Icon name="plus" size={16} /> {t('Ajouter un événement')}
              </button>
            </div>
          ) : mode === 'list' ? (
            <div className="timeline__scroll">
              {visible.length === 0 && activeTag && (
                <p className="muted timeline__none">{t('Aucun événement avec l’étiquette « {tag} ».', { tag: activeTag })}</p>
              )}
              <ol className="timeline-list">
                {visible.map((ev) => {
                  const row = { key: `event-${ev.id}`, id: ev.id, label: ev.title };
                  return (
                    <Fragment key={ev.id}>
                      {form?.isNew && form.beforeId === ev.id
                        ? <li className="timeline-item is-editing">{newForm}</li>
                        : <InsertRow onClick={() => startCreate(ev.id)} />}
                      <li data-event={ev.id} {...dnd.props(row, { enabled: !form })} draggable={false}
                        className={`timeline-item${dnd.className(row.key)}${highlight === ev.id ? ' is-target' : ''}${form?.id === ev.id ? ' is-editing' : ''}`}
                        style={{ '--tag': markerColor(ev) }}>
                        {form?.id === ev.id ? (
                          <EventForm key={ev.id} {...formProps} event={ev} onCancel={() => setForm(null)} onSaved={onSaved} />
                        ) : (
                          <EventCard event={ev} campaignId={cid} byKey={byKey} folded={!expanded.has(ev.id)}
                            draggable={!form}
                            onToggleFold={() => setExpanded((s) => { const n = new Set(s); if (n.has(ev.id)) n.delete(ev.id); else n.add(ev.id); return n; })}
                            onOpenNode={openMention} onEdit={() => setForm({ id: ev.id })} onMove={() => setMoving(ev)}
                            onDelete={() => remove(ev)} />
                        )}
                      </li>
                    </Fragment>
                  );
                })}
                {form?.isNew && (form.beforeId == null || !visible.some((e) => e.id === form.beforeId))
                  ? <li className="timeline-item is-editing">{newForm}</li>
                  : <InsertRow end onClick={() => startCreate(null)} />}
              </ol>
            </div>
          ) : (
            <div className="timeline__stage">
              {visible.length === 0 && activeTag ? (
                <p className="muted timeline__none">{t('Aucun événement avec l’étiquette « {tag} ».', { tag: activeTag })}</p>
              ) : (
                <Frieze events={visible} density={density} byKey={byKey} campaignId={cid}
                  selectedId={form?.id || selected} highlight={highlight}
                  onSelect={(id) => { setForm(null); setSelected(id); }} onInsert={startCreate} />
              )}
              {panelOpen && (
                <aside className="panel timeline-panel" aria-label={t('Événement')}>
                  <div className="panel__scroll">
                    <button type="button" className="icon-btn panel__close" aria-label={t('Fermer')}
                      onClick={() => { setForm(null); setSelected(null); }}>
                      <Icon name="close" />
                    </button>
                    {form?.isNew ? newForm
                      : editedEvent ? (
                        <EventForm key={editedEvent.id} {...formProps} event={editedEvent} onCancel={() => setForm(null)}
                          onSaved={onSaved} />
                      ) : selectedEvent && (
                        <EventDetail event={selectedEvent} campaignId={cid} byKey={byKey} onOpenNode={openMention} />
                      )}
                  </div>
                  {!form && selectedEvent && (
                    <footer className="panel__actions">
                      <button type="button" className="btn btn--primary" onClick={() => setForm({ id: selectedEvent.id })}>
                        <Icon name="edit" size={16} /> {t('Modifier')}
                      </button>
                      <button type="button" className="btn btn--ghost" onClick={() => setMoving(selectedEvent)}>
                        <Icon name="swap" size={16} /> {t('Déplacer vers…')}
                      </button>
                      <button type="button" className="icon-btn icon-btn--danger" onClick={() => remove(selectedEvent)}
                        aria-label={t('Supprimer')} title={t('Supprimer')}>
                        <Icon name="trash" size={17} />
                      </button>
                    </footer>
                  )}
                </aside>
              )}
            </div>
          )}
      </div>

      {moving && byId.has(moving.id) && (
        <MoveDialog
          title={t('Déplacer « {title} »', { title: moving.title })}
          intro={t('Choisissez sa nouvelle place dans la chronologie.')}
          options={TL.moveSlots(events, moving.id).map((s) => {
            const next = s.beforeId == null ? null : byId.get(s.beforeId);
            return {
              id: s.beforeId ?? 'fin',
              label: next ? t('Avant « {title} »', { title: next.date_label ? `${next.date_label}, ${next.title}` : next.title })
                : t('À la fin'),
              icon: 'timeline', current: s.current,
            };
          })}
          onPick={(id) => {
            const target = moving;
            setMoving(null);
            reorder(TL.moveBefore(TL.idsOf(events), target.id, id === 'fin' ? null : id));
          }}
          onClose={() => setMoving(null)} />
      )}
    </div>
  );
}

/** « + » entre deux événements de la liste (et en fin de liste). */
function InsertRow({ onClick, end = false }) {
  return (
    <li className={`timeline-insert${end ? ' timeline-insert--end' : ''}`}>
      <button type="button" onClick={onClick}
        aria-label={end ? t('Ajouter un événement à la fin') : t('Insérer un événement ici')}
        title={end ? t('Ajouter un événement à la fin') : t('Insérer un événement ici')}>
        <Icon name="plus" size={15} />
        {end && <span>{t('Ajouter un événement')}</span>}
      </button>
    </li>
  );
}

function Tags({ tags }) {
  if (!tags?.length) return null;
  return (
    <div className="timeline-tags" aria-label={t('Étiquettes')}>
      {tags.map((tag) => <span key={tag} className="timeline-tag" style={{ '--tag': TL.tagColor(tag) }}>{tag}</span>)}
    </div>
  );
}

/** Fiches citées par un événement : pastilles qui mènent à leur fiche (`dots` : pastilles seules, pour la frise). */
function Mentions({ event, byKey, campaignId, dots = false }) {
  const list = TL.eventMentions(event).map((m) => ({ ...m, entity: byKey.get(`${m.kind}:${m.id}`) })).filter((m) => m.entity);
  if (!list.length) return null;
  const shown = dots ? list.slice(0, 6) : list;
  return (
    <div className={`timeline-mentions${dots ? ' timeline-mentions--dots' : ''}`}>
      {shown.map(({ kind, id, entity }) => (
        <a key={`${kind}:${id}`} href={TL.mentionHref(campaignId, kind, id)} className="timeline-mention"
          style={{ '--chip': entity.color }} title={entity.name} aria-label={dots ? entity.name : undefined}>
          {entity.image ? <img src={entity.image} alt="" className={kind === 'faction' ? 'is-faction' : ''} />
            : entity.icon ? <span className="timeline-mention__icon"><Icon name={entity.icon} size={12} /></span>
              : <span className="timeline-mention__dot" />}
          {!dots && <span className="timeline-mention__name">{entity.name}</span>}
        </a>
      ))}
      {dots && list.length > shown.length && <span className="timeline-mention__more">+{list.length - shown.length}</span>}
    </div>
  );
}

/** Événement de la liste : date, titre, étiquettes, fiches citées, description (repliée si longue), actions. */
function EventCard({ event, campaignId, byKey, folded, draggable, onToggleFold, onOpenNode, onEdit, onMove, onDelete }) {
  const long = TL.isLongDescription(event.description);
  return (
    <article className="timeline-card" aria-label={event.title}>
      <span className="timeline-item__marker" aria-hidden="true" />
      <header className="timeline-card__head">
        {draggable && (
          <span className="timeline-grip" draggable title={t('Glisser pour déplacer')} aria-hidden="true"
            onDragStart={(e) => {
              const item = e.currentTarget.closest('li');
              if (item) e.dataTransfer.setDragImage(item, 24, 24);
            }}>
            <Icon name="grip" size={16} />
          </span>
        )}
        <div className="timeline-card__titles">
          {event.date_label && <p className="timeline-date">{event.date_label}</p>}
          <h3>{event.title}</h3>
        </div>
        <div className="timeline-card__tools">
          <button type="button" className="icon-btn" onClick={onEdit} aria-label={t('Modifier')} title={t('Modifier')}>
            <Icon name="edit" size={16} />
          </button>
          <button type="button" className="icon-btn" onClick={onMove} aria-label={t('Déplacer vers…')} title={t('Déplacer vers…')}>
            <Icon name="swap" size={16} />
          </button>
          <button type="button" className="icon-btn icon-btn--danger" onClick={onDelete} aria-label={t('Supprimer')} title={t('Supprimer')}>
            <Icon name="trash" size={16} />
          </button>
        </div>
      </header>
      <Tags tags={event.tags} />
      <Mentions event={event} byKey={byKey} campaignId={campaignId} />
      {TL.plainText(event.description) && (
        <>
          <div className={`timeline-desc${long && folded ? ' is-folded' : ''}`}>
            <Markdown text={event.description} onOpenNode={onOpenNode} />
          </div>
          {long && (
            <button type="button" className="timeline-more" aria-expanded={!folded} onClick={onToggleFold}>
              {folded ? t('Lire la suite') : t('Replier')}
            </button>
          )}
        </>
      )}
    </article>
  );
}

/** Panneau de la frise : événement complet. */
function EventDetail({ event, campaignId, byKey, onOpenNode }) {
  return (
    <div className="timeline-detail" data-event-detail={event.id}>
      {event.date_label && <p className="timeline-date">{event.date_label}</p>}
      <h2 className="timeline-detail__title">{event.title}</h2>
      <Tags tags={event.tags} />
      <Mentions event={event} byKey={byKey} campaignId={campaignId} />
      {TL.plainText(event.description)
        ? <div className="timeline-desc"><Markdown text={event.description} onOpenNode={onOpenNode} /></div>
        : <p className="muted">{t('Pas encore de description : « Modifier » pour en écrire une.')}</p>}
    </div>
  );
}

/**
 * Frise ordinale : un emplacement régulier par événement, dans l'ordre (aucune échelle de temps). Défilement
 * horizontal à la molette, en glissant à la souris, au doigt ; flèches gauche et droite (Début, Fin) d'une carte à
 * l'autre ; « + » sur l'axe avant chaque carte et à la fin.
 */
function Frieze({ events, density, byKey, campaignId, selectedId, highlight, onSelect, onInsert }) {
  const scrollRef = useRef(null);
  const grab = useRef(null);
  const [active, setActive] = useState(0);
  const activeIndex = Math.min(active, Math.max(events.length - 1, 0));

  // Molette verticale : défilement horizontal (écouteur non passif, pour empêcher le défilement de la page).
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      if (e.ctrlKey || Math.abs(e.deltaX) >= Math.abs(e.deltaY) || el.scrollWidth <= el.clientWidth) return;
      e.preventDefault();
      el.scrollLeft += e.deltaY;
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  // Carte ouverte depuis ailleurs (?event=, création) : elle devient la carte active au clavier.
  useEffect(() => {
    const i = events.findIndex((e) => e.id === (highlight || selectedId));
    if (i >= 0) setActive(i);
  }, [highlight, selectedId, events]);

  const onPointerDown = (e) => {
    if (e.pointerType !== 'mouse' || e.button !== 0 || e.target.closest('button, a')) return;
    const el = scrollRef.current;
    grab.current = { x: e.clientX, left: el.scrollLeft };
    el.setPointerCapture?.(e.pointerId);
    el.classList.add('is-grabbing');
  };
  const onPointerMove = (e) => {
    if (!grab.current) return;
    scrollRef.current.scrollLeft = grab.current.left - (e.clientX - grab.current.x);
  };
  const endGrab = () => {
    grab.current = null;
    scrollRef.current?.classList.remove('is-grabbing');
  };

  const onKeyDown = (e) => {
    const i = Number(e.target.dataset?.index);
    if (!Number.isInteger(i)) return;
    const next = TL.nextIndex(i, e.key, events.length);
    if (next === null) return;
    e.preventDefault();
    setActive(next);
    const card = scrollRef.current?.querySelector(`[data-index="${next}"]`);
    card?.focus({ preventScroll: true });
    card?.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' });
  };

  return (
    <div className={`frieze frieze--${density}`} ref={scrollRef} onPointerDown={onPointerDown}
      onPointerMove={onPointerMove} onPointerUp={endGrab} onPointerCancel={endGrab}>
      <ol className="frieze__track" onKeyDown={onKeyDown} aria-label={t('Frise chronologique')}>
        {events.map((ev, i) => (
          <li key={ev.id} data-event={ev.id} style={{ '--tag': markerColor(ev) }}
            className={`frieze__slot ${i % 2 ? 'is-below' : 'is-above'}${ev.id === selectedId ? ' is-selected' : ''}${ev.id === highlight ? ' is-target' : ''}`}>
            <button type="button" className="frieze__insert" onClick={() => onInsert(ev.id)}
              aria-label={t('Insérer un événement avant « {title} »', { title: ev.title })}
              title={t('Insérer un événement ici')}>
              <Icon name="plus" size={13} />
            </button>
            <span className="frieze__dot" aria-hidden="true" />
            <div className="frieze__card">
              <button type="button" className="frieze__open" data-index={i} tabIndex={i === activeIndex ? 0 : -1}
                aria-current={ev.id === selectedId ? 'true' : undefined}
                onClick={() => { setActive(i); onSelect(ev.id); }}>
                {ev.date_label && <span className="frieze__date">{ev.date_label}</span>}
                <span className="frieze__title">{ev.title}</span>
              </button>
              {density === 'normal' && <Mentions event={ev} byKey={byKey} campaignId={campaignId} dots />}
            </div>
          </li>
        ))}
        <li className="frieze__slot frieze__slot--end">
          <button type="button" className="frieze__insert" onClick={() => onInsert(null)}
            aria-label={t('Ajouter un événement à la fin')} title={t('Ajouter un événement à la fin')}>
            <Icon name="plus" size={13} />
          </button>
        </li>
      </ol>
    </div>
  );
}

/** Formulaire d'un événement (création ou modification) : base_revision et conflit comme les fiches. */
function EventForm({ event = null, isNew = false, beforeId = null, entities, campaignId, onCancel, onSaved }) {
  const formId = useId();
  const entry = event || { id: `nouveau-${formId}`, revision: null, title: '', date_label: '', description: '', tags: [] };
  const { draft, set, save, conflict, resolve, keepAll, fieldKey } = useEntryDraft({
    kind: 'event', entry, keys: EVENT_KEYS, empty: (k) => (k === 'tags' ? [] : ''),
    update: (data) => api.updateEvent(entry.id, data), newVersion: false,
    label: entry.title || t('Nouvel événement'),
  });
  const [saving, setSaving] = useState(false);
  const run = useAction();
  const toast = useToast();
  const titleRef = useRef(null);
  useEffect(() => { titleRef.current?.focus({ preventScroll: true }); }, []);

  const submit = async (e) => {
    e.preventDefault();
    if (saving || conflict || !draft.title.trim()) return;
    setSaving(true);
    if (isNew) {
      const body = { title: draft.title, date_label: draft.date_label, description: draft.description, tags: draft.tags,
        ...(beforeId != null ? { before_id: beforeId } : {}) };
      const created = await run(() => api.createEvent(campaignId, body), t('Événement ajouté'));
      setSaving(false);
      if (created) onSaved(created);
      return;
    }
    const result = await run(save);
    setSaving(false);
    if (result === 'unchanged') onCancel();
    else if (result === 'saved') { toast(t('Événement enregistré'), 'success'); onSaved(); }
  };

  return (
    <form className="sheet-form timeline-form" onSubmit={submit} data-own-escape=""
      onKeyDown={(e) => {
        if (e.key === 's' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(e); }
        if (e.key === 'Escape' && !e.target.closest('.ProseMirror')) { e.stopPropagation(); onCancel(); }
      }}>
      <h2 className="sheet-form__title">{isNew ? t('Nouvel événement') : t('Modifier l’événement')}</h2>
      <ConflictPanel conflict={conflict} labels={LABELS} rich={RICH} onResolve={resolve} onKeepAll={keepAll} />
      <div className="form-grid timeline-form__grid">
        <Field label={t('Date')} htmlFor={`${formId}-date`}>
          <input id={`${formId}-date`} value={draft.date_label} maxLength={200}
            placeholder={t('742.M41, vers l’an 850, Séance 4…')} onChange={(e) => set('date_label')(e.target.value)} />
        </Field>
        <Field label={t('Titre')} htmlFor={`${formId}-title`}>
          <input id={`${formId}-title`} ref={titleRef} value={draft.title} required maxLength={200}
            onChange={(e) => set('title')(e.target.value)} />
        </Field>
      </div>
      <Field label={t('Description')} hint={t('Tapez @ pour citer une fiche, / pour insérer un bloc.')}>
        <RichField key={fieldKey('description')} value={draft.description} onChange={set('description')}
          entities={entities} label={t('Description')} />
      </Field>
      <Field label={t('Étiquettes')} htmlFor={`${formId}-tags`}>
        <TagInput id={`${formId}-tags`} value={draft.tags} onChange={set('tags')} />
      </Field>
      <div className="sheet-form__actions">
        <button type="button" className="btn btn--ghost" onClick={onCancel}>{t('Annuler')}</button>
        <button type="submit" className="btn btn--primary" disabled={saving || Boolean(conflict) || !draft.title.trim()}>
          {isNew ? t('Ajouter') : t('Enregistrer')}
        </button>
      </div>
    </form>
  );
}

/** Section « Chronologie » d'une fiche PNJ, faction ou lieu : événements qui la citent (entity.timeline). */
export function TimelineSection({ campaignId, items }) {
  if (!items?.length) return null;
  return (
    <section className="panel-section" data-entry-section="timeline" data-entry-label={t('Chronologie')}>
      <div className="panel-section__head"><h3>{t('Chronologie')}</h3><span className="count">{items.length}</span></div>
      <ul className="backlinks timeline-backlinks">
        {items.map((ev) => (
          <li key={ev.id}>
            <a href={TL.eventHref(campaignId, ev.id)}>
              <Icon name="timeline" size={15} />
              {ev.date_label && <span className="timeline-backlinks__date">{ev.date_label}</span>}
              <span>{ev.title}</span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
