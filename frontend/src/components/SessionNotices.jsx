import { useEffect, useState } from 'react';
import { discardLocal, dismissLost, localEdits, lostEdits, purgeDate, reloadForUpdate, retryLocal, staleClient,
  subscribe } from '../session.js';
import { Modal } from './ui.jsx';
import { N_, dateFormat, pt, t, tn } from '../i18n/index.js';

/*
 * Avis persistants (lot 3 de l'audit), affichés par-dessus toute l'interface jusqu'à ce que l'utilisateur les ferme :
 *   - modifications d'une fiche rédigée que le temps réel n'a pas pu fusionner (M4, H3) : où le texte est gardé ;
 *   - Maze mis à jour pendant que la page était ouverte (H3), quand elle n'a pas pu être rechargée d'elle-même.
 */

// Raisons de l'abandon, traduites à l'affichage (t(REASONS[…])).
const REASONS = {
  reset: N_('La fiche a été remplacée pendant que ces modifications attendaient d’être envoyées (restauration d’une version ou d’une sauvegarde, modification par Claude, image supprimée).'),
  toolarge: N_('Une modification dépassait la taille maximale (collage de plus de 2 Mo).'),
  version: N_('Maze a été mis à jour pendant que vous écriviez : cette page ne pouvait plus envoyer ses modifications.'),
  deleted: N_('La fiche a été supprimée entre-temps.'),
  forbidden: N_('Votre accès à cette campagne a été retiré.'),
  auth: N_('Votre session a été fermée (mot de passe changé, déconnexion).'),
};

const SHORT_DATE_TIME = { dateStyle: 'short', timeStyle: 'short' };

function useSession() {
  const [, setTick] = useState(0);
  useEffect(() => subscribe(() => setTick((n) => n + 1)), []);
  return { lost: lostEdits(), stale: staleClient() };
}

export function download(entry) {
  const blob = new Blob([`<!doctype html><meta charset="utf-8"><title>${entry.title || pt('doc', 'Fiche')}</title>\n${entry.html}`],
    { type: 'text/html' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  const name = (entry.title || pt('doc', 'fiche')).replace(/[\\/:*?"<>|]+/g, '_');
  link.download = `${t('{name} - modifications non fusionnées', { name })}.html`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

async function copyText(entry) {
  const box = document.createElement('div');
  box.innerHTML = entry.html;
  const text = box.innerText || box.textContent || '';
  try {
    await navigator.clipboard.write([new ClipboardItem({
      'text/html': new Blob([entry.html], { type: 'text/html' }), 'text/plain': new Blob([text], { type: 'text/plain' }),
    })]);
  } catch {
    await navigator.clipboard?.writeText(text).catch(() => {});
  }
}

function LostEdit({ entry }) {
  const [copied, setCopied] = useState(false);
  const when = dateFormat(SHORT_DATE_TIME).format(new Date(entry.at));
  const href = entry.campaignId ? `#/c/${entry.campaignId}/scenarios/${entry.docId}` : null;
  const purge = purgeDate(entry);
  const soon = purge.getTime() - Date.now() < 24 * 3600 * 1000;
  return (
    <div className="session-notice session-notice--lost" role="alert" data-lost-edits={entry.status}>
      <p className="session-notice__title">
        {t('Modifications non fusionnées : « {title} »', { title: entry.title || t('Fiche rédigée') })}
        {' '}<span className="muted">({when})</span>
      </p>
      <p>{REASONS[entry.reason] ? t(REASONS[entry.reason]) : t('Ces modifications n’ont pas pu être fusionnées dans la fiche.')}</p>
      {entry.status === 'saving' && <p>{t('Enregistrement de votre texte dans l’historique de la fiche…')}</p>}
      {entry.status === 'version' && (
        <p>{t('Votre texte n’est pas perdu : il est gardé dans l’historique de la fiche, version « {label} » (bouton Historique '
          + 'de la fiche). Reprenez-y ce qui manque.', { label: <strong>{entry.label}</strong> })}</p>
      )}
      {entry.status === 'recovered' && (
        <p>{t('Votre texte, gardé dans ce navigateur, vient d’être enregistré dans l’historique de la fiche, version « {label} » ; '
          + 'il n’est plus dans le navigateur.', { label: <strong>{entry.label}</strong> })}</p>
      )}
      {entry.status === 'local' && (
        <p>
          {entry.memoryOnly
            ? (entry.error
              ? t('Votre texte n’a pas pu être enregistré sur le serveur ({error}) : il n’est gardé que dans cette page, copiez-le ou '
                + 'téléchargez-le maintenant.', { error: entry.error })
              : t('Votre texte n’a pas pu être enregistré sur le serveur : il n’est gardé que dans cette page, copiez-le ou '
                + 'téléchargez-le maintenant.'))
            : (entry.error
              ? t('Votre texte n’a pas pu être enregistré sur le serveur ({error}) : il est gardé dans ce navigateur (pour votre '
                + 'compte seulement), et sera de nouveau envoyé à votre prochaine connexion. Copiez-le ou téléchargez-le pour le '
                + 'reprendre.', { error: entry.error })
              : t('Votre texte n’a pas pu être enregistré sur le serveur : il est gardé dans ce navigateur (pour votre compte '
                + 'seulement), et sera de nouveau envoyé à votre prochaine connexion. Copiez-le ou téléchargez-le pour le reprendre.'))}
        </p>
      )}
      {entry.status === 'local' && !entry.memoryOnly && (
        <p className={soon ? 'session-notice__soon' : 'muted'} data-purge-soon={soon ? '' : undefined}>
          {soon
            ? t('Bientôt : ce texte sera effacé de ce navigateur le {date}.', { date: dateFormat(SHORT_DATE_TIME).format(purge) })
            : t('Ce texte sera effacé de ce navigateur le {date}.', { date: dateFormat(SHORT_DATE_TIME).format(purge) })}
        </p>
      )}
      <div className="session-notice__actions">
        {href && entry.status !== 'saving' && <a className="btn btn--small btn--ghost" href={href}>{pt('page', 'Ouvrir la fiche')}</a>}
        {entry.html && (
          <>
            <button type="button" className="btn btn--small btn--ghost"
              onClick={async () => { await copyText(entry); setCopied(true); }}>{copied ? t('Copié') : t('Copier le texte')}</button>
            <button type="button" className="btn btn--small btn--ghost" onClick={() => download(entry)}>{t('Télécharger')}</button>
          </>
        )}
        {entry.status === 'local' ? (
          <button type="button" className="btn btn--small btn--ghost" onClick={() => dismissLost(entry.id)}>
            {t('Abandonner ce texte')}
          </button>
        ) : entry.status !== 'saving' && (
          <button type="button" className="btn btn--small btn--primary" onClick={() => dismissLost(entry.id)}>
            {t('J’ai compris')}
          </button>
        )}
      </div>
    </div>
  );
}

export default function SessionNotices() {
  const { lost, stale } = useSession();
  if (!lost.length && !stale) return null;
  return (
    <div className="session-notices" aria-live="assertive">
      {stale && (
        <div className="session-notice session-notice--stale" role="alert" data-stale-client="">
          <p className="session-notice__title">{t('Maze a été mis à jour')}</p>
          <p>{t('Cette page date d’avant la mise à jour : ses enregistrements sont refusés. Rechargez-la pour continuer (une saisie '
            + 'en cours sera perdue : copiez-la d’abord).')}</p>
          <div className="session-notice__actions">
            <button type="button" className="btn btn--small btn--primary" onClick={() => reloadForUpdate(true)}>
              {t('Recharger la page')}
            </button>
          </div>
        </div>
      )}
      {lost.map((entry) => <LostEdit key={entry.id} entry={entry} />)}
    </div>
  );
}

/**
 * Déconnexion alors que des textes du compte ne sont gardés que dans ce navigateur (poste partagé) : les enregistrer
 * dans l'historique, les télécharger, ou les abandonner ; jamais laissés en silence pour le compte suivant.
 */
export function LogoutDialog({ onCancel, onLogout }) {
  useSession();
  const entries = localEdits();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => { if (!busy && !entries.length) onLogout(); }, [busy, entries.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = async () => {
    setBusy(true);
    const left = await retryLocal();
    setBusy(false);
    if (left) setFailed(true);
  };
  const leave = (before) => { before?.(); discardLocal(); onLogout(); };
  return (
    <Modal title={t('Textes gardés dans ce navigateur')} onClose={onCancel}>
      <div data-logout-lost="">
        <p className="confirm-text">
          {tn('{n} texte non fusionné de votre compte ({titles}) n’a pas pu être enregistré sur le serveur et est gardé dans ce '
            + 'navigateur. Sur un poste partagé, ne l’y laissez pas.',
          '{n} textes non fusionnés de votre compte ({titles}) n’ont pas pu être enregistrés sur le serveur et sont gardés dans ce '
            + 'navigateur. Sur un poste partagé, ne les y laissez pas.', entries.length,
          { titles: entries.map((e) => t('« {title} »', { title: e.title || t('Fiche rédigée') })).join(', ') })}
        </p>
        {failed && <p className="form-error">{t('L’enregistrement a encore échoué : téléchargez ou abandonnez.')}</p>}
        <div className="session-notice__actions">
          <button type="button" className="btn btn--ghost" onClick={onCancel}>{t('Annuler')}</button>
          <button type="button" className="btn btn--ghost" disabled={busy}
            onClick={() => leave(() => entries.forEach(download))}>{t('Télécharger puis se déconnecter')}</button>
          <button type="button" className="btn btn--danger" disabled={busy} onClick={() => leave()}>
            {t('Abandonner et se déconnecter')}</button>
          <button type="button" className="btn btn--primary" disabled={busy} onClick={save}>
            {busy ? t('Enregistrement…') : t('Enregistrer dans l’historique')}</button>
        </div>
      </div>
    </Modal>
  );
}
