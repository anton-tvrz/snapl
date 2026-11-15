# Data Model: Dashboard Look Refresh

Nothing here is served by the API. These are the view models the UI derives from answers it already has, so that the logic is tested without rendering. They live in `packages/presentation/ui/src/lib/`.

## `NumberCard` (`lib/numbers.ts`)

| field | type | |
| --- | --- | --- |
| `id` | `"in_sync" \| "open_drifts" \| "latest_repair" \| "deploys_repairs"` | |
| `label` | `string` | "Devices in sync", "Open drifts", "Latest repair", "Deploys and repairs, 24 h" |
| `state` | `"known" \| "nothing_to_compare" \| "unavailable" \| "present_only"` | |
| `value` | `string \| null` | the large text: "5 of 6", "2", "13 s", "4 ok · 1 failed". `null` unless the state is `known` or `nothing_to_compare` |
| `context` | `string` | one line: the comparison, the device and age, or why there is no value |
| `target` | `string` | the id of the panel the card links to |

Rules:
- `in_sync` counts devices with `in_intent`. With none, `value` is `null` and `context` says no device is in intent.
- `open_drifts` counts open episodes whose lane is in intent. Others go into `context` as "n more on devices no longer in intent".
- `latest_repair` uses `latest_closed`. Its context reads "<device>, scan to scan, <what repaired it>, <age>". With no closed episode, `value` is `null` and the context says no repair is recorded.
- `deploys_repairs` sums `by_outcome` over the buckets that start within 24 hours before `now`. Buckets with `null` counts are skipped, and if all are skipped the state is `unavailable`.
- A comparison is given only when the timeline covers the earlier point (research R4).

## `FabricSummary` (`lib/summary.ts`)

| field | type | |
| --- | --- | --- |
| `inIntent` | `number` | |
| `inSync` | `number` | |
| `scanned` | `number` | devices with any state but `never_scanned` |
| `drifted` | `DriftedDevice[]` | most differing paths first, then by name |

### `DriftedDevice`

| field | type | |
| --- | --- | --- |
| `name` | `string` | |
| `state` | drift state, raw | drawn with its own shape and glyph |
| `paths` | `number` | missing + undesired + unclassified |
| `share` | `number` | `paths` over the largest `paths`, for the bar's length; 1 when all are equal |

A device in the `error` state is listed with its state and no bar.

## `HistoryBar` (`lib/historyBars.ts`)

Built from `historyRows` (unchanged), oldest first.

| field | type | |
| --- | --- | --- |
| `t` | `number` | the bucket's start, in ms |
| `quiet` | `boolean` | no scan and no deploy: drawn as nothing |
| `scans` | `number \| null` | `null` when quiet |
| `mostDrifted` | `number \| null` | |
| `deploysOk`, `deploysFailed`, `repairsOk`, `repairsFailed` | `number \| null` | |

## `LeadValue` (`lib/metrics.ts`)

| field | type | |
| --- | --- | --- |
| `text` | `string` | the latest value, formatted: "212 ms", "1.4/min", "up" |
| `series` | `string \| null` | which series it is, when the view has more than one: the highest for durations and rates, the first that is down for up/down views |
| `unitNote` | `string \| null` | "per minute" when a per-second rate was reworded |

`formatValue` gains the per-minute form for `per_second` values below 1.

## Look rules (`src/look.test.ts`)

| rule | allowed |
| --- | --- |
| spacing between blocks (`m*`, `p*`, `gap*`, `space-*`) | 0, 2, 4, 6, 8, and `px` |
| spacing inside a chip, badge, mark or table cell | also 0.5, 1, 1.5, when the same class string carries the `chip` or `cell` marker class |
| type | `text-meta`, `text-body`, `text-title`, `text-heading`, `text-number`, `text-number-lg` |
| allowlist | file paths still to be restyled; empty when the feature is complete |
