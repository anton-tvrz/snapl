import { execFileSync } from "node:child_process";
import { mkdtempSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { expect, test } from "@playwright/test";

// A snapshot opened from disk with the network off (spec 011 US6, SC-009):
// the page works, and it makes no request at all.

const ROOT = join(import.meta.dirname, "..", "..", "..", "..");
let file: string;

test.beforeAll(() => {
  file = join(mkdtempSync(join(tmpdir(), "snapl-snapshot-")), "demo.html");
  execFileSync("uv", ["run", "python", join(ROOT, "dev", "scripts", "write_fixture_snapshot.py"), file], {
    cwd: ROOT,
    stdio: "inherit",
  });
});

test("a snapshot opened from disk offline shows the loop, replays the drift and asks nothing", async ({ browser }) => {
  const size = statSync(file).size;
  console.log(`snapshot size: ${(size / 1000).toFixed(0)} kB`);
  expect(size).toBeLessThan(3_000_000);

  const context = await browser.newContext();
  await context.setOffline(true);
  const page = await context.newPage();
  const requests: string[] = [];
  const problems: string[] = [];
  page.on("request", (request) => {
    if (!request.url().startsWith("file:")) requests.push(request.url());
  });
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(message.text());
  });
  page.on("pageerror", (error) => problems.push(error.message));

  await page.goto(pathToFileURL(file).href);

  await expect(page.getByRole("region", { name: "Recording" })).toContainText("A recording made 2026-11-07");
  await expect(page.getByRole("group", { name: "Fabric topology" })).toBeVisible();
  const spine = page.getByRole("button", { name: /^spine-01,/ });
  await expect(spine).toHaveAccessibleName("spine-01, spine, In sync");
  await expect(page.getByRole("list", { name: "The loop, block by block" })).toBeVisible();

  const slider = page.getByRole("slider", { name: "As of" });
  await slider.focus();
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowLeft");
  await expect(spine).toHaveAccessibleName("spine-01, spine, Missing intent");
  await expect(page.getByRole("region", { name: "Past time" })).toContainText("2026-11-07 21:20:15 UTC");

  await page.getByRole("button", { name: /^spine-01,/ }).click();
  await page.getByRole("button", { name: "Show the loop for DeployIntent started 2026-11-07 21:18:56 UTC" }).click();
  await expect(page.getByRole("list", { name: "The loop, block by block" }).getByRole("listitem").filter({ hasText: "Executor" })).toContainText("Done");

  expect(requests).toEqual([]);
  expect(problems).toEqual([]);
  await context.close();
});
