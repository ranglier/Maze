import * as Y from 'yjs';
import { prosemirrorToYXmlFragment, yXmlFragmentToProseMirrorRootNode } from 'y-prosemirror';
import { applyAwarenessUpdate, Awareness, encodeAwarenessUpdate, removeAwarenessStates } from 'y-protocols/awareness';
import { rescueEdits } from '../session.js';
import { docToHtml, htmlToDoc, schema } from './schema.js';
import { SCHEMA_VERSION, STALE_EVENT } from './schemaVersion.js';

/*
 * Connexion temps réel d'une fiche (voir backend/maze/collab.py pour le protocole).
 * Le document Yjs est la référence ; le serveur numérote et rediffuse les modifications.
 *
 * États : connecting > (waiting) > ready ; offline pendant une coupure ; closed à la fin.
 */

const UPDATE = 0;
const AWARENESS = 1;
const SNAPSHOT = 2;
const ACK = 3;
const REMOTE = Symbol('remote');
const BATCH_MS = 60;           // regroupe les frappes rapprochées en une seule modification
const CONTENT_MS = 1500;       // délai avant d'envoyer le HTML dérivé (meneur)
const RETRY_MS = [1000, 2000, 4000, 8000, 15000];
// Connexion sans réponse du serveur (proxy bloqué, serveur qui redémarre) : abandon et nouvel essai.
// Le navigateur n'ouvre qu'une connexion WebSocket à la fois vers un même serveur : une tentative
// bloquée retiendrait aussi celles des autres fiches.
// Délai sans aucune trame : réarmé à chaque trame reçue avant le « welcome » (journal d'une fiche volumineuse sur une
// liaison lente : il arrive en continu, mais en plus de 10 s ; audit FRONT-7).
const CONNECT_TIMEOUT_MS = 10000;
// 4429 : trop de connexions temps réel (par compte ou sur le serveur, audit PERF-9) ; raison affichée, nouvel essai.
const CLOSE_BUSY = 4429;
// 4426 : version du schéma de l'éditeur différente de celle du serveur (Maze mis à jour, audit H3).
const FINAL_CODES = { 4401: 'auth', 4403: 'forbidden', 4404: 'deleted', 4409: 'reset', 4413: 'toolarge', 4426: 'version' };
const MAX_UPDATE = 2 * 1024 * 1024;

// Fiches fermées avec des modifications pas encore confirmées : elles restent connectées jusqu'à l'envoi.
export const closingDocuments = new Set();

const readSeq = (bytes) => Number(new DataView(bytes.buffer, bytes.byteOffset + 1, 8).getBigUint64(0));
const frame = (type, payload, seq) => {
  const head = seq === undefined ? 1 : 9;
  const out = new Uint8Array(head + payload.length);
  out[0] = type;
  if (seq !== undefined) new DataView(out.buffer).setBigUint64(1, BigInt(seq));
  out.set(payload, head);
  return out;
};

export class LiveDocument {
  constructor({ docId, html, epoch, user, onChange, meta }) {
    this.docId = docId;
    this.meta = meta || {};  // { campaignId, created, title } : avis des modifications abandonnées
    this.epoch = epoch;      // époque du HTML reçu ; différente au « welcome » = contenu remplacé entre-temps
    this.html = html || '';
    this.user = user;
    this.onChange = onChange || (() => {});
    this.ydoc = new Y.Doc();
    this.fragment = this.ydoc.getXmlFragment('prosemirror');
    this.awareness = new Awareness(this.ydoc);
    this.state = { status: 'connecting', peers: [], pending: 0, error: null, synced: false };
    this.known = 0;          // toutes les modifications de numéro <= known sont appliquées ici
    this.leader = false;
    this.buffer = [];        // modifications locales pas encore envoyées
    this.inflight = [];      // envoyées, en attente de numéro (ACK)
    this.attempt = 0;
    this.closed = false;
    this.contentDirty = false;

    this.ydoc.on('update', (update, origin) => {
      if (origin !== REMOTE) {
        this.buffer.push(update);
        clearTimeout(this.batchTimer);
        this.batchTimer = setTimeout(() => this.flush(), BATCH_MS);
        this.setState({ pending: this.buffer.length + this.inflight.length });
      }
      this.scheduleContent();
    });
    this.awareness.on('update', ({ added, updated, removed }, origin) => {
      if (origin !== 'local') return;
      this.sendAwareness([...added, ...updated, ...removed]);
    });
    this.connect();
  }

  setState(patch) {
    this.state = { ...this.state, ...patch };
    this.onChange(this.state);
  }

  get ready() {
    return this.state.synced;
  }

  // --- Connexion ---------------------------------------------------------------------

  connect() {
    if (this.closed) return;
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${window.location.host}/api/documents/${this.docId}/live`
      + `?client=${this.ydoc.clientID}&v=${SCHEMA_VERSION}`);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    this.welcomed = false;
    const arm = () => {
      clearTimeout(this.connectTimer);
      this.connectTimer = setTimeout(() => {
        if (ws === this.ws && !this.welcomed) ws.close();  // onclose : nouvel essai
      }, CONNECT_TIMEOUT_MS);
    };
    arm();
    ws.onmessage = (e) => {
      if (ws !== this.ws) return;
      if (!this.welcomed) arm();  // le serveur répond : le journal arrive, on ne coupe pas (audit FRONT-7)
      if (typeof e.data === 'string') this.onControl(JSON.parse(e.data));
      else this.onBinary(new Uint8Array(e.data));
    };
    ws.onclose = (e) => {
      if (ws !== this.ws) return;
      this.ws = null;
      clearTimeout(this.connectTimer);
      clearTimeout(this.contentTimer);
      if (this.closed) return;
      const final = FINAL_CODES[e.code];
      if (final) {
        // Fermeture définitive : les modifications pas encore confirmées ne seront jamais fusionnées (audit M4).
        const rescued = this.rescue(final);
        if (final === 'version') {
          // Le texte local est gardé avant que la page soit rechargée (session.js attend la fin de l'envoi).
          Promise.resolve(rescued).finally(() => window.dispatchEvent(new CustomEvent(STALE_EVENT,
            { detail: { version: this.serverVersion ?? null } })));
        }
        if (this.draining) {
          this.finish();
          return;
        }
        this.closed = true;
        this.setState({ status: 'closed', error: this.state.error || final, pending: 0 });
        return;
      }
      // Les modifications envoyées sans accusé de réception seront renvoyées (Yjs ignore les doublons).
      this.buffer = [...this.inflight, ...this.buffer];
      this.inflight = [];
      const delay = RETRY_MS[Math.min(this.attempt, RETRY_MS.length - 1)];
      this.attempt += 1;
      // Connexion refusée faute de place (4429) : raison du serveur affichée jusqu'à la prochaine connexion réussie.
      const busy = e.code === CLOSE_BUSY ? (e.reason || 'Trop de connexions temps réel') : null;
      this.setState({ status: this.state.synced ? 'offline' : 'connecting', attempts: this.attempt, busy });
      this.retryTimer = setTimeout(() => this.connect(), delay);
    };
  }

  send(data) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(data);
      return true;
    }
    return false;
  }

  onBinary(bytes) {
    const type = bytes[0];
    if (type === UPDATE) {
      Y.applyUpdate(this.ydoc, bytes.subarray(9), REMOTE);
      this.known = Math.max(this.known, readSeq(bytes));
    } else if (type === ACK) {
      this.inflight.shift();
      this.known = Math.max(this.known, readSeq(bytes));
      this.setState({ pending: this.buffer.length + this.inflight.length });
    } else if (type === AWARENESS) {
      applyAwarenessUpdate(this.awareness, bytes.subarray(1), REMOTE);
    }
  }

  onControl(msg) {
    switch (msg.t) {
      case 'welcome': {
        // Contenu remplacé par l'API pendant une coupure (ou depuis la lecture du HTML) : on recharge.
        if (this.epoch !== undefined && msg.epoch !== this.epoch) {
          // Le journal a été réinitialisé : ces modifications ne peuvent plus être fusionnées, mais le texte
          // tapé pendant la coupure est gardé comme version nommée (audit M4).
          this.rescue('reset');
          if (this.draining) {
            this.finish();
            return;
          }
          this.closed = true;
          this.ws?.close(1000);
          this.setState({ status: 'closed', error: 'reset', pending: 0 });
          return;
        }
        this.epoch = msg.epoch;
        this.welcomed = true;
        clearTimeout(this.connectTimer);
        this.attempt = 0;
        this.known = Math.max(this.known, msg.seq);
        this.leader = msg.leader;
        this.awareness.setLocalStateField('user', { name: this.user.name, color: msg.color });
        if (msg.init) this.initialize();
        const waiting = msg.waiting && !msg.init;
        this.setState({ status: waiting ? 'waiting' : 'ready', peers: msg.peers, you: msg.you, error: null, busy: null,
          synced: this.state.synced || !waiting });
        if (!waiting) this.flush();
        if (this.leader && msg.stale) this.sendContent();
        break;
      }
      case 'init':
        // Conversion confiée à ce navigateur (le précédent est parti) : on n'attend plus.
        this.setState({ status: 'ready', synced: true });
        this.initialize();
        break;
      case 'ready':
        this.setState({ status: 'ready', synced: true });
        this.flush();
        break;
      case 'peers':
        this.setState({ peers: msg.peers });
        if (msg.joined) this.sendAwareness([this.ydoc.clientID]);
        if (msg.left_client) removeAwarenessStates(this.awareness, [msg.left_client], REMOTE);
        break;
      case 'leader':
        this.leader = true;
        this.sendContent();
        break;
      case 'compact':
        if (this.leader) {
          this.send(frame(SNAPSHOT, Y.encodeStateAsUpdate(this.ydoc), this.known));
        }
        break;
      case 'meta':
        this.onMeta?.(msg);
        break;
      case 'comments':
        this.onComments?.();
        break;
      case 'deleted':
      case 'reset':
        this.setState({ error: msg.t });
        break;
      case 'error':
        if (msg.version !== undefined) this.serverVersion = msg.version;
        this.setState({ error: FINAL_CODES[msg.code] || 'error', detail: msg.detail });
        break;
      default:
    }
  }

  /** Première ouverture d'une fiche écrite avant le temps réel : conversion de son HTML. */
  initialize() {
    try {
      if (this.fragment.length === 0 && this.html.trim()) {
        prosemirrorToYXmlFragment(htmlToDoc(this.html), this.fragment);
      }
      this.flush();
    } finally {
      this.send(JSON.stringify({ t: 'init-done' }));
    }
  }

  // --- Envois ------------------------------------------------------------------------

  flush() {
    clearTimeout(this.batchTimer);
    if (!this.buffer.length || !this.welcomed || this.state.status === 'waiting') return;
    const merged = this.buffer.length === 1 ? this.buffer[0] : Y.mergeUpdates(this.buffer);
    // Trop gros d'un bloc (longue période hors ligne) : envoi modification par modification.
    const parts = merged.length <= MAX_UPDATE ? [merged] : [...this.buffer];
    if (parts.some((u) => u.length > MAX_UPDATE)) {
      // Une seule modification dépasse la limite (collage géant) : impossible à enregistrer. Les modifications en
      // attente sont abandonnées (les suivantes dépendent d'elle) et la fiche est rechargée depuis le serveur pour
      // rester cohérente ; le texte local est d'abord gardé comme version nommée (audit M4).
      this.rescue('toolarge');
      this.setState({ error: 'toolarge', pending: 0 });
      return;
    }
    if (parts.length === 1) {
      if (this.send(frame(UPDATE, merged))) {
        this.buffer = [];
        this.inflight.push(merged);
      }
    } else {
      for (const part of parts) {
        if (!this.send(frame(UPDATE, part))) break;
        this.buffer.shift();
        this.inflight.push(part);
      }
    }
    this.setState({ pending: this.buffer.length + this.inflight.length });
  }

  sendAwareness(clients) {
    if (!this.welcomed) return;
    this.send(frame(AWARENESS, encodeAwarenessUpdate(this.awareness, clients)));
  }

  scheduleContent() {
    this.contentDirty = true;
    if (!this.leader) return;
    clearTimeout(this.contentTimer);
    this.contentTimer = setTimeout(() => this.sendContent(), CONTENT_MS);
  }

  /** Meneur : HTML dérivé du document, pour les rétroliens et les sauvegardes. */
  sendContent() {
    clearTimeout(this.contentTimer);
    if (!this.leader || !this.welcomed) return;
    const doc = yXmlFragmentToProseMirrorRootNode(this.fragment, schema);
    if (this.send(JSON.stringify({ t: 'content', html: docToHtml(doc), upto: this.known }))) {
      this.contentDirty = false;
    }
  }

  /**
   * Modifications locales jamais confirmées par le serveur, sur le point d'être abandonnées (contenu remplacé,
   * fiche fermée par le serveur, modification trop grosse, autre version de Maze) : le HTML de la copie locale est
   * confié à session.rescueEdits (version nommée de la fiche, à défaut gardé dans le navigateur, avis persistant).
   * Renvoie la promesse de cet enregistrement, ou null s'il n'y avait rien en attente.
   */
  rescue(reason) {
    if (!this.buffer.length && !this.inflight.length) return null;
    clearTimeout(this.batchTimer);
    let html = '';
    try {
      html = docToHtml(yXmlFragmentToProseMirrorRootNode(this.fragment, schema));
    } catch { /* document illisible : rien à garder */ }
    this.buffer = [];
    this.inflight = [];
    if (!html.trim()) return null;
    return rescueEdits({ docId: this.docId, campaignId: this.meta.campaignId, created: this.meta.created,
      title: this.meta.title, html, reason, author: this.user?.name });
  }

  setLocalUser(user) {
    this.user = user;
  }

  /** Fermeture (changement de fiche) : envoie ce qui reste avant de couper. */
  destroy() {
    if (this.finished) return;
    if (this.closed) {
      // Connexion déjà fermée par le serveur (fiche supprimée, réinitialisée, accès retiré).
      this.finish();
      return;
    }
    this.onChange = () => {};
    this.onMeta = null;
    this.onComments = null;
    this.flush();
    if (this.leader && this.contentDirty) this.sendContent();
    if (this.buffer.length || this.inflight.length) {
      // Hors ligne ou accusés de réception en attente : la connexion reste ouverte (ou se rétablit)
      // en arrière-plan jusqu'à ce que tout soit confirmé, puis se ferme.
      closingDocuments.add(this);
      this.draining = true;
      this.setState = (patch) => {
        this.state = { ...this.state, ...patch };
        if (!this.buffer.length && !this.inflight.length) this.finish();
      };
      return;
    }
    this.finish();
  }

  finish() {
    if (this.finished) return;
    this.finished = true;
    closingDocuments.delete(this);
    this.closed = true;
    clearTimeout(this.retryTimer);
    clearTimeout(this.batchTimer);
    clearTimeout(this.contentTimer);
    clearTimeout(this.connectTimer);
    if (this.ws) {
      const ws = this.ws;
      this.ws = null;
      ws.close(1000);  // le serveur annonce notre départ : les autres retirent notre curseur
    }
    // Différé : l'éditeur relié à ce document est détruit dans la même passe de rendu, et le
    // retrait de notre curseur ne doit plus lui envoyer de transaction.
    setTimeout(() => {
      this.awareness.destroy();
      this.ydoc.destroy();
    }, 0);
  }
}
