import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { RELATION_PALETTE } from '../relationTypes.js';
import { ColorPicker } from './EntityPanel.jsx';
import { Icon, useAction } from './ui.jsx';
import { t } from '../i18n/index.js';

/**
 * Choix de la nature d'un lien, avec création de nouvelles natures
 * et personnalisation (libellé, couleur, sens par défaut) des existantes.
 */
export default function RelationTypePicker({
  campaignId, types, value, onChange, onTypesChanged, extraOption,
}) {
  const [mode, setMode] = useState('pick'); // pick | create | edit

  return (
    <div className="field">
      <div className="type-picker__head">
        <span className="field__label" id="rel-type-label">{t('Nature du lien')}</span>
        {mode === 'pick' && (
          <button type="button" className="link-btn" onClick={() => setMode('edit')}>
            <Icon name="edit" size={13} /> {t('Personnaliser')}
          </button>
        )}
        {mode === 'edit' && (
          <button type="button" className="link-btn" onClick={() => setMode('pick')}>{t('Terminé')}</button>
        )}
      </div>

      {mode === 'edit' ? (
        <TypeEditor types={types} onTypesChanged={onTypesChanged}
          onAdd={() => setMode('create')} />
      ) : (
        <>
          <div className="type-grid" role="radiogroup" aria-labelledby="rel-type-label">
            {types.map((type) => (
              <button key={type.key} type="button" role="radio" aria-checked={value === type.key}
                className={`type-option${value === type.key ? ' is-active' : ''}`} style={{ '--rel': type.color }}
                onClick={() => onChange(type.key, type)}>
                <span className="type-option__swatch" />{type.label}
              </button>
            ))}
            {extraOption}
            {mode === 'pick' && (
              <button type="button" className="type-option type-option--new" onClick={() => setMode('create')}>
                <Icon name="plus" size={14} /> {t('Nouvelle nature')}
              </button>
            )}
          </div>
          {mode === 'create' && (
            <NewTypeForm
              campaignId={campaignId}
              usedColors={types.map((type) => type.color)}
              onCancel={() => setMode('pick')}
              onCreated={async (created) => {
                await onTypesChanged();
                onChange(created.key, created);
                setMode('pick');
              }}
            />
          )}
        </>
      )}
    </div>
  );
}

function NewTypeForm({ campaignId, usedColors, onCancel, onCreated }) {
  const run = useAction();
  const [label, setLabel] = useState('');
  const [color, setColor] = useState(() => RELATION_PALETTE.find((c) => !usedColors.includes(c)) || RELATION_PALETTE[0]);
  const [directed, setDirected] = useState(false);
  const [busy, setBusy] = useState(false);

  const create = async () => {
    if (!label.trim()) return;
    setBusy(true);
    const created = await run(() => api.createRelationType(campaignId, { label: label.trim(), color, directed }),
      t('Nature « {label} » créée', { label: label.trim() }));
    setBusy(false);
    if (created) onCreated(created);
  };

  return (
    <div className="type-new" role="group" aria-label={t('Nouvelle nature de lien')} data-own-escape>
      <input
        aria-label={t('Nom de la nouvelle nature')}
        placeholder={t('Vassalité, complot, ancienne dette…')}
        value={label}
        maxLength={40}
        autoFocus
        onChange={(e) => setLabel(e.target.value)}
        // Entrée crée la nature sans valider tout le formulaire de relation.
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); create(); }
          if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onCancel(); }
        }}
      />
      <ColorPicker id="new-type-color" value={color} onChange={setColor} palette={RELATION_PALETTE} />
      <div className="type-new__foot">
        <label className="check">
          <input type="checkbox" checked={directed} onChange={(e) => setDirected(e.target.checked)} />
          {t('À sens unique par défaut')}
        </label>
        <button type="button" className="btn btn--small btn--ghost" onClick={onCancel}>{t('Annuler')}</button>
        <button type="button" className="btn btn--small btn--primary" onClick={create} disabled={!label.trim() || busy}>
          {t('Créer la nature')}
        </button>
      </div>
    </div>
  );
}

function TypeEditor({ types, onTypesChanged, onAdd }) {
  const run = useAction();
  const save = async (type, changes) => {
    const ok = await run(() => api.updateRelationType(type.id, changes));
    if (ok) onTypesChanged();
  };
  const remove = async (type) => {
    const done = await run(() => api.deleteRelationType(type.id), t('Nature « {label} » supprimée', { label: type.label }));
    if (done !== undefined) onTypesChanged();
  };

  return (
    <div className="type-editor">
      <ul>
        {types.map((type) => (
          <li key={type.id}>
            <ColorInput value={type.color} label={type.label} onCommit={(color) => save(type, { color })} />
            <input className="type-editor__label" defaultValue={type.label} maxLength={40}
              aria-label={t('Nom de la nature {label}', { label: type.label })}
              onBlur={(e) => {
                const label = e.target.value.trim();
                if (!label) { e.target.value = type.label; return; }
                if (label !== type.label) save(type, { label });
              }}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur(); } }} />
            <label className="check type-editor__dir" title={t('Sens unique proposé par défaut pour les nouveaux liens')}>
              <input type="checkbox" checked={type.directed} onChange={(e) => save(type, { directed: e.target.checked })} />
              {t('Sens unique')}
            </label>
            <button type="button" className="icon-btn icon-btn--small icon-btn--danger" onClick={() => remove(type)}
              aria-label={t('Supprimer la nature {label}', { label: type.label })}
              title={t('Supprimer (uniquement si aucun lien ne l’utilise)')}>
              <Icon name="trash" size={14} />
            </button>
          </li>
        ))}
      </ul>
      <button type="button" className="btn btn--small btn--ghost" onClick={onAdd}>
        <Icon name="plus" size={14} /> {t('Nouvelle nature')}
      </button>
      <p className="field__hint">{t('Les changements s’appliquent immédiatement à toute la campagne, liens existants compris.')}</p>
    </div>
  );
}

/** Pastille de couleur : enregistre après une courte pause pour ne pas envoyer chaque nuance. */
function ColorInput({ value, label, onCommit }) {
  const [color, setColor] = useState(value);
  const timer = useRef(null);
  useEffect(() => setColor(value), [value]);
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <label className="type-editor__color" style={{ background: color }} title={t('Changer la couleur')}>
      <input type="color" value={color} aria-label={t('Couleur de {label}', { label })}
        onChange={(e) => {
          const next = e.target.value;
          setColor(next);
          clearTimeout(timer.current);
          timer.current = setTimeout(() => { if (next !== value) onCommit(next); }, 500);
        }} />
    </label>
  );
}
