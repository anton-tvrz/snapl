// 010's history as chart bars, one per bucket, oldest first (spec 014 FR-020,
// research R5). Built from the table's rows, so the chart and the table can
// never disagree. A quiet bucket has nulls: it is drawn as nothing, not zero.
import type { HistoryResponse } from "../api/types";
import { historyRows } from "./history";

export interface HistoryBar {
  /** The bucket's start, in milliseconds. */
  t: number;
  /** No scan and no deploy in the bucket. */
  quiet: boolean;
  scans: number | null;
  mostDrifted: number | null;
  deploysOk: number | null;
  deploysFailed: number | null;
  repairsOk: number | null;
  repairsFailed: number | null;
}

export function historyBars(history: HistoryResponse): HistoryBar[] {
  return historyRows(history)
    .map((row) => ({
      t: Date.parse(row.start),
      quiet: row.quiet,
      scans: row.scans > 0 ? row.scans : null,
      mostDrifted: row.mostDrifted,
      deploysOk: row.deploys.succeeded,
      deploysFailed: row.deploys.failed,
      repairsOk: row.repairs.succeeded,
      repairsFailed: row.repairs.failed,
    }))
    .reverse();
}
