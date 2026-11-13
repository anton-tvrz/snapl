import { afterEach, describe, expect, it, vi } from "vitest";

import { parseUrlState, urlSearch, writeUrlState } from "./urlState";

afterEach(() => {
  vi.restoreAllMocks();
  history.replaceState(null, "", "/");
});

describe("parseUrlState", () => {
  it("reads the selected device, the past time, the range and the run", () => {
    const state = parseUrlState(
      "?device=leaf-01&at=2026-11-07T21%3A20%3A16.000Z&from=2026-11-07T20%3A00%3A00.000Z&to=2026-11-07T22%3A00%3A00.000Z" +
        "&run=deploy-intent-1%402026-11-07T21%3A20%3A15.648265Z",
    );

    expect(state.device).toBe("leaf-01");
    expect(state.at?.toISOString()).toBe("2026-11-07T21:20:16.000Z");
    expect(state.from?.toISOString()).toBe("2026-11-07T20:00:00.000Z");
    expect(state.to?.toISOString()).toBe("2026-11-07T22:00:00.000Z");
    expect(state.run).toEqual({ workflow_id: "deploy-intent-1", started_at: "2026-11-07T21:20:15.648265Z" });
  });

  it("is empty for an empty address", () => {
    expect(parseUrlState("")).toEqual({});
  });

  it("ignores a value it cannot read, and keeps the rest", () => {
    const state = parseUrlState("?device=leaf-01&at=yesterday&from=&run=no-start-time");

    expect(state).toEqual({ device: "leaf-01" });
  });

  it("keeps a workflow id that itself contains an at sign", () => {
    expect(parseUrlState("?run=a%40b%402026-11-07T21%3A20%3A15Z").run).toEqual({
      workflow_id: "a@b",
      started_at: "2026-11-07T21:20:15Z",
    });
  });
});

describe("urlSearch", () => {
  it("writes what parseUrlState reads back", () => {
    const state = {
      device: "leaf-01",
      at: new Date("2026-11-07T21:20:16Z"),
      run: { workflow_id: "deploy-intent-1", started_at: "2026-11-07T21:20:15.648265Z" },
    };

    const parsed = parseUrlState(urlSearch(state));

    expect(parsed.device).toBe("leaf-01");
    expect(parsed.at?.getTime()).toBe(state.at.getTime());
    expect(parsed.run).toEqual(state.run);
  });

  it("is empty when nothing is selected", () => {
    expect(urlSearch({})).toBe("");
  });
});

describe("writeUrlState", () => {
  it("replaces the address without adding to the back button's history", () => {
    const replace = vi.spyOn(history, "replaceState");
    const push = vi.spyOn(history, "pushState");

    writeUrlState({ device: "spine-01" });

    expect(location.search).toBe("?device=spine-01");
    expect(replace).toHaveBeenCalledTimes(1);
    expect(push).not.toHaveBeenCalled();
  });

  it("clears the address when nothing is selected, keeping the path", () => {
    history.replaceState(null, "", "/?device=spine-01");

    writeUrlState({});

    expect(location.pathname).toBe("/");
    expect(location.search).toBe("");
  });

  it("does not touch the address when nothing changed", () => {
    history.replaceState(null, "", "/?device=spine-01");
    const replace = vi.spyOn(history, "replaceState");

    writeUrlState({ device: "spine-01" });

    expect(replace).not.toHaveBeenCalled();
  });
});
