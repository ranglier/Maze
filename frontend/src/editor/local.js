import * as Y from 'yjs';
import { prosemirrorToYXmlFragment } from 'y-prosemirror';
import { Awareness } from 'y-protocols/awareness';
import { htmlToDoc } from './schema.js';

/**
 * Document Yjs local (sans serveur) pour réutiliser l'éditeur des fiches dans un simple champ de
 * formulaire (PNJ, factions, lieux) : même barre d'outils, mêmes mentions, annulation comprise.
 */
export function localLive(html) {
  const ydoc = new Y.Doc();
  const fragment = ydoc.getXmlFragment('prosemirror');
  prosemirrorToYXmlFragment(htmlToDoc(html || ''), fragment);
  const awareness = new Awareness(ydoc);
  return {
    ydoc, fragment, awareness,
    destroy() { awareness.destroy(); ydoc.destroy(); },
  };
}
