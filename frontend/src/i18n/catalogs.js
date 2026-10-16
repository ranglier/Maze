// Catalogues de traduction : ./<langue>/<zone>.json, un objet JSON par fichier (clé : texte français).
//
// Ce fichier n'est pas celui du bundle : à la construction, le plugin d'esbuild de ../../i18n-catalogs.mjs (build.mjs,
// et les tests qui regroupent des modules avec esbuild, test/helpers.mjs) le remplace par un import statique de
// chaque catalogue, qui est donc inclus dans le bundle, sans chargement réseau. Ajouter une langue : ajouter un dossier.
//
// Tel quel, il sert aux modules importés directement par Node (node --test) : il lit les mêmes fichiers sur le disque
// (process.getBuiltinModule, Node 22.3 et plus : aucun import de node:fs, donc rien à résoudre pour un autre outil qui
// regrouperait les sources sans le plugin ; il n'y trouverait simplement aucun catalogue).
function load() {
  const fs = globalThis.process?.getBuiltinModule?.('node:fs');
  const url = import.meta.url;
  if (!fs || !url?.startsWith('file:')) return {};
  const root = new URL('./', url);
  const catalogs = {};
  for (const dir of fs.readdirSync(root, { withFileTypes: true })) {
    if (!dir.isDirectory() || !/^[a-z]{2,3}$/.test(dir.name)) continue;
    const merged = {};
    for (const file of fs.readdirSync(new URL(`${dir.name}/`, root)).filter((f) => f.endsWith('.json')).sort()) {
      Object.assign(merged, JSON.parse(fs.readFileSync(new URL(`${dir.name}/${file}`, root), 'utf8')));
    }
    catalogs[dir.name] = merged;
  }
  return catalogs;
}

export const CATALOGS = load();
