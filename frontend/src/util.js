export const nodeId = (kind, id) => `${kind}-${id}`;

/** Nœud du membre d'une appartenance : un PNJ, ou une faction membre d'une autre (sous-faction, member_faction_id). */
export const memberNodeId = (m) => (m.member_faction_id != null ? nodeId('faction', m.member_faction_id) : nodeId('npc', m.npc_id));

export function parseNodeId(value) {
  const [kind, id] = value.split('-');
  return { kind, id: Number(id) };
}

export function normalize(text) {
  return (text || '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .trim();
}

export function initials(name) {
  const words = (name || '?').replace(/[^\p{L}\p{N}\s'-]/gu, '').split(/[\s'-]+/).filter(Boolean);
  const picked = words.length > 1 ? [words[0], words[words.length - 1]] : words;
  return picked.map((w) => w[0].toUpperCase()).join('').slice(0, 2) || '?';
}

export function shade(hex, amount) {
  // amount < 0 assombrit, > 0 éclaircit
  const n = parseInt(hex.slice(1), 16);
  const mix = (c) => {
    const target = amount < 0 ? 0 : 255;
    return Math.round(c + (target - c) * Math.abs(amount));
  };
  const r = mix(n >> 16);
  const g = mix((n >> 8) & 255);
  const b = mix(n & 255);
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`;
}

const escapeXml = (s) => s.replace(/[<>&'"]/g, (c) => `&#${c.charCodeAt(0)};`);

export function initialsAvatar(name, color) {
  const bg = shade(color, -0.55);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128">`
    + `<rect width="128" height="128" fill="${bg}"/>`
    + `<text x="64" y="66" font-family="Palatino Linotype, Palatino, Georgia, serif" font-size="48" `
    + `fill="#e8e2d4" text-anchor="middle" dominant-baseline="middle">${escapeXml(initials(name))}</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export const storage = {
  get(key) {
    try { return window.localStorage.getItem(key); } catch { return null; }
  },
  set(key, value) {
    try { window.localStorage.setItem(key, value); } catch { /* stockage indisponible */ }
  },
};
