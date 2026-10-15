import { NEUTRAL_RING, PLACE_COLOR, placeKind } from '../constants.js';
import { npcRingColors } from '../graph/elements.js';
import { initialsAvatar } from '../util.js';

/** Fiches citables avec @ dans un champ de PNJ, faction ou lieu (PNJ, factions, et lieux s'ils sont fournis). */
export function mentionEntities(graph, places = null, kinds = null) {
  if (!graph) return null;
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
      const label = placeKind(p.kind, kinds).label;
      return { kind: 'place', id: p.id, name: p.name, color: PLACE_COLOR, image: p.thumb, summary: p.summary,
        sub: parent ? `${label}, ${parent.name}` : label };
    }),
  ];
}
