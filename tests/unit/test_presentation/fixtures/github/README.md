# GitHub and Codecov fixtures (spec 010 T043)

Captured on 2026-11-07, about 22:50 UTC, from the public APIs for `anton-tvrz/snapl`, **unauthenticated** (no token was sent, so nothing in these files came from one). Pretty-printed; otherwise unchanged.

| file | request |
| --- | --- |
| `commits_main_pulls.json` | `GET /repos/anton-tvrz/snapl/commits/main/pulls`: the PR whose squash commit is `main`'s head (#164, merge commit `1c649df9`) |
| `pulls_open.json` | `GET /repos/anton-tvrz/snapl/pulls?state=open`: three open PRs (#152, #135, #133) |
| `check_runs_pr164_head.json` | `GET /repos/anton-tvrz/snapl/commits/680be566…/check-runs`: every check on #164's head. Several workflows ran more than once (the `labeled` trigger), so most names appear two or three times. |
| `../codecov/commit_not_found.json` | `GET https://api.codecov.io/api/v2/github/anton-tvrz/repos/snapl/commits/680be566…/` (404) |

**What the capture showed, and the spec amendments it led to** (research R9):
- The quality workflow runs on pull requests only (`pr-validation.yml`), never on a push to `main`. `main`'s own head has no quality checks: only the issue-automation checks. So `main`'s gate is read from the PR that produced `main`'s head, which `commits/main/pulls` returns in one call.
- Codecov holds no data for the repository: no branches, and 404 for every commit tried. So coverage is reported as unknown (`null`) whenever Codecov has nothing.

`../codecov/commit_found.json` is **hand-built**, not captured: there was no successful response to capture. Its shape follows Codecov's v2 commit-detail response, and the parser reads only `totals.coverage`.
