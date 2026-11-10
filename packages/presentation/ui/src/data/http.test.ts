import { describe, expect, it, vi } from "vitest";

import { ApiError, HttpProvider } from "./http";

function provider(body: unknown = {}, status = 200) {
  const fetchFn = vi.fn(async (_url: string, _init?: RequestInit) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }));
  return { http: new HttpProvider("", fetchFn as unknown as typeof fetch), fetchFn };
}

const requested = (fetchFn: ReturnType<typeof provider>["fetchFn"]) => fetchFn.mock.calls.map((call) => call[0]);

describe("HttpProvider", () => {
  it("asks the contract's route for each view", async () => {
    const { http, fetchFn } = provider();

    await http.mode();
    await http.overview();
    await http.sources();
    await http.links();
    await http.topology();
    await http.devices();
    await http.runs({});

    expect(requested(fetchFn)).toEqual([
      "/api/v1/mode",
      "/api/v1/overview",
      "/api/v1/sources",
      "/api/v1/links",
      "/api/v1/topology",
      "/api/v1/devices",
      "/api/v1/runs",
    ]);
  });

  it("sends a past time with its offset", async () => {
    const { http, fetchFn } = provider();
    const at = new Date("2026-11-07T21:20:16.500Z");

    await http.devices(at);
    await http.runs({ at, device: "leaf-01", limit: 20 });

    expect(requested(fetchFn)).toEqual([
      "/api/v1/devices?at=2026-11-07T21%3A20%3A16.500Z",
      "/api/v1/runs?at=2026-11-07T21%3A20%3A16.500Z&device=leaf-01&limit=20",
    ]);
  });

  it("leaves the timeline's range to the server when none is given", async () => {
    const { http, fetchFn } = provider();

    await http.timeline();

    expect(requested(fetchFn)).toEqual(["/api/v1/drift-timeline"]);
  });

  it("sends a range for the timeline and for metrics", async () => {
    const { http, fetchFn } = provider();
    const range = { start: new Date("2026-11-07T20:00:00Z"), end: new Date("2026-11-07T21:00:00Z") };

    await http.timeline(range);
    await http.metrics("gnmi-duration", range);

    expect(requested(fetchFn)).toEqual([
      "/api/v1/drift-timeline?start=2026-11-07T20%3A00%3A00.000Z&end=2026-11-07T21%3A00%3A00.000Z",
      "/api/v1/metrics/gnmi-duration?start=2026-11-07T20%3A00%3A00.000Z&end=2026-11-07T21%3A00%3A00.000Z",
    ]);
  });

  it("identifies a run by its workflow id and start time", async () => {
    const { http, fetchFn } = provider();

    await http.steps({ workflow_id: "deploy-intent-1 2", started_at: "2026-11-07T21:20:15.648265Z" });

    expect(requested(fetchFn)).toEqual([
      "/api/v1/runs/steps?workflow_id=deploy-intent-1+2&started_at=2026-11-07T21%3A20%3A15.648265Z",
    ]);
  });

  it("prefixes a base address", async () => {
    const fetchFn = vi.fn(async (_url: string) => ({ ok: true, status: 200, json: async () => ({}) }));
    await new HttpProvider("http://127.0.0.1:18030", fetchFn as unknown as typeof fetch).mode();

    expect(fetchFn.mock.calls[0]?.[0]).toBe("http://127.0.0.1:18030/api/v1/mode");
  });

  it("returns the parsed body", async () => {
    const { http } = provider({ mode: "live", now: "2026-11-07T21:00:05Z" });

    await expect(http.mode()).resolves.toEqual({ mode: "live", now: "2026-11-07T21:00:05Z" });
  });

  it("only reads: every request is a GET with no body", async () => {
    const { http, fetchFn } = provider();

    await http.overview();

    const init = fetchFn.mock.calls[0]?.[1];
    expect(init?.method ?? "GET").toBe("GET");
    expect(init?.body).toBeUndefined();
  });

  it("rejects a refused request with its status and the server's reason", async () => {
    const { http } = provider({ detail: "the range holds 30000 scan points; ask for a shorter range" }, 422);

    const failure = await http.timeline({ start: new Date(0), end: new Date(1) }).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(422);
    expect((failure as ApiError).message).toBe("the range holds 30000 scan points; ask for a shorter range");
  });

  it("describes a validation error the server sent as a list", async () => {
    const { http } = provider({ detail: [{ loc: ["query", "at"], msg: "Input should have timezone info" }] }, 422);

    const failure = (await http.devices(new Date()).catch((error: unknown) => error)) as ApiError;

    expect(failure.message).toBe("at: Input should have timezone info");
  });

  it("falls back to the status when the body is not JSON", async () => {
    const fetchFn = vi.fn(async () => ({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError("not json");
      },
    }));

    const failure = (await new HttpProvider("", fetchFn as unknown as typeof fetch)
      .mode()
      .catch((error: unknown) => error)) as ApiError;

    expect(failure.status).toBe(502);
    expect(failure.message).toBe("the dashboard server answered 502");
  });

  it("says the server is unreachable when the request never lands", async () => {
    const fetchFn = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });

    const failure = (await new HttpProvider("", fetchFn as unknown as typeof fetch)
      .mode()
      .catch((error: unknown) => error)) as ApiError;

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.status).toBe(0);
    expect(failure.message).toBe("the dashboard server did not answer");
  });
});
