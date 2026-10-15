import { useEffect, useRef, useState } from 'react';
import Editor from '../editor/Editor.jsx';
import { localLive } from '../editor/local.js';
import { markdownToEditorHtml } from '../markdown.js';

/**
 * Champ de texte long d'une fiche (PNJ, faction, lieu) édité avec l'éditeur des scénarios : mise en forme,
 * alignement, @mentions, tableaux, encadrés. Le contenu est rendu en HTML à chaque modification ;
 * un ancien texte en Markdown n'est converti que si on le modifie.
 */
export default function RichField({ value, onChange, entities, resolve, placeholder, secret = false, label }) {
  const editorRef = useRef(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const [live] = useState(() => localLive(markdownToEditorHtml(value, resolve)));
  useEffect(() => {
    const onUpdate = () => {
      const html = editorRef.current?.getHtml();
      if (html != null) onChangeRef.current(html);
    };
    live.ydoc.on('update', onUpdate);
    return () => { live.ydoc.off('update', onUpdate); live.destroy(); };
  }, [live]);
  return (
    <div className={`rich-field${secret ? ' is-secret' : ''}`} aria-label={label}>
      <Editor ref={editorRef} live={live} entities={entities || []} entitiesLoaded={Boolean(entities)}
        placeholder={placeholder} simple />
    </div>
  );
}
