// Not a test: renders the page in light and dark, with a device open, so a
// person can look at it. Usage: node e2e/screenshot.mjs <base url> <out dir>
import { chromium } from "@playwright/test";

const [base, out] = process.argv.slice(2);
const browser = await chromium.launch();
for (const scheme of ["light", "dark"]) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, colorScheme: scheme });
  await page.goto(`${base}/?device=leaf-01`);
  await page.getByRole("group", { name: "Fabric topology" }).waitFor();
  await page.screenshot({ path: `${out}/dashboard-${scheme}.png`, fullPage: true });
  await page.close();
}
await browser.close();
