import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import { NPC_STATUS } from '../constants.js';
import { npcRingColors } from '../graph/elements.js';
import { initialsAvatar, nodeId, normalize, parseNodeId, storage } from '../util.js';
import { branchKeys, byOrder, factionForest, factionPaths, factionStructure } from '../factionTree.js';
import CreateDialog from './CreateDialog.jsx';
import EntityPanel from './EntityPanel.jsx';
import { EntryShell } from './EntryNotes.jsx';
import RelationDialog from './RelationDialog.jsx';
import { Icon, useAction } from './ui.jsx';
import { useTreeDrag } from './treeDrag.js';
import { compareText, formatList, pt, t, tn } from '../i18n/index.js';

/*
 * Onglet Personnages : index de tous les PNJ et factions de la campagne (à gauche, en arbre des factions et
 * sous-factions, ou par ordre alphabétique, avec recherche et filtres ; logique dans factionTree.js), fiche en
 * pleine page à droite.
 */

const byName = (a, b) => compareText(a.name, b.name, { sensitivity: 'base' });

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
  const nodeName = useCallback((id) => lookup.byNode.get(id)?.name || t('Fiche supprimée'), [lookup]);
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
            <Icon name="person" size={16} /><span className="btn__label">{t('PNJ')}</span>
          </button>
          <button type="button" className="btn btn--ghost" onClick={() => setDialog({ type: 'create', kind: 'faction' })}>
            <Icon name="banner" size={16} /><span className="btn__label">{t('Faction')}</span>
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
              timelineEnabled={Boolean(campaign.timeline_enabled)}
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
            {!graph ? <div className="doc-loading" aria-busy="true">{t('Chargement…')}</div>
              : entity ? (
                <div className="doc"><p className="muted">{t('Cette fiche n’existe plus.')}</p></div>
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
  // Factions repliées par défaut : seules s'ouvrent celles que le MJ déplie (et la branche de la fiche ouverte).
  // Clé = chemin dans l'arbre (« 74/77 ») : une faction à plusieurs parents se déplie sous chacun séparément.
  const [expanded, setExpanded] = useState(() => new Set());
  const structure = useMemo(() => (graph ? factionStructure(graph) : null), [graph]);
  const paths = useMemo(() => (structure ? factionPaths(graph, structure) : null), [graph, structure]);
  // Fiche ouverte : la branche qui y mène se déplie (une fois ; le MJ peut la replier). PNJ : tous les chemins vers
  // ses factions ; faction : tous ses chemins d'ancêtres.
  const expandedFor = useRef(null);
  useEffect(() => {
    if (!graph || !paths || !currentId || expandedFor.current === currentId) return;
    expandedFor.current = currentId;
    const { kind, id } = parseNodeId(currentId);
    const keys = kind === 'npc'
      ? branchKeys(paths, graph.memberships.filter((m) => m.npc_id === id).map((m) => m.faction_id), { self: true })
      : branchKeys(paths, kind === 'faction' ? [id] : []);
    if (keys.size) setExpanded((s) => ([...keys].every((k) => s.has(k)) ? s : new Set([...s, ...keys])));
  }, [currentId, graph, paths]);
  const setShow = (v) => { setShowState(v); storage.set('maze:characters:show', v); };
  const setGrouping = (v) => { setGroupingState(v); storage.set('maze:characters:group', v); };
  const toggle = (key) => setExpanded((s) => { const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n; });

  // Calculé une fois par graphe et par filtre ; le rendu ne parcourt ensuite que les branches dépliées.
  const model = useMemo(() => {
    if (!graph) return null;
    const q = normalize(query.trim());
    const withNpcs = show !== 'faction';
    const statusOk = (n) => !status || n.status === status;
    const npcMatch = (n) => (!q || normalize(`${n.name} ${n.title || ''} ${(n.tags || []).join(' ')}`).includes(q)) && statusOk(n);
    const factionText = (f) => !q || normalize(`${f.name} ${f.kind || ''} ${(f.tags || []).join(' ')}`).includes(q);
    const npcs = graph.npcs.filter(npcMatch).sort(byName);
    const factions = graph.factions.filter(factionText).sort(byName);
    const filtering = Boolean(q || (withNpcs && status));
    let forest = null;
    let loners = [];
    let factionCount = factions.length;
    if (grouping === 'faction' && show !== 'npc') {
      // Filtre de statut seul : une faction n'a pas de statut, seules restent celles qui mènent à un PNJ retenu.
      const factionMatch = q ? factionText : (withNpcs && status ? () => false : () => true);
      forest = factionForest(graph, { structure, factionMatch, npcVisible: npcMatch, memberVisible: statusOk, showNpcs: withNpcs });
      loners = withNpcs ? npcs.filter((n) => !structure.inFaction.has(n.id)).sort(byOrder) : [];
      if (!q && withNpcs && status) factionCount = 0;
    }
    return {
      npcs, factions, forest, loners, filtering, withNpcs, ring: npcRingColors(graph),
      shown: (withNpcs ? npcs.length : 0) + (show !== 'npc' ? factionCount : 0),
      all: graph.npcs.length + graph.factions.length,
    };
  }, [graph, structure, query, status, show, grouping]);

  // Glisser-déposer (vue par faction, sans filtre) : entre frères seulement, à toute profondeur. Factions entre
  // factions sœurs (même parent), PNJ dans leur groupe. Le nouvel ordre des frères est reporté dans l'ordre global
  // (les autres fiches gardent leur place) : une faction à plusieurs parents garde ce rang sous chacun.
  const dnd = useTreeDrag((dragged, target, where) => {
    const rest = target.groupIds.filter((x) => x !== dragged.id);
    rest.splice(rest.indexOf(target.id) + (where === 'after' ? 1 : 0), 0, dragged.id);
    const global = (dragged.kind === 'faction' ? graph.factions : graph.npcs).slice().sort(byOrder).map((x) => x.id);
    const inGroup = new Set(rest);
    let k = 0;
    onReorder(dragged.kind, global.map((id) => (inGroup.has(id) ? rest[k++] : id)));
  });
  if (!model) return <aside className="doc-tree" aria-busy="true" />;
  const { filtering } = model;
  const canDrag = !filtering && grouping === 'faction' && Boolean(onReorder);
  const dragProps = (row) => dnd.props(row, { enabled: canDrag, accepts: (d) => d.group === row.group });

  const npcItem = (n, depth, leader, group = null) => {
    const id = nodeId('npc', n.id);
    const row = group && { key: `${group.key}:${n.id}`, id: n.id, kind: 'npc', group: group.key, groupIds: group.ids, label: n.name };
    return (
      <li key={row ? row.key : id}>
        <div className={`doc-tree__item${id === currentId ? ' is-active' : ''}${row ? dnd.className(row.key) : ''}`}
          style={{ '--depth': depth }} {...(row ? dragProps(row) : {})}>
          <span className="doc-tree__caret" />
          <button type="button" className="doc-tree__link" onClick={() => onOpen(id)} aria-current={id === currentId ? 'page' : undefined}>
            <img className="char-list__avatar" src={n.portrait_thumb || initialsAvatar(n.name, model.ring.get(n.id) || '#5d6b80')} alt="" />
            <span className="doc-tree__label">{n.status === 'mort' ? `† ${n.name}` : n.name}</span>
            {leader && <Icon name="star" size={12} title={t('Dirigeant')} />}
          </button>
        </div>
      </li>
    );
  };

  const factionLink = (f, extra = null) => {
    const id = nodeId('faction', f.id);
    return (
      <button type="button" className="doc-tree__link" onClick={() => onOpen(id)} aria-current={id === currentId ? 'page' : undefined}>
        <span className="char-list__seal" style={{ background: f.color }} />
        <span className="doc-tree__label char-list__faction">{f.name}</span>
        {extra}
      </button>
    );
  };

  // Faction de l'arbre : sous-factions puis PNJ, rendus seulement si la branche est dépliée.
  const factionNode = (node, siblings) => {
    const f = node.faction;
    const id = nodeId('faction', f.id);
    const parentPath = node.path.slice(0, -String(f.id).length - 1);
    const hasKids = node.children.length > 0 || node.npcs.length > 0;
    const open = hasKids && (filtering || expanded.has(node.path));
    const row = { key: `f:${node.path}`, id: f.id, kind: 'faction', group: `fs:${parentPath}`, groupIds: siblings, label: f.name };
    const subs = node.children.length;
    const counts = [subs > 0 && tn('{n} sous-faction', '{n} sous-factions', subs),
      model.withNpcs && tn('{n} PNJ', '{n} PNJ', node.npcs.length)].filter(Boolean);
    const also = node.otherParents.length > 0
      && t('Aussi membre de : {names}', { names: formatList(node.otherParents.map((p) => p.name)) });
    const childIds = node.children.map((c) => c.faction.id);
    const group = { key: `g${node.path}`, ids: node.npcs.map((x) => x.npc.id) };
    return (
      <li key={row.key}>
        <div className={`doc-tree__item${id === currentId ? ' is-active' : ''}${dnd.className(row.key)}`}
          style={{ '--depth': node.depth }} data-path={node.path} {...dragProps(row)}>
          {hasKids && !filtering ? (
            <button type="button" className="doc-tree__caret" aria-label={open ? t('Replier') : t('Déplier')} aria-expanded={open}
              onClick={() => toggle(node.path)}>
              <Icon name={open ? 'chevron' : 'right'} size={13} />
            </button>
          ) : <span className="doc-tree__caret">{hasKids && <Icon name="chevron" size={13} />}</span>}
          {factionLink(f, (
            <>
              {also && <span className="char-list__also" role="img" title={also} aria-label={also}><Icon name="link" size={11} /></span>}
              {counts.length > 0 && (
                <span className="char-list__count" title={formatList(counts)}>
                  {subs > 0 && <span className="char-list__subs"><Icon name="banner" size={10} />{subs}</span>}
                  {model.withNpcs && (node.npcs.length > 0 || subs === 0) && <span>{node.npcs.length}</span>}
                </span>
              )}
            </>
          ))}
        </div>
        {open && (
          <ul>
            {node.children.map((c) => factionNode(c, childIds))}
            {node.npcs.map((x) => npcItem(x.npc, node.depth + 1, x.membership.is_leader, group))}
          </ul>
        )}
      </li>
    );
  };

  let content;
  if (model.forest) {
    // Arbre des factions (racines au premier niveau), puis les PNJ sans faction.
    const rootIds = model.forest.map((n) => n.faction.id);
    const lonerGroup = { key: 'none', ids: model.loners.map((n) => n.id) };
    content = (
      <>
        <ul className="doc-tree__list char-list__tree">{model.forest.map((n) => factionNode(n, rootIds))}</ul>
        {model.loners.length > 0 && (
          <>
            <div className="doc-tree__section"><span>{t('Sans faction')}</span><span className="char-list__count">{model.loners.length}</span></div>
            <ul className="doc-tree__list">{model.loners.map((n) => npcItem(n, 0, false, lonerGroup))}</ul>
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
        {items.map((i) => (i.kind === 'npc' ? npcItem(i.n, 0, false) : (
          <li key={nodeId('faction', i.f.id)}>
            <div className={`doc-tree__item${nodeId('faction', i.f.id) === currentId ? ' is-active' : ''}`} style={{ '--depth': 0 }}>
              <span className="doc-tree__caret" />
              {factionLink(i.f)}
            </div>
          </li>
        )))}
      </ul>
    );
  }
  const shownCount = model.shown;

  return (
    <aside className="doc-tree char-list" aria-label={t('Personnages de la campagne')}>
      <button type="button" className={`doc-tree__root${currentId ? '' : ' is-active'}`} onClick={() => onOpen(null)}>
        <Icon name="person" size={17} />
        <span>
          <strong>{t('Personnages')}</strong>
          <span className="doc-tree__sub">{campaignName}</span>
        </span>
      </button>
      <div className="char-list__tools">
        <input type="search" className="input char-list__search" placeholder={t('Rechercher…')} value={query}
          onChange={(e) => setQuery(e.target.value)} aria-label={t('Rechercher dans les personnages')} />
        <div className="char-list__row">
          <div className="segmented segmented--small" role="radiogroup" aria-label={t('Afficher')}>
            {[['all', t('Tous')], ['npc', pt('filtre', 'PNJ')], ['faction', t('Factions')]].map(([k, label]) => (
              <button key={k} type="button" role="radio" aria-checked={show === k} className={show === k ? 'is-active' : ''}
                onClick={() => setShow(k)}>{label}</button>
            ))}
          </div>
          <button type="button" className="icon-btn icon-btn--small" onClick={() => setGrouping(grouping === 'faction' ? 'alpha' : 'faction')}
            title={grouping === 'faction' ? t('Classer par ordre alphabétique') : t('Ranger par faction')}
            aria-label={grouping === 'faction' ? t('Classer par ordre alphabétique') : t('Ranger par faction')}>
            <Icon name={grouping === 'faction' ? 'banner' : 'list'} size={15} />
          </button>
        </div>
        {show !== 'faction' && (
          <select className="input char-list__status" value={status} onChange={(e) => setStatus(e.target.value)} aria-label={t('Statut des PNJ')}>
            <option value="">{t('Tous les statuts')}</option>
            {Object.entries(NPC_STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        )}
      </div>
      <div className="doc-tree__section">
        <span>{filtering ? tn('{n} résultat', '{n} résultats', shownCount) : tn('{n} fiche', '{n} fiches', model.all)}</span>
        <button type="button" className="icon-btn icon-btn--small" title={t('Nouveau PNJ')} aria-label={t('Nouveau PNJ')} onClick={() => onCreate('npc')}>
          <Icon name="plus" size={15} />
        </button>
      </div>
      {content}
      {model.all === 0 && (
        <button type="button" className="doc-tree__empty" onClick={() => onCreate('npc')}>
          <Icon name="plus" size={14} /> {t('Créer le premier PNJ')}
        </button>
      )}
      {model.all > 0 && shownCount === 0 && <p className="muted char-list__none">{t('Aucune fiche ne correspond.')}</p>}
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
        <p className="place__kind char-home__kind"><Icon name="person" size={15} /> {t('Personnages de la campagne')}</p>
        <h1 className="doc__title">{campaignName}</h1>
        <p className="muted">
          {dead ? tn('{npcs} (dont {n} mort)', '{npcs} (dont {n} morts)', dead, { npcs: tn('{n} PNJ', '{n} PNJ', graph.npcs.length) })
            : tn('{n} PNJ', '{n} PNJ', graph.npcs.length)}
          {' · '}{tn('{n} faction', '{n} factions', graph.factions.length)}
          {' · '}{tn('{n} relation', '{n} relations', graph.relations.length)}
        </p>
      </header>
      {graph.npcs.length + graph.factions.length === 0 ? (
        <div className="place-empty">
          <p>{t('Rangez ici tous les personnages de la campagne et les factions qui tirent les ficelles. Chaque scénario a ensuite sa propre carte des relations, avec les fiches qu’il cite.')}</p>
          <div className="empty-state__actions">
            <button type="button" className="btn btn--primary" onClick={() => onCreate('npc')}><Icon name="person" size={16} /> {t('Créer un PNJ')}</button>
            <button type="button" className="btn btn--ghost" onClick={() => onCreate('faction')}><Icon name="banner" size={16} /> {t('Créer une faction')}</button>
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
                <span className="doc-cards__meta">{[f.kind, tn('{n} membre', '{n} membres', counts.get(f.id) || 0)].filter(Boolean).join(' · ')}</span>
                {f.summary && <span className="place-cards__summary">{f.summary}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
