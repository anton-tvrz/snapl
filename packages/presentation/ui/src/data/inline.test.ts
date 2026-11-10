import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { DevicesResponse, RunsResponse } from "../api/types";
import { SNAPSHOT_BUNDLE, TIMELINE_AT_SCAN_2 } from "../test/fixtures";
import { HttpProvider } from "./http";
import { InlineProvider, providerFor, readBundle, type SnapshotBundle } from "./inline";

// The server's past state around every scan of the demo arc, pinned by
// tests/unit/test_presentation/test_dashboard_at.py (spec 011 T080).
const DEVICES_AT = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "..", "..", "..", "..", "tests", "unit", "test_presentation", "fixtures", "devices_at.json"), "utf8"),
) as { at: { at: string; devices: DevicesResponse; runs: RunsResponse }[] };

const bundle = SNAPSHOT_BUNDLE as SnapshotBundle;
const provider = new InlineProvider(bundle);

describe("InlineProvider at a past time", () => {
  // SC-004: the snapshot computes in the browser what the server answers.
  it.each(DEVICES_AT.at.map((entry) => [entry.at, entry] as const))("gives the server's devices at %s", async (at, entry) => {
    const mine = (await provider.devices(new Date(at))).devices.data!;
    const theirs = entry.devices.devices.data!;
    const pick = (d: (typeof theirs)[number]) => ({
      name: d.name,
      drift_state: d.drift_state,
      missing_paths: d.missing_paths ?? [],
      undesired_paths: d.undesired_paths ?? [],
      unclassified_paths: d.unclassified_paths ?? [],
      error: d.error ?? null,
      last_scanned_at: d.last_scanned_at ?? null,
      last_deploy: d.last_deploy ? { workflow_id: d.last_deploy.workflow_id, success: d.last_deploy.success, ended_at: d.last_deploy.ended_at } : null,
    });

    expect(mine.map(pick).sort((a, b) => a.name.localeCompare(b.name))).toEqual(
      theirs.map(pick).sort((a, b) => a.name.localeCompare(b.name)),
    );
  });

  it.each(DEVICES_AT.at.map((entry) => [entry.at, entry] as const))("gives the server's run states at %s", async (at, entry) => {
    const key = (r: { workflow_id: string; started_at: string; state: string }) => `${r.workflow_id}@${r.started_at}=${r.state}`;
    const mine = (await provider.runs({ at: new Date(at) })).runs.data!;

    expect(mine.map(key).sort()).toEqual(entry.runs.runs.data!.map(key).sort());
  });

  it("gives the headline's timeline as of a past time", async () => {
    const past = TIMELINE_AT_SCAN_2.timeline.data!;
    const mine = (await provider.timeline({ start: new Date(bundle.manifest.start), end: new Date(past.end) })).timeline.data!;

    expect(mine.latest_closed).toBeNull();
    expect(mine.open_count).toBe(past.open_count);
    expect(mine.episodes.map((e) => [e.name, e.closed_at ?? null])).toEqual(past.episodes.map((e) => [e.name, e.closed_at ?? null]));
  });
});

describe("InlineProvider at the bundle's time", () => {
  it("answers each view with the bundled response", async () => {
    expect(await provider.overview()).toBe(bundle.overview);
    expect(await provider.devices()).toBe(bundle.devices);
    expect(await provider.runs({})).toBe(bundle.runs);
    expect(await provider.topology()).toBe(bundle.topology);
    expect(await provider.timeline()).toBe(bundle.timeline);
    expect(await provider.history()).toBe(bundle.history);
    expect(await provider.links()).toBe(bundle.links);
    expect(await provider.sources()).toBe(bundle.sources);
  });

  it("gives a run's bundled steps, and no record for a run it does not hold", async () => {
    const [key, steps] = Object.entries(bundle.steps)[0]!;
    const at = key.lastIndexOf("@");
    expect(await provider.steps({ workflow_id: key.slice(0, at), started_at: key.slice(at + 1) })).toBe(steps);

    const none = await provider.steps({ workflow_id: "deploy-intent-x", started_at: "2026-01-01T00:00:00Z" });
    expect(none.steps).toMatchObject({ available: true, data: null });
  });

  it("gives the bundled metrics for the bundled range, and no other range", async () => {
    expect(await provider.metrics("worker-up")).toBe(bundle.metrics["worker-up"]);
    const other = await provider.metrics("worker-up", { start: new Date("2026-11-07T20:00:00Z"), end: new Date("2026-11-07T21:00:00Z") });
    expect(other.metrics).toMatchObject({ available: false });
    expect(other.metrics.reason).toMatch(/^a snapshot holds only its own range/);
  });

  it("is a snapshot whose now is the bundle's end", async () => {
    const mode = await provider.mode();

    expect(mode.mode).toBe("snapshot");
    expect(mode.now).toBe(bundle.manifest.end);
    expect(mode.archive).toMatchObject({
      created_at: bundle.manifest.created_at,
      snapl_commit: "6baff39",
      metrics_start: bundle.manifest.start,
      metrics_end: bundle.manifest.end,
    });
  });
});

describe("readBundle", () => {
  it("reads the bundle from its element", () => {
    document.body.replaceChildren();
    const element = document.createElement("script");
    element.id = "snapl-bundle";
    element.type = "application/json";
    element.textContent = JSON.stringify(bundle);
    document.body.append(element);

    expect(readBundle(document)).toEqual(bundle);
  });

  it("is nothing when the element is absent or empty, as on the served page", () => {
    document.body.replaceChildren();
    expect(readBundle(document)).toBeNull();

    const element = document.createElement("script");
    element.id = "snapl-bundle";
    document.body.append(element);
    expect(readBundle(document)).toBeNull();
  });

  it("refuses a bundle in a format it does not know", () => {
    expect(() => new InlineProvider({ ...bundle, manifest: { ...bundle.manifest, format: "snapl-snapshot/2" } } as SnapshotBundle)).toThrow(
      "This snapshot is in format snapl-snapshot/2; this page reads snapl-snapshot/1.",
    );
  });

  it("picks the provider: the bundle when the page holds one, the server otherwise", () => {
    document.body.replaceChildren();
    expect(providerFor(document)).toBeInstanceOf(HttpProvider);

    const element = document.createElement("script");
    element.id = "snapl-bundle";
    element.textContent = JSON.stringify(bundle);
    document.body.append(element);
    expect(providerFor(document)).toBeInstanceOf(InlineProvider);
  });
});
