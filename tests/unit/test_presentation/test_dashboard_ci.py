"""CI status from GitHub and coverage from Codecov (spec 010 T043, research R9).

Every response is served by an ``httpx.MockTransport`` from bodies captured
from the public APIs (fixtures/github/README.md). The fake API also plays the
parts that matter for the rate limit: ETags and ``304``, and the rate-limit
headers.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from snapl_presentation.dashboard.ci import TOKEN_REMEDY, CiSource, parse_repo, resolve_repo
from snapl_presentation.dashboard.models import JobResult, SourceName

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures"
MAIN_PULLS = json.loads((FIXTURES / "github" / "commits_main_pulls.json").read_text())
OPEN_PULLS = json.loads((FIXTURES / "github" / "pulls_open.json").read_text())
CHECK_RUNS = json.loads((FIXTURES / "github" / "check_runs_pr164_head.json").read_text())
CODECOV_FOUND = json.loads((FIXTURES / "codecov" / "commit_found.json").read_text())
CODECOV_MISSING = json.loads((FIXTURES / "codecov" / "commit_not_found.json").read_text())

REPO = "anton-tvrz/snapl"
TOKEN = "ghp_" + "x" * 36  # pragma: allowlist secret
T0 = datetime(2026, 11, 7, 23, 0, tzinfo=UTC)
MAIN_HEAD = MAIN_PULLS[0]["merge_commit_sha"]
PR164_HEAD = MAIN_PULLS[0]["head"]["sha"]
FAILING_SHA = "f" * 40


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _failing_check_runs() -> dict:
    body = copy.deepcopy(CHECK_RUNS)
    for run in body["check_runs"]:
        if run["name"].endswith("Unit Tests") or run["name"].endswith("Quality Gate"):
            run["conclusion"] = "failure"
    return body


def _open_prs(count: int) -> list[dict]:
    """``count`` open PRs shaped like the captured ones, newest updated last."""
    prs = []
    for index in range(count):
        pr = copy.deepcopy(OPEN_PULLS[0])
        pr["number"] = 300 + index
        pr["title"] = f"PR {300 + index}"
        pr["head"]["sha"] = f"{index:040x}"
        pr["updated_at"] = (T0 - timedelta(hours=count - index)).strftime("%Y-%m-%dT%H:%M:%SZ")
        prs.append(pr)
    return prs


class FakeApi:
    def __init__(self, *, open_prs: list[dict] | None = None, remaining: int = 59, codecov: int = 404) -> None:
        self.open_prs = OPEN_PULLS if open_prs is None else open_prs
        self.remaining = remaining
        self.reset = T0 + timedelta(hours=1)
        self.codecov = codecov
        self.offline = False
        self.requests: list[httpx.Request] = []

    def github_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "api.github.com"]

    def check_run_shas(self) -> list[str]:
        return [r.url.path.split("/")[-2] for r in self.github_requests() if r.url.path.endswith("/check-runs")]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.offline:
            raise httpx.ConnectError("nodename nor servname provided", request=request)
        if request.url.host == "api.codecov.io":
            body = CODECOV_FOUND if self.codecov == 200 else CODECOV_MISSING
            return httpx.Response(self.codecov, json=body)
        path = request.url.path
        if path.endswith("/commits/main/pulls"):
            body = MAIN_PULLS
        elif path.endswith("/pulls"):
            body = self.open_prs
        elif path.endswith("/check-runs"):
            body = _failing_check_runs() if f"/{FAILING_SHA}/" in path else CHECK_RUNS
        else:
            return httpx.Response(404, json={"message": "Not Found"})
        etag = 'W/"' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() + '"'
        headers = {
            "ETag": etag,
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(int(self.reset.timestamp())),
        }
        if request.headers.get("If-None-Match") == etag:
            return httpx.Response(304, headers=headers)
        return httpx.Response(200, json=body, headers=headers)


def _source(api: FakeApi, clock: Clock, *, token: str | None = None, repo: str | None = REPO) -> CiSource:
    return CiSource(repo, token, clock=clock, transport=httpx.MockTransport(api.handler))


# --- parsing -------------------------------------------------------------------


async def test_main_takes_its_gate_from_the_pr_it_was_merged_from():
    """The quality workflow runs on pull requests only, so main's own head
    carries no gate. Its squash commit has the PR head's tree."""
    api = FakeApi()

    view = await _source(api, Clock()).refresh()

    assert view.source is SourceName.CI
    assert view.available is True
    status = view.data
    assert status is not None
    main = status.targets[0]
    assert (main.kind, main.head_sha) == ("main", MAIN_HEAD)
    assert main.gate is JobResult.SUCCESS
    assert main.run_url is not None
    assert main.run_url.startswith("https://github.com/anton-tvrz/snapl/actions/runs/")
    assert "/job/" not in main.run_url
    assert PR164_HEAD in api.check_run_shas()


async def test_jobs_are_the_latest_run_of_each_check():
    """Several workflows ran more than once on the captured head."""
    view = await _source(FakeApi(), Clock()).refresh()

    jobs = view.data.targets[0].jobs
    names = [job.name for job in jobs]
    assert len(names) == len(set(names))
    assert "Quality Checks / Unit Tests" in names
    by_name = {job.name: job for job in jobs}
    assert by_name["Quality Checks / YAML Lint"].result is JobResult.SKIPPED
    assert by_name["Quality Checks / Unit Tests"].url.startswith("https://github.com/")


async def test_open_prs_follow_main_in_number_order_with_their_gates():
    prs = _open_prs(2)
    prs[0]["head"]["sha"] = FAILING_SHA
    api = FakeApi(open_prs=list(reversed(prs)))

    status = (await _source(api, Clock()).refresh()).data

    assert [(t.kind, t.number) for t in status.targets] == [
        ("main", None),
        ("pull_request", 300),
        ("pull_request", 301),
    ]
    failing = status.targets[1]
    assert failing.title == "PR 300"
    assert failing.head_sha == FAILING_SHA
    assert failing.gate is JobResult.FAILURE
    assert {j.name for j in failing.jobs if j.result is JobResult.FAILURE} >= {"Quality Checks / Unit Tests"}


async def test_the_captured_open_prs_are_listed():
    status = (await _source(FakeApi(), Clock()).refresh()).data

    assert [t.number for t in status.targets[1:]] == [133, 135, 152]


async def test_coverage_comes_from_codecov_when_it_has_the_commit():
    status = (await _source(FakeApi(codecov=200), Clock()).refresh()).data

    assert status.targets[0].coverage_pct == 90.0
    assert all(t.coverage_pct is None for t in status.targets[1:])


async def test_coverage_is_unknown_when_codecov_has_nothing():
    """What the capture found: Codecov holds no data for this repository."""
    status = (await _source(FakeApi(codecov=404), Clock()).refresh()).data

    assert status.targets[0].coverage_pct is None


# --- caching, budget and the rate limit ----------------------------------------


async def test_an_unchanged_response_is_reused_from_its_etag():
    api = FakeApi()
    clock = Clock()
    source = _source(api, clock)
    first = await source.refresh()
    clock.advance(600)

    second = await source.refresh()

    revalidated = [r for r in api.github_requests()[len(api.github_requests()) // 2 :] if "If-None-Match" in r.headers]
    assert revalidated
    assert second.data == first.data
    assert second.fetched_at == T0 + timedelta(seconds=600)


@pytest.mark.parametrize(("token", "interval"), [(TOKEN, 120), (None, 600)])
async def test_refresh_interval_depends_on_the_token(token: str | None, interval: int):
    api = FakeApi()
    clock = Clock()
    source = _source(api, clock, token=token)
    await source.refresh()
    sent = len(api.requests)

    clock.advance(interval - 1)
    await source.refresh()
    assert len(api.requests) == sent
    clock.advance(1)
    await source.refresh()
    assert len(api.requests) > sent


async def test_without_a_token_check_runs_are_fetched_for_the_5_newest_prs():
    api = FakeApi(open_prs=_open_prs(8))

    view = await _source(api, Clock()).refresh()

    status = view.data
    assert status.authenticated is False
    assert status.omitted_prs == 3
    assert [t.number for t in status.targets[1:]] == [303, 304, 305, 306, 307]
    assert view.remedy == TOKEN_REMEDY


async def test_with_a_token_every_pr_is_fetched():
    api = FakeApi(open_prs=_open_prs(8))

    view = await _source(api, Clock(), token=TOKEN).refresh()

    assert view.data.authenticated is True
    assert view.data.omitted_prs == 0
    assert len(view.data.targets) == 9
    assert view.remedy is None


async def test_an_unauthenticated_refresh_costs_3_plus_n_calls_even_when_unchanged():
    """Without a token a 304 still counts against the limit (research R9)."""
    api = FakeApi(open_prs=_open_prs(5))
    clock = Clock()
    source = _source(api, clock)

    await source.refresh()
    first = len(api.github_requests())
    clock.advance(600)
    await source.refresh()

    assert first == 3 + 5
    assert len(api.github_requests()) == 2 * (3 + 5)


async def test_a_nearly_spent_rate_limit_pauses_requests_until_it_resets():
    api = FakeApi(remaining=5)
    api.reset = T0 + timedelta(seconds=1000)
    clock = Clock()
    source = _source(api, clock, token=TOKEN)
    first = await source.refresh()
    sent = len(api.requests)

    clock.advance(120)
    paused = await source.refresh()
    clock.advance(881)
    resumed = await source.refresh()

    assert first.available is True
    assert first.data.rate_limit_remaining == 5
    assert paused.available is False
    assert paused.stale is True
    assert paused.data == first.data
    assert paused.reason is not None
    assert "rate limit" in paused.reason
    assert paused.remedy == TOKEN_REMEDY
    assert len(api.requests) > sent
    assert resumed.fetched_at == T0 + timedelta(seconds=1001)


async def test_no_network_on_the_first_call_is_unavailable():
    api = FakeApi()
    api.offline = True

    view = await _source(api, Clock()).refresh()

    assert view.available is False
    assert view.data is None
    assert view.remedy == TOKEN_REMEDY
    assert view.reason is not None
    assert "GitHub" in view.reason


async def test_the_token_is_sent_but_never_shown_or_logged(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG)
    api = FakeApi()
    source = _source(api, Clock(), token=TOKEN)

    view = await source.refresh()
    api.offline = True
    source_after_failure = await _source(api, Clock(), token=TOKEN).refresh()

    assert all(r.headers["Authorization"] == f"Bearer {TOKEN}" for r in api.github_requests())
    assert all("Authorization" not in r.headers for r in api.requests if r.url.host == "api.codecov.io")
    assert TOKEN not in view.model_dump_json()
    assert TOKEN not in source_after_failure.model_dump_json()
    assert TOKEN not in repr(source)
    assert TOKEN not in caplog.text


# --- which repository ----------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:anton-tvrz/snapl.git",
        "https://github.com/anton-tvrz/snapl.git",
        "https://github.com/anton-tvrz/snapl",
        "ssh://git@github.com/anton-tvrz/snapl.git",
    ],
)
def test_parse_repo_from_a_remote_url(url: str):
    assert parse_repo(url) == REPO


def test_a_non_github_remote_is_not_a_repo():
    assert parse_repo("git@gitlab.com:someone/thing.git") is None


def test_resolve_repo_reads_the_origin_remote(tmp_path: Path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "git@github.com:anton-tvrz/snapl.git"], check=True
    )

    assert resolve_repo(None, tmp_path) == REPO
    assert resolve_repo("someone/fork", tmp_path) == "someone/fork"


def test_resolve_repo_outside_a_repository(tmp_path: Path):
    assert resolve_repo(None, tmp_path) is None


async def test_an_unknown_repository_is_unavailable_with_how_to_name_it():
    view = await _source(FakeApi(), Clock(), repo=None).refresh()

    assert view.available is False
    assert view.remedy == "export SNAPL_GITHUB_REPO=owner/name"
