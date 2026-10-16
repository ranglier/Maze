import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { setUnsaved } from '../session.js';
import { Markdown } from './ui.jsx';
import { t } from '../i18n/index.js';

/*
 * Brouillon d'un formulaire de fiche PNJ, faction ou lieu (lot 3 de l'audit) :
 *   - M2 : seuls les champs modifiés par l'utilisateur sont envoyés (comparés à l'état d'ouverture du formulaire,
 *     jamais à la fiche relue entre-temps : un champ changé par Claude ou un autre MJ n'est pas remis à l'ancienne
 *     valeur) ;
 *   - M3 : état « modifié » inscrit dans session.js (confirmation avant de quitter, avertissement à la fermeture) ;
 *   - M1 : révision de départ envoyée (base_revision). Sur 409, les champs changés par l'autre seulement sont repris
 *     dans le formulaire, l'enregistrement repart tout seul s'il n'y a pas de conflit ; sinon le formulaire montre,
 *     champ par champ, la version actuelle à côté de la saisie, qui n'est jamais perdue. L'enregistrement qui suit
 *     un 409 porte new_version : la version de l'autre reste distincte dans l'historique.
 */

const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

export function useEntryDraft({ kind, entry, keys, empty }) {
  const initial = useRef(null);
  if (initial.current === null) initial.current = Object.fromEntries(keys.map((k) => [k, entry[k] ?? empty(k)]));
  const [draft, setDraftState] = useState(() => ({ ...initial.current }));
  const draftRef = useRef(draft);
  const base = useRef(entry.revision ?? null);
  // Après un 409, la version précédente de l'historique est celle de l'autre : jamais fondue avec la nôtre.
  const afterConflict = useRef(false);
  const [conflict, setConflict] = useState(null);  // { author, fields: [{ key, theirs }] }
  const [generation, setGeneration] = useState({});  // champ -> numéro : remonte l'éditeur du champ repris

  const setDraft = (next) => {
    draftRef.current = next;
    setDraftState(next);
  };
  const set = (key) => (value) => setDraft({ ...draftRef.current, [key]: value });
  const changedKeys = (d = draftRef.current) => keys.filter((k) => !same(d[k], initial.current[k]));
  const dirty = changedKeys(draft).length > 0;

  const unsavedKey = `entry:${kind}:${entry.id}`;
  useEffect(() => { setUnsaved(unsavedKey, dirty, entry.name); }, [unsavedKey, dirty, entry.name]);
  useEffect(() => () => setUnsaved(unsavedKey, false), [unsavedKey]);

  /** Reprend l'état actuel envoyé avec un 409 ; renvoie les champs en conflit (changés des deux côtés). */
  const merge = (current) => {
    const init = { ...initial.current };
    const next = { ...draftRef.current };
    const bumped = {};
    const conflicts = [];
    for (const k of keys) {
      const theirs = current[k] ?? empty(k);
      if (same(theirs, init[k])) continue;                 // pas touché par l'autre
      if (same(next[k], init[k])) {                        // pas touché ici : on prend le sien
        init[k] = theirs;
        next[k] = theirs;
        bumped[k] = true;
      } else if (same(next[k], theirs)) {
        init[k] = theirs;                                  // même changement des deux côtés
      } else {
        conflicts.push({ key: k, theirs });
      }
    }
    initial.current = init;
    base.current = current.revision ?? base.current;
    setDraft(next);
    if (Object.keys(bumped).length) {
      setGeneration((g) => ({ ...g, ...Object.fromEntries(Object.keys(bumped).map((k) => [k, (g[k] || 0) + 1])) }));
    }
    return conflicts;
  };

  /** Enregistre les champs modifiés. Renvoie 'saved', 'unchanged' ou 'conflict' ; lève l'erreur sinon. */
  const save = async () => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const changes = Object.fromEntries(changedKeys().map((k) => [k, draftRef.current[k]]));
      if (!Object.keys(changes).length) return attempt ? 'saved' : 'unchanged';
      try {
        const guard = base.current == null ? {}
          : { base_revision: base.current, ...(afterConflict.current ? { new_version: true } : {}) };
        await api.updateEntity(kind, entry.id, { ...changes, ...guard });
        setUnsaved(unsavedKey, false);
        return 'saved';
      } catch (err) {
        if (err.status !== 409 || !err.data?.current) throw err;
        afterConflict.current = true;
        const conflicts = merge(err.data.current);
        if (conflicts.length) {
          setConflict({ author: err.data.author || '', fields: conflicts });
          return 'conflict';
        }
      }
    }
    throw new Error(t('La fiche change sans cesse : réessayez dans un instant.'));
  };

  /** Conflit sur un champ : garder sa saisie (« mine ») ou reprendre la version actuelle (« theirs »). */
  const resolve = (key, choice) => {
    const field = conflict?.fields.find((f) => f.key === key);
    if (!field) return;
    initial.current = { ...initial.current, [key]: field.theirs };
    if (choice === 'theirs') {
      setDraft({ ...draftRef.current, [key]: field.theirs });
      setGeneration((g) => ({ ...g, [key]: (g[key] || 0) + 1 }));
    }
    const rest = conflict.fields.filter((f) => f.key !== key);
    setConflict(rest.length ? { ...conflict, fields: rest } : null);
  };

  const keepAll = () => {
    if (!conflict) return;
    initial.current = { ...initial.current, ...Object.fromEntries(conflict.fields.map((f) => [f.key, f.theirs])) };
    setConflict(null);
  };

  return { draft, set, dirty, save, conflict, resolve, keepAll, fieldKey: (k) => `${k}-${generation[k] || 0}` };
}

/** Conflit d'enregistrement : version actuelle de chaque champ changé des deux côtés, et choix.
 * `labels` : clé > libellé du registre en français (traduit ici). */
export function ConflictPanel({ conflict, labels, rich, resolveRef, onResolve, onKeepAll }) {
  const ref = useRef(null);
  const shown = Boolean(conflict);
  // Le bouton Enregistrer est en bas du formulaire, l'encadré en haut : il est amené à l'écran.
  useEffect(() => { if (shown) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, [shown]);
  if (!conflict) return null;
  return (
    <div className="entry-conflict" role="alert" data-entry-conflict="" ref={ref}>
      <p>
        <strong>{conflict.author ? t('Fiche modifiée entre-temps par {author}.', { author: conflict.author })
          : t('Fiche modifiée entre-temps.')}</strong>
        {' '}
        {t('Vos changements ne sont pas encore enregistrés et restent dans le formulaire. Pour chaque champ modifié des deux côtés, gardez votre saisie ou reprenez la version actuelle, puis enregistrez.')}
      </p>
      {conflict.fields.map((f) => (
        <div key={f.key} className="entry-conflict__field" data-conflict-field={f.key}>
          <h4>{t('{field} : version actuelle', { field: labels[f.key] ? t(labels[f.key]) : f.key })}</h4>
          <div className="entry-conflict__theirs">
            {rich.has(f.key) ? <Markdown text={f.theirs} resolve={resolveRef} />
              : <span>{Array.isArray(f.theirs) ? f.theirs.join(', ') : String(f.theirs ?? '') || t('(vide)')}</span>}
          </div>
          <div className="entry-conflict__actions">
            <button type="button" className="btn btn--small btn--primary" onClick={() => onResolve(f.key, 'mine')}>
              {t('Garder ma saisie')}
            </button>
            <button type="button" className="btn btn--small btn--ghost" onClick={() => onResolve(f.key, 'theirs')}>
              {t('Reprendre la version actuelle')}
            </button>
          </div>
        </div>
      ))}
      {conflict.fields.length > 1 && (
        <div className="entry-conflict__actions">
          <button type="button" className="btn btn--small btn--ghost" onClick={onKeepAll}>{t('Garder toute ma saisie')}</button>
        </div>
      )}
    </div>
  );
}
