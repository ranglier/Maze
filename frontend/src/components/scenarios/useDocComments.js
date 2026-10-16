import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api.js';
import { t } from '../../i18n/index.js';

/**
 * Commentaires de la fiche ouverte : fils ancrés dans le texte (marque de l'éditeur), brouillon sur la sélection,
 * réponses, résolution, modification, suppression ; surlignage des fils ouverts et du fil choisi dans le texte.
 */
export default function useDocComments({ doc, live, liveState, run, toast, confirm, editorRef, showComments }) {
  const [threads, setThreads] = useState(null);      // fils de commentaires de la fiche
  const [anchors, setAnchors] = useState({});        // id du fil > position et texte ancré
  const [draft, setDraft] = useState(null);          // commentaire en cours de rédaction { quote }
  const [activeComment, setActiveComment] = useState(null);

  const reloadComments = useCallback(async () => {
    if (!doc) return;
    const list = await run(() => api.comments(doc.id));
    if (list) setThreads(list);
  }, [doc?.id, run]); // eslint-disable-line react-hooks/exhaustive-deps

  // Surlignage dans le texte : fils ouverts et fil sélectionné.
  useEffect(() => {
    const openIds = new Set((threads || []).filter((th) => !th.resolved).map((th) => th.id));
    editorRef.current?.setCommentState(openIds, activeComment);
  }, [threads, activeComment, live, liveState?.synced]); // eslint-disable-line react-hooks/exhaustive-deps

  const startComment = (info) => {
    if (!info) {
      toast(t('Sélectionnez d’abord le passage à commenter.'), 'info');
      return;
    }
    setDraft(info);
    setActiveComment(null);
    showComments();
  };

  const submitDraft = async (body) => {
    const created = await run(() => api.createComment(doc.id, { body, quote: draft?.quote || '' }));
    if (!created) return false;
    if (!editorRef.current?.applyComment(created.id)) {
      toast(t('Le passage commenté a été supprimé entre-temps : le commentaire est enregistré sans ancrage.'), 'info');
    }
    setDraft(null);
    setActiveComment(created.id);
    await reloadComments();
    return true;
  };

  const cancelDraft = () => {
    editorRef.current?.cancelComment();
    setDraft(null);
  };

  const selectComment = (id) => {
    setActiveComment(id);
    editorRef.current?.revealComment(id);
  };

  const replyComment = async (threadId, body) => {
    const created = await run(() => api.createComment(doc.id, { body, parent_id: threadId }));
    if (!created) return false;
    await reloadComments();
    return true;
  };

  const resolveComment = async (thread, resolved) => {
    const done = await run(() => api.updateComment(thread.id, { resolved }), resolved ? t('Fil résolu') : t('Fil rouvert'));
    if (!done) return;
    if (resolved && activeComment === thread.id) setActiveComment(null);
    reloadComments();
  };

  const editComment = async (id, body) => {
    const done = await run(() => api.updateComment(id, { body }));
    if (!done) return false;
    await reloadComments();
    return true;
  };

  const deleteComment = async (comment) => {
    const isThread = comment.parent_id === null;
    const yes = await confirm({
      title: isThread ? t('Supprimer le fil') : t('Supprimer la réponse'),
      message: isThread ? t('Le commentaire et toutes ses réponses seront supprimés. Le texte de la fiche n’est pas modifié.')
        : t('Cette réponse sera supprimée.'),
      confirmLabel: t('Supprimer'),
    });
    if (!yes) return;
    const done = await run(() => api.deleteComment(comment.id));
    if (done === undefined) return;
    if (isThread) {
      editorRef.current?.removeComment(comment.id);
      if (activeComment === comment.id) setActiveComment(null);
    }
    reloadComments();
  };

  const openThreads = (threads || []).filter((th) => !th.resolved).length;

  return { threads, setThreads, anchors, setAnchors, draft, setDraft, activeComment, setActiveComment, startComment,
    submitDraft, cancelDraft, selectComment, replyComment, resolveComment, editComment, deleteComment, openThreads };
}
