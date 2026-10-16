import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../../api.js';
import { DOC_COLOR, NEUTRAL_RING, PLACE_COLOR, placeKind } from '../../constants.js';
import { npcRingColors } from '../../graph/elements.js';
import { initialsAvatar } from '../../util.js';
import { t, useLocale } from '../../i18n/index.js';

/**
 * Données de l'onglet Scénarios : arbre des fiches, carte (PNJ et factions), lieux, et la liste des fiches qu'une
 * mention @ peut citer.
 */
export default function useScenarioData({ cid, campaignName, run }) {
  const [tree, setTree] = useState(null);
  const [graph, setGraph] = useState(null);

  const loadTree = useCallback(async () => {
    const data = await run(() => api.documents(cid));
    if (data) setTree(data);
    return data;
  }, [cid, run]);

  const loadGraph = useCallback(async () => {
    const g = await run(() => api.graph(cid));
    if (g) setGraph(g);
    return g;
  }, [cid, run]);

  const [places, setPlaces] = useState(null);
  const [placeKinds, setPlaceKinds] = useState(null);
  const loadPlaces = useCallback(async () => {
    const p = await api.places(cid).catch(() => null);
    setPlaces(p ? p.places : []);
    setPlaceKinds(p?.kinds || null);
  }, [cid]);

  useEffect(() => { loadTree(); loadGraph(); loadPlaces(); }, [loadTree, loadGraph, loadPlaces]);

  // Sous-titres et noms par défaut traduits : recalculés au changement de langue.
  const locale = useLocale();
  const entities = useMemo(() => {
    if (!graph) return [];
    const ring = npcRingColors(graph);
    return [
      ...graph.npcs.map((n) => {
        const color = ring.get(n.id) || NEUTRAL_RING;
        return { kind: 'npc', id: n.id, name: n.name, sub: n.title || t('PNJ'), color,
          image: n.portrait_thumb || initialsAvatar(n.name, color), summary: n.summary, status: n.status };
      }),
      ...graph.factions.map((f) => ({ kind: 'faction', id: f.id, name: f.name, sub: f.kind || t('Faction'),
        color: f.color, image: f.emblem_thumb, summary: f.summary })),
      ...(places || []).map((p) => {
        const parent = places.find((x) => x.id === p.parent_id);
        return { kind: 'place', id: p.id, name: p.name, color: PLACE_COLOR, image: p.thumb, summary: p.summary,
          sub: parent ? `${placeKind(p.kind, placeKinds).label}, ${parent.name}` : placeKind(p.kind, placeKinds).label };
      }),
      // Autres fiches rédigées : renvois du type « voir la page La filature de Rathbone ».
      ...(tree?.documents || []).map((d) => {
        const parent = tree.documents.find((x) => x.id === d.parent_id);
        const name = d.kind === 'campaign' ? t('Fiche de campagne') : (d.title || t('Sans titre'));
        const sub = d.kind === 'campaign' ? campaignName : d.kind === 'scenario' ? t('Scénario')
          : t('Page de « {title} »', { title: parent?.title || t('Sans titre') });
        return { kind: 'doc', id: d.id, name, sub, color: DOC_COLOR, icon: 'book', status: d.status };
      }),
    ];
  }, [graph, places, placeKinds, tree, campaignName, locale]); // eslint-disable-line react-hooks/exhaustive-deps

  const patchTreeDoc = useCallback((id, changes) => {
    setTree((cur) => cur && { ...cur, documents: cur.documents.map((d) => (d.id === id ? { ...d, ...changes } : d)) });
  }, []);

  return { tree, graph, places, loadTree, loadGraph, loadPlaces, entities, patchTreeDoc };
}
