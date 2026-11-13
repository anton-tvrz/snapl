# Quickstart: Designed Dashboard UI

## Run it

```bash
uv run invoke dashboard.build     # once, and after UI changes; needs Node 22+
uv run invoke dev.deps            # the stack
uv run invoke orchestrator.start  # the worker
uv run snapl dashboard            # http://127.0.0.1:18030
```

- Without the build step the same address serves the plain page, and says how to build the UI.
- `http://127.0.0.1:18030/plain` is always the plain page.

## Work on the UI

```bash
uv run snapl dashboard            # the API, in one terminal
uv run invoke dashboard.dev       # Vite's dev server, proxying /api, in another
uv run invoke dashboard.test      # types, lint, the types check, unit tests with coverage
uv run invoke dashboard.e2e       # build, then the browser check on fixtures
uv run invoke dashboard.types     # after a contract change, regenerate the UI's types
```

## Look without the lab

```bash
uv run snapl dashboard --archive .snapl/archives/snapl-history-<ts>.tar.gz
uv run snapl dashboard --snapshot demo.html                    # from the running stack's sources
uv run snapl dashboard --archive <file> --snapshot demo.html   # from an archive
open demo.html                                                 # no server needed
```

## Live evidence for the implementation PRs (Principle III)

Each item names what to run and what to observe. Paste the output or a screenshot into the PR that closes the matching task. Items 1 to 6 need the lab.

1. **The demo arc on the topology (SC-001, SC-002)**:
   - With the fabric converged and the dashboard open, run the arc: `snapl deploy leaf-01`, induce drift on spine-01 (`sr_cli -ec 'set / interface ethernet-1/1 admin-state disable'`), `snapl scan --use-case dcfabric`, `snapl reconcile --use-case dcfabric --drifted --yes`, `snapl scan --use-case dcfabric`.
   - **Observe**: each deploy shows activity on its device although it takes about a second. After the first scan, spine-01 is drawn as missing and its link to leaf-01 is marked. After the last scan it is in sync. Nothing was typed into the page.
2. **Replay (SC-004)**:
   - Move the "as of" control to between the first and last scans.
   - **Observe**: spine-01 is drawn as drifted with its path, the page says it shows a past time, the schedule and CI panels say they show only the present, and "now" returns everything to live.
3. **The headline and the episode (SC-005)**:
   - **Observe**: the headline gives spine-01's drift duration, scan to scan, and names the reconcile. Compare both times with `snapl audit` for the two scans.
4. **The loop strip, including a failure (research R15)**:
   - Select the reconcile's deploy of spine-01. **Observe**: Intent, Executor, Collector and Observability each done, in order, with times.
   - Make a deploy fail at apply (for example `docker pause clab-dcfabric-leaf-04`, `snapl deploy leaf-04`, then unpause). **Observe**: Intent done, Executor failed with the reason, the rest not reached.
   - Copy that run's audit rows into `tests/unit/test_presentation/fixtures/` as the failed-deploy fixture, replacing the hand-built one, and update the fixtures README.
5. **A source down (SC-006)**:
   - Stop Temporal. **Observe**: the page is usable within 2 s, the schedule and running panels name Temporal with `uv run invoke dev.deps`, and the topology and lanes are unchanged. Start it again.
6. **Archive (SC-008)**:
   - `uv run invoke history.archive`, then `uv run invoke dev.stop`, and stop the worker and any dashboard.
   - `shasum -a 256 <archive>`, `uv run snapl dashboard --archive <archive>`.
   - **Observe**: the banner, the topology with its links, the lanes and the scrubber over the whole trail, the exported metrics, and "not in this archive" on the running panel. Stop it and check the checksum is unchanged and no temporary directory is left.
7. **Snapshot (SC-009)**. No lab needed if an archive exists:
   - `uv run snapl dashboard --archive <archive> --snapshot demo.html`, then open the file in Chromium, Firefox and Safari with the network panel open before loading it.
   - **Observe**: the recording banner, the topology, the scrubber replaying the arc, the browser's network panel showing zero requests, and the file's size.
8. **No toolchain (SC-012)**:
   - In a fresh clone with `node` off the `PATH`: `uv sync && uv run snapl dashboard`.
   - **Observe**: the plain page at `/`, with the build notice.
9. **The wheel**:
   - `uv run invoke dashboard.build && uv build --package snapl-presentation`, then `unzip -l dist/snapl_presentation-*.whl | grep static/ui/index.html`.
   - **Observe**: the file is in the wheel.

## For the maintainer, not a test

10. **A first-time viewer (SC-003)**: hand the snapshot to someone who has not seen snapl. Time how long they take to say which device drifted, what repaired it and how long it lasted. The target is under 60 seconds.
11. **The three questions (SC-014)**: time yourself answering "is the fabric in sync, what did the schedule do overnight, and is the lab evidence current?" from the designed page. The target is under 15 seconds.
12. **Before publishing a snapshot**: read the error text and paths in it. They are recorded from devices as they were.
13. **The design skill (#146)**: try `pbakaus/impeccable` locally on the built UI before any part of it is committed. No task depends on it.
