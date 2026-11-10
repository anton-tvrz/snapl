import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { DeviceDrawer } from "./components/DeviceDrawer";
import { Ci } from "./components/panels/Ci";
import { History } from "./components/panels/History";
import { LabEvidence } from "./components/panels/LabEvidence";
import { Links } from "./components/panels/Links";
import { MetricPanel } from "./components/panels/Metrics";
import { RangePicker } from "./components/panels/RangePicker";
import { Running, Runs } from "./components/panels/Runs";
import { Schedule } from "./components/panels/Schedule";
import { Shell } from "./components/Shell";
import { SourceStrip } from "./components/SourceStrip";
import { Topology } from "./components/topology/Topology";
import { ViewFrame } from "./components/ViewFrame";

// SC-011's checklist as a test (spec 011 T061, FR-029): every fact 010's plain
// page renders, and the designed component that shows it. The component tests
// named in the comments check the fact itself; this test fails when the plain
// page gains a render function with no row here, or a row names no component.

const PLAIN_PAGE = join(import.meta.dirname, "..", "..", "snapl_presentation", "dashboard", "static", "index.html");

type Component = (...args: never[]) => unknown;
type Row = [plain: string, fact: string, shownBy: Component];

const FACTS: Row[] = [
  // renderDevices / deviceRow: Topology.test.tsx, DeviceDrawer.test.tsx, Shell.test.tsx
  ["renderDevices", "the use case", Shell],
  ["deviceRow", "each device's name, and that it is no longer in intent", Topology],
  ["deviceRow", "its role", DeviceDrawer],
  ["deviceRow", "its drift state", Topology],
  ["deviceRow", "its missing, undesired and unclassified paths", DeviceDrawer],
  ["deviceRow", "its scan error", DeviceDrawer],
  ["deviceRow", "when it was last scanned", DeviceDrawer],
  ["deviceRow", "its last deploy and outcome", DeviceDrawer],
  ["deviceRow", "the command that would reconcile it, and its note", DeviceDrawer],
  // showProblem: ViewFrame.test.tsx
  ["showProblem", "a source unavailable, with its reason and remedy", ViewFrame],
  ["showProblem", "stale data, with its age", ViewFrame],
  // renderRuns / runningItem: panels/Runs.test.tsx
  ["renderRuns", "each run's start, type, target, state, reason, trigger, actor and duration", Runs],
  ["renderRuns", "each run's link into the Temporal Web UI", Runs],
  ["runningItem", "what is running now, its target, how long and its trigger", Running],
  // renderSchedule: panels/Schedule.test.tsx
  ["renderSchedule", "no schedule, with the command that creates one", Schedule],
  ["renderSchedule", "the mode, the interval, active or paused", Schedule],
  ["renderSchedule", "who paused it, when, and the note", Schedule],
  ["renderSchedule", "the next run and the last run", Schedule],
  ["renderSchedule", "repeated failed repairs, with the report-mode command", Schedule],
  ["renderSchedule", "the worker's pollers and task queue", Schedule],
  // renderLab: panels/LabEvidence.test.tsx
  ["renderLab", "the latest run per suite: status, counts, commit, uncommitted changes, age", LabEvidence],
  ["renderLab", "commits on main since, how matched, or unknown with the reason", LabEvidence],
  ["renderLab", "incomplete and unreadable runs", LabEvidence],
  ["renderLab", "no run yet, with the command that records one", LabEvidence],
  // renderCi: panels/Ci.test.tsx
  ["renderCi", "main and each PR, linked to its run, with its gate", Ci],
  ["renderCi", "the failed jobs, linked", Ci],
  ["renderCi", "main's coverage", Ci],
  ["renderCi", "the repository, and the PRs left out without a token", Ci],
  // renderSources: SourceStrip.test.tsx
  ["renderSources", "each source, its age, and its remedy when down", SourceStrip],
  // renderHistory: panels/History.test.tsx
  ["renderHistory", "per hour: scans, most drifted, most undesired, deploys and repairs", History],
  ["renderHistory", "an hour with no scans", History],
  ["renderHistory", "where the audit trail starts, and the repair runs", History],
  // renderLinks: panels/Links.test.tsx
  ["renderLinks", "the links by group, and why some are left out", Links],
  // renderMetric / startMetrics: panels/Metrics.test.tsx, panels/RangePicker.test.tsx
  ["renderMetric", "each metrics view as lines or up/down strips", MetricPanel],
  ["renderMetric", "the latest value per series, and the server's note", MetricPanel],
  ["renderMetric", "a view unavailable, with its reason", MetricPanel],
  ["startMetrics", "the metrics range: 1 h, 24 h or 7 d", RangePicker],
];

// Functions that only arrange others, with no fact of their own.
const PLUMBING = new Set(["renderOverview"]);

function rendered(): string[] {
  const page = readFileSync(PLAIN_PAGE, "utf8");
  const names = [...page.matchAll(/^function (render\w+|showProblem|deviceRow|runningItem|startMetrics)\(/gm)];
  return names.map((match) => match[1]!).filter((name) => !PLUMBING.has(name));
}

describe("the designed page shows every fact the plain page shows", () => {
  it("has a row for every render function of the plain page", () => {
    const covered = new Set(FACTS.map(([plain]) => plain));
    const missing = rendered().filter((name) => !covered.has(name));

    expect(missing).toEqual([]);
  });

  it("names a designed component for every row", () => {
    const unnamed = FACTS.filter(([, , shownBy]) => typeof shownBy !== "function").map(([plain, fact]) => `${plain}: ${fact}`);

    expect(unnamed).toEqual([]);
  });

  it("lists no render function the plain page does not have", () => {
    const real = new Set(rendered());
    const stale = [...new Set(FACTS.map(([plain]) => plain))].filter((name) => !real.has(name));

    expect(stale).toEqual([]);
  });
});
