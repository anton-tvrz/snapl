import type { Page } from "@playwright/test";

/** Waits until the page has its data (see loaded.mjs). */
export function waitLoaded(page: Page, timeout?: number): Promise<void>;
