// API responses captured from the real app on the demo arc. A pytest
// (tests/unit/test_presentation/test_ui_fixtures.py) fails when one of these
// files no longer matches what the server answers.
import type { DevicesResponse, DriftTimelineResponse, ModeResponse, Overview, SourcesResponse, TopologyResponse } from "../api/types";
import devicesAtScan2 from "./fixtures/devices-at-scan-2.json";
import devices from "./fixtures/devices.json";
import mode from "./fixtures/mode.json";
import overview from "./fixtures/overview.json";
import sources from "./fixtures/sources.json";
import timeline from "./fixtures/timeline.json";
import topology from "./fixtures/topology.json";

export const MODE = mode as unknown as ModeResponse;
export const SOURCES = sources as unknown as SourcesResponse;
export const OVERVIEW = overview as unknown as Overview;
export const TOPOLOGY = topology as unknown as TopologyResponse;
export const DEVICES = devices as unknown as DevicesResponse;
export const DEVICES_AT_SCAN_2 = devicesAtScan2 as unknown as DevicesResponse;
export const TIMELINE = timeline as unknown as DriftTimelineResponse;

/** The fixture server's fixed clock: just after the arc's last scan. */
export const NOW = new Date(OVERVIEW.generated_at);
