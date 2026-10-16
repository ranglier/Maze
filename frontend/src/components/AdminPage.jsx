import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, exportCampaign } from '../api.js';
import { CampaignDialog, importLabel, useCampaignImport } from './CampaignMenu.jsx';
import { Field, Icon, Modal, useAction, useConfirm, useToast } from './ui.jsx';
import { ROLE_LABEL } from './UserMenu.jsx';
import { N_, dateFormat, formatList, formatNumber, t, tn } from '../i18n/index.js';
import ClaudeSection from './ClaudeSection.jsx';
import FoundrySection from './FoundrySection.jsx';
import ImagesSection from './ImagesSection.jsx';

// Libellés traduits à l'affichage (t(s.label)) : N_ les signale seulement à l'extraction.
export const ADMIN_SECTIONS = [
  { key: 'comptes', label: N_('Comptes'), icon: 'users' },
  { key: 'campagnes', label: N_('Campagnes'), icon: 'campaign' },
  { key: 'images', label: N_('Images'), icon: 'image' },
  { key: 'sauvegardes', label: N_('Sauvegardes'), icon: 'archive' },
  { key: 'mise-a-jour', label: N_('Mise à jour'), icon: 'refresh' },
  { key: 'claude', label: N_('Connecteur Claude'), icon: 'spark' },
  { key: 'foundry', label: N_('Foundry VTT'), icon: 'castle' },
];

const DATE_TIME = { dateStyle: 'medium', timeStyle: 'short' };
/** Date et heure dans la langue de l'interface (formateur créé à l'affichage), « Jamais » si vide. */
export function fmtDate(iso) {
  if (!iso) return t('Jamais');
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : dateFormat(DATE_TIME).format(d);
}

const ONE_DECIMAL = { minimumFractionDigits: 1, maximumFractionDigits: 1 };
/** Taille d'un fichier : Ko (au moins 1), Mo ou Go avec une décimale. */
export function fmtSize(bytes) {
  if (bytes < 1024 * 1024) return t('{n} Ko', { n: Math.max(1, Math.round(bytes / 1024)) });
  if (bytes >= 1024 ** 3) return t('{n} Go', { n: formatNumber(bytes / 1024 ** 3, ONE_DECIMAL) });
  return t('{n} Mo', { n: formatNumber(bytes / 1024 / 1024, ONE_DECIMAL) });
}

/** Contenu d'une campagne : « 3 PNJ, 2 factions, 5 images ». */
const campaignContent = (c) => [tn('{n} PNJ', '{n} PNJ', c.npc_count),
  tn('{n} faction', '{n} factions', c.faction_count), tn('{n} image', '{n} images', c.image_count)].join(', ');

export default function AdminPage({ user, section, header, onNavigate, onCampaignsChanged }) {
  const [users, setUsers] = useState(null);
  const [campaigns, setCampaigns] = useState(null);
  const run = useAction();

  const reload = useCallback(async () => {
    const [u, c] = await Promise.all([
      run(() => api.admin.users()),
      run(() => api.admin.campaigns()),
    ]);
    if (u) setUsers(u);
    if (c) setCampaigns(c);
  }, [run]);

  useEffect(() => { reload(); }, [reload]);

  const current = ADMIN_SECTIONS.find((s) => s.key === section) || ADMIN_SECTIONS[0];
  const afterCampaignChange = async () => { await reload(); onCampaignsChanged?.(); };

  return (
    <div className="admin">
      <header className="topbar">{header}</header>
      <div className="admin__body">
        <nav className="admin__nav" aria-label={t('Sections de l’administration')}>
          {ADMIN_SECTIONS.map((s) => (
            <a key={s.key} href={`#/admin/${s.key}`} className={s.key === current.key ? 'is-active' : ''}
              aria-current={s.key === current.key ? 'page' : undefined}>
              <Icon name={s.icon} size={17} /> {t(s.label)}
            </a>
          ))}
        </nav>
        <main className="admin__main">
          {current.key === 'comptes' && (
            <UsersSection me={user} users={users} campaigns={campaigns} onChanged={reload} />
          )}
          {current.key === 'campagnes' && (
            <CampaignsSection users={users} campaigns={campaigns} onChanged={afterCampaignChange}
              onOpen={(id) => onNavigate(`#/c/${id}`)} />
          )}
          {current.key === 'images' && <ImagesSection campaigns={campaigns} />}
          {current.key === 'sauvegardes' && <BackupsSection />}
          {current.key === 'mise-a-jour' && <UpdatesSection />}
          {current.key === 'claude' && <ClaudeSection campaigns={campaigns} />}
          {current.key === 'foundry' && <FoundrySection campaigns={campaigns} />}
        </main>
      </div>
    </div>
  );
}

export function SectionHead({ title, description, children }) {
  return (
    <div className="admin__head">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="admin__head-actions">{children}</div>
    </div>
  );
}

// --- Comptes -------------------------------------------------------------------

function UsersSection({ me, users, campaigns, onChanged }) {
  const [dialog, setDialog] = useState(null);
  const campaignName = useMemo(() => new Map((campaigns || []).map((c) => [c.id, c.name])), [campaigns]);

  return (
    <>
      <SectionHead title={t('Comptes')} description={t('Les administrateurs voient toutes les campagnes. Un MJ ne voit que celles qui lui sont attribuées.')}>
        <button type="button" className="btn btn--primary" onClick={() => setDialog({})}>
          <Icon name="plus" size={16} /> {t('Nouveau compte')}
        </button>
      </SectionHead>
      {!users ? <p className="muted">{t('Chargement…')}</p> : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('Compte')}</th><th>{t('Rôle')}</th><th>{t('Campagnes')}</th><th>{t('Dernière connexion')}</th>
                <th><span className="sr-only">{t('Actions')}</span></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className={u.active ? '' : 'is-inactive'}>
                  <td>
                    <strong>{u.display_name || u.username}</strong>
                    <span className="table__sub">{u.id === me.id ? t('{username} (vous)', { username: u.username }) : u.username}</span>
                  </td>
                  <td>
                    <span className={`role-chip role-chip--${u.role}`}>{ROLE_LABEL[u.role]}</span>
                    {!u.active && <span className="role-chip role-chip--off">{t('Désactivé')}</span>}
                  </td>
                  <td className="table__campaigns">
                    {u.role === 'admin' ? <span className="muted">{t('Toutes')}</span>
                      : u.campaign_ids.length === 0 ? <span className="muted">{t('Aucune')}</span>
                        : u.campaign_ids.map((id) => campaignName.get(id)).filter(Boolean).join(', ')}
                  </td>
                  <td>{fmtDate(u.last_login_at)}</td>
                  <td className="table__actions">
                    <button type="button" className="btn btn--small btn--ghost" onClick={() => setDialog({ user: u })}>
                      <Icon name="edit" size={14} /> {t('Modifier')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {dialog && (
        <UserDialog me={me} user={dialog.user} campaigns={campaigns || []}
          onClose={() => setDialog(null)} onSaved={() => { setDialog(null); onChanged(); }} />
      )}
    </>
  );
}

function UserDialog({ me, user, campaigns, onClose, onSaved }) {
  const editing = Boolean(user);
  const run = useAction();
  const confirm = useConfirm();
  const [form, setForm] = useState({
    username: user?.username || '',
    display_name: user?.display_name || '',
    role: user?.role || 'mj',
    active: user?.active ?? true,
    campaign_ids: user?.campaign_ids || [],
    password: '',
    confirm: '',
  });
  const [resetPassword, setResetPassword] = useState(!editing);
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const isSelf = user?.id === me.id;
  const mismatch = form.confirm && form.password !== form.confirm;
  const passwordOk = !resetPassword || (form.password.length >= 8 && form.password === form.confirm);
  const valid = form.username && passwordOk;

  const toggleCampaign = (id) => set('campaign_ids',
    form.campaign_ids.includes(id) ? form.campaign_ids.filter((x) => x !== id) : [...form.campaign_ids, id]);

  const save = async (e) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    let ok;
    if (!editing) {
      ok = await run(() => api.admin.createUser({
        username: form.username.trim(), display_name: form.display_name, role: form.role,
        password: form.password, campaign_ids: form.role === 'mj' ? form.campaign_ids : [],
      }), t('Compte « {name} » créé', { name: form.username.trim() }));
    } else {
      const changes = { display_name: form.display_name, role: form.role, active: form.active };
      if (resetPassword) changes.password = form.password;
      ok = await run(async () => {
        await api.admin.updateUser(user.id, changes);
        // Attribution des campagnes : mise à jour campagne par campagne.
        if (form.role === 'mj') {
          for (const c of campaigns) {
            const had = c.member_ids.includes(user.id);
            const wants = form.campaign_ids.includes(c.id);
            if (had !== wants) {
              const ids = wants ? [...c.member_ids, user.id] : c.member_ids.filter((x) => x !== user.id);
              await api.admin.setMembers(c.id, ids);
            }
          }
        }
        return true;
      }, t('Compte enregistré'));
    }
    setBusy(false);
    if (ok) onSaved();
  };

  const remove = async () => {
    const yes = await confirm({
      title: t('Supprimer le compte « {name} »', { name: user.username }),
      message: user.role === 'admin'
        ? t('Le compte sera supprimé. Les campagnes et leur contenu sont conservés. Les jetons Foundry qu’il a créés seront révoqués.')
        : t('Le compte sera supprimé. Les campagnes et leur contenu sont conservés.'),
      confirmLabel: t('Supprimer le compte'),
    });
    if (!yes) return;
    const done = await run(() => api.admin.deleteUser(user.id), t('Compte supprimé'));
    if (done !== undefined) onSaved();
  };

  return (
    <Modal title={editing ? t('Modifier « {name} »', { name: user.username }) : t('Nouveau compte')} onClose={onClose} wide footer={(
      <>
        {editing && !isSelf && (
          <button type="button" className="btn btn--ghost btn--danger-text modal__foot-left" onClick={remove}>
            <Icon name="trash" size={15} /> {t('Supprimer')}
          </button>
        )}
        <button type="button" className="btn btn--ghost" onClick={onClose}>{t('Annuler')}</button>
        <button type="submit" form="user-form" className="btn btn--primary" disabled={!valid || busy}>
          {editing ? t('Enregistrer') : t('Créer le compte')}
        </button>
      </>
    )}>
      <form id="user-form" onSubmit={save}>
        <div className="form-grid form-grid--even">
          <Field label={t('Identifiant')} htmlFor="u-username" hint={editing ? t('Non modifiable.') : t('Servira à se connecter.')}>
            <input id="u-username" value={form.username} disabled={editing} autoCapitalize="none" spellCheck={false}
              onChange={(e) => set('username', e.target.value)} required maxLength={40} />
          </Field>
          <Field label={t('Nom affiché')} htmlFor="u-name">
            <input id="u-name" value={form.display_name} maxLength={200} onChange={(e) => set('display_name', e.target.value)} />
          </Field>
        </div>

        <div className="field">
          <span className="field__label">{t('Rôle')}</span>
          <div className="segmented" role="radiogroup" aria-label={t('Rôle')}>
            {['mj', 'admin'].map((k) => (
              <button key={k} type="button" role="radio" aria-checked={form.role === k}
                className={form.role === k ? 'is-active' : ''} disabled={isSelf}
                onClick={() => set('role', k)}>{ROLE_LABEL[k]}</button>
            ))}
          </div>
          {isSelf && <p className="field__hint">{t('Vous ne pouvez pas modifier votre propre rôle.')}</p>}
        </div>

        {form.role === 'mj' && (
          <fieldset className="checklist">
            <legend>{t('Campagnes attribuées')}</legend>
            {campaigns.length === 0 && <p className="muted">{t('Aucune campagne pour l’instant.')}</p>}
            {campaigns.map((c) => (
              <label key={c.id} className="check">
                <input type="checkbox" checked={form.campaign_ids.includes(c.id)} onChange={() => toggleCampaign(c.id)} />
                {c.name}
              </label>
            ))}
          </fieldset>
        )}

        {editing && !isSelf && (
          <label className="check check--block">
            <input type="checkbox" checked={form.active} onChange={(e) => set('active', e.target.checked)} />
            {t('Compte actif (décocher pour bloquer la connexion sans supprimer le compte)')}
          </label>
        )}

        {editing && (
          <label className="check check--block">
            <input type="checkbox" checked={resetPassword} onChange={(e) => setResetPassword(e.target.checked)} />
            {t('Définir un nouveau mot de passe')}
          </label>
        )}
        {resetPassword && (
          <div className="form-grid form-grid--even">
            <Field label={editing ? t('Nouveau mot de passe') : t('Mot de passe')} htmlFor="u-pw" hint={t('8 caractères minimum.')}>
              <input id="u-pw" type="password" autoComplete="new-password" value={form.password}
                onChange={(e) => set('password', e.target.value)} />
            </Field>
            <Field label={t('Confirmation')} htmlFor="u-pw2">
              <input id="u-pw2" type="password" autoComplete="new-password" value={form.confirm}
                onChange={(e) => set('confirm', e.target.value)} />
            </Field>
          </div>
        )}
        {mismatch && <p className="form-error">{t('Les deux mots de passe ne correspondent pas.')}</p>}
      </form>
    </Modal>
  );
}

// --- Campagnes -----------------------------------------------------------------

function CampaignsSection({ users, campaigns, onChanged, onOpen }) {
  const [dialog, setDialog] = useState(null);
  const run = useAction();
  const confirm = useConfirm();
  const userName = useMemo(() => new Map((users || []).map((u) => [u.id, u.display_name || u.username])), [users]);
  const importer = useCampaignImport(() => onChanged());

  const remove = async (c) => {
    const yes = await confirm({
      title: t('Supprimer « {name} »', { name: c.name }),
      message: t('Tout le contenu de cette campagne ({content}, relations) sera définitivement supprimé.', { content: campaignContent(c) }),
      confirmLabel: t('Supprimer la campagne'),
    });
    if (!yes) return;
    const done = await run(() => api.deleteCampaign(c.id), t('Campagne supprimée'));
    if (done !== undefined) onChanged();
  };

  return (
    <>
      <SectionHead title={t('Campagnes')} description={t('Attribuez chaque campagne aux MJ qui doivent y accéder. L’export produit une archive .zip (PNJ, factions, liens, scénarios et images) à réimporter ici ou dans un autre Maze.')}>
        <button type="button" className="btn btn--ghost" onClick={importer.pick} disabled={importer.progress !== null}>
          <Icon name="upload" size={16} /> {importLabel(importer.progress)}
        </button>
        {importer.input}
        <button type="button" className="btn btn--primary" onClick={() => setDialog({ type: 'create' })}>
          <Icon name="plus" size={16} /> {t('Nouvelle campagne')}
        </button>
      </SectionHead>
      {!campaigns ? <p className="muted">{t('Chargement…')}</p> : campaigns.length === 0 ? (
        <p className="muted">{t('Aucune campagne.')}</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('Campagne')}</th><th>{t('MJ')}</th><th>{t('Contenu')}</th><th>{t('Modifiée')}</th>
                <th><span className="sr-only">{t('Actions')}</span></th>
              </tr>
            </thead>
            <tbody>
              {campaigns.map((c) => (
                <tr key={c.id}>
                  <td>
                    <button type="button" className="table__link" onClick={() => onOpen(c.id)}>{c.name}</button>
                    {c.description && <span className="table__sub table__sub--clip">{c.description}</span>}
                  </td>
                  <td className="table__campaigns">
                    {c.member_ids.length === 0 ? <span className="muted">{t('Administrateurs seulement')}</span>
                      : c.member_ids.map((id) => userName.get(id)).filter(Boolean).join(', ')}
                  </td>
                  <td className="table__nums">{campaignContent(c)}</td>
                  <td>{fmtDate(c.updated_at)}</td>
                  <td className="table__actions">
                    <button type="button" className="btn btn--small btn--ghost" onClick={() => setDialog({ type: 'members', campaign: c })}>
                      <Icon name="users" size={14} /> {t('Accès')}
                    </button>
                    <a className="icon-btn icon-btn--small" title={t('Exporter (.zip)')} aria-label={t('Exporter {name}', { name: c.name })}
                      href={api.campaignExportUrl(c.id)} download
                      onClick={(e) => { e.preventDefault(); exportCampaign(c.id, confirm); }}>
                      <Icon name="download" size={15} />
                    </a>
                    <button type="button" className="icon-btn icon-btn--small" title={t('Renommer')} aria-label={t('Renommer {name}', { name: c.name })}
                      onClick={() => setDialog({ type: 'edit', campaign: c })}>
                      <Icon name="edit" size={15} />
                    </button>
                    <button type="button" className="icon-btn icon-btn--small icon-btn--danger" title={t('Supprimer')}
                      aria-label={t('Supprimer {name}', { name: c.name })} onClick={() => remove(c)}>
                      <Icon name="trash" size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(dialog?.type === 'create' || dialog?.type === 'edit') && (
        <CampaignDialog campaign={dialog.campaign} onClose={() => setDialog(null)}
          onSaved={() => { setDialog(null); onChanged(); }} />
      )}
      {dialog?.type === 'members' && (
        <MembersDialog campaign={dialog.campaign} users={users || []}
          onClose={() => setDialog(null)} onSaved={() => { setDialog(null); onChanged(); }} />
      )}
    </>
  );
}

function MembersDialog({ campaign, users, onClose, onSaved }) {
  const run = useAction();
  const [selected, setSelected] = useState(campaign.member_ids);
  const mjs = users.filter((u) => u.role === 'mj');
  const toggle = (id) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const save = async () => {
    const done = await run(() => api.admin.setMembers(campaign.id, selected), t('Accès enregistrés'));
    if (done) onSaved();
  };
  return (
    <Modal title={t('Accès à « {name} »', { name: campaign.name })} onClose={onClose} footer={(
      <>
        <button type="button" className="btn btn--ghost" onClick={onClose}>{t('Annuler')}</button>
        <button type="button" className="btn btn--primary" onClick={save}>{t('Enregistrer')}</button>
      </>
    )}>
      <p className="field__hint">{t('Les administrateurs ont toujours accès à toutes les campagnes.')}</p>
      <fieldset className="checklist">
        <legend>{t('MJ ayant accès')}</legend>
        {mjs.length === 0 && <p className="muted">{t('Aucun compte MJ. Créez-en un dans la section Comptes.')}</p>}
        {mjs.map((u) => (
          <label key={u.id} className="check">
            <input type="checkbox" checked={selected.includes(u.id)} onChange={() => toggle(u.id)} />
            {u.display_name || u.username} <span className="muted">({u.username})</span>
          </label>
        ))}
      </fieldset>
    </Modal>
  );
}

// --- Sauvegardes -----------------------------------------------------------------

// Origine d'une sauvegarde (nature dans le nom du fichier) ; traduite à l'affichage.
const BACKUP_KIND = {
  'avant-restauration': N_('Avant restauration'), import: N_('Importée'), manuelle: N_('Manuelle'),
  maj: N_('Avant mise à jour'),
};

/** Rotation de chaque origine (MAZE_BACKUP_KEEP, 0 : toutes gardées) : « 7 quotidiennes, toutes les manuelles… ». */
function rotation(keep) {
  return formatList([
    keep.quotidienne ? tn('{n} quotidienne', '{n} quotidiennes', keep.quotidienne) : t('toutes les quotidiennes'),
    keep.manuelle ? tn('{n} manuelle', '{n} manuelles', keep.manuelle) : t('toutes les manuelles'),
    keep.maj ? tn('{n} d’avant mise à jour', '{n} d’avant mise à jour', keep.maj) : t('toutes celles d’avant mise à jour'),
    keep['avant-restauration']
      ? tn('{n} d’avant restauration', '{n} d’avant restauration', keep['avant-restauration'])
      : t('toutes celles d’avant restauration'),
    keep.import ? tn('{n} importée', '{n} importées', keep.import) : t('toutes les importées'),
  ]);
}

function BackupsSection() {
  const [backups, setBackups] = useState(null);
  const [usage, setUsage] = useState(null);     // place des archives et espace libre du volume
  const [busy, setBusy] = useState(null);       // 'create' | 'export' | 'restore'
  const [upload, setUpload] = useState(null);   // progression de l'import (0..1)
  const fileRef = useRef(null);
  const run = useAction();
  const confirm = useConfirm();
  const toast = useToast();

  const load = useCallback(async () => {
    const list = await run(() => api.admin.backups());
    if (list) setBackups(list);
    const space = await run(() => api.admin.backupsUsage());
    if (space) setUsage(space);
  }, [run]);
  useEffect(() => { load(); }, [load]);

  const create = async () => {
    setBusy('create');
    await run(() => api.admin.createBackup(), t('Sauvegarde créée'));
    setBusy(null);
    load();
  };

  /** Export : sauvegarde de l'état actuel, téléchargée aussitôt. */
  const exportNow = async () => {
    setBusy('export');
    const entry = await run(() => api.admin.createBackup());
    setBusy(null);
    if (!entry) return;
    load();
    const link = document.createElement('a');
    link.href = api.admin.backupUrl(entry.name);
    link.download = entry.name;
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  const importFile = async (file) => {
    if (!file) return;
    setUpload(0);
    try {
      const entry = await api.admin.importBackup(file, setUpload);
      toast(t('Archive importée : {campaigns}, {users}. Elle peut maintenant être restaurée.', {
        campaigns: tn('{n} campagne', '{n} campagnes', entry.campaigns),
        users: tn('{n} compte', '{n} comptes', entry.users),
      }), 'success');
      load();
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      setUpload(null);
    }
  };

  const restore = async (b) => {
    const yes = await confirm({
      title: t('Restaurer cette sauvegarde'),
      message: t('Toutes les données actuelles (campagnes, fiches, images, comptes) seront remplacées par celles de {name}. '
        + 'L’état actuel est d’abord sauvegardé automatiquement (archive « avant-restauration ») pour pouvoir revenir en arrière. '
        + 'Les comptes présents dans les deux gardent leur mot de passe, leur rôle et leur état (actif ou désactivé) d’aujourd’hui ; '
        + 'ceux de la sauvegarde supprimés depuis reviennent désactivés. '
        + 'Toutes les sessions seront fermées : les autres MJ devront se reconnecter, vous restez connecté.', { name: b.name }),
      confirmLabel: t('Restaurer'),
    });
    if (!yes) return;
    setBusy('restore');
    const result = await run(() => api.admin.restoreBackup(b.name));
    setBusy(null);
    if (!result) return;
    if (result.disabled?.length) {
      // Comptes de l'archive supprimés depuis (sans équivalent aujourd'hui) : restaurés désactivés, à réactiver au besoin.
      await confirm({
        title: t('Sauvegarde restaurée'),
        message: t('État précédent conservé dans {name}. Les autres sessions sont fermées. '
          + 'Comptes de la sauvegarde absents d’aujourd’hui, restaurés désactivés : {accounts}. '
          + 'Réactivez-les dans Comptes s’ils doivent revenir.', { name: result.safety.name, accounts: result.disabled.join(', ') }),
        confirmLabel: t('Recharger'),
      });
      window.location.reload();
      return;
    }
    toast(t('Sauvegarde restaurée. État précédent conservé dans {name}. Les autres sessions sont fermées.', { name: result.safety.name }),
      'success');
    setTimeout(() => window.location.reload(), 1800);
  };

  const remove = async (b) => {
    const yes = await confirm({ title: t('Supprimer la sauvegarde'), message: t('{name} sera supprimée du serveur.', { name: b.name }) });
    if (!yes) return;
    await run(() => api.admin.deleteBackup(b.name), t('Sauvegarde supprimée'));
    load();
  };

  /** Origine d'après le nom (maze-AAAAMMJJ-HHMMSS[-nature][-N].tar.gz) ; chaque origine tourne à part. */
  const kind = (name) => {
    const nature = (name.match(/^maze-\d{8}-\d{6}-([a-z]+(?:-[a-z]+)*?)(?:-\d+)?\.tar\.gz$/) || [])[1];
    if (BACKUP_KIND[nature]) return t(BACKUP_KIND[nature]);
    return nature || t('Quotidienne');
  };

  const guide = t('Voir docs/fr/guide.md, section Sauvegarde et restauration.');
  const files = (m) => ({ size: fmtSize(m.size), date: fmtDate(m.modified) });

  return (
    <>
      <SectionHead title={t('Sauvegardes')}
        description={usage
          ? t('Archive de la base et des images, stockée sur le serveur dans le volume de données. Chaque origine a sa propre rotation : '
            + '{rotation} au plus (variable MAZE_BACKUP_KEEP). Exportez une archive pour la garder ailleurs ; importez-en une pour la '
            + 'restaurer ici ou sur une autre instance de Maze.', { rotation: rotation(usage.keep) })
          : t('Archive de la base et des images, stockée sur le serveur dans le volume de données. Chaque origine a sa propre rotation. '
            + 'Exportez une archive pour la garder ailleurs ; importez-en une pour la restaurer ici ou sur une autre instance de Maze.')}>
        <button type="button" className="btn btn--ghost" onClick={() => fileRef.current?.click()} disabled={busy !== null || upload !== null}>
          <Icon name="upload" size={16} /> {upload !== null ? t('Envoi… {percent} %', { percent: Math.round(upload * 100) }) : t('Importer')}
        </button>
        <button type="button" className="btn btn--ghost" onClick={exportNow} disabled={busy !== null}>
          <Icon name="download" size={16} /> {busy === 'export' ? t('Préparation…') : t('Exporter')}
        </button>
        <button type="button" className="btn btn--primary" onClick={create} disabled={busy !== null}>
          <Icon name="archive" size={16} /> {busy === 'create' ? t('Sauvegarde…') : t('Sauvegarder maintenant')}
        </button>
        <input ref={fileRef} type="file" accept=".gz,.tgz,application/gzip" hidden
          onChange={(e) => { importFile(e.target.files[0]); e.target.value = ''; }} />
      </SectionHead>
      {busy === 'restore' && (
        <div className="notice notice--busy"><span>{t('Restauration en cours, ne fermez pas la page…')}</span></div>
      )}
      {usage && (
        <p className="muted">
          {tn('{n} archive, {total} au total · {free} libres sur le volume', '{n} archives, {total} au total · {free} libres sur le volume',
            usage.count, { total: fmtSize(usage.total), free: fmtSize(usage.free) })}
          {usage.last_daily && <> · {t('dernière sauvegarde quotidienne : {date}', { date: fmtDate(usage.last_daily) })}</>}
        </p>
      )}
      {usage && usage.daily_stale && (
        <div className="notice notice--warning">
          <span>{usage.last_daily
            ? t('Dernière sauvegarde quotidienne le {date}, il y a plus de 48 h : la sauvegarde planifiée (timer maze-backup) ne tourne peut-être plus.',
              { date: fmtDate(usage.last_daily) })
            : t('Aucune sauvegarde quotidienne (archive sans suffixe) : la sauvegarde planifiée (timer maze-backup) n’est peut-être pas installée.')}
            {' '}{guide}</span>
        </div>
      )}
      {/* Images d'avant une restauration interrompue (audit PERF-15) : jamais supprimées par Maze, marche à suivre du
          guide (docs/fr/guide.md) ; plus fort si media/ manque (ce sont peut-être les seules images). */}
      {(usage?.replaced_media || []).map((m) => (
        <div key={m.name} className={`notice ${m.media_missing ? 'notice--danger' : 'notice--warning'}`}
          role={m.media_missing ? 'alert' : undefined} data-replaced-media={m.name}>
          <span>
            {m.media_missing ? (
              <>
                <strong>{t('Images de Maze absentes.')}</strong>{' '}
                {tn('Le dossier {media} manque ou est vide et {folder} ({n} fichier, {size}, {date}) contient les images '
                  + 'd’avant une restauration interrompue : ce sont peut-être les seules. Remettez-le en place avant toute '
                  + 'sauvegarde ou restauration (les dossiers vides de {media} sont retirés, rien n’est déplacé s’il '
                  + 'contient un fichier) : {command}.',
                'Le dossier {media} manque ou est vide et {folder} ({n} fichiers, {size}, {date}) contient les images '
                  + 'd’avant une restauration interrompue : ce sont peut-être les seules. Remettez-le en place avant toute '
                  + 'sauvegarde ou restauration (les dossiers vides de {media} sont retirés, rien n’est déplacé s’il '
                  + 'contient un fichier) : {command}.', m.files, {
                  ...files(m),
                  media: <code>/data/media</code>,
                  folder: <code>/data/{m.name}</code>,
                  command: <code>{`docker compose exec maze sh -c 'find /data/media -depth -type d -empty -delete; test ! -e /data/media && mv /data/${m.name} /data/media'`}</code>,
                })}
              </>
            ) : (
              tn('Images d’avant une restauration interrompue gardées dans {folder} ({n} fichier, {size}, {date}) : Maze ne les '
                + 'supprime jamais. Vérifiez que les images des fiches sont toutes présentes, puis supprimez ce dossier à la main : {command}.',
              'Images d’avant une restauration interrompue gardées dans {folder} ({n} fichiers, {size}, {date}) : Maze ne les '
                + 'supprime jamais. Vérifiez que les images des fiches sont toutes présentes, puis supprimez ce dossier à la main : {command}.',
              m.files, {
                ...files(m),
                folder: <code>/data/{m.name}</code>,
                command: <code>docker compose exec maze rm -r /data/{m.name}</code>,
              })
            )}
            {' '}{guide}
          </span>
        </div>
      ))}
      {/* Images d'une restauration arrêtée net pendant leur remplacement, que Maze n'a pas remises en place au
          démarrage : la base en service n'est pas celle de cette sauvegarde (contre-revue du lot 6). */}
      {(usage?.interrupted_restores || []).map((m) => (
        <div key={m.name} className={`notice ${m.media_missing ? 'notice--danger' : 'notice--warning'}`}
          role={m.media_missing ? 'alert' : undefined} data-interrupted-restore={m.name}>
          <span>
            <strong>{t('Restauration interrompue pendant le remplacement des images.')}</strong>{' '}
            {tn('Les images de la sauvegarde restaurée sont gardées dans {folder} ({n} fichier, {size}, {date}), mais la base en '
              + 'service n’est pas celle de cette sauvegarde : Maze ne les a pas remises en place.',
            'Les images de la sauvegarde restaurée sont gardées dans {folder} ({n} fichiers, {size}, {date}), mais la base en '
              + 'service n’est pas celle de cette sauvegarde : Maze ne les a pas remises en place.',
            m.files, { ...files(m), folder: <code>/data/{m.name}/media</code> })}
            {m.media_missing && (
              <>
                {' '}
                {usage.replaced_media?.length
                  ? t('Les images de la base en service sont normalement celles du dossier .media-remplace signalé ci-dessus : remettez-le en place.')
                  : t('Les images de la base en service sont dans la dernière archive « -avant-restauration » : restaurez-la.')}
              </>
            )}
            {' '}{t('Une fois les images des fiches vérifiées, supprimez ce dossier : {command}.',
              { command: <code>docker compose exec maze rm -r /data/{m.name}</code> })}
            {' '}{guide}
          </span>
        </div>
      ))}
      {usage && usage.free < usage.needed && (
        <div className="notice notice--warning">
          <span>{t('Espace insuffisant pour une nouvelle sauvegarde ou une restauration : {size} nécessaires (deux fois la dernière '
            + 'archive, plus une marge). Supprimez d’anciennes archives ou agrandissez le volume.', { size: fmtSize(usage.needed) })}</span>
        </div>
      )}
      {!backups ? <p className="muted">{t('Chargement…')}</p> : backups.length === 0 ? (
        <p className="muted">{t('Aucune sauvegarde pour l’instant.')}</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('Date')}</th><th>{t('Fichier')}</th><th>{t('Origine')}</th><th>{t('Taille')}</th>
                <th><span className="sr-only">{t('Actions')}</span></th>
              </tr>
            </thead>
            <tbody>
              {backups.map((b) => (
                <tr key={b.name}>
                  <td>{fmtDate(b.created_at)}</td>
                  <td className="table__mono">{b.name}</td>
                  <td>{kind(b.name)}</td>
                  <td>{fmtSize(b.size)}</td>
                  <td className="table__actions">
                    <a className="btn btn--small btn--ghost" href={api.admin.backupUrl(b.name)} download>
                      <Icon name="download" size={14} /> {t('Télécharger')}
                    </a>
                    <button type="button" className="btn btn--small btn--ghost" disabled={busy !== null}
                      onClick={() => restore(b)}>
                      <Icon name="refresh" size={14} /> {t('Restaurer')}
                    </button>
                    <button type="button" className="icon-btn icon-btn--small icon-btn--danger" title={t('Supprimer')}
                      aria-label={t('Supprimer {name}', { name: b.name })} onClick={() => remove(b)}>
                      <Icon name="trash" size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

// --- Mise à jour -------------------------------------------------------------------

// États du service de mise à jour ; traduits à l'affichage.
const STATE_LABEL = {
  idle: N_('En attente'),
  checking: N_('Vérification en cours…'),
  updating: N_('Mise à jour en cours…'),
  success: N_('Dernière mise à jour réussie'),
  failed: N_('Dernière mise à jour en échec'),
  rolledback: N_('Dernière mise à jour annulée : retour à la version précédente'),
  interrupted: N_('Opération interrompue'),
};

// Hash abrégés de longueurs différentes (MAZE_VERSION et git) : même commit si l'un commence par l'autre.
const sameCommit = (a, b) => Boolean(a && b) && (a.startsWith(b) || b.startsWith(a));

function UpdatesSection() {
  const [status, setStatus] = useState(null);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState(null);
  const startVersion = useRef(null);
  const toast = useToast();
  const confirm = useConfirm();

  const load = useCallback(async () => {
    try {
      const s = await api.admin.system();
      setStatus(s);
      setOffline(false);
      setError(null);
      if (startVersion.current === null) startVersion.current = s.version;
      return s;
    } catch (err) {
      // Pendant la reconstruction, le serveur redémarre : l'API ne répond plus quelques instants.
      if (err.status === 0 || err.status >= 500) setOffline(true);
      else setError(err.message);
      return null;
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const up = status?.updater;
  const busy = offline || up?.pending || up?.state === 'checking' || up?.state === 'updating';
  const newVersion = status && startVersion.current && status.version !== startVersion.current;

  // Rafraîchissement automatique tant qu'une opération est en cours.
  useEffect(() => {
    if (!busy) return undefined;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [busy, load]);

  const request = async (action) => {
    if (action === 'update') {
      const yes = await confirm({
        title: t('Mettre à jour Maze'),
        message: t('Une sauvegarde est faite, puis le serveur reconstruit et redémarre l’application. Maze sera indisponible une à deux minutes.'),
        confirmLabel: t('Lancer la mise à jour'),
      });
      if (!yes) return;
    }
    try {
      await (action === 'update' ? api.admin.runUpdate() : api.admin.checkUpdates());
      toast(action === 'update' ? t('Mise à jour demandée') : t('Vérification demandée'), 'success');
      load();
    } catch (err) {
      toast(err.message, 'error');
    }
  };

  if (error) return <p className="form-error">{error}</p>;
  if (!status) return <p className="muted">{t('Chargement…')}</p>;

  return (
    <>
      <SectionHead title={t('Mise à jour')} description={t('Récupère la dernière version publiée sur GitHub, la reconstruit et redémarre Maze sur le serveur.')}>
        <button type="button" className="btn btn--ghost" onClick={() => request('check')} disabled={!up?.installed || busy}>
          <Icon name="refresh" size={16} /> {t('Vérifier')}
        </button>
        <button type="button" className="btn btn--primary" onClick={() => request('update')} disabled={!up?.installed || busy}>
          <Icon name="download" size={16} /> {t('Mettre à jour')}
        </button>
      </SectionHead>

      {newVersion && (
        <div className="notice notice--success">
          <span>{t('Nouvelle version installée ({version}). Rechargez la page pour utiliser la nouvelle interface.', { version: status.version })}</span>
          <button type="button" className="btn btn--small btn--primary" onClick={() => window.location.reload()}>{t('Recharger')}</button>
        </div>
      )}
      {offline && (
        <div className="notice notice--busy" role="status">
          <span className="spinner" aria-hidden="true" /> {t('Redémarrage du serveur en cours… la page se reconnectera automatiquement.')}
        </div>
      )}
      {!busy && (up?.state === 'failed' || up?.state === 'rolledback' || up?.state === 'interrupted') && (
        <div className="notice notice--warning" role="alert" data-update-outcome={up.state}>
          <span>
            <strong>{t(STATE_LABEL[up.state])}.</strong> {up.message} {t('Version en service : {version}.', { version: status.version })}
          </span>
        </div>
      )}
      {!busy && up?.installed && up.current && status.version !== 'dev' && !sameCommit(up.current, status.version) && (
        <div className="notice notice--warning" data-version-mismatch="">
          <span>{t('Le code sur le serveur ({current}) n’est pas la version en service ({version}) : la dernière mise à jour n’a '
            + 'pas abouti. « Mettre à jour » reconstruit la version en attente.', { current: up.current, version: status.version })}</span>
        </div>
      )}
      {status.untrusted_proxy && (
        <div className="notice notice--warning">
          <span>{t('Maze reçoit des requêtes HTTPS relayées par {proxy}, dont il ne croit pas les en-têtes : toutes les connexions '
            + 'semblent venir de cette adresse. Sur le serveur, ajouter FORWARDED_ALLOW_IPS={proxy} dans .env, puis docker compose '
            + 'up -d (README, Configuration).', { proxy: status.untrusted_proxy })}</span>
        </div>
      )}

      <dl className="facts">
        <div><dt>{t('Version en service')}</dt><dd className="table__mono">{status.version}</dd></div>
        {up?.installed && (
          <>
            <div><dt>{t('Code sur le serveur')}</dt><dd className="table__mono">{up.current || t('inconnu')}</dd></div>
            <div><dt>{t('Dernière version publiée')}</dt><dd className="table__mono">{up.available || t('inconnue')}</dd></div>
            <div><dt>{t('Dernière vérification')}</dt><dd>{fmtDate(up.checked_at)}</dd></div>
          </>
        )}
      </dl>

      {!up?.installed ? (
        <div className="notice">
          <span>
            {t('Le service de mise à jour n’est pas installé sur le serveur. Une fois installé (voir docs/fr/guide.md, section '
              + '« Mise à jour depuis l’interface »), ces boutons deviennent actifs.')}
          </span>
        </div>
      ) : (
        <>
          <section className="admin__block">
            {(() => {
              // Le message enregistré peut dater d'avant la dernière mise à jour : l'état réel prime.
              const upToDate = up.current && up.available && up.current === up.available && up.commits.length === 0;
              // check_failed : repère posé par le serveur, quelle que soit la langue du message (traduit). Repli pour
              // un serveur qui ne le fournit pas : message français du service (« Échec de la vérification : … »).
              const failed = typeof up.check_failed === 'boolean'
                ? up.check_failed
                : Boolean(up.check_message?.startsWith('Échec')); // i18n-ignore (message du service de mise à jour)
              return (
                <>
                  <h2>
                    <span className={`state-dot state-dot--${upToDate ? 'success' : 'available'}`} aria-hidden="true" />
                    {upToDate ? t('Maze est à jour') : t('Modifications disponibles')}
                  </h2>
                  {failed && <p className="form-error">{up.check_message}</p>}
                  {upToDate
                    ? <p className="muted">{t('Version {version} : aucune modification en attente sur GitHub.', { version: up.current })}</p>
                    : up.commits.length > 0 && (
                      <p className="muted">{tn('{n} modification disponible.', '{n} modifications disponibles.', up.commits.length)}</p>
                    )}
                </>
              );
            })()}
            {up.commits.length > 0 && (
              <ul className="commit-list">
                {up.commits.map((c) => (
                  <li key={c.hash}>
                    <span className="table__mono">{c.hash}</span>
                    <span className="commit-list__subject">{c.subject}</span>
                    <span className="muted">{fmtDate(c.date)}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="admin__block">
            <h2>
              <span className={`state-dot state-dot--${up.pending ? 'checking' : up.state}`} aria-hidden="true" />
              {up.pending ? t('Demande transmise au serveur…') : (STATE_LABEL[up.state] ? t(STATE_LABEL[up.state]) : up.state)}
            </h2>
            {up.message && <p>{up.message}</p>}
            {up.finished_at && <p className="muted">{t('Terminée le {date}', { date: fmtDate(up.finished_at) })}</p>}
            {up.log && (
              <details className="log" open={['failed', 'rolledback', 'updating', 'interrupted'].includes(up.state)}>
                <summary>{t('Journal de la dernière mise à jour')}</summary>
                <pre>{up.log}</pre>
              </details>
            )}
          </section>
        </>
      )}
    </>
  );
}
