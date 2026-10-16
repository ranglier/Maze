import { useCallback, useEffect, useState } from 'react';
import { api, onUnauthorized } from './api.js';
import AdminPage from './components/AdminPage.jsx';
import CampaignMenu, { CampaignDialog, importLabel, useCampaignImport } from './components/CampaignMenu.jsx';
import LoginScreen, { SetupScreen } from './components/LoginScreen.jsx';
import CharactersView from './components/CharactersView.jsx';
import MapsView from './components/MapsView.jsx';
import PlacesView from './components/PlacesView.jsx';
import ScenariosView from './components/ScenariosView.jsx';
import SearchPalette from './components/SearchPalette.jsx';
import UserMenu from './components/UserMenu.jsx';
import Workspace from './components/Workspace.jsx';
import { Icon, useConfirm } from './components/ui.jsx';
import { confirmDiscard, hasUnsaved, localEdits, setSessionUser } from './session.js';
import { LogoutDialog } from './components/SessionNotices.jsx';
import { N_, applyPreferences, pt, t, useLocale } from './i18n/index.js';
import { storage } from './util.js';

// Textes de module traduits à l'affichage (t(TREE_LABEL[…])) : N_ les signale seulement à l'extraction.
const TREE_LABEL = {
  scenarios: N_('Liste des fiches'), personnages: N_('Liste des personnages'), lieux: N_('Liste des lieux'),
};

/**
 * Routes :
 *   #/c/<id>                            fiche de campagne (onglet Scénarios), page d'accueil
 *   #/c/<id>/scenarios[/<doc>]          fiche de campagne et scénarios
 *        ?find=<texte>                  ouvre la fiche sur le passage trouvé (recherche globale)
 *        ?comment=<id>                  ouvre le fil de commentaires
 *   #/c/<id>/carte[?scenario=<doc>]     carte des relations d'un scénario (sans : carte des factions)
 *   #/c/<id>/carte?place=<lieu>         PNJ et factions d'un lieu (et de ses sous-lieux)
 *   #/c/<id>/personnages[/<npc|faction>-<n>][?edit=1]   index des PNJ et factions
 *   #/c/<id>/fiche/<npc|faction>-<n>    ancien lien de fiche : index des personnages
 *   #/c/<id>/lieux[/<lieu>][?edit=1]    lieux de la campagne (fiche ouverte en édition)
 *   #/c/<id>/cartes[/<carte>][?place=<lieu>]  cartes interactives des lieux (lieu montré sur la carte)
 *   #/admin/<section>
 */
function parseHash() {
  const [hash, qs] = window.location.hash.split('?');
  const params = new URLSearchParams(qs || '');
  const extra = {
    find: params.get('find') || null,
    comment: Number(params.get('comment')) || null,
    scenario: Number(params.get('scenario')) || null,
    place: Number(params.get('place')) || null,
    edit: params.get('edit') === '1',
    factions: params.get('factions') === '1',
  };
  const route = parsePath(hash);
  if (route.view !== 'campaign') return route;
  // Ancienne adresse de la carte filtrée (#/c/<id>?scenario=… ou ?place=…).
  if (/^#\/c\/\d+$/.test(hash) && (extra.scenario || extra.place)) route.tab = 'carte';
  return { ...route, ...extra };
}

function parsePath(hash) {
  const admin = hash.match(/^#\/admin(?:\/([\w-]+))?/);
  if (admin) return { view: 'admin', section: admin[1] || 'comptes' };
  const m = hash.match(/^#\/c\/(\d+)(?:\/(scenarios|cartes|carte|personnages|fiche|lieux)(?:\/([\w-]+))?)?/);
  if (!m) return { view: 'campaign', id: null, tab: 'scenarios', docId: null };
  const id = Number(m[1]);
  if (m[2] === 'carte') return { view: 'campaign', id, tab: 'carte' };
  if (m[2] === 'lieux') return { view: 'campaign', id, tab: 'lieux', placeId: m[3] ? Number(m[3]) : null };
  if (m[2] === 'cartes') return { view: 'campaign', id, tab: 'lieux', sub: 'cartes', mapId: Number(m[3]) || null };
  if (m[2] === 'personnages' || m[2] === 'fiche') {
    return { view: 'campaign', id, tab: 'personnages', entity: /^(npc|faction)-\d+$/.test(m[3] || '') ? m[3] : null };
  }
  return { view: 'campaign', id, tab: 'scenarios', docId: m[2] === 'scenarios' && m[3] ? Number(m[3]) : null };
}

const navigate = (hash) => { window.location.hash = hash; };

// Rang de l'entrée courante de l'historique du navigateur, gardé dans history.state (voir onHash dans App).
const stampHistory = (index) => window.history.replaceState({ ...(window.history.state || {}), mazeIdx: index }, '');
// Rang exact donné par l'API Navigation quand le navigateur l'a (Chromium, Firefox et Safari récents) : une entrée
// créée avant ce code (onglet ouvert pendant la mise à jour, puis rechargé : Chromium garde ces entrées dans le même
// document) n'a pas de rang dans history.state, et « Retour » vers elle passait pour un nouveau lien (rang + 1) ;
// refusé, le navigateur reculait alors d'une entrée de plus au lieu de revenir.
const entryRank = () => {
  const index = window.navigation?.currentEntry?.index;
  return Number.isInteger(index) && index >= 0 ? index : window.history.state?.mazeIdx;
};
let historyIndex = entryRank() ?? 0;
stampHistory(historyIndex);

export default function App() {
  // Langue courante : App est redessinée quand elle change, et toute l'interface avec elle.
  const locale = useLocale();
  const [auth, setAuth] = useState(null);
  const [campaigns, setCampaigns] = useState(null);
  const [route, setRoute] = useState(parseHash);
  const [error, setError] = useState(null);
  const [searchOpen, setSearchOpen] = useState(false);
  // Téléphone : la liste des fiches (scénarios, personnages, lieux) s'ouvre en tiroir par-dessus la page.
  const [drawerOpen, setDrawerOpen] = useState(false);
  useEffect(() => {
    document.documentElement.classList.toggle('is-drawer-open', drawerOpen);
    if (!drawerOpen) return undefined;
    const onKey = (e) => { if (e.key === 'Escape' && !document.querySelector('.modal-backdrop, .popover')) setDrawerOpen(false); };
    // Fiche choisie dans le tiroir : il se referme, même si c'est la fiche déjà affichée (adresse inchangée).
    const onClick = (e) => { if (e.target.closest?.('.doc-tree .doc-tree__item:not(.doc-tree__group) > .doc-tree__link, .doc-tree .doc-tree__root')) setDrawerOpen(false); };
    window.addEventListener('keydown', onKey);
    document.addEventListener('click', onClick);
    return () => { window.removeEventListener('keydown', onKey); document.removeEventListener('click', onClick); };
  }, [drawerOpen]);
  useEffect(() => {
    const close = () => setDrawerOpen(false);
    const onDrawer = (e) => setDrawerOpen(Boolean(e.detail?.open));
    window.addEventListener('maze:scrim', close);
    window.addEventListener('maze:drawer', onDrawer);
    return () => { window.removeEventListener('maze:scrim', close); window.removeEventListener('maze:drawer', onDrawer); };
  }, []);

  const checkAuth = useCallback(() => {
    api.authStatus().then(setAuth).catch((e) => setError(e.message));
  }, []);

  // Langue : réglage du compte, puis choix de l'écran de connexion (ce navigateur), langue du navigateur, langue par
  // défaut de l'instance (MAZE_DEFAULT_LOCALE).
  const accountLocale = auth?.authenticated ? auth.user?.locale || '' : '';
  useEffect(() => {
    if (auth) applyPreferences({ account: accountLocale, fallback: auth.default_locale });
  }, [auth, accountLocale]);
  const onLocale = useCallback((locale) => {
    setAuth((a) => (a?.user ? { ...a, user: { ...a.user, locale } } : a));
  }, []);

  useEffect(() => {
    checkAuth();
    onUnauthorized(() => setAuth((a) => (a ? { ...a, authenticated: false, user: null } : a)));
  }, [checkAuth]);

  // Recherche globale : Ctrl+K (dans l'éditeur, Ctrl+K reste « lien » ; Ctrl+Maj+F marche partout).
  useEffect(() => {
    const onKey = (e) => {
      const mod = e.ctrlKey || e.metaKey;
      if (!mod || !auth?.authenticated) return;
      const key = e.key.toLowerCase();
      const inEditor = e.target.closest?.('.ProseMirror');
      if ((key === 'k' && !e.shiftKey && !inEditor && !e.defaultPrevented) || (key === 'f' && e.shiftKey)) {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [auth?.authenticated]);

  // Changement d'adresse (lien, autre fiche de la liste, onglet, retour ou avance du navigateur) pendant une saisie de
  // fiche PNJ, faction ou lieu : confirmation (audit M3). Refusée : le navigateur revient sur l'entrée de
  // l'historique d'où l'on partait (history.go, d'après le rang posé dans history.state), sans entrée en double.
  const confirm = useConfirm();
  useEffect(() => {
    let reverting = false;
    const onHash = async (e) => {
      const wasReverting = reverting;
      reverting = false;
      if (wasReverting && entryRank() === historyIndex) return;  // retour provoqué ci-dessous
      const known = entryRank();
      const target = known ?? historyIndex + 1;  // sans rang : nouvelle entrée (lien, adresse saisie)
      if (hasUnsaved() && !(await confirmDiscard(confirm))) {
        const delta = historyIndex - target;
        if (delta) {
          reverting = true;
          window.history.go(delta);
        } else {
          window.history.replaceState(window.history.state, '', e.oldURL ? new URL(e.oldURL).hash || '#/' : '#/');
        }
        return;
      }
      if (window.history.state?.mazeIdx !== target) stampHistory(target);
      historyIndex = target;
      setRoute(parseHash());
      setDrawerOpen(false);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, [confirm]);

  const loadCampaigns = useCallback(async () => {
    try {
      setCampaigns(await api.campaigns());
    } catch (e) {
      if (e.status !== 401) setError(e.message);
    }
  }, []);

  useEffect(() => {
    if (auth?.authenticated) loadCampaigns();
    else setCampaigns(null);
  }, [auth?.authenticated, auth?.user?.id, loadCampaigns]);

  const wantedId = route.view === 'campaign' ? (route.id || Number(storage.get('maze:campaign')) || null) : null;
  const current = campaigns?.find((c) => c.id === wantedId) || campaigns?.[0] || null;

  useEffect(() => {
    if (route.view !== 'campaign' || !current) return;
    storage.set('maze:campaign', String(current.id));
    document.title = `${current.name} | Maze`;
    if (route.id !== current.id) {
      // Adresse changée depuis ce rendu, hashchange pas encore traité (lien ouvert juste après la connexion, pendant
      // l'arrivée de la liste des campagnes) : l'événement mettra route à jour, l'adresse ne doit pas être écrasée.
      const live = parseHash();
      if (live.view !== route.view || live.id !== route.id) return;
      window.history.replaceState(window.history.state, '', `#/c/${current.id}`);
      setRoute(parseHash());
    }
  }, [current, route]);

  useEffect(() => {
    if (route.view === 'admin') document.title = `${t('Administration')} | Maze`;
  }, [route.view, locale]);

  // Met à jour les compteurs du menu sans recharger toute la liste.
  const onGraphLoaded = useCallback((g) => {
    setCampaigns((list) => list?.map((c) => (c.id === g.campaign.id
      ? { ...c, ...g.campaign, npc_count: g.npcs.length, faction_count: g.factions.length } : c)));
  }, []);

  // Textes non fusionnés : rangés par compte et par instance (session.js), montrés seulement à ce compte connecté.
  useEffect(() => {
    setSessionUser(auth?.authenticated && auth.user ? { ...auth.user, instance: auth.instance } : null);
  }, [auth]);
  const [logoutAsk, setLogoutAsk] = useState(false);
  const doLogout = async () => { setLogoutAsk(false); await api.logout(); setCampaigns(null); checkAuth(); };
  // Textes du compte gardés dans ce navigateur seulement : avertissement avant de se déconnecter (poste partagé).
  const logout = () => { if (localEdits().length) setLogoutAsk(true); else doLogout(); };
  const logoutDialog = logoutAsk && <LogoutDialog onCancel={() => setLogoutAsk(false)} onLogout={doLogout} />;

  if (error) return <main className="splash"><div className="splash__card"><p className="form-error">{error}</p></div></main>;
  if (!auth) return <main className="splash" aria-busy="true" />;
  if (auth.setup_required) return <SetupScreen onDone={checkAuth} sourceUrl={auth.source_url} />;
  // Après connexion : fiche de la campagne (onglet Scénarios), quelle que soit l'adresse d'arrivée.
  if (!auth.authenticated) {
    // Après connexion : fiche de la campagne, sauf lien direct vers une page précise (session expirée, lien partagé).
    return <LoginScreen sourceUrl={auth.source_url} onLoggedIn={() => {
      if (!/^#\/(c\/\d+\/|admin)/.test(window.location.hash)) window.history.replaceState(window.history.state, '', '#/');
      setRoute(parseHash());
      checkAuth();
    }} />;
  }

  const user = auth.user;
  const userMenu = (
    <UserMenu user={user} onLogout={logout} onAdmin={() => navigate('#/admin')} sourceUrl={auth.source_url}
      onLocale={onLocale} />
  );
  const palette = (
    <>
      {searchOpen && (
        <SearchPalette campaign={current} multiple={(campaigns?.length || 0) > 1}
          onClose={() => setSearchOpen(false)} onNavigate={navigate} />
      )}
      {logoutDialog}
    </>
  );
  // Effacer ?find= / ?comment= / ?scenario= une fois pris en compte (un rechargement ne les rejoue pas).
  const consumeQuery = (path) => { window.history.replaceState(window.history.state, '', path); setRoute(parseHash()); };

  if (route.view === 'admin') {
    if (user.role !== 'admin') {
      navigate('#/');
      return null;
    }
    const header = (
      <>
        <span className="wordmark wordmark--small">Maze</span>
        <span className="topbar__title">{t('Administration')}</span>
        <div className="toolbar">
          <button type="button" className="btn btn--ghost" onClick={() => navigate(current ? `#/c/${current.id}` : '#/')}>
            <Icon name="back" size={16} /><span className="btn__label">{t('Retour aux campagnes')}</span>
          </button>
          {userMenu}
        </div>
      </>
    );
    return (
      <>
        <AdminPage user={user} section={route.section} header={header} onNavigate={navigate} onCampaignsChanged={loadCampaigns} />
        {palette}
      </>
    );
  }

  if (!campaigns) return <main className="splash" aria-busy="true" />;

  if (!current) {
    return (
      <main className="splash">
        {logoutDialog}
        <div className="splash__corner">{userMenu}</div>
        <div className="splash__card splash__card--wide">
          <h1 className="wordmark">Maze</h1>
          <p className="splash__lead">
            {user.role === 'admin'
              ? t('Commencez par créer une campagne : elle rassemblera vos PNJ, factions et intrigues.')
              : t('Aucune campagne ne vous est attribuée pour l’instant. Créez-en une, ou demandez à un administrateur de vous en ouvrir l’accès.')}
          </p>
          <CampaignDialogInline onSaved={(c) => { setCampaigns([{ npc_count: 0, faction_count: 0, ...c }]); navigate(`#/c/${c.id}`); }} />
        </div>
      </main>
    );
  }

  const header = (
    <>
      <span className="wordmark wordmark--small">Maze</span>
      <CampaignMenu
        campaigns={campaigns}
        current={current}
        onSwitch={(id) => navigate(route.tab === 'scenarios' ? `#/c/${id}` : `#/c/${id}/${route.sub || route.tab}`)}
        onChanged={async (saved, isNew) => { await loadCampaigns(); if (isNew) navigate(`#/c/${saved.id}`); }}
        onDeleted={async (id) => {
          const rest = campaigns.filter((c) => c.id !== id);
          setCampaigns(rest);
          window.history.replaceState(window.history.state, '', rest[0] ? `#/c/${rest[0].id}` : '#/');
          setRoute(parseHash());
        }}
      />
      <nav className="module-tabs" aria-label={t('Modules')}>
        {[
          ['scenarios', `#/c/${current.id}`, 'book', t('Scénarios')],
          ['carte', `#/c/${current.id}/carte`, 'graph', pt('nav', 'Carte')],
          ['personnages', `#/c/${current.id}/personnages`, 'person', t('Personnages')],
          ['lieux', `#/c/${current.id}/lieux`, 'pin', t('Lieux')],
        ].map(([tab, href, icon, label]) => (
          <a key={tab} href={href} className={route.tab === tab ? 'is-active' : ''} title={label}
            aria-current={route.tab === tab ? 'page' : undefined}>
            <Icon name={icon} size={16} /><span>{label}</span>
          </a>
        ))}
      </nav>
      {route.tab !== 'carte' && (
        <button type="button" className={`icon-btn tree-toggle${drawerOpen ? ' is-active' : ''}`} aria-expanded={drawerOpen}
          aria-haspopup="true"
          title={t(TREE_LABEL[route.tab])} aria-label={t(TREE_LABEL[route.tab])}
          onClick={() => setDrawerOpen((v) => !v)}>
          <Icon name="list" size={19} />
        </button>
      )}
      <button type="button" className="search-trigger" onClick={() => setSearchOpen(true)}
        title={t('Rechercher dans les PNJ, factions, lieux, scénarios et commentaires (Ctrl+K)')}>
        <Icon name="search" size={16} />
        <span className="search-trigger__label">{t('Rechercher')}</span>
        <kbd>Ctrl K</kbd>
      </button>
    </>
  );

  const bottomNav = (
    <BottomNav campaignId={current.id} tab={route.tab} onSearch={() => setSearchOpen(true)} />
  );

  if (route.tab === 'scenarios') {
    return (
      <>
        {bottomNav}
        <ScenariosView key={current.id} campaign={current} user={user} header={header} trailing={userMenu}
          docId={route.docId} find={route.find} commentId={route.comment} onNavigate={navigate}
          onTargetDone={() => consumeQuery(window.location.hash.split('?')[0])} />
        {palette}
      </>
    );
  }

  if (route.tab === 'lieux' && route.sub === 'cartes') {
    return (
      <>
        {bottomNav}
        <MapsView key={current.id} campaign={current} header={header} trailing={userMenu}
          mapId={route.mapId} focusPlace={route.place} onNavigate={navigate}
          onFocusDone={(mapId) => consumeQuery(`#/c/${current.id}/cartes${mapId ? `/${mapId}` : ''}`)} />
        {palette}
      </>
    );
  }

  if (route.tab === 'lieux') {
    return (
      <>
        {bottomNav}
        <PlacesView key={current.id} campaign={current} user={user} header={header} trailing={userMenu}
          placeId={route.placeId} editRequested={route.edit} commentId={route.comment} onNavigate={navigate}
          onEditDone={() => consumeQuery(window.location.hash.split('?')[0])}
          onCommentDone={() => consumeQuery(window.location.hash.split('?')[0])} />
        {palette}
      </>
    );
  }

  if (route.tab === 'personnages') {
    return (
      <>
        {bottomNav}
        <CharactersView key={current.id} campaign={current} user={user} header={header} trailing={userMenu}
          entity={route.entity} editRequested={route.edit} commentId={route.comment} onNavigate={navigate}
          onEditDone={() => consumeQuery(window.location.hash.split('?')[0])}
          onCommentDone={() => consumeQuery(window.location.hash.split('?')[0])}
          onGraphLoaded={onGraphLoaded} />
        {palette}
      </>
    );
  }

  return (
    <>
      {bottomNav}
      <Workspace key={current.id} campaign={current} header={header} trailing={userMenu}
        scenario={route.scenario} place={route.place} factionsAsked={route.factions}
        onScenario={(id) => consumeQuery(`#/c/${current.id}/carte${id ? `?scenario=${id}` : '?factions=1'}`)}
        onPlace={(id) => consumeQuery(`#/c/${current.id}/carte${id ? `?place=${id}` : '?factions=1'}`)}
        onNavigate={navigate} onGraphLoaded={onGraphLoaded} />
      {palette}
    </>
  );
}

function CampaignDialogInline({ onSaved }) {
  const [open, setOpen] = useState(false);
  const importer = useCampaignImport(onSaved);
  return (
    <>
      <button type="button" className="btn btn--primary btn--block" onClick={() => setOpen(true)}>
        {t('Créer une campagne')}
      </button>
      <button type="button" className="btn btn--ghost btn--block" onClick={importer.pick} disabled={importer.progress !== null}>
        {importLabel(importer.progress)}
      </button>
      {importer.input}
      {open && <CampaignDialog onClose={() => setOpen(false)} onSaved={(c) => { setOpen(false); onSaved(c); }} />}
    </>
  );
}

// Libellés traduits à l'affichage (fonctions appelées au rendu, pas au chargement du module).
const NAV = [
  ['scenarios', '', 'book', () => t('Scénarios')],
  ['carte', '/carte', 'graph', () => pt('nav', 'Carte')],
  ['personnages', '/personnages', 'person', () => t('Personnages')],
  ['lieux', '/lieux', 'pin', () => t('Lieux')],
];

/** Téléphone : onglets des modules et recherche en bas de l'écran, à portée du pouce. */
function BottomNav({ campaignId, tab, onSearch }) {
  useEffect(() => {
    document.documentElement.classList.add('has-nav');
    return () => document.documentElement.classList.remove('has-nav');
  }, []);
  return (
    <nav className="bottom-nav" aria-label={t('Modules')}>
      {NAV.map(([key, path, icon, label]) => (
        <a key={key} href={`#/c/${campaignId}${path}`} className={tab === key ? 'is-active' : ''}
          aria-current={tab === key ? 'page' : undefined}>
          <Icon name={icon} size={21} /><span>{label()}</span>
        </a>
      ))}
      <button type="button" onClick={onSearch}>
        <Icon name="search" size={21} /><span>{t('Rechercher')}</span>
      </button>
    </nav>
  );
}
