import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../api.js';
import { closingDocuments } from '../../editor/live.js';

/**
 * Titre et statut de la fiche ouverte : enregistrés par l'API (après 700 ms de pause, ou tout de suite pour le
 * statut), diffusés aux autres MJ par le temps réel ; envoyés d'office (balise) au changement de fiche et à la
 * fermeture de l'onglet, qui prévient aussi s'il reste des modifications du texte non confirmées.
 */
export default function useDocMeta({ doc, setDoc, patchTreeDoc, toast, liveRef }) {
  const [metaState, setMetaState] = useState('saved');
  const pendingMeta = useRef(null);  // { id, title?, status? }
  const metaTimer = useRef(null);

  const saveMeta = useCallback(async () => {
    clearTimeout(metaTimer.current);
    const p = pendingMeta.current;
    if (!p) return;
    pendingMeta.current = null;
    setMetaState('saving');
    const { id, ...changes } = p;
    try {
      const saved = await api.updateDocument(id, changes);
      patchTreeDoc(saved.id, { title: saved.title, status: saved.status, updated_at: saved.updated_at });
      setDoc((d) => (d && d.id === saved.id ? { ...d, updated_at: saved.updated_at } : d));
      setMetaState(pendingMeta.current ? 'dirty' : 'saved');
    } catch (err) {
      // À renvoyer avec la prochaine modification de la même fiche ; si une autre fiche a été ouverte entre-temps,
      // jamais fusionné avec ses changements à elle (audit FRONT-6) : renvoyé tout de suite, à part.
      if (!pendingMeta.current || pendingMeta.current.id === id) {
        pendingMeta.current = { ...p, ...(pendingMeta.current || {}) };
      } else {
        api.saveDocumentBeacon(id, changes);
      }
      setMetaState('error');
      toast(err.message, 'error');
    }
  }, [patchTreeDoc, toast]); // eslint-disable-line react-hooks/exhaustive-deps

  const queueMeta = useCallback((changes, immediate = false) => {
    if (!doc) return;
    if (pendingMeta.current && pendingMeta.current.id !== doc.id) {
      // Titre ou statut d'une autre fiche encore en attente (échec d'enregistrement) : envoyé pour elle seule.
      const { id, ...rest } = pendingMeta.current;
      api.saveDocumentBeacon(id, rest);
      pendingMeta.current = null;
    }
    pendingMeta.current = { ...(pendingMeta.current || {}), id: doc.id, ...changes };
    setMetaState('dirty');
    clearTimeout(metaTimer.current);
    metaTimer.current = setTimeout(saveMeta, immediate ? 0 : 700);
  }, [doc, saveMeta]);

  /** Envoie tout de suite le titre en attente (changement de fiche, fermeture d'onglet). */
  const flushMeta = useCallback(() => {
    clearTimeout(metaTimer.current);
    const p = pendingMeta.current;
    if (!p) return;
    pendingMeta.current = null;
    const { id, ...changes } = p;
    api.saveDocumentBeacon(id, changes);
  }, []);

  useEffect(() => {
    const onUnload = (e) => {
      flushMeta();
      // Modifications pas encore confirmées par le serveur : on prévient avant de quitter.
      if ((liveRef.current && liveRef.current.state.pending > 0) || closingDocuments.size > 0) e.preventDefault();
    };
    window.addEventListener('beforeunload', onUnload);
    return () => { window.removeEventListener('beforeunload', onUnload); flushMeta(); };
  }, [flushMeta]); // eslint-disable-line react-hooks/exhaustive-deps

  return { metaState, setMetaState, pendingMeta, saveMeta, queueMeta, flushMeta };
}
