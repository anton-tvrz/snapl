// Colour arithmetic for checking the encoding, not for drawing. The numbers
// follow the dataviz skill's validator: WCAG 2 contrast, and distance in OKLab
// (×100) after simulating a colour-vision deficiency (Machado et al., 2009).

type Rgb = [number, number, number];
export type Vision = "normal" | "protan" | "deutan" | "tritan";

const CVD: Record<Exclude<Vision, "normal">, [Rgb, Rgb, Rgb]> = {
  protan: [
    [0.152286, 1.052583, -0.204868],
    [0.114503, 0.786281, 0.099216],
    [-0.003882, -0.048116, 1.051998],
  ],
  deutan: [
    [0.367322, 0.860646, -0.227968],
    [0.280085, 0.672501, 0.047413],
    [-0.01182, 0.04294, 0.968881],
  ],
  tritan: [
    [1.255528, -0.076749, -0.178779],
    [-0.078411, 0.930809, 0.147602],
    [0.004733, 0.691367, 0.3039],
  ],
};

function linear(hex: string): Rgb {
  const value = hex.replace(/^#/, "");
  const channel = (index: number) => {
    const c = parseInt(value.slice(index, index + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return [channel(0), channel(2), channel(4)];
}

/** WCAG 2 contrast ratio, from 1 to 21. */
export function contrast(a: string, b: string): number {
  const luminance = (hex: string) => {
    const [r, g, b] = linear(hex);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

function oklab([r, g, b]: Rgb): Rgb {
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

function seenBy(rgb: Rgb, vision: Vision): Rgb {
  if (vision === "normal") return rgb;
  const clamp = (value: number) => Math.min(1, Math.max(0, value));
  return CVD[vision].map((row) => clamp(row[0] * rgb[0] + row[1] * rgb[1] + row[2] * rgb[2])) as Rgb;
}

/** How far apart two colours look under a kind of vision: OKLab distance ×100. */
export function cvdDistance(a: string, b: string, vision: Vision): number {
  const [la, aa, ba] = oklab(seenBy(linear(a), vision));
  const [lb, ab, bb] = oklab(seenBy(linear(b), vision));
  return 100 * Math.hypot(la - lb, aa - ab, ba - bb);
}
