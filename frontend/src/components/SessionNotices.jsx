import { useEffect, useState } from 'react';
import { discardLocal, dismissLost, localEdits, lostEdits, purgeDate, reloadForUpdate, retryLocal, staleClient,
  subscribe } from '../session.js';
import { Modal } from './ui.jsx';

/*
 * Avis persistants (lot 3 de l'audit), affichés par-dessus toute l'interface jusqu'à ce que l'utilisateur les ferme :
 *   - modifications d'une fiche rédigée que le temps réel n'a pas pu fusionner (M4, H3) : où le texte est gardé ;
 *   - Maze mis à jour pendant que la page était ouverte (H3), quand elle n'a pas pu être rechargée d'elle-même.
 */

const REASONS = {
  reset: 'La fiche a été remplacée pendant que ces modifications attendaient d’être envoyées (restauration d’une version ou d’une sauvegarde, modification par Claude, image supprimée).',
  toolarge: 'Une modification dépassait la taille maximale (collage de plus de 2 Mo).',
  version: 'Maze a été mis à jour pendant que vous écriviez : cette page ne pouvait plus envoyer ses modifications.',
  deleted: 'La fiche a été supprimée entre-temps.',
  forbidden: 'Votre accès à cette campagne a été retiré.',
  auth: 'Votre session a été fermée (mot de passe changé, déconnexion).',
};

function useSession() {
  const [, setTick] = useState(0);
  useEffect(() => subscribe(() => setTick((t) => t + 1)), []);
  return { lost: lostEdits(), stale: staleClient() };
}

export function download(entry) {
  const blob = new Blob([`<!doctype html><meta charset="utf-8"><title>${entry.title || 'Fiche'}</title>\n${entry.html}`],
    { type: 'text/html' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `${(entry.title || 'fiche').replace(/[\\/:*?"<>|]+/g, '_')} - modifications non fusionnées.html`;
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
  const when = new Date(entry.at).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' });
  const href = entry.campaignId ? `#/c/${entry.campaignId}/scenarios/${entry.docId}` : null;
  const purge = purgeDate(entry);
  const soon = purge.getTime() - Date.now() < 24 * 3600 * 1000;
  return (
    <div className="session-notice session-notice--lost" role="alert" data-lost-edits={entry.status}>
      <p className="session-notice__title">
        Modifications non fusionnées : « {entry.title || 'Fiche rédigée'} » <span className="muted">({when})</span>
      </p>
      <p>{REASONS[entry.reason] || 'Ces modifications n’ont pas pu être fusionnées dans la fiche.'}</p>
      {entry.status === 'saving' && <p>Enregistrement de votre texte dans l’historique de la fiche…</p>}
      {entry.status === 'version' && (
        <p>Votre texte n’est pas perdu : il est gardé dans l’historique de la fiche, version
          « <strong>{entry.label}</strong> » (bouton Historique de la fiche). Reprenez-y ce qui manque.</p>
      )}
      {entry.status === 'recovered' && (
        <p>Votre texte, gardé dans ce navigateur, vient d’être enregistré dans l’historique de la fiche, version
          « <strong>{entry.label}</strong> » ; il n’est plus dans le navigateur.</p>
      )}
      {entry.status === 'local' && (
        <p>Votre texte n’a pas pu être enregistré sur le serveur{entry.error ? ` (${entry.error})` : ''} :
          {entry.memoryOnly ? ' il n’est gardé que dans cette page, copiez-le ou téléchargez-le maintenant.'
            : ' il est gardé dans ce navigateur (pour votre compte seulement), et sera de nouveau envoyé à votre prochaine connexion. Copiez-le ou téléchargez-le pour le reprendre.'}</p>
      )}
      {entry.status === 'local' && !entry.memoryOnly && (
        <p className={soon ? 'session-notice__soon' : 'muted'} data-purge-soon={soon ? '' : undefined}>
          {soon ? 'Bientôt : ' : ''}ce texte sera effacé de ce navigateur le {purge.toLocaleString('fr-FR',
            { dateStyle: 'short', timeStyle: 'short' })}.</p>
      )}
      <div className="session-notice__actions">
        {href && entry.status !== 'saving' && <a className="btn btn--small btn--ghost" href={href}>Ouvrir la fiche</a>}
        {entry.html && (
          <>
            <button type="button" className="btn btn--small btn--ghost"
              onClick={async () => { await copyText(entry); setCopied(true); }}>{copied ? 'Copié' : 'Copier le texte'}</button>
            <button type="button" className="btn btn--small btn--ghost" onClick={() => download(entry)}>Télécharger</button>
          </>
        )}
        {entry.status === 'local' ? (
          <button type="button" className="btn btn--small btn--ghost" onClick={() => dismissLost(entry.id)}>
            Abandonner ce texte
          </button>
        ) : entry.status !== 'saving' && (
          <button type="button" className="btn btn--small btn--primary" onClick={() => dismissLost(entry.id)}>
            J’ai compris
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
          <p className="session-notice__title">Maze a été mis à jour</p>
          <p>Cette page date d’avant la mise à jour : ses enregistrements sont refusés. Rechargez-la pour continuer
            (une saisie en cours sera perdue : copiez-la d’abord).</p>
          <div className="session-notice__actions">
            <button type="button" className="btn btn--small btn--primary" onClick={() => reloadForUpdate(true)}>
              Recharger la page
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
    <Modal title="Textes gardés dans ce navigateur" onClose={onCancel}>
      <div data-logout-lost="">
        <p className="confirm-text">{entries.length} texte{entries.length > 1 ? 's' : ''} non fusionné{entries.length > 1 ? 's' : ''} de
          votre compte ({entries.map((e) => `« ${e.title || 'Fiche rédigée'} »`).join(', ')}) n’
          {entries.length > 1 ? 'ont' : 'a'} pas pu être enregistré{entries.length > 1 ? 's' : ''} sur le serveur et
          {entries.length > 1 ? ' sont gardés' : ' est gardé'} dans ce navigateur. Sur un poste partagé, ne les y laissez pas.</p>
        {failed && <p className="form-error">L’enregistrement a encore échoué : téléchargez ou abandonnez.</p>}
        <div className="session-notice__actions">
          <button type="button" className="btn btn--ghost" onClick={onCancel}>Annuler</button>
          <button type="button" className="btn btn--ghost" disabled={busy}
            onClick={() => leave(() => entries.forEach(download))}>Télécharger puis se déconnecter</button>
          <button type="button" className="btn btn--danger" disabled={busy} onClick={() => leave()}>
            Abandonner et se déconnecter</button>
          <button type="button" className="btn btn--primary" disabled={busy} onClick={save}>
            {busy ? 'Enregistrement…' : 'Enregistrer dans l’historique'}</button>
        </div>
      </div>
    </Modal>
  );
}
