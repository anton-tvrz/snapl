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
