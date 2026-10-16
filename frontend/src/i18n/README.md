# Translating Maze

Maze is written in French. The translation layer is **gettext style**: the French sentence *is* the key. You wrap the existing text, you never invent keys:

```jsx
import { t, tn, pt } from '../i18n/index.js';

<button>{t('Enregistrer')}</button>
<p>{t('{n} lieux trouvés', { n })}</p>
<span>{tn('{n} lieu', '{n} lieux', count)}</span>
```

A key missing from a catalog falls back to the French text, so the interface never shows a blank or a raw key. French has no catalog: it is the source language.

## Files

| Path | Role |
|---|---|
| `frontend/src/i18n/core.js` | `t`, `tn`, `pt`, `N_`, current language, formats. No React: importable by Node tests |
| `frontend/src/i18n/index.js` | Everything from `core.js`, plus `useLocale()`; `t`/`tn`/`pt` accept React elements as parameters |
| `frontend/src/i18n/catalogs.js` | Catalog loader. Replaced at build time by static imports (`frontend/i18n-catalogs.mjs`, an esbuild plugin used by `build.mjs` and the test bundles): catalogs are in the bundle, no network request |
| `frontend/src/i18n/<lang>/<zone>.json` | One JSON object per file: French text > translation |
| `frontend/test/i18n.test.mjs`, `i18n-pending.test.mjs`, `i18n-core.test.mjs` | Checks run in CI (see [Checking](#checking)) |
| `frontend/test/i18n-scan.mjs` | Static analysis shared by the tests, and a command line tool |
| `frontend/test/i18n-pending.json` | Files not translated yet (the ratchet list) |
| `frontend/e2e/langue.mjs` | Browser journey: language choice, English screens, server messages |
| `backend/maze/i18n/` | Server side: same principle, see [Server messages](#server-messages) |

## API

From `i18n/index.js` (components) or `i18n/core.js` (plain modules):

| Name | Use |
|---|---|
| `t(text, params?)` | Translated text. `params` fills `{name}` placeholders |
| `tn(singular, plural, n, params?)` | Plural, chosen with `Intl.PluralRules` of the current language. `n` is added to `params` automatically. Catalog key: the singular; value: `{ "one": "...", "other": "..." }` (all categories of that language) |
| `pt(context, text, params?)` | Same French text, different translations depending on where it is: `pt('nav', 'Carte')` (the relationship graph tab) vs `t('Carte')`. Catalog key: `"nav\|Carte"`. Context: letters, digits, `-`, `_` |
| `N_(text)` | Returns `text` unchanged. Marks a string for extraction when it is translated later, at display time: `const LABELS = { a: N_('Texte') }` then `t(LABELS[k])` in the render |
| `useLocale()` | Current language; the component re-renders when it changes. `App` calls it, so the whole tree re-renders |
| `getLocale()`, `setLocale(code)`, `subscribe(fn)` | Current language (`fr`, `en`), change it (`<html lang>` follows), listen to changes |
| `LOCALES`, `SOURCE_LOCALE`, `languageName(code)` | Known languages (`['fr', 'en']`), `'fr'`, native name (`Français`, `English`) |
| `applyPreferences({ account, fallback })`, `resolveLocale(...)`, `storedLocale()`, `storeLocale(code)` | Language choice (see [Language choice](#language-choice)) |
| `formatDate(value, options)`, `dateFormat(options)` | Dates in the current language (`Intl.DateTimeFormat`, cached). `fr-FR` in French, exactly as before translation |
| `formatNumber(n, options)`, `numberFormat(options)` | Numbers |
| `formatList(items)`, `relativeTime(value, unit)` | `A, B et C` / `A, B, and C`; `il y a 5 minutes` / `5 minutes ago` |
| `compareText(a, b, options)` | Sorting, instead of `a.localeCompare(b, 'fr')` |

## Conventions

**Which function.** `t` for any visible text: JSX text, `title`, `aria-label`, `placeholder`, `label`, `alt`, toast and confirm messages, document titles, error messages composed in the interface. `tn` whenever a number decides the form: never write `image(s)`, `lien(s)` or `${n > 1 ? 's' : ''}`. `pt` only when one French text needs two translations.

**Whole sentences, with placeholders.** Never concatenate pieces of sentences: word order and punctuation change between languages (French puts a space before `:`, English does not).

```jsx
// No
toast(`${name} rejoint la carte`);
<p>Le fichier <code>{file}</code> est prêt.</p>     // three pieces
// Yes
toast(t('{name} rejoint la carte', { name }));
<p>{t('Le fichier {file} est prêt.', { file: <code>{file}</code> })}</p>
```

React elements as parameters work with the functions of `index.js` (the result is a fragment). Placeholders are `{word}` (letters, digits, `_`), the same on both sides of the catalog; literal braces are not supported. Numbers are inserted as `String(n)`, as before; use `formatNumber(n)` explicitly when grouping is wanted.

**Text outside components.** Translate at display time, never at import time: the language can change while the module stays loaded, and `t()` called at the top level of a module would freeze the language of the first load. Three patterns, all used in the shared files:

- getters, when existing code reads a property (`constants.js`): `vivant: { get label() { return t('Vivant'); } }` (consumers keep `NPC_STATUS[s].label`, translated on each read; do not copy these objects at module level, `{ ...v }` freezes the text);
- `N_` in the table and `t()` at display (`TREE_LABEL` in `App.jsx`);
- functions: `[..., () => t('Lieux')]` (`NAV` in `App.jsx`), `FIELD_LABELS` in `api.js`.

The same applies to `useMemo`, `useState` initialisers and non-React objects (Cytoscape styles, ProseMirror plugins, canvases): add the value of `useLocale()` to their dependencies, or translate when rendering.

**Dates, numbers, sorting.** No `'fr-FR'` or `'fr'` written in the code (the ratchet test flags them in `toLocale*`, `Intl.*` and `localeCompare` calls). Create formatters at render time with `dateFormat(options)`, not at module level.

**Which module to import.** Components import from `i18n/index.js`. Plain modules imported directly by Node tests (`fields.js`, `places.js`, `session.js`, `util.js`, `cast.js`, `api.js`, `constants.js`…) import from `i18n/core.js`, which does not need React. Node tests run in French: outside a browser page, the language of the system is ignored; tests that run in Chromium open a `fr-FR` page.

**Do not name a local variable `t`, `tn`, `pt` or `N_`** in a file that imports them (`list.map((t) => ...)`): it hides the function. The test reports it (esbuild renames such variables `t2`, which gives them away).

**User data is never translated**: campaign, NPC and place names, relation types and place types of a campaign (stored in the database, created in French by older versions), roles typed by the GMs. Built-in fallbacks (`PLACE_KINDS`, the `Autre` relation type) are translated.

**Server messages arrive translated.** `api.js` sends `Accept-Language` with the interface language, and the server translates `detail` (see below). Display `err.message` as it is; do not wrap server messages in `t()`.

**Entry field labels.** The labels of NPC, faction and place fields come from the server registry (`backend/maze/entries.py`), copied to `entry-fields.generated.js`. They are translated **in the interface, zone `characters`**: the scanner treats every `"label"` of that file as a marked key (like `N_`), so its translations go to `en/characters.json`; consumers display `t(field.label)` (`fields.js` builds the field lists; translate where the label is shown, not when the list is built at import time). The generated file is never edited by hand and is not checked for hard-coded text.

**False positives** of the hard-coded text check: add `// i18n-ignore` at the end of the line (or `{/* i18n-ignore */}` in JSX), or `// i18n-ignore-next-line` on the line before. Use it for text that is not interface text (a sample name, a code, a regular expression source).

### Glossary

Keep the terms consistent (British spelling, as in the README):

| French | English |
|---|---|
| PNJ | NPC |
| MJ | GM |
| fiche (PNJ, faction, lieu) | sheet |
| fiche rédigée, page, scénario | page, scenario |
| fiche de campagne | campaign page |
| lieu, sous-lieu | place, sub-place |
| type de lieu | place type |
| carte (onglet des relations) | graph (`pt('nav', 'Carte')`) |
| carte interactive | interactive map |
| épingle | pin |
| nature de lien | relation type |
| appartenance | membership |
| encadré « À lire » / « Secret » | "Read aloud" box / "Secret" box |
| version, historique | version, history |
| sauvegarde | backup |
| connecteur Claude | Claude connector |

## Zones

Catalogs are split by language **and** by zone so that several people can translate in parallel without merge conflicts: each zone has its own file, and each file of `frontend/src` belongs to exactly one zone (checked by the tests; rules in `ZONE_RULES` of `frontend/test/i18n-scan.mjs`, printed by `node test/i18n-scan.mjs --zones`).

| Zone | Files and folders of `frontend/src/` |
|---|---|
| common | `App.jsx`, `main.jsx`, `api.js`, `constants.js`, `util.js`, `viewport.js`, `markdown.js`, `relationTypes.js`, `cast.js`, `styles.css`, `i18n/`, `components/ui.jsx`, `components/UserMenu.jsx`, `components/LoginScreen.jsx`, `components/MoveDialog.jsx`, `components/RichField.jsx`, `components/PlaceIcon.jsx`, `components/treeDrag.js`, `components/mentionEntities.js` |
| admin | `components/AdminPage.jsx`, `components/ClaudeSection.jsx`, `components/ImagesSection.jsx`, `components/FoundrySection.jsx`, `components/CampaignMenu.jsx`, `components/SessionNotices.jsx`, `components/SearchPalette.jsx`, `session.js` |
| scenarios | `components/ScenariosView.jsx`, `components/scenarios/`, `editor/`, `components/DocDialogs.jsx`, `components/HistoryPanel.jsx`, `components/CommentsPanel.jsx`, `components/ScenarioFilter.jsx` |
| places | `components/PlacesView.jsx`, `components/PlaceTree.jsx`, `components/PlaceCrumbs.jsx`, `components/MapsView.jsx`, `components/maps/`, `places.js` |
| characters | `components/CharactersView.jsx`, `components/EntityPanel.jsx`, `components/EntryNotes.jsx`, `components/EntryDraft.jsx`, `components/Workspace.jsx`, `components/Gallery.jsx`, `components/Lightbox.jsx`, `components/ImagePicker.jsx`, `components/CreateDialog.jsx`, `components/RelationDialog.jsx`, `components/RelationPanel.jsx`, `components/RelationTypePicker.jsx`, `components/Legend.jsx`, `graph/`, `fields.js`, `entry-fields.generated.js` |

Rules when translating a zone:

- Edit only the files of your zone, and only your zone's catalog (`en/<zone>.json`). `common.json` and the shared files belong to the `common` zone.
- **Before adding a key, look for it in `common.json`** (and the other catalogs): `t('Annuler')`, `t('Enregistrer')`, `t('Supprimer')`, `t('Fermer')`, `t('Nom')`, the NPC statuses, place types… already exist. Reuse them as they are; do not add them again with another translation.
- The same key in two zone files is tolerated only with the same value (two people translating in parallel may both add it). Different values fail the tests: use `pt(context, ...)` if the meaning really differs.
- Catalogs are sorted, one entry per line (`node test/i18n-scan.mjs --format` rewrites them).
- When a file is done, remove its line from `frontend/test/i18n-pending.json`. That file lists the untranslated files by zone, one line per file, sorted; each zone is edited only by whoever translates it, so parallel branches merge without conflicts. The test fails if a listed file has nothing left to translate.

## Language choice

Order: account setting (account menu > Language: Automatic / Français / English, `users.locale`, `PUT /api/auth/locale`), then the choice made on the login screen in this browser (`localStorage`, key `maze:locale`; changing the account setting updates it too), then the browser languages (the first one Maze knows), then the instance default (`MAZE_DEFAULT_LOCALE`, `fr` by default, returned by `/api/auth/status` as `default_locale`). `App` applies it with `applyPreferences()` as soon as `/api/auth/status` answers.

## Server messages

`backend/maze/i18n/` (Python): catalogs `backend/maze/i18n/<lang>/<zone>.json` (zones `server`: API error messages; `seeds`: data created with each campaign; any other `.json` file in the folder is read too), same format. Messages are translated **when the response is sent**, in the language of the signed-in account, else the `Accept-Language` header, else `MAZE_DEFAULT_LOCALE`:

- the error handlers of `app.py` translate `detail` of every `HTTPException` and the heading of 422 errors (`Données invalides`); field details of 422 errors are composed by `api.js`;
- `I18nMiddleware` translates `detail` of JSON error responses written directly (middlewares, `JSONResponse({"detail": ...})`), by exact key or by recognising a template of the catalog;
- plain messages need nothing: `raise HTTPException(404, "Fichier introuvable")` is translated by exact key;
- messages built with values use `msg()` instead of an f-string: `raise HTTPException(404, msg("{what} introuvable", what=msg("Campagne")))`. A `msg` is a `str` (the French text, for logs, tests and the Claude connector) that keeps its template and parameters; a parameter that is itself a `msg` is translated too, a plain string (user data) never. `msgn(singular, plural, n, **params)` for plurals. `i18n.translate(text, locale)` and `i18n.request_locale(scope)` for anything else (a JSON field other than `detail`).

`backend/tests/test_i18n.py` checks the server catalogs (valid JSON, same placeholders, plural forms, every key present in the server code) and the mechanism.

## Checking

```sh
cd frontend
node --test "test/*.test.mjs"                  # all interface tests, i18n included (MAZE_TEST_BROWSER=1 for the Chromium ones)
node --test test/i18n.test.mjs test/i18n-pending.test.mjs test/i18n-core.test.mjs
node test/i18n-scan.mjs --missing scenarios    # entries to add to en/scenarios.json (JSON skeleton)
node test/i18n-scan.mjs --report components/PlacesView.jsx   # hard-coded French left in a file
node test/i18n-scan.mjs --format               # sort catalogs and the pending list
cd backend && python -m unittest tests.test_i18n
NODE_PATH="$(npm root -g)" frontend/e2e/run-all.sh langue   # browser journey (after npm run build)
```

The tests check that every literal `t('...')`, `tn(...)`, `pt(...)` and `N_('...')` of `frontend/src` has an English translation (files still listed in `i18n-pending.json` excepted), that no catalog entry is unused, that placeholders are identical on both sides, that plural entries have every form of the language, that calls are well formed (no concatenation or template literal as the key, parameters given), and, for every file not in the pending list, that no French text is left hard-coded (heuristic: accented letters or common French words in JSX text, in `title`, `placeholder`, `aria-label`, `label`… and in arguments of `toast` or `confirm`).

To see the interface in English: set the browser language to English, or choose English in the account menu or on the login screen. Browser journeys (`frontend/e2e/`) open `fr-FR` browsers and expect French; `run-all.sh` starts each instance with `MAZE_DEFAULT_LOCALE=fr`.

## Adding a language

1. Copy `frontend/src/i18n/en/` to `frontend/src/i18n/<code>/` (two or three lowercase letters, e.g. `de`) and translate the values; plural entries need the categories of that language (`new Intl.PluralRules('<code>').resolvedOptions().pluralCategories`).
2. Optionally, the same for the server: `backend/maze/i18n/<code>/`.

Nothing else: the language appears in the selectors under its own name, `MAZE_DEFAULT_LOCALE=<code>` accepts it, and the tests check its catalogs. Only English is required to be complete; other languages may be partial (missing entries fall back to French).
