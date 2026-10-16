import { useEffect, useRef, useState } from 'react';
import { api, exportCampaign } from '../api.js';
import { Field, Icon, Modal, useAction, useConfirm, useToast } from './ui.jsx';
import { useSheet } from '../viewport.js';
import { t, tn } from '../i18n/index.js';

export function CampaignDialog({ campaign, onClose, onSaved }) {
  const run = useAction();
  const [name, setName] = useState(campaign?.name || '');
  const [description, setDescription] = useState(campaign?.description || '');
  const submit = async (e) => {
    e.preventDefault();
    const data = { name, description };
    const saved = await run(() => (campaign ? api.updateCampaign(campaign.id, data) : api.createCampaign(data)),
      campaign ? t('Campagne renommée') : t('Campagne créée'));
    if (saved) onSaved(saved);
  };
  return (
    <Modal title={campaign ? t('Modifier la campagne') : t('Nouvelle campagne')} onClose={onClose}
      footer={(
        <>
          <button type="button" className="btn btn--ghost" onClick={onClose}>{t('Annuler')}</button>
          <button type="submit" form="campaign-form" className="btn btn--primary" disabled={!name.trim()}>
            {campaign ? t('Enregistrer') : t('Créer la campagne')}
          </button>
        </>
      )}>
      <form id="campaign-form" onSubmit={submit}>
        <Field label={t('Nom')} htmlFor="campaign-name">
          <input id="campaign-name" value={name} maxLength={200} required onChange={(e) => setName(e.target.value)}
            placeholder={t('Les Brumes d’Ys')} />
        </Field>
        <Field label={t('Description')} htmlFor="campaign-desc" hint={t('Univers, ton, joueurs… facultatif.')}>
          <textarea id="campaign-desc" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
      </form>
    </Modal>
  );
}

/**
 * Import d'une campagne exportée (.zip) : `pick()` ouvre le sélecteur de fichier, `input` est à
 * placer dans le rendu, `progress` vaut null ou 0..1 pendant l'envoi.
 */
export function useCampaignImport(onImported) {
  const fileRef = useRef(null);
  const [progress, setProgress] = useState(null);
  const toast = useToast();
  const upload = async (file) => {
    if (!file) return;
    setProgress(0);
    try {
      const created = await api.importCampaign(file, setProgress);
      toast(t('Campagne « {name} » importée : {content}.', {
        name: created.name,
        content: [tn('{n} PNJ', '{n} PNJ', created.npc_count), tn('{n} faction', '{n} factions', created.faction_count),
          tn('{n} fiche rédigée', '{n} fiches rédigées', created.document_count)].join(', '),
      }), 'success');
      onImported?.(created);
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      setProgress(null);
    }
  };
  const input = (
    <input ref={fileRef} type="file" accept=".zip,application/zip" hidden
      onChange={(e) => { upload(e.target.files[0]); e.target.value = ''; }} />
  );
  return { pick: () => fileRef.current?.click(), input, progress };
}

export const importLabel = (progress) => (progress === null ? t('Importer une campagne')
  : t('Import… {percent} %', { percent: Math.round(progress * 100) }));

export default function CampaignMenu({ campaigns, current, onSwitch, onChanged, onDeleted }) {
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState(null);
  const ref = useRef(null);
  useSheet(open);
  const run = useAction();
  const confirm = useConfirm();
  const importer = useCampaignImport((created) => onChanged(created, true));

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);

  const remove = async () => {
    setOpen(false);
    const ok = await confirm({
      title: t('Supprimer « {name} »', { name: current.name }),
      message: t('Tous les PNJ, factions, relations et images de cette campagne seront définitivement supprimés.'),
      confirmLabel: t('Supprimer la campagne'),
    });
    if (!ok) return;
    const done = await run(() => api.deleteCampaign(current.id), t('Campagne supprimée'));
    if (done !== undefined) onDeleted(current.id);
  };

  return (
    <div className="popover-anchor campaign-menu" ref={ref}>
      <button type="button" className="campaign-menu__trigger" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="campaign-menu__name">{current?.name}</span>
        <Icon name="chevron" size={14} />
      </button>
      {open && (
        <div className="popover popover--left" role="menu">
          <p className="popover__title">{t('Campagnes')}</p>
          <ul className="campaign-menu__list">
            {campaigns.map((c) => (
              <li key={c.id}>
                <button type="button" role="menuitemradio" aria-checked={c.id === current?.id}
                  className={c.id === current?.id ? 'is-active' : ''}
                  onClick={() => { setOpen(false); onSwitch(c.id); }}>
                  <span>{c.name}</span>
                  <span className="campaign-menu__count">
                    {tn('{n} PNJ', '{n} PNJ', c.npc_count)}, {tn('{n} faction', '{n} factions', c.faction_count)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className="popover__group popover__group--actions">
            <button type="button" role="menuitem" onClick={() => { setOpen(false); setDialog({}); }}>
              <Icon name="plus" size={15} /> {t('Nouvelle campagne')}
            </button>
            {current && (
              <button type="button" role="menuitem" onClick={() => { setOpen(false); setDialog({ campaign: current }); }}>
                <Icon name="edit" size={15} /> {t('Renommer « {name} »', { name: current.name })}
              </button>
            )}
            {current && (
              <a role="menuitem" href={api.campaignExportUrl(current.id)} download
                onClick={(e) => { e.preventDefault(); setOpen(false); exportCampaign(current.id, confirm); }}
                title={t('Archive .zip : PNJ, factions, liens, scénarios et images, à importer dans un autre Maze')}>
                <Icon name="download" size={15} /> {t('Exporter « {name} »', { name: current.name })}
              </a>
            )}
            <button type="button" role="menuitem" disabled={importer.progress !== null}
              onClick={() => { setOpen(false); importer.pick(); }}>
              <Icon name="upload" size={15} /> {importLabel(importer.progress)}
            </button>
            {current && (
              <button type="button" role="menuitem" className="danger-text" onClick={remove}>
                <Icon name="trash" size={15} /> {t('Supprimer cette campagne')}
              </button>
            )}
          </div>
        </div>
      )}
      {importer.input}
      {dialog && (
        <CampaignDialog campaign={dialog.campaign} onClose={() => setDialog(null)}
          onSaved={(saved) => { setDialog(null); onChanged(saved, !dialog.campaign); }} />
      )}
    </div>
  );
}
