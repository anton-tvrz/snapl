import { describe, expect, it } from "vitest";

import type { DeviceView } from "../api/types";
import { DEVICES } from "../test/fixtures";
import { healthContext, healthMark, healthTitle } from "./health";

const devices = DEVICES.devices.data!;
const spine = devices.find((d) => d.name === "spine-01")!;

function device(name: string, state: string | null, inIntent = true): DeviceView {
  return {
    device_id: `00000000-0000-0000-0000-${name.padStart(12, "0")}`,
    name,
    role: "leaf",
    in_intent: inIntent,
    drift_state: "in_sync",
    health: state === null ? null : { state, since: "2026-11-07T20:00:00Z", reasons: [] },
  } as DeviceView;
}

describe("healthMark", () => {
  it("draws nothing for a healthy device", () => {
    expect(healthMark("healthy")).toEqual({ glyph: null, label: "Healthy", tone: "ok" });
  });

  it("draws a red ! for an unhealthy device", () => {
    expect(healthMark("unhealthy")).toEqual({ glyph: "!", label: "Unhealthy", tone: "fail" });
  });

  it("draws a grey ? when the health is unknown", () => {
    expect(healthMark("unknown")).toEqual({ glyph: "?", label: "Health unknown", tone: "unknown" });
  });

  it("shows a state a newer server sends by its raw text, as unknown", () => {
    expect(healthMark("degraded")).toEqual({ glyph: "?", label: "degraded", tone: "unknown" });
  });
});

describe("healthTitle", () => {
  it("names the state and each reason", () => {
    expect(healthTitle(spine.health!)).toBe(
      "Unhealthy: BGP 10.10.1.1 active (expected established); BGP 10.10.1.3 active (expected established); " +
        "BGP 10.10.1.5 active (expected established); BGP 10.10.1.7 active (expected established)",
    );
  });

  it("is the state alone when there is no reason", () => {
    expect(healthTitle({ state: "healthy", since: "2026-11-07T20:00:00Z", reasons: [] })).toBe("Healthy");
  });
});

describe("healthContext", () => {
  it("counts the unhealthy devices", () => {
    expect(healthContext(devices)).toBe("2 unhealthy");
  });

  it("says all healthy when every device in intent is", () => {
    expect(healthContext([device("1", "healthy"), device("2", "healthy"), device("3", "unhealthy", false)])).toBe("all healthy");
  });

  it("counts unknown health apart from unhealthy", () => {
    expect(healthContext([device("1", "unhealthy"), device("2", "unknown"), device("3", "unknown")])).toBe(
      "1 unhealthy, 2 health unknown",
    );
  });

  it("says nothing when no device has a recorded health", () => {
    expect(healthContext([device("1", null), device("2", null)])).toBeNull();
  });

  it("does not call a fabric all healthy while a device has no recorded health", () => {
    expect(healthContext([device("1", "healthy"), device("2", null)])).toBeNull();
  });

  it("leaves out devices no longer in intent", () => {
    expect(healthContext([device("1", "healthy"), device("2", "unhealthy", false)])).toBe("all healthy");
  });
});
