// Waits until the page has its data: the fabric and the lanes are drawn and no
// panel still says "Loading…" (spec 014 research R6). A panel that never
// loads fails the capture, naming itself, so a half-loaded page is never
// mistaken for the page.
const LOADING = /^Loading/;

export async function waitLoaded(page, timeout = 30_000) {
  await page.getByRole("group", { name: "Fabric topology" }).waitFor({ timeout });
  await page.getByRole("list", { name: "Drift lanes" }).waitFor({ timeout });
  try {
    await page.waitForFunction(
      (source) => {
        const loading = new RegExp(source);
        return ![...document.querySelectorAll("p")].some((p) => loading.test(p.textContent ?? ""));
      },
      LOADING.source,
      { timeout },
    );
  } catch {
    const stuck = await page.evaluate((source) => {
      const loading = new RegExp(source);
      return [...document.querySelectorAll("p")]
        .filter((p) => loading.test(p.textContent ?? ""))
        .map((p) => p.closest("section")?.querySelector("h2")?.textContent ?? p.closest("section")?.getAttribute("aria-label") ?? "an unnamed panel");
    }, LOADING.source);
    throw new Error(`still loading after ${timeout / 1000} s: ${stuck.join(", ")}`);
  }
}
