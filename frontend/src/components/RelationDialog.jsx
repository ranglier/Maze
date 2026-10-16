import { useMemo, useState } from 'react';
import { api } from '../api.js';
import { typeIndex } from '../relationTypes.js';
import { nodeId, parseNodeId } from '../util.js';
import RelationTypePicker from './RelationTypePicker.jsx';
import { Field, Modal, useAction } from './ui.jsx';
import { t } from '../i18n/index.js';

const MEMBER = 'membre';

export default function RelationDialog({
  campaignId, graph, relation, source, target, onClose, onSaved, onTypesChanged,
}) {
  const run = useAction();
  const editing = Boolean(relation);
  const types = useMemo(() => typeIndex(graph), [graph]);
  const typeList = graph.relation_types || [];
  const [form, setForm] = useState(() => (relation ? {
    source: nodeId(relation.source_kind, relation.source_id),
    target: nodeId(relation.target_kind, relation.target_id),
    type: relation.type,
    label: relation.label,
    description: relation.description,
    directed: relation.directed,
    secret: relation.secret,
    role: '',
    is_leader: false,
  } : {
    source: source || '',
    target: target || '',
    type: types.has('allie') ? 'allie' : (typeList[0]?.key || 'autre'),
    label: '',
    description: '',
    directed: false,
    secret: false,
    role: '',
    is_leader: false,
  }));
  const [saving, setSaving] = useState(false);
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  const options = useMemo(() => ({
    npcs: graph.npcs.map((n) => ({ value: nodeId('npc', n.id), label: n.name })),
    factions: graph.factions.map((f) => ({ value: nodeId('faction', f.id), label: f.name })),
  }), [graph]);

  const kinds = [form.source, form.target].filter(Boolean).map((v) => parseNodeId(v).kind);
  const canBeMember = !editing && kinds.length === 2 && kinds.includes('npc') && kinds.includes('faction');
  const alreadyMember = canBeMember && (() => {
    const npc = [form.source, form.target].map(parseNodeId).find((x) => x.kind === 'npc');
    const fac = [form.source, form.target].map(parseNodeId).find((x) => x.kind === 'faction');
    return graph.memberships.some((m) => m.npc_id === npc.id && m.faction_id === fac.id);
  })();
  const isMember = form.type === MEMBER && canBeMember;
  const type = types.get(form.type);

  const chooseType = (key, picked) => {
    setForm((f) => ({
      ...f,
      type: key,
      // Pour une nouvelle relation, le sens par défaut suit la nature choisie.
      directed: editing ? f.directed : Boolean((picked || types.get(key))?.directed),
    }));
  };

  const valid = form.source && form.target && form.source !== form.target && !(isMember && alreadyMember);

  const save = async (e) => {
    e.preventDefault();
    if (!valid) return;
    setSaving(true);
    let result;
    if (isMember) {
      const ends = [form.source, form.target].map(parseNodeId);
      result = await run(() => api.createMembership({
        npc_id: ends.find((x) => x.kind === 'npc').id,
        faction_id: ends.find((x) => x.kind === 'faction').id,
        role: form.role,
        is_leader: form.is_leader,
      }), t('Appartenance ajoutée'));
    } else {
      const s = parseNodeId(form.source);
      const d = parseNodeId(form.target);
      const payload = {
        source_kind: s.kind, source_id: s.id, target_kind: d.kind, target_id: d.id,
        type: form.type, label: form.label, description: form.description,
        directed: form.directed, secret: form.secret,
      };
      result = await run(
        () => (editing ? api.updateRelation(relation.id, payload) : api.createRelation(campaignId, payload)),
        editing ? t('Relation modifiée') : t('Relation créée'),
      );
    }
    setSaving(false);
    if (result) onSaved(result, isMember);
  };

  const entitySelect = (id, value, onChange) => (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)} required>
      <option value="">{t('Choisir…')}</option>
      {options.npcs.length > 0 && (
        <optgroup label={t('PNJ')}>
          {options.npcs.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </optgroup>
      )}
      {options.factions.length > 0 && (
        <optgroup label={t('Factions')}>
          {options.factions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </optgroup>
      )}
    </select>
  );

  return (
    <Modal title={editing ? t('Modifier la relation') : t('Nouvelle relation')} onClose={onClose} wide
      footer={(
        <>
          <button type="button" className="btn btn--ghost" onClick={onClose}>{t('Annuler')}</button>
          <button type="submit" form="relation-form" className="btn btn--primary" disabled={!valid || saving}>
            {editing ? t('Enregistrer') : isMember ? t('Ajouter le membre') : t('Créer la relation')}
          </button>
        </>
      )}>
      <form id="relation-form" onSubmit={save} className="relation-form">
        <div className="relation-form__ends">
          <Field label={t('De')} htmlFor="rel-source">
            {entitySelect('rel-source', form.source, (v) => set('source', v))}
          </Field>
          <button type="button" className="icon-btn relation-form__swap" title={t('Inverser')} aria-label={t('Inverser la source et la cible')}
            onClick={() => setForm((f) => ({ ...f, source: f.target, target: f.source }))}>
            ⇄
          </button>
          <Field label={t('Vers')} htmlFor="rel-target">
            {entitySelect('rel-target', form.target, (v) => set('target', v))}
          </Field>
        </div>
        {form.source && form.source === form.target && (
          <p className="form-error">{t('Choisissez deux fiches différentes.')}</p>
        )}

        <RelationTypePicker
          campaignId={campaignId}
          types={typeList}
          value={form.type}
          onChange={chooseType}
          onTypesChanged={onTypesChanged}
          extraOption={canBeMember && (
            <button type="button" role="radio" aria-checked={form.type === MEMBER}
              className={`type-option type-option--member${form.type === MEMBER ? ' is-active' : ''}`}
              onClick={() => chooseType(MEMBER)}>
              <span className="type-option__swatch" />{t('Membre de')}
            </button>
          )}
        />

        {isMember ? (
          <>
            {alreadyMember && <p className="form-error">{t('Ce PNJ est déjà membre de cette faction.')}</p>}
            <div className="form-grid">
              <Field label={t('Rôle dans la faction')} htmlFor="rel-role">
                <input id="rel-role" value={form.role} maxLength={200} onChange={(e) => set('role', e.target.value)}
                  placeholder={t('Trésorier, recrue, agent double…')} />
              </Field>
              <label className="check check--field">
                <input type="checkbox" checked={form.is_leader} onChange={(e) => set('is_leader', e.target.checked)} />
                {t('Dirige la faction')}
              </label>
            </div>
          </>
        ) : (
          <>
            <Field label={t('Libellé')} hint={t('Affiché sur le lien. Laissez vide pour afficher la nature du lien.')} htmlFor="rel-label">
              <input id="rel-label" value={form.label} maxLength={200} onChange={(e) => set('label', e.target.value)}
                placeholder={type ? t('{type}, ou plus précis : « frère aîné », « lui doit 300 po »…', { type: type.label }) : ''} />
            </Field>
            <Field label={t('Détails')} htmlFor="rel-desc" hint={t('Markdown accepté.')}>
              <textarea id="rel-desc" rows={3} value={form.description}
                onChange={(e) => set('description', e.target.value)} />
            </Field>
            <div className="relation-form__flags">
              <label className="check">
                <input type="checkbox" checked={form.directed} onChange={(e) => set('directed', e.target.checked)} />
                {type?.hint && form.directed ? t('Lien à sens unique ({hint})', { hint: type.hint }) : t('Lien à sens unique')}
              </label>
              <label className="check">
                <input type="checkbox" checked={form.secret} onChange={(e) => set('secret', e.target.checked)} />
                {t('Secret (connu du MJ seulement)')}
              </label>
            </div>
          </>
        )}
      </form>
    </Modal>
  );
}
