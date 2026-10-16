# CLAUDE.md

Quick map for agents and contributors: where to change the code for a feature, and what to run to check it.

## What Maze is

- Self-hosted web workshop for tabletop RPG game masters: NPCs, factions, places, interactive maps and scenarios, written together in real time.
- Server: Python 3.13, Starlette, Pydantic, SQLite (WAL). Interface: React 19 built by esbuild, ProseMirror + Yjs editor, Cytoscape graph. Plus a Foundry VTT module, a Claude connector (MCP), French and English, MIT license.
- Identifiers in English; comments, messages, docs and commits in French (follow the surrounding code).
- Detailed reference (French): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Rules for a pull request: [CONTRIBUTING.md](CONTRIBUTING.md). Translations: [frontend/src/i18n/README.md](frontend/src/i18n/README.md). Do not duplicate them here: read the relevant section.

## Repository map

- `backend/maze/`: server, one module per domain (routes listed at the end of each module as `routes = [...]`, assembled in `app.py`).
- `backend/maze/migrations/`: numbered SQL migrations (`NNN_name.sql`).
- `backend/maze/i18n/`: server translation (`__init__.py`: `msg`, `msgn`, `request_locale`) and catalogs `en/*.json`.
- `backend/maze/foundry_module/`: Foundry VTT module served by Maze (`module.json`, `scripts/maze-sync.mjs`, `lang/`, `styles/`).
- `backend/tests/`: server tests (`unittest`, `test_*.py`, real uvicorn server for API tests).
- `frontend/src/`: interface. `api.js` (every REST call), `App.jsx` (routing, session), `components/` (screens; `scenarios/`, `maps/`), `editor/` (ProseMirror), `graph/` (Cytoscape), `i18n/`.
- `frontend/test/`: `node --test` tests of the interface logic. `frontend/e2e/`: Playwright journeys (`*.mjs`, `run-all.sh`).
- `frontend/build.mjs`: esbuild build (checks the editor schema fingerprint).
- `deploy/`: `update.sh`, `maze-updater.sh`, `systemd/`, `Caddyfile.maze`, Debian install guide.
- `docs/`: `ARCHITECTURE.md`, `fr/guide.md` (French user and admin guide).
- `site/`: presentation website (GitHub Pages), screenshots by `site/tools/capture.mjs`, see `site/README.md`.
- `.github/workflows/`: `ci.yml` (tests), `lockfile.yml`, `pages.yml`; helper scripts in `.github/scripts/`.

## Feature map

Order: database > server > interface > tests. Every REST call of the interface is in `frontend/src/api.js`.

**Accounts, sessions, language**
- DB: migrations `003_users.sql`, `007_session_key.sql`, `029_login_addresses.sql`, `030_user_locale.sql`.
- Server: `auth.py` (passwords, `current_user`, `require_admin`), `api.py` (`/api/auth/*`: login, logout, setup, password, locale), `throttle.py` (login attempts), `app.py` (`AuthMiddleware`), `i18n/__init__.py` (`request_locale`, `account_locale`), `config.py` (`MAZE_DEFAULT_LOCALE`).
- Interface: `components/LoginScreen.jsx`, `components/UserMenu.jsx` (language menu), `session.js`, `i18n/core.js`, `i18n/index.js`.
- Tests: `test_api` (`AccountsTest`, `SetupTest`), `test_limits` (`ThrottleTest`), `test_i18n` (`RequestLocaleTest`); e2e `langue`, `connexion-limites`.

**Campaigns and access**
- Server: `auth.can_access`, `auth.visible_campaign_ids` (single rule); `api.py`: `scoped()` (load a row by id and check its campaign: use it in every route that takes an id), `get_or_404`, `campaign_of`, campaign routes; `admin.py` (campaign members).
- Interface: `components/CampaignMenu.jsx`, `components/AdminPage.jsx` (`CampaignsSection`, `MembersDialog`).
- Tests: `test_api`, `test_connector` (visible campaigns).

**NPCs, factions, memberships, relations, relationship types**
- DB: `001_initial.sql`, `002_relation_types.sql`, `013_order_place_kinds.sql` (sort order), `025_entry_revision.sql`, `031_nested_factions.sql` (sub-factions in `memberships`).
- Server: `api.py` (`/api/npcs`, `/api/factions`, `/api/memberships`, `/api/relations`, `/api/relation-types`; `check_faction_member`, `faction_ancestors` for cycles), `schemas.py`, `defaults.py` (seeded relationship types and place kinds), `entry_history.py` (versions and comments of entries).
- Interface: `components/CharactersView.jsx` (index as a faction tree, pure logic in `factionTree.js`), `EntityPanel.jsx` (sheet, `MembersSection`, `FactionParentsEditor`), `EntryDraft.jsx` (form, `base_revision`, conflicts), `EntryNotes.jsx` (outline, comments, history), `RelationDialog.jsx`, `RelationPanel.jsx`, `RelationTypePicker.jsx`, `relationTypes.js`, `CreateDialog.jsx`.
- Tests: `test_api` (`RelationTypesTest`), `test_factions`, `test_entries`, `test_connector`; node `elements.test.mjs`, `factionTree.test.mjs`; e2e `edition-fiches`, `factions-imbriquees`.

**Entry field registry (NPC, faction, place fields)**
- `backend/maze/entries.py` is the single source: API models (`schemas.py`), history (`entry_history.FIELDS`), connector (`mcp.ENTRY_FIELDS`), Foundry pages, import (`transfer.py`), interface.
- Generated copy: `frontend/src/entry-fields.generated.js` (never edit by hand), read by `frontend/src/fields.js`.
- Tests: `test_registry` (triggers, columns, generated file, every field end to end); node `fields.test.mjs`.

**Places and place kinds**
- DB: `010_places.sql`, `013_order_place_kinds.sql`, `019_kind_images.sql`, `020_place_pins.sql`.
- Server: `places.py` (tree, links, kinds, pins, depth), `defaults.py`, `imaging.process_icon`.
- Interface: `components/PlacesView.jsx` (also the place kinds editor), `PlaceTree.jsx`, `PlaceCrumbs.jsx`, `PlaceIcon.jsx`, `MoveDialog.jsx`, `places.js`, `constants.js` (`PLACE_ICONS`, `PLACE_KINDS`).
- Tests: `test_api` (`PlacesTest`, `DeepTreeTest`); node `places.test.mjs`.

**Interactive maps**
- DB: `017_maps.sql`, `018_map_preview.sql`, `019_kind_images.sql` (`pin_style`).
- Server: `maps.py`, `imaging.py` (`MAP_SIDE`, `MAP_PREVIEW_SIDE`), `mcp_maps.py` (connector tools).
- Interface: `components/MapsView.jsx`, `components/maps/` (`MapCanvas.jsx`, `useMapViewport.js`, `rings.js`, `MapRings.jsx`, `MapZoom.jsx`, `PlacePanel.jsx`, `PinList.jsx`, `MapDialog.jsx`, `mapTree.js`, `useMapsData.js`).
- Tests: `test_maps`, `test_api` (`LargeMapTest`), `test_backup` (`LargeMapRoundTripTest`); node `rings.test.mjs`; e2e `cartes-interactives`.

**Scenarios and written pages (editor, real time, versions, comments)**
- DB: `004_documents.sql`, `005_collab.sql`, `006_comments.sql`, `009_versions_templates.sql`, `011_scenario_maps.sql`, `012_doc_links.sql`, `027_live_epoch_random.sql`.
- Server: `documents.py` (tree, content, `has_unsaved_edits`), `collab.py` (WebSocket relay, `SCHEMA_VERSION`), `versions.py`, `comments.py`, `templates.py`, `scenemap.py` (scenario map), `htmlimg.py`.
- Interface: `components/ScenariosView.jsx`, `components/scenarios/` (`DocTree.jsx`, `DocBar.jsx`, `DocSide.jsx`, `useDocVersions.js`, `useDocComments.js`, `useScenarioData.js`...), `DocDialogs.jsx`, `CommentsPanel.jsx`, `HistoryPanel.jsx`, `SessionNotices.jsx`, `RichField.jsx` (long entry fields), `cast.js`.
- Editor: `editor/schema.js`, `editor/schema-version.json`, `editor/schemaVersion.js`, `editor/Editor.jsx`, `editor/live.js` (real-time client), `editor/local.js`, `editor/sanitize.js`, `editor/commands.js`, `editor/calloutLabels.js`; `frontend/schema-fingerprint.mjs`.
- Tests: `test_api` (`DocumentsTest`, `CollabTest`, `CommentsTest`, `ScenarioMapTest`), `test_cast_rules`, `test_concurrency`; node `schema.test.mjs`, `conversions.test.mjs`, `cast.test.mjs`, `session.test.mjs`; e2e `scenarios`, `edition-redigees`, `edition-avis`, `edition-version`.

**Images and library**
- DB: `016_library_images.sql`.
- Server: `imaging.py` (WebP, thumbnails, atomic writes), `mediafiles.py` (allowed extensions), `media_admin.py` (Administration > Images, library), `image_copy.py` (reuse an image), `api.py` (gallery upload), `fetch.py` (download for the connector).
- Interface: `components/Gallery.jsx`, `ImagePicker.jsx`, `ImagesSection.jsx` (multiple selection, bulk delete, sort; pure logic in `imageSelection.js`), `Lightbox.jsx`.
- Tests: `test_media`, `test_api` (`ImagesTest`), `test_backup` (`AtomicImageTest`); node `imageSelection.test.mjs`; e2e `images-suppression`.

**Search**
- DB: `008_search.sql`, `014_rich_fields.sql`, `028_search_legacy_fields.sql` (FTS5 triggers).
- Server: `search.py`. Interface: `components/SearchPalette.jsx` (Ctrl+K).
- Tests: `test_api` (`SearchTest`), `test_registry` (`test_search`).

**Relationship graph (Map tab)**
- Server: `api.campaign_graph`, `api.save_positions`, `scenemap.py`.
- Interface: `graph/elements.js` (`buildElements`: nodes, edges, filters), `graph/style.js` (`graphStyle`), `graph/GraphView.jsx`, `graph/declutter.js`, `components/Workspace.jsx` (tab, selection), `Legend.jsx`.
- Tests: node `elements.test.mjs`, `declutter.test.mjs`.

**Timeline (optional module)**
- DB: `032_timeline.sql` (`campaigns.timeline_enabled`, `timeline_events`, `timeline_mentions`, search type `event`).
- Server: `timeline.py` (events, order, `sync_event_mentions`), `api.py` (`timeline_enabled` on campaigns), `search.py`; export / import in `transfer.py`; connector tools `list_events`, `create_event`, `update_event` in `mcp.py`.
- Interface: `components/TimelineView.jsx` (list and ordinal timeline), `timeline.js`; "Chronologie" section on NPC, faction and place sheets.
- Tests: `test_timeline`, `test_api` (`TimelineTransferTest`), `test_connector` (`test_timeline`); node `timeline.test.mjs`; e2e `chronologie`.

**Campaign export and import**
- Server: `transfer.py` (`SECTIONS`, import limits, id rewriting), `limits.py` (`MAZE_MAX_IMPORT_MB`).
- Interface: `exportCampaign` in `api.js`, `components/CampaignMenu.jsx`, `AdminPage.jsx`.
- Tests: `test_api` (`CampaignTransferTest`, `ImportNameTest`, `TimelineTransferTest`), `test_limits` (`ImportLimitsTest`), `test_factions` (`NestedFactionsTransferTest`), `test_registry`.

**Backups and restore**
- Server: `backup.py` (CLI `python -m maze.backup`, rotation, restore), `admin.py` (`/api/admin/backups*`), `app.py` (`RestoringMiddleware`); host timer `deploy/systemd/maze-backup.*`.
- Interface: `components/AdminPage.jsx` (`BackupsSection`).
- Tests: `test_backup`, `test_api` (`ImportRestoreTest`); e2e `sauvegardes-acces`.

**Administration**
- Server: `admin.py` (users, campaigns, backups, system), `media_admin.py`, `connector.py`, `foundry.py`.
- Interface: `components/AdminPage.jsx` (`UsersSection`, `CampaignsSection`, `BackupsSection`, `UpdatesSection`), `ImagesSection.jsx`, `ClaudeSection.jsx`, `FoundrySection.jsx`.

**Updates**
- Host: `deploy/update.sh` (build, `-maj` backup, rollback), `deploy/maze-updater.sh` (`control/` > `status/`), `deploy/systemd/maze-updater.*`.
- Server: `admin.py` (`system_status`, `_status_source`, `UPDATER_MESSAGES`).
- Interface: `components/AdminPage.jsx` (`UpdatesSection`).
- Tests: `test_deploy` (scripts replayed with a simulated Docker), `test_api` (`AdminSystemTest`); e2e `mise-a-jour`.

**Claude connector (MCP)**
- DB: `015_claude_connector.sql`, `024_mcp_log_campaign_name.sql`.
- Server: `mcp.py` (`/mcp`, tools, `Context`), `mcp_images.py`, `mcp_maps.py`, `oauth.py` (OAuth 2.1), `connector.py` (settings, grants, log).
- Interface: `components/ClaudeSection.jsx`.
- Tests: `test_connector`.

**Foundry VTT**
- DB: `021_foundry.sql`, `023_foundry_token_creator.sql`.
- Server: `foundry.py` (tokens, `/api/foundry/v1/`, `build_export`, `module_zip`), `foundry_html.py` (HTML of the pages).
- Module: `backend/maze/foundry_module/` (bump `version` in `module.json` on any change).
- Interface: `components/FoundrySection.jsx`.
- Tests: `test_foundry`, `test_i18n` (`FoundryExportLanguageTest`); node `foundry-module.test.mjs`.

**Demo campaign**
- `backend/maze/demo.py` (`python -m maze.demo --lang en`), texts in `backend/maze/i18n/en/demo.json`. Test: `test_demo`.

**Translations**
- Interface: `frontend/src/i18n/core.js` (`t`, `tn`, `pt`, `N_`), `index.js` (`useLocale`), catalogs `frontend/src/i18n/en/{common,admin,scenarios,places,characters}.json`, `frontend/i18n-catalogs.mjs`.
- Server: `backend/maze/i18n/__init__.py`, catalogs `backend/maze/i18n/en/{server,seeds,foundry,oauth,demo}.json`.
- Foundry module: `foundry_module/lang/fr.json`, `en.json`.
- Tests: node `i18n.test.mjs`, `i18n-pending.test.mjs` (list `frontend/test/i18n-pending.json`, only shrinks), `i18n-core.test.mjs`; `test_i18n`; e2e `langue`, `interface-anglaise`.

**Website**
- `site/index.html`, `site/style.css`, `site/img/*.webp`; screenshots by `site/tools/capture.mjs` (see `site/README.md`); published by `.github/workflows/pages.yml`.

### Recipe: add a field to an entry

1. Migration: new column, and recreate the triggers that must follow it (`trg_*_revision` if tracked by history, `trg_search_*` if indexed).
2. One line in `backend/maze/entries.py` (type, group, secret, history, search, connector, Foundry role).
3. `cd backend && python -m maze.entries > ../frontend/src/entry-fields.generated.js`
4. English label in `frontend/src/i18n/en/characters.json` (labels are keys of zone `characters`).
5. `cd backend && python -m unittest tests.test_registry tests.test_i18n`, then node `fields.test.mjs` and `i18n.test.mjs`: they say what is missing.

### Recipe: add a migration

1. New file `backend/maze/migrations/NNN_name.sql`, NNN = highest existing + 1 (currently 032). Never edit a released one (checksum recorded).
2. No `BEGIN`/`COMMIT` in the file: `db.migrate` wraps it in one transaction with foreign keys suspended, then checked.
3. New table whose id is cited elsewhere: `INTEGER PRIMARY KEY AUTOINCREMENT`. Rebuilt table: recreate all its indexes and triggers.
4. Data that belongs to a campaign: give it a foreign key to its campaign (or parent row) with `ON DELETE CASCADE`, since deleting a campaign relies on cascades; add it to export / import (`transfer.SECTIONS`); restore needs nothing (whole database).
5. Test the upgrade from an existing database: `cd backend && python -m unittest tests.test_migrations` plus the domain test.
6. Mention the migration in the pull request and in `docs/ARCHITECTURE.md`.

## Commands

Run from the repository root unless stated.

- `scripts/check.sh quick`: targeted tests for the modified files.
- `scripts/check.sh full`: whole suite, once before a pull request.
- `frontend/e2e/run-all.sh [journey...]`: browser journeys, each on a fresh instance; `MAZE_E2E_JOBS=<n>` to run them in parallel (default: based on CPU cores). Needs Playwright (`NODE_PATH="$(npm root -g)"`) and the built interface.
- One server test module: `cd backend && python -m unittest tests.test_xxx` (one class: `tests.test_api.SearchTest`).
- All server tests: `cd backend && python -m unittest discover -s tests`
- One node test: `cd frontend && node --test test/places.test.mjs`; all: `cd frontend && npm test`.
- Ruff (as in CI, ruff 0.16.8): `cd backend && ruff check --isolated --select F,E9,B --line-length 120 maze tests`
- Build the interface: `cd frontend && npm ci && npm run build` (`npm run watch` while developing).
- Editor schema fingerprint: `cd frontend && node build.mjs --schema-fingerprint`
- Missing translations: `node frontend/test/i18n-scan.mjs --missing <zone>`; zone of each file: `--zones`; sort catalogs: `--format`.
- Local server: `cd backend && MAZE_DATA_DIR=../data MAZE_STATIC_DIR=../frontend/dist uvicorn maze.app:app_factory --factory --reload --port 8000`

## Working method

- Targeted tests while developing (the test module, node test or journey of the domain); full suite once, before the pull request.
- Write the test first: it must fail on the code before the change.
- Every visible string goes through `t()`, `tn()` or `pt()` (interface) or `msg()` / `msgn()` (server, values as parameters, never an f-string). The French sentence is the key; the English catalog must be complete in the same pull request.
- Migrations are numbered, contiguous and run in one transaction each; existing databases, backups and exports must keep working.
- Any change under `backend/maze/foundry_module/` bumps `version` in its `module.json` (CI checks it).
- Editor schema change: bump `editor/schema-version.json` (new fingerprint) and `collab.SCHEMA_VERSION` together.
- Update the docs with the behaviour: `docs/ARCHITECTURE.md`, README or `docs/fr/guide.md`. Every new environment variable goes into the README Configuration table (`tests.test_docs` checks it).
- Never commit real campaign data, secrets, copyrighted images or personal infrastructure (hosts, IPs, paths): invented names and generated images only; `example.org` and private example IPs.
- Dependencies pinned exactly; a new one is discussed in an issue first, lockfile updated with it.
- Commits in French, one line "Area : what changed" (as `git log`), optional paragraph, signed off with `git commit -s` (DCO). One topic per pull request.

## Known pitfalls

- No local variable named `t` in `frontend/src`: it shadows the translation function (esbuild renames it `t2`, the scan fails).
- Translate at display time, never at module load: `N_()` to mark, `t()` where shown (accessors in `constants.js`).
- Typographic apostrophe (’) is part of French keys; same key in two zones = same translation, else `pt(context, text)`.
- Enum values stored in French (`vivant`, `mort`, relationship keys `allie`, place kinds `lieu`, `batiment`...) are keys: never translate or rename them.
- User data (names, campaign relationship types and place kinds, roles) is never translated.
- Exception messages: read with `i18n.message(exc)`, not `str(exc)` (loses the template).
- Claude connector tool descriptions and messages stay in French (read by Claude), as do server logs.
- Foundry hashes: page HTML in French must stay byte-identical, or every imported page looks modified in Foundry; fixed hashes in `foundry-module.test.mjs`.
- `maze-sync.mjs` has no `import` / `export`: the node test loads it as is in `node:vm`.
- `base_revision`: PATCH of an entry compares it and returns 409 with the current state on conflict; required by connector `update_document`, optional for `update_entry`. Send only changed fields.
- Every route that takes an object id checks its campaign (`api.scoped` / `auth.can_access`); request bodies are size-limited (`limits.py`).
- Database work runs off the event loop: `@api.threaded` routes, or `await api.in_tx(request, work)`; GET is a read-only transaction.
- Server tests wait with `test_api.wait_until`, not a fixed `sleep`.
- Images under `media/` are always produced by Maze (WebP, JPEG for large map backgrounds), written to a temp name then renamed.
- `entry-fields.generated.js` is generated: rerun `python -m maze.entries` instead of editing it.
- Browser journeys must accept an empty database and take the server URL as argument; `run-all.sh` starts instances with `MAZE_DEFAULT_LOCALE=fr`.
- Avoid running several browser sessions (e2e, screenshots) at the same time.
