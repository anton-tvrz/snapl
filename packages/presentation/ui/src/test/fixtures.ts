// API responses captured from the real app on the demo arc. A pytest
// (tests/unit/test_presentation/test_ui_fixtures.py) fails when one of these
// files no longer matches what the server answers.
import type {
  DevicesResponse,
  DriftTimelineResponse,
  HistoryResponse,
  LinksResponse,
  MetricsResponse,
  MetricsViewName,
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
import history from "./fixtures/history.json";
import links from "./fixtures/links.json";
import sources from "./fixtures/sources.json";
import stepsDeploy from "./fixtures/steps-deploy.json";
import stepsReconcile from "./fixtures/steps-reconcile.json";
import timelineAtScan2 from "./fixtures/timeline-at-scan-2.json";
import timeline from "./fixtures/timeline.json";
import modeArchive from "./fixtures/archive/mode.json";
import overviewArchive from "./fixtures/archive/overview.json";
import overviewUp from "./fixtures/up/overview.json";
import metricsWorkflowDuration from "./fixtures/up/metrics-workflow-duration.json";
import metricsGnmiDuration from "./fixtures/up/metrics-gnmi-duration.json";
import metricsGnmiErrors from "./fixtures/up/metrics-gnmi-errors.json";
import metricsWorkerLag from "./fixtures/up/metrics-worker-lag.json";
import metricsWorkerUp from "./fixtures/up/metrics-worker-up.json";
import metricsSourceHealth from "./fixtures/up/metrics-source-health.json";
import topology from "./fixtures/topology.json";

export const MODE = mode as unknown as ModeResponse;
export const SOURCES = sources as unknown as SourcesResponse;
export const LINKS = links as unknown as LinksResponse;
/** 010's history over the default range (the last 24 h), hourly. */
export const HISTORY = history as unknown as HistoryResponse;
export const OVERVIEW = overview as unknown as Overview;
/** The same arc with the other sources up: an active schedule, one worker, a scan running. */
export const OVERVIEW_UP = overviewUp as unknown as Overview;
/** The arc as a history archive opened by `snapl dashboard --archive`, its Prometheus snapshot missing. */
export const MODE_ARCHIVE = modeArchive as unknown as ModeResponse;
export const OVERVIEW_ARCHIVE = overviewArchive as unknown as Overview;
/** Each metrics view over the hour before NOW, from the server with Prometheus up. */
export const METRICS: Record<MetricsViewName, MetricsResponse> = {
  "workflow-duration": metricsWorkflowDuration as unknown as MetricsResponse,
  "gnmi-duration": metricsGnmiDuration as unknown as MetricsResponse,
  "gnmi-errors": metricsGnmiErrors as unknown as MetricsResponse,
  "worker-lag": metricsWorkerLag as unknown as MetricsResponse,
  "worker-up": metricsWorkerUp as unknown as MetricsResponse,
  "source-health": metricsSourceHealth as unknown as MetricsResponse,
};
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
