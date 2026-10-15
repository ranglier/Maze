import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import { Icon, useAction, useConfirm, useToast } from './ui.jsx';
import { SectionHead, fmtDate } from './AdminPage.jsx';

const MODES = [
  { key: 'none', label: 'Aucun' },
  { key: 'read', label: 'Lecture' },
  { key: 'write', label: 'Lecture et écriture' },
];

const TOOL_LABEL = {
  list_campaigns: 'Liste des campagnes',
  search: 'Recherche',
  get_campaign: 'Vue d’ensemble',
  get_entry: 'Lecture d’une fiche',
  list_relations: 'Liste des liens',
  get_document: 'Lecture d’une fiche rédigée',
  create_entry: 'Création d’une fiche',
  update_entry: 'Modification d’une fiche',
  create_relation: 'Création d’un lien',
  update_relation: 'Modification d’un lien',
  delete_relation: 'Suppression d’un lien',
  set_membership: 'Appartenance à une faction',
  remove_membership: 'Retrait d’une faction',
  set_place_link: 'Lien avec un lieu',
  remove_place_link: 'Retrait d’un lieu',
  create_document: 'Création d’une fiche rédigée',
  update_document: 'Modification d’une fiche rédigée',
  add_comment: 'Commentaire',
  view_image: 'Lecture d’une image',
  add_image: 'Ajout d’une image',
  update_image: 'Modification d’une image',
  edit_image: 'Retouche d’une image',
  delete_image: 'Suppression d’une image',
  add_document_image: 'Illustration d’une fiche rédigée',
  list_library: 'Lecture de la bibliothèque',
  delete_library_image: 'Retrait de la bibliothèque',
  list_maps: 'Lecture des cartes interactives',
  view_map: 'Lecture d’une carte',
  create_map: 'Création d’une carte',
  update_map: 'Modification d’une carte',
  place_on_map: 'Épingle posée sur une carte',
  remove_from_map: 'Épingle retirée d’une carte',
  set_place_kind: 'Type de lieux modifié',
};

const LOG_VISIBLE = 10;   // actions visibles sans défiler ; les suivantes dans la barre de défilement du journal

/** Journal : hauteur réglée sur les 10 premières lignes (mesurées : une ligne peut tenir sur deux), en-tête fixe. */
function LogTable({ log }) {
  const wrapRef = useRef(null);
  const [height, setHeight] = useState(null);

  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return undefined;
    const measure = () => {
      const rows = wrap.querySelectorAll('tbody tr');
      if (rows.length <= LOG_VISIBLE) { setHeight(null); return; }
      const head = wrap.querySelector('thead')?.offsetHeight || 0;
      const last = rows[LOG_VISIBLE - 1];
      setHeight(head + last.offsetTop + last.offsetHeight - (rows[0].offsetTop || 0) + 2);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, [log]);

  return (
    <>
      <div ref={wrapRef} className="table-wrap claude-log" style={height ? { maxHeight: height } : undefined}>
        <table className="table">
          <thead><tr><th>Date</th><th>Action</th><th>Campagne</th><th>Cible</th><th>Résultat</th></tr></thead>
          <tbody>
            {log.map((e) => (
              <tr key={e.id}>
                <td className="table__nums">{fmtDate(e.at)}</td>
                <td>{TOOL_LABEL[e.tool] || e.tool}</td>
                <td>{e.campaign || <span className="muted">-</span>}</td>
                <td className="table__mono">{e.target}</td>
                <td>{e.ok ? 'OK' : <span className="claude-log__error" title={e.detail}>{e.detail || 'Échec'}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {log.length > LOG_VISIBLE && (
        <p className="muted claude-log__more">
          {LOG_VISIBLE} dernières actions affichées sur {log.length} : faites défiler le journal pour voir les précédentes.
        </p>
      )}
    </>
  );
}

export function CopyField({ label, value, mono = true }) {
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      toast(`${label} copié`, 'success');
    } catch {
      toast('Copie impossible : sélectionnez le texte à la main.', 'error');
    }
  };
  return (
    <div className="copy-field">
      <dt>{label}</dt>
      <dd>
        <code className={mono ? 'table__mono' : ''}>{value}</code>
        <button type="button" className="icon-btn icon-btn--small" title="Copier" aria-label={`Copier : ${label}`} onClick={copy}>
          <Icon name="copy" size={15} />
        </button>
      </dd>
    </div>
  );
}

export default function ClaudeSection({ campaigns }) {
  const [state, setState] = useState(null);
  const [secret, setSecret] = useState(null);     // montré une seule fois, juste après sa création
  const [busy, setBusy] = useState(false);
  const run = useAction();
  const confirm = useConfirm();

  const load = useCallback(async () => {
    const out = await run(() => api.admin.claude());
    if (out) setState(out);
  }, [run]);
  useEffect(() => { load(); }, [load]);

  const modes = useMemo(() => new Map((state?.grants || []).map((g) => [g.campaign_id, g.mode])), [state]);

  const save = async (data, success) => {
    setBusy(true);
    const out = await run(() => api.admin.updateClaude(data), success);
    setBusy(false);
    if (out) setState(out);
  };

  const toggle = () => save({ enabled: !state.enabled },
    state.enabled ? 'Accès de Claude suspendu' : 'Accès de Claude activé');

  const setMode = (campaignId, mode) => save({ grant: { campaign_id: campaignId, mode } });

  const generate = async () => {
    if (state.client_id) {
      const yes = await confirm({
        title: 'Générer un nouveau secret',
        message: 'Claude sera déconnecté : il faudra saisir le nouvel identifiant et le nouveau secret dans les '
          + 'paramètres du connecteur sur claude.ai, puis vous reconnecter.',
        confirmLabel: 'Générer',
      });
      if (!yes) return;
    }
    setBusy(true);
    const out = await run(() => api.admin.claudeCredentials());
    setBusy(false);
    if (!out) return;
    setSecret(out.client_secret);
    setState(out);
  };

  const disconnect = async () => {
    const yes = await confirm({
      title: 'Déconnecter Claude',
      message: 'Les connexions en cours sont révoquées. Pour travailler à nouveau avec Claude, il faudra rouvrir la '
        + 'connexion depuis claude.ai (Connecteurs > Maze > Connecter) et saisir votre mot de passe.',
      confirmLabel: 'Déconnecter',
    });
    if (!yes) return;
    setBusy(true);
    const out = await run(() => api.admin.claudeDisconnect(), 'Claude est déconnecté');
    setBusy(false);
    if (out) setState(out);
  };

  if (!state) return <p className="muted">Chargement…</p>;
  const openCount = state.grants.length;

  return (
    <>
      <SectionHead title="Connecteur Claude"
        description="Claude (claude.ai) peut lire, et modifier si vous le permettez, les campagnes que vous lui ouvrez ici. Il agit au nom de l’administrateur qui a autorisé la connexion ; ses modifications de fiches rédigées sont gardées dans leur historique.">
        <button type="button" className={`btn ${state.enabled ? 'btn--ghost' : 'btn--primary'}`} onClick={toggle} disabled={busy}>
          <Icon name={state.enabled ? 'lock' : 'check'} size={16} /> {state.enabled ? 'Suspendre l’accès' : 'Activer l’accès'}
        </button>
      </SectionHead>

      <div className={`notice claude-status${state.enabled ? ' notice--success' : ''}`} role="status">
        <span className="claude-status__text">
          <span className={`state-dot state-dot--${state.enabled ? 'success' : 'failed'}`} aria-hidden="true" />
          {state.enabled
            ? `Accès actif : Claude peut utiliser ${openCount === 0 ? 'Maze, mais aucune campagne ne lui est ouverte' : `${openCount} campagne(s)`}.`
            : 'Accès suspendu : Claude ne peut rien lire ni modifier, même connecté.'}
        </span>
      </div>

      <section className="admin__block">
        <h2>Connexion à claude.ai</h2>
        <dl className="facts claude-facts">
          <CopyField label="URL du serveur" value={state.mcp_url} />
          {state.client_id && <CopyField label="Identifiant client" value={state.client_id} />}
        </dl>
        {secret && (
          <div className="notice claude-secret">
            <dl className="claude-secret__field"><CopyField label="Secret client" value={secret} /></dl>
            <span className="muted">Copiez-le maintenant : il ne sera plus affiché.</span>
          </div>
        )}
        <p>
          <button type="button" className={`btn ${state.client_id ? 'btn--ghost' : 'btn--primary'}`} onClick={generate} disabled={busy}>
            <Icon name="refresh" size={16} /> {state.client_id ? 'Générer un nouveau secret' : 'Générer l’identifiant et le secret'}
          </button>
        </p>
        <ol className="claude-steps">
          {!state.enabled && <li><strong>Activer l’accès</strong> (bouton en haut de cette page) et ouvrir au moins une campagne ci-dessous.</li>}
          <li>Sur claude.ai : <strong>Paramètres &gt; Connecteurs &gt; Ajouter un connecteur personnalisé</strong>.</li>
          <li>Nom : <strong>Maze</strong> ; URL : l’URL du serveur ci-dessus.</li>
          <li><strong>Paramètres avancés</strong> : l’identifiant client et le secret client.</li>
          <li><strong>Connecter</strong> : Maze demande votre identifiant et votre mot de passe d’administrateur, puis <strong>Autoriser</strong>.</li>
          <li>Dans une conversation, activez le connecteur Maze depuis le menu des outils quand vous voulez travailler avec Claude.</li>
        </ol>
      </section>

      <section className="admin__block">
        <h2>Campagnes ouvertes à Claude</h2>
        {!campaigns ? <p className="muted">Chargement…</p> : campaigns.length === 0 ? (
          <p className="muted">Aucune campagne pour l’instant.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Campagne</th><th>Accès de Claude</th></tr></thead>
              <tbody>
                {campaigns.map((c) => {
                  const current = modes.get(c.id) || 'none';
                  return (
                    <tr key={c.id}>
                      <td><strong>{c.name}</strong></td>
                      <td>
                        <div className="segmented segmented--compact" role="radiogroup" aria-label={`Accès de Claude à ${c.name}`}>
                          {MODES.map((m) => (
                            <button key={m.key} type="button" role="radio" aria-checked={current === m.key}
                              className={current === m.key ? 'is-active' : ''} disabled={busy}
                              onClick={() => current !== m.key && setMode(c.id, m.key)}>
                              {m.label}
                            </button>
                          ))}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="admin__block">
        <h2>Connexions</h2>
        {state.sessions.length === 0 ? (
          <p className="muted">Claude n’est pas connecté.</p>
        ) : (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Autorisée par</th><th>Autorisée le</th><th>Dernière activité</th></tr></thead>
                <tbody>
                  {state.sessions.map((s) => (
                    <tr key={s.id}><td>{s.user}</td><td>{fmtDate(s.authorized_at)}</td><td>{fmtDate(s.last_used_at)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="claude-actions">
              <button type="button" className="btn btn--ghost btn--danger-text" onClick={disconnect} disabled={busy}>
                <Icon name="logout" size={16} /> Déconnecter Claude
              </button>
            </p>
          </>
        )}
      </section>

      <section className="admin__block">
        <h2>Journal</h2>
        {state.log.length === 0 ? <p className="muted">Aucune action pour l’instant.</p> : <LogTable log={state.log} />}
      </section>
    </>
  );
}
