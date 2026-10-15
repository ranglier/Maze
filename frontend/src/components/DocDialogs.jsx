import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { BUILTIN_TEMPLATES } from '../editor/templates.js';
import { Field, Icon, Modal, useAction, useConfirm } from './ui.jsx';

/*
 * Création d'une fiche à partir d'un modèle, et enregistrement d'une fiche comme modèle.
 */

const BLANK = { key: 'blank', name: 'Page vierge', description: 'Commencer sans structure.', content: '' };

export function NewDocDialog({ isScenario, user, onClose, onCreate }) {
  const [title, setTitle] = useState('');
  const [choice, setChoice] = useState('blank');
  const [custom, setCustom] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = useAction();
  const confirm = useConfirm();

  useEffect(() => {
    api.templates().then((r) => setCustom(r.templates)).catch(() => setCustom([]));
  }, []);

  // Modèles les plus utiles d'abord : scénario et séance à la racine, scène et lieu en sous-page.
  const builtins = [...BUILTIN_TEMPLATES].sort((a, b) => {
    const rank = (t) => (t.level === (isScenario ? 'scenario' : 'page') ? 0 : t.level === 'any' ? 1 : 2);
    return rank(a) - rank(b);
  });
  const all = [BLANK, ...builtins, ...(custom || []).map((t) => ({ ...t, key: `custom-${t.id}` }))];
  const selected = all.find((t) => t.key === choice) || BLANK;

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    const done = await onCreate({ title: title.trim(), content: selected.content || '' });
    if (!done) setBusy(false);
  };

  const removeTemplate = async (t) => {
    const yes = await confirm({
      title: `Supprimer le modèle « ${t.name} »`,
      message: 'Le modèle ne sera plus proposé aux MJ. Les fiches déjà créées avec lui ne changent pas.',
      confirmLabel: 'Supprimer',
    });
    if (!yes) return;
    if (await run(() => api.deleteTemplate(t.id), 'Modèle supprimé') === undefined) return;
    setCustom((list) => list.filter((x) => x.id !== t.id));
    if (choice === t.key) setChoice('blank');
  };

  const card = (t) => (
    <li key={t.key}>
      <label className={`tpl-card${choice === t.key ? ' is-active' : ''}`}>
        <input type="radio" name="tpl" value={t.key} checked={choice === t.key} onChange={() => setChoice(t.key)} />
        <span className="tpl-card__name">{t.name}</span>
        {t.description && <span className="tpl-card__desc">{t.description}</span>}
        {t.author_name && <span className="tpl-card__by">par {t.author_name}</span>}
      </label>
      {t.id && (user.role === 'admin' || t.user_id === user.id) && (
        <button type="button" className="icon-btn icon-btn--small tpl-card__del" aria-label={`Supprimer le modèle ${t.name}`}
          title="Supprimer le modèle" onClick={() => removeTemplate(t)}>
          <Icon name="trash" size={14} />
        </button>
      )}
    </li>
  );

  return (
    <Modal title={isScenario ? 'Nouveau scénario' : 'Nouvelle sous-page'} wide onClose={onClose} footer={(
      <>
        <button type="button" className="btn btn--ghost" onClick={onClose}>Annuler</button>
        <button type="submit" form="new-doc-form" className="btn btn--primary" disabled={busy}>Créer</button>
      </>
    )}>
      <form id="new-doc-form" onSubmit={submit}>
        <Field label="Titre" htmlFor="new-doc-title">
          <input id="new-doc-title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)}
            placeholder={isScenario ? 'Nouveau scénario' : 'Nouvelle page'} autoComplete="off" />
        </Field>
        <fieldset className="tpl-picker">
          <legend className="field__label">Modèle</legend>
          <ul className="tpl-grid">
            {card(BLANK)}
            {builtins.map(card)}
          </ul>
          {custom === null ? <p className="muted">Chargement des modèles…</p> : custom.length > 0 && (
            <>
              <p className="tpl-picker__sub">Modèles des MJ</p>
              {custom.some((t) => t.shared) && (
                <p className="field__hint tpl-picker__shared">
                  Visibles par tous les MJ de l’instance, quelle que soit la campagne (un modèle ne contient ni mention, ni image).
                </p>
              )}
              <ul className="tpl-grid">{custom.map((t) => card({ ...t, key: `custom-${t.id}` }))}</ul>
            </>
          )}
          {custom?.length === 0 && (
            <p className="field__hint">Pour créer vos propres modèles : menu ⋯ d’une fiche, « Enregistrer comme modèle ».</p>
          )}
        </fieldset>
      </form>
    </Modal>
  );
}

export function SaveTemplateDialog({ defaultName, onClose, onSave }) {
  const [name, setName] = useState(defaultName || '');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    const done = await onSave({ name: name.trim(), description: description.trim() });
    if (!done) setBusy(false);
  };
  return (
    <Modal title="Enregistrer comme modèle" onClose={onClose} footer={(
      <>
        <button type="button" className="btn btn--ghost" onClick={onClose}>Annuler</button>
        <button type="submit" form="tpl-form" className="btn btn--primary" disabled={!name.trim() || busy}>Enregistrer</button>
      </>
    )}>
      <form id="tpl-form" onSubmit={submit}>
        <p className="muted tpl-form__intro">
          La structure et le texte de la fiche seront proposés à la création de nouvelles fiches.
          Les mentions deviennent du texte simple ; images et commentaires ne sont pas repris.
        </p>
        <p className="notice notice--busy tpl-form__shared">
          Le modèle sera visible par tous les MJ de l’instance, pas seulement ceux de cette campagne : n’y laissez rien de secret.
        </p>
        <Field label="Nom du modèle" htmlFor="tpl-name">
          <input id="tpl-name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} autoComplete="off" />
        </Field>
        <Field label="Description" htmlFor="tpl-desc" hint="Facultatif : à quoi sert ce modèle.">
          <input id="tpl-desc" value={description} maxLength={300} onChange={(e) => setDescription(e.target.value)} autoComplete="off" />
        </Field>
      </form>
    </Modal>
  );
}
