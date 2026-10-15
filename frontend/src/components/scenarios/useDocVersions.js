import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../api.js';
import { versionDate } from '../HistoryPanel.jsx';

/**
 * Historique des versions de la fiche ouverte (chargé quand l'onglet Historique est affiché) : aperçu d'une version,
 * version nommée, renommage, suppression, restauration (contenu remplacé pour tous les MJ ; la fin est annoncée par
 * le temps réel, `restoringRef`).
 */
export default function useDocVersions({ doc, sideTab, reloadKey, run, toast, confirm, editorRef, restoringRef, setSideOpen }) {
  const [versions, setVersions] = useState(null);    // historique de la fiche ouverte
  const [versionView, setVersionView] = useState(null); // aperçu : { version, content }
  const docIdRef = useRef(null);
  docIdRef.current = doc?.id ?? null;
  const loadVersions = useCallback(async () => {
    if (!doc) return;
    const id = doc.id;
    try {
      const data = await api.versions(id);
      if (docIdRef.current === id) setVersions(data.versions);
    } catch (err) {
      toast(err.message, 'error');
    }
  }, [doc?.id, toast]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (sideTab === 'history' && doc) loadVersions();
  }, [sideTab, doc?.id, reloadKey, loadVersions]); // eslint-disable-line react-hooks/exhaustive-deps

  const openVersion = async (v) => {
    setVersionView({ version: v, content: null });
    setSideOpen(false);
    const full = await run(() => api.getVersion(doc.id, v.id));
    if (full) setVersionView((cur) => (cur?.version.id === v.id ? { version: v, content: full.content } : cur));
  };

  const createVersion = async (label) => {
    const content = editorRef.current?.getHtml();
    const created = await run(() => api.createVersion(doc.id, { label, ...(content != null ? { content } : {}) }),
      'Version enregistrée');
    if (!created) return false;
    loadVersions();
    return true;
  };

  const renameVersion = async (vid, label) => {
    const done = await run(() => api.updateVersion(doc.id, vid, { label }));
    if (!done) return false;
    setVersionView((cur) => (cur?.version.id === vid ? { ...cur, version: done } : cur));
    loadVersions();
    return true;
  };

  const deleteVersion = async (v) => {
    const yes = await confirm({
      title: 'Supprimer cette version',
      message: `La version du ${versionDate(v)} sera supprimée de l’historique. Le contenu actuel de la fiche ne change pas.`,
      confirmLabel: 'Supprimer',
    });
    if (!yes) return;
    if (await run(() => api.deleteVersion(doc.id, v.id)) === undefined) return;
    if (versionView?.version.id === v.id) setVersionView(null);
    loadVersions();
  };

  const restoreVersion = async () => {
    const v = versionView?.version;
    if (!v) return;
    const yes = await confirm({
      title: 'Restaurer cette version',
      message: `Le contenu de la fiche sera remplacé par la version du ${versionDate(v)} pour tous les MJ. `
        + 'L’état actuel est d’abord gardé dans l’historique : vous pourrez y revenir. Le titre ne change pas.',
      confirmLabel: 'Restaurer',
    });
    if (!yes) return;
    restoringRef.current = true;
    const current = editorRef.current?.getHtml();
    const done = await run(() => api.restoreVersion(doc.id, v.id, current != null ? { current } : {}));
    if (!done) restoringRef.current = false;
  };

  return { versions, setVersions, versionView, setVersionView, openVersion, createVersion, renameVersion, deleteVersion,
    restoreVersion };
}
