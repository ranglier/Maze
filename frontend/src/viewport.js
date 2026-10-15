/*
 * Petits écrans (téléphone) : zone réellement visible, clavier à l'écran, saisie en cours et voile
 * des feuilles (menus présentés en bas de l'écran).
 *
 * Variables CSS posées sur <html> :
 *   --vv-top  haut de la zone visible (Safari décale la page quand le clavier s'ouvre) ;
 *   --vvh     hauteur de la zone visible ;
 *   --kb      hauteur cachée en bas par le clavier (0 sans clavier).
 * Classes :
 *   is-typing  un champ de saisie a le focus sur écran tactile (barre de navigation basse masquée) ;
 *   has-sheet  un menu est ouvert (voile sur la page, en mode téléphone seulement : cf. styles.css).
 */
import { useEffect } from 'react';

// Mise en page « téléphone » (cf. styles.css) : écran étroit, ou téléphone tenu en paysage.
export const PHONE_QUERY = '(max-width: 760px), (pointer: coarse) and (max-height: 500px)';
export const isPhone = () => window.matchMedia(PHONE_QUERY).matches;
/** Téléphone en paysage : panneaux sur le côté, comme sur grand écran. */
export const isLandscapePhone = () => window.matchMedia('(pointer: coarse) and (max-height: 500px) and (min-width: 761px)').matches;
/** Écran tactile sans souris : consignes « touchez » au lieu de « cliquez ». */
export const TOUCH = window.matchMedia('(pointer: coarse)').matches;

const root = document.documentElement;

let lastKeyboard = 0;
function syncViewport() {
  const vv = window.visualViewport;
  const height = vv ? vv.height : window.innerHeight;
  const top = vv ? vv.offsetTop : 0;
  // Clavier : différence entre le bas de la fenêtre de mise en page et le bas de la zone visible.
  const hidden = Math.max(0, Math.round(window.innerHeight - height - top));
  // Page agrandie au pincement : la zone visible rétrécit sans clavier.
  const zoomed = vv && Math.abs(vv.scale - 1) > 0.01;
  const keyboard = hidden > 80 && !zoomed ? hidden : 0;
  root.style.setProperty('--vv-top', `${Math.round(top)}px`);
  root.style.setProperty('--vvh', `${Math.round(height)}px`);
  root.style.setProperty('--kb', `${keyboard}px`);
  root.classList.toggle('has-keyboard', keyboard > 0);
  // Le clavier s'ouvre (ou grandit) : l'application rétrécit, le point de saisie doit rester visible.
  if (keyboard > lastKeyboard) requestAnimationFrame(() => requestAnimationFrame(revealCaret));
  // Clavier refermé sans quitter le champ (bouton Retour d'Android) : la barre du bas revient.
  if (keyboard === 0 && lastKeyboard > 0) root.classList.remove('is-typing');
  lastKeyboard = keyboard;
}

const scrollParent = (el) => {
  for (let p = el?.parentElement; p; p = p.parentElement) {
    const o = getComputedStyle(p).overflowY;
    if ((o === 'auto' || o === 'scroll') && p.scrollHeight > p.clientHeight) return p;
  }
  return null;
};

/** Fait défiler le conteneur du champ actif pour que le curseur reste au-dessus du clavier et de la barre d'outils. */
function revealCaret() {
  const el = document.activeElement;
  if (!isTextEntry(el)) return;
  let rect = null;
  if (el.isContentEditable) {
    const sel = window.getSelection();
    if (sel?.rangeCount) {
      const range = sel.getRangeAt(0).cloneRange();
      range.collapse(false);
      rect = range.getClientRects()[0] || range.startContainer.parentElement?.getBoundingClientRect() || null;
    }
  }
  rect = rect || el.getBoundingClientRect();
  const box = scrollParent(el);
  if (!box) return;
  const view = box.getBoundingClientRect();
  // Barre de mise en forme ancrée au-dessus du clavier (fiches rédigées, champs riches).
  const toolbar = el.closest('.rich') ? 60 : 12;
  const bottom = Math.min(view.bottom, window.innerHeight - lastKeyboard) - toolbar;
  if (rect.bottom > bottom) box.scrollTop += rect.bottom - bottom + 24;
  else if (rect.top < view.top) box.scrollTop -= view.top - rect.top + 24;
}

const coarse = window.matchMedia('(pointer: coarse)');
const isTextEntry = (el) => Boolean(el && (
  el.isContentEditable
  || el.tagName === 'TEXTAREA'
  || (el.tagName === 'INPUT' && !['checkbox', 'radio', 'button', 'submit', 'file', 'color', 'range'].includes(el.type))
));

let typingTimer = 0;
function syncTyping() {
  clearTimeout(typingTimer);
  // Laisse passer le changement de focus d'un champ à l'autre sans faire clignoter la barre du bas.
  typingTimer = setTimeout(() => {
    root.classList.toggle('is-typing', coarse.matches && isTextEntry(document.activeElement));
  }, 60);
}

// --- Voile des feuilles ----------------------------------------------------------------------
let sheets = 0;
let releaseTimer = 0;
let scrim = null;

function ensureScrim() {
  if (scrim) return;
  scrim = document.createElement('div');
  scrim.className = 'sheet-scrim';
  scrim.setAttribute('aria-hidden', 'true');
  // Un toucher sur le voile ferme les menus (par leur propre détection du clic « ailleurs ») ; s'il n'y avait
  // pas de menu ouvert, il ferme le tiroir (un menu ouvert depuis le tiroir se ferme seul, tiroir conservé).
  let menuAtPress = false;
  scrim.addEventListener('pointerdown', () => { menuAtPress = sheets > 0; });
  scrim.addEventListener('click', () => {
    if (!menuAtPress) window.dispatchEvent(new CustomEvent('maze:scrim'));
    menuAtPress = false;
  });
  document.body.appendChild(scrim);
}

/** Inscrit une feuille ouverte ; renvoie la fonction qui la retire. */
export function openSheet() {
  ensureScrim();
  sheets += 1;
  clearTimeout(releaseTimer);
  root.classList.add('has-sheet');
  let done = false;
  return () => {
    if (done) return;
    done = true;
    sheets -= 1;
    // Le voile reste un instant : le toucher qui a fermé le menu ne doit pas « traverser » vers la page.
    if (sheets === 0) releaseTimer = setTimeout(() => { if (sheets === 0) root.classList.remove('has-sheet'); }, 350);
  };
}

/** Menu, liste ou fenêtre présenté en feuille sur téléphone : voile tant que `active`. */
export function useSheet(active = true) {
  useEffect(() => (active ? openSheet() : undefined), [active]);
}

export function installViewport() {
  syncViewport();
  window.visualViewport?.addEventListener('resize', syncViewport);
  window.visualViewport?.addEventListener('scroll', syncViewport);
  window.addEventListener('resize', syncViewport);
  document.addEventListener('focusin', syncTyping);
  document.addEventListener('focusout', syncTyping);
  ensureScrim();
}
