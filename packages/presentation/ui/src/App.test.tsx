import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { ApiError } from "./data/http";
import type { DataProvider } from "./data/provider";

function provider(overrides: Partial<DataProvider> = {}): DataProvider {
  const unused = async () => {
    throw new Error("not asked in this slice");
  };
  return {
    mode: async () => ({
      contract_version: "1",
      use_case: "dcfabric",
      generated_at: "2026-11-07T21:00:05Z",
      mode: "live",
      now: "2026-11-07T21:00:05Z",
      archive: null,
    }),
    sources: async () => ({
      contract_version: "1",
      use_case: "dcfabric",
      generated_at: "2026-11-07T21:00:05Z",
      sources: [
        { source: "audit", available: true, fetched_at: "2026-11-07T21:00:04Z" },
        { source: "temporal", available: false, reason: "connection refused", remedy: "uv run invoke dev.deps" },
      ],
    }),
    overview: unused,
    links: unused,
    topology: unused,
    devices: unused,
    runs: unused,
    timeline: unused,
    steps: unused,
    metrics: unused,
    ...overrides,
  } as DataProvider;
}

describe("App", () => {
  it("shows the use case, the mode and each source's state", async () => {
    render(<App provider={provider()} />);

    expect(await screen.findByText("dcfabric")).toBeInTheDocument();
    expect(await screen.findByText("Live")).toBeInTheDocument();
    expect(await screen.findByText("Audit trail")).toBeInTheDocument();
    expect(screen.getByText("connection refused")).toBeInTheDocument();
    expect(screen.getByText("uv run invoke dev.deps")).toBeInTheDocument();
  });

  it("says the server did not answer, and how to start it", async () => {
    const down = async () => {
      throw new ApiError(0, "the dashboard server did not answer");
    };
    render(<App provider={provider({ mode: down, sources: down })} />);

    expect(await screen.findByText("the dashboard server did not answer")).toBeInTheDocument();
    expect(screen.getByText("uv run snapl dashboard")).toBeInTheDocument();
  });

  it("reads the mode once and the sources on a timer", async () => {
    const mode = vi.fn(provider().mode);
    const sources = vi.fn(provider().sources);
    render(<App provider={provider({ mode, sources })} />);

    await screen.findByText("Audit trail");

    expect(mode).toHaveBeenCalledTimes(1);
    expect(sources).toHaveBeenCalledTimes(1);
  });
});
