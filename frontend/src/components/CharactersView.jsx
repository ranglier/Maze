import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import { NPC_STATUS } from '../constants.js';
import { npcRingColors } from '../graph/elements.js';
import { initialsAvatar, nodeId, normalize, parseNodeId, storage } from '../util.js';
import CreateDialog from './CreateDialog.jsx';
import EntityPanel from './EntityPanel.jsx';
import { EntryShell } from './EntryNotes.jsx';
import RelationDialog from './RelationDialog.jsx';
import { Icon, useAction } from './ui.jsx';
import { useTreeDrag } from './treeDrag.js';

/*
 * Onglet Personnages : index de tous les PNJ et factions de la campagne (à gauche, rangés par
 * faction ou par ordre alphabétique, avec recherche et filtres), fiche en pleine page à droite.
 */

const byName = (a, b) => a.name.localeCompare(b.name, 'fr', { sensitivity: 'base' });
// Ordre choisi par le MJ (glisser-déposer), sinon alphabétique (sort_order à 0 tant que rien n'est rangé).
const byOrder = (a, b) => (a.sort_order || 0) - (b.sort_order || 0) || byName(a, b);

export default function CharactersView({
  campaign, user, header, trailing, entity, editRequested, commentId, onNavigate, onEditDone, onCommentDone, onGraphLoaded,
}) {
  const cid = campaign.id;
  const [graph, setGraph] = useState(null);
  const [version, setVersion] = useState(0);
  const [dialog, setDialog] = useState(null);
  const [entityEditing, setEntityEditing] = useState(false);
  const run = useAction();

  const reload = useCallback(async () => {
    const g = await run(() => api.graph(cid));
    if (g) {
      setGraph(g);
      setVersion((v) => v + 1);
      onGraphLoaded?.(g);
    }
  }, [cid, run, onGraphLoaded]);
  useEffect(() => { reload(); }, [reload]);

  const go = (nid) => onNavigate(nid ? `#/c/${cid}/personnages/${nid}` : `#/c/${cid}/personnages`);

  const lookup = useMemo(() => {
    const byNode = new Map();
    const names = new Map();
    for (const n of graph?.npcs || []) { byNode.set(nodeId('npc', n.id), n); names.set(normalize(n.name), nodeId('npc', n.id)); }
    for (const f of graph?.factions || []) { byNode.set(nodeId('faction', f.id), f); names.set(normalize(f.name), nodeId('faction', f.id)); }
    return { byNode, names };
  }, [graph]);
  const nodeName = useCallback((id) => lookup.byNode.get(id)?.name || 'Fiche supprimée', [lookup]);
  const resolveRef = useCallback((name) => lookup.names.get(normalize(name)), [lookup]);

  const selected = entity && graph && lookup.byNode.has(entity) ? parseNodeId(entity) : null;
  // Arrivée depuis une création (?edit=1) : la demande est mémorisée avant de nettoyer l'adresse,
  // sinon le formulaire se refermerait dès que ?edit disparaît.
  const [editFor, setEditFor] = useState(null);
  useEffect(() => {
    if (editRequested && selected) {
      setEditFor(entity);
      onEditDone?.();
    }
  }, [editRequested, selected, entity, onEditDone]);
  const startInEdit = editFor === entity && Boolean(selected);

  return (
    <div className="scenarios characters">
      <header className="topbar">
        {header}
        <div className="topbar__right">
        <div className="toolbar">
          <button type="button" className="btn btn--ghost" onClick={() => setDialog({ type: 'create', kind: 'npc' })}>
            <Icon name="person" size={16} /><span className="btn__label">PNJ</span>
          </button>
          <button type="button" className="btn btn--ghost" onClick={() => setDialog({ type: 'create', kind: 'faction' })}>
            <Icon name="banner" size={16} /><span className="btn__label">Faction</span>
          </button>
          <span className="toolbar__sep" />
        </div>
        {trailing && <div className="topbar__end">{trailing}</div>}
        </div>
      </header>
      <div className="places__body">
        <CharacterList graph={graph} campaignId={cid} campaignName={campaign.name} currentId={selected ? entity : null} onOpen={go}
          onCreate={(kind) => setDialog({ type: 'create', kind })}
          onReorder={async (kind, ids) => { if (await run(() => api.reorderEntities(cid, kind, ids)) !== undefined) reload(); }} />
        {selected ? (
          <EntryShell key={entity} kind={selected.kind} id={selected.id} user={user} stamp={version} editing={entityEditing}
            commentId={commentId} mainClassName="characters__main" resolveRef={resolveRef}
            onCommentDone={onCommentDone} onRestored={reload}>
            <EntityPanel
              page
              kind={selected.kind}
              id={selected.id}
              graph={graph}
              version={version}
              startInEdit={startInEdit}
              nodeName={nodeName}
              resolveRef={resolveRef}
              onOpenNode={go}
              onOpenEdge={(eid) => {
                const r = graph.relations.find((x) => `rel-${x.id}` === eid);
                if (r) setDialog({ type: 'relation', relation: r });
              }}
              onStartLink={(nid) => setDialog({ type: 'relation', source: nid })}
              onChanged={reload}
              onDeleted={async () => { go(null); await reload(); }}
              onEditingChange={setEntityEditing}
            />
          </EntryShell>
        ) : (
          <main className="doc-main characters__main">
            {!graph ? <div className="doc-loading" aria-busy="true">Chargement…</div>
              : entity ? (
                <div className="doc"><p className="muted">Cette fiche n’existe plus.</p></div>
              ) : (
                <CharactersHome graph={graph} campaignName={campaign.name} onOpen={go}
                  onCreate={(kind) => setDialog({ type: 'create', kind })} />
              )}
          </main>
        )}
      </div>
      {dialog?.type === 'create' && (
        <CreateDialog
          campaignId={cid}
          kind={dialog.kind}
          usedColors={graph?.factions.map((f) => f.color) || []}
          onClose={() => setDialog(null)}
          onCreated={async (kind, created) => {
            setDialog(null);
            await reload();
            onNavigate(`#/c/${cid}/personnages/${nodeId(kind, created.id)}?edit=1`);
          }}
        />
      )}
      {dialog?.type === 'relation' && graph && (
        <RelationDialog
          campaignId={cid}
          graph={graph}
          relation={dialog.relation}
          source={dialog.source}
          onClose={() => setDialog(null)}
          onTypesChanged={reload}
          onSaved={async () => { setDialog(null); await reload(); }}
        />
      )}
    </div>
  );
}

// --- Liste ---------------------------------------------------------------------------------

function CharacterList({ graph, campaignName, currentId, onOpen, onCreate, onReorder }) {
  const [query, setQuery] = useState('');
  const [show, setShowState] = useState(() => storage.get('maze:characters:show') || 'all');
  const [grouping, setGroupingState] = useState(() => storage.get('maze:characters:group') || 'faction');
  const [status, setStatus] = useState('');
  // Factions repliées par défaut : seules s'ouvrent celles que le MJ déplie (et celle de la fiche ouverte).
  const [expanded, setExpanded] = useState(() => new Set());
  // Fiche ouverte : ses factions se déplient (une fois ; le MJ peut les replier).
  const expandedFor = useRef(null);
  useEffect(() => {
    if (!graph || !currentId?.startsWith('npc-') || expandedFor.current === currentId) return;
    expandedFor.current = currentId;
    const npc = Number(currentId.slice(4));
    const ids = graph.memberships.filter((m) => m.npc_id === npc).map((m) => m.faction_id);
    if (ids.length) setExpanded((s) => (ids.every((id) => s.has(id)) ? s : new Set([...s, ...ids])));
  }, [currentId, graph]);
  const setShow = (v) => { setShowState(v); storage.set('maze:characters:show', v); };
  const setGrouping = (v) => { setGroupingState(v); storage.set('maze:characters:group', v); };

  const model = useMemo(() => {
    if (!graph) return null;
    const q = normalize(query.trim());
    const ring = npcRingColors(graph);
    const npcMatch = (n) => (!q || normalize(`${n.name} ${n.title || ''} ${(n.tags || []).join(' ')}`).includes(q))
      && (!status || n.status === status);
    const factionMatch = (f) => !q || normalize(`${f.name} ${f.kind || ''} ${(f.tags || []).join(' ')}`).includes(q);
    const npcs = graph.npcs.filter(npcMatch).sort(byName);
    const factions = graph.factions.filter(factionMatch).sort(byName);
    const membersOf = new Map(graph.factions.map((f) => [f.id, []]));
    const inFaction = new Set();
    for (const m of graph.memberships) {
      inFaction.add(m.npc_id);
      membersOf.get(m.faction_id)?.push(m);
    }
    return { npcs, factions, membersOf, inFaction, ring, npcMatch, all: graph.npcs.length + graph.factions.length };
  }, [graph, query, status]);

  // Glisser-déposer (vue par faction, sans filtre) : ordre des factions, et des PNJ dans leur groupe.
  const dnd = useTreeDrag((dragged, target, where) => {
    const place = (list, id) => {
      const rest = list.filter((x) => x !== dragged.id);
      rest.splice(rest.indexOf(id) + (where === 'after' ? 1 : 0), 0, dragged.id);
      return rest;
    };
    if (dragged.group === 'factions') {
      onReorder('faction', place(graph.factions.slice().sort(byOrder).map((f) => f.id), target.id));
      return;
    }
    // PNJ : nouvel ordre du groupe, reporté dans l'ordre global (les places des autres PNJ ne bougent pas).
    const groupIds = place(target.groupIds, target.id);
    const global = graph.npcs.slice().sort(byOrder).map((n) => n.id);
    const inGroup = new Set(groupIds);
    let k = 0;
    onReorder('npc', global.map((id) => (inGroup.has(id) ? groupIds[k++] : id)));
  });
  if (!model) return <aside className="doc-tree" aria-busy="true" />;
  const npcById = new Map(graph.npcs.map((n) => [n.id, n]));
  const filtering = Boolean(query.trim() || status);
  const canDrag = !filtering && grouping === 'faction' && Boolean(onReorder);

  const npcItem = (n, depth, role, leader, group = null) => {
    const id = nodeId('npc', n.id);
    const row = group && { key: `${group.key}:${n.id}`, id: n.id, group: group.key, groupIds: group.ids, label: n.name };
    return (
      <li key={`${id}-${depth}-${role || ''}`}>
        <div className={`doc-tree__item${id === currentId ? ' is-active' : ''}${row ? dnd.className(row.key) : ''}`}
          style={{ '--depth': depth }}
          {...(row ? dnd.props(row, { enabled: canDrag, accepts: (d) => d.group === row.group }) : {})}>
          <span className="doc-tree__caret" />
          <button type="button" className="doc-tree__link" onClick={() => onOpen(id)} aria-current={id === currentId ? 'page' : undefined}>
            <img className="char-list__avatar" src={n.portrait_thumb || initialsAvatar(n.name, model.ring.get(n.id) || '#5d6b80')} alt="" />
            <span className="doc-tree__label">{n.status === 'mort' ? `† ${n.name}` : n.name}</span>
            {leader && <Icon name="star" size={12} title="Dirigeant" />}
          </button>
        </div>
      </li>
    );
  };

  const factionItem = (f, members, draggable = false) => {
    const id = nodeId('faction', f.id);
    const open = filtering || expanded.has(f.id);
    const row = { key: `f:${f.id}`, id: f.id, group: 'factions', label: f.name };
    return (
      <li key={id}>
        <div className={`doc-tree__item${id === currentId ? ' is-active' : ''}${dnd.className(row.key)}`} style={{ '--depth': 0 }}
          {...dnd.props(row, { enabled: canDrag && draggable, accepts: (d) => d.group === 'factions' })}>
          {members && members.length > 0 ? (
            <button type="button" className="doc-tree__caret" aria-label={open ? 'Replier' : 'Déplier'} aria-expanded={open}
              onClick={() => setExpanded((s) => { const n = new Set(s); if (n.has(f.id)) n.delete(f.id); else n.add(f.id); return n; })}>
              <Icon name={open ? 'chevron' : 'right'} size={13} />
            </button>
          ) : <span className="doc-tree__caret" />}
          <button type="button" className="doc-tree__link" onClick={() => onOpen(id)} aria-current={id === currentId ? 'page' : undefined}>
            <span className="char-list__seal" style={{ background: f.color }} />
            <span className="doc-tree__label char-list__faction">{f.name}</span>
            {members && <span className="char-list__count">{members.length}</span>}
          </button>
        </div>
        {members && members.length > 0 && open && (
          <ul>
            {members.map((m) => npcItem(npcById.get(m.npc_id), 1, m.role, m.is_leader,
              { key: `g${f.id}`, ids: members.map((x) => x.npc_id) }))}
          </ul>
        )}
      </li>
    );
  };

  let content;
  if (grouping === 'faction' && show !== 'npc') {
    // Factions avec leurs membres (filtrés), puis les PNJ sans faction.
    const visible = new Set(model.npcs.map((n) => n.id));
    const groups = graph.factions.slice().sort(byOrder).map((f) => {
      const members = model.membersOf.get(f.id)
        .filter((m) => visible.has(m.npc_id))
        .sort((a, b) => {
          const na = npcById.get(a.npc_id);
          const nb = npcById.get(b.npc_id);
          return (na.sort_order || 0) - (nb.sort_order || 0) || Number(b.is_leader) - Number(a.is_leader) || byName(na, nb);
        });
      const keep = show === 'faction' ? model.factions.includes(f) : (model.factions.includes(f) || members.length > 0);
      return keep ? factionItem(f, show === 'faction' ? null : members, true) : null;
    }).filter(Boolean);
    const loners = show === 'faction' ? [] : model.npcs.filter((n) => !model.inFaction.has(n.id)).sort(byOrder);
    const lonerGroup = { key: 'none', ids: loners.map((n) => n.id) };
    content = (
      <>
        <ul className="doc-tree__list">{groups}</ul>
        {loners.length > 0 && (
          <>
            <div className="doc-tree__section"><span>Sans faction</span><span className="char-list__count">{loners.length}</span></div>
            <ul className="doc-tree__list">{loners.map((n) => npcItem(n, 0, null, false, lonerGroup))}</ul>
          </>
        )}
      </>
    );
  } else {
    const items = [
      ...(show !== 'faction' ? model.npcs.map((n) => ({ n, kind: 'npc', name: n.name })) : []),
      ...(show !== 'npc' ? model.factions.map((f) => ({ f, kind: 'faction', name: f.name })) : []),
    ].sort(byName);
    content = (
      <ul className="doc-tree__list">
        {items.map((i) => (i.kind === 'npc' ? npcItem(i.n, 0) : factionItem(i.f, null)))}
      </ul>
    );
  }
  const shownCount = (show !== 'faction' ? model.npcs.length : 0) + (show !== 'npc' ? model.factions.length : 0);

  return (
    <aside className="doc-tree char-list" aria-label="Personnages de la campagne">
      <button type="button" className={`doc-tree__root${currentId ? '' : ' is-active'}`} onClick={() => onOpen(null)}>
        <Icon name="person" size={17} />
        <span>
          <strong>Personnages</strong>
          <span className="doc-tree__sub">{campaignName}</span>
        </span>
      </button>
      <div className="char-list__tools">
        <input type="search" className="input char-list__search" placeholder="Rechercher…" value={query}
          onChange={(e) => setQuery(e.target.value)} aria-label="Rechercher dans les personnages" />
        <div className="char-list__row">
          <div className="segmented segmented--small" role="radiogroup" aria-label="Afficher">
            {[['all', 'Tous'], ['npc', 'PNJ'], ['faction', 'Factions']].map(([k, label]) => (
              <button key={k} type="button" role="radio" aria-checked={show === k} className={show === k ? 'is-active' : ''}
                onClick={() => setShow(k)}>{label}</button>
            ))}
          </div>
          <button type="button" className="icon-btn icon-btn--small" onClick={() => setGrouping(grouping === 'faction' ? 'alpha' : 'faction')}
            title={grouping === 'faction' ? 'Classer par ordre alphabétique' : 'Ranger par faction'}
            aria-label={grouping === 'faction' ? 'Classer par ordre alphabétique' : 'Ranger par faction'}>
            <Icon name={grouping === 'faction' ? 'banner' : 'list'} size={15} />
          </button>
        </div>
        {show !== 'faction' && (
          <select className="input char-list__status" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Statut des PNJ">
            <option value="">Tous les statuts</option>
            {Object.entries(NPC_STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        )}
      </div>
      <div className="doc-tree__section">
        <span>{filtering ? `${shownCount} résultat${shownCount > 1 ? 's' : ''}` : `${model.all} fiche${model.all > 1 ? 's' : ''}`}</span>
        <button type="button" className="icon-btn icon-btn--small" title="Nouveau PNJ" aria-label="Nouveau PNJ" onClick={() => onCreate('npc')}>
          <Icon name="plus" size={15} />
        </button>
      </div>
      {content}
      {model.all === 0 && (
        <button type="button" className="doc-tree__empty" onClick={() => onCreate('npc')}>
          <Icon name="plus" size={14} /> Créer le premier PNJ
        </button>
      )}
      {model.all > 0 && shownCount === 0 && <p className="muted char-list__none">Aucune fiche ne correspond.</p>}
    </aside>
  );
}

// --- Accueil de l'onglet -----------------------------------------------------------------

function CharactersHome({ graph, campaignName, onOpen, onCreate }) {
  const counts = new Map();
  for (const m of graph.memberships) counts.set(m.faction_id, (counts.get(m.faction_id) || 0) + 1);
  const factions = graph.factions.slice().sort((a, b) => (counts.get(b.id) || 0) - (counts.get(a.id) || 0) || byName(a, b));
  const dead = graph.npcs.filter((n) => n.status === 'mort').length;
  return (
    <article className="doc">
      <header className="doc__head">
        <p className="place__kind char-home__kind"><Icon name="person" size={15} /> Personnages de la campagne</p>
        <h1 className="doc__title">{campaignName}</h1>
        <p className="muted">
          {graph.npcs.length} PNJ{dead ? ` (dont ${dead} mort${dead > 1 ? 's' : ''})` : ''} · {graph.factions.length} faction{graph.factions.length > 1 ? 's' : ''} · {graph.relations.length} relation{graph.relations.length > 1 ? 's' : ''}
        </p>
      </header>
      {graph.npcs.length + graph.factions.length === 0 ? (
        <div className="place-empty">
          <p>Rangez ici tous les personnages de la campagne et les factions qui tirent les ficelles. Chaque scénario
            a ensuite sa propre carte des relations, avec les fiches qu’il cite.</p>
          <div className="empty-state__actions">
            <button type="button" className="btn btn--primary" onClick={() => onCreate('npc')}><Icon name="person" size={16} /> Créer un PNJ</button>
            <button type="button" className="btn btn--ghost" onClick={() => onCreate('faction')}><Icon name="banner" size={16} /> Créer une faction</button>
          </div>
        </div>
      ) : factions.length > 0 && (
        <ul className="doc-cards place-cards">
          {factions.map((f) => (
            <li key={f.id}>
              <button type="button" onClick={() => onOpen(nodeId('faction', f.id))}>
                {f.emblem_thumb ? <img src={f.emblem_thumb} alt="" className="place-cards__img" />
                  : <span className="place-cards__img place-cards__img--icon" style={{ background: f.color }} />}
                <span className="doc-cards__title">{f.name}</span>
                <span className="doc-cards__meta">{[f.kind, `${counts.get(f.id) || 0} membre${(counts.get(f.id) || 0) > 1 ? 's' : ''}`].filter(Boolean).join(' · ')}</span>
                {f.summary && <span className="place-cards__summary">{f.summary}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
