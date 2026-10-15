import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../../api.js';
import { DOC_COLOR, NEUTRAL_RING, PLACE_COLOR, placeKind } from '../../constants.js';
import { npcRingColors } from '../../graph/elements.js';
import { initialsAvatar } from '../../util.js';

/**
 * Données de l'onglet Scénarios : arbre des fiches, carte (PNJ et factions), lieux, et la liste des fiches qu'une
 * mention @ peut citer.
 */
export default function useScenarioData({ cid, campaignName, run }) {
  const [tree, setTree] = useState(null);
  const [graph, setGraph] = useState(null);

  const loadTree = useCallback(async () => {
    const t = await run(() => api.documents(cid));
    if (t) setTree(t);
    return t;
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

  const entities = useMemo(() => {
    if (!graph) return [];
    const ring = npcRingColors(graph);
    return [
      ...graph.npcs.map((n) => {
        const color = ring.get(n.id) || NEUTRAL_RING;
        return { kind: 'npc', id: n.id, name: n.name, sub: n.title || 'PNJ', color,
          image: n.portrait_thumb || initialsAvatar(n.name, color), summary: n.summary, status: n.status };
      }),
      ...graph.factions.map((f) => ({ kind: 'faction', id: f.id, name: f.name, sub: f.kind || 'Faction',
        color: f.color, image: f.emblem_thumb, summary: f.summary })),
      ...(places || []).map((p) => {
        const parent = places.find((x) => x.id === p.parent_id);
        return { kind: 'place', id: p.id, name: p.name, color: PLACE_COLOR, image: p.thumb, summary: p.summary,
          sub: parent ? `${placeKind(p.kind, placeKinds).label}, ${parent.name}` : placeKind(p.kind, placeKinds).label };
      }),
      // Autres fiches rédigées : renvois du type « voir la page La filature de Rathbone ».
      ...(tree?.documents || []).map((d) => {
        const parent = tree.documents.find((x) => x.id === d.parent_id);
        const name = d.kind === 'campaign' ? 'Fiche de campagne' : (d.title || 'Sans titre');
        const sub = d.kind === 'campaign' ? campaignName : d.kind === 'scenario' ? 'Scénario'
          : `Page de « ${parent?.title || 'Sans titre'} »`;
        return { kind: 'doc', id: d.id, name, sub, color: DOC_COLOR, icon: 'book', status: d.status };
      }),
    ];
  }, [graph, places, placeKinds, tree, campaignName]);

  const patchTreeDoc = useCallback((id, changes) => {
    setTree((t) => t && { ...t, documents: t.documents.map((d) => (d.id === id ? { ...d, ...changes } : d)) });
  }, []);

  return { tree, graph, places, loadTree, loadGraph, loadPlaces, entities, patchTreeDoc };
}
