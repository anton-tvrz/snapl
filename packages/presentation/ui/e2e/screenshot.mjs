// Not a test: renders the page in light and dark, with a device open, at now
// and at a past time, so a person can look at it.
// Usage: node e2e/screenshot.mjs <base url> <out dir>
import { chromium } from "@playwright/test";

const [base, out] = process.argv.slice(2);
const browser = await chromium.launch();
const views = {
  now: "?device=leaf-01",
  // Between scans 2 and 3 of the demo arc: spine-01 still drifted.
  past: "?device=spine-01&at=2026-11-07T21:20:15.330Z",
  // The reconcile's deploy of spine-01 as the loop.
  loop: "?device=spine-01&run=deploy-intent-18e29d6d-e0c5-0ba7-32d1-c510fc3ef077%402026-11-07T21%3A20%3A15.648265Z",
};
for (const scheme of ["light", "dark"]) {
  for (const [name, search] of Object.entries(views)) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, colorScheme: scheme });
    await page.goto(`${base}/${search}`);
    await page.getByRole("group", { name: "Fabric topology" }).waitFor();
    await page.getByRole("list", { name: "Drift lanes" }).waitFor();
    await page.screenshot({ path: `${out}/dashboard-${name}-${scheme}.png`, fullPage: true });
    await page.close();
  }
}
await browser.close();
