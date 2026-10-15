import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import GraphView from '../graph/GraphView.jsx';
import { DEFAULT_FILTERS, buildElements } from '../graph/elements.js';
import { NPC_STATUS } from '../constants.js';
import { nodeId, normalize, parseNodeId, storage } from '../util.js';
import { typeIndex } from '../relationTypes.js';
import CreateDialog from './CreateDialog.jsx';
import EntityPanel from './EntityPanel.jsx';
import { FilterMenu, Legend } from './Legend.jsx';
import RelationDialog from './RelationDialog.jsx';
import RelationPanel from './RelationPanel.jsx';
import { subtreeIds } from './PlacesView.jsx';
import { AddToMap, MapPicker, ScenarioBanner } from './ScenarioFilter.jsx';
import { castOf, neighborsOf, scenariosOf } from '../cast.js';
import { Icon, useConfirm, useToast } from './ui.jsx';
import { confirmDiscard } from '../session.js';
import { TOUCH, isLandscapePhone, isPhone } from '../viewport.js';

/*
 * Onglet Carte. Trois sortes de cartes :
 *   - scénario (?scenario=<doc>) : distribution du scénario, disposition propre au scénario ;
 *   - lieu (?place=<lieu>) : PNJ et factions du lieu et de ses sous-lieux ;
 *   - factions (par défaut) : factions de la campagne et liens entre elles.
 * Les fiches et les liens sont ceux de la campagne : seules la sélection et la disposition changent.
 */

// Place occupée par le panneau de fiche : à droite sur grand écran (cf. --panel-w), en bas sur téléphone
// (feuille de 62 % de la hauteur, cf. styles.css).
const panelInset = () => {
  if (isLandscapePhone()) return { right: document.querySelector('.stage > .panel')?.offsetWidth || Math.min(420, window.innerWidth * 0.55) };
  if (isPhone()) return { bottom: Math.round((document.querySelector('.stage')?.clientHeight || window.innerHeight) * 0.62) };
  return { right: 440 };
};
const TAP_VERB = TOUCH ? 'Touchez' : 'Cliquez sur';
const nextFrame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

function loadFilters(cid) {
  try {
    return { ...DEFAULT_FILTERS, ...JSON.parse(storage.get(`maze:filters:${cid}`) || '{}') };
  } catch {
    return DEFAULT_FILTERS;
  }
}

/** Sous-graphe limité à `keep`, positions données par `pos(id)` ({x, y} ou null). */
function restrict(graph, keep, pos) {
  const at = (kind, item) => {
    const p = pos(nodeId(kind, item.id));
    return { ...item, pos_x: p ? p.x : null, pos_y: p ? p.y : null };
  };
  return {
    ...graph,
    npcs: graph.npcs.filter((n) => keep.has(nodeId('npc', n.id))).map((n) => at('npc', n)),
    factions: graph.factions.filter((f) => keep.has(nodeId('faction', f.id))).map((f) => at('faction', f)),
    memberships: graph.memberships.filter((m) => keep.has(nodeId('npc', m.npc_id)) && keep.has(nodeId('faction', m.faction_id))),
    relations: graph.relations.filter((r) => keep.has(nodeId(r.source_kind, r.source_id)) && keep.has(nodeId(r.target_kind, r.target_id))),
  };
}

const campaignPos = (graph) => {
  const byNode = new Map();
  for (const n of graph.npcs) if (n.pos_x != null) byNode.set(nodeId('npc', n.id), { x: n.pos_x, y: n.pos_y });
  for (const f of graph.factions) if (f.pos_x != null) byNode.set(nodeId('faction', f.id), { x: f.pos_x, y: f.pos_y });
  return (id) => byNode.get(id) || null;
};

export default function Workspace({
  campaign, header, trailing, scenario, onScenario, place, onPlace, factionsAsked, onNavigate, onGraphLoaded,
}) {
  const cid = campaign.id;
  const mode = scenario ? 'scenario' : place ? 'place' : 'factions';
  const [graph, setGraph] = useState(null);
  const [error, setError] = useState(null);
  const [version, setVersion] = useState(0);
  const [selection, setSelection] = useState(null);
  const [link, setLink] = useState(null); // { source: nodeId | null }
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false);
  const [filters, setFiltersState] = useState(() => loadFilters(cid));
  const graphRef = useRef(null);
  const toast = useToast();
  const confirm = useConfirm();

  const setFilters = (f) => { setFiltersState(f); storage.set(`maze:filters:${cid}`, JSON.stringify(f)); };

  // Autre fiche, fond de la carte, fermeture du panneau : confirmation si le formulaire de la fiche ouverte a une
  // saisie non enregistrée (audit M3). Rouvrir la fiche déjà ouverte ne change rien.
  const selectionRef = useRef(selection);
  selectionRef.current = selection;
  const select = useCallback(async (next) => {
    const cur = selectionRef.current;
    if (next && cur && next.type === cur.type && next.id === cur.id && !next.edit) return true;
    if (!next && !cur) return true;
    if (!(await confirmDiscard(confirm))) return false;
    setSelection(next);
    return true;
  }, [confirm]);

  // Citations et réglages de toutes les cartes de scénario (choix de la carte, distribution).
  const [docMentions, setDocMentions] = useState(null);
  const loadMentions = useCallback(async () => {
    try {
      setDocMentions(await api.documentMentions(cid));
    } catch (err) {
      toast(err.message, 'error');
    }
  }, [cid, toast]);
  // Rechargées aussi à chaque changement de carte (scénario créé entre-temps dans un autre onglet).
  useEffect(() => { loadMentions(); }, [loadMentions, scenario]);

  // Carte d'un scénario : réglages et positions propres ({ docId, nodes: [{kind, id, state, pos_x, pos_y}] }).
  // Rattachée à son scénario : au premier rendu après un changement de carte, l'ancienne n'est jamais
  // utilisée (sinon la nouvelle carte serait calculée, puis enregistrée, avec les positions de l'ancienne).
  const [loadedMap, setScenarioMap] = useState(null);
  const scenarioMap = loadedMap && loadedMap.docId === scenario ? loadedMap : null;
  const scenarioRef = useRef(scenario);
  scenarioRef.current = scenario;
  useEffect(() => {
    setScenarioMap(null);
    if (!scenario) return;
    api.scenarioMap(scenario)
      .then((m) => { if (scenarioRef.current === scenario) setScenarioMap({ ...m, docId: scenario }); })
      .catch((err) => { if (scenarioRef.current === scenario) setScenarioMap({ error: err.message, nodes: [], docId: scenario }); });
  }, [scenario]);

  const [neighbors, setNeighborsState] = useState(() => storage.get('maze:scenario:neighbors') === '1');
  const setNeighbors = (v) => { setNeighborsState(v); storage.set('maze:scenario:neighbors', v ? '1' : '0'); };

  // Carte d'un lieu : PNJ et factions du lieu et de ses sous-lieux.
  const [placesData, setPlacesData] = useState(null);
  const loadPlaces = useCallback(() => {
    api.places(cid).then(setPlacesData).catch((err) => toast(err.message, 'error'));
  }, [cid, toast]);
  useEffect(() => { if (mode === 'place') loadPlaces(); }, [mode, place, loadPlaces]);
  const placeItem = mode === 'place' && placesData ? placesData.places.find((p) => p.id === place) : null;

  const modeRef = useRef(mode);
  modeRef.current = mode;
  const reload = useCallback(async () => {
    if (modeRef.current === 'place') loadPlaces();
    try {
      const g = await api.graph(cid);
      setGraph(g);
      setVersion((v) => v + 1);
      onGraphLoaded?.(g);
    } catch (err) {
      setError(err.message);
    }
  }, [cid, onGraphLoaded, loadPlaces]);
  useEffect(() => { reload(); }, [reload]);

  // Carte par défaut : la dernière carte de scénario ouverte, sinon la carte des factions.
  const scenarios = useMemo(() => scenariosOf(docMentions), [docMentions]);
  // (?factions=1 : carte des factions demandée explicitement, pas de redirection.)
  useEffect(() => {
    if (mode !== 'factions' || factionsAsked || !docMentions) return;
    const last = Number(storage.get(`maze:map:${cid}`));
    if (last && scenarios.some((d) => d.id === last)) onScenario?.(last);
  }, [mode, factionsAsked, docMentions, scenarios, cid, onScenario]);
  const pickMap = async (id) => {
    if (!(await select(null))) return;
    storage.set(`maze:map:${cid}`, id ? String(id) : '');
    setLink(null);
    onScenario?.(id);
  };
  const scenarioDoc = scenario ? docMentions?.documents.find((d) => d.id === scenario) : null;
  const badScenario = Boolean(scenario && docMentions && (!scenarioDoc || scenarioDoc.kind !== 'scenario'));
  // Dernière carte de scénario ouverte, quel que soit le chemin (sélecteur, bouton « Carte du scénario », lien).
  useEffect(() => {
    if (scenarioDoc?.kind === 'scenario') storage.set(`maze:map:${cid}`, String(scenarioDoc.id));
  }, [scenarioDoc, cid]);

  // --- Fiches affichées --------------------------------------------------------
  const cast = useMemo(() => (scenarioDoc && scenarioMap && !scenarioMap.error
    ? castOf(docMentions, scenarioDoc.id, scenarioMap.nodes) : null),
    [scenarioDoc, scenarioMap, docMentions]);

  const view = useMemo(() => {
    if (!graph) return null;
    if (mode === 'scenario') {
      if (!cast) return null;
      const context = neighbors ? neighborsOf(graph, cast.nodes) : null;
      const keep = new Set([...cast.nodes, ...(context || [])]);
      const rows = new Map(scenarioMap.nodes.map((r) => [nodeId(r.kind, r.id), r]));
      const pos = (id) => { const r = rows.get(id); return r && r.pos_x != null ? { x: r.pos_x, y: r.pos_y } : null; };
      return { graph: restrict(graph, keep, pos), scope: { nodes: cast.nodes, context } };
    }
    if (mode === 'place') {
      if (!placeItem) return null;
      const ids = subtreeIds(placesData.places, placeItem.id);
      const nodes = new Set(placesData.links.filter((l) => ids.has(l.place_id)).map((l) => nodeId(l.entity_kind, l.entity_id)));
      const context = neighbors ? neighborsOf(graph, nodes) : null;
      const keep = new Set([...nodes, ...(context || [])]);
      return { graph: restrict(graph, keep, campaignPos(graph)), scope: { nodes, context } };
    }
    const factions = new Set(graph.factions.map((f) => nodeId('faction', f.id)));
    return { graph: restrict(graph, factions, campaignPos(graph)), scope: null };
  }, [graph, mode, cast, neighbors, scenarioMap, placeItem, placesData]);

  // La carte des factions n'a pas de menu de filtres : les réglages des autres cartes ne s'y appliquent pas.
  const elements = useMemo(() => (view ? buildElements(view.graph, mode === 'factions' ? DEFAULT_FILTERS : filters, view.scope) : []),
    [view, filters, mode]);
  const types = useMemo(() => typeIndex(graph), [graph]);
  const mapKey = `${mode}-${scenario || place || ''}`;

  const lookup = useMemo(() => {
    if (!graph) return { byNode: new Map(), byName: new Map() };
    const byNode = new Map();
    const byName = new Map();
    for (const n of graph.npcs) {
      byNode.set(nodeId('npc', n.id), { ...n, kind: 'npc' });
      byName.set(normalize(n.name), nodeId('npc', n.id));
    }
    for (const f of graph.factions) {
      byNode.set(nodeId('faction', f.id), { ...f, kind: 'faction' });
      byName.set(normalize(f.name), nodeId('faction', f.id));
    }
    return { byNode, byName };
  }, [graph]);
  const shown = useMemo(() => new Set(elements.filter((e) => e.group === 'nodes').map((e) => e.data.id)), [elements]);

  const nodeName = useCallback((id) => lookup.byNode.get(id)?.name || 'Fiche supprimée', [lookup]);
  const resolveRef = useCallback((name) => lookup.byName.get(normalize(name)), [lookup]);
  const describe = useCallback((id) => {
    const e = lookup.byNode.get(id);
    if (!e) return null;
    if (e.kind === 'npc') {
      const status = e.status !== 'vivant' ? NPC_STATUS[e.status].label : null;
      return { title: e.name, subtitle: [e.title, status].filter(Boolean).join(', '), text: e.summary };
    }
    return { title: e.name, subtitle: e.kind, text: e.summary };
  }, [lookup]);

  // --- Navigation -----------------------------------------------------------
  // Une fiche absente de cette carte s'ouvre dans l'index des personnages.
  const openNode = useCallback(async (id) => {
    if (!lookup.byNode.has(id)) return;
    if (!shown.has(id)) { onNavigate(`#/c/${cid}/personnages/${id}`); return; }
    if (!(await select({ type: 'node', id }))) return;
    graphRef.current?.focus(id, panelInset());
  }, [lookup, shown, cid, onNavigate, select]);

  const openEdge = useCallback(async (id) => {
    if (id.startsWith('mem-')) {
      const m = graph.memberships.find((x) => `mem-${x.id}` === id);
      if (m) openNode(nodeId('npc', m.npc_id));
      return;
    }
    const r = graph.relations.find((x) => `rel-${x.id}` === id);
    if (r && !(shown.has(nodeId(r.source_kind, r.source_id)) && shown.has(nodeId(r.target_kind, r.target_id)))) {
      toast('Cette relation relie une fiche absente de cette carte.', 'info');
      return;
    }
    if (!(await select({ type: 'edge', id }))) return;
    graphRef.current?.reveal(id, panelInset());
  }, [graph, shown, openNode, toast, select]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape' || document.querySelector('.modal-backdrop, .lightbox, .popover, .sheet-form')) return;
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;
      if (link) setLink(null);
      else select(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [link, select]);

  // --- Interactions avec la carte -------------------------------------------
  const onNodeTap = async (id) => {
    if (link) {
      if (!link.source) { setLink({ source: id }); return; }
      if (link.source === id) return;
      setDialog({ type: 'relation', source: link.source, target: id });
      setLink(null);
      return;
    }
    if (!(await select({ type: 'node', id }))) return;
    graphRef.current?.reveal(id, panelInset());
  };

  const onPositions = useCallback(async (nodes) => {
    // Mise à jour locale silencieuse (sans redessiner), puis enregistrement.
    try {
      if (modeRef.current === 'scenario') {
        // Jamais sur la campagne : uniquement sur la carte chargée de ce scénario.
        if (!scenarioMap || scenarioMap.error || scenarioMap.docId !== scenarioRef.current) return;
        for (const p of nodes) {
          const row = scenarioMap.nodes.find((r) => r.kind === p.kind && r.id === p.id);
          if (row) { row.pos_x = p.x; row.pos_y = p.y; } else scenarioMap.nodes.push({ kind: p.kind, id: p.id, state: 'auto', pos_x: p.x, pos_y: p.y });
        }
        await api.saveScenarioPositions(scenarioMap.docId, nodes);
      } else if (graph) {
        for (const p of nodes) {
          const item = (p.kind === 'npc' ? graph.npcs : graph.factions).find((x) => x.id === p.id);
          if (item) { item.pos_x = p.x; item.pos_y = p.y; }
        }
        await api.savePositions(cid, nodes);
      }
    } catch (err) {
      toast(`Positions non enregistrées : ${err.message}`, 'error');
    }
  }, [cid, graph, scenarioMap, toast]);

  // Ajout / retrait d'une fiche sur la carte du scénario.
  const setOnMap = useCallback(async (id, state, position) => {
    const { kind, id: eid } = parseNodeId(id);
    try {
      const res = await api.setScenarioNode(scenario, kind, eid, { state, ...(position ? { x: position.x, y: position.y } : {}) });
      if (scenarioRef.current === scenario) {
        // Les positions locales (déplacements peut-être pas encore enregistrés) priment sur la réponse.
        setScenarioMap((m) => {
          if (!m || m.docId !== scenario) return m;
          const local = new Map(m.nodes.filter((r) => r.pos_x != null).map((r) => [nodeId(r.kind, r.id), r]));
          const merged = res.nodes.map((r) => {
            const l = local.get(nodeId(r.kind, r.id));
            return l && !position ? { ...r, pos_x: l.pos_x, pos_y: l.pos_y } : r;
          });
          return { ...m, nodes: merged };
        });
      }
      setVersion((v) => v + 1); // « Sur les cartes » de la fiche ouverte
      loadMentions();
    } catch (err) {
      toast(err.message, 'error');
    }
  }, [scenario, loadMentions, toast]);
  const addToMap = (id) => setOnMap(id, 'added', graphRef.current?.viewportCenter());
  const removeFromMap = async (id) => {
    setSelection(null);
    await setOnMap(id, cast?.mentioned.has(id) ? 'excluded' : 'auto');
  };

  const autoLayout = async () => {
    const ok = await confirm({
      title: 'Réorganiser la carte',
      message: 'Les fiches seront replacées automatiquement selon leurs liens. Vos placements manuels sur cette carte seront remplacés.',
      confirmLabel: 'Réorganiser',
    });
    if (!ok) return;
    setBusy(true);
    await nextFrame();
    try {
      graphRef.current?.layout();
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return <div className="workspace"><header className="topbar">{header}<div className="toolbar">{trailing}</div></header><p className="panel__error">{error}</p></div>;
  }

  const selectedRelation = selection?.type === 'edge'
    ? graph?.relations.find((r) => `rel-${r.id}` === selection.id) : null;
  const selectedNode = selection?.type === 'node' && lookup.byNode.has(selection.id) ? parseNodeId(selection.id) : null;
  const panelOpen = Boolean(selectedNode || selectedRelation);
  const mapLabel = mode === 'scenario' ? (scenarioDoc?.title || 'Sans titre')
    : mode === 'place' ? `Lieu : ${placeItem?.name || '…'}` : 'Carte des factions';
  const hiddenDead = view?.scope && !filters.showDead
    ? view.graph.npcs.filter((n) => n.status === 'mort' && view.scope.nodes.has(nodeId('npc', n.id))).length : 0;

  // Action propre à la carte du scénario dans la fiche ouverte.
  let mapAction = null;
  if (mode === 'scenario' && selectedNode && cast) {
    mapAction = cast.nodes.has(selection.id)
      ? { label: 'Retirer de la carte du scénario', icon: 'minus', onClick: () => removeFromMap(selection.id) }
      : { label: 'Ajouter à la carte du scénario', icon: 'plus', onClick: () => setOnMap(selection.id, 'added') };
  }

  const createFrom = (kind, position) => setDialog({ type: 'create', kind, position });

  return (
    <div className={`workspace${panelOpen ? ' has-panel' : ''}`}>
      <header className="topbar">
        {header}
        <div className="topbar__right">
        <div className="toolbar" role="toolbar" aria-label="Outils de la carte">
          <MapPicker data={docMentions} selected={mode === 'scenario' ? scenario : null} label={mapLabel} onPick={pickMap} />
          <span className="toolbar__sep" />
          {mode === 'scenario' && cast && (
            <AddToMap graph={graph} cast={cast.nodes} excluded={cast.excluded} onAdd={addToMap} />
          )}
          {mode === 'scenario' && (
            <button type="button" className="btn btn--ghost" onClick={() => createFrom('npc')}>
              <Icon name="person" size={16} /><span className="btn__label">PNJ</span>
            </button>
          )}
          {mode !== 'place' && (
            <button type="button" className="btn btn--ghost" onClick={() => createFrom('faction')}>
              <Icon name="banner" size={16} /><span className="btn__label">Faction</span>
            </button>
          )}
          <button type="button" className={`btn ${link ? 'btn--primary' : 'btn--ghost'}`} aria-pressed={Boolean(link)}
            disabled={shown.size < 2}
            onClick={() => setLink(link ? null : { source: selectedNode ? selection.id : null })}>
            <Icon name="link" size={16} /><span className="btn__label">Lier</span>
          </button>
          <span className="toolbar__sep" />
          {graph && mode !== 'factions' && <FilterMenu graph={view?.graph || graph} filters={filters} onFilters={setFilters} />}
          <button type="button" className="icon-btn" onClick={() => graphRef.current?.fit()} title="Voir toute la carte" aria-label="Voir toute la carte">
            <Icon name="fit" />
          </button>
          <button type="button" className="icon-btn" onClick={autoLayout} title="Réorganiser automatiquement" aria-label="Réorganiser automatiquement"
            disabled={busy || shown.size < 2}>
            <Icon name="layout" />
          </button>
          <button type="button" className="icon-btn" title="Exporter la carte en image" aria-label="Exporter la carte en image"
            onClick={() => graphRef.current?.exportPng(`${`${campaign.name}-${mapLabel}`.replace(/[^\p{L}\p{N}]+/gu, '-')}.png`)}>
            <Icon name="download" />
          </button>
          {trailing && <span className="toolbar__sep" />}
        </div>
        {trailing && <div className="topbar__end">{trailing}</div>}
        </div>
      </header>

      <main className="stage">
        {view && (
          <GraphView
            key={mapKey}
            ref={graphRef}
            elements={elements}
            selectedId={selection?.id}
            linkSourceId={link?.source}
            linkMode={Boolean(link)}
            describe={describe}
            onNodeTap={onNodeTap}
            onEdgeTap={(id) => { if (!link) openEdge(id); }}
            onBackgroundTap={() => { if (!link) select(null); }}
            onBackgroundDblTap={(position) => {
              if (mode === 'scenario') createFrom('npc', position);
              else if (mode === 'factions') createFrom('faction', position);
            }}
            onPositions={onPositions}
          />
        )}
        {!view && !badScenario && !scenarioMap?.error && !(mode === 'place' && placesData && !placeItem) && (
          <div className="stage__loading" aria-busy="true">Chargement de la carte…</div>
        )}
        {busy && <div className="stage__busy" role="status"><span className="spinner" /> Réorganisation…</div>}

        {mode === 'scenario' && scenarioDoc && cast && (
          <ScenarioBanner
            title={scenarioDoc.title || 'Sans titre'} count={cast.nodes.size} verb="" hiddenCount={hiddenDead}
            neighbors={neighbors} onNeighbors={setNeighbors}
            openLabel="Ouvrir le scénario" onOpenDoc={() => onNavigate(`#/c/${cid}/scenarios/${scenarioDoc.id}`)}
          />
        )}
        {mode === 'scenario' && cast && cast.nodes.size === 0 && (
          <div className="empty-state">
            <h2>Carte vide</h2>
            <p>Citez des PNJ et des factions dans « {scenarioDoc?.title || 'Sans titre'} » ou ses pages en tapant @ suivi d’un nom :
              ils apparaissent ici. Vous pouvez aussi ajouter une fiche de la campagne, ou {TOUCH ? 'toucher deux fois le fond de la carte' : 'double-cliquer'} pour créer un PNJ.</p>
            <div className="empty-state__actions">
              <AddToMap graph={graph} cast={cast.nodes} excluded={cast.excluded} onAdd={addToMap} />
              <button type="button" className="btn btn--primary" onClick={() => createFrom('npc')}>
                <Icon name="person" size={16} /> Créer un PNJ
              </button>
            </div>
          </div>
        )}
        {badScenario && (
          <div className="empty-state">
            <h2>Carte introuvable</h2>
            <p>Ce scénario n’existe plus.</p>
            <div className="empty-state__actions">
              <button type="button" className="btn btn--primary" onClick={() => pickMap(null)}>Carte des factions</button>
            </div>
          </div>
        )}
        {scenarioMap?.error && <p className="panel__error">{scenarioMap.error}</p>}

        {mode === 'place' && placeItem && view && (
          <ScenarioBanner
            icon="pin" verb="présente" openLabel="Ouvrir le lieu" clearLabel="Carte des factions"
            title={placeItem.name} count={view.scope.nodes.size} hiddenCount={hiddenDead}
            neighbors={neighbors} onNeighbors={setNeighbors}
            onOpenDoc={() => onNavigate(`#/c/${cid}/lieux/${placeItem.id}`)}
            onClear={() => pickMap(null)}
          />
        )}
        {mode === 'place' && view && view.scope.nodes.size === 0 && (
          <div className="empty-state">
            <h2>Personne ici</h2>
            <p>Aucun PNJ ni faction n’est relié à « {placeItem.name} » ni à ses sous-lieux. Modifiez la fiche du lieu pour en ajouter.</p>
          </div>
        )}
        {mode === 'place' && placesData && !placeItem && (
          <div className="scenario-banner" role="status">
            <span>Ce lieu n’existe plus.</span>
            <button type="button" className="btn btn--small btn--ghost" onClick={() => pickMap(null)}>Carte des factions</button>
          </div>
        )}

        {mode === 'factions' && graph && graph.factions.length === 0 && (
          <div className="empty-state">
            <h2>Aucune faction</h2>
            <p>La carte des factions montre les grandes puissances de la campagne et leurs liens. Les PNJ apparaissent sur la carte
              de chaque scénario, et tous sont rangés dans l’onglet Personnages.</p>
            <div className="empty-state__actions">
              <button type="button" className="btn btn--primary" onClick={() => createFrom('faction')}>
                <Icon name="banner" size={16} /> Créer une faction
              </button>
              {scenarios.length > 0 && (
                <button type="button" className="btn btn--ghost" onClick={() => pickMap(scenarios[0].id)}>
                  <Icon name="book" size={16} /> Carte d’un scénario
                </button>
              )}
            </div>
          </div>
        )}

        {link && (
          <div className="link-banner" role="status">
            <Icon name="link" size={16} />
            {link.source
              ? <span>{TAP_VERB} la fiche à relier à <strong>{nodeName(link.source)}</strong></span>
              : <span>{TAP_VERB} la première fiche à relier</span>}
            <button type="button" className="btn btn--small btn--ghost" onClick={() => setLink(null)}>Annuler</button>
          </div>
        )}

        {view && mode !== 'factions' && <Legend graph={view.graph} filters={filters} onFilters={setFilters} />}

        {selectedNode && (
          <EntityPanel
            key={selection.id}
            kind={selectedNode.kind}
            id={selectedNode.id}
            graph={graph}
            version={version}
            startInEdit={selection.edit}
            nodeName={nodeName}
            resolveRef={resolveRef}
            mapAction={mapAction}
            onClose={() => select(null)}
            onOpenNode={(id) => openNode(id)}
            onOpenEdge={openEdge}
            onFocus={(id) => graphRef.current?.focus(id, panelInset())}
            onStartLink={(id) => setLink({ source: id })}
            onChanged={reload}
            onDeleted={() => { setSelection(null); reload(); loadMentions(); }}
          />
        )}
        {selectedRelation && (
          <RelationPanel
            relation={selectedRelation}
            types={types}
            nodeName={nodeName}
            resolveRef={resolveRef}
            onClose={() => setSelection(null)}
            onOpenNode={(id) => openNode(id)}
            onEdit={() => setDialog({ type: 'relation', relation: selectedRelation })}
            onChanged={reload}
            onDeleted={() => { setSelection(null); reload(); }}
          />
        )}
      </main>

      {dialog?.type === 'create' && (
        <CreateDialog
          campaignId={cid}
          kind={dialog.kind}
          position={dialog.position || graphRef.current?.viewportCenter()}
          usedColors={graph?.factions.map((f) => f.color) || []}
          onClose={() => setDialog(null)}
          onCreated={async (kind, created) => {
            const position = dialog.position || graphRef.current?.viewportCenter();
            setDialog(null);
            const id = nodeId(kind, created.id);
            if (mode === 'scenario') await setOnMap(id, 'added', position);
            await reload();
            if (mode === 'scenario' || (mode === 'factions' && kind === 'faction')) select({ type: 'node', id, edit: true });
            else onNavigate(`#/c/${cid}/personnages/${id}?edit=1`);
          }}
        />
      )}
      {dialog?.type === 'relation' && graph && (
        <RelationDialog
          campaignId={cid}
          graph={graph}
          relation={dialog.relation}
          source={dialog.source}
          target={dialog.target}
          onClose={() => setDialog(null)}
          onTypesChanged={reload}
          onSaved={async (saved, isMembership) => {
            setDialog(null);
            await reload();
            if (!isMembership && !selection) setSelection({ type: 'edge', id: `rel-${saved.id}` });
          }}
        />
      )}
    </div>
  );
}
