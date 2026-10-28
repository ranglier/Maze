import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import { Icon, useAction, useConfirm, useToast } from './ui.jsx';
import { SectionHead, fmtDate } from './AdminPage.jsx';
import { N_, pt, t, tn } from '../i18n/index.js';

// Accès de Claude à une campagne : libellés traduits à l'affichage (label()).
const MODES = [
  { key: 'none', label: () => t('Aucun') },
  { key: 'read', label: () => pt('access', 'Lecture') },
  { key: 'write', label: () => pt('access', 'Lecture et écriture') },
  { key: 'delete', label: () => pt('access', 'Lecture, écriture et suppression') },
];

// Libellés traduits à l'affichage (t(…)) : N_ les signale seulement à l'extraction.
const TOOL_LABEL = {
  list_campaigns: N_('Liste des campagnes'),
  search: N_('Recherche'),
  get_campaign: N_('Vue d’ensemble'),
  get_entry: N_('Lecture d’une fiche'),
  list_relations: N_('Liste des liens'),
  get_document: N_('Lecture d’une fiche rédigée'),
  create_entry: N_('Création d’une fiche'),
  update_entry: N_('Modification d’une fiche'),
  delete_entry: N_('Suppression d’une fiche'),
  create_relation: N_('Création d’un lien'),
  update_relation: N_('Modification d’un lien'),
  delete_relation: N_('Suppression d’un lien'),
  set_membership: N_('Appartenance à une faction'),
  remove_membership: N_('Retrait d’une faction'),
  set_place_link: N_('Lien avec un lieu'),
  remove_place_link: N_('Retrait d’un lieu'),
  create_document: N_('Création d’une fiche rédigée'),
  update_document: N_('Modification d’une fiche rédigée'),
  delete_document: N_('Suppression d’une fiche rédigée'),
  add_comment: N_('Commentaire'),
  add_entry_comment: N_('Commentaire'),
  delete_comment: N_('Suppression d’un commentaire'),
  view_image: N_('Lecture d’une image'),
  add_image: N_('Ajout d’une image'),
  update_image: N_('Modification d’une image'),
  edit_image: N_('Retouche d’une image'),
  delete_image: N_('Suppression d’une image'),
  add_document_image: N_('Illustration d’une fiche rédigée'),
  list_library: N_('Lecture de la bibliothèque'),
  delete_library_image: N_('Retrait de la bibliothèque'),
  list_maps: N_('Lecture des cartes interactives'),
  view_map: N_('Lecture d’une carte'),
  create_map: N_('Création d’une carte'),
  update_map: N_('Modification d’une carte'),
  delete_map: N_('Suppression d’une carte'),
  place_on_map: N_('Épingle posée sur une carte'),
  remove_from_map: N_('Épingle retirée d’une carte'),
  set_place_kind: N_('Type de lieux modifié'),
  list_events: N_('Lecture de la chronologie'),
  create_event: N_('Création d’un événement'),
  update_event: N_('Modification d’un événement'),
  delete_event: N_('Suppression d’un événement'),
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
          <thead>
            <tr><th>{t('Date')}</th><th>{t('Action')}</th><th>{t('Campagne')}</th><th>{t('Cible')}</th><th>{t('Résultat')}</th></tr>
          </thead>
          <tbody>
            {log.map((e) => (
              <tr key={e.id}>
                <td className="table__nums">{fmtDate(e.at)}</td>
                <td>{TOOL_LABEL[e.tool] ? t(TOOL_LABEL[e.tool]) : e.tool}</td>
                <td>{e.campaign || <span className="muted">-</span>}</td>
                <td className="table__mono">{e.target}</td>
                <td>{e.ok ? t('OK') : <span className="claude-log__error" title={e.detail}>{e.detail || t('Échec')}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {log.length > LOG_VISIBLE && (
        <p className="muted claude-log__more">
          {t('{shown} dernières actions affichées sur {total} : faites défiler le journal pour voir les précédentes.',
            { shown: LOG_VISIBLE, total: log.length })}
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
      toast(t('{label} copié', { label }), 'success');
    } catch {
      toast(t('Copie impossible : sélectionnez le texte à la main.'), 'error');
    }
  };
  return (
    <div className="copy-field">
      <dt>{label}</dt>
      <dd>
        <code className={mono ? 'table__mono' : ''}>{value}</code>
        <button type="button" className="icon-btn icon-btn--small" title={t('Copier')} aria-label={t('Copier : {label}', { label })} onClick={copy}>
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
    state.enabled ? t('Accès de Claude suspendu') : t('Accès de Claude activé'));

  const setMode = async (campaign, mode) => {
    if (mode === 'delete') {
      const yes = await confirm({
        title: t('Autoriser les suppressions'),
        message: t('Claude pourra supprimer définitivement des fiches, des fiches rédigées, des événements, des cartes '
          + 'interactives et des commentaires de « {name} ». À réserver à une campagne sauvegardée.', { name: campaign.name }),
        confirmLabel: t('Autoriser'),
      });
      if (!yes) return;
    }
    save({ grant: { campaign_id: campaign.id, mode } });
  };

  const generate = async () => {
    if (state.client_id) {
      const yes = await confirm({
        title: t('Générer un nouveau secret'),
        message: t('Claude sera déconnecté : il faudra saisir le nouvel identifiant et le nouveau secret dans les '
          + 'paramètres du connecteur sur claude.ai, puis vous reconnecter.'),
        confirmLabel: t('Générer'),
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
      title: t('Déconnecter Claude'),
      message: t('Les connexions en cours sont révoquées. Pour travailler à nouveau avec Claude, il faudra rouvrir la '
        + 'connexion depuis claude.ai (Connecteurs > Maze > Connecter) et saisir votre mot de passe.'),
      confirmLabel: t('Déconnecter'),
    });
    if (!yes) return;
    setBusy(true);
    const out = await run(() => api.admin.claudeDisconnect(), t('Claude est déconnecté'));
    setBusy(false);
    if (out) setState(out);
  };

  if (!state) return <p className="muted">{t('Chargement…')}</p>;
  const openCount = state.grants.length;

  return (
    <>
      <SectionHead title={t('Connecteur Claude')}
        description={t('Claude (claude.ai) peut lire, et modifier si vous le permettez, les campagnes que vous lui ouvrez ici. Il agit au nom de l’administrateur qui a autorisé la connexion ; ses modifications de fiches rédigées sont gardées dans leur historique.')}>
        <button type="button" className={`btn ${state.enabled ? 'btn--ghost' : 'btn--primary'}`} onClick={toggle} disabled={busy}>
          <Icon name={state.enabled ? 'lock' : 'check'} size={16} /> {state.enabled ? t('Suspendre l’accès') : t('Activer l’accès')}
        </button>
      </SectionHead>

      <div className={`notice claude-status${state.enabled ? ' notice--success' : ''}`} role="status">
        <span className="claude-status__text">
          <span className={`state-dot state-dot--${state.enabled ? 'success' : 'failed'}`} aria-hidden="true" />
          {!state.enabled ? t('Accès suspendu : Claude ne peut rien lire ni modifier, même connecté.')
            : openCount === 0 ? t('Accès actif : Claude peut utiliser Maze, mais aucune campagne ne lui est ouverte.')
              : tn('Accès actif : Claude peut utiliser {n} campagne.', 'Accès actif : Claude peut utiliser {n} campagnes.', openCount)}
        </span>
      </div>

      <section className="admin__block">
        <h2>{t('Connexion à claude.ai')}</h2>
        <dl className="facts claude-facts">
          <CopyField label={t('URL du serveur')} value={state.mcp_url} />
          {state.client_id && <CopyField label={t('Identifiant client')} value={state.client_id} />}
        </dl>
        {secret && (
          <div className="notice claude-secret">
            <dl className="claude-secret__field"><CopyField label={t('Secret client')} value={secret} /></dl>
            <span className="muted">{t('Copiez-le maintenant : il ne sera plus affiché.')}</span>
          </div>
        )}
        <p>
          <button type="button" className={`btn ${state.client_id ? 'btn--ghost' : 'btn--primary'}`} onClick={generate} disabled={busy}>
            <Icon name="refresh" size={16} /> {state.client_id ? t('Générer un nouveau secret') : t('Générer l’identifiant et le secret')}
          </button>
        </p>
        <ol className="claude-steps">
          {!state.enabled && (
            <li>{t('{activate} (bouton en haut de cette page) et ouvrir au moins une campagne ci-dessous.',
              { activate: <strong>{t('Activer l’accès')}</strong> })}</li>
          )}
          <li>{t('Sur claude.ai : {path}.', { path: <strong>{t('Paramètres > Connecteurs > Ajouter un connecteur personnalisé')}</strong> })}</li>
          <li>{t('Nom : {name} ; URL : l’URL du serveur ci-dessus.', { name: <strong>Maze</strong> })}</li>
          <li>{t('{advanced} : l’identifiant client et le secret client.', { advanced: <strong>{t('Paramètres avancés')}</strong> })}</li>
          <li>{t('{connect} : Maze demande votre identifiant et votre mot de passe d’administrateur, puis {authorize}.',
            { connect: <strong>{t('Connecter')}</strong>, authorize: <strong>{t('Autoriser')}</strong> })}</li>
          <li>{t('Dans une conversation, activez le connecteur Maze depuis le menu des outils quand vous voulez travailler avec Claude.')}</li>
        </ol>
      </section>

      <section className="admin__block">
        <h2>{t('Campagnes ouvertes à Claude')}</h2>
        <p className="field__hint">
          {t('{level} permet aussi à Claude de supprimer définitivement des fiches, fiches rédigées, événements, cartes interactives et commentaires : à réserver aux campagnes sauvegardées.',
            { level: <strong>{pt('access', 'Lecture, écriture et suppression')}</strong> })}
        </p>
        {!campaigns ? <p className="muted">{t('Chargement…')}</p> : campaigns.length === 0 ? (
          <p className="muted">{t('Aucune campagne pour l’instant.')}</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>{t('Campagne')}</th><th>{t('Accès de Claude')}</th></tr></thead>
              <tbody>
                {campaigns.map((c) => {
                  const current = modes.get(c.id) || 'none';
                  return (
                    <tr key={c.id}>
                      <td><strong>{c.name}</strong></td>
                      <td>
                        <div className="segmented segmented--compact" role="radiogroup" aria-label={t('Accès de Claude à {name}', { name: c.name })}>
                          {MODES.map((m) => (
                            <button key={m.key} type="button" role="radio" aria-checked={current === m.key}
                              className={current === m.key ? 'is-active' : ''} disabled={busy}
                              onClick={() => current !== m.key && setMode(c, m.key)}>
                              {m.label()}
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
        <h2>{t('Connexions')}</h2>
        {state.sessions.length === 0 ? (
          <p className="muted">{t('Claude n’est pas connecté.')}</p>
        ) : (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>{t('Autorisée par')}</th><th>{t('Autorisée le')}</th><th>{t('Dernière activité')}</th></tr></thead>
                <tbody>
                  {state.sessions.map((s) => (
                    <tr key={s.id}><td>{s.user}</td><td>{fmtDate(s.authorized_at)}</td><td>{fmtDate(s.last_used_at)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="claude-actions">
              <button type="button" className="btn btn--ghost btn--danger-text" onClick={disconnect} disabled={busy}>
                <Icon name="logout" size={16} /> {t('Déconnecter Claude')}
              </button>
            </p>
          </>
        )}
      </section>

      <section className="admin__block">
        <h2>{t('Journal')}</h2>
        {state.log.length === 0 ? <p className="muted">{t('Aucune action pour l’instant.')}</p> : <LogTable log={state.log} />}
      </section>
    </>
  );
}
