import { describe, expect, it } from "vitest";

import { contrast, cvdDistance } from "./color";

describe("contrast", () => {
  it("is 21 between black and white, and 1 for a colour with itself", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#fab219", "#fab219")).toBeCloseTo(1, 5);
  });

  it("does not depend on the order", () => {
    expect(contrast("#0ca30c", "#ffffff")).toBeCloseTo(contrast("#ffffff", "#0ca30c"), 10);
  });

  it("matches known WCAG ratios", () => {
    expect(contrast("#767676", "#ffffff")).toBeCloseTo(4.54, 1);
    expect(contrast("#d03b3b", "#ffffff")).toBeCloseTo(4.8, 0);
  });
});

describe("cvdDistance", () => {
  it("is zero for the same colour under every kind of vision", () => {
    for (const vision of ["normal", "protan", "deutan", "tritan"] as const) {
      expect(cvdDistance("#0ca30c", "#0ca30c", vision)).toBeCloseTo(0, 5);
    }
  });

  it("shows status green and red far apart in normal vision and close under red-green deficiency", () => {
    // The reason no state is told by colour alone (dev/skills/dashboard).
    expect(cvdDistance("#0ca30c", "#d03b3b", "normal")).toBeGreaterThan(15);
    expect(cvdDistance("#0ca30c", "#d03b3b", "deutan")).toBeLessThan(8);
  });
});
