import { expect, type Page, test } from "@playwright/test";

// What only a real browser can show: that the page's own policy does not block
// it, and that nothing it loads comes from anywhere but this server (spec 011
// FR-005, SC-007).

interface Watch {
  /** Requests to anywhere but this server. */
  offOrigin: string[];
  /** Content-Security-Policy violations, and errors thrown by the page's script. */
  blocked: string[];
}

function watch(page: Page, origin: string): Watch {
  const seen: Watch = { offOrigin: [], blocked: [] };
  page.on("request", (request) => {
    const url = request.url();
    if (!url.startsWith(origin) && !url.startsWith("data:")) seen.offOrigin.push(url);
  });
  // The browser reports a policy violation as a console error naming the policy.
  // Other console errors are not this check's business: the plain page asks for
  // metrics from the browser's own clock, which the fixture server's fixed clock
  // refuses with a 422.
  page.on("console", (message) => {
    const text = message.text();
    if (message.type() === "error" && /Content Security Policy|Refused to/i.test(text)) seen.blocked.push(text);
  });
  page.on("pageerror", (error) => seen.blocked.push(error.message));
  return seen;
}

test("the designed UI loads under its policy and reaches nowhere else", async ({ page, baseURL }) => {
  const seen = watch(page, baseURL ?? "");

  const response = await page.goto("/");

  expect(response?.headers()["content-security-policy"]).toContain("default-src 'self'");
  await expect(page.getByRole("banner")).toContainText("dcfabric");
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
  await expect(page.getByText("Read-only")).toBeVisible();

  const sources = page.getByRole("list", { name: "Sources" }).getByRole("listitem");
  await expect(sources).toHaveCount(6);
  await expect(sources.filter({ hasText: "Audit trail" })).toContainText("up");
  await expect(sources.filter({ hasText: "Temporal" })).toContainText("down");
  await expect(sources.filter({ hasText: "Temporal" })).toContainText("uv run invoke dev.deps");

  // The stylesheet and the bundled font were allowed by the policy.
  const family = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
  expect(family).toContain("Inter");
  await page.evaluate(() => document.fonts.ready);

  expect(seen.offOrigin).toEqual([]);
  expect(seen.blocked).toEqual([]);
});

test("the theme choice restyles the page and survives a reload", async ({ page }) => {
  await page.goto("/");
  const background = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const light = await background();

  await page.getByRole("combobox", { name: "Theme" }).selectOption("dark");

  expect(await background()).not.toBe(light);
  await page.reload();
  await expect(page.getByRole("combobox", { name: "Theme" })).toHaveValue("dark");
});

test("the plain page still works at /plain, under its own policy", async ({ page, baseURL }) => {
  const seen = watch(page, baseURL ?? "");

  const response = await page.goto("/plain");

  expect(response?.headers()["content-security-policy"]).toContain("script-src 'unsafe-inline'");
  await expect(page.getByRole("heading", { name: "snapl dashboard" })).toBeVisible();
  // Its inline script ran and rendered the device table from the API.
  await expect(page.getByText("leaf-01").first()).toBeVisible();
  expect(seen.offOrigin).toEqual([]);
  expect(seen.blocked).toEqual([]);
});

test("the page links to the plain page", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("banner").getByRole("link", { name: "Plain page" }).click();

  await expect(page).toHaveURL(/\/plain$/);
});

test("a policy violation would be caught by this check", async ({ page }) => {
  // Proves the watch is not vacuous: an inline script is exactly what the UI's
  // policy forbids, and the browser must refuse it and say so.
  const seen = watch(page, "");
  await page.goto("/");

  await page.evaluate(() => {
    const script = document.createElement("script");
    script.textContent = "window.__injected = true";
    document.head.append(script);
  });

  expect(await page.evaluate(() => (window as unknown as { __injected?: boolean }).__injected)).toBeUndefined();
  expect(seen.blocked.length).toBeGreaterThan(0);
});
