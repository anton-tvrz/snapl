"""CI status from GitHub, coverage from Codecov (spec 010 US4, research R9).

**What `main`'s status means here.** The quality workflow runs on pull
requests only, never on a push to `main`, so `main`'s own head carries no
quality gate. `main` is squash-merged from up-to-date branches, so its head
has the tree of the PR head it came from. `main`'s gate is therefore read
from that PR's head (``GET /repos/{repo}/commits/main/pulls``).

**Rate limit.** Without a token GitHub allows 60 requests an hour, and every
request counts, ``304`` included. So: refresh every 600 s without a token
(120 s with one), fetch check runs for at most 5 open PRs without a token,
revalidate with ETags, and stop asking when fewer than 10 requests remain until
the limit resets. A refresh costs 3 + N GitHub requests for N PRs.

The token is sent in the ``Authorization`` header and nowhere else: never in
a view, a log line or a ``repr``.
"""

from __future__ import annotations

import re

# Bandit B404: subprocess runs one fixed git command, no shell.
import subprocess  # nosec B404
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from snapl_presentation.dashboard.models import CiJob, CiStatus, CiTarget, JobResult, SourceName, View
from snapl_presentation.dashboard.sources import Cached, SourceUnavailable, utcnow

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

TOKEN_REMEDY = "export GITHUB_TOKEN=…"  # noqa: S105 — a remedy to print, not a secret
REPO_REMEDY = "export SNAPL_GITHUB_REPO=owner/name"
UNAUTHENTICATED_PR_CAP = 5
RATE_LIMIT_FLOOR = 10
GATE_SUFFIX = "Quality Gate"
_TIMEOUT_S = 10.0

_REMOTE = re.compile(r"github\.com[:/](?P<repo>[^/\s]+/[^/\s]+?)(?:\.git)?/?$")
_CONCLUSIONS = {
    "success": JobResult.SUCCESS,
    "failure": JobResult.FAILURE,
    "timed_out": JobResult.FAILURE,
    "startup_failure": JobResult.FAILURE,
    "cancelled": JobResult.CANCELLED,
    "skipped": JobResult.SKIPPED,
    "neutral": JobResult.NEUTRAL,
    "action_required": JobResult.NEUTRAL,
    "stale": JobResult.NEUTRAL,
}


def parse_repo(url: str) -> str | None:
    """``owner/name`` from a GitHub remote URL (https, scp-like ssh, or ssh://)."""
    match = _REMOTE.search(url.strip())
    return match.group("repo") if match else None


def resolve_repo(configured: str | None, repo_path: Path) -> str | None:
    """``SNAPL_GITHUB_REPO`` if set, else the ``origin`` remote of ``repo_path``."""
    if configured:
        return configured
    try:
        # Bandit: fixed git arguments, no shell.
        result = subprocess.run(  # nosec B603 B607
            ["git", "remote", "get-url", "origin"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_repo(result.stdout) if result.returncode == 0 else None


def _job_result(run: dict[str, Any]) -> JobResult:
    if run.get("status") == "queued":
        return JobResult.QUEUED
    if run.get("status") != "completed":
        return JobResult.IN_PROGRESS
    return _CONCLUSIONS.get(run.get("conclusion") or "", JobResult.NEUTRAL)


def _latest_per_name(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A workflow re-run (the ``labeled`` trigger) leaves several runs per name."""
    latest: dict[str, dict[str, Any]] = {}
    for run in runs:
        name = run.get("name", "")
        if name not in latest or run.get("id", 0) > latest[name].get("id", 0):
            latest[name] = run
    return sorted(latest.values(), key=lambda run: run.get("name", ""))


def _target(
    kind: str, head_sha: str, check_runs: dict[str, Any], *, number: int | None = None, title: str | None = None
) -> CiTarget:
    runs = _latest_per_name(check_runs.get("check_runs", []))
    gate = next((run for run in runs if run.get("name", "").endswith(GATE_SUFFIX)), None)
    run_url = None
    if gate is not None:
        url = gate.get("html_url") or ""
        run_url = url.split("/job/")[0] if "/job/" in url else gate.get("details_url")
    return CiTarget(
        kind=kind,  # type: ignore[arg-type]
        number=number,
        title=title,
        head_sha=head_sha,
        # No gate yet: the checks have not been queued, or are about to be.
        gate=_job_result(gate) if gate is not None else JobResult.QUEUED,
        jobs=[CiJob(name=run["name"], result=_job_result(run), url=run.get("html_url") or "") for run in runs],
        run_url=run_url,
    )


class CiSource:
    """GitHub's view of `main` and the open PRs, refreshed at most every 120 or 600 s."""

    def __init__(
        self,
        repo: str | None,
        token: str | None,
        *,
        clock: Callable[[], datetime] = utcnow,
        transport: httpx.AsyncBaseTransport | None = None,
        github_url: str = "https://api.github.com",
        codecov_url: str = "https://api.codecov.io",
    ) -> None:
        self._repo = repo
        self._token = token
        self._clock = clock
        self._transport = transport
        self._github_url = github_url
        self._codecov_url = codecov_url
        self._etags: dict[str, tuple[str, Any]] = {}
        self._remaining: int | None = None
        self._paused_until: datetime | None = None
        self._reset_at: datetime | None = None
        self._cached: Cached[CiStatus] = Cached(
            SourceName.CI, self._load, ttl=self.interval, clock=clock, remedy=TOKEN_REMEDY
        )

    def __repr__(self) -> str:
        return f"CiSource(repo={self._repo!r}, authenticated={self._token is not None})"

    @property
    def interval(self) -> timedelta:
        return timedelta(seconds=120 if self._token else 600)

    async def refresh(self) -> View[CiStatus]:
        view = await self._cached.get()
        if view.available and view.data is not None and view.data.omitted_prs:
            # Available, but incomplete: say how to see every PR.
            return view.model_copy(update={"remedy": TOKEN_REMEDY})
        return view

    async def _load(self) -> CiStatus:
        if self._repo is None:
            raise SourceUnavailable("could not tell which GitHub repository this is", REPO_REMEDY)
        now = self._clock()
        if self._paused_until is not None and now < self._paused_until:
            raise SourceUnavailable(
                f"GitHub rate limit nearly spent ({self._remaining} left); paused until {self._paused_until:%H:%M} UTC",
                TOKEN_REMEDY,
            )
        async with httpx.AsyncClient(transport=self._transport, timeout=_TIMEOUT_S) as client:
            try:
                status = await self._fetch(client)
            except httpx.HTTPError as exc:
                reason = f"GitHub could not be reached: {type(exc).__name__}: {exc}"
                raise SourceUnavailable(reason, TOKEN_REMEDY) from exc
        if self._remaining is not None and self._remaining < RATE_LIMIT_FLOOR:
            self._paused_until = self._reset_at
        return status

    async def _fetch(self, client: httpx.AsyncClient) -> CiStatus:
        repo = self._repo
        assert repo is not None
        merged = await self._github(client, f"/repos/{repo}/commits/main/pulls")
        open_prs = await self._github(client, f"/repos/{repo}/pulls?state=open&per_page=100")

        targets: list[CiTarget] = []
        source_pr = next((pr for pr in merged if pr.get("merge_commit_sha")), None)
        if source_pr is not None:
            pr_head = source_pr["head"]["sha"]
            main = _target("main", source_pr["merge_commit_sha"], await self._check_runs(client, pr_head)).model_copy(
                update={"coverage_pct": await self._coverage(client, pr_head)}
            )
            targets.append(main)

        newest_first = sorted(open_prs, key=lambda pr: pr.get("updated_at") or "", reverse=True)
        fetched = newest_first if self._token else newest_first[:UNAUTHENTICATED_PR_CAP]
        prs = [
            _target(
                "pull_request",
                pr["head"]["sha"],
                await self._check_runs(client, pr["head"]["sha"]),
                number=pr["number"],
                title=pr.get("title"),
            )
            for pr in fetched
        ]
        targets += sorted(prs, key=lambda target: target.number or 0)
        return CiStatus(
            repo=repo,
            authenticated=self._token is not None,
            rate_limit_remaining=self._remaining,
            targets=targets,
            omitted_prs=len(open_prs) - len(fetched),
        )

    async def _check_runs(self, client: httpx.AsyncClient, sha: str) -> dict[str, Any]:
        body: dict[str, Any] = await self._github(client, f"/repos/{self._repo}/commits/{sha}/check-runs?per_page=100")
        return body

    async def _github(self, client: httpx.AsyncClient, path: str) -> Any:
        url = f"{self._github_url}{path}"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "snapl-dashboard"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        cached = self._etags.get(url)
        if cached is not None:
            headers["If-None-Match"] = cached[0]
        response = await client.get(url, headers=headers)
        self._note_rate_limit(response)
        if response.status_code == 304 and cached is not None:
            return cached[1]
        if response.status_code != 200:
            if self._remaining == 0:
                self._paused_until = self._reset_at
            reason = f"GitHub answered {response.status_code} for {path.split('?', maxsplit=1)[0]}"
            raise SourceUnavailable(reason, TOKEN_REMEDY)
        body = response.json()
        etag = response.headers.get("ETag")
        if etag:
            self._etags[url] = (etag, body)
        return body

    def _note_rate_limit(self, response: httpx.Response) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if remaining is not None and remaining.isdigit():
            self._remaining = int(remaining)
        if reset is not None and reset.isdigit():
            self._reset_at = datetime.fromtimestamp(int(reset), tz=UTC)

    async def _coverage(self, client: httpx.AsyncClient, sha: str) -> float | None:
        """Codecov's coverage for a commit, or None when it has none.

        Codecov is a separate service: it costs nothing against GitHub's limit,
        and its failure never fails the CI view.
        """
        owner, _, name = (self._repo or "").partition("/")
        url = f"{self._codecov_url}/api/v2/github/{owner}/repos/{name}/commits/{sha}/"
        try:
            response = await client.get(url, headers={"User-Agent": "snapl-dashboard"})
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        coverage = (response.json().get("totals") or {}).get("coverage")
        return float(coverage) if isinstance(coverage, (int, float)) else None
