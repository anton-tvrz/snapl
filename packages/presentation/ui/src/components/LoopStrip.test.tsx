import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RunSteps } from "../api/types";
import { STEPS_DEPLOY, STEPS_RECONCILE } from "../test/fixtures";
import { LoopStrip } from "./LoopStrip";

const deploy = STEPS_DEPLOY.steps.data!;
const reconcile = STEPS_RECONCILE.steps.data!;

// The arc has no failed deploy. This one is the fixture's with its apply
// failed, as the server reports one (research R15); T057 captures a real one.
const failed: RunSteps = {
  ...deploy,
  run: { ...deploy.run, state: "failed", reason: "gNMI Set rejected: invalid value for mtu" },
  steps: [deploy.steps[0]!, { ...deploy.steps[1]!, outcome: "failed", reason: "gNMI Set rejected: invalid value for mtu" }],
  blocks: deploy.blocks.map((block) => {
    if (block.block === "executor") return { ...block, status: "failed", reason: "gNMI Set rejected: invalid value for mtu" };
    if (block.block === "collector" || block.block === "observability") return { block: block.block, status: "not_reached" };
    return block;
  }),
};

const blocks = () => within(screen.getByRole("list", { name: "The loop, block by block" })).getAllByRole("listitem");
const block = (name: string) => blocks().find((item) => item.getAttribute("aria-label")?.startsWith(`${name}:`))!;

describe("LoopStrip", () => {
  it("shows the six blocks in loop order", () => {
    render(<LoopStrip steps={deploy} />);

    expect(blocks().map((item) => item.getAttribute("aria-label")?.split(":")[0])).toEqual([
      "Intent",
      "Executor",
      "Collector",
      "Observability",
      "Orchestrator",
      "Presentation",
    ]);
  });

  it("marks a successful deploy's four steps done with their times, and Presentation as you are here", () => {
    render(<LoopStrip steps={deploy} />);

    for (const name of ["Intent", "Executor", "Collector", "Observability", "Orchestrator"]) {
      expect(block(name)).toHaveTextContent("Done");
    }
    expect(block("Executor")).toHaveTextContent("21:20:16 UTC");
    expect(block("Presentation")).toHaveTextContent("You are here");
    expect(screen.getByText(/DeployIntent/)).toBeInTheDocument();
  });

  it("names the failed block with its reason, and the blocks after it not reached", () => {
    render(<LoopStrip steps={failed} />);

    expect(block("Executor")).toHaveTextContent("Failed");
    expect(block("Executor")).toHaveTextContent("gNMI Set rejected: invalid value for mtu");
    expect(block("Collector")).toHaveTextContent("Not reached");
    expect(block("Observability")).toHaveTextContent("Not reached");
    expect(block("Intent")).toHaveTextContent("Done");
  });

  it("says when a run type's steps are not recorded one by one, and gives the outcome", () => {
    render(<LoopStrip steps={reconcile} />);

    expect(screen.getByText(/ReconcileDevices steps are not recorded one by one/)).toBeInTheDocument();
    expect(screen.getByText(/Succeeded/)).toBeInTheDocument();
    expect(block("Executor")).toHaveTextContent("Not recorded");
  });

  it("never shows a block as in progress, even for a running run", () => {
    const running = { ...deploy, run: { ...deploy.run, state: "running" as const, ended_at: null } };
    render(<LoopStrip steps={running} />);

    expect(screen.getByRole("list", { name: "The loop, block by block" }).textContent).not.toMatch(
      /in progress|running/i,
    );
  });

  it("shows a block or status a newer server sent by its raw text", () => {
    const newer = {
      ...deploy,
      blocks: [...deploy.blocks, { block: "verifier", status: "skipped" }],
    } as unknown as RunSteps;
    render(<LoopStrip steps={newer} />);

    expect(block("verifier")).toHaveTextContent("skipped");
  });

  it("tells each status apart by its icon as well as its colour", () => {
    render(<LoopStrip steps={failed} />);
    const glyph = (name: string) => block(name).querySelector("[data-glyph]")!.textContent;

    expect(new Set([glyph("Intent"), glyph("Executor"), glyph("Collector"), glyph("Presentation")]).size).toBe(4);
  });
});
