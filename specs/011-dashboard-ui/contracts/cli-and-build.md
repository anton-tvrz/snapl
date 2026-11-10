# Contract: `snapl dashboard` options, the snapshot file, and the build tasks

**Feature**: 011-dashboard-ui | **Extends**: [010's CLI contract](../../010-dashboard-read-api/contracts/cli-dashboard.md)

## `snapl dashboard`

```text
snapl dashboard [--use-case TEXT] [--port INT] [--host TEXT]
                [--archive FILE] [--snapshot FILE [--days INT]]
```

| option | default | meaning |
| --- | --- | --- |
| `--archive FILE` | none | Serve from a `history.archive` tarball, read-only, with no stack. |
| `--snapshot FILE` | none | Write a single-file snapshot to `FILE` and exit. Serves nothing. |
| `--days INT` | `7` | With `--snapshot`: the range the snapshot covers, ending at the server's now. 1 to 90. |

010's behaviour (port, host, the no-authentication warning, exit codes) is unchanged.

### Start line

- **Built UI present**: `Dashboard for dcfabric at http://127.0.0.1:18030 (read-only, Ctrl-C to stop)`, as today.
- **Built UI absent**: the same line, then `The designed UI is not built: serving the plain page. Build it with: uv run invoke dashboard.build`.
- **Archive**: `Archive snapl-history-20261101T090000Z.tar.gz (made 2026-11-01 09:00 UTC, commit 6baff39) for dcfabric at http://127.0.0.1:18030 (read-only, Ctrl-C to stop)`.

### `--archive`

- **Use case**: taken from the archive's manifest. `--use-case` with a different value is an error.
- **Refused before binding a port**, exit `ExitCode.ERROR`, with one of:
  - `error: <file> does not exist.`
  - `error: <file> is not a snapl history archive (no manifest.json).`
  - `error: <file> has format <format>; this snapl reads snapl-history/1.`
- **Missing parts**: the archive opens. The start line is followed by one line per missing part, `  missing: <part> (<reason from the manifest>)`.
- **Reads**: the archive is extracted to a temporary directory, with members confined to it. The directory is removed when the server stops, on Ctrl-C included. The archive file is never opened for writing.
- **Network**: no connection is made to Temporal, the Source of Truth, GitHub, Codecov or Prometheus. A test replaces each client with one that fails on use.
- **Environment**: connection settings and tokens are not read, so a stale `.env` cannot change what an archive shows.

### `--snapshot`

- **Without `--archive`**: built from the live sources, exactly as the server would answer.
- **With `--archive`**: built from the archive.
- **Needs the built UI.** Without `static/ui/snapshot.html`: `error: the designed UI is not built. Build it with: uv run invoke dashboard.build`, exit `ExitCode.ERROR`. There is no plain-page snapshot.
- **Refuses to overwrite** an existing file unless `--force` is given.
- **Writes** through a `.partial` file, renamed when complete.
- **Prints**, on success, exit `0`:

  ```text
  Snapshot written: demo.html (412 kB)
    range: 2026-11-02 21:20 to 2026-11-09 21:20 UTC (7 days)
    from: live dashboard sources, commit 6baff39
    contains: devices, links, drift history, runs and their steps, schedule, CI status, lab runs, metrics
    note: error text and paths recorded from devices are included as recorded. Read it before publishing.
    unavailable when written: prometheus (connection refused)
  ```

  The last line appears only when a source did not answer. The snapshot is still written, and those views say so inside it.

## The snapshot file (`format: "snapl-snapshot/1"`)

- **One file.** Every script, style and font is inside it. It references nothing outside itself.
- **Policy**: it carries `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:">`.
- **Data**: one `<script id="snapl-bundle" type="application/json">` element holding `SnapshotBundle` (data-model.md). `<`, `>`, `&`, U+2028 and U+2029 are escaped inside it.
- **Contains no** credential, token or environment value. The bundle is built only from API responses.
- **In the browser**:
  - A banner says it is a recording, when it was made, from which commit, and the range it covers.
  - "Now" is the bundle's `end`. The "as of" control works over the bundle's range.
  - No view is labelled live, and nothing refreshes.
  - Suggested commands are shown under "the operator could run", with no copy control.
  - The metrics range control is fixed to the bundled range.
- **A reader refuses** a bundle whose `format` it does not know, with a message, instead of rendering part of it.

## Invoke tasks

| task | does |
| --- | --- |
| `invoke dashboard.build` | `npm ci`, then the UI build, into `packages/presentation/snapl_presentation/dashboard/static/ui/`. Fails with `Node 22.12 or later is needed to build the UI (only to build it). Running snapl does not need Node.` when `node` is missing or older. *Amended in PR C*: the floor is 22.12, Vite 8's. |
| `invoke dashboard.dev` | The Vite dev server, proxying `/api` to `DASHBOARD_PORT`. Needs `snapl dashboard` running. |
| `invoke dashboard.test` | `tsc --noEmit`, ESLint, the types check, and Vitest with coverage. |
| `invoke dashboard.types` | Regenerates `src/api/schema.d.ts` from the OpenAPI snapshot. |
| `invoke dashboard.e2e` | Builds, installs the browser the check uses, then runs the Playwright check against the fixture server. |

Every task takes `--no-install` to skip `npm ci` when the dependencies are already there.

`invoke check-all` is unchanged: it stays Python-only, so it never needs Node.

## Packaging

- `packages/presentation/pyproject.toml` lists `snapl_presentation/dashboard/static/ui/**` as build artifacts for the wheel and the sdist.
- `.gitignore` ignores `packages/presentation/snapl_presentation/dashboard/static/ui/` and `packages/presentation/ui/node_modules/`.
- `build-artifacts.yml` builds the UI before the presentation wheel and fails if the wheel lacks `snapl_presentation/dashboard/static/ui/index.html`.

## CI

`quality.yml` gains a `ui` job behind a `ui` path filter (`packages/presentation/ui/**`, `packages/presentation/snapl_presentation/dashboard/**`, `tests/unit/test_presentation/fixtures/openapi_v1.json`, `dev/scripts/serve_dashboard_fixtures.py`, and `quality.yml` itself, so a change to the job runs it). The quality gate lists it, and treats `skipped` as passing, as for every other job.
