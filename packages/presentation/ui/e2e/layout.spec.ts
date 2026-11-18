import { expect, test } from "@playwright/test";

import { waitLoaded } from "./loaded.mjs";

// SC-003 of spec 014: the first metrics chart starts within 2000 px of the top
// of the page. Measured as e2e/look.mjs measures it, on the look check's
// fixtures with a scan that recorded its steps, whose loop strip draws a tile
// per block and is the tallest the default page gets (#295). The server is the
// second one in playwright.config.ts.
const LOOK = "http://127.0.0.1:18933";
const LIMIT = 2000;

test.use({ viewport: { width: 1280, height: 900 }, colorScheme: "light" });

test("the first metrics chart starts within 2000 px of the top when a scan recorded its steps", async ({ page }) => {
  await page.goto(`${LOOK}/`);
  await waitLoaded(page);
  await expect(page.getByRole("list", { name: "The loop, block by block" })).toHaveAttribute("data-form", "tiles");

  const top = await page.evaluate(() => {
    const metrics = document.querySelector('section[aria-label="Metrics"]');
    const first = metrics?.querySelector("section, [role='img']");
    return first ? Math.round(first.getBoundingClientRect().top + window.scrollY) : null;
  });

  console.log(`first metrics chart top: ${top} px`);
  expect(top).not.toBeNull();
  expect(top).toBeLessThanOrEqual(LIMIT);
});
