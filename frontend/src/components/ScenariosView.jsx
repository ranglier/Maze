import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import Editor from '../editor/Editor.jsx';
import ImagePicker from './ImagePicker.jsx';
import { NewDocDialog, SaveTemplateDialog } from './DocDialogs.jsx';
import { VersionPreview } from './HistoryPanel.jsx';
import { LiveDocument } from '../editor/live.js';
import { storage } from '../util.js';
import { fmtDate } from './AdminPage.jsx';
import { useAction, useConfirm, useToast } from './ui.jsx';
import DocTree from './scenarios/DocTree.jsx';
import DocBar from './scenarios/DocBar.jsx';
import DocSide from './scenarios/DocSide.jsx';
import { DocChildren, DocLinks } from './scenarios/DocChildren.jsx';
import { MentionPreview, PopupMenu } from './scenarios/popups.jsx';
import { saveLabel } from './scenarios/SaveState.jsx';
import { DOC_STATUS, childrenOf } from './scenarios/docs.js';
import useScenarioData from './scenarios/useScenarioData.js';
import useDocMeta from './scenarios/useDocMeta.js';
import useSidePanel from './scenarios/useSidePanel.js';
import useDocVersions from './scenarios/useDocVersions.js';
import useDocComments from './scenarios/useDocComments.js';

export { DOC_STATUS };

/*
 * Onglet Scénarios : arbre des fiches rédigées (scenarios/DocTree.jsx), fiche ouverte (titre et statut :
 * useDocMeta ; texte : éditeur temps réel, LiveDocument), panneau latéral (scenarios/DocSide.jsx : sommaire,
 * commentaires avec useDocComments, historique avec useDocVersions). Données de la campagne : useScenarioData.
 */

export default function ScenariosView({ campaign, user, header, trailing, docId, find, commentId, onNavigate, onTargetDone }) {
  const cid = campaign.id;
  const [doc, setDoc] = useState(null);
  const [live, setLive] = useState(null);          // LiveDocument de la fiche ouverte
  const [liveState, setLiveState] = useState(null);
  const [headings, setHeadings] = useState([]);
  const [activeHeading, setActiveHeading] = useState(null);
  const [readMode, setReadMode] = useState(() => storage.get('maze:docs:read') === '1');
  const [preview, setPreview] = useState(null);
  const [imagePick, setImagePick] = useState(null);   // { resolve } pendant le choix d'une image
  const [reloadKey, setReloadKey] = useState(0);
  const [newDoc, setNewDoc] = useState(null);        // création : { parentId, isScenario }
  const [saveTemplate, setSaveTemplate] = useState(false);
  const [docMenu, setDocMenu] = useState(null);      // menu ⋯ de la fiche : { rect }
  const closeDocMenu = useCallback(() => setDocMenu(null), []);
  const restoringRef = useRef(false);
  const editorRef = useRef(null);
  const scrollRef = useRef(null);
  const titleRef = useRef(null);
  // Hauteur du titre ajustée à son contenu (passage à la ligne), aussi au redimensionnement de la page.
  const titleText = doc?.title;
  useLayoutEffect(() => {
    const el = titleRef.current;
    if (!el) return undefined;
    const fit = () => { el.style.height = 'auto'; el.style.height = `${el.scrollHeight}px`; };
    fit();
    let width = el.parentElement.clientWidth;
    const ro = new ResizeObserver(() => {
      if (el.parentElement.clientWidth !== width) { width = el.parentElement.clientWidth; fit(); }
    });
    ro.observe(el.parentElement);
    return () => ro.disconnect();
  }, [titleText, doc?.id]);
  const run = useAction();
  const toast = useToast();
  const confirm = useConfirm();

  // --- Données --------------------------------------------------------------------
  const { tree, graph, places, loadTree, loadGraph, loadPlaces, entities, patchTreeDoc } = useScenarioData({
    cid, campaignName: campaign.name, run });

  const currentId = docId || tree?.root_id || null;
  // Scénario en cours (accueil des lieux : lieux cités dans le dernier scénario ouvert).
  useEffect(() => {
    if (!tree || !docId) return;
    const byId = new Map(tree.documents.map((d) => [d.id, d]));
    for (let d = byId.get(docId), n = 0; d && n < 50; d = byId.get(d.parent_id), n += 1) {
      if (d.kind === 'scenario') { storage.set(`maze:scenario:${cid}`, String(d.id)); break; }
    }
  }, [tree, docId, cid]);
  // Fiche ouverte absente de l'arbre (créée dans un autre onglet ou par un autre MJ) : arbre rechargé, une fois.
  const treeRetry = useRef(null);
  useEffect(() => {
    if (!tree || !docId || tree.documents.some((d) => d.id === docId) || treeRetry.current === docId) return;
    treeRetry.current = docId;
    loadTree();
  }, [tree, docId, loadTree]);

  // --- Titre et statut : enregistrés par l'API, diffusés aux autres MJ ----------------------
  const liveRef = useRef(null);
  const deletingRef = useRef(false);  // fiche ouverte en cours de suppression par cet onglet
  const { metaState, setMetaState, pendingMeta, saveMeta, queueMeta, flushMeta } = useDocMeta({
    doc, setDoc, patchTreeDoc, toast, liveRef });

  // --- Panneau latéral, historique des versions et commentaires -----------------------------------
  const side = useSidePanel();
  const { sideTab, setSideOpen, showToc, showHistory, showComments } = side;
  const versioning = useDocVersions({ doc, sideTab, reloadKey, run, toast, confirm, editorRef, restoringRef, setSideOpen });
  const { versionView, setVersionView, setVersions } = versioning;
  const notes = useDocComments({ doc, live, liveState, run, toast, confirm, editorRef, showComments });
  const { setThreads, setAnchors, setDraft, setActiveComment, openThreads } = notes;

  // --- Chargement de la fiche courante et connexion temps réel ---------------------------------
  useEffect(() => {
    deletingRef.current = false;
    if (!currentId) return undefined;
    let cancelled = false;
    let instance = null;
    flushMeta();
    setDoc(null);
    setLive(null);
    setLiveState(null);
    setHeadings([]);
    setPreview(null);
    setThreads(null);
    setAnchors({});
    setDraft(null);
    setActiveComment(null);
    setVersionView(null);
    setVersions(null);
    const loadComments = () => api.comments(currentId).then((t) => { if (!cancelled) setThreads(t); }).catch(() => {});
    loadComments();
    api.getDocument(currentId).then((d) => {
      if (cancelled) return;
      setMetaState('saved');
      setDoc(d);
      scrollRef.current?.scrollTo(0, 0);
      instance = new LiveDocument({
        docId: d.id,
        html: d.content,
        epoch: d.live_epoch,
        user: { name: user.display_name || user.username },
        meta: { campaignId: cid, created: d.created_at,
          title: d.title || (d.kind === 'campaign' ? 'Fiche de campagne' : 'Sans titre') },
        onChange: (st) => { if (!cancelled) setLiveState(st); },
      });
      instance.onMeta = (m) => {
        if (cancelled) return;
        setDoc((cur) => {
          if (!cur || cur.id !== d.id) return cur;
          const next = { ...cur, updated_at: m.updated_at || cur.updated_at };
          // Titre et statut changés par un autre MJ (sauf pendant qu'on tape soi-même le titre).
          if (m.title !== undefined && document.activeElement !== titleRef.current && !pendingMeta.current?.title) next.title = m.title;
          if (m.status !== undefined && !pendingMeta.current?.status) next.status = m.status;
          return next;
        });
        const changes = { updated_at: m.updated_at };
        if (m.title !== undefined) changes.title = m.title;
        if (m.status !== undefined) changes.status = m.status;
        patchTreeDoc(d.id, changes);
      };
      instance.onComments = loadComments;  // un autre MJ a commenté, répondu ou résolu
      liveRef.current = instance;
      setLive(instance);
    }).catch((err) => {
      if (cancelled) return;
      toast(err.message, 'error');
      if (err.status === 404 && docId) onNavigate(`#/c/${cid}/scenarios`);
    });
    return () => {
      cancelled = true;
      instance?.destroy();
      if (liveRef.current === instance) liveRef.current = null;
    };
  }, [currentId, reloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fin de connexion imposée par le serveur.
  const liveError = liveState?.error;
  useEffect(() => {
    if (!liveError || !doc) return;
    if (liveError === 'reset') {
      if (restoringRef.current) {
        restoringRef.current = false;
        toast('Version restaurée.', 'success');
      } else {
        toast('La fiche a été remplacée : rechargement.', 'info');
      }
      setReloadKey((k) => k + 1);
    } else if (liveError === 'deleted') {
      if (deletingRef.current) return;  // suppression faite ici : deleteDoc s'occupe de la suite
      toast('Cette fiche a été supprimée.', 'error');
      loadTree().then(() => onNavigate(`#/c/${cid}/scenarios`));
    } else if (liveError === 'forbidden') {
      toast('Vous n’avez plus accès à cette campagne.', 'error');
      onNavigate('#/');
    } else if (liveError === 'auth') {
      window.location.reload();
    } else if (liveError === 'toolarge') {
      toast('Modification trop volumineuse pour être enregistrée (collage de plus de 2 Mo) : la fiche est rechargée, votre texte est gardé dans son historique. Collez-le en plusieurs fois.', 'error');
      setReloadKey((k) => k + 1);
    }
    // 'version' : Maze a été mis à jour ; session.js garde le texte non envoyé puis recharge la page.
  }, [liveError]); // eslint-disable-line react-hooks/exhaustive-deps

  const go = (id) => {
    flushMeta();
    onNavigate(id === tree?.root_id ? `#/c/${cid}/scenarios` : `#/c/${cid}/scenarios/${id}`);
  };

  // --- Actions sur l'arbre -----------------------------------------------------------
  const createDoc = (parentId) => {
    setNewDoc({ parentId, isScenario: parentId === tree?.root_id });
  };

  const confirmCreate = async ({ title, content }) => {
    flushMeta();
    const created = await run(() => api.createDocument(cid, { parent_id: newDoc.parentId, title, content }));
    if (!created) return false;
    setNewDoc(null);
    await loadTree();
    onNavigate(`#/c/${cid}/scenarios/${created.id}`);
    if (!title) setTimeout(() => { titleRef.current?.focus(); titleRef.current?.select(); }, 400);
    return true;
  };

  const duplicateDoc = async (item) => {
    flushMeta();
    const created = await run(() => api.duplicateDocument(item.id), 'Fiche dupliquée');
    if (!created) return;
    await loadTree();
    onNavigate(`#/c/${cid}/scenarios/${created.id}`);
  };

  const confirmSaveTemplate = async ({ name, description }) => {
    const content = editorRef.current?.getTemplateHtml();
    if (content == null) {
      toast('La fiche n’est pas encore prête, réessayez dans un instant.', 'info');
      return false;
    }
    const created = await run(() => api.createTemplate({ name, description, content }));
    if (!created) return false;
    toast(`Modèle « ${name} » enregistré : il est proposé à la création des fiches${created.shared
      ? ', et visible par tous les MJ de l’instance' : ''}.`, 'success');
    setSaveTemplate(false);
    return true;
  };

  const moveDoc = async (item, dir) => {
    const siblings = childrenOf(tree.documents, item.parent_id).map((d) => d.id);
    const i = siblings.indexOf(item.id);
    const j = i + dir;
    if (j < 0 || j >= siblings.length) return;
    [siblings[i], siblings[j]] = [siblings[j], siblings[i]];
    await run(() => api.reorderDocuments(cid, item.parent_id, siblings));
    loadTree();
  };

  // Glisser-déposer dans l'arbre : rang parmi les sœurs ou nouvelle fiche parente. Refus (trop de niveaux de
  // sous-pages, 400 ; fiche changée par un autre) : message du serveur, arbre rechargé tel qu'il est sur le serveur.
  const moveDocTo = async (id, parentId, index) => {
    const done = await run(() => api.moveDocument(id, parentId, index));
    loadTree();
    return done !== undefined;
  };

  const deleteDoc = async (item) => {
    const subCount = tree.documents.filter((d) => d.parent_id === item.id).length;
    const yes = await confirm({
      title: `Supprimer « ${item.title || 'Sans titre'} »`,
      message: subCount
        ? `La fiche et ses ${subCount} sous-page(s) seront définitivement supprimées, images comprises.`
        : 'La fiche sera définitivement supprimée, images comprises.',
      confirmLabel: 'Supprimer',
    });
    if (!yes) return;
    if (pendingMeta.current?.id === item.id) pendingMeta.current = null;
    const wasOpen = item.id === currentId || isDescendant(item.id, currentId);
    deletingRef.current = wasOpen;
    const done = await run(() => api.deleteDocument(item.id), 'Fiche supprimée');
    if (done === undefined) {
      deletingRef.current = false;
      return;
    }
    await loadTree();
    if (wasOpen) go(item.parent_id);
  };

  const isDescendant = (ancestorId, id) => {
    let d = tree?.documents.find((x) => x.id === id);
    while (d && d.parent_id) {
      if (d.parent_id === ancestorId) return true;
      d = tree.documents.find((x) => x.id === d.parent_id);
    }
    return false;
  };

  const createEntity = async (kind, name) => {
    const payload = kind === 'faction' ? { name, color: '#8e5aa8' } : { name };
    const created = await run(() => api.createEntity(kind, cid, payload),
      { npc: `PNJ « ${name} » créé`, faction: `Faction « ${name} » créée`, place: `Lieu « ${name} » créé` }[kind]);
    if (!created) return null;
    await (kind === 'place' ? loadPlaces() : loadGraph());
    return { kind, id: created.id, name: created.name };
  };

  // Arrivée depuis la recherche globale : passage trouvé ou fil de commentaires.
  const editorReady = Boolean(live && liveState?.synced && doc && doc.id === currentId);
  useEffect(() => {
    if (!editorReady || (!find && !commentId)) return undefined;
    const t = setTimeout(() => {
      if (find) {
        if (!editorRef.current?.revealText(find)) toast('Passage introuvable dans le texte (il est peut-être dans le titre).', 'info');
      } else if (commentId) {
        setActiveComment(commentId);
        showComments();
        editorRef.current?.revealComment(commentId);
      }
      onTargetDone?.();
    }, 350);
    return () => clearTimeout(t);
  }, [editorReady, find, commentId]); // eslint-disable-line react-hooks/exhaustive-deps

  // --- Table des matières : section en cours de lecture --------------------------------
  useEffect(() => {
    const box = scrollRef.current;
    if (!box) return undefined;
    const onScroll = () => {
      const top = box.getBoundingClientRect().top + 110;
      let current = headings[0]?.id || null;
      for (const h of headings) {
        const el = document.getElementById(h.id);
        if (el && el.getBoundingClientRect().top <= top) current = h.id;
      }
      setActiveHeading(current);
      setPreview(null);
    };
    onScroll();
    box.addEventListener('scroll', onScroll, { passive: true });
    return () => box.removeEventListener('scroll', onScroll);
  }, [headings]);

  const toggleRead = () => {
    setReadMode((r) => {
      storage.set('maze:docs:read', r ? '0' : '1');
      return !r;
    });
  };


  // --- Rendu ---------------------------------------------------------------------------
  const docs = tree?.documents || [];
  const isRoot = doc && doc.kind === 'campaign';
  const breadcrumb = [];
  if (doc) {
    let d = docs.find((x) => x.id === doc.id);
    while (d) {
      breadcrumb.unshift(d);
      d = docs.find((x) => x.id === d.parent_id);
    }
  }
  const children = doc ? childrenOf(docs, doc.id) : [];
  const status = saveLabel(liveState, metaState);
  // Autres MJ sur la fiche (un même compte peut avoir plusieurs onglets ouverts).
  const others = [];
  for (const p of liveState?.peers || []) {
    if (p.id === liveState.you || others.some((o) => o.user_id === p.user_id)) continue;
    if (p.user_id === user.id && (liveState.peers || []).filter((x) => x.user_id === user.id).length > 1) {
      others.push({ ...p, name: `${p.name} (autre onglet)` });
    } else if (p.user_id !== user.id) {
      others.push(p);
    }
  }

  return (
    <div className="scenarios">
      <header className="topbar">
        {header}
        <div className="topbar__right">
        <div className="toolbar" />
        {trailing && <div className="topbar__end">{trailing}</div>}
        </div>
      </header>
      <div className="scenarios__body">
        <DocTree
          tree={tree}
          campaignName={campaign.name}
          currentId={currentId}
          onOpen={go}
          onCreate={createDoc}
          onDuplicate={duplicateDoc}
          onMove={moveDoc}
          onMoveTo={moveDocTo}
          onDelete={deleteDoc}
        />

        <main className="doc-main" ref={scrollRef}>
          {!doc ? <div className="doc-loading" aria-busy="true">Chargement…</div> : (
            <article className="doc" key={doc.id}>
              <DocBar campaignId={cid} campaignName={campaign.name} breadcrumb={breadcrumb} others={others}
                openThreads={openThreads} menuOpen={Boolean(docMenu)}
                onMenu={(rect) => setDocMenu(docMenu ? null : { rect })}
                status={status} onRetry={metaState === 'error' ? saveMeta : null}
                readMode={readMode} onToggleRead={toggleRead} onGo={go} onComments={showComments} />

              <header className="doc__head">
                {isRoot ? (
                  <h1 className="doc__title">{campaign.name}</h1>
                ) : (
                  // Zone de texte d'une ligne qui s'agrandit : un titre long passe à la ligne au lieu d'être coupé.
                  <textarea
                    ref={titleRef}
                    className="doc__title"
                    rows={1}
                    value={doc.title}
                    placeholder="Titre"
                    readOnly={readMode}
                    maxLength={200}
                    aria-label="Titre de la fiche"
                    onChange={(e) => {
                      const title = e.target.value.replace(/[\r\n]+/g, ' ');
                      setDoc((d) => ({ ...d, title }));
                      queueMeta({ title });
                    }}
                    onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); editorRef.current?.focus(); } }}
                  />
                )}
                <div className="doc__meta">
                  {!isRoot && (
                    <div className="status-pick" role="radiogroup" aria-label="Statut">
                      {Object.entries(DOC_STATUS).map(([k, v]) => (
                        <button key={k} type="button" role="radio" aria-checked={doc.status === k} disabled={readMode}
                          className={`status-pick__opt status--${k}${doc.status === k ? ' is-active' : ''}`}
                          onClick={() => { setDoc((d) => ({ ...d, status: k })); queueMeta({ status: k }, true); }}>
                          <span className="status-dot" />{v.label}
                        </button>
                      ))}
                    </div>
                  )}
                  <span className="muted">Modifiée le {fmtDate(doc.updated_at)}</span>
                </div>
              </header>

              {versionView && (
                <VersionPreview version={versionView.version} content={versionView.content} readOnly={readMode}
                  onRestore={versioning.restoreVersion} onClose={() => setVersionView(null)} />
              )}
              {live && liveState?.synced ? (
                <div hidden={Boolean(versionView)}>
                <Editor
                  ref={editorRef}
                  live={live}
                  readOnly={readMode}
                  entities={entities}
                  entitiesLoaded={Boolean(graph && places && tree)}
                  placeholder={isRoot
                    ? 'Présentez la campagne : univers, ton, enjeux, fils rouges… Tapez @ pour citer un PNJ, une faction, un lieu ou une autre fiche.'
                    : 'Écrivez le scénario. Tapez @ pour citer un PNJ, une faction, un lieu ou une autre fiche, / pour insérer un bloc.'}
                  onHeadings={setHeadings}
                  onMentionClick={(target, rect) => {
                    // Renvoi vers une autre fiche : on l'ouvre directement.
                    if (target.kind === 'doc') {
                      if (tree?.documents.some((d) => d.id === target.id)) { flushMeta(); go(target.id); }
                      else toast('Cette fiche a été supprimée.', 'info');
                      return;
                    }
                    setPreview({ ...target, rect });
                  }}
                  onCreateEntity={createEntity}
                  onUploadImage={(file) => run(() => api.uploadDocumentImage(doc.id, file))}
                  onPickImage={() => new Promise((resolve) => setImagePick({ resolve, docId: doc.id }))}
                  onStartComment={notes.startComment}
                  onCommentClick={(id) => { setActiveComment(id); showComments(); }}
                  onCommentAnchors={setAnchors}
                />
                {imagePick && (
                  <ImagePicker campaignId={cid} title="Insérer une image"
                    onClose={() => { imagePick.resolve(imagePick.result || null); setImagePick(null); }}
                    onUpload={async (files) => {
                      const img = await run(() => api.uploadDocumentImage(imagePick.docId, files[0]));
                      if (img) imagePick.result = img;
                      return Boolean(img);
                    }}
                    onPick={async (data) => {
                      const img = await run(() => api.copyImageToDocument(imagePick.docId, { source: data.source, id: data.id }));
                      if (img) imagePick.result = img;
                      return Boolean(img);
                    }} />
                )}
                </div>
              ) : (
                <div className="doc-connecting" aria-busy="true">
                  {liveState?.status === 'waiting'
                    ? 'Un autre MJ ouvre cette fiche pour la première fois, elle sera prête dans un instant…'
                    : liveState?.status === 'closed' ? 'Connexion à la fiche interrompue.' : 'Connexion à la fiche…'}
                  {(liveState?.status === 'closed' || (liveState?.status === 'connecting' && liveState?.attempts > 0)) && (
                    <p className="doc-connecting__retry">
                      {liveState.status !== 'closed' && (liveState.busy
                        ? `${liveState.busy} : nouvel essai en cours. ` : 'Le serveur ne répond pas, nouvel essai en cours. ')}
                      <button type="button" className="btn btn--small btn--ghost" onClick={() => setReloadKey((k) => k + 1)}>
                        Réessayer maintenant
                      </button>
                    </p>
                  )}
                </div>
              )}

              <DocChildren isRoot={isRoot} readMode={readMode} docs={docs} items={children}
                onCreate={() => createDoc(doc.id)} onGo={go} />
              <DocLinks links={doc.linked_from} docs={tree?.documents} onGo={go} />
            </article>
          )}
        </main>

        <DocSide side={side} doc={doc} openThreads={openThreads} headings={headings} activeHeading={activeHeading}
          onHeading={(h) => { setSideOpen(false); editorRef.current?.scrollToHeading(h.id); }}
          history={{
            versions: versioning.versions,
            activeId: versionView?.version.id ?? null,
            readOnly: readMode,
            onSelect: versioning.openVersion,
            onCurrent: () => setVersionView(null),
            onCreate: versioning.createVersion,
            onRename: versioning.renameVersion,
            onDelete: versioning.deleteVersion,
          }}
          comments={{
            threads: notes.threads,
            anchors: notes.anchors,
            draft: notes.draft,
            activeId: notes.activeComment,
            user,
            readOnly: readMode,
            onSubmitDraft: notes.submitDraft,
            onCancelDraft: notes.cancelDraft,
            onSelect: notes.selectComment,
            onReply: notes.replyComment,
            onResolve: notes.resolveComment,
            onEdit: notes.editComment,
            onDelete: notes.deleteComment,
          }} />
      </div>

      {preview && (
        <MentionPreview
          target={preview}
          entity={entities.find((e) => e.kind === preview.kind && e.id === preview.id)}
          onClose={() => setPreview(null)}
          onOpen={() => {
            flushMeta();
            onNavigate(preview.kind === 'place' ? `#/c/${cid}/lieux/${preview.id}` : `#/c/${cid}/personnages/${preview.kind}-${preview.id}`);
          }}
        />
      )}
      {docMenu && doc && (
        <PopupMenu rect={docMenu.rect} align="right" onClose={closeDocMenu} anchorSelector=".doc__more" items={[
          !isRoot && !readMode && { key: 'dup', icon: 'copy', label: 'Dupliquer la fiche', hint: 'avec ses sous-pages',
            onClick: () => duplicateDoc(doc) },
          !readMode && { key: 'tpl', icon: 'template', label: 'Enregistrer comme modèle', onClick: () => setSaveTemplate(true) },
          { key: 'hist', icon: 'history', label: 'Historique des versions', onClick: showHistory },
          // Panneau latéral replié (écran étroit) : le sommaire n'a pas d'autre accès.
          window.matchMedia('(max-width: 1280px)').matches && { key: 'toc', icon: 'list', label: 'Sommaire', onClick: showToc },
        ]} />
      )}
      {newDoc && (
        <NewDocDialog isScenario={newDoc.isScenario} user={user} onClose={() => setNewDoc(null)} onCreate={confirmCreate} />
      )}
      {saveTemplate && doc && (
        <SaveTemplateDialog defaultName={isRoot ? '' : doc.title} onClose={() => setSaveTemplate(false)} onSave={confirmSaveTemplate} />
      )}
    </div>
  );
}
