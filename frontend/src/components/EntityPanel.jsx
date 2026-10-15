import { useEffect, useMemo, useState } from 'react';
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
      title: `Supprimer ${entity.name}`,
      message: 'La fiche, ses images et toutes ses relations seront supprimées. Cette action est définitive.',
    });
    if (!ok) return;
    const done = await run(() => api.deleteEntity(kind, id), `${entity.name} supprimé${kind === 'faction' ? 'e' : ''}`);
    if (done !== undefined) onDeleted();
  };

  return (
    <aside className={`${shell} panel--${kind}`} aria-label={`Fiche ${entity.name}`}>
      <div className="panel__scroll">
        {onClose && (
          <button type="button" className="icon-btn panel__close" onClick={onClose} aria-label="Fermer la fiche">
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
            <Icon name="edit" size={16} /> Modifier
          </button>
          <button type="button" className="btn btn--ghost" onClick={() => onStartLink(nid)}>
            <Icon name="link" size={16} /> Lier
          </button>
          {page && <EntrySideButton />}
          {mapAction && (
            <button type="button" className="icon-btn" onClick={mapAction.onClick} title={mapAction.label} aria-label={mapAction.label}>
              <Icon name={mapAction.icon} />
            </button>
          )}
          {onFocus && (
            <button type="button" className="icon-btn" onClick={() => onFocus(nid)} title="Centrer sur la carte"
              aria-label="Centrer sur la carte">
              <Icon name="target" />
            </button>
          )}
          <button type="button" className="icon-btn icon-btn--danger" onClick={remove} title="Supprimer"
            aria-label={`Supprimer ${entity.name}`}>
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
  const memberships = graph.memberships.filter((m) => (kind === 'npc' ? m.npc_id : m.faction_id) === entity.id);
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
        <div className="chips" aria-label="Factions">
          {memberships.map((m) => {
            const f = factionById.get(m.faction_id);
            return f && (
              <button key={m.id} type="button" className="chip" style={{ '--chip': f.color }}
                onClick={() => onOpenNode(nodeId('faction', f.id))}>
                <span className="chip__dot" />
                {f.name}
                {(m.role || m.is_leader) && <span className="chip__role">{m.role || 'Dirigeant'}</span>}
                {m.is_leader && <Icon name="star" size={12} title="Dirigeant" />}
              </button>
            );
          })}
        </div>
      )}

      {entity.places?.length > 0 && (
        <div className="chips chips--places" aria-label="Lieux">
          {entity.places.map((p) => (
            <a key={p.link_id} className="chip chip--place" href={`#/c/${entity.campaign_id}/lieux/${p.id}`}
              title={`${p.kind_label || placeKind(p.kind).label} : ouvrir le lieu`}>
              {p.kind_image ? <KindImage src={p.kind_image} size={13} />
                : <Icon name={p.kind_icon || placeKind(p.kind).icon} size={13} />}
              {p.name}
              {p.role && <span className="chip__role">{p.role}</span>}
            </a>
          ))}
        </div>
      )}

      {kind === 'faction' && (
        <section className="panel-section" data-entry-section="members" data-entry-label="Membres">
          <div className="panel-section__head"><h3>Membres</h3><span className="count">{memberships.length}</span></div>
          {memberships.length === 0 ? (
            <p className="muted">Aucun membre. Ajoutez une faction depuis la fiche d'un PNJ, ou liez un PNJ à cette faction.</p>
          ) : (
            <ul className="member-list">
              {[...memberships].sort((a, b) => Number(b.is_leader) - Number(a.is_leader)).map((m) => {
                const n = npcById.get(m.npc_id);
                return n && (
                  <li key={m.id}>
                    <button type="button" onClick={() => onOpenNode(nodeId('npc', n.id))}>
                      <img src={n.portrait_thumb || initialsAvatar(n.name, entity.color)} alt="" />
                      <span className="member-list__name">{n.status === 'mort' ? `† ${n.name}` : n.name}</span>
                      <span className="member-list__role">{m.role || (m.is_leader ? 'Dirigeant' : n.title)}</span>
                      {m.is_leader && <Icon name="star" size={13} title="Dirigeant" />}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}

      {sections.map((s) => (
        <section key={s.key} className={`panel-section${s.secret ? ' panel-section--secret' : ''}`}
          data-entry-section={s.key} data-entry-label={s.label}>
          <div className="panel-section__head">
            <h3>{s.secret && <Icon name="lock" size={14} />} {s.label}</h3>
            <SectionComment field={s.key} label={s.label} />
          </div>
          <div data-entry-field={s.key}>
            <Markdown text={entity[s.key]} resolve={resolveRef} onOpenNode={onOpenNode} />
          </div>
        </section>
      ))}

      {sections.length === 0 && !entity.summary && (
        <p className="muted sheet-empty">Fiche encore vierge : « Modifier » pour la compléter.</p>
      )}

      <RelationList kind={kind} entity={entity} graph={graph} nodeName={nodeName}
        onOpenNode={onOpenNode} onOpenEdge={onOpenEdge} />

      {entity.mentioned_in?.length > 0 && (
        <section className="panel-section" data-entry-section="mentioned" data-entry-label="Apparaît dans">
          <div className="panel-section__head"><h3>Apparaît dans</h3><span className="count">{entity.mentioned_in.length}</span></div>
          <ul className="backlinks">
            {entity.mentioned_in.map((d) => (
              <li key={d.id}>
                <a href={`#/c/${entity.campaign_id}/scenarios/${d.id}`}>
                  <Icon name={d.kind === 'campaign' ? 'book' : 'page'} size={15} />
                  <span>{d.kind === 'campaign' ? 'Fiche de campagne' : (d.title || 'Sans titre')}</span>
                  {d.kind !== 'campaign' && <span className={`status-dot status--${d.status}`} />}
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      {entity.maps?.length > 0 && (
        <section className="panel-section" data-entry-section="maps" data-entry-label="Sur les cartes">
          <div className="panel-section__head"><h3>Sur les cartes</h3><span className="count">{entity.maps.length}</span></div>
          <ul className="backlinks">
            {entity.maps.map((d) => (
              <li key={d.id}>
                <a href={`#/c/${entity.campaign_id}/carte?scenario=${d.id}`}>
                  <Icon name="graph" size={15} />
                  <span>{d.title || 'Sans titre'}</span>
                  <span className={`status-dot status--${d.status}`} />
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      {entity.tags.length > 0 && (
        <div className="tags" aria-label="Étiquettes">
          {entity.tags.map((t) => <span key={t} className="tag">{t}</span>)}
        </div>
      )}

      <div data-entry-section="gallery" data-entry-label="Images">
        <Gallery kind={kind} entity={entity} onChanged={onChanged} />
      </div>
    </>
  );
}

function RelationList({ kind, entity, graph, nodeName, onOpenNode, onOpenEdge }) {
  const types = typeIndex(graph);
  const own = graph.relations.filter((r) =>
    (r.source_kind === kind && r.source_id === entity.id) || (r.target_kind === kind && r.target_id === entity.id));
  return (
    <section className="panel-section" data-entry-section="relations" data-entry-label="Relations">
      <div className="panel-section__head"><h3>Relations</h3><span className="count">{own.length}</span></div>
      {own.length === 0 ? (
        <p className="muted">Aucune relation. Utilisez Lier pour en créer une.</p>
      ) : (
        <ul className="relation-list">
          {own.map((r) => {
            const outgoing = r.source_kind === kind && r.source_id === entity.id;
            const other = outgoing ? nodeId(r.target_kind, r.target_id) : nodeId(r.source_kind, r.source_id);
            const type = typeOf(types, r.type);
            return (
              <li key={r.id} style={{ '--rel': type.color }}>
                <button type="button" className="relation-list__type" onClick={() => onOpenEdge(`rel-${r.id}`)}
                  title="Voir la relation">
                  {type.label}
                  {r.directed && <span className="relation-list__dir">{outgoing ? 'vers' : 'de'}</span>}
                </button>
                <button type="button" className="relation-list__other" onClick={() => onOpenNode(other)}>
                  {nodeName(other)}
                </button>
                {r.secret && <span className="relation-list__secret"><Icon name="lock" size={13} title="Relation secrète" /></span>}
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
  const labels = Object.fromEntries([...fields.identity, fields.summary, ...fields.sections, { key: 'tags', label: 'Étiquettes' }]
    .map((f) => [f.key, f.label]));
  const rich = new Set(fields.sections.map((f) => f.key));

  const save = async (e) => {
    e.preventDefault();
    if (saving || conflict) return;
    setSaving(true);
    const result = await run(saveDraft);
    setSaving(false);
    if (result === 'unchanged') onCancel();
    else if (result === 'saved') { toast('Fiche enregistrée', 'success'); onSaved(); }
  };

  return (
    <form className="sheet-form" onSubmit={save}
      onKeyDown={(e) => { if (e.key === 's' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(e); } }}>
      <h2 className="sheet-form__title">Modifier la fiche</h2>
      <ConflictPanel conflict={conflict} labels={labels} rich={rich} resolveRef={resolveRef} onResolve={resolve}
        onKeepAll={keepAll} />
      <div className="form-grid">
        {fields.identity.map((f) => (
          <Field key={f.key} label={f.label} htmlFor={`f-${f.key}`}>
            {f.type === 'status' ? (
              <select id={`f-${f.key}`} value={draft[f.key]} onChange={(e) => set(f.key)(e.target.value)}>
                {Object.entries(NPC_STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
              </select>
            ) : f.type === 'color' ? (
              <ColorPicker id={`f-${f.key}`} value={draft[f.key]} onChange={set(f.key)} />
            ) : (
              <input id={`f-${f.key}`} value={draft[f.key]} required={f.required} maxLength={200}
                placeholder={f.placeholder} onChange={(e) => set(f.key)(e.target.value)} />
            )}
          </Field>
        ))}
      </div>
      <Field label={fields.summary.label} hint={fields.summary.hint} htmlFor="f-summary">
        <textarea id="f-summary" rows={2} maxLength={500} value={draft.summary}
          onChange={(e) => set('summary')(e.target.value)} />
      </Field>

      {kind === 'npc' && (
        <MembershipEditor npcId={entity.id} graph={graph} onChanged={onMembershipsChanged} />
      )}
      <EntityPlacesEditor kind={kind} entity={entity} onChanged={onMembershipsChanged} />

      {fields.sections.map((s) => (
        <Field key={s.key}
          label={<>{s.secret && <Icon name="lock" size={13} />} {s.label}</>}
          hint={s.key === fields.sections[0].key ? 'Tapez @ pour citer une fiche, / pour insérer un bloc.' : undefined}>
          <RichField key={fieldKey(s.key)} value={draft[s.key]} onChange={set(s.key)} entities={mentions}
            resolve={resolveRef} secret={s.secret} label={s.label} />
        </Field>
      ))}
      <Field label="Étiquettes" htmlFor="f-tags">
        <TagInput id="f-tags" value={draft.tags} onChange={set('tags')} />
      </Field>
      <Gallery kind={kind} entity={entity} onChanged={onMembershipsChanged} />
      <p className="field__hint">Les ajouts et suppressions d'images sont enregistrés immédiatement.</p>
      <div className="sheet-form__actions">
        <button type="button" className="btn btn--ghost" onClick={onCancel}>Annuler</button>
        <button type="submit" className="btn btn--primary" disabled={saving || Boolean(conflict)}
          title={conflict ? 'Choisissez d’abord pour chaque champ en conflit' : undefined}>
          {saving ? 'Enregistrement…' : 'Enregistrer'}
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
      <label className="swatch swatch--custom" title="Couleur personnalisée"
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
      <legend>Factions</legend>
      {mine.length === 0 && <p className="muted">Ce PNJ n'appartient à aucune faction.</p>}
      {mine.map((m) => {
        const f = factionById.get(m.faction_id);
        return (
          <div key={m.id} className="memberships__row" style={{ '--chip': f?.color }}>
            <span className="chip__dot" />
            <span className="memberships__name">{f?.name}</span>
            <input aria-label={`Rôle dans ${f?.name}`} placeholder="Rôle" defaultValue={m.role} maxLength={200}
              onBlur={async (e) => {
                if (e.target.value !== m.role) {
                  await run(() => api.updateMembership(m.id, { role: e.target.value }));
                  onChanged();
                }
              }} />
            <label className="check" title="Dirige cette faction">
              <input type="checkbox" checked={m.is_leader}
                onChange={async (e) => { await run(() => api.updateMembership(m.id, { is_leader: e.target.checked })); onChanged(); }} />
              Dirige
            </label>
            <button type="button" className="icon-btn icon-btn--small" aria-label={`Quitter ${f?.name}`}
              onClick={async () => { await run(() => api.deleteMembership(m.id)); onChanged(); }}>
              <Icon name="close" size={14} />
            </button>
          </div>
        );
      })}
      {available.length > 0 && (
        <div className="memberships__add">
          <select value={adding} onChange={(e) => setAdding(e.target.value)} aria-label="Faction à rejoindre">
            <option value="">Rejoindre une faction…</option>
            {available.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
          <button type="button" className="btn btn--small btn--ghost" onClick={add} disabled={!adding}>Ajouter</button>
        </div>
      )}
      <p className="field__hint">Les changements de factions sont enregistrés immédiatement.</p>
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
      .sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name, 'fr'))) {
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
      <legend>Lieux</legend>
      {mine.length === 0 && <p className="muted">{kind === 'npc' ? 'Ce PNJ' : 'Cette faction'} n’est relié{kind === 'npc' ? '' : 'e'} à aucun lieu.</p>}
      {mine.map((p) => (
        <div key={p.link_id} className="memberships__row" style={{ '--chip': '#8aa36b' }}>
          <span className="chip__dot" />
          <span className="memberships__name">{p.name}</span>
          <input aria-label={`Rôle à ${p.name}`} placeholder="Rôle" defaultValue={p.role} maxLength={80}
            list={`entity-roles-${kind}`}
            onBlur={async (e) => {
              if (e.target.value.trim() !== p.role) {
                await run(() => api.updatePlaceLink(p.link_id, { role: e.target.value.trim() }));
                onChanged();
              }
            }} />
          <button type="button" className="icon-btn icon-btn--small" aria-label={`Retirer de ${p.name}`}
            onClick={async () => { await run(() => api.deletePlaceLink(p.link_id)); onChanged(); }}>
            <Icon name="close" size={14} />
          </button>
        </div>
      ))}
      {options.length > 0 && (
        <div className="memberships__add place-links-editor__add">
          <select value={adding} onChange={(e) => setAdding(e.target.value)} aria-label="Lieu à ajouter">
            <option value="">Ajouter un lieu…</option>
            {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select>
          <input value={role} onChange={(e) => setRole(e.target.value)} placeholder="Rôle (facultatif)" maxLength={80}
            list={`entity-roles-${kind}`} aria-label="Rôle" />
          <button type="button" className="btn btn--small btn--ghost" onClick={add} disabled={!adding}>Ajouter</button>
        </div>
      )}
      {places && places.length === 0 && (
        <p className="field__hint">Aucun lieu dans cette campagne : créez-en dans l’onglet Lieux.</p>
      )}
      <datalist id={`entity-roles-${kind}`}>{PLACE_ROLES[kind].map((r) => <option key={r} value={r} />)}</datalist>
    </fieldset>
  );
}
