import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import { Icon, Modal, useAction, useConfirm, useToast } from './ui.jsx';
import { SectionHead, fmtDate } from './AdminPage.jsx';

const FILTERS = [
  { key: 'all', label: 'Toutes' },
  { key: 'library', label: 'Bibliothèque' },
  { key: 'gallery', label: 'Galeries des fiches' },
  { key: 'document', label: 'Fiches rédigées' },
  { key: 'map', label: 'Cartes' },
];
const KIND_LABEL = { npc: 'PNJ', faction: 'Faction', place: 'Lieu' };
const COVER_LABEL = { npc: 'Portrait', faction: 'Emblème', place: 'Illustration' };
const DOC_LABEL = { campaign: 'Fiche de campagne', scenario: 'Scénario', page: 'Page' };

function fmtSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} Ko`;
  return `${(bytes / 1024 / 1024).toFixed(1).replace('.', ',')} Mo`;
}

const keyOf = (img) => `${img.source}-${img.id}`;

function origin(img, cid) {
  if (img.source === 'library') return { label: 'Bibliothèque', href: null };
  if (img.source === 'map') return { label: 'Carte interactive', href: `#/c/${cid}/cartes/${img.id}` };
  if (img.source === 'gallery') {
    const { kind, id, name } = img.owner;
    const href = kind === 'place' ? `#/c/${cid}/lieux/${id}` : `#/c/${cid}/personnages/${kind}-${id}`;
    return { label: `${KIND_LABEL[kind]} : ${name || 'sans nom'}`, href };
  }
  if (!img.document) return { label: 'Fiche rédigée supprimée', href: null };
  return { label: `${DOC_LABEL[img.document.kind] || 'Fiche'} : ${img.document.title || 'Sans titre'}`,
    href: `#/c/${cid}/scenarios/${img.document.id}` };
}

function title(img) {
  return img.caption || img.name || (img.source === 'document' ? 'Illustration' : 'Sans légende');
}

export default function ImagesSection({ campaigns }) {
  const [cid, setCid] = useState(null);
  const [data, setData] = useState(null);
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(null);
  const [upload, setUpload] = useState(false);
  const [drop, setDrop] = useState(false);
  const fileRef = useRef(null);
  const run = useAction();
  const confirm = useConfirm();
  const toast = useToast();

  useEffect(() => {
    if (cid === null && campaigns?.length) setCid(campaigns[0].id);
  }, [campaigns, cid]);

  const load = useCallback(async () => {
    if (cid === null) return;
    const out = await run(() => api.admin.images(cid));
    if (out && out.campaign_id === cid) setData(out);
  }, [cid, run]);
  useEffect(() => { setData(null); load(); }, [load]);

  const shown = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    return data.images.filter((img) => (filter === 'all' || img.source === filter)
      && (!q || `${title(img)} ${img.name} ${origin(img, cid).label}`.toLowerCase().includes(q)));
  }, [data, filter, query, cid]);
  const counts = useMemo(() => {
    const c = { all: 0, library: 0, gallery: 0, document: 0, map: 0 };
    for (const img of data?.images || []) { c.all += 1; c[img.source] += 1; }
    return c;
  }, [data]);

  const send = async (files) => {
    const list = [...files].filter((f) => f.type.startsWith('image/'));
    if (!list.length || cid === null) return;
    setUpload(true);
    try {
      for (let i = 0; i < list.length; i += 20) {
        await api.admin.uploadLibrary(cid, list.slice(i, i + 20));
      }
      toast(`${list.length} image(s) ajoutée(s) à la bibliothèque`, 'success');
      setFilter('library');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      setUpload(false);
      load();
    }
  };

  const clean = async () => {
    const yes = await confirm({
      title: 'Supprimer les illustrations inutilisées',
      message: `${data.orphans} illustration(s) de fiches rédigées ne sont plus citées nulle part, ni dans le texte ni dans l’historique des fiches. Elles seront supprimées du serveur.`,
      confirmLabel: 'Supprimer',
    });
    if (!yes) return;
    const out = await run(() => api.admin.cleanImages(cid));
    if (out) toast(`${out.deleted} illustration(s) supprimée(s)`, 'success');
    load();
  };

  const current = campaigns?.find((c) => c.id === cid);

  return (
    <div
      className={`images-admin${drop ? ' is-drop' : ''}`}
      onDragOver={(e) => { if ([...e.dataTransfer.types].includes('Files')) { e.preventDefault(); setDrop(true); } }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDrop(false); }}
      onDrop={(e) => { e.preventDefault(); setDrop(false); send(e.dataTransfer.files); }}
    >
      <SectionHead title="Images"
        description="Toutes les images d’une campagne : galeries des fiches, illustrations des fiches rédigées, fonds des cartes interactives et bibliothèque. Déposez dans la bibliothèque les images à classer plus tard, par vous ou par Claude (connecteur).">
        <button type="button" className="btn btn--primary" onClick={() => fileRef.current?.click()} disabled={upload || cid === null}>
          <Icon name="upload" size={16} /> {upload ? 'Envoi…' : 'Déposer des images'}
        </button>
        <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple hidden
          onChange={(e) => { send(e.target.files); e.target.value = ''; }} />
      </SectionHead>

      {!campaigns ? <p className="muted">Chargement…</p> : campaigns.length === 0 ? (
        <p className="muted">Aucune campagne pour l’instant.</p>
      ) : (
        <>
          <div className="images-admin__bar">
            <select value={cid ?? ''} onChange={(e) => { setCid(Number(e.target.value)); setSelected(null); }}
              aria-label="Campagne" className="images-admin__campaign">
              {campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <input type="search" placeholder="Rechercher une légende, une fiche…" value={query}
              onChange={(e) => setQuery(e.target.value)} aria-label="Rechercher" />
          </div>
          <div className="segmented segmented--compact images-admin__filters" role="tablist" aria-label="Sorte d’images">
            {FILTERS.map((f) => (
              <button key={f.key} type="button" role="tab" aria-selected={filter === f.key}
                className={filter === f.key ? 'is-active' : ''} onClick={() => setFilter(f.key)}>
                {f.label} <span className="images-admin__count">{counts[f.key]}</span>
              </button>
            ))}
          </div>
          {data && (
            <p className="muted images-admin__stats">
              {data.count} image(s), {fmtSize(data.bytes)} sur le disque pour « {current?.name} ».
              {data.orphans > 0 && (
                <> {data.orphans} illustration(s) inutilisée(s).{' '}
                  <button type="button" className="btn btn--small btn--ghost" onClick={clean}>
                    <Icon name="trash" size={14} /> Faire le ménage
                  </button>
                </>
              )}
            </p>
          )}
          {!data ? <p className="muted">Chargement…</p> : shown.length === 0 ? (
            <div className="images-admin__empty">
              <Icon name="image" size={28} />
              <p>{data.images.length === 0 ? 'Aucune image dans cette campagne.' : 'Aucune image ne correspond.'}</p>
              <p className="muted">Glissez des images ici ou utilisez « Déposer des images » : elles iront dans la bibliothèque.</p>
            </div>
          ) : (
            <ul className="images-admin__grid">
              {shown.map((img) => {
                const from = origin(img, cid);
                return (
                  <li key={keyOf(img)}>
                    <button type="button" className="images-admin__card" onClick={() => setSelected(keyOf(img))}>
                      <span className="images-admin__thumb">
                        <img src={img.thumb_url} alt="" loading="lazy" />
                        {img.cover && <span className="images-admin__tag">{COVER_LABEL[img.owner.kind]}</span>}
                        {img.source === 'document' && !img.used && <span className="images-admin__tag is-muted">Inutilisée</span>}
                      </span>
                      <span className="images-admin__title">{title(img)}</span>
                      <span className="images-admin__origin">{from.label}</span>
                      <span className="images-admin__meta">{img.width}×{img.height} · {fmtSize(img.bytes)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
      {drop && <div className="images-admin__dropzone" aria-hidden="true"><Icon name="upload" size={28} /> Déposer dans la bibliothèque</div>}
      {selected && data && data.images.some((i) => keyOf(i) === selected) && (
        <ImageDialog img={data.images.find((i) => keyOf(i) === selected)} cid={cid}
          onClose={() => setSelected(null)} onChanged={load} />
      )}
    </div>
  );
}

function ImageDialog({ img, cid, onClose, onChanged }) {
  const [caption, setCaption] = useState(img.caption);
  const [targets, setTargets] = useState(null);
  const [target, setTarget] = useState('');
  const [filter, setFilter] = useState('');
  const [asCover, setAsCover] = useState(false);
  const [remove, setRemove] = useState(true);
  const [busy, setBusy] = useState(false);
  const run = useAction();
  const confirm = useConfirm();
  const from = origin(img, cid);

  useEffect(() => setCaption(img.caption), [img.caption]);
  useEffect(() => {
    if (img.source !== 'library') return;
    (async () => {
      const [graph, places] = await Promise.all([run(() => api.graph(cid)), run(() => api.places(cid))]);
      if (!graph || !places) return;
      setTargets([
        ...graph.npcs.map((n) => ({ key: `npc:${n.id}`, kind: 'npc', id: n.id, name: n.name })),
        ...graph.factions.map((f) => ({ key: `faction:${f.id}`, kind: 'faction', id: f.id, name: f.name })),
        ...places.places.map((p) => ({ key: `place:${p.id}`, kind: 'place', id: p.id, name: p.name })),
      ]);
    })();
  }, [img.source, cid, run]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return (targets || []).filter((t) => !q || t.name.toLowerCase().includes(q)).slice(0, 300);
  }, [targets, filter]);

  const saveCaption = async () => {
    if (caption === img.caption) return;
    if (img.source === 'library') await run(() => api.admin.updateLibrary(img.id, caption));
    if (img.source === 'gallery') await run(() => api.updateImage(img.id, { caption }));
    if (img.source === 'map' && caption.trim()) await run(() => api.updateMap(img.id, { name: caption.trim() }));
    onChanged();
  };

  const assign = async () => {
    const t = targets.find((x) => x.key === target);
    if (!t) return;
    setBusy(true);
    const out = await run(() => api.admin.assignLibrary(img.id, { kind: t.kind, entity_id: t.id, set_cover: asCover, remove }),
      `Image ajoutée à ${t.name}`);
    setBusy(false);
    if (out) { onChanged(); if (remove) onClose(); }
  };

  const setCover = async () => {
    const field = { npc: 'portrait_image_id', faction: 'emblem_image_id', place: 'image_id' }[img.owner.kind];
    await run(() => api.updateEntity(img.owner.kind, img.owner.id, { [field]: img.id }), `${COVER_LABEL[img.owner.kind]} modifié`);
    onChanged();
  };

  const remove_ = async () => {
    const message = {
      library: 'Elle sera retirée de la bibliothèque et supprimée du serveur. Les copies déjà faites sur des fiches restent.',
      map: 'C’est le fond d’une carte interactive : la carte et ses épingles seront supprimées (les lieux restent).',
      gallery: `Elle sera retirée de la galerie de « ${img.owner.name} » et supprimée du serveur.`,
      document: img.used
        ? `Elle sera retirée du texte de « ${img.document?.title || 'la fiche'} » puis supprimée du serveur. L’état actuel de la fiche reste dans son historique (sans l’image) ; les MJ qui l’ont ouverte la verront se recharger.`
        : img.in_history
          ? 'Elle n’est plus dans le texte mais figure dans l’historique de la fiche : une version restaurée s’afficherait sans elle.'
          : 'Elle n’est plus utilisée dans le texte de la fiche. Elle sera supprimée du serveur.',
    }[img.source];
    const yes = await confirm({ title: 'Supprimer cette image', message });
    if (!yes) return;
    const call = { library: () => api.admin.deleteLibrary(img.id), gallery: () => api.deleteImage(img.id),
      document: () => api.admin.deleteDocImage(img.id), map: () => api.deleteMap(img.id) }[img.source];
    const ok = await run(call, 'Image supprimée');
    if (ok !== undefined) { onClose(); onChanged(); }
  };

  return (
    <Modal title={title(img)} onClose={onClose} wide
      footer={(
        <>
          <a className="btn btn--ghost" href={img.url} download><Icon name="download" size={16} /> Télécharger</a>
          <button type="button" className="btn btn--ghost btn--danger-text" onClick={remove_}>
            <Icon name="trash" size={16} /> Supprimer
          </button>
        </>
      )}>
      <div className="image-dialog">
        <a href={img.url} target="_blank" rel="noreferrer" className="image-dialog__preview">
          <img src={img.url} alt={img.caption || ''} />
        </a>
        <dl className="image-dialog__facts">
          <div><dt>Origine</dt><dd>{from.href ? <a href={from.href} className="table__link">{from.label}</a> : from.label}</dd></div>
          <div><dt>Taille</dt><dd>{img.width}×{img.height} px · {fmtSize(img.bytes)}</dd></div>
          <div><dt>Ajoutée le</dt><dd>{fmtDate(img.created_at)}</dd></div>
          {img.name && <div><dt>Fichier d’origine</dt><dd className="table__mono">{img.name}</dd></div>}
        </dl>
        {img.source !== 'document' && (
          <label className="field">
            <span className="field__label">{img.source === 'map' ? 'Nom de la carte' : 'Légende'}</span>
            <input value={caption} onChange={(e) => setCaption(e.target.value)} onBlur={saveCaption}
              onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }} placeholder="Ajouter une légende" />
          </label>
        )}
        {img.source === 'gallery' && !img.cover && (
          <button type="button" className="btn btn--ghost" onClick={setCover}>
            <Icon name="star" size={16} /> Utiliser comme {COVER_LABEL[img.owner.kind].toLowerCase()}
          </button>
        )}
        {img.source === 'document' && img.used && (
          <p className="muted">Affichée dans le texte de la fiche : la supprimer la retire aussi du texte.</p>
        )}
        {img.source === 'library' && (
          <section className="image-dialog__assign">
            <h3>Utiliser sur une fiche</h3>
            {!targets ? <p className="muted">Chargement des fiches…</p> : targets.length === 0 ? (
              <p className="muted">Aucune fiche dans cette campagne.</p>
            ) : (
              <>
                <input type="search" placeholder="Filtrer les fiches…" value={filter} onChange={(e) => setFilter(e.target.value)}
                  aria-label="Filtrer les fiches" />
                <select value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Fiche" size={6}>
                  {['npc', 'faction', 'place'].map((k) => {
                    const items = filtered.filter((t) => t.kind === k);
                    return items.length ? (
                      <optgroup key={k} label={k === 'npc' ? 'PNJ' : k === 'faction' ? 'Factions' : 'Lieux'}>
                        {items.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
                      </optgroup>
                    ) : null;
                  })}
                </select>
                <label className="check"><input type="checkbox" checked={asCover} onChange={(e) => setAsCover(e.target.checked)} />
                  En faire le portrait, l’emblème ou l’illustration</label>
                <label className="check"><input type="checkbox" checked={remove} onChange={(e) => setRemove(e.target.checked)} />
                  Retirer de la bibliothèque une fois ajoutée</label>
                <button type="button" className="btn btn--primary" disabled={!target || busy} onClick={assign}>
                  <Icon name="plus" size={16} /> {busy ? 'Ajout…' : 'Ajouter à la fiche'}
                </button>
              </>
            )}
          </section>
        )}
      </div>
    </Modal>
  );
}
