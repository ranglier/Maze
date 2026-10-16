import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import { NPC_STATUS } from '../constants.js';
import { historyIdentity, textSections } from '../fields.js';
import { storage } from '../util.js';
import { TOUCH } from '../viewport.js';
import CommentsPanel from './CommentsPanel.jsx';
import { HistoryPanel, versionDate, versionTitle } from './HistoryPanel.jsx';
import { useKinds } from './PlaceIcon.jsx';
import { Icon, Markdown, useAction, useConfirm, useToast } from './ui.jsx';
import { N_, t, tn } from '../i18n/index.js';

/*
 * Panneau latéral des fiches PNJ, factions et lieux (pleine page) : Sommaire, Commentaires, Historique,
 * comme pour les fiches rédigées.
 *
 * La fiche affichée balise son contenu : data-entry-section (+ data-entry-label) sur chaque bloc du sommaire,
 * data-entry-field sur le texte de chaque section commentable (résumé, apparence, secret…). Les commentaires
 * ne posent aucune marque dans le texte : l'extrait cité est retrouvé dans la section à l'affichage et surligné
 * avec l'API CSS Custom Highlight (aucune modification du DOM, donc rien qui gêne React). Un extrait qui n'est
 * plus dans le texte laisse le fil rattaché à sa section (« extrait modifié »).
 */

const NotesContext = createContext(null);
/** Panneau de la fiche en cours (null hors pleine page : panneau de la carte, formulaire de création…). */
export const useEntryNotes = () => useContext(NotesContext);

const HL = { open: 'maze-entry-comment', active: 'maze-entry-comment-active', draft: 'maze-entry-comment-draft' };
const canHighlight = () => typeof CSS !== 'undefined' && Boolean(CSS.highlights) && typeof Highlight !== 'undefined';
const SIDE_KEY = 'maze:entry:side';
const MAX_QUOTE = 500;

function clearHighlights() {
  if (!canHighlight()) return;
  for (const name of Object.values(HL)) CSS.highlights.delete(name);
}

/** Texte d'un élément sans les blancs, avec la position (nœud, décalage) de chaque caractère. */
function textIndex(root) {
  let compact = '';
  const map = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.nodeValue;
    for (let i = 0; i < text.length; i += 1) {
      if (/\s/.test(text[i])) continue;
      compact += text[i];
      map.push([node, i]);
    }
  }
  return { compact, map };
}

/** Plage du passage `quote` dans l'index (blancs ignorés), ou null. */
function findQuote(index, quote) {
  const q = (quote || '').replace(/\s+/g, '');
  if (!q) return null;
  const at = index.compact.indexOf(q);
  if (at < 0) return null;
  const [startNode, startOffset] = index.map[at];
  const [endNode, endOffset] = index.map[at + q.length - 1];
  const range = document.createRange();
  range.setStart(startNode, startOffset);
  range.setEnd(endNode, endOffset + 1);
  return { range, at };
}

function pointAt(x, y) {
  if (document.caretPositionFromPoint) {
    const p = document.caretPositionFromPoint(x, y);
    return p ? [p.offsetNode, p.offset] : null;
  }
  if (document.caretRangeFromPoint) {
    const r = document.caretRangeFromPoint(x, y);
    return r ? [r.startContainer, r.startOffset] : null;
  }
  return null;
}

const elementOf = (node) => (node?.nodeType === Node.TEXT_NODE ? node.parentElement : node);

/**
 * Zone principale d'une fiche avec son panneau latéral. `children(notes)` rend la fiche ; `stamp` change
 * à chaque rechargement de la fiche (l'historique est alors relu).
 */
export function EntryShell({
  kind, id, user, stamp, editing, commentId, mainClassName = '', resolveRef, onCommentDone, onRestored, children,
}) {
  const run = useAction();
  const toast = useToast();
  const confirm = useConfirm();
  const mainRef = useRef(null);
  const sections = useMemo(() => textSections(kind), [kind]);
  // Libellé traduit à l'appel (registre en français).
  const labelOf = useCallback((field) => {
    const label = sections.find((s) => s.key === field)?.label;
    return label ? t(label) : null;
  }, [sections]);

  const [sideTab, setSideTab] = useState(() => {
    const saved = storage.get(SIDE_KEY);
    return ['toc', 'comments', 'history'].includes(saved) ? saved : 'toc';
  });
  const [sideOpen, setSideOpen] = useState(false);
  const [threads, setThreads] = useState(null);
  const [versions, setVersions] = useState(null);
  const [draft, setDraft] = useState(null);           // { field, quote, label }
  const [activeComment, setActiveComment] = useState(null);
  const [versionView, setVersionView] = useState(null); // { version, content, current }
  const [anchors, setAnchors] = useState({});
  const [toc, setToc] = useState([]);
  const [activeToc, setActiveToc] = useState(null);
  const [fab, setFab] = useState(null);                // bouton « Commenter » près de la sélection

  const tocEls = useRef([]);
  const ranges = useRef(new Map());                    // fil -> Range du passage commenté
  const fieldEls = useRef(new Map());

  const keyRef = useRef(`${kind}:${id}`);
  keyRef.current = `${kind}:${id}`;

  const showTab = useCallback((tab) => {
    setSideTab(tab);
    storage.set(SIDE_KEY, tab);
  }, []);
  const openSide = useCallback((tab) => { if (tab) showTab(tab); setSideOpen(true); }, [showTab]);

  // --- Chargements -------------------------------------------------------------------------
  const loadComments = useCallback(async () => {
    const key = `${kind}:${id}`;
    try {
      const th = await api.entryComments(kind, id);
      if (keyRef.current === key) setThreads(th);
    } catch (err) {
      if (keyRef.current === key) toast(err.message, 'error');
    }
  }, [kind, id, toast]);

  const loadVersions = useCallback(async () => {
    const key = `${kind}:${id}`;
    try {
      const data = await api.entryVersions(kind, id);
      if (keyRef.current === key) setVersions(data.versions);
    } catch (err) {
      if (keyRef.current === key) toast(err.message, 'error');
    }
  }, [kind, id, toast]);

  useEffect(() => {
    setThreads(null);
    setVersions(null);
    setDraft(null);
    setActiveComment(null);
    setVersionView(null);
    loadComments();
  }, [kind, id, loadComments]);

  useEffect(() => {
    if (sideTab === 'history') loadVersions();
  }, [sideTab, stamp, loadVersions]);

  // Un autre MJ a pu commenter entre-temps : liste relue au retour sur l'onglet du navigateur.
  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === 'visible') loadComments(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [loadComments]);

  useEffect(() => {
    if (editing) { setVersionView(null); setDraft(null); setFab(null); }
  }, [editing]);

  // --- Lecture de la fiche affichée : sommaire, passages commentés, surlignage --------------------
  const scan = useCallback(() => {
    const root = mainRef.current;
    if (!root) return;
    // Sommaire : blocs de la fiche, puis titres du texte de chaque section.
    const items = [];
    const els = [];
    root.querySelectorAll('[data-entry-section]').forEach((sec) => {
      items.push({ id: `s:${sec.dataset.entrySection}`, label: sec.dataset.entryLabel || '', level: 1 });
      els.push(sec);
      sec.querySelectorAll('[data-entry-field] :is(h1, h2, h3)').forEach((h, i) => {
        const text = h.textContent.trim();
        if (!text) return;
        items.push({ id: `h:${sec.dataset.entrySection}:${i}`, label: text, level: h.tagName === 'H3' ? 3 : 2 });
        els.push(h);
      });
    });
    tocEls.current = els;
    setToc((cur) => (JSON.stringify(cur) === JSON.stringify(items) ? cur : items));
    // Formulaire ou aperçu d'une version : le texte de la fiche n'est pas affiché, les fils gardent leur
    // ancrage (sinon ils passeraient tous pour « extrait introuvable »).
    if (editing || versionView || els.length === 0) {
      ranges.current = new Map();
      clearHighlights();
      return;
    }

    // Passages commentés.
    const fields = new Map();
    root.querySelectorAll('[data-entry-field]').forEach((el) => {
      if (!fields.has(el.dataset.entryField)) fields.set(el.dataset.entryField, el);
    });
    fieldEls.current = fields;
    const order = [...fields.keys()];
    const indexes = new Map();
    const indexOf = (field) => {
      if (!indexes.has(field)) indexes.set(field, textIndex(fields.get(field)));
      return indexes.get(field);
    };
    const nextAnchors = {};
    const nextRanges = new Map();
    for (const th of threads || []) {
      if (!th.field) {
        // Toute la fiche (ou passage cherché dans toutes les sections, pour un fil posé par Claude).
        let found = null;
        if (th.quote) {
          for (const f of order) {
            const hit = findQuote(indexOf(f), th.quote);
            if (hit) { found = { ...hit, f }; break; }
          }
          if (found) {
            nextRanges.set(th.id, found.range);
            nextAnchors[th.id] = { pos: order.indexOf(found.f) * 1e6 + found.at, text: th.quote };
          }
        } else {
          nextAnchors[th.id] = { pos: -1, text: '' };
        }
        continue;
      }
      if (!fields.has(th.field)) continue;                       // section vidée : fil « détaché »
      const base = order.indexOf(th.field) * 1e6;
      if (!th.quote) { nextAnchors[th.id] = { pos: base, text: '' }; continue; }
      const hit = findQuote(indexOf(th.field), th.quote);
      if (!hit) continue;                                        // extrait modifié
      nextRanges.set(th.id, hit.range);
      nextAnchors[th.id] = { pos: base + hit.at, text: th.quote };
    }
    ranges.current = nextRanges;
    setAnchors((cur) => (JSON.stringify(cur) === JSON.stringify(nextAnchors) ? cur : nextAnchors));

    if (canHighlight()) {
      const open = [];
      let active = null;
      for (const th of threads || []) {
        const r = nextRanges.get(th.id);
        if (!r) continue;
        if (th.id === activeComment) active = r;
        else if (!th.resolved) open.push(r);
      }
      CSS.highlights.set(HL.open, new Highlight(...open));
      if (active) CSS.highlights.set(HL.active, new Highlight(active)); else CSS.highlights.delete(HL.active);
      const draftRange = draft?.quote && fields.has(draft.field) ? findQuote(indexOf(draft.field), draft.quote) : null;
      if (draftRange) CSS.highlights.set(HL.draft, new Highlight(draftRange.range)); else CSS.highlights.delete(HL.draft);
    }
  }, [threads, activeComment, draft, editing, versionView]);

  // Relecture à chaque changement de la fiche affichée (rendu React, texte riche, galerie…).
  useEffect(() => {
    const root = mainRef.current;
    if (!root) return undefined;
    let frame = 0;
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(scan); };
    schedule();
    const observer = new MutationObserver(schedule);
    observer.observe(root, { childList: true, subtree: true, characterData: true });
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [scan, versionView]);

  useEffect(() => clearHighlights, []);
  useEffect(() => { clearHighlights(); }, [kind, id]);

  // Section en cours (sommaire) et bouton « Commenter » qui suit la sélection.
  const placeFab = useCallback(() => {
    const root = mainRef.current;
    const sel = window.getSelection();
    if (!root || editing || versionView || !sel || sel.isCollapsed || !sel.rangeCount) { setFab(null); return; }
    const range = sel.getRangeAt(0);
    const field = elementOf(range.commonAncestorContainer)?.closest?.('[data-entry-field]');
    const quote = sel.toString().replace(/\s+/g, ' ').trim();
    if (!field || !root.contains(field) || !quote) { setFab(null); return; }
    const rect = range.getBoundingClientRect();
    const size = TOUCH ? 40 : 34;
    // Souris : dans la marge droite du texte, à hauteur de la sélection (comme les fiches rédigées) ;
    // tactile : sous la sélection, pour ne pas gêner le menu du système au-dessus.
    const margin = field.closest('section, .panel-section')?.getBoundingClientRect().right ?? rect.right;
    setFab({
      field: field.dataset.entryField,
      quote: quote.slice(0, MAX_QUOTE),
      top: TOUCH ? Math.min(rect.bottom + 10, window.innerHeight - size - 80) : Math.max(rect.top - 4, 60),
      left: TOUCH ? Math.min(Math.max(rect.left, 8), window.innerWidth - size - 8)
        : Math.min(margin + 12, window.innerWidth - size - 8),
    });
  }, [editing, versionView]);

  useEffect(() => {
    document.addEventListener('selectionchange', placeFab);
    return () => document.removeEventListener('selectionchange', placeFab);
  }, [placeFab]);

  useEffect(() => {
    const root = mainRef.current;
    if (!root) return undefined;
    const onScroll = () => {
      if (fab) placeFab();
      const top = root.getBoundingClientRect().top + 90;
      let current = null;
      tocEls.current.forEach((el, i) => { if (el.isConnected && el.getBoundingClientRect().top <= top) current = toc[i]?.id; });
      setActiveToc(current ?? toc[0]?.id ?? null);
    };
    onScroll();
    root.addEventListener('scroll', onScroll, { passive: true });
    return () => root.removeEventListener('scroll', onScroll);
  }, [toc, fab, placeFab]);

  // --- Commentaires ------------------------------------------------------------------------
  const reveal = useCallback((threadId) => {
    const root = mainRef.current;
    const range = ranges.current.get(threadId);
    const thread = threads?.find((th) => th.id === threadId);
    const target = range ? range.getBoundingClientRect() : fieldEls.current.get(thread?.field)?.getBoundingClientRect();
    if (!root || !target) return;
    const box = root.getBoundingClientRect();
    if (target.top < box.top + 70 || target.bottom > box.bottom - 40) {
      root.scrollBy({ top: target.top - box.top - box.height / 3, behavior: 'smooth' });
    }
  }, [threads]);

  const selectComment = useCallback((threadId) => {
    setActiveComment(threadId);
    reveal(threadId);
  }, [reveal]);

  const startComment = useCallback((field, quote = '') => {
    if (editing) return;
    setDraft({ field: field || '', quote, label: field ? labelOf(field) : t('Toute la fiche') });
    setActiveComment(null);
    setFab(null);
    window.getSelection()?.removeAllRanges();
    openSide('comments');
  }, [editing, labelOf, openSide]);

  const submitDraft = async (body) => {
    const created = await run(() => api.createEntryComment(kind, id, { body, field: draft?.field || '', quote: draft?.quote || '' }));
    if (!created) return false;
    setDraft(null);
    setActiveComment(created.id);
    await loadComments();
    return true;
  };

  const replyComment = async (threadId, body) => {
    const created = await run(() => api.createEntryComment(kind, id, { body, parent_id: threadId }));
    if (!created) return false;
    await loadComments();
    return true;
  };

  const resolveComment = async (thread, resolved) => {
    const done = await run(() => api.updateEntryComment(thread.id, { resolved }), resolved ? t('Fil résolu') : t('Fil rouvert'));
    if (!done) return;
    if (resolved && activeComment === thread.id) setActiveComment(null);
    loadComments();
  };

  const editComment = async (commentId, body) => {
    const done = await run(() => api.updateEntryComment(commentId, { body }));
    if (!done) return false;
    await loadComments();
    return true;
  };

  const deleteComment = async (comment) => {
    const isThread = comment.parent_id === null;
    const yes = await confirm({
      title: isThread ? t('Supprimer le fil') : t('Supprimer la réponse'),
      message: isThread ? t('Le commentaire et toutes ses réponses seront supprimés. Le texte de la fiche n’est pas modifié.')
        : t('Cette réponse sera supprimée.'),
      confirmLabel: t('Supprimer'),
    });
    if (!yes) return;
    if (await run(() => api.deleteEntryComment(comment.id)) === undefined) return;
    if (isThread && activeComment === comment.id) setActiveComment(null);
    loadComments();
  };

  // Clic sur un passage surligné : son fil.
  useEffect(() => {
    const root = mainRef.current;
    if (!root) return undefined;
    const onClick = (e) => {
      if (e.target.closest('a, button, input, textarea, select') || !window.getSelection()?.isCollapsed) return;
      const point = pointAt(e.clientX, e.clientY);
      if (!point) return;
      for (const [threadId, range] of ranges.current) {
        let inside = false;
        try { inside = range.isPointInRange(point[0], point[1]); } catch { /* nœud hors du document */ }
        const thread = threads?.find((th) => th.id === threadId);
        if (inside && thread && !thread.resolved) {
          setActiveComment(threadId);
          openSide('comments');
          return;
        }
      }
    };
    root.addEventListener('click', onClick);
    return () => root.removeEventListener('click', onClick);
  }, [threads, openSide]);

  // Arrivée depuis la recherche globale (?comment=<id>).
  useEffect(() => {
    if (!commentId || !threads) return undefined;
    const timer = setTimeout(() => {
      const thread = threads.find((x) => x.id === commentId || x.replies.some((r) => r.id === commentId));
      if (thread) {
        openSide('comments');
        selectComment(thread.id);
      } else {
        toast(t('Ce commentaire n’existe plus.'), 'info');
      }
      onCommentDone?.();
    }, 150);
    return () => clearTimeout(timer);
  }, [commentId, threads]); // eslint-disable-line react-hooks/exhaustive-deps

  // --- Historique --------------------------------------------------------------------------
  const openVersion = async (v) => {
    if (editing) {
      toast(t('Enregistrez ou annulez d’abord la modification en cours.'), 'info');
      return;
    }
    setVersionView({ version: v, content: null, current: null });
    setSideOpen(false);
    const [full, current] = await Promise.all([run(() => api.entryVersion(kind, id, v.id)), run(() => api.getEntity(kind, id))]);
    if (full && current) {
      setVersionView((cur) => (cur?.version.id === v.id ? { version: v, content: full.content, current } : cur));
    } else {
      setVersionView(null);
    }
  };

  const createVersion = async (label) => {
    const created = await run(() => api.createEntryVersion(kind, id, { label }), t('Version enregistrée'));
    if (!created) return false;
    loadVersions();
    return true;
  };

  const renameVersion = async (vid, label) => {
    const done = await run(() => api.updateEntryVersion(kind, id, vid, { label }));
    if (!done) return false;
    setVersionView((cur) => (cur?.version.id === vid ? { ...cur, version: { ...cur.version, ...done } } : cur));
    loadVersions();
    return true;
  };

  const deleteVersion = async (v) => {
    const yes = await confirm({
      title: t('Supprimer cette version'),
      message: t('La version du {date} sera supprimée de l’historique. Le contenu actuel de la fiche ne change pas.',
        { date: versionDate(v) }),
      confirmLabel: t('Supprimer'),
    });
    if (!yes) return;
    if (await run(() => api.deleteEntryVersion(kind, id, v.id)) === undefined) return;
    if (versionView?.version.id === v.id) setVersionView(null);
    loadVersions();
  };

  const restoreVersion = async () => {
    const v = versionView?.version;
    if (!v) return;
    const yes = await confirm({
      title: t('Restaurer cette version'),
      message: t('Le texte de la fiche (nom, résumé, sections, étiquettes) reprendra la version du {date}. Les images, liens, appartenances et lieux ne changent pas. L’état actuel reste dans l’historique.',
        { date: versionDate(v) }),
      confirmLabel: t('Restaurer'),
    });
    if (!yes) return;
    const done = await run(() => api.restoreEntryVersion(kind, id, v.id), t('Version restaurée'));
    if (!done) return;
    setVersionView(null);
    await onRestored?.();
    loadVersions();
  };

  // --- Rendu -------------------------------------------------------------------------------
  const openThreads = (threads || []).filter((th) => !th.resolved);
  const notes = useMemo(() => ({
    openCount: openThreads.length,
    countFor: (field) => openThreads.filter((th) => th.field === field).length,
    firstFor: (field) => openThreads.find((th) => th.field === field)?.id ?? null,
    startComment,
    showThread: (threadId) => { openSide('comments'); selectComment(threadId); },
    openSide,
  }), [threads, startComment, openSide, selectComment]); // eslint-disable-line react-hooks/exhaustive-deps

  const goToc = (i) => {
    const el = tocEls.current[i];
    const root = mainRef.current;
    if (!el?.isConnected || !root) return;
    setSideOpen(false);
    const top = el.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop - 16;
    root.scrollTo({ top, behavior: 'smooth' });
  };

  return (
    <NotesContext.Provider value={notes}>
      <main className={`doc-main ${mainClassName}`} ref={mainRef}>
        {versionView ? (
          <EntryVersionView kind={kind} view={versionView} resolveRef={resolveRef}
            onRestore={restoreVersion} onClose={() => setVersionView(null)} />
        ) : children}
      </main>
      {fab && (
        <button type="button" className="comment-fab entry-fab" style={{ top: fab.top, left: fab.left }}
          title={t('Commenter la sélection')} aria-label={t('Commenter la sélection')}
          onMouseDown={(e) => e.preventDefault()} onClick={() => startComment(fab.field, fab.quote)}>
          <Icon name="comment" size={17} />
        </button>
      )}
      <aside className={`doc-side entry-side${sideOpen ? ' is-open' : ''}`} aria-label={t('Panneau latéral de la fiche')}>
        <div className="doc-side__tabs" role="tablist">
          <button type="button" role="tab" aria-selected={sideTab === 'toc'} className={sideTab === 'toc' ? 'is-active' : ''}
            onClick={() => showTab('toc')}>
            {t('Sommaire')}
          </button>
          <button type="button" role="tab" aria-selected={sideTab === 'comments'} className={sideTab === 'comments' ? 'is-active' : ''}
            onClick={() => showTab('comments')}>
            {t('Commentaires')}{openThreads.length > 0 && <span className="doc-side__badge">{openThreads.length}</span>}
          </button>
          <button type="button" role="tab" aria-selected={sideTab === 'history'} className={sideTab === 'history' ? 'is-active' : ''}
            onClick={() => showTab('history')}>
            {t('Historique')}
          </button>
          <button type="button" className="icon-btn icon-btn--small doc-side__close" aria-label={t('Fermer le panneau')}
            onClick={() => setSideOpen(false)}>
            <Icon name="close" size={15} />
          </button>
        </div>
        {sideTab === 'toc' ? (
          <nav className="doc-toc" aria-label={t('Sommaire de la fiche')}>
            {versionView ? (
              <p className="muted doc-toc__empty">{t('Sommaire de la version actuelle : revenez-y pour l’utiliser.')}</p>
            ) : toc.length === 0 ? (
              <p className="muted doc-toc__empty">{editing ? t('Le sommaire revient après l’enregistrement.') : t('Les sections de la fiche apparaîtront ici.')}</p>
            ) : (
              <ul>
                {toc.map((item, i) => (
                  <li key={item.id} className={`doc-toc__l${item.level}${item.id === activeToc ? ' is-active' : ''}`}>
                    <a href={`#${item.id}`} onClick={(e) => { e.preventDefault(); goToc(i); }}>{item.label}</a>
                  </li>
                ))}
              </ul>
            )}
          </nav>
        ) : sideTab === 'history' ? (
          <HistoryPanel
            versions={versions}
            activeId={versionView?.version.id ?? null}
            readOnly={editing}
            onSelect={openVersion}
            onCurrent={() => setVersionView(null)}
            onCreate={createVersion}
            onRename={renameVersion}
            onDelete={deleteVersion}
            currentMeta={editing ? t('modification en cours') : t('texte enregistré')}
            notice={editing ? t('Enregistrez ou annulez la modification en cours pour consulter ou restaurer une version.') : null}
            emptyText={t('Aucune version pour l’instant. Maze en garde une à chaque enregistrement de la fiche (les modifications rapprochées d’un même MJ sont regroupées) ; vous pouvez aussi nommer l’état actuel.')}
            note={t('Seul le texte de la fiche est versionné (pas les images, liens ni lieux). Au-delà de 50, les versions automatiques les plus anciennes sont effacées ; les versions nommées sont conservées.')}
          />
        ) : (
          <CommentsPanel
            threads={threads}
            anchors={anchors}
            draft={draft}
            activeId={activeComment}
            user={user}
            readOnly={false}
            fieldLabel={(th) => (th.field ? labelOf(th.field) : null)}
            orphan={{ withQuote: t('extrait introuvable dans le texte actuel'), empty: t('Cette section a été vidée') }}
            emptyText={editing ? t('Aucun commentaire.') : (TOUCH
              ? t('Aucun commentaire. Sélectionnez un passage de la fiche puis touchez la bulle, ou commentez une section.')
              : t('Aucun commentaire. Sélectionnez un passage de la fiche puis cliquez sur la bulle, ou utilisez celle d’une section.'))}
            onNewThread={editing ? undefined : () => startComment('')}
            onSubmitDraft={submitDraft}
            onCancelDraft={() => setDraft(null)}
            onSelect={selectComment}
            onReply={replyComment}
            onResolve={resolveComment}
            onEdit={editComment}
            onDelete={deleteComment}
          />
        )}
      </aside>
    </NotesContext.Provider>
  );
}

/** Bouton de section : nombre de fils ouverts (affiche le premier), sinon « Commenter cette section ».
 * `label` : libellé du registre en français (traduit ici). */
export function SectionComment({ field, label }) {
  const notes = useEntryNotes();
  if (!notes) return null;
  const count = notes.countFor(field);
  const shown = t(label);
  const title = count ? tn('{n} commentaire sur « {label} »', '{n} commentaires sur « {label} »', count, { label: shown })
    : t('Commenter « {label} »', { label: shown });
  return (
    <button type="button" className={`icon-btn icon-btn--small entry-sec-comment${count ? ' has-count' : ''}`}
      title={title} aria-label={title}
      onClick={() => (count ? notes.showThread(notes.firstFor(field)) : notes.startComment(field))}>
      <Icon name="comment" size={14} />{count > 0 && <span>{count}</span>}
    </button>
  );
}

/** Bouton d'ouverture du panneau sur écran moyen ou téléphone (le panneau est fixe sur grand écran). */
export function EntrySideButton() {
  const notes = useEntryNotes();
  if (!notes) return null;
  return (
    <button type="button" className="btn btn--small btn--ghost doc__comments-btn" onClick={() => notes.openSide()}
      title={t('Sommaire, commentaires et historique de la fiche')}
      aria-label={notes.openCount
        ? tn('Sommaire, commentaires et historique ({n} fil ouvert)', 'Sommaire, commentaires et historique ({n} fils ouverts)', notes.openCount)
        : t('Sommaire, commentaires et historique')}>
      <Icon name="comment" size={15} />
      <span className="btn__label">{t('Panneau')}</span>
      {notes.openCount > 0 && <span className="doc-side__badge">{notes.openCount}</span>}
    </button>
  );
}

// --- Aperçu d'une version --------------------------------------------------------------------

const DIFF = {
  changed: N_('Modifié depuis'),
  added: N_('Ajouté depuis'),
  removed: N_('Vidé depuis'),
};

function diffState(before, after) {
  const a = (before ?? '').toString().trim();
  const b = (after ?? '').toString().trim();
  if (a === b) return null;
  if (!a) return 'added';
  if (!b) return 'removed';
  return 'changed';
}

function Badge({ state }) {
  return state ? <span className={`entry-diff entry-diff--${state}`}>{t(DIFF[state])}</span> : null;
}

// Champs d'en-tête de l'aperçu d'une version : registre (historyIdentity), présentation propre à l'aperçu.
const VERSION_LABEL = { place: { kind: N_('Type de lieu') } };

function identityRows(kind, kinds) {
  const format = {
    status: (v) => NPC_STATUS[v]?.label || v,
    place_kind: (v) => kinds?.find((k) => k.key === v)?.label || v,
  };
  return historyIdentity(kind).map((f) => [f.key, VERSION_LABEL[kind]?.[f.key] || f.label, format[f.type] || ((v) => v)]);
}

function EntryVersionView({ kind, view, resolveRef, onRestore, onClose }) {
  const kinds = useKinds();
  const { version, content, current } = view;
  const sections = textSections(kind);
  const ids = identityRows(kind, kinds);
  const tags = (list) => (list || []).join(', ');
  return (
    <article className="doc entry-version">
      <div className="version-view__bar" role="status">
        <Icon name="history" size={17} />
        <span className="version-view__text">
          <strong>{versionTitle(version)}</strong>
          <span className="muted"> · {versionDate(version)}{version.author_name ? ` · ${version.author_name}` : ''}</span>
        </span>
        <button type="button" className="btn btn--small btn--primary" onClick={onRestore} disabled={content == null || version.is_current}>
          {t('Restaurer cette version')}
        </button>
        <button type="button" className="btn btn--small btn--ghost" onClick={onClose}>{t('Revenir à la version actuelle')}</button>
      </div>
      {content == null ? <div className="doc-connecting" aria-busy="true">{t('Chargement de la version…')}</div> : (
        <>
          <p className="entry-version__legend">
            {version.is_current ? t('Cette version est identique au texte actuel de la fiche.')
              : t('Les sections marquées ont changé entre cette version et le texte actuel de la fiche.')}
          </p>
          <header className="entry-version__head">
            <h1 className="doc__title">{content.name}</h1>
            <Badge state={diffState(content.name, current.name)} />
          </header>
          <dl className="entry-version__ids">
            {ids.map(([key, label, fmt]) => {
              const state = diffState(content[key], current[key]);
              if (!content[key] && !state) return null;
              return (
                <div key={key} className={state ? 'is-changed' : ''}>
                  <dt>{t(label)}</dt>
                  <dd>
                    {key === 'color' && content[key] && <span className="entry-version__swatch" style={{ background: content[key] }} />}
                    {content[key] ? fmt(content[key]) : <span className="muted">{t('vide')}</span>}
                    <Badge state={state} />
                  </dd>
                </div>
              );
            })}
          </dl>
          {sections.map((s) => {
            const state = diffState(content[s.key], current[s.key]);
            if (!content[s.key]?.trim() && !state) return null;
            return (
              <section key={s.key} className={`panel-section entry-version__sec${s.secret ? ' panel-section--secret' : ''}${state ? ` is-${state}` : ''}`}>
                <div className="panel-section__head">
                  <h3>{s.secret && <Icon name="lock" size={14} />} {t(s.label)}</h3>
                  <Badge state={state} />
                </div>
                {content[s.key]?.trim()
                  ? (s.key === 'summary' ? <p className="sheet-summary">{content[s.key]}</p>
                    : <Markdown text={content[s.key]} resolve={resolveRef} />)
                  : <p className="muted">{t('Vide dans cette version.')}</p>}
              </section>
            );
          })}
          {(content.tags?.length > 0 || diffState(tags(content.tags), tags(current.tags))) && (
            <section className={`panel-section entry-version__sec${diffState(tags(content.tags), tags(current.tags)) ? ' is-changed' : ''}`}>
              <div className="panel-section__head">
                <h3>{t('Étiquettes')}</h3>
                <Badge state={diffState(tags(content.tags), tags(current.tags))} />
              </div>
              {content.tags?.length > 0
                ? <div className="tags">{content.tags.map((tag) => <span key={tag} className="tag">{tag}</span>)}</div>
                : <p className="muted">{t('Aucune dans cette version.')}</p>}
            </section>
          )}
        </>
      )}
    </article>
  );
}

