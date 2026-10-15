// Fiches rédigées : statuts et ordre de l'arbre (fiche de campagne > scénarios > pages).

export const DOC_STATUS = {
  brouillon: { label: 'Brouillon' },
  pret: { label: 'Prêt' },
  en_cours: { label: 'En cours' },
  joue: { label: 'Joué' },
};

export function childrenOf(docs, parentId) {
  return docs.filter((d) => d.parent_id === parentId).sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
}
