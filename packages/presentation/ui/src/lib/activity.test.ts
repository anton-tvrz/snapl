import { describe, expect, it } from "vitest";

import { activityFor, RECENT_SECONDS } from "./activity";

const NOW = "2026-11-07T21:20:20Z";
const DEVICES = new Set(["spine-01", "leaf-01"]);
const secondsBefore = (seconds: number) => new Date(new Date(NOW).getTime() - seconds * 1000).toISOString();

function run(overrides: Record<string, unknown>) {
  return {
    workflow_id: "deploy-intent-x",
    workflow_type: "DeployIntent",
    target: "leaf-01",
    started_at: secondsBefore(30),
    ended_at: null,
    state: "running",
    trigger: "operator",
    web_ui_url: "http://localhost:18034/x",
    ...overrides,
  };
}

const activity = (running: unknown[], runs: unknown[]) =>
  activityFor({ running, runs, now: NOW, devices: DEVICES } as Parameters<typeof activityFor>[0]);

describe("activityFor", () => {
  it("marks a device with a workflow running against it as active", () => {
    const result = activity([run({})], []);

    expect(result.devices.get("leaf-01")).toMatchObject({ kind: "active", workflowType: "DeployIntent" });
    expect(result.devices.has("spine-01")).toBe(false);
    expect(result.fabric).toBeNull();
  });

  it("marks a device whose run ended inside the window as recently active, with the outcome", () => {
    const ended = run({ state: "failed", ended_at: secondsBefore(9) });

    expect(activity([], [ended]).devices.get("leaf-01")).toMatchObject({ kind: "recent", outcome: "failed" });
  });

  it("uses a ten-second window: two refreshes", () => {
    expect(RECENT_SECONDS).toBe(10);
    const inside = run({ state: "succeeded", ended_at: secondsBefore(10) });
    const outside = run({ state: "succeeded", ended_at: secondsBefore(11) });

    expect(activity([], [inside]).devices.size).toBe(1);
    expect(activity([], [outside]).devices.size).toBe(0);
  });

  it("catches a deploy that started and ended between two refreshes (SC-002)", () => {
    // The captured deploys took about a second: never seen running by a 5 s poll.
    const quick = run({ state: "succeeded", started_at: secondsBefore(3), ended_at: secondsBefore(2) });

    expect(activity([], [quick]).devices.get("leaf-01")?.kind).toBe("recent");
  });

  it("prefers what is running now over what just ended", () => {
    const ended = run({ state: "succeeded", ended_at: secondsBefore(2) });

    expect(activity([run({})], [ended]).devices.get("leaf-01")?.kind).toBe("active");
  });

  it("keeps the latest of several recent runs", () => {
    const older = run({ state: "failed", ended_at: secondsBefore(8) });
    const newer = run({ state: "succeeded", ended_at: secondsBefore(2) });

    expect(activity([], [older, newer]).devices.get("leaf-01")?.outcome).toBe("succeeded");
    expect(activity([], [newer, older]).devices.get("leaf-01")?.outcome).toBe("succeeded");
  });

  it("puts a fabric-wide run on the fabric, not on every device", () => {
    const scan = run({ workflow_type: "ScanDrift", target: "dcfabric" });
    const reconcile = run({ workflow_type: "ReconcileDevices", target: null });

    for (const fabricWide of [scan, reconcile]) {
      const result = activity([fabricWide], []);
      expect(result.devices.size).toBe(0);
      expect(result.fabric).toMatchObject({ kind: "active", workflowType: fabricWide.workflow_type });
    }
  });

  it("still shows a run whose end is slightly ahead of the server clock", () => {
    const ahead = run({ state: "succeeded", ended_at: secondsBefore(-5) });

    expect(activity([], [ahead]).devices.get("leaf-01")?.kind).toBe("recent");
  });

  it("is empty when there is nothing to show, or the views have no data", () => {
    expect(activity([], [])).toEqual({ devices: new Map(), fabric: null });
    expect(activityFor({ running: undefined, runs: null, now: NOW, devices: DEVICES })).toEqual({
      devices: new Map(),
      fabric: null,
    });
  });
});
