// Client HTTP de l'API Maze.
import { SCHEMA_VERSION, STALE_EVENT } from './editor/schemaVersion.js';

export class ApiError extends Error {
  constructor(status, detail, errors, data) {
    super(formatDetail(detail, errors));
    this.status = status;
    this.errors = errors;
    this.data = data;  // corps JSON de la réponse (409 d'une fiche : état actuel dans `current`)
  }
}

// Version du schéma de l'éditeur annoncée à chaque requête (audit H3) : une écriture d'un onglet resté sur une
// autre version de Maze est refusée (409, code « version ») et la page est rechargée (session.js).
const VERSION_HEADER = 'X-Maze-Version';
function staleCheck(status, data) {
  if (status === 409 && data?.code === 'version') {
    window.dispatchEvent(new CustomEvent(STALE_EVENT, { detail: { version: data.version } }));
  }
}

const FIELD_LABELS = {
  username: 'Identifiant', password: 'Mot de passe', new_password: 'Nouveau mot de passe',
  name: 'Nom', color: 'Couleur', title: 'Fonction', summary: 'Résumé', tags: 'Étiquettes',
};

function formatDetail(detail, errors) {
  if (!errors?.length) return detail || 'Erreur inattendue';
  const e = errors[0];
  const field = FIELD_LABELS[e.loc?.[e.loc.length - 1]] || e.loc?.[e.loc.length - 1];
  const messages = {
    string_too_short: e.loc?.includes('password') || e.loc?.includes('new_password')
      ? '8 caractères minimum' : 'champ obligatoire',
    string_pattern_mismatch: e.loc?.includes('username')
      ? 'lettres, chiffres, point, tiret ou tiret bas (2 à 40 caractères)' : 'format invalide',
    string_too_long: 'texte trop long',
    missing: 'champ obligatoire',
  };
  return `${field ? field + ' : ' : ''}${messages[e.type] || e.msg}`;
}

/** Envoi d'un fichier en corps brut (ou d'un formulaire si contentType est nul) avec suivi de
 * progression ; onProgress(0..1). */
function uploadRaw(url, file, contentType, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.setRequestHeader(VERSION_HEADER, String(SCHEMA_VERSION));
    if (contentType) xhr.setRequestHeader('Content-Type', contentType);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress?.(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* réponse non JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else {
        staleCheck(xhr.status, data);
        reject(new ApiError(xhr.status, data?.detail || `Erreur ${xhr.status}`, undefined, data));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, 'Connexion au serveur impossible'));
    xhr.send(file);
  });
}

const ENTRY_URL = { npc: 'npcs', faction: 'factions', place: 'places' };
const entryUrl = (kind, id) => `/api/${ENTRY_URL[kind]}/${id}`;

let unauthorizedHandler = () => {};
export function onUnauthorized(fn) {
  unauthorizedHandler = fn;
}

/** `schemaHeader: false` : sans en-tête de version (version nommée gardée par session.rescueEdits). */
async function request(method, path, body, { schemaHeader = true } = {}) {
  const options = { method, headers: schemaHeader ? { [VERSION_HEADER]: String(SCHEMA_VERSION) } : {},
    credentials: 'same-origin' };
  if (body instanceof FormData) {
    options.body = body;
  } else if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, options);
  } catch {
    throw new ApiError(0, 'Serveur injoignable. Vérifiez votre connexion.');
  }
  if (res.status === 401 && !path.startsWith('/api/auth/')) unauthorizedHandler();
  if (!res.ok) {
    let detail = res.statusText;
    let errors;
    let json;
    try {
      json = await res.json();
      detail = json.detail || detail;
      errors = json.errors;
    } catch { /* corps non JSON */ }
    staleCheck(res.status, json);
    throw new ApiError(res.status, detail, errors, json);
  }
  return res.status === 204 ? null : res.json();
}

const plural = { npc: 'npcs', faction: 'factions', place: 'places' };

export const api = {
  authStatus: () => request('GET', '/api/auth/status'),
  login: (username, password) => request('POST', '/api/auth/login', { username, password }),
  setup: (data) => request('POST', '/api/auth/setup', data),
  logout: () => request('POST', '/api/auth/logout'),
  changePassword: (current_password, new_password) =>
    request('POST', '/api/auth/password', { current_password, new_password }),

  admin: {
    users: () => request('GET', '/api/admin/users'),
    createUser: (data) => request('POST', '/api/admin/users', data),
    updateUser: (id, data) => request('PATCH', `/api/admin/users/${id}`, data),
    deleteUser: (id) => request('DELETE', `/api/admin/users/${id}`),
    campaigns: () => request('GET', '/api/admin/campaigns'),
    setMembers: (cid, userIds) => request('PUT', `/api/admin/campaigns/${cid}/members`, { user_ids: userIds }),
    backups: () => request('GET', '/api/admin/backups'),
    backupsUsage: () => request('GET', '/api/admin/backups/usage'),
    createBackup: () => request('POST', '/api/admin/backups'),
    deleteBackup: (name) => request('DELETE', `/api/admin/backups/${encodeURIComponent(name)}`),
    backupUrl: (name) => `/api/admin/backups/${encodeURIComponent(name)}`,
    restoreBackup: (name) => request('POST', `/api/admin/backups/${encodeURIComponent(name)}/restore`),
    importBackup: (file, onProgress) => uploadRaw('/api/admin/backups/import', file, 'application/gzip', onProgress),
    system: () => request('GET', '/api/admin/system'),
    checkUpdates: () => request('POST', '/api/admin/system/check'),
    runUpdate: () => request('POST', '/api/admin/system/update'),
    images: (cid) => request('GET', `/api/admin/campaigns/${cid}/images`),
    uploadLibrary: (cid, files) => {
      const form = new FormData();
      for (const f of files) form.append('files', f);
      return request('POST', `/api/admin/campaigns/${cid}/library`, form);
    },
    updateLibrary: (id, caption) => request('PATCH', `/api/admin/library/${id}`, { caption }),
    deleteLibrary: (id) => request('DELETE', `/api/admin/library/${id}`),
    assignLibrary: (id, data) => request('POST', `/api/admin/library/${id}/assign`, data),
    deleteDocImage: (id) => request('DELETE', `/api/admin/document-images/${id}`),
    cleanImages: (cid) => request('POST', `/api/admin/campaigns/${cid}/images/clean`, {}),
    claude: () => request('GET', '/api/admin/claude'),
    updateClaude: (data) => request('PUT', '/api/admin/claude', data),
    claudeCredentials: () => request('POST', '/api/admin/claude/credentials', {}),
    claudeDisconnect: () => request('POST', '/api/admin/claude/disconnect', {}),
    foundry: () => request('GET', '/api/admin/foundry'),
    createFoundryToken: (data) => request('POST', '/api/admin/foundry/tokens', data),
    updateFoundryToken: (id, data) => request('PUT', `/api/admin/foundry/tokens/${id}`, data),
    deleteFoundryToken: (id) => request('DELETE', `/api/admin/foundry/tokens/${id}`),
  },

  campaigns: () => request('GET', '/api/campaigns'),
  createCampaign: (data) => request('POST', '/api/campaigns', data),
  updateCampaign: (id, data) => request('PATCH', `/api/campaigns/${id}`, data),
  deleteCampaign: (id) => request('DELETE', `/api/campaigns/${id}`),
  campaignExportUrl: (id) => `/api/campaigns/${id}/export`,
  campaignExportCheck: (id) => request('GET', `/api/campaigns/${id}/export/check`),
  search: (q, campaign) => request('GET', `/api/search?q=${encodeURIComponent(q)}&campaign=${campaign}`),
  places: (cid) => request('GET', `/api/campaigns/${cid}/places`),
  reorderPlaces: (cid, parentId, ids) => request('PUT', `/api/campaigns/${cid}/places/order`, { parent_id: parentId, ids }),
  movePlace: (id, parentId, index) => request('PUT', `/api/places/${id}/move`, { parent_id: parentId, index }),
  pinPlace: (id, pinned) => request(pinned ? 'PUT' : 'DELETE', `/api/places/${id}/pin`),
  createPlaceKind: (cid, data) => request('POST', `/api/campaigns/${cid}/place-kinds`, data),
  updatePlaceKind: (id, data) => request('PATCH', `/api/place-kinds/${id}`, data),
  deletePlaceKind: (id) => request('DELETE', `/api/place-kinds/${id}`),
  reorderPlaceKinds: (cid, ids) => request('PUT', `/api/campaigns/${cid}/place-kinds/order`, { ids }),
  setPlaceKindImage: (id, file) => {
    const form = new FormData();
    form.append('file', file);
    return request('POST', `/api/place-kinds/${id}/image`, form);
  },
  deletePlaceKindImage: (id) => request('DELETE', `/api/place-kinds/${id}/image`),
  reorderEntities: (cid, kind, ids) => request('PUT', `/api/campaigns/${cid}/order/${kind}`, { ids }),
  createPlaceLink: (placeId, data) => request('POST', `/api/places/${placeId}/links`, data),
  updatePlaceLink: (id, data) => request('PATCH', `/api/place-links/${id}`, data),
  deletePlaceLink: (id) => request('DELETE', `/api/place-links/${id}`),
  documentMentions: (cid) => request('GET', `/api/campaigns/${cid}/documents/mentions`),
  comments: (docId) => request('GET', `/api/documents/${docId}/comments`),
  createComment: (docId, data) => request('POST', `/api/documents/${docId}/comments`, data),
  updateComment: (id, data) => request('PATCH', `/api/comments/${id}`, data),
  deleteComment: (id) => request('DELETE', `/api/comments/${id}`),
  importCampaign: (file, onProgress) => uploadRaw('/api/campaigns/import', file, 'application/zip', onProgress),

  graph: (cid) => request('GET', `/api/campaigns/${cid}/graph`),
  savePositions: (cid, nodes) => request('PUT', `/api/campaigns/${cid}/positions`, { nodes }),
  scenarioMap: (docId) => request('GET', `/api/documents/${docId}/map`),
  saveScenarioPositions: (docId, nodes) => request('PUT', `/api/documents/${docId}/map/positions`, { nodes }),
  setScenarioNode: (docId, kind, id, data) => request('PUT', `/api/documents/${docId}/map/nodes/${kind}/${id}`, data),

  createEntity: (kind, cid, data) => request('POST', `/api/campaigns/${cid}/${plural[kind]}`, data),
  getEntity: (kind, id) => request('GET', `/api/${plural[kind]}/${id}`),
  updateEntity: (kind, id, data) => request('PATCH', `/api/${plural[kind]}/${id}`, data),
  deleteEntity: (kind, id) => request('DELETE', `/api/${plural[kind]}/${id}`),
  campaignImages: (cid) => request('GET', `/api/campaigns/${cid}/images`),
  copyImageToEntity: (kind, id, data) => request('POST', `/api/${plural[kind]}/${id}/images/copy`, data),
  copyImageToDocument: (docId, data) => request('POST', `/api/documents/${docId}/images/copy`, data),
  uploadImages: (kind, id, files) => {
    const form = new FormData();
    for (const f of files) form.append('files', f);
    return request('POST', `/api/${plural[kind]}/${id}/images`, form);
  },

  createMembership: (data) => request('POST', '/api/memberships', data),
  updateMembership: (id, data) => request('PATCH', `/api/memberships/${id}`, data),
  deleteMembership: (id) => request('DELETE', `/api/memberships/${id}`),

  createRelation: (cid, data) => request('POST', `/api/campaigns/${cid}/relations`, data),
  updateRelation: (id, data) => request('PATCH', `/api/relations/${id}`, data),
  deleteRelation: (id) => request('DELETE', `/api/relations/${id}`),

  createRelationType: (cid, data) => request('POST', `/api/campaigns/${cid}/relation-types`, data),
  updateRelationType: (id, data) => request('PATCH', `/api/relation-types/${id}`, data),
  deleteRelationType: (id) => request('DELETE', `/api/relation-types/${id}`),

  documents: (cid) => request('GET', `/api/campaigns/${cid}/documents`),
  createDocument: (cid, data) => request('POST', `/api/campaigns/${cid}/documents`, data),
  moveDocument: (id, parentId, index) => request('PUT', `/api/documents/${id}/move`, { parent_id: parentId, index }),
  reorderDocuments: (cid, parentId, ids) => request('PUT', `/api/campaigns/${cid}/documents/order`, { parent_id: parentId, ids }),
  getDocument: (id) => request('GET', `/api/documents/${id}`),
  updateDocument: (id, data) => request('PATCH', `/api/documents/${id}`, data),
  deleteDocument: (id) => request('DELETE', `/api/documents/${id}`),
  duplicateDocument: (id) => request('POST', `/api/documents/${id}/duplicate`),

  versions: (docId) => request('GET', `/api/documents/${docId}/versions`),
  getVersion: (docId, vid) => request('GET', `/api/documents/${docId}/versions/${vid}`),
  createVersion: (docId, data, options) => request('POST', `/api/documents/${docId}/versions`, data, options),
  updateVersion: (docId, vid, data) => request('PATCH', `/api/documents/${docId}/versions/${vid}`, data),
  deleteVersion: (docId, vid) => request('DELETE', `/api/documents/${docId}/versions/${vid}`),
  restoreVersion: (docId, vid, data) => request('POST', `/api/documents/${docId}/versions/${vid}/restore`, data),

  // Fiches PNJ, factions et lieux : historique des versions et commentaires (kind : npc, faction ou place).
  entryVersions: (kind, id) => request('GET', `${entryUrl(kind, id)}/versions`),
  entryVersion: (kind, id, vid) => request('GET', `${entryUrl(kind, id)}/versions/${vid}`),
  createEntryVersion: (kind, id, data) => request('POST', `${entryUrl(kind, id)}/versions`, data),
  updateEntryVersion: (kind, id, vid, data) => request('PATCH', `${entryUrl(kind, id)}/versions/${vid}`, data),
  deleteEntryVersion: (kind, id, vid) => request('DELETE', `${entryUrl(kind, id)}/versions/${vid}`),
  restoreEntryVersion: (kind, id, vid) => request('POST', `${entryUrl(kind, id)}/versions/${vid}/restore`),
  entryComments: (kind, id) => request('GET', `${entryUrl(kind, id)}/comments`),
  createEntryComment: (kind, id, data) => request('POST', `${entryUrl(kind, id)}/comments`, data),
  updateEntryComment: (cid, data) => request('PATCH', `/api/entry-comments/${cid}`, data),
  deleteEntryComment: (cid) => request('DELETE', `/api/entry-comments/${cid}`),

  templates: () => request('GET', '/api/templates'),
  createTemplate: (data) => request('POST', '/api/templates', data),
  updateTemplate: (id, data) => request('PATCH', `/api/templates/${id}`, data),
  deleteTemplate: (id) => request('DELETE', `/api/templates/${id}`),
  uploadDocumentImage: (id, file) => {
    const form = new FormData();
    form.append('file', file);
    return request('POST', `/api/documents/${id}/images`, form);
  },
  /** Enregistrement de dernière minute (changement de page, fermeture d'onglet). */
  saveDocumentBeacon: (id, data) => fetch(`/api/documents/${id}`, {
    method: 'PATCH', keepalive: true, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', [VERSION_HEADER]: String(SCHEMA_VERSION) },
    body: JSON.stringify(data),
  }).catch(() => {}),

  updateImage: (id, data) => request('PATCH', `/api/images/${id}`, data),
  deleteImage: (id) => request('DELETE', `/api/images/${id}`),

  // Cartes interactives (Lieux > Carte interactive).
  maps: (cid) => request('GET', `/api/campaigns/${cid}/maps`),
  getMap: (id) => request('GET', `/api/maps/${id}`),
  createMap: (cid, { file, name, placeId }, onProgress) => {
    const form = new FormData();
    form.append('file', file);
    if (name) form.append('name', name);
    if (placeId) form.append('place_id', String(placeId));
    return uploadRaw(`/api/campaigns/${cid}/maps`, form, null, onProgress);
  },
  copyMap: (cid, data) => request('POST', `/api/campaigns/${cid}/maps/copy`, data),
  reorderMaps: (cid, ids) => request('PUT', `/api/campaigns/${cid}/maps/order`, { ids }),
  updateMap: (id, data) => request('PATCH', `/api/maps/${id}`, data),
  deleteMap: (id) => request('DELETE', `/api/maps/${id}`),
  replaceMapImage: (id, file, onProgress) => {
    const form = new FormData();
    form.append('file', file);
    return uploadRaw(`/api/maps/${id}/image`, form, null, onProgress);
  },
  copyMapImage: (id, data) => request('POST', `/api/maps/${id}/image/copy`, data),
  setPin: (mapId, placeId, x, y) => request('PUT', `/api/maps/${mapId}/pins/${placeId}`, { x, y }),
  deletePin: (mapId, placeId) => request('DELETE', `/api/maps/${mapId}/pins/${placeId}`),
};

// Export d'une campagne : si son campaign.json dépasse la limite d'import (audit M16), l'archive ne pourrait pas
// être réimportée ; on le dit avant de la télécharger. `confirm` : fonction de useConfirm.
export async function exportCampaign(id, confirm) {
  let check = null;
  try {
    check = await api.campaignExportCheck(id);
  } catch { /* vérification indisponible : export direct */ }
  if (check && !check.importable) {
    const yes = await confirm({
      title: 'Archive impossible à réimporter',
      message: `${check.message}. Elle reste lisible (campaign.json et images), mais Maze refusera de l’importer.`,
      confirmLabel: 'Exporter quand même',
    });
    if (!yes) return false;
  }
  const link = document.createElement('a');
  link.href = api.campaignExportUrl(id);
  link.download = '';
  document.body.append(link);
  link.click();
  link.remove();
  return true;
}
