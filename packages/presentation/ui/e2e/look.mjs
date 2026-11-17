// Not a test: the look check of spec 014 (FR-037). Renders the page so a
// person, or an unattended run, can look at it, and prints how long the page
// is and how far down the metrics start (SC-002, SC-003).
// Usage: node e2e/look.mjs <base url> <out dir>
// Serve fixtures with every source answering first:
//   uv run python dev/scripts/serve_dashboard_fixtures.py --port 18932 --sources up
import { mkdirSync } from "node:fs";

import { chromium } from "@playwright/test";

import { waitLoaded } from "./loaded.mjs";

const [base, out] = process.argv.slice(2);
if (!base || !out) {
  console.error("usage: node e2e/look.mjs <base url> <out dir>");
  process.exit(2);
}
mkdirSync(out, { recursive: true });

const WIDE = { width: 1280, height: 900 };
const NARROW = { width: 360, height: 800 };
const shots = [
  { file: "full-light", scheme: "light", viewport: WIDE, search: "", fullPage: true, measure: true },
  { file: "full-dark", scheme: "dark", viewport: WIDE, search: "", fullPage: true },
  { file: "first-screen-light", scheme: "light", viewport: WIDE, search: "", fullPage: false },
  { file: "first-screen-dark", scheme: "dark", viewport: WIDE, search: "", fullPage: false },
  { file: "device-light", scheme: "light", viewport: WIDE, search: "?device=leaf-01", fullPage: true },
  { file: "narrow-light", scheme: "light", viewport: NARROW, search: "", fullPage: true },
];

const browser = await chromium.launch();
let measured;
let narrowOverflow = null;
try {
  for (const shot of shots) {
    const page = await browser.newPage({ viewport: shot.viewport, colorScheme: shot.scheme });
    await page.goto(`${base}/${shot.search}`);
    await waitLoaded(page);
    if (shot.measure) {
      measured = await page.evaluate(() => {
        const metrics = document.querySelector('section[aria-label="Metrics"]');
        const first = metrics?.querySelector("section, [role='img']");
        return {
          pageHeight: document.documentElement.scrollHeight,
          firstMetricsChartTop: first ? Math.round(first.getBoundingClientRect().top + window.scrollY) : null,
          sidewaysScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        };
      });
    }
    if (shot.viewport === NARROW) {
      narrowOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    }
    await page.screenshot({ path: `${out}/${shot.file}.png`, fullPage: shot.fullPage });
    await page.close();
  }
} finally {
  await browser.close();
}
// narrowOverflow: how many pixels the page is wider than a 360 px window. It must be 0.
console.log(JSON.stringify({ viewport: WIDE, scheme: "light", ...measured, narrowOverflow }));
