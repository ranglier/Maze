import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { builtinTemplates } from '../editor/templates.js';
import { Field, Icon, Modal, useAction, useConfirm } from './ui.jsx';
import { t } from '../i18n/index.js';

/*
 * Création d'une fiche à partir d'un modèle, et enregistrement d'une fiche comme modèle.
 */

const blankTemplate = () => ({ key: 'blank', name: t('Page vierge'), description: t('Commencer sans structure.'), content: '' });

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

  // Modèles fournis dans la langue de l'interface (contenu inséré tel quel dans la nouvelle fiche).
  // Les plus utiles d'abord : scénario et séance à la racine, scène et lieu en sous-page.
  const blank = blankTemplate();
  const builtins = builtinTemplates().sort((a, b) => {
    const rank = (tpl) => (tpl.level === (isScenario ? 'scenario' : 'page') ? 0 : tpl.level === 'any' ? 1 : 2);
    return rank(a) - rank(b);
  });
  const all = [blank, ...builtins, ...(custom || []).map((tpl) => ({ ...tpl, key: `custom-${tpl.id}` }))];
  const selected = all.find((tpl) => tpl.key === choice) || blank;

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    const done = await onCreate({ title: title.trim(), content: selected.content || '' });
    if (!done) setBusy(false);
  };

  const removeTemplate = async (tpl) => {
    const yes = await confirm({
      title: t('Supprimer le modèle « {name} »', { name: tpl.name }),
      message: t('Le modèle ne sera plus proposé aux MJ. Les fiches déjà créées avec lui ne changent pas.'),
      confirmLabel: t('Supprimer'),
    });
    if (!yes) return;
    if (await run(() => api.deleteTemplate(tpl.id), t('Modèle supprimé')) === undefined) return;
    setCustom((list) => list.filter((x) => x.id !== tpl.id));
    if (choice === tpl.key) setChoice('blank');
  };

  const card = (tpl) => (
    <li key={tpl.key}>
      <label className={`tpl-card${choice === tpl.key ? ' is-active' : ''}`}>
        <input type="radio" name="tpl" value={tpl.key} checked={choice === tpl.key} onChange={() => setChoice(tpl.key)} />
        <span className="tpl-card__name">{tpl.name}</span>
        {tpl.description && <span className="tpl-card__desc">{tpl.description}</span>}
        {tpl.author_name && <span className="tpl-card__by">{t('par {name}', { name: tpl.author_name })}</span>}
      </label>
      {tpl.id && (user.role === 'admin' || tpl.user_id === user.id) && (
        <button type="button" className="icon-btn icon-btn--small tpl-card__del"
          aria-label={t('Supprimer le modèle {name}', { name: tpl.name })}
          title={t('Supprimer le modèle')} onClick={() => removeTemplate(tpl)}>
          <Icon name="trash" size={14} />
        </button>
      )}
    </li>
  );

  return (
    <Modal title={isScenario ? t('Nouveau scénario') : t('Nouvelle sous-page')} wide onClose={onClose} footer={(
      <>
        <button type="button" className="btn btn--ghost" onClick={onClose}>{t('Annuler')}</button>
        <button type="submit" form="new-doc-form" className="btn btn--primary" disabled={busy}>{t('Créer')}</button>
      </>
    )}>
      <form id="new-doc-form" onSubmit={submit}>
        <Field label={t('Titre')} htmlFor="new-doc-title">
          <input id="new-doc-title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)}
            placeholder={isScenario ? t('Nouveau scénario') : t('Nouvelle page')} autoComplete="off" />
        </Field>
        <fieldset className="tpl-picker">
          <legend className="field__label">{t('Modèle')}</legend>
          <ul className="tpl-grid">
            {card(blank)}
            {builtins.map(card)}
          </ul>
          {custom === null ? <p className="muted">{t('Chargement des modèles…')}</p> : custom.length > 0 && (
            <>
              <p className="tpl-picker__sub">{t('Modèles des MJ')}</p>
              {custom.some((tpl) => tpl.shared) && (
                <p className="field__hint tpl-picker__shared">
                  {t('Visibles par tous les MJ de l’instance, quelle que soit la campagne (un modèle ne contient ni mention, ni image).')}
                </p>
              )}
              <ul className="tpl-grid">{custom.map((tpl) => card({ ...tpl, key: `custom-${tpl.id}` }))}</ul>
            </>
          )}
          {custom?.length === 0 && (
            <p className="field__hint">{t('Pour créer vos propres modèles : menu ⋯ d’une fiche, « Enregistrer comme modèle ».')}</p>
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
    <Modal title={t('Enregistrer comme modèle')} onClose={onClose} footer={(
      <>
        <button type="button" className="btn btn--ghost" onClick={onClose}>{t('Annuler')}</button>
        <button type="submit" form="tpl-form" className="btn btn--primary" disabled={!name.trim() || busy}>{t('Enregistrer')}</button>
      </>
    )}>
      <form id="tpl-form" onSubmit={submit}>
        <p className="muted tpl-form__intro">
          {t('La structure et le texte de la fiche seront proposés à la création de nouvelles fiches. Les mentions deviennent du texte simple ; images et commentaires ne sont pas repris.')}
        </p>
        <p className="notice notice--busy tpl-form__shared">
          {t('Le modèle sera visible par tous les MJ de l’instance, pas seulement ceux de cette campagne : n’y laissez rien de secret.')}
        </p>
        <Field label={t('Nom du modèle')} htmlFor="tpl-name">
          <input id="tpl-name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} autoComplete="off" />
        </Field>
        <Field label={t('Description')} htmlFor="tpl-desc" hint={t('Facultatif : à quoi sert ce modèle.')}>
          <input id="tpl-desc" value={description} maxLength={300} onChange={(e) => setDescription(e.target.value)} autoComplete="off" />
        </Field>
      </form>
    </Modal>
  );
}
