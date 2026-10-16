# Contributing to Maze

Thank you for your interest in Maze! This guide explains how to propose a change and what a pull request needs before it can be merged. The [README](README.md) covers installation, configuration and the development setup; [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (in French) explains how things work and why.

## Ways to help

- **Translations.** The interface, the server messages and the Foundry module are in French and English. Better English wording, or a new language, is welcome: see [Translations](#translations).
- **Bug reports.** Describe what you did, what you expected and what happened. Include the Maze version (Administration > Mise à jour), your browser and the relevant server log lines (`docker compose logs --tail 100 maze`), without passwords, tokens or campaign content you do not want to share.
- **Game systems.** The core does not depend on any game system. The Foundry VTT module fills in actors for the Dark Heresy systems only; mappings for other systems are welcome.
- **Documentation**, in English or French: installation on other platforms, reverse proxies other than Caddy, corrections.
- **Features.** Open an issue describing the need before writing code. Maze has a single maintainer, and features that fit the project's scope are easier to accept when discussed first.

## Language

Issues, pull requests and reviews can be written in **English or French**.

The codebase currently follows this convention: identifiers (modules, functions, variables, API fields) are in English; comments, docstrings, log and error messages, the interface, the documentation and commit messages are in French. Follow the language of the surrounding code. New interface strings and server messages are written in French and translated into English in the same pull request: see [Translations](#translations).

## Development workflow

1. **Fork** the repository and create a branch from `main` with a short descriptive name (`fix-map-zoom`, `i18n-setup`).
2. **Set up** the development environment as described in [README > Development](README.md#development): Python 3.13 and Node 22. Playwright is only needed for browser journeys.
3. **Write the test first.** Every fix or feature comes with a test that fails on the code before your change:
   - server: `unittest`, in `backend/tests/`;
   - interface logic: `node --test`, in `frontend/test/`;
   - user-visible behaviour: a Playwright journey, in `frontend/e2e/` (a standalone script that takes the server URL; see the existing ones and `run-all.sh`).
4. **Keep pull requests small and focused.** One topic per pull request. Refactoring, formatting and behaviour changes go in separate pull requests.
5. **Run the checks**: targeted while you work, complete before the pull request. `scripts/check.sh` runs the same commands as CI (after `cd frontend && npm ci`):

       scripts/check.sh quick                         # while developing: only what your changes touch
       scripts/check.sh quick --e2e langue,scenarios  # same, plus these browser journeys
       NODE_PATH="$(npm root -g)" scripts/check.sh full   # before opening the pull request: everything

   `quick [ref]` compares your branch with `ref` (default `origin/main`, uncommitted and new files included), prints which checks it runs and why, and runs them: the server test module of each changed `backend/maze/X.py` (`tests/test_X.py`) and the test modules that import it, ruff on changed Python files, the build and all `node --test` tests for any change under `frontend/src/`, `test_i18n` and `i18n.test.mjs` for translation catalogs, `test_foundry` and `foundry-module.test.mjs` for the Foundry module, `test_migrations` for migrations, the journey `frontend/e2e/X.mjs` you changed. The full rule list is at the top of the script.

   `full` runs ruff, the whole server suite (test modules in parallel, `MAZE_TEST_JOBS`, default two per CPU up to 4), the build, the `node --test` tests in Chromium (`MAZE_TEST_BROWSER=1`) and every browser journey in parallel. Both exit non-zero on any failure and end with a summary.

   Browser journeys run in parallel: `MAZE_E2E_JOBS` journeys at a time (default: number of CPUs, up to 4; `1` runs them one after the other), each on its own Maze instance on ports `MAZE_E2E_PORT` (default 8790) to `MAZE_E2E_PORT + MAZE_E2E_JOBS - 1`, with its own data folder and logs. Each journey's output is printed as one block when it finishes. Single journeys: `frontend/e2e/run-all.sh langue scenarios`.

   Variables: `PYTHON` (server interpreter), `RUFF`, `NODE_PATH` (folder containing the global `playwright` module), `MAZE_NODE_PATHS` (extra `node_modules` folders for esbuild), `MAZE_STATIC_DIR` (built interface, default `frontend/dist`), `MAZE_BUILD_CMD` (build command, default `node build.mjs` in `frontend/`), `MAZE_E2E_KEEP=1` (keep journey data and screenshots).

6. **Open the pull request** against `main`, every commit signed off (see [Developer Certificate of Origin](#developer-certificate-of-origin)): explain what changes and why, how you tested it, and anything a server administrator must know when updating (migration, new variable, manual step). GitHub Actions runs the whole suite; it must be green before review.

## Project rules

These rules exist because breaking one of them has already caused a bug, or would break existing installations.

- **Migrations.** A schema change is a new SQL file in `backend/maze/migrations/`, numbered right after the highest one; `tests.test_migrations` checks the numbering. Never edit a migration that has already been released: Maze records each applied migration with its checksum. Existing databases must migrate without data loss, and existing backups and campaign exports must stay readable.
- **Entry fields.** The fields of NPCs, factions and places are declared once, in `backend/maze/entries.py`. Everything else (API models, history, Claude connector, Foundry export, import and export, interface) derives from it. After a change, regenerate the interface copy:

      cd backend && python -m maze.entries > ../frontend/src/entry-fields.generated.js

- **Editor schema.** Changing `frontend/src/editor/schema.js` requires bumping the version in `frontend/src/editor/schema-version.json`, using the fingerprint printed by `node build.mjs --schema-fingerprint`, and `SCHEMA_VERSION` in `backend/maze/collab.py`. Otherwise the build fails. This version is what stops a browser tab still running an older interface from damaging documents edited by others.
- **Foundry module.** Any change under `backend/maze/foundry_module/` requires bumping `version` in its `module.json`, or Foundry will not offer the update. CI checks it.
- **Dependencies.** Versions are pinned exactly: `backend/requirements.txt`, `frontend/package.json`, and the lockfile `frontend/package-lock.json`, installed with `npm ci`. Discuss any new dependency in an issue first. If one is accepted, update the lockfile in the same pull request.
- **Deployment scripts.** `deploy/update.sh`, `deploy/maze-updater.sh`, `docker-compose.yml` and the systemd units run on existing servers. A change must let the current version update itself without manual steps; if a manual step is unavoidable, say so clearly in the pull request. `backend/tests/test_deploy.py` replays these scripts with a simulated Docker.
- **Security.** Every route that receives an object identifier must check that the object's campaign is accessible to the current account (`auth.can_access`). Request bodies are size-limited (`backend/maze/limits.py`). Never commit secrets, real campaign data or copyrighted images: tests use invented names and generated images.
- **Translations.** No visible text outside `t()`, `tn()` or `pt()` in `frontend/src`, and every key translated into English: see [Translations](#translations). The tests fail otherwise.
- **Documentation.** Update `docs/ARCHITECTURE.md`, and the README or the French guide (`docs/fr/guide.md`), whenever behaviour, configuration or deployment changes. Document every new environment variable in the README's Configuration table; `tests.test_docs` checks it.

## Translations

Maze is translated gettext style: the French text is the key, and French has no catalog. The full guide, in English, is [frontend/src/i18n/README.md](frontend/src/i18n/README.md). The rules for any change:

- **Interface.** Every visible string goes through `t()` (text), `tn()` (a number decides the form: never `lien(s)`) or `pt(context, text)` (same French text, different meanings). This includes `title`, `aria-label`, `placeholder`, toasts, confirmations and error messages composed in the interface. Whole sentences with `{placeholders}`, never concatenated pieces. Translate at display time, never at module load (`N_()` marks a string translated later).
- **Server.** Error messages are French strings; when they contain values, build them with `msg("{name} introuvable", name=...)` (or `msgn()` for plurals) instead of an f-string. User data is never translated.
- **Keys.** The key is the exact French text, typographic apostrophe (’) included. Reuse an existing key (`common.json`) before adding one: the same key in two zones must have the same translation.
- **Catalogs.** Add the English translation of every new string in the same pull request: `frontend/src/i18n/en/<zone>.json` (zone of the file: `node frontend/test/i18n-scan.mjs --zones`) and `backend/maze/i18n/en/*.json`. English must stay complete; `node frontend/test/i18n-scan.mjs --missing <zone>` prints what is missing, `--format` sorts the catalogs. Follow the glossary (GM, NPC, sheet, page, place, relationship, read-aloud, GM secret…).
- **Tests.** `cd frontend && node --test "test/*.test.mjs"` (i18n tests included), `cd backend && python -m unittest tests.test_i18n`, and the browser journeys `langue` and `interface-anglaise` if you changed screens. Look at the screen in English: labels must not wrap or overflow.

**Proposing a new language.** Open an issue first, then follow "Adding a language" in [frontend/src/i18n/README.md](frontend/src/i18n/README.md): one catalog folder for the interface, one for the server, a `lang/<code>.json` file and an entry in `module.json` for the Foundry module. A new language may be partial (missing entries fall back to French); say in the pull request what is translated and who can review it.

## Developer Certificate of Origin

Maze is released under the MIT License (see the [README](README.md#license) and [LICENSE](LICENSE)). Every contribution is published under that same license.

Every commit must be signed off with `git commit -s`, which adds a line with your name and email address at the end of the message:

    Signed-off-by: Your Name <you@example.org>

By signing off, you certify that you wrote the change, or otherwise have the right to submit it under the project's license, as stated in the [Developer Certificate of Origin](https://developercertificate.org/). Use your real name or the name you are known by, and the email address of your commits (GitHub's `noreply` address is accepted).

Forgot it? `git commit --amend -s` fixes the last commit; `git rebase --signoff main` signs every commit of your branch (then `git push --force-with-lease` to your fork).

## Commit messages

One line, in the form "area : what changed", optionally followed by a paragraph explaining why, and the sign-off line. For example:

    Sauvegardes : écriture dans un .part renommé une fois complet, .part abandonnés retirés

    Signed-off-by: Your Name <you@example.org>

Pull requests are merged with a merge commit, so keep the commits of your branch meaningful.

## AI-assisted contributions

Maze itself is developed with substantial help from Claude, so AI-assisted contributions are welcome. The same rules apply: you are responsible for every line you submit, you must have run the tests yourself, and the pull request description must say which parts were generated.

## Reporting a security issue

Do not open a public issue for a vulnerability. Use GitHub's private vulnerability reporting (the repository's Security tab, "Report a vulnerability"), with the steps to reproduce and the affected version.
