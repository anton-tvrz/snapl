# Lab run fixtures (spec 010 T039)

Hand-written records in the `format: 1` layout of `contracts/lab-runs.md`, one per case the reader must handle:

| folder | case |
| --- | --- |
| `20261105T090000Z-e2e` | passed, with `junit.xml` |
| `20261106T090000Z-integration` | failed, dirty tree, with `junit.xml` |
| `20261106T100000Z-e2e` | still `running`, with pid 2147483646, above any pid limit on macOS or Linux: `incomplete` |
| `20261106T120000Z-integration` | `format: 2`: `unreadable` |

The truncated-JSON case (`20261106T110000Z-e2e`, `unreadable`) is written by the test itself: a corrupt JSON file can't be committed past the repository's `check-json` hook.

`COMMIT_PLACEHOLDER` and `TREE_PLACEHOLDER` stand for git object ids. The reader tests copy these folders into a temporary directory and fill the placeholders from a temporary git repository, so the commit-count cases run against real git history.
