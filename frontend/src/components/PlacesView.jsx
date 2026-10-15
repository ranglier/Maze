import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import { NPC_STATUS, PLACE_COLOR, PLACE_ICONS, PLACE_ROLES, placeKind } from '../constants.js';
import { ancestorsOf, childrenOf, recentPlaces, rememberPlace, subtreeIds } from '../places.js';
import { PLACE_EDITABLE, PLACE_SECTIONS, fieldLabels } from '../fields.js';
import { initialsAvatar, nodeId, normalize, parseNodeId, storage } from '../util.js';
import { EntryShell, EntrySideButton, SectionComment } from './EntryNotes.jsx';
import Gallery from './Gallery.jsx';
import PlaceCrumbs from './PlaceCrumbs.jsx';
import { KindsContext, PlaceIcon, useKinds } from './PlaceIcon.jsx';
import PlaceTree from './PlaceTree.jsx';
import RichField from './RichField.jsx';
import { mentionEntities } from './mentionEntities.js';
import { ConflictPanel, useEntryDraft } from './EntryDraft.jsx';
import { Field, Icon, KindImage, Markdown, Modal, TagInput, useAction, useConfirm, useToast } from './ui.jsx';

/*
 * Onglet Lieux : arborescence libre (région > ville > quartier > bâtiment…) à gauche, fiche du lieu
 * au centre. Les PNJ et factions y sont reliés avec un rôle ; les scénarios les citent avec @.
 */

export { KindsContext, PlaceIcon, subtreeIds };

/** Sous-onglets de Lieux : index (arborescence et fiches) et cartes interactives. */
export function PlacesTabs({ campaignId, active }) {
  return (
    <nav className="segmented segmented--compact places-tabs" aria-label="Lieux">
      <a href={`#/c/${campaignId}/lieux`} className={active === 'index' ? 'is-active' : ''} title="Index des lieux"
        aria-current={active === 'index' ? 'page' : undefined}>
        <Icon name="list" size={15} /><span className="places-tabs__long">Index des lieux</span><span className="places-tabs__short">Index</span>
      </a>
      <a href={`#/c/${campaignId}/cartes`} className={active === 'cartes' ? 'is-active' : ''} title="Carte interactive"
        aria-current={active === 'cartes' ? 'page' : undefined}>
        <Icon name="map" size={15} /><span className="places-tabs__long">Carte interactive</span><span className="places-tabs__short">Carte</span>
      </a>
    </nav>
  );
}

function KindOptions() {
  return useKinds().map((k) => <option key={k.key} value={k.key}>{k.label}</option>);
}

export default function PlacesView({
  campaign, user, header, trailing, placeId, editRequested, commentId, onNavigate, onEditDone, onCommentDone,
}) {
  const cid = campaign.id;
  const [data, setData] = useState(null);       // { places, links }
  const [graph, setGraph] = useState(null);     // PNJ et factions (noms, portraits)
  const [detail, setDetail] = useState(null);
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(null); // { parentId }
  const [managingKinds, setManagingKinds] = useState(false);
  const run = useAction();
  const confirm = useConfirm();

  const loadTree = useCallback(async () => {
    const d = await run(() => api.places(cid));
    if (d) setData(d);
    return d;
  }, [cid, run]);
  const loadGraph = useCallback(async () => {
    const g = await run(() => api.graph(cid));
    if (g) setGraph(g);
  }, [cid, run]);
  useEffect(() => { loadTree(); loadGraph(); }, [loadTree, loadGraph]);

  // Lieu affiché (une réponse arrivée après un changement de lieu est ignorée).
  const placeIdRef = useRef(placeId);
  placeIdRef.current = placeId;
  const loadDetail = useCallback(async (id) => {
    if (!id) { setDetail(null); return; }
    try {
      const d = await api.getEntity('place', id);
      if (placeIdRef.current === id) setDetail(d);
      rememberPlace(cid, id); // accueil de l'onglet : lieux récemment consultés

    } catch (err) {
      if (placeIdRef.current === id) setDetail({ error: err.message, id });
    }
  }, [cid]);
  useEffect(() => {
    setDetail(null);
    setEditing(false);
    loadDetail(placeId);
  }, [placeId, loadDetail]);

  // Arrivée depuis une création (#/c/<id>/lieux/<n>?edit=1) : formulaire ouvert.
  useEffect(() => {
    if (editRequested && detail && detail.id === placeId && !detail.error) {
      setEditing(true);
      onEditDone?.();
    }
  }, [editRequested, detail, placeId, onEditDone]);

  const refresh = async () => {
    await Promise.all([loadTree(), placeId ? loadDetail(placeId) : null]);
  };

  const go = (id) => onNavigate(id ? `#/c/${cid}/lieux/${id}` : `#/c/${cid}/lieux`);

  // Liens [[Nom]] des textes : PNJ, factions et lieux de la campagne.
  const lookup = useMemo(() => {
    const byName = new Map();
    for (const n of graph?.npcs || []) byName.set(normalize(n.name), nodeId('npc', n.id));
    for (const f of graph?.factions || []) byName.set(normalize(f.name), nodeId('faction', f.id));
    for (const p of data?.places || []) byName.set(normalize(p.name), nodeId('place', p.id));
    return byName;
  }, [graph, data]);
  const resolveRef = useCallback((name) => lookup.get(normalize(name)), [lookup]);
  const openNode = (nid) => {
    const { kind, id } = parseNodeId(nid);
    onNavigate(kind === 'place' ? `#/c/${cid}/lieux/${id}` : `#/c/${cid}/personnages/${kind}-${id}`);
  };

  const create = async ({ name, kind, parentId }) => {
    const created = await run(() => api.createEntity('place', cid, { name, kind, parent_id: parentId }));
    if (!created) return false;
    setCreating(null);
    await loadTree();
    onNavigate(`#/c/${cid}/lieux/${created.id}?edit=1`);
    return true;
  };

  const move = async (item, dir) => {
    const siblings = childrenOf(data.places, item.parent_id ?? null).map((p) => p.id);
    const i = siblings.indexOf(item.id);
    const j = i + dir;
    if (j < 0 || j >= siblings.length) return;
    [siblings[i], siblings[j]] = [siblings[j], siblings[i]];
    await run(() => api.reorderPlaces(cid, item.parent_id ?? null, siblings));
    refresh();
  };

  // Glisser-déposer dans l'arbre : rang parmi les voisins, dans un autre lieu, ou à la racine (parentId null). Refus
  // (trop de niveaux de sous-lieux, 400) : message du serveur, arbre rechargé tel qu'il est sur le serveur.
  const moveTo = async (id, parentId, index) => {
    const done = await run(() => api.movePlace(id, parentId, index));
    refresh();
    return done !== undefined;
  };

  const pin = async (item, pinned) => {
    if (await run(() => api.pinPlace(item.id, pinned), pinned ? `« ${item.name} » épinglé sur l’accueil` : 'Retiré des épinglés')
      !== undefined) loadTree();
  };

  const remove = async (item) => {
    const kids = childrenOf(data.places, item.id).length;
    const yes = await confirm({
      title: `Supprimer « ${item.name} »`,
      message: `Le lieu et ses images seront supprimés.${kids ? ` Ses ${kids} sous-lieu(x) remonteront d’un niveau.` : ''}`
        + ' Les PNJ et factions qui y étaient reliés ne sont pas touchés.',
      confirmLabel: 'Supprimer',
    });
    if (!yes) return;
    if (await run(() => api.deleteEntity('place', item.id), 'Lieu supprimé') === undefined) return;
    // Les sous-lieux remontent d'un niveau : seul le lieu supprimé lui-même disparaît.
    if (item.id === placeId) {
      await loadTree();
      go(item.parent_id);
    } else {
      await refresh();
    }
  };

  return (
    <KindsContext.Provider value={data?.kinds || null}>
    <div className="scenarios places">
      <header className="topbar">
        {header}
        <PlacesTabs campaignId={cid} active="index" />
        <div className="topbar__right">
        <div className="toolbar">
          <button type="button" className="btn btn--ghost" onClick={() => setCreating({ parentId: placeId || null })}>
            <Icon name="pin" size={16} /><span className="btn__label">Lieu</span>
          </button>
          <button type="button" className="btn btn--ghost" onClick={() => setManagingKinds(true)} disabled={!data}
            title="Créer, renommer, supprimer les types de lieux et choisir leurs icônes">
            <Icon name="list" size={16} /><span className="btn__label"><span className="places-tabs__long">Types de lieux</span><span className="places-tabs__short">Types</span></span>
          </button>
          <span className="toolbar__sep" />
        </div>
        {trailing && <div className="topbar__end">{trailing}</div>}
        </div>
      </header>
      <div className="places__body">
        <PlaceTree data={data} campaignName={campaign.name} currentId={placeId} onOpen={go}
          onCreate={(parentId) => setCreating({ parentId })} onMove={move} onMoveTo={moveTo} onDelete={remove}
          onPin={pin} />
        {data && placeId && detail?.id === placeId && !detail.error ? (
          <EntryShell key={placeId} kind="place" id={placeId} user={user} stamp={detail.updated_at} editing={editing}
            commentId={commentId} resolveRef={resolveRef} onCommentDone={onCommentDone} onRestored={refresh}>
            {editing ? (
              <article className="doc place">
                <PlaceForm place={detail} data={data} graph={graph} resolveRef={resolveRef}
                  onCancel={() => setEditing(false)}
                  onSaved={async () => { setEditing(false); await refresh(); }}
                  onLinksChanged={refresh} />
              </article>
            ) : (
              <PlaceSheet place={detail} data={data} campaignId={cid} resolveRef={resolveRef}
                onOpen={go} onOpenNode={openNode} onEdit={() => setEditing(true)} onChanged={refresh}
                onCreateChild={() => setCreating({ parentId: detail.id })} onDelete={() => remove(detail)}
                onPin={pin} />
            )}
          </EntryShell>
        ) : (
          <main className="doc-main">
            {!data ? <div className="doc-loading" aria-busy="true">Chargement…</div>
              : !placeId ? (
                <PlacesHome data={data} campaignId={cid} campaignName={campaign.name} onOpen={go}
                  onCreate={() => setCreating({ parentId: null })} />
              ) : !detail || detail.id !== placeId ? <div className="doc-loading" aria-busy="true">Chargement…</div>
                : <p className="panel__error doc">{detail.error}</p>}
          </main>
        )}
      </div>
      {creating && data && (
        <CreatePlaceDialog places={data.places} parentId={creating.parentId} onClose={() => setCreating(null)} onCreate={create}
          onManageKinds={() => setManagingKinds(true)} />
      )}
      {managingKinds && data && (
        <KindsManager campaignId={cid} kinds={data.kinds || []} places={data.places} onClose={() => setManagingKinds(false)}
          onChanged={refresh} />
      )}
    </div>
    </KindsContext.Provider>
  );
}

// --- Accueil de l'onglet ---------------------------------------------------------------------

const scenarioKey = (cid) => `maze:scenario:${cid}`;

/** Date courte d'une modification : « aujourd'hui », « hier », sinon jour et mois. */
function shortDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(new Date()) - day(d)) / 86400000);
  if (diff === 0) return 'aujourd’hui';
  if (diff === 1) return 'hier';
  return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' });
}

/**
 * Accueil de l'onglet : lieux épinglés, récemment consultés (sur cet appareil), cités dans un scénario,
 * modifiés récemment, puis les lieux de premier niveau.
 */
function PlacesHome({ data, campaignId, campaignName, onOpen, onCreate }) {
  const byId = useMemo(() => new Map(data.places.map((p) => [p.id, p])), [data.places]);
  const counts = new Map();
  for (const l of data.links) counts.set(l.place_id, (counts.get(l.place_id) || 0) + 1);
  const roots = childrenOf(data.places, null);
  const pathName = (p) => ancestorsOf(byId, p).map((a) => a.name).join(' / ');

  // Fiches rédigées et leurs mentions : lieux cités dans le scénario choisi et ses pages.
  const [mentions, setMentions] = useState(null);
  useEffect(() => {
    let alive = true;
    api.documentMentions(campaignId).then((m) => { if (alive) setMentions(m); }).catch(() => { if (alive) setMentions(null); });
    return () => { alive = false; };
  }, [campaignId]);
  const scenarios = useMemo(() => (mentions
    ? mentions.documents.filter((d) => d.kind === 'scenario' && d.parent_id === mentions.root_id)
      .sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
    : []), [mentions]);
  const [picked, setPicked] = useState(() => Number(storage.get(scenarioKey(campaignId)))
    || Number(storage.get(`maze:map:${campaignId}`)) || null);
  const scenario = scenarios.find((d) => d.id === picked) || scenarios.find((d) => d.status === 'en_cours') || scenarios[0];
  const cited = useMemo(() => {
    if (!scenario || !mentions) return [];
    const ids = new Set();
    const visit = (docId) => {
      for (const [kind, id] of mentions.mentions[docId] || []) if (kind === 'place' && byId.has(id)) ids.add(id);
      for (const d of mentions.documents) if (d.parent_id === docId) visit(d.id);
    };
    visit(scenario.id);
    return [...ids].map((id) => byId.get(id))
      .sort((a, b) => `${pathName(a)} / ${a.name}`.localeCompare(`${pathName(b)} / ${b.name}`, 'fr'));
  }, [scenario, mentions, byId]);
  const pickScenario = (id) => { setPicked(id); storage.set(scenarioKey(campaignId), String(id)); };

  const pinned = data.places.filter((p) => p.pinned).sort((a, b) => a.name.localeCompare(b.name, 'fr'));
  const recent = recentPlaces(campaignId).map((id) => byId.get(id)).filter(Boolean).slice(0, 6);
  const modified = [...data.places].sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || '')).slice(0, 6);

  const list = (items, empty, meta) => (items.length === 0 ? <p className="muted place-home__empty">{empty}</p> : (
    <ul className="place-list">
      {items.map((p) => (
        <li key={p.id}>
          <button type="button" onClick={() => onOpen(p.id)}>
            <span className="place-tree__icon"><PlaceIcon kind={p.kind} size={16} /></span>
            <span className="place-list__text">
              <span className="place-list__name">{p.name}</span>
              {p.parent_id != null && <span className="place-list__path">{pathName(p)}</span>}
            </span>
            {meta && <span className="place-list__meta">{meta(p)}</span>}
          </button>
        </li>
      ))}
    </ul>
  ));

  return (
    <article className="doc place place-home">
      <header className="doc__head">
        <p className="place__kind"><Icon name="pin" size={15} /> Lieux de la campagne</p>
        <h1 className="doc__title">{campaignName}</h1>
      </header>
      {roots.length === 0 ? (
        <div className="place-empty">
          <p>Rangez ici les lieux qui reviennent d’une séance à l’autre : régions, villes, quartiers, tavernes, repaires…
            Chaque lieu peut contenir des sous-lieux, accueillir des PNJ et des factions, et être cité dans les scénarios avec @.</p>
          <button type="button" className="btn btn--primary" onClick={onCreate}><Icon name="plus" size={16} /> Créer un lieu</button>
        </div>
      ) : (
        <>
          <div className="place-home__grid">
            <section className="panel-section">
              <div className="panel-section__head"><h3><Icon name="star" size={14} /> Épinglés</h3><span className="count">{pinned.length}</span></div>
              {list(pinned, 'Épinglez les lieux où vous revenez souvent : étoile sur leur fiche, ou menu ⋯ de l’arbre.')}
            </section>
            <section className="panel-section">
              <div className="panel-section__head"><h3><Icon name="history" size={14} /> Consultés récemment</h3></div>
              {list(recent, 'Les derniers lieux ouverts sur cet appareil apparaîtront ici.')}
            </section>
            <section className="panel-section">
              <div className="panel-section__head place-home__scenario">
                <h3><Icon name="book" size={14} /> Dans le scénario</h3>
                {scenarios.length > 1 && (
                  <select className="place-home__select" value={scenario?.id ?? ''} aria-label="Scénario"
                    onChange={(e) => pickScenario(Number(e.target.value))}>
                    {scenarios.map((d) => <option key={d.id} value={d.id}>{d.title || 'Sans titre'}</option>)}
                  </select>
                )}
              </div>
              {!mentions ? <p className="muted place-home__empty">Chargement…</p>
                : !scenario ? <p className="muted place-home__empty">Aucun scénario dans cette campagne.</p>
                  : (
                    <>
                      {scenarios.length === 1 && <p className="place-home__sub">{scenario.title || 'Sans titre'}</p>}
                      {list(cited, 'Aucun lieu cité avec @ dans ce scénario ni dans ses pages.')}
                    </>
                  )}
            </section>
            <section className="panel-section">
              <div className="panel-section__head"><h3><Icon name="edit" size={14} /> Modifiés récemment</h3></div>
              {list(modified, 'Aucun lieu.', (p) => shortDate(p.updated_at))}
            </section>
          </div>
          <section className="panel-section">
            <div className="panel-section__head"><h3>Premier niveau</h3><span className="count">{roots.length}</span></div>
            <PlaceCards places={roots} all={data.places} counts={counts} onOpen={onOpen} />
          </section>
        </>
      )}
    </article>
  );
}

function PlaceCards({ places, all, counts, onOpen }) {
  const kinds = useContext(KindsContext);
  return (
    <ul className="doc-cards place-cards">
      {places.map((p) => {
        const kids = childrenOf(all, p.id).length;
        const meta = [placeKind(p.kind, kinds).label, kids && `${kids} sous-lieu${kids > 1 ? 'x' : ''}`,
          counts?.get(p.id) && `${counts.get(p.id)} fiche${counts.get(p.id) > 1 ? 's' : ''}`].filter(Boolean).join(' · ');
        return (
          <li key={p.id}>
            <button type="button" onClick={() => onOpen(p.id)}>
              {p.thumb ? <img src={p.thumb} alt="" className="place-cards__img" />
                : <span className="place-cards__img place-cards__img--icon"><PlaceIcon kind={p.kind} size={22} /></span>}
              <span className="doc-cards__title">{p.name}</span>
              <span className="doc-cards__meta">{meta}</span>
              {p.summary && <span className="place-cards__summary">{p.summary}</span>}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

// --- Fiche du lieu -------------------------------------------------------------------------

function PlaceSheet({ place, data, campaignId, resolveRef, onOpen, onOpenNode, onEdit, onChanged, onCreateChild, onDelete, onPin }) {
  const pinned = Boolean(data.places.find((p) => p.id === place.id)?.pinned);
  const cover = place.images.find((i) => i.id === place.image_id);
  const counts = new Map();
  for (const l of data.links) counts.set(l.place_id, (counts.get(l.place_id) || 0) + 1);
  const onMap = place.links.length + place.nested_links.length > 0;
  const kind = placeKind(place.kind, useContext(KindsContext));

  const entityHref = (l) => `#/c/${campaignId}/personnages/${l.entity_kind}-${l.entity_id}`;
  const avatar = (l) => l.thumb || (l.entity_kind === 'npc' ? initialsAvatar(l.name, '#5d6b80') : null);

  // Fiches des sous-lieux, regroupées par fiche (une fiche peut être dans plusieurs sous-lieux).
  const nested = new Map();
  for (const l of place.nested_links) {
    const key = `${l.entity_kind}-${l.entity_id}`;
    if (!nested.has(key)) nested.set(key, { ...l, where: [] });
    nested.get(key).where.push({ id: l.place_id, name: l.place_name, role: l.role });
  }

  return (
    <article className="doc place">
      <div className="doc__bar">
        <PlaceCrumbs place={place} places={data.places} onOpen={onOpen} />
        <button type="button" className={`icon-btn icon-btn--small place-pin${pinned ? ' is-active' : ''}`} aria-pressed={pinned}
          title={pinned ? 'Retirer des épinglés' : 'Épingler sur l’accueil des lieux'}
          aria-label={pinned ? 'Retirer des épinglés' : 'Épingler sur l’accueil des lieux'} onClick={() => onPin(place, !pinned)}>
          <Icon name="star" size={15} />
        </button>
        {onMap && (
          <a className="btn btn--small btn--ghost" href={`#/c/${campaignId}/carte?place=${place.id}`}
            title="Afficher sur la carte les PNJ et factions de ce lieu et de ses sous-lieux">
            <Icon name="graph" size={15} /><span className="btn__label">Sur la carte</span>
          </a>
        )}
        {(place.maps?.length > 0 || place.own_maps?.length > 0) && (
          <a className="btn btn--small btn--ghost"
            href={place.maps?.length ? `#/c/${campaignId}/cartes/${place.maps[0].id}?place=${place.id}` : `#/c/${campaignId}/cartes/${place.own_maps[0].id}`}
            title={place.maps?.length ? `Voir ce lieu sur la carte « ${place.maps[0].name} »` : `Ouvrir la carte « ${place.own_maps[0].name} »`}>
            <Icon name="map" size={15} /><span className="btn__label">Carte interactive</span>
          </a>
        )}
        <EntrySideButton />
        <button type="button" className="btn btn--small btn--primary" onClick={onEdit}>
          <Icon name="edit" size={15} /> Modifier
        </button>
        <button type="button" className="icon-btn icon-btn--small icon-btn--danger" onClick={onDelete}
          title="Supprimer le lieu" aria-label={`Supprimer ${place.name}`}>
          <Icon name="trash" size={15} />
        </button>
      </div>

      <header className="place__head">
        <div className="place__head-text">
          <p className="place__kind"><PlaceIcon kind={place.kind} size={15} /> {kind.label}</p>
          <h1 className="doc__title">{place.name}</h1>
          {place.summary && <p className="sheet-summary place__summary" data-entry-field="summary">{place.summary}</p>}
        </div>
        {cover && <img className="place__cover" src={cover.thumb_url} alt="" />}
      </header>

      {place.ambiance?.trim() && (
        <section className="place-ambiance" aria-label="Accroche : texte à lire aux joueurs"
          data-entry-section="ambiance" data-entry-label="Accroche">
          <p className="place-ambiance__label"><Icon name="scroll" size={14} /> Accroche
            <SectionComment field="ambiance" label="Accroche" /></p>
          <div data-entry-field="ambiance">
            <Markdown text={place.ambiance} resolve={resolveRef} onOpenNode={onOpenNode} />
          </div>
        </section>
      )}

      {place.description?.trim() && (
        <section className="panel-section" data-entry-section="description" data-entry-label="Description">
          <div className="panel-section__head"><h3>Description</h3><SectionComment field="description" label="Description" /></div>
          <div data-entry-field="description">
            <Markdown text={place.description} resolve={resolveRef} onOpenNode={onOpenNode} />
          </div>
        </section>
      )}

      {(place.links.length > 0 || nested.size === 0) && (
      <section className="panel-section" data-entry-section="links" data-entry-label="Présents ici">
        <div className="panel-section__head"><h3>Présents ici</h3><span className="count">{place.links.length}</span></div>
        {place.links.length === 0 ? (
          <p className="muted">Aucun PNJ ni faction relié à ce lieu. Modifiez la fiche pour en ajouter.</p>
        ) : (
          <ul className="member-list place-links">
            {place.links.map((l) => (
              <li key={l.id}>
                <a href={entityHref(l)}>
                  {avatar(l) ? <img src={avatar(l)} alt="" className={l.entity_kind === 'faction' ? 'is-faction' : ''} />
                    : <span className="place-links__seal" style={{ background: l.color }} />}
                  <span className="member-list__name">{l.status === 'mort' ? `† ${l.name}` : l.name}</span>
                  <span className="member-list__role">
                    {[l.role, l.sub || (l.entity_kind === 'faction' ? 'Faction' : 'PNJ'), l.entity_kind === 'npc' && l.status !== 'vivant' && NPC_STATUS[l.status]?.label]
                      .filter(Boolean).join(' · ')}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>
      )}

      <section className="panel-section" data-entry-section="children" data-entry-label="Sous-lieux">
        <div className="panel-section__head">
          <h3>Sous-lieux</h3><span className="count">{place.children.length}</span>
          <button type="button" className="btn btn--small btn--ghost panel-section__action" onClick={onCreateChild}>
            <Icon name="plus" size={14} /> Ajouter
          </button>
        </div>
        {place.children.length === 0
          ? <p className="muted">Aucun sous-lieu. Découpez une ville en quartiers, un bâtiment en salles…</p>
          : <PlaceCards places={place.children} all={data.places} counts={counts} onOpen={onOpen} />}
      </section>

      {nested.size > 0 && (
        <section className="panel-section" data-entry-section="nested" data-entry-label="Dans les sous-lieux">
          <div className="panel-section__head"><h3>Dans les sous-lieux</h3><span className="count">{nested.size}</span></div>
          <ul className="member-list place-links">
            {[...nested.values()].map((l) => (
              <li key={`${l.entity_kind}-${l.entity_id}`}>
                <a href={entityHref(l)}>
                  {avatar(l) ? <img src={avatar(l)} alt="" className={l.entity_kind === 'faction' ? 'is-faction' : ''} />
                    : <span className="place-links__seal" style={{ background: l.color }} />}
                  <span className="member-list__name">{l.status === 'mort' ? `† ${l.name}` : l.name}</span>
                  <span className="member-list__role">
                    {l.where.map((w) => `${w.name}${w.role ? ` (${w.role.toLowerCase()})` : ''}`).join(', ')}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      {place.secrets?.trim() && (
        <section className="panel-section panel-section--secret" data-entry-section="secrets" data-entry-label="Secret">
          <div className="panel-section__head"><h3><Icon name="lock" size={14} /> Secret</h3><SectionComment field="secrets" label="Secret" /></div>
          <div data-entry-field="secrets">
            <Markdown text={place.secrets} resolve={resolveRef} onOpenNode={onOpenNode} />
          </div>
        </section>
      )}
      {place.notes?.trim() && (
        <section className="panel-section" data-entry-section="notes" data-entry-label="Notes">
          <div className="panel-section__head"><h3>Notes</h3><SectionComment field="notes" label="Notes" /></div>
          <div data-entry-field="notes">
            <Markdown text={place.notes} resolve={resolveRef} onOpenNode={onOpenNode} />
          </div>
        </section>
      )}

      {place.mentioned_in.length > 0 && (
        <section className="panel-section" data-entry-section="mentioned" data-entry-label="Apparaît dans">
          <div className="panel-section__head"><h3>Apparaît dans</h3><span className="count">{place.mentioned_in.length}</span></div>
          <ul className="backlinks">
            {place.mentioned_in.map((d) => (
              <li key={d.id}>
                <a href={d.kind === 'campaign' ? `#/c/${campaignId}/scenarios` : `#/c/${campaignId}/scenarios/${d.id}`}>
                  <Icon name={d.kind === 'campaign' ? 'book' : 'page'} size={15} />
                  <span>{d.kind === 'campaign' ? 'Fiche de campagne' : (d.title || 'Sans titre')}</span>
                  {d.kind !== 'campaign' && <span className={`status-dot status--${d.status}`} />}
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      {place.tags.length > 0 && (
        <div className="tags" aria-label="Étiquettes">{place.tags.map((t) => <span key={t} className="tag">{t}</span>)}</div>
      )}

      <section className="panel-section" data-entry-section="gallery" data-entry-label="Images">
        <Gallery kind="place" entity={place} onChanged={onChanged} />
      </section>
    </article>
  );
}

// --- Édition ---------------------------------------------------------------------------------

const FIELDS = PLACE_SECTIONS;
const EDITABLE = PLACE_EDITABLE;

/** Options « Rangé dans » : tous les lieux sauf celui-ci et ses sous-lieux, en arbre. */
function parentOptions(places, excludeId) {
  const banned = excludeId ? subtreeIds(places, excludeId) : new Set();
  const out = [];
  const walk = (parentId, depth) => {
    for (const p of childrenOf(places, parentId)) {
      if (banned.has(p.id)) continue;
      out.push({ id: p.id, label: `${'   '.repeat(depth)}${p.name}` });
      walk(p.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

const PLACE_LABELS = fieldLabels('place');
const PLACE_RICH = new Set(FIELDS.map((f) => f.key));

function PlaceForm({ place, data, graph, resolveRef, onCancel, onSaved, onLinksChanged }) {
  const mentions = useMemo(() => mentionEntities(graph, data.places, data.kinds), [graph, data.places, data.kinds]);
  // Champs modifiés seulement, révision de départ, conflit montré sans perdre la saisie (audit M1, M2, M3).
  const { draft, set, save: saveDraft, conflict, resolve, keepAll, fieldKey } = useEntryDraft({
    kind: 'place', entry: place, keys: EDITABLE, empty: (k) => (k === 'parent_id' ? null : '') });
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  const parents = useMemo(() => parentOptions(data.places, place.id), [data.places, place.id]);

  const save = async (e) => {
    e.preventDefault();
    if (saving || conflict) return;
    setSaving(true);
    let result;
    try {
      result = await saveDraft();
    } catch (err) {
      toast(err.message || String(err), 'error');
      // Rangement refusé (trop de niveaux de sous-lieux, 400 ; audit DATA-10) : rien n'est enregistré, « Rangé dans »
      // revient au lieu actuel, le reste de la saisie est gardé pour un nouvel essai.
      if (err.status === 400 && /niveaux/.test(err.message) && (draft.parent_id ?? null) !== (place.parent_id ?? null)) {
        set('parent_id')(place.parent_id ?? null);
      }
    }
    setSaving(false);
    if (result === 'unchanged') onCancel();
    else if (result === 'saved') { toast('Lieu enregistré', 'success'); onSaved(); }
  };

  return (
    <form className="sheet-form place-form" onSubmit={save}
      onKeyDown={(e) => { if (e.key === 's' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(e); } }}>
      <h2 className="sheet-form__title">Modifier le lieu</h2>
      <ConflictPanel conflict={conflict} labels={PLACE_LABELS} rich={PLACE_RICH} resolveRef={resolveRef}
        onResolve={resolve} onKeepAll={keepAll} />
      <div className="form-grid">
        <Field label="Nom" htmlFor="pl-name">
          <input id="pl-name" value={draft.name} required maxLength={200} onChange={(e) => set('name')(e.target.value)} />
        </Field>
        <Field label="Type" htmlFor="pl-kind">
          <select id="pl-kind" value={draft.kind} onChange={(e) => set('kind')(e.target.value)}>
            <KindOptions />
          </select>
        </Field>
        <Field label="Rangé dans" htmlFor="pl-parent">
          <select id="pl-parent" value={draft.parent_id ?? ''} onChange={(e) => set('parent_id')(e.target.value ? Number(e.target.value) : null)}>
            <option value="">(aucun : lieu principal)</option>
            {parents.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </Field>
      </div>
      <Field label="Résumé" hint="Une phrase, affichée dans les listes et aperçus." htmlFor="pl-summary">
        <textarea id="pl-summary" rows={2} maxLength={500} value={draft.summary} onChange={(e) => set('summary')(e.target.value)} />
      </Field>

      <LinksEditor place={place} graph={graph} onChanged={onLinksChanged} />

      {FIELDS.map((f) => (
        <Field key={f.key} hint={f.hint} label={<>{f.secret && <Icon name="lock" size={13} />} {f.label}</>}>
          <RichField key={fieldKey(f.key)} value={draft[f.key]} onChange={set(f.key)} entities={mentions}
            resolve={resolveRef} secret={f.secret} label={f.label} />
        </Field>
      ))}
      <Field label="Étiquettes" htmlFor="pl-tags">
        <TagInput id="pl-tags" value={draft.tags} onChange={set('tags')} />
      </Field>
      <Gallery kind="place" entity={place} onChanged={onLinksChanged} />
      <p className="field__hint">Les ajouts d’images et les changements de PNJ et factions sont enregistrés immédiatement.</p>
      <div className="sheet-form__actions">
        <button type="button" className="btn btn--ghost" onClick={onCancel}>Annuler</button>
        <button type="submit" className="btn btn--primary" disabled={saving || Boolean(conflict)}
          title={conflict ? 'Choisissez d’abord pour chaque champ en conflit' : undefined}>
          {saving ? 'Enregistrement…' : 'Enregistrer'}
        </button>
      </div>
    </form>
  );
}

/** PNJ et factions du lieu, avec leur rôle (enregistrés tout de suite). */
function LinksEditor({ place, graph, onChanged }) {
  const run = useAction();
  const [adding, setAdding] = useState('');
  const [role, setRole] = useState('');
  const taken = new Set(place.links.map((l) => `${l.entity_kind}-${l.entity_id}`));
  const npcs = (graph?.npcs || []).filter((n) => !taken.has(`npc-${n.id}`));
  const factions = (graph?.factions || []).filter((f) => !taken.has(`faction-${f.id}`));
  const addingKind = adding ? adding.split('-')[0] : 'npc';

  const add = async () => {
    if (!adding) return;
    const { kind, id } = parseNodeId(adding);
    if (await run(() => api.createPlaceLink(place.id, { entity_kind: kind, entity_id: id, role: role.trim() })) === undefined) return;
    setAdding('');
    setRole('');
    onChanged();
  };

  return (
    <fieldset className="memberships place-links-editor">
      <legend>PNJ et factions présents</legend>
      {place.links.length === 0 && <p className="muted">Personne n’est encore relié à ce lieu.</p>}
      {place.links.map((l) => (
        <div key={l.id} className="memberships__row" style={{ '--chip': l.entity_kind === 'faction' ? l.color : PLACE_COLOR }}>
          <span className="chip__dot" />
          <span className="memberships__name">{l.name}</span>
          <input aria-label={`Rôle de ${l.name}`} placeholder="Rôle" defaultValue={l.role} maxLength={80}
            list={`roles-${l.entity_kind}`}
            onBlur={async (e) => {
              if (e.target.value.trim() !== l.role) {
                await run(() => api.updatePlaceLink(l.id, { role: e.target.value.trim() }));
                onChanged();
              }
            }} />
          <button type="button" className="icon-btn icon-btn--small" aria-label={`Retirer ${l.name}`}
            onClick={async () => { await run(() => api.deletePlaceLink(l.id)); onChanged(); }}>
            <Icon name="close" size={14} />
          </button>
        </div>
      ))}
      {(npcs.length > 0 || factions.length > 0) && (
        <div className="memberships__add place-links-editor__add">
          <select value={adding} onChange={(e) => setAdding(e.target.value)} aria-label="PNJ ou faction à ajouter">
            <option value="">Ajouter un PNJ ou une faction…</option>
            {npcs.length > 0 && (
              <optgroup label="PNJ">{npcs.map((n) => <option key={n.id} value={`npc-${n.id}`}>{n.name}</option>)}</optgroup>
            )}
            {factions.length > 0 && (
              <optgroup label="Factions">{factions.map((f) => <option key={f.id} value={`faction-${f.id}`}>{f.name}</option>)}</optgroup>
            )}
          </select>
          <input value={role} onChange={(e) => setRole(e.target.value)} placeholder="Rôle (facultatif)" maxLength={80}
            list={`roles-${addingKind}`} aria-label="Rôle" />
          <button type="button" className="btn btn--small btn--ghost" onClick={add} disabled={!adding}>Ajouter</button>
        </div>
      )}
      <datalist id="roles-npc">{PLACE_ROLES.npc.map((r) => <option key={r} value={r} />)}</datalist>
      <datalist id="roles-faction">{PLACE_ROLES.faction.map((r) => <option key={r} value={r} />)}</datalist>
    </fieldset>
  );
}

// --- Création ------------------------------------------------------------------------------

function CreatePlaceDialog({ places, parentId, onClose, onCreate, onManageKinds }) {
  const kinds = useKinds();
  const [name, setName] = useState('');
  const [parent, setParent] = useState(parentId ?? null);
  const parentKind = places.find((p) => p.id === parent)?.kind;
  // Type proposé d'après le parent : une ville contient des quartiers, un quartier des bâtiments…
  const guess = { region: 'ville', ville: 'quartier', quartier: 'batiment', batiment: 'salle', donjon: 'salle' }[parentKind];
  const suggested = kinds.some((k) => k.key === guess) ? guess : 'lieu';
  const [kind, setKind] = useState(suggested);
  const [kindTouched, setKindTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const options = useMemo(() => parentOptions(places, null), [places]);

  useEffect(() => { if (!kindTouched) setKind(suggested); }, [suggested, kindTouched]);

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    if (!await onCreate({ name: name.trim(), kind, parentId: parent })) setBusy(false);
  };

  return (
    <Modal title="Nouveau lieu" onClose={onClose} footer={(
      <>
        <button type="button" className="btn btn--ghost" onClick={onClose}>Annuler</button>
        <button type="submit" form="new-place" className="btn btn--primary" disabled={!name.trim() || busy}>Créer</button>
      </>
    )}>
      <form id="new-place" onSubmit={submit}>
        <Field label="Nom" htmlFor="np-name">
          <input id="np-name" value={name} maxLength={200} onChange={(e) => setName(e.target.value)}
            placeholder="Taverne du Hareng Saur, Haute ville…" autoComplete="off" />
        </Field>
        <div className="form-grid">
          <Field label="Type" htmlFor="np-kind">
            <select id="np-kind" value={kind} onChange={(e) => { setKind(e.target.value); setKindTouched(true); }}>
              <KindOptions />
            </select>
            {onManageKinds && (
              <button type="button" className="link-btn field__action" onClick={onManageKinds}>Gérer les types de lieux</button>
            )}
          </Field>
          <Field label="Rangé dans" htmlFor="np-parent">
            <select id="np-parent" value={parent ?? ''} onChange={(e) => setParent(e.target.value ? Number(e.target.value) : null)}>
              <option value="">(aucun : lieu principal)</option>
              {options.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </Field>
        </div>
      </form>
    </Modal>
  );
}

// --- Types de lieux --------------------------------------------------------------------------

/** Création, renommage, icône, ordre et suppression des types de lieux de la campagne. */
function KindsManager({ campaignId, kinds, places, onClose, onChanged }) {
  const run = useAction();
  const confirm = useConfirm();
  const [labels, setLabels] = useState(() => Object.fromEntries(kinds.map((k) => [k.id, k.label])));
  const [picker, setPicker] = useState(null); // id du type dont on choisit l'icône, ou 'new'
  const [newLabel, setNewLabel] = useState('');
  const [newIcon, setNewIcon] = useState('pin');
  const [importing, setImporting] = useState(null); // « 3 / 19 » pendant la création de types depuis des images
  const imageInput = useRef(null);
  const imageFor = useRef(null);   // type qui reçoit l'image choisie
  const bulkInput = useRef(null);
  const toast = useToast();
  // Types rechargés (après un enregistrement) : noms à jour, sauf celui en cours de saisie.
  useEffect(() => {
    const focused = document.activeElement?.dataset.kind;
    setLabels((l) => Object.fromEntries(kinds.map((k) => [k.id, String(k.id) === focused ? (l[k.id] ?? k.label) : k.label])));
  }, [kinds]);
  // Palette d'icônes : fermée par un clic ailleurs.
  useEffect(() => {
    if (picker === null) return undefined;
    const onDown = (e) => { if (!e.target.closest?.('.kinds__icon, .kinds__palette')) setPicker(null); };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [picker]);
  const used = (key) => places.filter((p) => p.kind === key).length;

  const save = async (k, changes) => {
    if (await run(() => api.updatePlaceKind(k.id, changes)) !== undefined) onChanged();
  };
  const rename = (k) => {
    const label = (labels[k.id] || '').trim();
    if (!label) { setLabels((l) => ({ ...l, [k.id]: k.label })); return; }
    if (label !== k.label) save(k, { label });
  };
  const remove = async (k) => {
    const n = used(k.key);
    const yes = await confirm({
      title: `Supprimer le type « ${k.label} »`,
      message: n ? `${n} lieu${n > 1 ? 'x' : ''} de ce type passer${n > 1 ? 'ont' : 'a'} au type « Lieu ».` : 'Aucun lieu n’utilise ce type.',
      confirmLabel: 'Supprimer',
    });
    if (yes && await run(() => api.deletePlaceKind(k.id), 'Type supprimé') !== undefined) onChanged();
  };
  const shift = async (i, dir) => {
    const ids = kinds.map((k) => k.id);
    const j = i + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    if (await run(() => api.reorderPlaceKinds(campaignId, ids)) !== undefined) onChanged();
  };
  const add = async (e) => {
    e.preventDefault();
    const label = newLabel.trim();
    if (!label) return;
    const created = await run(() => api.createPlaceKind(campaignId, { label, icon: newIcon }), `Type « ${label} » créé`);
    if (created) { setNewLabel(''); setNewIcon('pin'); onChanged(); }
  };

  // Icône importée (symbole d'une légende de carte) : fond clair rendu transparent et recadrage par le serveur.
  const pickImage = (k) => { imageFor.current = k; setPicker(null); imageInput.current?.click(); };
  const setImage = async (file) => {
    const k = imageFor.current;
    if (!file || !k) return;
    if (await run(() => api.setPlaceKindImage(k.id, file), `Icône de « ${k.label} » importée`) !== undefined) onChanged();
  };
  const clearImage = async (k) => {
    setPicker(null);
    if (await run(() => api.deletePlaceKindImage(k.id)) !== undefined) onChanged();
  };
  // Un type par image, nommé d'après le fichier (« Cité-forteresse.png ») ; un type du même nom reçoit l'image.
  const importMany = async (files) => {
    if (!files.length) return;
    let done = 0;
    let failed = 0;
    const known = new Map(kinds.map((k) => [k.label.toLowerCase(), k]));
    for (const file of files) {
      setImporting(`${done + failed + 1} / ${files.length}`);
      const name = file.name.replace(/\.[^.]+$/, '').replace(/[_]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 40);
      const label = name ? name[0].toUpperCase() + name.slice(1) : 'Type';
      let kind = known.get(label.toLowerCase());
      if (!kind) {
        kind = await run(() => api.createPlaceKind(campaignId, { label, icon: 'pin' }));
        if (kind) known.set(label.toLowerCase(), kind);
      }
      if (kind && await run(() => api.setPlaceKindImage(kind.id, file)) !== undefined) done += 1;
      else failed += 1;
    }
    setImporting(null);
    toast(`${done} type${done > 1 ? 's' : ''} avec icône${failed ? ` (${failed} en échec)` : ''}`, failed ? 'error' : 'success');
    onChanged();
  };

  const iconButton = (icon, key, title, image) => (
    <button type="button" className={`icon-btn kinds__icon${picker === key ? ' is-open' : ''}`} title={title} aria-label={title}
      aria-expanded={picker === key} onClick={() => setPicker(picker === key ? null : key)}>
      {image ? <KindImage src={image} size={17} /> : <Icon name={icon} size={17} />}
    </button>
  );
  // Choix de l'icône : déplié sous la ligne du type (dans la liste, sans recouvrir les autres types).
  const palette = (icon, key, onPick, kind) => picker === key && (
    <div className="kinds__palette" role="listbox" aria-label="Choisir une icône">
      {PLACE_ICONS.map((name) => (
        <button key={name} type="button" role="option" aria-selected={!kind?.image_url && name === icon} title={name} aria-label={name}
          className={`icon-btn${!kind?.image_url && name === icon ? ' is-active' : ''}`}
          onClick={() => { setPicker(null); onPick(name); if (kind?.image_url) clearImage(kind); }}>
          <Icon name={name} size={17} />
        </button>
      ))}
      {kind && (
        <div className="kinds__palette-image">
          <button type="button" className="btn btn--small btn--ghost" onClick={() => pickImage(kind)}>
            <Icon name="upload" size={14} /> {kind.image_url ? 'Autre image…' : 'Importer une image…'}
          </button>
          {kind.image_url && (
            <button type="button" className="btn btn--small btn--ghost" onClick={() => clearImage(kind)}>
              <Icon name="close" size={14} /> Revenir à l’icône
            </button>
          )}
        </div>
      )}
    </div>
  );

  return (
    <Modal title="Types de lieux" onClose={onClose} footer={<button type="button" className="btn btn--primary" onClick={onClose}>Fermer</button>}>
      <p className="muted kinds__intro">Les types servent à classer les lieux (menus, icônes de l’arbre, des fiches et des cartes).
        Une icône peut être une image importée, par exemple un symbole de la légende de votre carte (fond blanc retiré
        automatiquement). Supprimer un type fait passer ses lieux au type « Lieu ».</p>
      <ul className="kinds">
        {kinds.map((k, i) => (
          <li key={k.id}>
            <div className="kinds__row">
            {iconButton(k.icon, k.id, `Icône de « ${k.label} »`, k.image_url)}
            <input className="input kinds__label" value={labels[k.id] ?? k.label} maxLength={40} data-kind={k.id}
              aria-label={`Nom du type ${k.label}`}
              onChange={(e) => setLabels((l) => ({ ...l, [k.id]: e.target.value }))}
              onBlur={() => rename(k)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur(); } }} />
            <span className="kinds__count" title="Lieux de ce type">{used(k.key) || ''}</span>
            <button type="button" className="icon-btn icon-btn--small" aria-label="Monter" title="Monter" disabled={i === 0}
              onClick={() => shift(i, -1)}><Icon name="up" size={15} /></button>
            <button type="button" className="icon-btn icon-btn--small" aria-label="Descendre" title="Descendre"
              disabled={i === kinds.length - 1} onClick={() => shift(i, 1)}><Icon name="down" size={15} /></button>
            {k.key === 'lieu' ? (
              <span className="icon-btn icon-btn--small kinds__locked" title="Type par défaut : ne se supprime pas"><Icon name="lock" size={14} /></span>
            ) : (
              <button type="button" className="icon-btn icon-btn--small" aria-label={`Supprimer ${k.label}`} title="Supprimer"
                onClick={() => remove(k)}><Icon name="trash" size={15} /></button>
            )}
            </div>
            {palette(k.icon, k.id, (icon) => save(k, { icon }), k)}
          </li>
        ))}
      </ul>
      <form className="kinds__new" onSubmit={add}>
        <div className="kinds__row">
        {iconButton(newIcon, 'new', 'Icône du nouveau type')}
        <input className="input kinds__label" value={newLabel} maxLength={40} placeholder="Nouveau type : Planète, Vaisseau…"
          aria-label="Nom du nouveau type" onChange={(e) => setNewLabel(e.target.value)} />
        <button type="submit" className="btn btn--small btn--primary" disabled={!newLabel.trim()}>
          <Icon name="plus" size={14} /> Ajouter
        </button>
        </div>
        {palette(newIcon, 'new', setNewIcon)}
      </form>
      <div className="kinds__bulk">
        <button type="button" className="btn btn--small btn--ghost" disabled={importing !== null} onClick={() => bulkInput.current?.click()}
          title="Un type par image, nommé d’après le fichier (« Cité-forteresse.png ») ; un type existant du même nom reçoit l’image">
          <Icon name="upload" size={14} /> {importing ? `Import… ${importing}` : 'Créer des types depuis des images…'}
        </button>
      </div>
      <input ref={imageInput} type="file" accept="image/png,image/webp,image/jpeg,image/gif" hidden
        onChange={(e) => { const f = e.target.files[0]; e.target.value = ''; setImage(f); }} />
      <input ref={bulkInput} type="file" accept="image/png,image/webp,image/jpeg,image/gif" multiple hidden
        onChange={(e) => { const files = [...e.target.files]; e.target.value = ''; importMany(files); }} />
    </Modal>
  );
}
