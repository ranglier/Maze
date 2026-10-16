import { NEUTRAL_RING } from '../constants.js';
import { t } from '../i18n/core.js';
import { typeIndex, typeOf } from '../relationTypes.js';
import { initialsAvatar, memberNodeId, nodeId } from '../util.js';

export const DEFAULT_FILTERS = {
  hiddenTypes: [],
  hiddenFactions: [],
  showMemberships: true,
  showDead: true,
  showSecret: true,
};

/** Couleur d'anneau d'un PNJ : faction qu'il dirige, sinon première faction. */
export function npcRingColors(graph) {
  const factionColor = new Map(graph.factions.map((f) => [f.id, f.color]));
  const best = new Map();
  for (const m of graph.memberships) {
    if (m.npc_id == null) continue; // sous-faction
    const current = best.get(m.npc_id);
    if (!current || (m.is_leader && !current.is_leader)) best.set(m.npc_id, m);
  }
  const result = new Map();
  for (const [npcId, m] of best) result.set(npcId, factionColor.get(m.faction_id) || NEUTRAL_RING);
  return result;
}

/**
 * `scope` (facultatif) : filtre sur une fiche rédigée.
 *   nodes   fiches citées (affichées normalement)
 *   context voisins directs affichés en retrait, ou null
 * Toutes les autres fiches sont masquées.
 * Libellés traduits ici (« Dirigeant ») : appeler à nouveau quand la langue change (useLocale() dans les dépendances).
 */
export function buildElements(graph, filters, scope = null) {
  const outOfScope = (id) => scope && !scope.nodes.has(id) && !scope.context?.has(id);
  const scopeClass = (id) => (!scope ? null : scope.nodes.has(id) ? 'in-scope' : scope.context?.has(id) ? 'context' : 'hidden');
  const ring = npcRingColors(graph);
  const hiddenTypes = new Set(filters.hiddenTypes);
  const hiddenFactions = new Set(filters.hiddenFactions);
  const factionColor = new Map(graph.factions.map((f) => [f.id, f.color]));
  const types = typeIndex(graph);
  const elements = [];

  for (const n of graph.npcs) {
    const color = ring.get(n.id) || NEUTRAL_RING;
    const classes = ['npc', `status-${n.status}`];
    if (n.status === 'mort' && !filters.showDead) classes.push('hidden');
    if (scope) classes.push(scopeClass(nodeId('npc', n.id)));
    elements.push({
      group: 'nodes',
      data: {
        id: nodeId('npc', n.id),
        kind: 'npc',
        eid: n.id,
        label: n.status === 'mort' ? `† ${n.name}` : n.name,
        image: n.portrait_thumb || initialsAvatar(n.name, color),
        ring: color,
      },
      position: n.pos_x != null ? { x: n.pos_x, y: n.pos_y } : undefined,
      classes: classes.join(' '),
    });
  }

  for (const f of graph.factions) {
    const classes = ['faction'];
    if (f.emblem_thumb) classes.push('has-emblem');
    if (hiddenFactions.has(f.id) && !scope?.nodes.has(nodeId('faction', f.id))) classes.push('hidden');
    if (scope) classes.push(scopeClass(nodeId('faction', f.id)));
    elements.push({
      group: 'nodes',
      data: {
        id: nodeId('faction', f.id),
        kind: 'faction',
        eid: f.id,
        label: f.name,
        color: f.color,
        image: f.emblem_thumb || 'none',
      },
      position: f.pos_x != null ? { x: f.pos_x, y: f.pos_y } : undefined,
      classes: classes.join(' '),
    });
  }

  // Appartenances : PNJ vers faction, ou faction vers la faction dont elle est membre (sous-faction, orientée
  // vers la faction parente). Masquées avec le filtre « appartenances » ou avec l'une ou l'autre faction.
  for (const m of graph.memberships) {
    const sub = m.member_faction_id != null;
    const member = memberNodeId(m);
    const classes = ['membership'];
    if (sub) classes.push('faction-member');
    if (m.is_leader) classes.push('leader');
    if (!filters.showMemberships || hiddenFactions.has(m.faction_id) || (sub && hiddenFactions.has(m.member_faction_id))) classes.push('hidden');
    if (outOfScope(member) || outOfScope(nodeId('faction', m.faction_id))) classes.push('hidden');
    else if (scope && !(scope.nodes.has(member) && scope.nodes.has(nodeId('faction', m.faction_id)))) classes.push('context');
    elements.push({
      group: 'edges',
      data: {
        id: `mem-${m.id}`,
        kind: 'membership',
        eid: m.id,
        source: member,
        target: nodeId('faction', m.faction_id),
        color: factionColor.get(m.faction_id) || NEUTRAL_RING,
        label: m.is_leader ? (m.role || t('Dirigeant')) : m.role,
      },
      classes: classes.join(' '),
    });
  }

  for (const r of graph.relations) {
    const type = typeOf(types, r.type);
    const classes = ['relation'];
    if (r.directed) classes.push('directed');
    if (r.secret) classes.push('secret');
    if (hiddenTypes.has(r.type) || (r.secret && !filters.showSecret)) classes.push('hidden');
    const src = nodeId(r.source_kind, r.source_id);
    const dst = nodeId(r.target_kind, r.target_id);
    if (outOfScope(src) || outOfScope(dst)) classes.push('hidden');
    else if (scope && !(scope.nodes.has(src) && scope.nodes.has(dst))) classes.push('context');
    elements.push({
      group: 'edges',
      data: {
        id: `rel-${r.id}`,
        kind: 'relation',
        eid: r.id,
        source: nodeId(r.source_kind, r.source_id),
        target: nodeId(r.target_kind, r.target_id),
        color: type.color,
        label: r.label || type.label,
      },
      classes: classes.join(' '),
    });
  }
  return elements;
}
