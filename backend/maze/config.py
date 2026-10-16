"""Configuration lue depuis l'environnement (compatible secrets Docker via *_FILE)."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("maze")

# Licence de Maze (identifiant SPDX) et adresse de son code source par défaut. L'interface affiche le lien (écran de
# connexion, menu du compte), via /api/auth/status ; un fork peut régler MAZE_SOURCE_URL sur ses propres sources.
LICENSE = "MIT"
DEFAULT_SOURCE_URL = "https://github.com/ranglier/Maze"


def _env_secret(name: str) -> str | None:
    """Lit NAME, ou le contenu du fichier désigné par NAME_FILE (secrets Docker)."""
    file_path = os.environ.get(f"{name}_FILE")
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip() or None
        except OSError as exc:
            raise RuntimeError(f"Impossible de lire {name}_FILE={file_path}: {exc}") from exc
    value = os.environ.get(name)
    return value or None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "oui"}


def _env_int(name: str, default: int) -> int:
    """Entier strictement positif lu dans NAME ; valeur absente ou invalide : défaut (avec un avertissement)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        if value < 1:
            raise ValueError
    except ValueError:
        log.warning("%s=%r invalide (entier au moins égal à 1) : %s par défaut", name, raw, default)
        return default
    return value


def _env_source_url() -> str:
    """MAZE_SOURCE_URL : adresse http(s) du code source ; absente ou invalide : dépôt de Maze (avec un avertissement)."""
    raw = os.environ.get("MAZE_SOURCE_URL", "").strip()
    if not raw:
        return DEFAULT_SOURCE_URL
    if not raw.lower().startswith(("https://", "http://")) or any(c.isspace() for c in raw):
        log.warning("MAZE_SOURCE_URL=%r invalide (adresse http ou https attendue) : %s", raw, DEFAULT_SOURCE_URL)
        return DEFAULT_SOURCE_URL
    return raw


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    static_dir: Path
    password: str | None
    secret_key: str
    cookie_secure: bool
    max_upload_mb: int
    session_days: int
    version: str
    control_dir: Path | None
    # État publié par le service maze-updater (lecture seule dans le conteneur, audit H2) ; à défaut,
    # ancien emplacement : le dossier de contrôle.
    status_dir: Path | None = None
    # Adresse publique (https://maze.example.org) : adresses OAuth du connecteur Claude.
    # À défaut, déduite de chaque requête (en-têtes du reverse proxy).
    public_url: str | None = None
    # Connexions temps réel admises (audit PERF-9) : par compte (toutes fiches) et pour toute l'instance.
    live_max_per_user: int = 12
    live_max_total: int = 100
    # Code source de cette version, proposé à tous les utilisateurs.
    source_url: str = DEFAULT_SOURCE_URL

    @property
    def db_path(self) -> Path:
        return self.data_dir / "maze.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"


def _persistent_secret_key(data_dir: Path) -> str:
    """Génère une clé de session une fois et la conserve dans le volume de données."""
    key_file = data_dir / ".secret_key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(48)
    key_file.write_text(key, encoding="utf-8")
    key_file.chmod(0o600)
    return key


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("MAZE_DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "media").mkdir(exist_ok=True)

    default_static = Path(__file__).resolve().parent / "static"
    static_dir = Path(os.environ.get("MAZE_STATIC_DIR", default_static)).resolve()

    # Sert uniquement à créer le compte « admin » au premier démarrage (aucun compte en base).
    password = _env_secret("MAZE_PASSWORD")

    return Settings(
        data_dir=data_dir,
        static_dir=static_dir,
        password=password,
        secret_key=_env_secret("MAZE_SECRET_KEY") or _persistent_secret_key(data_dir),
        cookie_secure=_env_bool("MAZE_COOKIE_SECURE", False),
        max_upload_mb=int(os.environ.get("MAZE_MAX_UPLOAD_MB", "20")),
        session_days=int(os.environ.get("MAZE_SESSION_DAYS", "30")),
        version=os.environ.get("MAZE_VERSION", "dev"),
        control_dir=Path(os.environ["MAZE_CONTROL_DIR"]) if os.environ.get("MAZE_CONTROL_DIR") else None,
        status_dir=Path(os.environ["MAZE_STATUS_DIR"]) if os.environ.get("MAZE_STATUS_DIR") else None,
        public_url=(os.environ.get("MAZE_PUBLIC_URL") or "").strip().rstrip("/") or None,
        live_max_per_user=_env_int("MAZE_LIVE_MAX_PER_USER", 12),
        live_max_total=_env_int("MAZE_LIVE_MAX_TOTAL", 100),
        source_url=_env_source_url(),
    )
