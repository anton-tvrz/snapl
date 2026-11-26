import { afterEach, describe, expect, it, vi } from "vitest";

import { applyTheme, readTheme, storeTheme } from "./theme";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe("theme", () => {
  it("follows the system until one is chosen", () => {
    expect(readTheme()).toBe("system");
  });

  it("remembers a choice", () => {
    storeTheme("dark");
    expect(readTheme()).toBe("dark");
  });

  it("ignores a stored value it does not know", () => {
    localStorage.setItem("snapl.theme", "sepia");
    expect(readTheme()).toBe("system");
  });

  it("works when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });

    expect(readTheme()).toBe("system");
    expect(() => storeTheme("light")).not.toThrow();
  });

  it("marks the document for a chosen theme and clears the mark for system", () => {
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    applyTheme("system");
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });
});
