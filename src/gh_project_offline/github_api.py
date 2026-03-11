# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib import error, parse, request

from .config import AppConfig


class GitHubApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: str | None = None,
        url: str | None = None,
        response_headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.url = url
        self.response_headers = response_headers or {}


class GitHubClient:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        token = os.getenv(config.github.token_env)
        if not token:
            raise GitHubApiError(
                f"Missing token environment variable '{config.github.token_env}'."
            )
        self._token = token

    def fetch_project(self) -> dict[str, Any]:
        payload, _ = self._request_json(self._owner_path())
        return payload

    def fetch_rate_limit_status(self) -> dict[str, Any]:
        payload, _ = self._request_json("/rate_limit")
        return payload

    def fetch_fields(self) -> list[dict[str, Any]]:
        return self._fetch_cursor_paginated(
            f"{self._owner_path()}/fields",
            result_key="fields",
        )

    def fetch_views(self) -> list[dict[str, Any]]:
        return self._fetch_cursor_paginated(
            f"{self._owner_path()}/views",
            result_key="views",
        )

    def fetch_view_items(self, field_ids: list[str]) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"per_page": 100}
        if field_ids:
            query["fields[]"] = field_ids
        return self._fetch_cursor_paginated(
            f"{self._owner_path()}/views/{self._config.github.view_number}/items",
            result_key="items",
            base_query=query,
        )

    def fetch_issue(self, repository_name: str, issue_number: int) -> dict[str, Any]:
        owner, repo = split_repository_name(repository_name)
        payload, _ = self._request_json(f"/repos/{owner}/{repo}/issues/{issue_number}")
        return payload

    def fetch_issue_comments(self, repository_name: str, issue_number: int) -> list[dict[str, Any]]:
        owner, repo = split_repository_name(repository_name)
        path = f"/repos/{owner}/{repo}/issues/{issue_number}/comments"
        comments: list[dict[str, Any]] = []
        page = 1
        while True:
            payload, _ = self._request_json(path, query={"per_page": 100, "page": page})
            page_comments = list(payload if isinstance(payload, list) else payload.get("comments", []))
            if not page_comments:
                break
            comments.extend(page_comments)
            if len(page_comments) < 100:
                break
            page += 1
        return comments

    def _owner_path(self) -> str:
        github = self._config.github
        prefix = "users" if github.owner_type == "user" else "orgs"
        return f"/{prefix}/{github.owner}/projectsV2/{github.project_number}"

    def _fetch_cursor_paginated(
        self,
        path: str,
        *,
        result_key: str,
        base_query: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            query = dict(base_query or {})
            if after:
                query["after"] = after
            payload, headers = self._request_json(path, query=query)
            page_items = list(payload if isinstance(payload, list) else payload.get(result_key, []))
            if not page_items:
                break
            items.extend(page_items)
            after = extract_next_cursor(headers.get("Link"))
            if not after:
                break
        return items

    def _request_json(
        self,
        path: str,
        *,
        query: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | list[dict[str, Any]], dict[str, str]]:
        url = build_url(path, query)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": self._config.sync.user_agent,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        req = request.Request(url, headers=headers)
        try:
            with request.urlopen(req, timeout=self._config.sync.timeout_seconds) as response:  # nosec B310
                return json.loads(response.read().decode("utf-8")), dict(response.headers.items())
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GitHubApiError(
                f"GitHub API request failed for {url}: {exc.code} {body}",
                status_code=exc.code,
                response_body=body,
                url=url,
                response_headers=dict(exc.headers.items()),
            ) from exc
        except error.URLError as exc:
            raise GitHubApiError(f"GitHub API request failed for {url}: {exc.reason}") from exc


def build_url(path: str, query: dict[str, Any] | None = None) -> str:
    if not query:
        return f"https://api.github.com{path}"
    return f"https://api.github.com{path}?{parse.urlencode(query, doseq=True)}"


def extract_next_cursor(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' not in part:
            continue
        match = re.search(r"[?&]after=([^&>]+)", part)
        if match:
            return parse.unquote(match.group(1))
    return None


def split_repository_name(repository_name: str) -> tuple[str, str]:
    owner, separator, repo = repository_name.partition("/")
    if not separator or not owner or not repo:
        raise ValueError(f"Repository name must look like 'owner/repo', got {repository_name!r}.")
    return owner, repo
