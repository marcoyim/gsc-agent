"""Locations for local credentials, tokens, and the SQLite database."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import platformdirs

APP_NAME = "gsc-agent"
READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


def app_data_dir() -> Path:
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))


def default_database_path() -> Path:
    return app_data_dir() / "gsc.sqlite"


def default_token_path() -> Path:
    return app_data_dir() / "token.json"


def default_client_secrets_path() -> Path:
    return app_data_dir() / "client_secret.json"


@dataclass(frozen=True)
class RuntimePaths:
    database: Path
    token: Path
    client_secrets: Path


def restrict_file_permissions(path: Path) -> None:
    if os.name == "nt":
        return
    os.chmod(path, 0o600)


def is_private_file(path: Path) -> bool:
    if os.name == "nt" or not path.exists():
        return True
    return path.stat().st_mode & 0o077 == 0
