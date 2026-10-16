import { useEffect, useState } from 'react';
import { t } from '../../i18n/index.js';

/** Libellé d'enregistrement : texte (temps réel) et titre/statut (API). */
export function saveLabel(live, metaState) {
  if (!live) return { key: 'saving', text: t('Connexion…') };
  // Connexion refusée par le serveur faute de place (4429, audit PERF-9) : sa raison, au lieu de « Connexion… ».
  if (live.status === 'offline') {
    return { key: 'error', text: live.busy
      ? t('{reason} : modifications conservées, envoi à la reconnexion', { reason: t(live.busy) })
      : t('Hors ligne : modifications conservées, envoi à la reconnexion') };
  }
  if (live.status === 'connecting' && live.busy) {
    return { key: 'error', text: t('{reason} : nouvel essai automatique', { reason: t(live.busy) }) };
  }
  if (live.status === 'connecting' || live.status === 'waiting') return { key: 'saving', text: t('Connexion…') };
  if (metaState === 'error') return { key: 'error', text: t('Échec de l’enregistrement du titre') };
  if (live.pending > 0 || metaState === 'saving' || metaState === 'dirty') return { key: 'saving', text: t('Enregistrement…') };
  return { key: 'saved', text: t('Enregistré') };
}

/**
 * État d'enregistrement. « Enregistrement… » n'apparaît que si l'envoi dure : pendant la frappe, chaque touche
 * l'afficherait un instant, et le changement de largeur faisait passer le fil d'Ariane à la ligne (tout sautait).
 */
export function SaveState({ status, onRetry }) {
  const [shown, setShown] = useState(status);
  useEffect(() => {
    if (status.key !== 'saving' || shown.key === 'saving') { setShown(status); return undefined; }
    const timer = setTimeout(() => setShown(status), 1200);
    return () => clearTimeout(timer);
  }, [status.key, status.text]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <span className={`save-state save-state--${shown.key}`} role="status">
      {shown.text}
      {onRetry && <button type="button" className="link-btn" onClick={onRetry}>{t('Réessayer')}</button>}
    </span>
  );
}

