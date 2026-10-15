// Empreinte du schéma de l'éditeur (audit H3), contrôlée par build.mjs.
//
// y-prosemirror supprime du document partagé tout bloc, marque ou attribut que le schéma d'un navigateur ne
// connaît pas, et cette suppression part chez tous les MJ. Le serveur refuse donc les navigateurs dont la version
// du schéma (src/editor/schema-version.json, injectée par esbuild) diffère de la sienne (collab.SCHEMA_VERSION).
// Cette version est tenue à la main, pour ne changer que si le schéma change (pas à chaque mise à jour) ; l'empreinte
// ci-dessous la vérifie : si le schéma change sans que la version soit augmentée, la construction échoue.
//
// L'empreinte porte sur ce que y-prosemirror et ProseMirror utilisent pour reconstruire un document : noms des
// nœuds et des marques, attributs et valeurs par défaut, contenu admis, groupes, nœuds en ligne ou atomiques,
// marques exclues. Les règles de lecture et d'affichage (parseDOM, toDOM) n'y sont pas : elles ne suppriment rien
// du document partagé.
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const attrsOf = (spec) => Object.fromEntries(Object.keys(spec.attrs || {}).sort()
  .map((name) => [name, 'default' in spec.attrs[name] ? spec.attrs[name].default : '(obligatoire)']));

/** Description stable du schéma (objet Schema de prosemirror-model). */
export function describeSchema(schema) {
  const nodes = Object.keys(schema.nodes).sort().map((name) => {
    const { spec } = schema.nodes[name];
    return [name, {
      attrs: attrsOf(spec), content: spec.content || '', group: spec.group || '', inline: Boolean(spec.inline),
      atom: Boolean(spec.atom), marks: spec.marks ?? null,
    }];
  });
  const marks = Object.keys(schema.marks).sort().map((name) => {
    const { spec } = schema.marks[name];
    return [name, { attrs: attrsOf(spec), excludes: spec.excludes ?? null, inclusive: spec.inclusive ?? null }];
  });
  return { top: schema.topNodeType.name, nodes, marks };
}

export function fingerprintOf(schema) {
  return createHash('sha256').update(JSON.stringify(describeSchema(schema))).digest('hex').slice(0, 16);
}

/** Empreinte du schéma de src/editor/schema.js (`build` : fonction build d'esbuild ; `root` : dossier frontend). */
export async function schemaFingerprint(build, root = path.dirname(fileURLToPath(import.meta.url)), nodePaths = []) {
  const result = await build({
    absWorkingDir: root,
    stdin: { contents: "export { schema } from './src/editor/schema.js';", resolveDir: root, loader: 'js' },
    bundle: true, write: false, format: 'esm', platform: 'node', nodePaths, logLevel: 'silent',
    outdir: path.join(root, 'schema-check'),
  });
  const code = result.outputFiles[0].text;
  const { schema } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
  return fingerprintOf(schema);
}
