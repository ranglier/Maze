import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';
import { daysLeft, filterByKind, kindCounts, kindIcon, kindLabel, objectHref, parseStamp, TRASH_KINDS } from '../trash.js';
import { Icon, useConfirm, useToast } from './ui.jsx';
import { formatDate, pt, t, tn } from '../i18n/index.js';

/*
 * Corbeille d'une campagne (route #/c/<id>/corbeille, menu de la campagne) : PNJ, factions, lieux, fiches rédigées
 * (avec leurs sous-pages), événements et cartes interactives supprimés depuis l'interface ou par Claude, gardés
 * 30 jours par le serveur puis effacés. Restaurer, supprimer définitivement, vider. Logique pure : ../trash.js.
 * useTrashDelete : suppression suivie d'une notification « Annuler » (restauration immédiate), pour les écrans
 * qui suppriment ces éléments.
 */

/**
 * Suppression mise à la corbeille, avec « Annuler » dans la notification de succès.
 * `del(fn, message, onRestored)` : fn appelle la route DELETE (réponse { trash_id }) ; message : texte de succès ;
 * onRestored({ kind, object_id }) après une annulation réussie (recharger l'écran). Renvoie la réponse (null pour
 * une réponse vide), ou undefined si la suppression a échoué (erreur affichée), comme useAction.
 */
export function useTrashDelete() {
  const toast = useToast();
  return useCallback(async (fn, message, onRestored) => {
    let result;
    try {
      result = await fn();
    } catch (err) {
      toast(err.message || String(err), 'error');
      return undefined;
    }
    const trashId = result?.trash_id;
    const undo = async () => {
      try {
        const restored = await api.restoreTrash(trashId);
        toast(t('Suppression annulée'), 'success');
        await onRestored?.(restored);
      } catch (err) {
        toast(err.message || String(err), 'error');
      }
    };
    toast(message, 'success', trashId ? { action: { label: pt('toast', 'Annuler'), onClick: undo } } : undefined);
    return result ?? null;
  }, [toast]);
}

const STAMP = { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' };

function deletedText(item) {
  const date = formatDate(parseStamp(item.deleted_at) || '', STAMP);
  if (item.via === 'claude') return t('Supprimé le {date} par Claude', { date });
  if (item.deleted_by_name) return t('Supprimé le {date} par {name}', { date, name: item.deleted_by_name });
  return t('Supprimé le {date}', { date });
}

function expiryText(item) {
  const n = daysLeft(item.expires_at);
  if (n === null) return '';
  return n === 0 ? t('Effacé aujourd’hui') : tn('Effacé dans {n} jour', 'Effacé dans {n} jours', n);
}

export default function TrashView({ campaign, header, trailing, onNavigate }) {
  const cid = campaign.id;
  const toast = useToast();
  const confirm = useConfirm();
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [kind, setKind] = useState('');
  const [busy, setBusy] = useState(null);   // id de l'élément en cours de traitement, ou 'all'

  const load = useCallback(async () => {
    try {
      const data = await api.listTrash(cid);
      setItems(data?.items || []);
      setError(null);
    } catch (err) {
      setError(err.message || String(err));
    }
  }, [cid]);
  useEffect(() => { load(); }, [load]);

  const counts = useMemo(() => kindCounts(items), [items]);
  const visible = useMemo(() => filterByKind(items, kind), [items, kind]);
  const activeKind = visible === items ? '' : kind;

  const restore = async (item) => {
    setBusy(item.id);
    try {
      const restored = await api.restoreTrash(item.id);
      const href = objectHref(cid, restored?.kind || item.kind, restored?.object_id ?? item.object_id);
      toast(t('Élément restauré : « {name} »', { name: item.name || t('Sans titre') }), 'success',
        href ? { action: { label: t('Ouvrir'), onClick: () => onNavigate(href) } } : undefined);
      await load();
    } catch (err) {
      // 409 : restauration impossible, explication du serveur affichée telle quelle.
      toast(err.message || String(err), 'error');
      if (err.status === 404) load();
    } finally {
      setBusy(null);
    }
  };

  const purge = async (item) => {
    const name = item.name || t('Sans titre');
    const ok = await confirm({
      title: t('Supprimer définitivement « {name} »', { name }),
      message: t('L’élément et ses images seront effacés sans retour possible.'),
      confirmLabel: t('Supprimer définitivement'),
    });
    if (!ok) return;
    setBusy(item.id);
    try {
      await api.purgeTrash(item.id);
      toast(t('« {name} » supprimé définitivement', { name }), 'success');
    } catch (err) {
      toast(err.message || String(err), 'error');
    } finally {
      setBusy(null);
      await load();
    }
  };

  const empty = async () => {
    const ok = await confirm({
      title: t('Vider la corbeille'),
      message: tn('L’élément de la corbeille sera effacé sans retour possible, images comprises.',
        'Les {n} éléments de la corbeille seront effacés sans retour possible, images comprises.', items.length),
      confirmLabel: t('Vider la corbeille'),
    });
    if (!ok) return;
    setBusy('all');
    try {
      await api.emptyTrash(cid);
      toast(t('Corbeille vidée'), 'success');
      setKind('');
    } catch (err) {
      toast(err.message || String(err), 'error');
    } finally {
      setBusy(null);
      await load();
    }
  };

  return (
    <div className="scenarios trash">
      <header className="topbar">
        {header}
        <div className="topbar__right">
          {trailing && <div className="topbar__end">{trailing}</div>}
        </div>
      </header>
      <main className="trash__body">
        <div className="trash__inner">
          <div className="admin__head trash__head">
            <div>
              <h1>{t('Corbeille')}</h1>
              <p>{t('Les PNJ, factions, lieux, fiches rédigées, événements et cartes supprimés restent ici 30 jours, puis sont effacés définitivement.')}</p>
            </div>
            {items?.length > 0 && (
              <div className="admin__head-actions">
                <button type="button" className="btn btn--ghost btn--danger-text" onClick={empty} disabled={busy !== null}>
                  <Icon name="trash" size={16} /> {t('Vider la corbeille')}
                </button>
              </div>
            )}
          </div>

          {error ? <p className="form-error">{error}</p>
            : !items ? <div className="doc-loading" aria-busy="true">{t('Chargement…')}</div>
            : items.length === 0 ? (
              <div className="trash-empty">
                <Icon name="trash" size={34} />
                <h2>{t('La corbeille est vide')}</h2>
                <p>{t('Un élément supprimé apparaît ici : vous pourrez le restaurer pendant 30 jours.')}</p>
              </div>
            ) : (
              <>
                {counts.length > 1 && (
                  <div className="trash-filters" role="radiogroup" aria-label={t('Filtrer la corbeille')}>
                    <button type="button" role="radio" aria-checked={!activeKind} className={!activeKind ? 'is-active' : ''}
                      onClick={() => setKind('')}>
                      {t('Tout')} <span className="count">{items.length}</span>
                    </button>
                    {counts.map((c) => (
                      <button key={c.kind} type="button" role="radio" aria-checked={activeKind === c.kind}
                        className={activeKind === c.kind ? 'is-active' : ''} onClick={() => setKind(c.kind)}>
                        <Icon name={kindIcon(c.kind)} size={14} />
                        {TRASH_KINDS[c.kind] ? t(TRASH_KINDS[c.kind].filter) : c.kind} <span className="count">{c.count}</span>
                      </button>
                    ))}
                  </div>
                )}
                <ul className="trash-list">
                  {visible.map((item) => {
                    const name = item.name || t('Sans titre');
                    const expires = parseStamp(item.expires_at);
                    return (
                      <li key={item.id} className="trash-item" data-trash={item.id} data-kind={item.kind}>
                        <span className="trash-item__icon" title={kindLabel(item.kind)}>
                          <Icon name={kindIcon(item.kind)} size={18} />
                        </span>
                        <div className="trash-item__main">
                          <p className="trash-item__name">{name}</p>
                          <p className="trash-item__meta">
                            <span>{kindLabel(item.kind)}</span>
                            {item.summary && <span>{item.summary}</span>}
                          </p>
                          <p className="trash-item__meta">
                            <span>{deletedText(item)}</span>
                            <span className="trash-item__expiry"
                              title={expires ? t('Effacement définitif le {date}', { date: formatDate(expires, STAMP) }) : undefined}>
                              {expiryText(item)}
                            </span>
                          </p>
                        </div>
                        <div className="trash-item__actions">
                          <button type="button" className="btn btn--small btn--ghost" onClick={() => restore(item)}
                            disabled={busy !== null} aria-label={t('Restaurer « {name} »', { name })}>
                            <Icon name="undo" size={15} /> {t('Restaurer')}
                          </button>
                          <button type="button" className="icon-btn icon-btn--danger" onClick={() => purge(item)}
                            disabled={busy !== null}
                            aria-label={t('Supprimer définitivement « {name} »', { name })}
                            title={t('Supprimer définitivement')}>
                            <Icon name="trash" size={17} />
                          </button>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </>
            )}
        </div>
      </main>
    </div>
  );
}
