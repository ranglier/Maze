import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import { Field, Icon, Modal, useAction, useConfirm, useToast } from './ui.jsx';
import { SectionHead, fmtDate } from './AdminPage.jsx';
import { CopyField } from './ClaudeSection.jsx';
import { t } from '../i18n/index.js';

/** Cases à cocher des campagnes lisibles avec un jeton. */
function CampaignChecks({ campaigns, selected, onChange }) {
  const toggle = (id) => onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  return (
    <fieldset className="checklist">
      <legend>{t('Campagnes lisibles avec ce jeton')}</legend>
      {campaigns.length === 0 && <p className="muted">{t('Aucune campagne pour l’instant.')}</p>}
      {campaigns.map((c) => (
        <label key={c.id} className="check">
          <input type="checkbox" checked={selected.includes(c.id)} onChange={() => toggle(c.id)} />
          {c.name}
        </label>
      ))}
    </fieldset>
  );
}

function TokenDialog({ token, campaigns, onClose, onSaved }) {
  const run = useAction();
  const [name, setName] = useState(token?.name || '');
  const [selected, setSelected] = useState(token?.campaigns || []);
  const [busy, setBusy] = useState(false);
  const save = async (e) => {
    e?.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    const data = { name: name.trim(), campaigns: selected };
    const out = await run(() => (token ? api.admin.updateFoundryToken(token.id, data) : api.admin.createFoundryToken(data)),
      token ? t('Jeton modifié') : undefined);
    setBusy(false);
    if (out) onSaved(out);
  };
  return (
    <Modal title={token ? t('Jeton « {name} »', { name: token.name }) : t('Nouveau jeton Foundry')} onClose={onClose} footer={(
      <>
        <button type="button" className="btn btn--ghost" onClick={onClose}>{t('Annuler')}</button>
        <button type="button" className="btn btn--primary" onClick={save} disabled={busy || !name.trim()}>
          {token ? t('Enregistrer') : t('Créer le jeton')}
        </button>
      </>
    )}>
      <form onSubmit={save}>
        <Field label={t('Nom')} htmlFor="foundry-token-name" hint={t('Pour reconnaître le jeton : le nom du serveur ou du monde Foundry.')}>
          <input id="foundry-token-name" className="input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
        </Field>
        <CampaignChecks campaigns={campaigns || []} selected={selected} onChange={setSelected} />
      </form>
    </Modal>
  );
}

/** Jeton tout juste créé : montré une seule fois, dans une fenêtre qu'on ne peut pas manquer. */
function CreatedTokenDialog({ created, onClose }) {
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created.token);
      toast(t('Jeton copié'), 'success');
    } catch {
      toast(t('Copie impossible : sélectionnez le jeton et copiez-le avec Ctrl+C.'), 'error');
    }
  };
  return (
    <Modal title={t('Jeton « {name} » créé', { name: created.name })} onClose={onClose} footer={(
      <>
        <button type="button" className="btn btn--ghost" onClick={copy}><Icon name="copy" size={16} /> {t('Copier')}</button>
        <button type="button" className="btn btn--primary" onClick={onClose}>{t('J’ai copié le jeton')}</button>
      </>
    )}>
      <Field label={t('Jeton')} htmlFor="foundry-new-token"
        hint={t('Copiez-le maintenant : Maze n’en garde qu’une empreinte, il ne sera plus jamais affiché. Collez-le dans la fenêtre « Importer depuis Maze » du module Foundry.')}>
        <input id="foundry-new-token" className="input table__mono" readOnly value={created.token}
          onFocus={(e) => e.target.select()} onClick={(e) => e.target.select()} />
      </Field>
    </Modal>
  );
}

export default function FoundrySection({ campaigns }) {
  const [state, setState] = useState(null);
  const [created, setCreated] = useState(null);   // jeton montré une seule fois, juste après sa création
  const [dialog, setDialog] = useState(null);
  const run = useAction();
  const confirm = useConfirm();

  const load = useCallback(async () => {
    const out = await run(() => api.admin.foundry());
    if (out) setState(out);
  }, [run]);
  useEffect(() => { load(); }, [load]);

  const names = new Map((campaigns || []).map((c) => [c.id, c.name]));

  const revoke = async (token) => {
    const yes = await confirm({
      title: t('Révoquer le jeton'),
      message: t('Le module Foundry qui utilise « {name} » ne pourra plus lire Maze. Ce qui a déjà été importé reste dans Foundry.',
        { name: token.name }),
      confirmLabel: t('Révoquer'),
    });
    if (!yes) return;
    const out = await run(() => api.admin.deleteFoundryToken(token.id), t('Jeton révoqué'));
    if (out) {
      setState(out);
      if (created?.id === token.id) setCreated(null);
    }
  };

  if (!state) return <p className="muted">{t('Chargement…')}</p>;

  return (
    <>
      <SectionHead title="Foundry VTT"
        description={t('Le module « Maze - import des fiches » importe dans un monde Foundry les PNJ, factions, lieux et cartes interactives d’une campagne : journaux, scènes avec notes, acteurs (Dark Heresy). Il lit Maze avec un jeton, en lecture seule ; rien ne repart de Foundry vers Maze.')}>
        <button type="button" className="btn btn--primary" onClick={() => setDialog({ type: 'create' })}>
          <Icon name="plus" size={16} /> {t('Nouveau jeton')}
        </button>
      </SectionHead>

      <section className="admin__block">
        <h2>{t('Installer le module dans Foundry')}</h2>
        <dl className="facts claude-facts">
          <CopyField label={t('Adresse du manifeste')} value={state.manifest_url} />
          <CopyField label={t('Adresse de Maze (à saisir dans le module)')} value={state.maze_url} />
        </dl>
        <ol className="claude-steps">
          <li>{t('Écran de configuration de Foundry : {menu}, coller l’adresse du manifeste en bas de la fenêtre, puis {install}. Les mises à jour du module suivent celles de Maze (version {version}).', {
            menu: <strong>{t('Modules complémentaires > Installer un module')}</strong>,
            install: <strong>{t('Installer')}</strong>,
            version: state.module_version,
          })}</li>
          <li>{t('Dans le monde : {manage}, activer « Maze - import des fiches ».', { manage: <strong>{t('Gérer les modules')}</strong> })}</li>
          <li>{t('Onglet {tab} (ou Paramètres > Maze) : saisir l’adresse de Maze et le jeton, {connect}, choisir la campagne, {import}. Un nouvel import met à jour ce qui a changé.', {
            tab: <strong>{t('Journaux > Maze')}</strong>,
            connect: <strong>{t('Se connecter')}</strong>,
            import: <strong>{t('Importer')}</strong>,
          })}</li>
        </ol>
        <p className="field__hint">
          {t('Le serveur Foundry télécharge le module à cette adresse : s’il est sur le même réseau que Maze et que le nom public ne s’y résout pas, ajoutez-le au conteneur Foundry (extra_hosts de Docker Compose, vers l’adresse du reverse proxy).')}
        </p>
      </section>

      <section className="admin__block">
        <h2>{t('Jetons')}</h2>
        <p className="field__hint">{t('Un jeton n’est affiché qu’à sa création. Perdu : révoquez-le et créez-en un autre. Il est révoqué d’office si l’administrateur qui l’a créé perd ce rôle, est désactivé ou supprimé, et à la restauration d’une sauvegarde.')}</p>
        {state.tokens.length === 0 ? (
          <p className="muted">{t('Aucun jeton. Créez-en un pour chaque serveur Foundry.')}</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>{t('Nom')}</th><th>{t('Campagnes')}</th><th>{t('Créé le')}</th><th>{t('Dernière utilisation')}</th><th /></tr></thead>
              <tbody>
                {state.tokens.map((tok) => (
                  <tr key={tok.id}>
                    <td>
                      <strong>{tok.name}</strong>
                      <span className="table__sub table__mono">mzf_{tok.hint}…</span>
                    </td>
                    <td className="table__campaigns">
                      {tok.campaigns.length === 0
                        ? <span className="muted">{t('Aucune')}</span>
                        : tok.campaigns.map((id) => names.get(id) || `#${id}`).join(', ')}
                    </td>
                    <td className="table__nums">
                      {tok.created_by ? t('{date} ({name})', { date: fmtDate(tok.created_at), name: tok.created_by }) : fmtDate(tok.created_at)}
                    </td>
                    <td className="table__nums">{fmtDate(tok.last_used_at)}</td>
                    <td className="table__actions">
                      <button type="button" className="icon-btn" title={t('Nom et campagnes')} aria-label={t('Modifier {name}', { name: tok.name })}
                        onClick={() => setDialog({ type: 'edit', token: tok })}>
                        <Icon name="edit" size={16} />
                      </button>
                      <button type="button" className="icon-btn" title={t('Révoquer')} aria-label={t('Révoquer {name}', { name: tok.name })}
                        onClick={() => revoke(tok)}>
                        <Icon name="trash" size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {created && <CreatedTokenDialog created={created} onClose={() => setCreated(null)} />}

      {dialog && (
        <TokenDialog token={dialog.token} campaigns={campaigns} onClose={() => setDialog(null)}
          onSaved={(out) => {
            setState(out);
            if (dialog.type === 'create') setCreated({ id: out.token_id, name: out.tokens.find((x) => x.id === out.token_id)?.name, token: out.token });
            setDialog(null);
          }} />
      )}
    </>
  );
}
