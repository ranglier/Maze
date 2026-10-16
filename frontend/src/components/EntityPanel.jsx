import { useEffect, useId, useMemo, useState } from 'react';
import { api } from '../api.js';
import { FACTION_COLORS, KIND_LABEL, NPC_STATUS, PLACE_ROLES, placeKind } from '../constants.js';
import { typeIndex, typeOf } from '../relationTypes.js';
import { ENTITY_FIELDS, editableKeys } from '../fields.js';
import { initialsAvatar, nodeId } from '../util.js';
import { EntrySideButton, SectionComment } from './EntryNotes.jsx';
import Gallery from './Gallery.jsx';
import RichField from './RichField.jsx';
import { mentionEntities } from './mentionEntities.js';
import { ConflictPanel, useEntryDraft } from './EntryDraft.jsx';
import { Field, Icon, KindImage, Markdown, TagInput, useAction, useConfirm, useToast } from './ui.jsx';
import { N_, compareText, pt, t, tn } from '../i18n/index.js';

/**
 * Fiche PNJ / faction. Sur la carte : panneau latéral. Dans l'index des personnages (`page`) :
 * pleine page, sans bouton de fermeture ni de centrage.
 * `mapAction` (facultatif) : action propre à la carte du scénario ({ label, icon, onClick }).
 */
export default function EntityPanel({
  kind, id, graph, version, startInEdit, nodeName, resolveRef, page = false, mapAction = null,
  onClose, onOpenNode, onOpenEdge, onFocus, onStartLink, onChanged, onDeleted, onEditingChange,
}) {
  const [entity, setEntity] = useState(null);
  const [editing, setEditing] = useState(Boolean(startInEdit));
  const [loadError, setLoadError] = useState(null);
  const run = useAction();
  const confirm = useConfirm();
  const nid = nodeId(kind, id);

  const reload = () => api.getEntity(kind, id).then(setEntity).catch((e) => setLoadError(e.message));

  useEffect(() => { setEditing(Boolean(startInEdit)); }, [kind, id, startInEdit]);
  useEffect(() => { onEditingChange?.(editing); }, [editing]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setLoadError(null); reload(); }, [kind, id, version]);

  const refresh = () => { reload(); onChanged(); };

  const shell = page ? 'panel panel--page' : 'panel';
  if (loadError) return <aside className={shell}><p className="panel__error">{loadError}</p></aside>;
  if (!entity || entity.id !== id) return <aside className={`${shell} panel--loading`} aria-busy="true" />;

  const remove = async () => {
    const ok = await confirm({
      title: t('Supprimer {name}', { name: entity.name }),
      message: t('La fiche, ses images et toutes ses relations seront supprimées. Cette action est définitive.'),
    });
    if (!ok) return;
    const done = await run(() => api.deleteEntity(kind, id),
      kind === 'faction' ? t('{name} supprimée', { name: entity.name }) : t('{name} supprimé', { name: entity.name }));
    if (done !== undefined) onDeleted();
  };

  return (
    <aside className={`${shell} panel--${kind}`} aria-label={t('Fiche {name}', { name: entity.name })}>
      <div className="panel__scroll">
        {onClose && (
          <button type="button" className="icon-btn panel__close" onClick={onClose} aria-label={t('Fermer la fiche')}>
            <Icon name="close" />
          </button>
        )}
        {editing ? (
          <EntityForm kind={kind} entity={entity} graph={graph} resolveRef={resolveRef}
            onCancel={() => setEditing(false)}
            onSaved={() => { setEditing(false); refresh(); }}
            onMembershipsChanged={refresh} />
        ) : (
          <EntityView kind={kind} entity={entity} graph={graph} nodeName={nodeName} resolveRef={resolveRef}
            onOpenNode={onOpenNode} onOpenEdge={onOpenEdge} onChanged={refresh} />
        )}
      </div>
      {!editing && (
        <footer className="panel__actions">
          <button type="button" className="btn btn--primary" onClick={() => setEditing(true)}>
            <Icon name="edit" size={16} /> {t('Modifier')}
          </button>
          <button type="button" className="btn btn--ghost" onClick={() => onStartLink(nid)}>
            <Icon name="link" size={16} /> {t('Lier')}
          </button>
          {page && <EntrySideButton />}
          {mapAction && (
            <button type="button" className="icon-btn" onClick={mapAction.onClick} title={mapAction.label} aria-label={mapAction.label}>
              <Icon name={mapAction.icon} />
            </button>
          )}
          {onFocus && (
            <button type="button" className="icon-btn" onClick={() => onFocus(nid)} title={t('Centrer sur la carte')}
              aria-label={t('Centrer sur la carte')}>
              <Icon name="target" />
            </button>
          )}
          <button type="button" className="icon-btn icon-btn--danger" onClick={remove} title={t('Supprimer')}
            aria-label={t('Supprimer {name}', { name: entity.name })}>
            <Icon name="trash" />
          </button>
        </footer>
      )}
    </aside>
  );
}

// --- Consultation -------------------------------------------------------------

function EntityView({ kind, entity, graph, nodeName, resolveRef, onOpenNode, onOpenEdge, onChanged }) {
  const fields = ENTITY_FIELDS[kind];
  const coverId = kind === 'npc' ? entity.portrait_image_id : entity.emblem_image_id;
  const cover = entity.images.find((i) => i.id === coverId);
  const factionById = useMemo(() => new Map(graph.factions.map((f) => [f.id, f])), [graph]);
  const npcById = useMemo(() => new Map(graph.npcs.map((n) => [n.id, n])), [graph]);
  // PNJ : ses factions. Faction : ses membres (PNJ et sous-factions) ; parents : factions dont elle est membre.
  const memberships = graph.memberships.filter((m) => (kind === 'npc' ? m.npc_id : m.faction_id) === entity.id);
  const parents = kind === 'faction' ? graph.memberships.filter((m) => m.member_faction_id === entity.id) : [];
  const ringColor = kind === 'npc'
    ? (factionById.get((memberships.find((m) => m.is_leader) || memberships[0])?.faction_id)?.color || '#5d6b80')
    : entity.color;

  const subtitle = kind === 'npc' ? entity.title : entity.kind;
  const sections = fields.sections.filter((s) => entity[s.key]?.trim());

  return (
    <>
      <header className="sheet-head" style={{ '--ring': ringColor }}>
        <div className={`sheet-head__media sheet-head__media--${kind}`}>
          {cover ? <img src={cover.thumb_url} alt="" />
            : kind === 'npc' ? <img src={initialsAvatar(entity.name, ringColor)} alt="" />
              : <span className="sheet-head__seal" style={{ background: entity.color }} />}
        </div>
        <div className="sheet-head__text">
          <p className="sheet-head__kind">{KIND_LABEL[kind]}</p>
          <h2>{entity.name}</h2>
          {subtitle && <p className="sheet-head__subtitle">{subtitle}</p>}
          {kind === 'npc' && entity.status !== 'vivant' && (
            <span className={`status-chip status-chip--${entity.status}`}>{NPC_STATUS[entity.status].label}</span>
          )}
        </div>
      </header>

      {entity.summary && <p className="sheet-summary" data-entry-field="summary">{entity.summary}</p>}

      {kind === 'npc' && memberships.length > 0 && (
        <div className="chips" aria-label={t('Factions')}>
          {memberships.map((m) => {
            const f = factionById.get(m.faction_id);
            return f && (
              <button key={m.id} type="button" className="chip" style={{ '--chip': f.color }}
                onClick={() => onOpenNode(nodeId('faction', f.id))}>
                <span className="chip__dot" />
                {f.name}
                {(m.role || m.is_leader) && <span className="chip__role">{m.role || t('Dirigeant')}</span>}
                {m.is_leader && <Icon name="star" size={12} title={t('Dirigeant')} />}
              </button>
            );
          })}
        </div>
      )}

      {entity.places?.length > 0 && (
        <div className="chips chips--places" aria-label={t('Lieux')}>
          {entity.places.map((p) => (
            <a key={p.link_id} className="chip chip--place" href={`#/c/${entity.campaign_id}/lieux/${p.id}`}
              title={t('{kind} : ouvrir le lieu', { kind: p.kind_label || placeKind(p.kind).label })}>
              {p.kind_image ? <KindImage src={p.kind_image} size={13} />
                : <Icon name={p.kind_icon || placeKind(p.kind).icon} size={13} />}
              {p.name}
              {p.role && <span className="chip__role">{p.role}</span>}
            </a>
          ))}
        </div>
      )}

      {parents.length > 0 && (
        <section className="panel-section" data-entry-section="member-of" data-entry-label={t('Membre de')}>
          <div className="panel-section__head"><h3>{t('Membre de')}</h3><span className="count">{parents.length}</span></div>
          <div className="chips chips--parents">
            {parents.map((m) => {
              const f = factionById.get(m.faction_id);
              return f && (
                <button key={m.id} type="button" className="chip" style={{ '--chip': f.color }}
                  onClick={() => onOpenNode(nodeId('faction', f.id))}>
                  <span className="chip__dot" />
                  {f.name}
                  {m.role && <span className="chip__role">{m.role}</span>}
                </button>
              );
            })}
          </div>
        </section>
      )}

      {kind === 'faction' && (
        <MembersSection key={entity.id} faction={entity} memberships={memberships} npcById={npcById}
          factionById={factionById} onOpenNode={onOpenNode} />
      )}

      {sections.map((s) => (
        <section key={s.key} className={`panel-section${s.secret ? ' panel-section--secret' : ''}`}
          data-entry-section={s.key} data-entry-label={t(s.label)}>
          <div className="panel-section__head">
            <h3>{s.secret && <Icon name="lock" size={14} />} {t(s.label)}</h3>
            <SectionComment field={s.key} label={s.label} />
          </div>
          <div data-entry-field={s.key}>
            <Markdown text={entity[s.key]} resolve={resolveRef} onOpenNode={onOpenNode} />
          </div>
        </section>
      ))}

      {sections.length === 0 && !entity.summary && (
        <p className="muted sheet-empty">{t('Fiche encore vierge : « Modifier » pour la compléter.')}</p>
      )}

      <RelationList kind={kind} entity={entity} graph={graph} nodeName={nodeName}
        onOpenNode={onOpenNode} onOpenEdge={onOpenEdge} />

      {entity.mentioned_in?.length > 0 && (
        <section className="panel-section" data-entry-section="mentioned" data-entry-label={t('Apparaît dans')}>
          <div className="panel-section__head"><h3>{t('Apparaît dans')}</h3><span className="count">{entity.mentioned_in.length}</span></div>
          <ul className="backlinks">
            {entity.mentioned_in.map((d) => (
              <li key={d.id}>
                <a href={`#/c/${entity.campaign_id}/scenarios/${d.id}`}>
                  <Icon name={d.kind === 'campaign' ? 'book' : 'page'} size={15} />
                  <span>{d.kind === 'campaign' ? t('Fiche de campagne') : (d.title || t('Sans titre'))}</span>
                  {d.kind !== 'campaign' && <span className={`status-dot status--${d.status}`} />}
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      {entity.maps?.length > 0 && (
        <section className="panel-section" data-entry-section="maps" data-entry-label={t('Sur les cartes')}>
          <div className="panel-section__head"><h3>{t('Sur les cartes')}</h3><span className="count">{entity.maps.length}</span></div>
          <ul className="backlinks">
            {entity.maps.map((d) => (
              <li key={d.id}>
                <a href={`#/c/${entity.campaign_id}/carte?scenario=${d.id}`}>
                  <Icon name="graph" size={15} />
                  <span>{d.title || t('Sans titre')}</span>
                  <span className={`status-dot status--${d.status}`} />
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      {entity.tags.length > 0 && (
        <div className="tags" aria-label={t('Étiquettes')}>
          {entity.tags.map((tag) => <span key={tag} className="tag">{tag}</span>)}
        </div>
      )}

      <div data-entry-section="gallery" data-entry-label={t('Images')}>
        <Gallery kind={kind} entity={entity} onChanged={onChanged} />
      </div>
    </>
  );
}

/**
 * Membres d'une faction : PNJ (chefs en tête) puis sous-factions. Section repliable, repliée par défaut (état
 * propre à la fiche affichée), compteur visible dans l'en-tête ; deux colonnes, une seule dans un panneau étroit.
 */
function MembersSection({ faction, memberships, npcById, factionById, onOpenNode }) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const rank = (m) => (m.member_faction_id != null ? 2 : m.is_leader ? 0 : 1);
  const members = [...memberships].sort((a, b) => rank(a) - rank(b));
  const subCount = members.filter((m) => m.member_faction_id != null).length;
  const summary = subCount
    ? t('{npcs} et {factions}', { npcs: tn('{n} PNJ', '{n} PNJ', members.length - subCount), factions: tn('{n} sous-faction', '{n} sous-factions', subCount) })
    : tn('{n} PNJ', '{n} PNJ', members.length);
  return (
    <section className={`panel-section panel-section--fold${open ? ' is-open' : ''}`} data-entry-section="members"
      data-entry-label={t('Membres')}>
      <div className="panel-section__head">
        <h3>
          <button type="button" className="panel-section__toggle" aria-expanded={open} aria-controls={bodyId}
            aria-label={t('Membres : {detail}', { detail: summary })}
            onClick={() => setOpen((o) => !o)} title={open ? t('Replier la liste des membres') : t('Afficher la liste des membres')}>
            <span className="panel-section__chevron" aria-hidden="true"><Icon name="chevron" size={14} /></span>
            {t('Membres')}
            <span className="count" title={summary}>{members.length}</span>
          </button>
        </h3>
      </div>
      <div id={bodyId} className="panel-section__body" hidden={!open}>
        {members.length === 0 ? (
          <p className="muted">{t('Aucun membre. Ajoutez des PNJ depuis leur fiche, ou des sous-factions depuis la fiche de chacune (Membre de).')}</p>
        ) : (
          <ul className="member-list member-list--columns">
            {members.map((m) => {
              if (m.member_faction_id != null) {
                const f = factionById.get(m.member_faction_id);
                return f && (
                  <li key={m.id} className="member-list__faction">
                    <button type="button" onClick={() => onOpenNode(nodeId('faction', f.id))}
                      title={m.role ? `${f.name} · ${m.role}` : f.name}>
                      {f.emblem_thumb ? <img src={f.emblem_thumb} alt="" className="is-faction" />
                        : <span className="member-list__seal" style={{ background: f.color }}><Icon name="banner" size={16} /></span>}
                      <span className="member-list__name">{f.name}</span>
                      <span className="member-list__role">
                        <span className="member-list__tag">{t('Sous-faction')}</span>{m.role && ` · ${m.role}`}
                      </span>
                    </button>
                  </li>
                );
              }
              const n = npcById.get(m.npc_id);
              return n && (
                <li key={m.id}>
                  <button type="button" onClick={() => onOpenNode(nodeId('npc', n.id))}
                    title={[n.name, m.role || n.title].filter(Boolean).join(' · ')}>
                    <img src={n.portrait_thumb || initialsAvatar(n.name, faction.color)} alt="" />
                    <span className="member-list__name">{n.status === 'mort' ? `† ${n.name}` : n.name}</span>
                    <span className="member-list__role">{m.role || (m.is_leader ? t('Dirigeant') : n.title)}</span>
                    {m.is_leader && <Icon name="star" size={13} title={t('Dirigeant')} />}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}

function RelationList({ kind, entity, graph, nodeName, onOpenNode, onOpenEdge }) {
  const types = typeIndex(graph);
  const own = graph.relations.filter((r) =>
    (r.source_kind === kind && r.source_id === entity.id) || (r.target_kind === kind && r.target_id === entity.id));
  return (
    <section className="panel-section" data-entry-section="relations" data-entry-label={t('Relations')}>
      <div className="panel-section__head"><h3>{t('Relations')}</h3><span className="count">{own.length}</span></div>
      {own.length === 0 ? (
        <p className="muted">{t('Aucune relation. Utilisez Lier pour en créer une.')}</p>
      ) : (
        <ul className="relation-list">
          {own.map((r) => {
            const outgoing = r.source_kind === kind && r.source_id === entity.id;
            const other = outgoing ? nodeId(r.target_kind, r.target_id) : nodeId(r.source_kind, r.source_id);
            const type = typeOf(types, r.type);
            return (
              <li key={r.id} style={{ '--rel': type.color }}>
                <button type="button" className="relation-list__type" onClick={() => onOpenEdge(`rel-${r.id}`)}
                  title={t('Voir la relation')}>
                  {type.label}
                  {r.directed && <span className="relation-list__dir">{outgoing ? pt('direction', 'vers') : pt('direction', 'de')}</span>}
                </button>
                <button type="button" className="relation-list__other" onClick={() => onOpenNode(other)}>
                  {nodeName(other)}
                </button>
                {r.secret && <span className="relation-list__secret"><Icon name="lock" size={13} title={t('Relation secrète')} /></span>}
                {r.label && r.label !== type.label && <span className="relation-list__label">{r.label}</span>}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// --- Édition ------------------------------------------------------------------

function EntityForm({ kind, entity, graph, resolveRef, onCancel, onSaved, onMembershipsChanged }) {
  const mentions = useMemo(() => mentionEntities(graph), [graph]);
  const fields = ENTITY_FIELDS[kind];
  const keys = editableKeys(kind);
  // Champs modifiés seulement, révision de départ, conflit montré sans perdre la saisie (audit M1, M2, M3).
  const { draft, set, save: saveDraft, conflict, resolve, keepAll, fieldKey } = useEntryDraft({
    kind, entry: entity, keys, empty: () => '' });
  const [saving, setSaving] = useState(false);
  const run = useAction();
  const toast = useToast();
  const labels = Object.fromEntries([...fields.identity, fields.summary, ...fields.sections, { key: 'tags', label: N_('Étiquettes') }]
    .map((f) => [f.key, f.label]));
  const rich = new Set(fields.sections.map((f) => f.key));

  const save = async (e) => {
    e.preventDefault();
    if (saving || conflict) return;
    setSaving(true);
    const result = await run(saveDraft);
    setSaving(false);
    if (result === 'unchanged') onCancel();
    else if (result === 'saved') { toast(t('Fiche enregistrée'), 'success'); onSaved(); }
  };

  return (
    <form className="sheet-form" onSubmit={save}
      onKeyDown={(e) => { if (e.key === 's' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(e); } }}>
      <h2 className="sheet-form__title">{t('Modifier la fiche')}</h2>
      <ConflictPanel conflict={conflict} labels={labels} rich={rich} resolveRef={resolveRef} onResolve={resolve}
        onKeepAll={keepAll} />
      <div className="form-grid">
        {fields.identity.map((f) => (
          <Field key={f.key} label={t(f.label)} htmlFor={`f-${f.key}`}>
            {f.type === 'status' ? (
              <select id={`f-${f.key}`} value={draft[f.key]} onChange={(e) => set(f.key)(e.target.value)}>
                {Object.entries(NPC_STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
              </select>
            ) : f.type === 'color' ? (
              <ColorPicker id={`f-${f.key}`} value={draft[f.key]} onChange={set(f.key)} />
            ) : (
              <input id={`f-${f.key}`} value={draft[f.key]} required={f.required} maxLength={200}
                placeholder={f.placeholder && t(f.placeholder)} onChange={(e) => set(f.key)(e.target.value)} />
            )}
          </Field>
        ))}
      </div>
      <Field label={t(fields.summary.label)} hint={fields.summary.hint && t(fields.summary.hint)} htmlFor="f-summary">
        <textarea id="f-summary" rows={2} maxLength={500} value={draft.summary}
          onChange={(e) => set('summary')(e.target.value)} />
      </Field>

      {kind === 'npc' && (
        <MembershipEditor npcId={entity.id} graph={graph} onChanged={onMembershipsChanged} />
      )}
      {kind === 'faction' && (
        <FactionParentsEditor factionId={entity.id} graph={graph} onChanged={onMembershipsChanged} />
      )}
      <EntityPlacesEditor kind={kind} entity={entity} onChanged={onMembershipsChanged} />

      {fields.sections.map((s) => (
        <Field key={s.key}
          label={<>{s.secret && <Icon name="lock" size={13} />} {t(s.label)}</>}
          hint={s.key === fields.sections[0].key ? t('Tapez @ pour citer une fiche, / pour insérer un bloc.') : undefined}>
          <RichField key={fieldKey(s.key)} value={draft[s.key]} onChange={set(s.key)} entities={mentions}
            resolve={resolveRef} secret={s.secret} label={t(s.label)} />
        </Field>
      ))}
      <Field label={t('Étiquettes')} htmlFor="f-tags">
        <TagInput id="f-tags" value={draft.tags} onChange={set('tags')} />
      </Field>
      <Gallery kind={kind} entity={entity} onChanged={onMembershipsChanged} />
      <p className="field__hint">{t('Les ajouts et suppressions d\'images sont enregistrés immédiatement.')}</p>
      <div className="sheet-form__actions">
        <button type="button" className="btn btn--ghost" onClick={onCancel}>{t('Annuler')}</button>
        <button type="submit" className="btn btn--primary" disabled={saving || Boolean(conflict)}
          title={conflict ? t('Choisissez d’abord pour chaque champ en conflit') : undefined}>
          {saving ? t('Enregistrement…') : t('Enregistrer')}
        </button>
      </div>
    </form>
  );
}

export function ColorPicker({ id, value, onChange, palette = FACTION_COLORS }) {
  return (
    <div className="color-picker" role="radiogroup" aria-labelledby={id}>
      {palette.map((c) => (
        <button key={c} type="button" role="radio" aria-checked={value === c} aria-label={c}
          className={`swatch${value === c ? ' is-active' : ''}`} style={{ background: c }}
          onClick={() => onChange(c)} />
      ))}
      <label className="swatch swatch--custom" title={t('Couleur personnalisée')}
        style={{ background: palette.includes(value) ? undefined : value }}>
        <input id={id} type="color" value={value} onChange={(e) => onChange(e.target.value)} />
        <Icon name="plus" size={12} />
      </label>
    </div>
  );
}

function MembershipEditor({ npcId, graph, onChanged }) {
  const run = useAction();
  const mine = graph.memberships.filter((m) => m.npc_id === npcId);
  const available = graph.factions.filter((f) => !mine.some((m) => m.faction_id === f.id));
  const [adding, setAdding] = useState('');
  const factionById = new Map(graph.factions.map((f) => [f.id, f]));

  const add = async () => {
    if (!adding) return;
    await run(() => api.createMembership({ npc_id: npcId, faction_id: Number(adding) }));
    setAdding('');
    onChanged();
  };

  return (
    <fieldset className="memberships">
      <legend>{t('Factions')}</legend>
      {mine.length === 0 && <p className="muted">{t('Ce PNJ n\'appartient à aucune faction.')}</p>}
      {mine.map((m) => {
        const f = factionById.get(m.faction_id);
        return (
          <div key={m.id} className="memberships__row" style={{ '--chip': f?.color }}>
            <span className="chip__dot" />
            <span className="memberships__name">{f?.name}</span>
            <input aria-label={t('Rôle dans {name}', { name: f?.name })} placeholder={t('Rôle')} defaultValue={m.role} maxLength={200}
              onBlur={async (e) => {
                if (e.target.value !== m.role) {
                  await run(() => api.updateMembership(m.id, { role: e.target.value }));
                  onChanged();
                }
              }} />
            <label className="check" title={t('Dirige cette faction')}>
              <input type="checkbox" checked={m.is_leader}
                onChange={async (e) => { await run(() => api.updateMembership(m.id, { is_leader: e.target.checked })); onChanged(); }} />
              {pt('leader', 'Dirige')}
            </label>
            <button type="button" className="icon-btn icon-btn--small" aria-label={t('Quitter {name}', { name: f?.name })}
              onClick={async () => { await run(() => api.deleteMembership(m.id)); onChanged(); }}>
              <Icon name="close" size={14} />
            </button>
          </div>
        );
      })}
      {available.length > 0 && (
        <div className="memberships__add">
          <select value={adding} onChange={(e) => setAdding(e.target.value)} aria-label={t('Faction à rejoindre')}>
            <option value="">{t('Rejoindre une faction…')}</option>
            {available.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
          <button type="button" className="btn btn--small btn--ghost" onClick={add} disabled={!adding}>{t('Ajouter')}</button>
        </div>
      )}
      <p className="field__hint">{t('Les changements de factions sont enregistrés immédiatement.')}</p>
    </fieldset>
  );
}

/**
 * Factions dont cette faction est membre (sous-faction), avec un rôle ; plusieurs possibles, enregistrées tout de
 * suite. Ni elle-même ni une de ses sous-factions (directes ou non) ne sont proposées : le serveur refuse les cycles.
 */
function FactionParentsEditor({ factionId, graph, onChanged }) {
  const run = useAction();
  const mine = graph.memberships.filter((m) => m.member_faction_id === factionId);
  const descendants = useMemo(() => {
    const found = new Set([factionId]);
    const stack = [factionId];
    while (stack.length) {
      const cur = stack.pop();
      for (const m of graph.memberships) {
        if (m.faction_id === cur && m.member_faction_id != null && !found.has(m.member_faction_id)) {
          found.add(m.member_faction_id);
          stack.push(m.member_faction_id);
        }
      }
    }
    return found;
  }, [graph, factionId]);
  const available = graph.factions.filter((f) => !descendants.has(f.id) && !mine.some((m) => m.faction_id === f.id));
  const [adding, setAdding] = useState('');
  const factionById = new Map(graph.factions.map((f) => [f.id, f]));

  const add = async () => {
    if (!adding) return;
    const done = await run(() => api.createMembership({ member_faction_id: factionId, faction_id: Number(adding) }));
    if (done === undefined) return;
    setAdding('');
    onChanged();
  };

  return (
    <fieldset className="memberships memberships--parents">
      <legend>{t('Membre de')}</legend>
      {mine.length === 0 && <p className="muted">{t('Cette faction n’est membre d’aucune autre faction.')}</p>}
      {mine.map((m) => {
        const f = factionById.get(m.faction_id);
        return (
          <div key={m.id} className="memberships__row memberships__row--parent" style={{ '--chip': f?.color }}>
            <span className="chip__dot" />
            <span className="memberships__name">{f?.name}</span>
            <input aria-label={t('Rôle dans {name}', { name: f?.name })} placeholder={t('Rôle')} defaultValue={m.role} maxLength={200}
              onBlur={async (e) => {
                if (e.target.value !== m.role) {
                  await run(() => api.updateMembership(m.id, { role: e.target.value }));
                  onChanged();
                }
              }} />
            <button type="button" className="icon-btn icon-btn--small" aria-label={t('Quitter {name}', { name: f?.name })}
              onClick={async () => { await run(() => api.deleteMembership(m.id)); onChanged(); }}>
              <Icon name="close" size={14} />
            </button>
          </div>
        );
      })}
      {available.length > 0 && (
        <div className="memberships__add">
          <select value={adding} onChange={(e) => setAdding(e.target.value)} aria-label={t('Faction parente à ajouter')}>
            <option value="">{t('Rejoindre une faction…')}</option>
            {available.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
          <button type="button" className="btn btn--small btn--ghost" onClick={add} disabled={!adding}>{t('Ajouter')}</button>
        </div>
      )}
      <p className="field__hint">{t('Sous-faction d’une ou plusieurs factions : elle apparaît parmi leurs membres. Enregistré immédiatement.')}</p>
    </fieldset>
  );
}

/** Lieux où se trouve le PNJ ou la faction, avec leur rôle (enregistrés tout de suite). */
function EntityPlacesEditor({ kind, entity, onChanged }) {
  const run = useAction();
  const [places, setPlaces] = useState(null);
  const [adding, setAdding] = useState('');
  const [role, setRole] = useState('');
  useEffect(() => {
    api.places(entity.campaign_id).then((d) => setPlaces(d.places)).catch(() => setPlaces([]));
  }, [entity.campaign_id]);

  const mine = entity.places || [];
  const taken = new Set(mine.map((p) => p.id));
  // Lieux en arbre (indentés), sans ceux déjà reliés.
  const options = [];
  const walk = (parentId, depth) => {
    for (const p of (places || []).filter((x) => (x.parent_id ?? null) === parentId)
      .sort((a, b) => a.sort_order - b.sort_order || compareText(a.name, b.name))) {
      if (!taken.has(p.id)) options.push({ id: p.id, label: `${'\u00a0\u00a0\u00a0'.repeat(depth)}${p.name}` });
      walk(p.id, depth + 1);
    }
  };
  walk(null, 0);

  const add = async () => {
    if (!adding) return;
    const done = await run(() => api.createPlaceLink(Number(adding), { entity_kind: kind, entity_id: entity.id, role: role.trim() }));
    if (done === undefined) return;
    setAdding('');
    setRole('');
    onChanged();
  };

  return (
    <fieldset className="memberships">
      <legend>{t('Lieux')}</legend>
      {mine.length === 0 && (
        <p className="muted">{kind === 'npc' ? t('Ce PNJ n’est relié à aucun lieu.') : t('Cette faction n’est reliée à aucun lieu.')}</p>
      )}
      {mine.map((p) => (
        <div key={p.link_id} className="memberships__row" style={{ '--chip': '#8aa36b' }}>
          <span className="chip__dot" />
          <span className="memberships__name">{p.name}</span>
          <input aria-label={t('Rôle à {name}', { name: p.name })} placeholder={t('Rôle')} defaultValue={p.role} maxLength={80}
            list={`entity-roles-${kind}`}
            onBlur={async (e) => {
              if (e.target.value.trim() !== p.role) {
                await run(() => api.updatePlaceLink(p.link_id, { role: e.target.value.trim() }));
                onChanged();
              }
            }} />
          <button type="button" className="icon-btn icon-btn--small" aria-label={t('Retirer de {name}', { name: p.name })}
            onClick={async () => { await run(() => api.deletePlaceLink(p.link_id)); onChanged(); }}>
            <Icon name="close" size={14} />
          </button>
        </div>
      ))}
      {options.length > 0 && (
        <div className="memberships__add place-links-editor__add">
          <select value={adding} onChange={(e) => setAdding(e.target.value)} aria-label={t('Lieu à ajouter')}>
            <option value="">{t('Ajouter un lieu…')}</option>
            {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
          <input value={role} onChange={(e) => setRole(e.target.value)} placeholder={t('Rôle (facultatif)')} maxLength={80}
            list={`entity-roles-${kind}`} aria-label={t('Rôle')} />
          <button type="button" className="btn btn--small btn--ghost" onClick={add} disabled={!adding}>{t('Ajouter')}</button>
        </div>
      )}
      {places && places.length === 0 && (
        <p className="field__hint">{t('Aucun lieu dans cette campagne : créez-en dans l’onglet Lieux.')}</p>
      )}
      <datalist id={`entity-roles-${kind}`}>{PLACE_ROLES[kind].map((r) => <option key={r} value={r} />)}</datalist>
    </fieldset>
  );
}
