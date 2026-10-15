import { useState } from 'react';
import { storage } from '../../util.js';

/** Panneau latéral des fiches rédigées : onglet (Sommaire, Commentaires, Historique, retenu dans le navigateur) et,
 * sur petit écran, ouverture en surimpression. */
export default function useSidePanel() {
  const [sideTab, setSideTab] = useState(() => storage.get('maze:docs:side') || 'toc');
  const [sideOpen, setSideOpen] = useState(false);   // petits écrans : panneau latéral en surimpression
  const pick = (tab, open) => {
    setSideTab(tab);
    storage.set('maze:docs:side', tab);
    if (open) setSideOpen(true);
  };
  return {
    sideTab, sideOpen, setSideOpen,
    selectToc: () => pick('toc', false),
    showToc: () => pick('toc', true),
    showHistory: () => pick('history', true),
    showComments: () => pick('comments', true),
  };
}
