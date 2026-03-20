from __future__ import annotations

import logging

import httpx

from server.app.core.config import ServerSettings
from shared.schemas import ConfiguredRepo

logger = logging.getLogger(__name__)


class GitHubService:
    def __init__(self, settings: ServerSettings) -> None:
        self.settings = settings
        self.repos = {repo.id: repo for repo in settings.load_repos()}

    def list_repos(self) -> list[ConfiguredRepo]:
        return list(self.repos.values())

    def get_repo(self, repo_id: str) -> ConfiguredRepo:
        try:
            return self.repos[repo_id]
        except KeyError as exc:
            raise KeyError(f"unknown repo_id {repo_id}") from exc

    def browse_commits(self, repo_id: str, query: str | None = None, limit: int = 20) -> list[dict]:
        repo = self.get_repo(repo_id)
        headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        owner_repo = repo.full_name
        params = {"per_page": str(limit)}
        if query:
            params["sha"] = query
        url = f"{repo.api_url}/repos/{owner_repo}/commits"
        with httpx.Client(timeout=15.0) as client:
            response = client.get(url, headers=headers, params=params)
            response.raise_for_status()
            commits = response.json()
        output = []
        for item in commits:
            commit = item.get("commit", {})
            author = commit.get("author", {})
            output.append(
                {
                    "sha": item.get("sha"),
                    "short_sha": (item.get("sha") or "")[:12],
                    "message": (commit.get("message") or "").splitlines()[0],
                    "author_name": author.get("name"),
                    "author_date": author.get("date"),
                    "html_url": item.get("html_url"),
                }
            )
        logger.info("fetched %s commits for %s", len(output), repo_id)
        return output

