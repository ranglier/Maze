# Maze

**A self-hosted workshop for tabletop RPG game masters: characters, factions, places, maps and scenarios, written together in real time.**

Maze is a web application for game masters who prepare their campaigns alone or with co-GMs. NPCs and factions live on a relationship graph, places in a tree with interactive maps, and scenarios in a collaborative editor that links to all of them. It runs as a single Docker container with a SQLite database, on your own server.

> **Note:** the interface is available in **French and English** (see [Languages](#languages)). The detailed guide is in French: [docs/fr/guide.md](docs/fr/guide.md).

## Features

**Characters and factions**
- Relationship graph: free placement, zoom, PNG export, automatic layout that keeps the most readable of several arrangements.
- NPC sheets (portrait, status, hook, personality, motivations, GM secrets, gallery…) and faction sheets (colour, emblem, goals, resources, members).
- Memberships with a role, for NPCs and for factions (a faction can be a sub-faction of one or more others, no loops); typed relations (ally, enemy, family, rival, mentor, debt…), one-way or mutual, optionally secret.
- Graph views for a scenario's cast, for the factions, or for a place; a Characters tab lists everyone.

**Places and interactive maps**
- Free hierarchy of places (region > city > building > room…), with the NPCs and factions present and their role.
- Interactive maps: upload a map (up to 8192 px), pin places, open rings of sub-places, "enter" a place's own map. Custom icons per place type.

**Scenarios**
- A campaign page, scenarios and nested sub-pages (acts, scenes, handouts), each with a status (draft, ready, in progress, played).
- Rich text editor: tables, images, "read-aloud" and "GM secret" boxes, `@` mentions of NPCs, factions and places, templates, reading mode, clean printing.
- **Real-time collaboration**: several GMs edit the same page and see each other's cursors. Offline edits are merged on reconnection; what cannot be merged is kept in the page history.
- Comment threads anchored to passages, and a version history with named versions and one-click restore.

**Everything else**
- Global full-text search (Ctrl+K), image library, campaign export and import (`.zip`), phone layout.
- Two roles: administrator and GM (assigned campaigns only); scrypt passwords, login throttling.
- Backups, restore and one-click updates from the web interface.
- Integrations: a connector for Claude (MCP) and a Foundry VTT import module.

## Project status

Maze is used in production by its author and has been through a thorough audit, but it is young as a community project:

- **Interface in French and English.** Every screen, server message, the data created with a campaign and the Foundry VTT module are available in both languages. The documentation is in French, except this README, CONTRIBUTING.md and the translation guide. Other languages are welcome: see [Translations](#translations).
- **One maintainer.** Reviews and answers may take a while.
- **Free and open source.** Maze is released under the MIT License (see [License](#license)).
- **Built with heavy AI assistance.** Maze is developed with substantial help from Claude (Anthropic), under the direction and review of its author.
- **System-agnostic core.** Nothing in Maze depends on a particular game system. The Foundry VTT module imports journals, scenes and map notes into any world; it currently fills in actors only for the Dark Heresy systems (`dark-heresy-v1` and `dark-heresy`).

## Languages

Maze speaks French and English. The language is chosen in this order:

1. the account setting (account menu > Language: Automatic, Français, English), kept on every device;
2. the choice made on the login screen, kept in that browser;
3. the browser languages (the first one Maze knows);
4. `MAZE_DEFAULT_LOCALE` (`fr` by default, see [Configuration](#configuration)).

Server messages follow the same language. Relationship types, place types and default titles are created in the language of the person who creates (or imports) the campaign; after that they are campaign data, never translated. `python -m maze.demo --lang en` creates an English demo campaign. The Foundry VTT module shows its window in the language of each GM's Foundry; the pages, folders and names it creates in the world use one language per world, the same for every GM (module setting "Language of imported pages": Automatic keeps the language of the first import, French for a world imported before this setting). What stays in French: the tools of the Claude connector (read by Claude, who answers in the language of your conversation), the detailed guide and architecture notes, server logs, and internal values such as the backup origins of `MAZE_BACKUP_KEEP`.

## Quick start (Docker Compose)

Requirements: a Linux server with Docker and the Compose plugin, at least 10 GB of disk (the update script rebuilds the image next to the running one), a domain name, and a reverse proxy that terminates HTTPS. The examples use `/opt/maze`, `maze.example.org` and Caddy.

**1. Get the code**

    sudo mkdir -p /opt/maze && sudo chown "$USER": /opt/maze
    git clone https://github.com/ranglier/maze.git /opt/maze
    cd /opt/maze
    cp .env.example .env
    install -d -m 2775 control

`control/` receives the container's requests to the update service. The container runs as UID/GID 1000: if `id -u` is not 1000, also run `sudo chgrp 1000 control`.

**2. Administrator password (Docker secret)**

Masked input, run on its own (the length is shown to confirm):

    read -rs -p "Maze admin password: " MAZE_PW && echo " - entered (${#MAZE_PW} characters)"

Then:

    mkdir -p secrets
    printf '%s' "$MAZE_PW" > secrets/maze_password.txt
    unset MAZE_PW
    chmod 600 secrets/maze_password.txt
    sudo chown 1000:1000 secrets/maze_password.txt

It is used once, to create the `admin` account; `secrets/` is ignored by git.

**3. Edit `.env`**

    MAZE_BIND=127.0.0.1
    MAZE_PUBLIC_URL=https://maze.example.org
    FORWARDED_ALLOW_IPS=

`MAZE_BIND` is `127.0.0.1` if the reverse proxy runs on the same machine, otherwise this machine's private IP; never `0.0.0.0` (Docker's published ports bypass firewalls such as ufw). For `FORWARDED_ALLOW_IPS`, see [Configuration](#configuration).

**4. Start**

    docker compose up -d --build
    docker compose logs -f maze

Optionally, load a demo campaign: `docker compose exec maze python -m maze.demo` (add `--lang en` for an English one; default: `MAZE_DEFAULT_LOCALE`).

**5. Reverse proxy**

Point a DNS record for `maze.example.org` to your server, then add a site to your Caddyfile:

    maze.example.org {
    	encode zstd gzip

    	# Archive imports (backups, campaigns) up to MAZE_MAX_IMPORT_MB; other requests up to
    	# the largest image upload Maze accepts. Maze enforces its own, finer limits.
    	@import path /api/admin/backups/import /api/campaigns/import
    	request_body @import {
    		max_size 4GiB
    	}
    	@other not path /api/admin/backups/import /api/campaigns/import
    	request_body @other {
    		max_size 410MiB
    	}

    	reverse_proxy 127.0.0.1:8000
    }

Use the private IP of the Maze machine instead of `127.0.0.1` if Caddy runs elsewhere. `deploy/Caddyfile.maze` is a fuller example (explicit HTTP redirect, HSTS). Then:

    sudo caddy validate --config /etc/caddy/Caddyfile
    sudo systemctl reload caddy

**6. First login**

Open https://maze.example.org and sign in as `admin` with the password from step 2 (the session cookie is HTTPS-only, so `http://127.0.0.1:8000` will not let you in). Create GM accounts in the account menu > Administration.

Next: schedule the daily backup and install the update service (below). A step-by-step installation on a fresh Debian server, in French: `deploy/installation-debian.md`.

## Configuration

With Docker, `.env` feeds `MAZE_BIND`, `FORWARDED_ALLOW_IPS`, `MAZE_PUBLIC_URL`, `MAZE_SOURCE_URL`, `MAZE_DEFAULT_LOCALE` and `MAZE_BACKUP_KEEP` to `docker-compose.yml`; set other variables under `environment:` in a `docker-compose.override.yml`, then `docker compose up -d`.

| Variable | Default | Purpose |
|---|---|---|
| MAZE_PASSWORD_FILE / MAZE_PASSWORD | (empty) | Password of the `admin` account created on first start (ignored afterwards). Without it, the interface offers to create the first administrator |
| MAZE_CONTROL_DIR | (empty; /control in the compose file) | Folder where the container drops its requests to the maze-updater service |
| MAZE_STATUS_DIR | (empty; /status in the compose file, read-only) | State published by the maze-updater service (falls back to MAZE_CONTROL_DIR, the former location) |
| FORWARDED_ALLOW_IPS | private and local networks (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, ::1, fc00::/7) | Reverse proxy addresses or subnets (comma-separated) whose X-Forwarded-For and X-Forwarded-Proto headers are trusted (client address for login throttling, HTTPS for the Claude connector); read by uvicorn. Recommended in `.env`: the exact proxy IP, otherwise a local device reaching Maze's port directly could choose its apparent address. Proxy on the same machine: the Docker network gateway (`docker network inspect maze_default`, field Gateway), not 127.0.0.1. Never `*` |
| MAZE_VERSION | dev | Displayed version (set by `deploy/update.sh` at build time) |
| MAZE_SECRET_KEY(_FILE) | generated in `<data>/.secret_key` | Key signing the session cookies |
| MAZE_COOKIE_SECURE | false (true in the compose file) | Session cookie restricted to HTTPS |
| MAZE_SESSION_DAYS | 30 | Session lifetime, in days |
| MAZE_MAX_UPLOAD_MB | 20 | Maximum size of an uploaded image |
| MAZE_MAX_IMPORT_MB | 4096 | Maximum size of an imported archive (backup or campaign) |
| MAZE_BACKUP_KEEP | (empty: quotidienne=14, manuelle=3, maj=3, avant-restauration=3, import=2) | Archives kept per origin, e.g. `quotidienne=30,maj=5` (0 = keep all; see [Backups and restore](#backups-and-restore)) |
| MAZE_DATA_DIR | /data in the image (`./data` otherwise) | Database, media and backups |
| MAZE_STATIC_DIR | /app/static in the image (`backend/maze/static` otherwise) | Built interface served by Maze (`frontend/dist` in development) |
| MAZE_DB_WRITE_THREADS / MAZE_DB_READ_THREADS | 5 / 11 | Threads reserved for database writes and reads (writes waiting for a lock never delay reads) |
| MAZE_PUBLIC_URL | (empty: derived from requests) | Public address, e.g. https://maze.example.org: OAuth addresses and MCP server URL of the Claude connector, Foundry module manifest |
| MAZE_DEFAULT_LOCALE | fr | Language of the interface and of server messages when neither the account (account menu > Language), the login screen choice nor the browser asks for a language Maze knows: `fr` or `en` (see [Languages](#languages)). Also the language of data created without a request (`python -m maze.demo` without `--lang`) and of Foundry pages when the module asks for a language the server does not know |
| MAZE_SOURCE_URL | https://github.com/ranglier/Maze | Where users of this instance can get its source code: "Code source" link on the login screen and in the account menu. A fork can point it to its own source code |
| MAZE_MAP_FONT | (empty) | TrueType font for map previews sent to Claude (`view_map`), tried before DejaVu Sans (in the image) and Pillow's font (no accents). Only useful outside the image |
| MAZE_LIVE_MAX_PER_USER / MAZE_LIVE_MAX_TOTAL | 12 / 100 | Simultaneous real-time editing connections, per account and for the whole instance; beyond that, the page shows why and the browser retries later (close code 4429) |

The host update service (`deploy/maze-updater.sh`, run by systemd, not by the container nor from `.env`) reads `MAZE_BRANCH` (default `main`): the branch it checks and installs. To change it: `sudo systemctl edit maze-updater.service`, then `Environment=MAZE_BRANCH=<branch>` under `[Service]`. `./deploy/update.sh` run by hand takes the branch as its argument (`main` by default) and ignores `MAZE_BRANCH`.

One server per data folder: a second process on the same folder (`uvicorn --workers 2`, a second container on the volume) refuses to start. Request body limits per route: guide, section "Limites des requêtes".

## Backups and restore

A backup is a hot, consistent copy of the database plus all images, written to `/data/backups` in the volume:

    docker compose exec maze python -m maze.backup

Each origin has its own rotation, based on the archive name (`maze-YYYYMMDD-HHMMSS[-origin].tar.gz`):

| Archive | Origin | Key in `MAZE_BACKUP_KEEP` | Kept by default |
|---|---|---|---|
| `maze-….tar.gz` | command line, daily timer | `quotidienne` | 14 (or `--keep N`) |
| `maze-…-manuelle.tar.gz` | "back up now" and "export" buttons of the administration | `manuelle` | 3 |
| `maze-…-maj.tar.gz` | before each update | `maj` | 3 |
| `maze-…-avant-restauration.tar.gz` | state before each restore | `avant-restauration` | 3 |
| `maze-…-import.tar.gz` | archives imported in the administration | `import` | 2 |

A backup or restore is refused when the volume lacks free space (twice the latest archive plus 200 MB), and nothing is written.

**Daily backup.** A systemd timer is provided in `deploy/systemd/` (03:15 every night, catches up after downtime, output in the journal). The units assume `/opt/maze` and a deploy user named `ops`: adjust `User=`, `Group=` and `WorkingDirectory=` otherwise.

    sudo cp deploy/systemd/maze-backup.service deploy/systemd/maze-backup.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now maze-backup.timer
    sudo systemctl start maze-backup.service
    journalctl -u maze-backup -n 20 --no-pager

Administration > Sauvegardes shows the latest daily backup and warns when it is more than 48 hours old.

**Restore** from Administration > Sauvegardes (import the archive first if it comes from elsewhere), or on the command line:

    docker compose cp ./maze-YYYYMMDD-HHMMSS.tar.gz maze:/data/backups/
    docker compose exec maze python -m maze.backup --restore /data/backups/maze-YYYYMMDD-HHMMSS.tar.gz
    docker compose restart maze

Maze can keep running: writes are refused until the restore ends, and the current state is first saved as `-avant-restauration`. Accounts present on both sides keep today's password, role and active state (restoring after a compromise does not let an intruder back in), and sessions and tokens are closed. Disaster recovery on a fresh instance: add `--keep-archive-accounts`.

**Off-site copy and restore test.** Archives sit on the same disk as the database, and Maze copies nothing elsewhere: copy `/data/backups` off the machine regularly (the guide has an rclone setup with a second timer), and once a month restore the latest copy into a throwaway container to check it (script in the guide).

Details (account rules, `--exact`, interrupted restores, cron instead of systemd): [docs/fr/guide.md](docs/fr/guide.md), section "Sauvegarde et restauration".

## Updates

    cd /opt/maze && ./deploy/update.sh

The script fetches the code (`main`, or the branch given as argument), tags the running image `maze:previous`, builds the new one, takes a `-maj` backup, restarts and checks that Maze answers. `FORCE=1` rebuilds an up-to-date version; less than 1.5 GB free (`MIN_FREE_MB`) stops it.

**Automatic rollback.** A failure before the restart leaves the old version running. If the new one is not healthy within `HEALTH_TIMEOUT` (180 s, migrations not counted), the script returns to `maze:previous` and the previous commit, restoring the `-maj` backup first if the database was migrated; if that is impossible, Maze is left stopped and the script prints what to do.

| Exit code | Meaning |
|---|---|
| 0 | Updated, or already up to date |
| 1 | Failed before the restart: the previous version is still running |
| 3 | New version unhealthy: rolled back to the previous one |
| 4, 5 | Manual intervention needed (5: Maze left stopped, migrated database not restored) |

**Update button.** The container has no access to Docker: the button in Administration > Mise à jour drops a request in `control/`, and the `maze-updater` systemd service on the host runs `deploy/update.sh` as `ops` and publishes state and log in `status/`. It also checks for new commits nightly. Install it once:

    sudo cp deploy/systemd/maze-updater.service deploy/systemd/maze-updater.path deploy/systemd/maze-updater.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now maze-updater.path maze-updater.timer
    sudo systemctl start maze-updater.service

`ops` must be in the `docker` group, which amounts to root: use a dedicated administration account. Details: [docs/fr/guide.md](docs/fr/guide.md), sections "Mises à jour" and "Mise à jour depuis l'interface".

## Integrations

**Claude connector (MCP).** Maze includes a remote MCP server (`/mcp`) with its own OAuth 2.1 authorisation: Claude can search and read your campaigns and, if you allow it, edit NPCs, factions, places, relations and scenario pages, comment, add images and pin places on maps. In Administration > Connecteur Claude, generate the client ID and secret, enable access and open campaigns read-only or read-write; then add a custom connector on claude.ai with `https://maze.example.org/mcp` and those credentials. Each authorisation needs an administrator's password, actions are logged, and pages keep their version from before Claude's change.

**Foundry VTT module.** Your instance serves the `maze-sync` module (Foundry 13 and 14): install it with the manifest URL `https://maze.example.org/foundry/module.json` and give it a read-only token from Administration > Foundry VTT. It imports a campaign, or what one scenario needs: a journal per NPC, faction and place (secrets on a GM-only page), a scene per interactive map with a note per pin, and Dark Heresy actors. Import is one-way and repeatable; pages edited in Foundry are kept unless you choose to replace them.

## Development

| Layer | Choice |
|---|---|
| Server | Python 3.13, Starlette, Pydantic, Uvicorn |
| Database | SQLite (WAL mode), numbered SQL migrations |
| Images | Pillow (WebP, thumbnails; JPEG for large maps) |
| Interface | React 19, built with esbuild |
| Graph | Cytoscape.js + fcose, with a custom decluttering pass |
| Editor | ProseMirror + Yjs (y-prosemirror): CRDT merging in the browser, relayed by the server over WebSocket |

**Run locally without Docker** (Python 3.13, Node 22):

    cd frontend && npm ci && npm run watch           # terminal 1
    cd backend && pip install -r requirements.txt     # terminal 2
    MAZE_DATA_DIR=../data MAZE_STATIC_DIR=../frontend/dist uvicorn maze.app:app_factory --factory --reload --port 8000

Then open http://127.0.0.1:8000: on an empty database, the interface asks you to create the first administrator.

**Tests**

    cd backend && python -m unittest discover -s tests     # server: every tests/test_*.py
    cd backend && ruff check --isolated --select F,E9,B --line-length 120 maze tests
    cd frontend && npm test                                 # interface logic (node --test)

Browser journeys use Playwright, installed globally rather than as a project dependency. Each script in `frontend/e2e/` runs against a fresh instance started by `run-all.sh`:

    npm install -g playwright@1.56.0 && playwright install chromium
    cd frontend && npm ci && npm run build && cd ..
    NODE_PATH="$(npm root -g)" frontend/e2e/run-all.sh     # or: run-all.sh scenarios

GitHub Actions (`.github/workflows/ci.yml`) runs all of this on every push and pull request, plus the migration numbering check and the Foundry module version check.

**Project layout**

    backend/maze/             server: api.py (REST), db.py, collab.py (real time), mcp*.py, foundry*.py…
    backend/maze/migrations/  numbered SQL migrations
    backend/maze/foundry_module/  Foundry VTT module served by Maze
    backend/tests/            server tests (unittest)
    frontend/src/             interface: graph/, editor/, components/ (scenarios/, maps/)…
    frontend/test/            interface logic tests (node --test)
    frontend/e2e/             browser journeys (Playwright)
    deploy/                   update.sh, maze-updater.sh, systemd units, Caddy example
    docs/                     ARCHITECTURE.md, fr/guide.md

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (French) is the reference for how things work and why.

## Contributing

Contributions are welcome. Issues and pull requests can be written in English or French. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request: it covers the Developer Certificate of Origin (`git commit -s`), the workflow (test first, small focused pull requests, green CI) and the project rules (migrations, entry fields, editor schema version, Foundry module version, deployment scripts).

### Translations

Every visible string of the interface goes through `t()`, `tn()` or `pt()`, with the French text as the key, and server messages through `msg()` / `msgn()`; the English catalogs must stay complete, which the tests check. To fix a translation, translate a new string or propose a new language (interface, server messages and Foundry module), read [frontend/src/i18n/README.md](frontend/src/i18n/README.md).

## License

Maze is released under the [MIT License](LICENSE) (SPDX identifier: `MIT`). You may use, copy, modify, host and redistribute it freely, as long as the copyright notice and the license text are kept.

Each instance shows a "Code source" link on the login screen and in the account menu, pointing to `MAZE_SOURCE_URL` (this repository by default). If you run a fork, you can point it to your own repository.
