// API responses captured from the real app on the demo arc. A pytest
// (tests/unit/test_presentation/test_ui_fixtures.py) fails when one of these
// files no longer matches what the server answers.
import type {
  DevicesResponse,
  DriftTimelineResponse,
  ModeResponse,
  Overview,
  RunStepsResponse,
  RunsResponse,
  SourcesResponse,
  TopologyResponse,
} from "../api/types";
import devicesAtScan1 from "./fixtures/devices-at-scan-1.json";
import devicesAtScan2 from "./fixtures/devices-at-scan-2.json";
import devicesAtScan3 from "./fixtures/devices-at-scan-3.json";
import devices from "./fixtures/devices.json";
import mode from "./fixtures/mode.json";
import overview from "./fixtures/overview.json";
import runsAtScan2 from "./fixtures/runs-at-scan-2.json";
import sources from "./fixtures/sources.json";
import stepsDeploy from "./fixtures/steps-deploy.json";
import stepsReconcile from "./fixtures/steps-reconcile.json";
import timelineAtScan2 from "./fixtures/timeline-at-scan-2.json";
import timeline from "./fixtures/timeline.json";
import topology from "./fixtures/topology.json";

export const MODE = mode as unknown as ModeResponse;
export const SOURCES = sources as unknown as SourcesResponse;
export const OVERVIEW = overview as unknown as Overview;
export const TOPOLOGY = topology as unknown as TopologyResponse;
export const DEVICES = devices as unknown as DevicesResponse;
export const DEVICES_AT_SCAN_1 = devicesAtScan1 as unknown as DevicesResponse;
export const DEVICES_AT_SCAN_2 = devicesAtScan2 as unknown as DevicesResponse;
export const DEVICES_AT_SCAN_3 = devicesAtScan3 as unknown as DevicesResponse;
export const RUNS_AT_SCAN_2 = runsAtScan2 as unknown as RunsResponse;
export const TIMELINE = timeline as unknown as DriftTimelineResponse;
/** The reconcile's deploy of spine-01, every step recorded. */
export const STEPS_DEPLOY = stepsDeploy as unknown as RunStepsResponse;
/** The reconcile itself: its steps are not recorded one by one. */
export const STEPS_RECONCILE = stepsReconcile as unknown as RunStepsResponse;
export const TIMELINE_AT_SCAN_2 = timelineAtScan2 as unknown as DriftTimelineResponse;

/** The "as of" stops of the arc's three scans, as the fixtures above were asked for. */
export const SCAN_STOPS = ["2026-11-07T21:20:13.666Z", "2026-11-07T21:20:15.330Z", "2026-11-07T21:20:18.853Z"];

/** The fixture server's fixed clock: just after the arc's last scan. */
export const NOW = new Date(OVERVIEW.generated_at);
