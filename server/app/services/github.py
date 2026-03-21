from __future__ import annotations

import base64
import logging
import re
import subprocess
from pathlib import Path

import httpx

from server.app.core.config import ServerSettings
from shared.schemas import ConfiguredRepo

logger = logging.getLogger(__name__)
SHA_PREFIX_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
LOCAL_COMMIT_SCAN_LIMIT = 200


class GitHubService:
    def __init__(self, settings: ServerSettings) -> None:
        self.settings = settings
        self.repos = {repo.id: repo for repo in settings.load_repos()}

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        return headers

    def list_repos(self) -> list[ConfiguredRepo]:
        return list(self.repos.values())

    def get_repo(self, repo_id: str) -> ConfiguredRepo:
        try:
            return self.repos[repo_id]
        except KeyError as exc:
            raise KeyError(f"unknown repo_id {repo_id}") from exc

    def browse_commits(self, repo_id: str, query: str | None = None, limit: int = 20) -> list[dict]:
        repo = self.get_repo(repo_id)
        local_checkout = self._local_checkout(repo)
        if local_checkout is not None:
            try:
                commits = self._browse_local_commits(repo, local_checkout, query=query, limit=limit)
                logger.info("fetched %s commits for %s from local checkout", len(commits), repo_id)
                return commits
            except (FileNotFoundError, ValueError) as exc:
                logger.warning(
                    "local commit browse failed for %s using %s: %s",
                    repo_id,
                    local_checkout,
                    exc,
                )

        commits = self._browse_remote_commits(repo, query=query, limit=limit)
        logger.info("fetched %s commits for %s from GitHub API", len(commits), repo_id)
        return commits

    def fetch_file_at_ref(self, repo_id: str, path: str, ref: str) -> str:
        repo = self.get_repo(repo_id)
        local_checkout = self._local_checkout(repo)
        if local_checkout is not None:
            try:
                return self._fetch_file_from_local_checkout(repo, local_checkout, path, ref)
            except FileNotFoundError as exc:
                logger.warning(
                    "local file lookup failed for %s@%s using %s: %s",
                    repo_id,
                    ref,
                    local_checkout,
                    exc,
                )

        owner_repo = repo.full_name
        url = f"{repo.api_url}/repos/{owner_repo}/contents/{path.lstrip('/')}"
        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                url,
                headers=self._headers(),
                params={"ref": ref},
            )
            if response.status_code == 404:
                if self._fetch_remote_commit(repo, ref) is None:
                    raise FileNotFoundError(f"commit {ref} not found or is not accessible for {repo_id}")
                raise FileNotFoundError(f"{path} not found for {repo_id}@{ref}")
            response.raise_for_status()
            payload = response.json()
        if payload.get("encoding") != "base64":
            raise ValueError(
                f"unsupported GitHub content encoding for {repo_id}@{ref}: {payload.get('encoding')}"
            )
        return base64.b64decode(payload.get("content", "")).decode("utf-8")

    def _local_checkout(self, repo: ConfiguredRepo) -> Path | None:
        if not repo.local_checkout_path:
            return None
        checkout = Path(repo.local_checkout_path).expanduser()
        if not checkout.exists() or not (checkout / ".git").exists():
            logger.warning("configured local checkout path does not exist for %s: %s", repo.id, checkout)
            return None
        return checkout

    def _run_git(self, repo_root: Path, *args: str) -> str:
        command = ["git", "-C", str(repo_root), *args]
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise ValueError(detail or f"git command failed: {' '.join(command)}") from exc
        return completed.stdout

    def _parse_commit_lines(self, output: str) -> list[dict]:
        commits = []
        for raw_line in output.splitlines():
            if not raw_line.strip():
                continue
            sha, short_sha, author_name, author_date, message = raw_line.split("\x1f", 4)
            commits.append(
                {
                    "sha": sha,
                    "short_sha": short_sha,
                    "message": message,
                    "author_name": author_name,
                    "author_date": author_date,
                    "html_url": None,
                }
            )
        return commits

    def _list_local_commits(self, repo_root: Path, limit: int, revspec: str | None = None) -> list[dict]:
        args = [
            "log",
            f"-n{limit}",
            "--date=iso-strict",
            "--pretty=format:%H%x1f%h%x1f%an%x1f%aI%x1f%s",
        ]
        if revspec:
            args.append(revspec)
        return self._parse_commit_lines(self._run_git(repo_root, *args))

    def _browse_local_commits(
        self,
        repo: ConfiguredRepo,
        repo_root: Path,
        *,
        query: str | None,
        limit: int,
    ) -> list[dict]:
        query_text = (query or "").strip()
        if not query_text:
            try:
                return self._list_local_commits(repo_root, limit=limit, revspec=repo.default_branch)
            except ValueError:
                return self._list_local_commits(repo_root, limit=limit)

        if SHA_PREFIX_RE.fullmatch(query_text):
            try:
                resolved_sha = self._run_git(repo_root, "rev-parse", "--verify", f"{query_text}^{{commit}}").strip()
            except ValueError:
                return []
            commits = self._list_local_commits(repo_root, limit=1, revspec=resolved_sha)
            return commits[:1]

        try:
            branch_commits = self._list_local_commits(repo_root, limit=limit, revspec=query_text)
            if branch_commits:
                return branch_commits
        except ValueError:
            pass

        candidates = self._parse_commit_lines(
            self._run_git(
                repo_root,
                "log",
                "--all",
                f"-n{max(limit * 5, LOCAL_COMMIT_SCAN_LIMIT)}",
                "--date=iso-strict",
                "--pretty=format:%H%x1f%h%x1f%an%x1f%aI%x1f%s",
            )
        )
        lowered = query_text.lower()
        return [
            commit
            for commit in candidates
            if lowered in commit["sha"].lower()
            or lowered in commit["short_sha"].lower()
            or lowered in (commit["message"] or "").lower()
            or lowered in (commit["author_name"] or "").lower()
        ][:limit]

    def _browse_remote_commits(self, repo: ConfiguredRepo, query: str | None = None, limit: int = 20) -> list[dict]:
        query_text = (query or "").strip()
        if not query_text:
            commits = self._fetch_remote_commit_list(repo, limit=limit, ref=repo.default_branch)
            if commits:
                return commits
            return self._fetch_remote_commit_list(repo, limit=limit, ref=None)

        if SHA_PREFIX_RE.fullmatch(query_text):
            commit = self._fetch_remote_commit(repo, query_text)
            return [commit] if commit else []

        branch_commits = self._fetch_remote_commit_list(repo, limit=limit, ref=query_text)
        if branch_commits:
            return branch_commits

        candidates = self._fetch_remote_commit_list(repo, limit=max(limit * 5, LOCAL_COMMIT_SCAN_LIMIT), ref=None)
        lowered = query_text.lower()
        return [
            commit
            for commit in candidates
            if lowered in commit["sha"].lower()
            or lowered in commit["short_sha"].lower()
            or lowered in (commit["message"] or "").lower()
            or lowered in (commit["author_name"] or "").lower()
        ][:limit]

    def _fetch_remote_commit_list(
        self,
        repo: ConfiguredRepo,
        *,
        limit: int,
        ref: str | None,
    ) -> list[dict]:
        owner_repo = repo.full_name
        params = {"per_page": str(limit)}
        if ref:
            params["sha"] = ref
        url = f"{repo.api_url}/repos/{owner_repo}/commits"
        with httpx.Client(timeout=15.0) as client:
            response = client.get(url, headers=self._headers(), params=params)
            if response.status_code == 404:
                if ref:
                    return []
                raise FileNotFoundError(f"repo {owner_repo} is not accessible via the GitHub API")
            response.raise_for_status()
            commits = response.json()
        return [self._normalize_remote_commit(item) for item in commits]

    def _fetch_remote_commit(self, repo: ConfiguredRepo, ref: str) -> dict | None:
        owner_repo = repo.full_name
        url = f"{repo.api_url}/repos/{owner_repo}/commits/{ref}"
        with httpx.Client(timeout=15.0) as client:
            response = client.get(url, headers=self._headers())
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return self._normalize_remote_commit(response.json())

    def _normalize_remote_commit(self, item: dict) -> dict:
        commit = item.get("commit", {})
        author = commit.get("author", {})
        return {
            "sha": item.get("sha"),
            "short_sha": (item.get("sha") or "")[:12],
            "message": (commit.get("message") or "").splitlines()[0],
            "author_name": author.get("name"),
            "author_date": author.get("date"),
            "html_url": item.get("html_url"),
        }

    def _fetch_file_from_local_checkout(
        self,
        repo: ConfiguredRepo,
        repo_root: Path,
        path: str,
        ref: str,
    ) -> str:
        normalized_path = path.lstrip("/")
        try:
            self._run_git(repo_root, "rev-parse", "--verify", f"{ref}^{{commit}}")
        except ValueError as exc:
            raise FileNotFoundError(f"commit {ref} not found in local checkout for {repo.id}") from exc
        try:
            return self._run_git(repo_root, "show", f"{ref}:{normalized_path}")
        except ValueError as exc:
            raise FileNotFoundError(f"{path} not found for {repo.id}@{ref}") from exc
