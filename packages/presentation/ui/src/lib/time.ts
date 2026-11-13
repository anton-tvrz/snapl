/**
 * How long ago `iso` was, relative to `now`. `now` is always passed in: an
 * archive's "now" is when it was made, not the wall clock (spec 011 FR-037).
 */
export function ageText(iso: string | null | undefined, now: Date): string {
  if (!iso) return "never";
  const seconds = Math.floor((now.getTime() - new Date(iso).getTime()) / 1000);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

/** A duration in words: "47 s", "3 min 12 s", "1 h 25 min", "2 d 4 h". */
export function durationText(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const parts = (big: number, bigUnit: string, small: number, smallUnit: string) =>
    small > 0 ? `${big} ${bigUnit} ${small} ${smallUnit}` : `${big} ${bigUnit}`;
  if (s < 60) return `${s} s`;
  if (s < 3600) return parts(Math.floor(s / 60), "min", s % 60, "s");
  if (s < 86400) return parts(Math.floor(s / 3600), "h", Math.floor((s % 3600) / 60), "min");
  return parts(Math.floor(s / 86400), "d", Math.floor((s % 86400) / 3600), "h");
}

/** The date and time in UTC, to the second: "2026-11-07 21:20:15 UTC". */
export function utcText(t: Date): string {
  return `${t.toISOString().slice(0, 19).replace("T", " ")} UTC`;
}

/** The time of day in a zone, the viewer's own by default: "22:20:15 CET". */
export function localText(t: Date, zone?: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: zone,
    timeZoneName: "short",
  }).format(t);
}
