// Plugin d'esbuild : remplace src/i18n/catalogs.js (lecture des catalogues sur le disque, pour Node) par un import
// statique de chaque src/i18n/<langue>/<zone>.json. Les catalogues sont ainsi dans le bundle, sans requête, et une
// nouvelle langue ou zone est prise sans rien déclarer. Utilisé par build.mjs et test/helpers.mjs (bundle).
import { readdirSync } from 'node:fs';
import path from 'node:path';

export function catalogsModule(i18nDir) {
  const lines = [];
  const langs = [];
  for (const dir of readdirSync(i18nDir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    if (!dir.isDirectory() || !/^[a-z]{2,3}$/.test(dir.name)) continue;
    const files = readdirSync(path.join(i18nDir, dir.name)).filter((f) => f.endsWith('.json')).sort();
    const names = files.map((file, i) => {
      const name = `${dir.name}_${i}`;
      lines.push(`import ${name} from ${JSON.stringify(`./${dir.name}/${file}`)};`);
      return name;
    });
    langs.push(`  ${JSON.stringify(dir.name)}: Object.assign({}, ${names.join(', ')}),`);
  }
  return `${lines.join('\n')}\nexport const CATALOGS = {\n${langs.join('\n')}\n};\n`;
}

export function i18nCatalogsPlugin() {
  return {
    name: 'maze-i18n-catalogs',
    setup(build) {
      build.onLoad({ filter: /[\\/]src[\\/]i18n[\\/]catalogs\.js$/ }, (args) => {
        const dir = path.dirname(args.path);
        const files = readdirSync(dir, { withFileTypes: true })
          .filter((d) => d.isDirectory() && /^[a-z]{2,3}$/.test(d.name))
          .flatMap((d) => readdirSync(path.join(dir, d.name)).map((f) => path.join(dir, d.name, f)));
        return { contents: catalogsModule(dir), loader: 'js', resolveDir: dir, watchFiles: files, watchDirs: [dir] };
      });
    },
  };
}
