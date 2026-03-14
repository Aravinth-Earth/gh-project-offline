# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

APP_DIR_NAME = ".ghpo"
APP_DIR_PATH = Path(APP_DIR_NAME)
DEFAULT_CONFIG_PATH = APP_DIR_PATH / "config.toml"
DEFAULT_PROJECT_URL = "https://github.com/users/YOUR-OWNER/projects/123/views/1"
PROJECT_URL_RE = re.compile(
    r"^https://github\.com/(?P<scope>users|orgs)/(?P<owner>[^/]+)/projects/(?P<project_number>\d+)"
    r"(?:/views/(?P<view_number>\d+))?/?$"
)


@dataclass(slots=True)
class ParsedProjectUrl:
    owner: str
    owner_type: str
    project_number: int
    view_number: int | None


@dataclass(slots=True)
class GitHubConfig:
    owner: str
    owner_type: str
    project_number: int
    view_number: int
    token_env: str
    project_url: str | None = None

    @property
    def project_web_url(self) -> str:
        prefix = "users" if self.owner_type == "user" else "orgs"
        return f"https://github.com/{prefix}/{self.owner}/projects/{self.project_number}/views/{self.view_number}"


@dataclass(slots=True)
class StorageConfig:
    database_path: Path
    logs_dir: Path


@dataclass(slots=True)
class SyncConfig:
    interval: str
    timeout_seconds: int
    user_agent: str
    include_closed_items: bool


@dataclass(slots=True)
class AppConfig:
    config_path: Path
    github: GitHubConfig
    storage: StorageConfig
    sync: SyncConfig


def render_default_config(*, project_url: str | None = None, token_env: str = "GITHUB_TOKEN") -> str:
    chosen_project_url = project_url or DEFAULT_PROJECT_URL
    return f"""[github]
project_url = "{chosen_project_url}"
token_env = "{token_env}"

[storage]
database_path = "data/cache.db"
logs_dir = "logs"

[sync]
interval = "15m"
timeout_seconds = 30
user_agent = "gh-project-offline/0.1.1"
include_closed_items = false
"""


def write_default_config(path: Path, *, project_url: str | None = None, token_env: str = "GITHUB_TOKEN") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_default_config(project_url=project_url, token_env=token_env), encoding="utf-8")


def parse_project_url(url: str) -> ParsedProjectUrl:
    cleaned = url.strip()
    match = PROJECT_URL_RE.fullmatch(cleaned)
    if not match:
        raise ValueError(
            "Project URL must look like "
            "'https://github.com/users/<owner>/projects/<number>/views/<view>' or "
            "'https://github.com/orgs/<org>/projects/<number>/views/<view>'."
        )
    return ParsedProjectUrl(
        owner=match.group("owner"),
        owner_type="user" if match.group("scope") == "users" else "org",
        project_number=int(match.group("project_number")),
        view_number=int(match.group("view_number")) if match.group("view_number") else None,
    )


def load_config(path: Path) -> AppConfig:
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    github = document["github"]
    storage = document["storage"]
    sync = document["sync"]
    base_dir = path.parent
    parsed_url = parse_project_url(github["project_url"]) if github.get("project_url") else None

    owner = github.get("owner") or (parsed_url.owner if parsed_url else None)
    owner_type = github.get("owner_type") or (parsed_url.owner_type if parsed_url else None)
    project_number = github.get("project_number")
    if project_number is None and parsed_url is not None:
        project_number = parsed_url.project_number
    view_number = github.get("view_number")
    if view_number is None and parsed_url is not None:
        view_number = parsed_url.view_number

    if not owner or not owner_type or project_number is None or view_number is None:
        raise ValueError(
            "Config must define owner, owner_type, project_number, and view_number, "
            "either explicitly or through github.project_url."
        )

    return AppConfig(
        config_path=path,
        github=GitHubConfig(
            owner=owner,
            owner_type=owner_type,
            project_number=int(project_number),
            view_number=int(view_number),
            token_env=github.get("token_env", "GITHUB_TOKEN"),
            project_url=github.get("project_url"),
        ),
        storage=StorageConfig(
            database_path=(base_dir / storage["database_path"]).resolve(),
            logs_dir=(base_dir / storage.get("logs_dir", "logs")).resolve(),
        ),
        sync=SyncConfig(
            interval=sync["interval"],
            timeout_seconds=int(sync["timeout_seconds"]),
            user_agent=sync["user_agent"],
            include_closed_items=bool(sync.get("include_closed_items", False)),
        ),
    )


def log_timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
