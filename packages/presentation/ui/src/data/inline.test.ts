import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DevicesResponse, RunsResponse } from "../api/types";
import { HEALTH, SNAPSHOT_BUNDLE, TIMELINE_AT_SCAN_2 } from "../test/fixtures";
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

  it("keeps a device's health at a past time only when it has not changed since (spec 016)", async () => {
    // spine-01 has been unhealthy since 20:20:48, leaf-01 healthy since 19:50:48.
    const health = (devices: DevicesResponse, name: string) => devices.devices.data!.find((d) => d.name === name)!.health;
    const now = await provider.devices();
    const later = await provider.devices(new Date("2026-11-07T21:00:00Z"));
    const earlier = await provider.devices(new Date("2026-11-07T20:00:00Z"));

    expect(health(later, "spine-01")).toEqual(health(now, "spine-01"));
    expect(health(earlier, "spine-01")).toBeNull();
    expect(health(earlier, "leaf-01")).toEqual(health(now, "leaf-01"));
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

  it("gives the bundled series of spec 015 when they are named, and 010's otherwise", async () => {
    expect(await provider.history(undefined, undefined, ["in_sync_devices", "runs"])).toBe(bundle.fabric_history);
    expect(await provider.history(undefined, undefined, ["deploys"])).toBe(bundle.history);
  });

  it("gives the bundled drift summary for the bundled range, and no other range", async () => {
    expect(await provider.driftSummary()).toBe(bundle.drift_summary);
    const same = { start: new Date(bundle.manifest.start), end: new Date(bundle.manifest.end) };
    expect(await provider.driftSummary(same)).toBe(bundle.drift_summary);

    const other = await provider.driftSummary({ start: new Date("2026-11-07T20:00:00Z"), end: new Date("2026-11-07T21:00:00Z") });
    expect(other.summary).toMatchObject({ source: "audit", available: false, data: null });
    expect(other.summary.reason).toMatch(/^a snapshot holds only its own range/);
  });

  it("says a snapshot made before spec 015 does not hold the drift summary or the new series", async () => {
    const older = new InlineProvider({ ...bundle, drift_summary: undefined, fabric_history: undefined });

    expect((await older.driftSummary()).summary).toMatchObject({ available: false, reason: "not in this snapshot" });
    expect((await older.history(undefined, undefined, ["runs"])).series).toEqual([]);
  });

  it("gives the bundled health for the bundled range, and says an older snapshot does not hold it (spec 016)", async () => {
    const withHealth = new InlineProvider({ ...bundle, health: HEALTH });
    expect(await withHealth.health()).toBe(HEALTH);

    const other = await withHealth.health({ start: new Date("2026-11-07T20:00:00Z"), end: new Date("2026-11-07T21:00:00Z") });
    expect(other.health).toMatchObject({ source: "audit", available: false, data: null });
    expect(other.health.reason).toMatch(/^a snapshot holds only its own range/);

    const older = new InlineProvider({ ...bundle, health: undefined });
    expect((await older.health()).health).toMatchObject({ available: false, reason: "not in this snapshot" });
  });

  // Spec 016 PR F: the network's figures at the snapshot's end.
  it("gives the bundled network, and says an older snapshot does not hold it", async () => {
    const network = await provider.network();
    expect(network.network.available).toBe(true);
    expect(network.network.data!.at).toBe(bundle.manifest.end);
    expect(network.network.data!.devices.map((d) => d.name)).toContain("spine-01");

    const older = new InlineProvider({ ...bundle, network: undefined });
    expect((await older.network()).network).toMatchObject({
      source: "prometheus",
      available: false,
      reason: "not in this snapshot",
      data: null,
    });
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

/** Text gzipped and base64-encoded, as `snapl dashboard --snapshot` packs a bundle (spec 018 US5). */
async function packed(text: string): Promise<string> {
  const stream = new Response(new TextEncoder().encode(text)).body!.pipeThrough(new CompressionStream("gzip"));
  const bytes = new Uint8Array(await new Response(stream).arrayBuffer());
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/** The page's bundle element, holding `text`, packed when `encoding` is given. */
function holding(text: string, encoding?: string) {
  document.body.replaceChildren();
  // Typed as data, as in the template: an untyped script would be run as code.
  const element = document.createElement("script");
  element.id = "snapl-bundle";
  element.type = "application/json";
  if (encoding) element.dataset.encoding = encoding;
  element.textContent = text;
  document.body.append(element);
}

describe("readBundle", () => {
  afterEach(() => {
    document.body.replaceChildren();
    vi.unstubAllGlobals();
  });

  it("reads a packed bundle: gzipped and base64-encoded (spec 018 US5)", async () => {
    holding(await packed(JSON.stringify(bundle)), "gzip+base64");

    await expect(readBundle(document)).resolves.toEqual(bundle);
  });

  it("still reads a plain bundle, as a snapshot written before spec 018 holds", async () => {
    holding(JSON.stringify(bundle));

    await expect(readBundle(document)).resolves.toEqual(bundle);
  });

  it("refuses a packed bundle it cannot unpack, with a message", async () => {
    holding("not base64 at all!", "gzip+base64");
    await expect(readBundle(document)).rejects.toThrow("This snapshot's data is damaged and can't be unpacked.");

    holding(btoa("valid base64, but not gzip"), "gzip+base64");
    await expect(readBundle(document)).rejects.toThrow("This snapshot's data is damaged and can't be unpacked.");

    holding(await packed("{ not json"), "gzip+base64");
    await expect(readBundle(document)).rejects.toThrow("This snapshot's data is damaged and can't be unpacked.");
  });

  it("refuses a bundle packed in a way it does not know", async () => {
    holding("abc", "brotli+base64");

    await expect(readBundle(document)).rejects.toThrow(
      "This snapshot's data is packed as brotli+base64; this page reads gzip+base64.",
    );
  });

  it("says the browser is too old when it cannot unpack gzip", async () => {
    holding(await packed(JSON.stringify(bundle)), "gzip+base64");
    vi.stubGlobal("DecompressionStream", undefined);

    await expect(readBundle(document)).rejects.toThrow(
      "This snapshot needs a newer browser: this one can't unpack its data.",
    );
  });

  it("is nothing when the element is absent or empty, as on the served page", async () => {
    document.body.replaceChildren();
    await expect(readBundle(document)).resolves.toBeNull();

    holding("");
    await expect(readBundle(document)).resolves.toBeNull();
  });

  it("refuses a bundle in a format it does not know", () => {
    expect(() => new InlineProvider({ ...bundle, manifest: { ...bundle.manifest, format: "snapl-snapshot/2" } } as SnapshotBundle)).toThrow(
      "This snapshot is in format snapl-snapshot/2; this page reads snapl-snapshot/1.",
    );
  });

  it("picks the provider: the bundle when the page holds one, the server otherwise", async () => {
    document.body.replaceChildren();
    expect(await providerFor(document)).toBeInstanceOf(HttpProvider);

    holding(await packed(JSON.stringify(bundle)), "gzip+base64");
    expect(await providerFor(document)).toBeInstanceOf(InlineProvider);
  });
});
