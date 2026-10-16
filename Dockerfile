# syntax=docker/dockerfile:1

# Images de base : à épingler par empreinte (« node:22-alpine@sha256:… », audit M21), valeurs données par le
# workflow .github/workflows/lockfile.yml (artefact verrou-et-images, images.txt) ; voir docs/fr/guide.md, « Verrou des
# dépendances de l'interface ».

# --- Étape 1 : construction de l'interface -----------------------------------
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS frontend
WORKDIR /build
COPY frontend/package*.json ./
# Dépendances figées par frontend/package-lock.json : npm ci (audit M21). Tant que le verrou n'est pas dans le
# dépôt, npm install, avec un avertissement dans la sortie de la construction.
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; \
    else echo "AVERTISSEMENT : frontend/package-lock.json absent, npm install (versions non figées, voir docs/fr/guide.md)" >&2; \
         npm install --no-audit --no-fund; fi
COPY frontend/ ./
RUN npm run build

# --- Étape 2 : image d'exécution ---------------------------------------------
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MAZE_DATA_DIR=/data \
    MAZE_STATIC_DIR=/app/static

WORKDIR /app
# Police avec accents pour les aperçus de cartes renvoyés à Claude (celle de Pillow n'a pas é, è, à…).
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/maze ./maze
# Texte de la licence (MIT), joint aussi à l'archive du module Foundry (foundry.LICENSE_FILES).
COPY LICENSE ./LICENSE
COPY --from=frontend /build/dist ./static

RUN groupadd --system --gid 1000 maze \
    && useradd --system --uid 1000 --gid 1000 --no-create-home --home-dir /app maze \
    && mkdir -p /data \
    && chown maze:maze /data
USER maze

VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# En dernier : la version change à chaque commit sans invalider le cache des couches précédentes.
ARG MAZE_VERSION=dev
ENV MAZE_VERSION=${MAZE_VERSION}

# Proxys de confiance : variable FORWARDED_ALLOW_IPS (lue par uvicorn, réglable dans .env via le compose), jamais
# « * » (X-Forwarded-For serait choisi par le client, audit M13). Par défaut, les réseaux privés et locaux (même
# valeur que dans docker-compose.yml) : le reverse proxy y est, et un client d'Internet n'y est jamais.
ENV FORWARDED_ALLOW_IPS=127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1,fc00::/7
# Messages WebSocket de 16 Mio au plus (état complet d'une fiche : collab.MAX_SNAPSHOT, 15 Mio).
CMD ["uvicorn", "maze.app:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--ws-max-size", "16777216"]
