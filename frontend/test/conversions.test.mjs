// Conversions Markdown et HTML de l'interface, qui ont besoin d'un vrai DOM (DOMPurify, ProseMirror) : exécutées dans
// Chromium (Playwright, voir helpers.mjs). markdown.js (anciens champs en Markdown, [[Nom]] devenus mentions),
// editor/sanitize.js (filtre du contenu des fiches rédigées), editor/schema.js (HTML <-> document de l'éditeur).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { browserPage, bundle } from './helpers.mjs';

const code = await bundle(`
import { isRichHtml, markdownToEditorHtml, renderMarkdown } from './src/markdown.js';
import { sanitizeDoc } from './src/editor/sanitize.js';
import { docToHtml, htmlToDoc, templateHtml } from './src/editor/schema.js';
const resolve = (name) => ({ vex: 'npc-3', 'la main grise': 'faction-7', port: 'place-2' })[name.toLowerCase()];
window.T = {
  md: (text) => markdownToEditorHtml(text, resolve),
  render: (text) => renderMarkdown(text, resolve),
  rich: isRichHtml,
  sanitize: sanitizeDoc,
  roundTrip: (html) => docToHtml(htmlToDoc(html)),
  template: (html) => templateHtml(htmlToDoc(html)),
  // Éléments et attributs d'un rendu, pour vérifier la liste blanche des anciens champs (audit FRONT-5).
  elements: (html) => {
    const box = document.createElement('div');
    box.innerHTML = html;
    return [...box.querySelectorAll('*')].map((el) => ({ tag: el.localName,
      attrs: Object.fromEntries([...el.attributes].map((a) => [a.name, a.value])) }));
  },
};`, { platform: 'browser' });

const ctx = await browserPage();
test.after(async () => { await ctx.browser?.close(); });
const opts = ctx.skip ? { skip: ctx.skip } : {};
if (!ctx.skip) {
  await ctx.page.setContent('<!doctype html><html><body></body></html>');
  await ctx.page.addScriptTag({ content: code, type: 'module' });
  await ctx.page.waitForFunction(() => window.T);
}
const run = (fn, ...args) => ctx.page.evaluate(([name, values]) => window.T[name](...values), [fn, args]);

test('isRichHtml : HTML de l\'éditeur contre ancien Markdown', opts, async () => {
  assert.equal(await run('rich', '<p>Texte</p>'), true);
  assert.equal(await run('rich', '  <h2>Titre</h2>'), true);
  assert.equal(await run('rich', '<div data-callout="secret"><p>x</p></div>'), true);
  assert.equal(await run('rich', '**gras** <p>pas en tête</p>'), false);
  assert.equal(await run('rich', ''), false);
});

test('Markdown vers l\'éditeur : [[Nom]] trouvé = mention, introuvable = texte signalé', opts, async () => {
  const html = await run('md', '**Vex** travaille pour [[La Main Grise]] au [[Port]] avec [[Personne]].');
  assert.match(html, /<strong>Vex<\/strong>/);
  assert.match(html, /<a class="mention" data-mention="faction:7">La Main Grise<\/a>/);
  assert.match(html, /<a class="mention" data-mention="place:2">Port<\/a>/);
  assert.match(html, /<span class="wikilink missing"[^>]*>Personne<\/span>/);
  assert.equal(await run('md', '<p>Déjà <em>HTML</em></p>'), '<p>Déjà <em>HTML</em></p>');   // laissé tel quel
  assert.equal(await run('md', ''), '');
});

test('Markdown affiché : GFM (tableaux, listes imbriquées), rien d\'actif ni d\'image externe', opts, async () => {
  const table = await run('render', '| Arme | Dégâts |\n|---|---|\n| Arbalète | 1d10+5 |');
  assert.match(table, /<table>[\s\S]*<td>Arbalète<\/td>/);
  const nested = await run('render', '- Acte I\n  - Scène 1\n- Acte II');
  assert.match(nested, /<li>Acte I<ul>\s*<li>Scène 1<\/li>/);
  const hostile = await run('render', '<script>alert(1)</script><img src="https://pixel.example/x.png" onerror="alert(2)">'
    + '<form><input></form>\n\n[lien](https://exemple.org) ![carte](/media/1/abc.webp)');
  assert.ok(!/script|onerror|pixel\.example|<form|<input/.test(hostile), hostile);
  assert.match(hostile, /<a href="https:\/\/exemple\.org" target="_blank" rel="noopener noreferrer">lien<\/a>/);
  assert.match(hostile, /<img src="\/media\/1\/abc\.webp" alt="carte">/);
});

test('Filtre des fiches rédigées : balises, attributs et valeurs de l\'éditeur seulement', opts, async () => {
  const out = await run('sanitize', [
    '<p data-align="center" style="color:red" onclick="x()">Centré</p>',
    '<p data-align="diagonal">Faux alignement</p>',
    '<h2 style="text-align: right">Collé depuis Word</h2>',
    '<a data-mention="npc:12" href="https://ailleurs" target="_blank">Vex</a>',
    '<a data-mention="javascript:1" href="javascript:alert(1)">piège</a>',
    '<a href="https://exemple.org" class="lien externe">site</a>',
    '<div data-callout="secret" class="callout x"><p>Chut</p></div>',
    '<div data-callout="autre"><p>Encadré inconnu</p></div>',
    '<img src="/media/4/abc-1.webp" data-doc-image="9"><img src="/media/4/abc.png"><img src="https://x/y.webp">',
    '<script>alert(1)</script><style>p{}</style><iframe src="x"></iframe>',
  ].join(''));
  assert.match(out, /<p data-align="center">Centré<\/p>/);
  assert.match(out, /<p>Faux alignement<\/p>/);
  assert.match(out, /<h2 data-align="right">Collé depuis Word<\/h2>/);
  assert.match(out, /<a data-mention="npc:12" href="#mention" class="mention" contenteditable="false">Vex<\/a>/);
  assert.match(out, /<a>piège<\/a>/);
  assert.match(out, /<a href="https:\/\/exemple\.org" target="_blank" rel="noopener noreferrer">site<\/a>/);
  assert.match(out, /<div data-callout="secret" class="callout"><p>Chut<\/p><\/div>/);
  assert.match(out, /<div><p>Encadré inconnu<\/p><\/div>/);
  assert.equal((out.match(/<img/g) || []).length, 1);
  assert.match(out, /<img src="\/media\/4\/abc-1\.webp" data-doc-image="9">/);
  assert.ok(!/script|style|iframe|onclick|color/.test(out), out);
});

test('HTML <-> document de l\'éditeur : aller-retour stable, rien de perdu', opts, async () => {
  const html = '<h2 data-align="center">Acte I</h2><p>Avec <a class="mention" data-mention="npc:3">Vex</a> et '
    + '<strong>gras</strong>, <em>italique</em>, <u>souligné</u>.</p>'
    + '<div class="callout" data-callout="secret"><p>Le coupable</p></div>'
    + '<div class="callout" data-callout="read"><p>À lire</p></div>'
    + '<ul><li><p>Un</p><ul><li><p>Un et demi</p></li></ul></li><li><p>Deux</p></li></ul>'
    + '<table><tbody><tr><th><p>Arme</p></th><th><p>Dégâts</p></th></tr><tr><td><p>Arbalète</p></td><td><p>1d10</p></td></tr></tbody></table>'
    + '<blockquote><p>Citation</p></blockquote><hr><p data-align="justify">Fin <span class="comment-anchor" data-comment="4">commentée</span></p>';
  const once = await run('roundTrip', html);
  assert.equal(await run('roundTrip', once), once);
  for (const part of ['data-mention="npc:3"', 'data-callout="secret"', 'data-callout="read"', '<table>', '<hr>',
    'data-align="center"', 'data-align="justify"', 'data-comment="4"', '<u>souligné</u>', 'Un et demi', '<blockquote>']) {
    assert.ok(once.includes(part), `${part} perdu : ${once}`);
  }
  assert.ok(!once.includes('<p></p>'));
});

test('Modèle tiré d\'une fiche : mentions en texte, images et commentaires retirés', opts, async () => {
  const out = await run('template', '<p>Voir <a class="mention" data-mention="npc:3">Vex</a> '
    + '<span class="comment-anchor" data-comment="2">ici</span></p><p><img src="/media/1/a.webp" data-doc-image="5"></p>');
  assert.match(out, /<p>Voir Vex ici<\/p>/);
  assert.ok(!/mention|comment|img/.test(out), out);
});

// Anciens champs en Markdown (audit FRONT-5). Contenus légitimes : sorties de renderMarkdown d'avant la liste
// blanche (lot 6), qui doivent rester identiques : Markdown GFM, [[Nom]], et HTML de l'éditeur collé dans du Markdown
// (mentions, encadrés, alignements, tableaux, images de fiche, commentaires).
const LEGIT = JSON.parse(readFileSync(new URL('./fixtures/markdown-legit.json', import.meta.url), 'utf8'));
const MD_TAGS = new Set(['p', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b', 'em', 'i', 'u', 's', 'strike',
  'del', 'code', 'pre', 'sub', 'sup', 'ul', 'ol', 'li', 'blockquote', 'hr', 'a', 'img', 'span', 'div', 'table', 'thead',
  'tbody', 'tr', 'th', 'td', 'colgroup', 'col',
  // contre-revue : balises sémantiques sans risque
  'mark', 'kbd', 'small', 'abbr', 'dl', 'dt', 'dd', 'caption', 'tfoot', 'ins', 'q', 'cite', 'figure', 'figcaption']);
const MD_CLASSES = /^(wikilink|missing|mention|callout|comment-anchor|language-[\w+#-]+)$/;
const MD_ATTRS = new Set(['href', 'rel', 'target', 'title', 'src', 'alt', 'class', 'align', 'start', 'colspan', 'rowspan',
  'data-node', 'data-mention', 'data-callout', 'data-doc-image', 'data-comment', 'data-align', 'data-colwidth',
  'width', 'height']);   // largeur et hauteur : images seulement, nombres (contre-revue)

test('Markdown affiché : contenus légitimes rendus à l\'identique', opts, async () => {
  assert.ok(LEGIT.length >= 40);
  for (const { name, text, html } of LEGIT) assert.equal(await run('render', text), html, name);
});

test('Markdown affiché : classes, id, data-*, dialog et liens //hôte neutralisés (FRONT-5)', opts, async () => {
  const cases = [
    // work-FRONT/xss_probe.cjs et xss_probe2.cjs de l'audit
    'Texte <div class="lightbox modal-backdrop">Session expirée : <a href="https://evil.example/login">reconnectez-vous</a></div>',
    'x <a href="//evil.example/login" target="_self">lien relatif au protocole</a>',
    'x <a href="javascript:alert(1)">js</a> <a href="data:text/html,<script>alert(1)</script>">data</a>',
    'x <img src=x onerror=alert(1)> <svg><script>alert(1)</script></svg> <iframe src="https://e"></iframe>',
    'x <dialog open class="palette-backdrop">faux dialogue</dialog> <details open ontoggle=alert(1)><summary>s</summary></details>',
    'x <a href="#" class="wikilink" data-node="npc-1">faux lien</a> <span id="link-url" name="x">id</span>',
    'x <video src="https://evil.example/a.mp4" autoplay></video><audio src="https://evil.example/a.mp3"></audio>',
    'Apparence <section data-entry-section="faux" data-entry-label="Sommaire piégé"><div data-entry-field="secrets">texte</div></section>',
    // variantes
    'x <a href="\\\\evil.example/a">a</a> <a href="/\\evil.example/b">b</a> <a href=" //evil.example/c">c</a> [d](//evil.example/d)',
    'x <div class="callout sheet-form" data-callout="autre" style="position:fixed;inset:0" role="dialog" aria-modal="true">y</div>',
    'x <span class="comment-anchor palette-backdrop" data-comment="abc" data-thread="1" data-own-escape="">z</span>',
    'x <a class="mention lightbox" data-mention="javascript:alert(1)" data-node="x" href="https://e.example" target="_top">m</a>',
    'x <p data-align="fixed" align="center" id="root">p</p><img src="/media/1/a.webp" class="lightbox" data-doc-image="x">',
    'x <form action="https://evil.example"><input name="password"><button>OK</button></form><style>*{}</style>',
    'x <math><mi>x</mi></math><object data="x"></object><embed src="x"><marquee>m</marquee><button popover>b</button>',
    // contre-revue : tabulation ou retour à la ligne, retirés de l'adresse par le navigateur (« /\t/hôte » = « //hôte »)
    'x <a href="/\t/evil.example/t">t</a> <a href="/\n/evil.example/n">n</a> <a href="\\\r\\evil.example/r">r</a>',    // contre-revue : dimensions non numériques ou hors des images, id des titres (écraserait un titre du sommaire de
    // l'éditeur par document.getElementById), balises obsolètes
    'x <img src="/media/1/a.webp" width="100%" height="1e9"><table width="9999"><tr><td height="5">t</td></tr></table>'
      + '<h2 id="introduction" name="root">Titre</h2><center>centré</center><font color="red" size="7">rouge</font>'
      + '<abbr title="ok" onclick="x()">PV</abbr><q cite="javascript:alert(1)">q</q>',
  ];
  const outs = [];
  for (const text of cases) {
    const html = await run('render', text);
    outs.push(html);
    for (const { tag, attrs } of await run('elements', html)) {
      assert.ok(MD_TAGS.has(tag), `<${tag}> gardé : ${html}`);
      for (const [name, value] of Object.entries(attrs)) {
        assert.ok(MD_ATTRS.has(name), `${name} gardé sur <${tag}> : ${html}`);
        if (name === 'class') for (const c of value.split(/\s+/)) assert.match(c, MD_CLASSES, html);
        if (name === 'href') assert.match(value, /^(https:\/\/[\w.]+\/?\w*|#)$/, html);   // ni //hôte, ni javascript:
        if (name === 'target') assert.equal(value, '_blank', html);
      }
    }
  }
  const all = outs.join('\n');
  for (const bad of ['lightbox', 'modal-backdrop', 'palette-backdrop', 'sheet-form', '"//', '/\\', '<dialog', '<details',
    '<section', 'data-entry', 'link-url', 'onerror', 'script', 'style', '<video', '<audio', '<iframe', 'javascript', 'role',
    'aria-', 'data-thread', 'data-own-escape', 'password', 'popover']) {
    assert.ok(!all.includes(bad), `${bad} : ${all}`);
  }
  // Le texte reste lisible, seules les balises et attributs tombent.
  assert.match(outs[0], /^<p>Texte <\/p><div>Session expirée : <a href="https:\/\/evil\.example\/login" target="_blank" rel="noopener noreferrer">reconnectez-vous<\/a><\/div>/);
  assert.match(outs[1], /<a>lien relatif au protocole<\/a>/);
  assert.match(outs[4], /faux dialogue/);
  assert.match(outs[5], /<a href="#" class="wikilink" data-node="npc-1">faux lien<\/a> <span>id<\/span>/);
  assert.match(outs[9], /<div class="callout">y<\/div>/);
  assert.match(outs[11], /<a class="mention" href="https:\/\/e\.example" target="_blank" rel="noopener noreferrer">m<\/a>/);
  assert.match(outs[12], /<p align="center">p<\/p><img src="\/media\/1\/a\.webp">/);
  assert.match(outs[15], /<a>t<\/a> <a>n<\/a> <a>r<\/a>/);
  for (const bad of ['width', 'height', 'id=', 'name=', '<center', '<font', 'color', 'onclick', 'cite=', 'javascript'])
    assert.ok(!outs[16].includes(bad), `${bad} : ${outs[16]}`);
  for (const good of ['<img src="/media/1/a.webp">', '<h2>Titre</h2>', 'centré', 'rouge', '<abbr title="ok">PV</abbr>', '<q>q</q>'])
    assert.ok(outs[16].includes(good), `${good} : ${outs[16]}`);
});
