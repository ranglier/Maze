import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import { Icon, Modal, useAction, useConfirm, useToast } from './ui.jsx';
import { SectionHead, fmtDate, fmtSize } from './AdminPage.jsx';
import { KIND_LABEL } from '../constants.js';
import { N_, pt, t, tn, useLocale } from '../i18n/index.js';

// Libellés traduits à l'affichage (t(…)) : N_ les signale seulement à l'extraction.
const FILTERS = [
  { key: 'all', label: N_('Toutes') },
  { key: 'library', label: N_('Bibliothèque') },
  { key: 'gallery', label: N_('Galeries des fiches') },
  { key: 'document', label: N_('Fiches rédigées') },
  { key: 'map', label: N_('Cartes') },
];
const COVER_LABEL = { npc: N_('Portrait'), faction: N_('Emblème'), place: N_('Illustration') };
const DOC_LABEL = { campaign: N_('Fiche de campagne'), scenario: N_('Scénario'), page: N_('Page') };
// Phrases entières par sorte d'image de couverture (genre et ordre des mots propres à chaque langue).
const COVER_DONE = { npc: () => t('Portrait modifié'), faction: () => t('Emblème modifié'), place: () => t('Illustration modifiée') };
const COVER_USE = {
  npc: () => t('Utiliser comme portrait'), faction: () => t('Utiliser comme emblème'), place: () => t('Utiliser comme illustration'),
};

const keyOf = (img) => `${img.source}-${img.id}`;

function origin(img, cid) {
  if (img.source === 'library') return { label: t('Bibliothèque'), href: null };
  if (img.source === 'map') return { label: t('Carte interactive'), href: `#/c/${cid}/cartes/${img.id}` };
  if (img.source === 'gallery') {
    const { kind, id, name } = img.owner;
    const href = kind === 'place' ? `#/c/${cid}/lieux/${id}` : `#/c/${cid}/personnages/${kind}-${id}`;
    return { label: t('{kind} : {name}', { kind: KIND_LABEL[kind], name: name || t('sans nom') }), href };
  }
  if (!img.document) return { label: t('Fiche rédigée supprimée'), href: null };
  const docKind = DOC_LABEL[img.document.kind] ? t(DOC_LABEL[img.document.kind]) : pt('doc', 'Fiche');
  return { label: t('{kind} : {name}', { kind: docKind, name: img.document.title || t('Sans titre') }),
    href: `#/c/${cid}/scenarios/${img.document.id}` };
}

function title(img) {
  return img.caption || img.name || (img.source === 'document' ? t('Illustration') : t('Sans légende'));
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
  const locale = useLocale();   // la recherche porte aussi sur les libellés d'origine, traduits

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
  }, [data, filter, query, cid, locale]);
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
      toast(tn('{n} image ajoutée à la bibliothèque', '{n} images ajoutées à la bibliothèque', list.length), 'success');
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
      title: t('Supprimer les illustrations inutilisées'),
      message: tn('{n} illustration de fiche rédigée n’est plus citée nulle part, ni dans le texte ni dans l’historique des fiches. Elle sera supprimée du serveur.',
        '{n} illustrations de fiches rédigées ne sont plus citées nulle part, ni dans le texte ni dans l’historique des fiches. Elles seront supprimées du serveur.',
        data.orphans),
      confirmLabel: t('Supprimer'),
    });
    if (!yes) return;
    const out = await run(() => api.admin.cleanImages(cid));
    if (out) toast(tn('{n} illustration supprimée', '{n} illustrations supprimées', out.deleted), 'success');
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
      <SectionHead title={t('Images')}
        description={t('Toutes les images d’une campagne : galeries des fiches, illustrations des fiches rédigées, fonds des cartes interactives et bibliothèque. Déposez dans la bibliothèque les images à classer plus tard, par vous ou par Claude (connecteur).')}>
        <button type="button" className="btn btn--primary" onClick={() => fileRef.current?.click()} disabled={upload || cid === null}>
          <Icon name="upload" size={16} /> {upload ? t('Envoi…') : t('Déposer des images')}
        </button>
        <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple hidden
          onChange={(e) => { send(e.target.files); e.target.value = ''; }} />
      </SectionHead>

      {!campaigns ? <p className="muted">{t('Chargement…')}</p> : campaigns.length === 0 ? (
        <p className="muted">{t('Aucune campagne pour l’instant.')}</p>
      ) : (
        <>
          <div className="images-admin__bar">
            <select value={cid ?? ''} onChange={(e) => { setCid(Number(e.target.value)); setSelected(null); }}
              aria-label={t('Campagne')} className="images-admin__campaign">
              {campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <input type="search" placeholder={t('Rechercher une légende, une fiche…')} value={query}
              onChange={(e) => setQuery(e.target.value)} aria-label={t('Rechercher')} />
          </div>
          <div className="segmented segmented--compact images-admin__filters" role="tablist" aria-label={t('Sorte d’images')}>
            {FILTERS.map((f) => (
              <button key={f.key} type="button" role="tab" aria-selected={filter === f.key}
                className={filter === f.key ? 'is-active' : ''} onClick={() => setFilter(f.key)}>
                {t(f.label)} <span className="images-admin__count">{counts[f.key]}</span>
              </button>
            ))}
          </div>
          {data && (
            <p className="muted images-admin__stats">
              {tn('{n} image, {size} sur le disque pour « {campaign} ».', '{n} images, {size} sur le disque pour « {campaign} ».',
                data.count, { size: fmtSize(data.bytes), campaign: current?.name ?? '' })}
              {data.orphans > 0 && (
                <> {tn('{n} illustration inutilisée.', '{n} illustrations inutilisées.', data.orphans)}{' '}
                  <button type="button" className="btn btn--small btn--ghost" onClick={clean}>
                    <Icon name="trash" size={14} /> {t('Faire le ménage')}
                  </button>
                </>
              )}
            </p>
          )}
          {!data ? <p className="muted">{t('Chargement…')}</p> : shown.length === 0 ? (
            <div className="images-admin__empty">
              <Icon name="image" size={28} />
              <p>{data.images.length === 0 ? t('Aucune image dans cette campagne.') : t('Aucune image ne correspond.')}</p>
              <p className="muted">{t('Glissez des images ici ou utilisez « Déposer des images » : elles iront dans la bibliothèque.')}</p>
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
                        {img.cover && <span className="images-admin__tag">{t(COVER_LABEL[img.owner.kind])}</span>}
                        {img.source === 'document' && !img.used && <span className="images-admin__tag is-muted">{t('Inutilisée')}</span>}
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
      {drop && <div className="images-admin__dropzone" aria-hidden="true"><Icon name="upload" size={28} /> {t('Déposer dans la bibliothèque')}</div>}
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
    return (targets || []).filter((x) => !q || x.name.toLowerCase().includes(q)).slice(0, 300);
  }, [targets, filter]);

  const saveCaption = async () => {
    if (caption === img.caption) return;
    if (img.source === 'library') await run(() => api.admin.updateLibrary(img.id, caption));
    if (img.source === 'gallery') await run(() => api.updateImage(img.id, { caption }));
    if (img.source === 'map' && caption.trim()) await run(() => api.updateMap(img.id, { name: caption.trim() }));
    onChanged();
  };

  const assign = async () => {
    const picked = targets.find((x) => x.key === target);
    if (!picked) return;
    setBusy(true);
    const out = await run(() => api.admin.assignLibrary(img.id, { kind: picked.kind, entity_id: picked.id, set_cover: asCover, remove }),
      t('Image ajoutée à {name}', { name: picked.name }));
    setBusy(false);
    if (out) { onChanged(); if (remove) onClose(); }
  };

  const setCover = async () => {
    const field = { npc: 'portrait_image_id', faction: 'emblem_image_id', place: 'image_id' }[img.owner.kind];
    await run(() => api.updateEntity(img.owner.kind, img.owner.id, { [field]: img.id }), COVER_DONE[img.owner.kind]());
    onChanged();
  };

  const remove_ = async () => {
    const message = {
      library: () => t('Elle sera retirée de la bibliothèque et supprimée du serveur. Les copies déjà faites sur des fiches restent.'),
      map: () => t('C’est le fond d’une carte interactive : la carte et ses épingles seront supprimées (les lieux restent).'),
      gallery: () => t('Elle sera retirée de la galerie de « {name} » et supprimée du serveur.', { name: img.owner.name }),
      document: () => (img.used
        ? t('Elle sera retirée du texte de « {title} » puis supprimée du serveur. L’état actuel de la fiche reste dans son historique (sans l’image) ; les MJ qui l’ont ouverte la verront se recharger.',
          { title: img.document?.title || pt('doc', 'la fiche') })
        : img.in_history
          ? t('Elle n’est plus dans le texte mais figure dans l’historique de la fiche : une version restaurée s’afficherait sans elle.')
          : t('Elle n’est plus utilisée dans le texte de la fiche. Elle sera supprimée du serveur.')),
    }[img.source]();
    const yes = await confirm({ title: t('Supprimer cette image'), message });
    if (!yes) return;
    const call = { library: () => api.admin.deleteLibrary(img.id), gallery: () => api.deleteImage(img.id),
      document: () => api.admin.deleteDocImage(img.id), map: () => api.deleteMap(img.id) }[img.source];
    const ok = await run(call, t('Image supprimée'));
    if (ok !== undefined) { onClose(); onChanged(); }
  };

  return (
    <Modal title={title(img)} onClose={onClose} wide
      footer={(
        <>
          <a className="btn btn--ghost" href={img.url} download><Icon name="download" size={16} /> {t('Télécharger')}</a>
          <button type="button" className="btn btn--ghost btn--danger-text" onClick={remove_}>
            <Icon name="trash" size={16} /> {t('Supprimer')}
          </button>
        </>
      )}>
      <div className="image-dialog">
        <a href={img.url} target="_blank" rel="noreferrer" className="image-dialog__preview">
          <img src={img.url} alt={img.caption || ''} />
        </a>
        <dl className="image-dialog__facts">
          <div><dt>{t('Origine')}</dt><dd>{from.href ? <a href={from.href} className="table__link">{from.label}</a> : from.label}</dd></div>
          <div><dt>{t('Taille')}</dt><dd>{img.width}×{img.height} px · {fmtSize(img.bytes)}</dd></div>
          <div><dt>{t('Ajoutée le')}</dt><dd>{fmtDate(img.created_at)}</dd></div>
          {img.name && <div><dt>{t('Fichier d’origine')}</dt><dd className="table__mono">{img.name}</dd></div>}
        </dl>
        {img.source !== 'document' && (
          <label className="field">
            <span className="field__label">{img.source === 'map' ? t('Nom de la carte') : pt('image', 'Légende')}</span>
            <input value={caption} onChange={(e) => setCaption(e.target.value)} onBlur={saveCaption}
              onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }} placeholder={t('Ajouter une légende')} />
          </label>
        )}
        {img.source === 'gallery' && !img.cover && (
          <button type="button" className="btn btn--ghost" onClick={setCover}>
            <Icon name="star" size={16} /> {COVER_USE[img.owner.kind]()}
          </button>
        )}
        {img.source === 'document' && img.used && (
          <p className="muted">{t('Affichée dans le texte de la fiche : la supprimer la retire aussi du texte.')}</p>
        )}
        {img.source === 'library' && (
          <section className="image-dialog__assign">
            <h3>{t('Utiliser sur une fiche')}</h3>
            {!targets ? <p className="muted">{t('Chargement des fiches…')}</p> : targets.length === 0 ? (
              <p className="muted">{t('Aucune fiche dans cette campagne.')}</p>
            ) : (
              <>
                <input type="search" placeholder={t('Filtrer les fiches…')} value={filter} onChange={(e) => setFilter(e.target.value)}
                  aria-label={t('Filtrer les fiches')} />
                <select value={target} onChange={(e) => setTarget(e.target.value)} aria-label={pt('entry', 'Fiche')} size={6}>
                  {['npc', 'faction', 'place'].map((k) => {
                    const items = filtered.filter((x) => x.kind === k);
                    return items.length ? (
                      <optgroup key={k} label={k === 'npc' ? t('PNJ') : k === 'faction' ? t('Factions') : t('Lieux')}>
                        {items.map((x) => <option key={x.key} value={x.key}>{x.name}</option>)}
                      </optgroup>
                    ) : null;
                  })}
                </select>
                <label className="check"><input type="checkbox" checked={asCover} onChange={(e) => setAsCover(e.target.checked)} />
                  {t('En faire le portrait, l’emblème ou l’illustration')}</label>
                <label className="check"><input type="checkbox" checked={remove} onChange={(e) => setRemove(e.target.checked)} />
                  {t('Retirer de la bibliothèque une fois ajoutée')}</label>
                <button type="button" className="btn btn--primary" disabled={!target || busy} onClick={assign}>
                  <Icon name="plus" size={16} /> {busy ? t('Ajout…') : t('Ajouter à la fiche')}
                </button>
              </>
            )}
          </section>
        )}
      </div>
    </Modal>
  );
}
