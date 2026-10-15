import { useState } from 'react';
import { api } from '../api.js';
import { FACTION_COLORS } from '../constants.js';
import { ColorPicker } from './EntityPanel.jsx';
import { Field, Modal, useAction } from './ui.jsx';

export default function CreateDialog({ campaignId, kind: initialKind, position, usedColors, onClose, onCreated }) {
  const run = useAction();
  const [kind, setKind] = useState(initialKind || 'npc');
  const [name, setName] = useState('');
  const [detail, setDetail] = useState('');
  const [color, setColor] = useState(() => FACTION_COLORS.find((c) => !usedColors.includes(c)) || FACTION_COLORS[0]);
  const [saving, setSaving] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    const payload = kind === 'npc'
      ? { name, title: detail }
      : { name, kind: detail, color };
    if (position) Object.assign(payload, { pos_x: position.x, pos_y: position.y });
    const created = await run(() => api.createEntity(kind, campaignId, payload),
      kind === 'npc' ? `${name.trim()} rejoint la carte` : `Faction ${name.trim()} créée`);
    setSaving(false);
    if (created) onCreated(kind, created);
  };

  return (
    <Modal title={kind === 'npc' ? 'Nouveau PNJ' : 'Nouvelle faction'} onClose={onClose}
      footer={(
        <>
          <button type="button" className="btn btn--ghost" onClick={onClose}>Annuler</button>
          <button type="submit" form="create-form" className="btn btn--primary" disabled={!name.trim() || saving}>
            Créer et compléter la fiche
          </button>
        </>
      )}>
      <form id="create-form" onSubmit={submit}>
        <div className="segmented" role="radiogroup" aria-label="Type de fiche">
          {[['npc', 'PNJ'], ['faction', 'Faction']].map(([k, label]) => (
            <button key={k} type="button" role="radio" aria-checked={kind === k}
              className={kind === k ? 'is-active' : ''} onClick={() => setKind(k)}>{label}</button>
          ))}
        </div>
        <Field label="Nom" htmlFor="create-name">
          <input id="create-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required autoFocus
            placeholder={kind === 'npc' ? 'Maëlis Corvin' : 'La Main Grise'} />
        </Field>
        <Field label={kind === 'npc' ? 'Fonction' : 'Type'} htmlFor="create-detail">
          <input id="create-detail" value={detail} onChange={(e) => setDetail(e.target.value)} maxLength={200}
            placeholder={kind === 'npc' ? 'Receleuse du port' : 'Guilde de voleurs'} />
        </Field>
        {kind === 'faction' && (
          <Field label="Couleur" htmlFor="create-color" hint="Elle colore aussi l'anneau des membres sur la carte.">
            <ColorPicker id="create-color" value={color} onChange={setColor} />
          </Field>
        )}
      </form>
    </Modal>
  );
}
