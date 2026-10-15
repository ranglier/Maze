import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import { Field, Icon, Modal, useAction, useConfirm, useToast } from './ui.jsx';
import { SectionHead, fmtDate } from './AdminPage.jsx';
import { CopyField } from './ClaudeSection.jsx';

/** Cases à cocher des campagnes lisibles avec un jeton. */
function CampaignChecks({ campaigns, selected, onChange }) {
  const toggle = (id) => onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  return (
    <fieldset className="checklist">
      <legend>Campagnes lisibles avec ce jeton</legend>
      {campaigns.length === 0 && <p className="muted">Aucune campagne pour l’instant.</p>}
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
      token ? 'Jeton modifié' : undefined);
    setBusy(false);
    if (out) onSaved(out);
  };
  return (
    <Modal title={token ? `Jeton « ${token.name} »` : 'Nouveau jeton Foundry'} onClose={onClose} footer={(
      <>
        <button type="button" className="btn btn--ghost" onClick={onClose}>Annuler</button>
        <button type="button" className="btn btn--primary" onClick={save} disabled={busy || !name.trim()}>
          {token ? 'Enregistrer' : 'Créer le jeton'}
        </button>
      </>
    )}>
      <form onSubmit={save}>
        <Field label="Nom" htmlFor="foundry-token-name" hint="Pour reconnaître le jeton : le nom du serveur ou du monde Foundry.">
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
      toast('Jeton copié', 'success');
    } catch {
      toast('Copie impossible : sélectionnez le jeton et copiez-le avec Ctrl+C.', 'error');
    }
  };
  return (
    <Modal title={`Jeton « ${created.name} » créé`} onClose={onClose} footer={(
      <>
        <button type="button" className="btn btn--ghost" onClick={copy}><Icon name="copy" size={16} /> Copier</button>
        <button type="button" className="btn btn--primary" onClick={onClose}>J’ai copié le jeton</button>
      </>
    )}>
      <Field label="Jeton" htmlFor="foundry-new-token"
        hint="Copiez-le maintenant : Maze n’en garde qu’une empreinte, il ne sera plus jamais affiché. Collez-le dans la fenêtre « Importer depuis Maze » du module Foundry.">
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
      title: 'Révoquer le jeton',
      message: `Le module Foundry qui utilise « ${token.name} » ne pourra plus lire Maze. Ce qui a déjà été importé `
        + 'reste dans Foundry.',
      confirmLabel: 'Révoquer',
    });
    if (!yes) return;
    const out = await run(() => api.admin.deleteFoundryToken(token.id), 'Jeton révoqué');
    if (out) {
      setState(out);
      if (created?.id === token.id) setCreated(null);
    }
  };

  if (!state) return <p className="muted">Chargement…</p>;

  return (
    <>
      <SectionHead title="Foundry VTT"
        description="Le module « Maze - import des fiches » importe dans un monde Foundry les PNJ, factions, lieux et cartes interactives d’une campagne : journaux, scènes avec notes, acteurs (Dark Heresy). Il lit Maze avec un jeton, en lecture seule ; rien ne repart de Foundry vers Maze.">
        <button type="button" className="btn btn--primary" onClick={() => setDialog({ type: 'create' })}>
          <Icon name="plus" size={16} /> Nouveau jeton
        </button>
      </SectionHead>

      <section className="admin__block">
        <h2>Installer le module dans Foundry</h2>
        <dl className="facts claude-facts">
          <CopyField label="Adresse du manifeste" value={state.manifest_url} />
          <CopyField label="Adresse de Maze (à saisir dans le module)" value={state.maze_url} />
        </dl>
        <ol className="claude-steps">
          <li>Écran de configuration de Foundry : <strong>Modules complémentaires &gt; Installer un module</strong>, coller l’adresse du manifeste en bas de la fenêtre, puis <strong>Installer</strong>. Les mises à jour du module suivent celles de Maze (version {state.module_version}).</li>
          <li>Dans le monde : <strong>Gérer les modules</strong>, activer « Maze - import des fiches ».</li>
          <li>Onglet <strong>Journaux &gt; Maze</strong> (ou Paramètres &gt; Maze) : saisir l’adresse de Maze et le jeton, <strong>Se connecter</strong>, choisir la campagne, <strong>Importer</strong>. Un nouvel import met à jour ce qui a changé.</li>
        </ol>
        <p className="field__hint">
          Le serveur Foundry télécharge le module à cette adresse : s’il est sur le même réseau que Maze et que le nom
          public ne s’y résout pas, ajoutez-le au conteneur Foundry (extra_hosts de Docker Compose, vers l’adresse du
          reverse proxy).
        </p>
      </section>

      <section className="admin__block">
        <h2>Jetons</h2>
        <p className="field__hint">Un jeton n’est affiché qu’à sa création. Perdu : révoquez-le et créez-en un autre. Il est révoqué d’office si l’administrateur qui l’a créé perd ce rôle, est désactivé ou supprimé, et à la restauration d’une sauvegarde.</p>
        {state.tokens.length === 0 ? (
          <p className="muted">Aucun jeton. Créez-en un pour chaque serveur Foundry.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Nom</th><th>Campagnes</th><th>Créé le</th><th>Dernière utilisation</th><th /></tr></thead>
              <tbody>
                {state.tokens.map((t) => (
                  <tr key={t.id}>
                    <td>
                      <strong>{t.name}</strong>
                      <span className="table__sub table__mono">mzf_{t.hint}…</span>
                    </td>
                    <td className="table__campaigns">
                      {t.campaigns.length === 0
                        ? <span className="muted">Aucune</span>
                        : t.campaigns.map((id) => names.get(id) || `#${id}`).join(', ')}
                    </td>
                    <td className="table__nums">{fmtDate(t.created_at)}{t.created_by ? ` (${t.created_by})` : ''}</td>
                    <td className="table__nums">{fmtDate(t.last_used_at)}</td>
                    <td className="table__actions">
                      <button type="button" className="icon-btn" title="Nom et campagnes" aria-label={`Modifier ${t.name}`}
                        onClick={() => setDialog({ type: 'edit', token: t })}>
                        <Icon name="edit" size={16} />
                      </button>
                      <button type="button" className="icon-btn" title="Révoquer" aria-label={`Révoquer ${t.name}`}
                        onClick={() => revoke(t)}>
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
            if (dialog.type === 'create') setCreated({ id: out.token_id, name: out.tokens.find((t) => t.id === out.token_id)?.name, token: out.token });
            setDialog(null);
          }} />
      )}
    </>
  );
}
