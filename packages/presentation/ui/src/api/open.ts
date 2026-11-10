// The contract marks some enumerations as open: a newer server may send a
// value this UI has never seen, and the UI must show it by its raw text
// instead of hiding it (010 contract "Versioning", spec 011 FR-008).

/** A known value, or any other string a newer server sends. */
export type Open<T extends string> = T | (string & {});

/** The table's entry for `value`, or one built from its raw text. */
export function lookup<V>(table: Readonly<Record<string, V>>, value: string, unknown: (raw: string) => V): V {
  const known = Object.hasOwn(table, value) ? table[value] : undefined;
  return known === undefined ? unknown(value) : known;
}

export const SOURCE_LABELS = {
  audit: "Audit trail",
  temporal: "Temporal",
  sot: "Source of Truth",
  ci: "CI",
  lab_runs: "Lab runs",
  prometheus: "Prometheus",
} as const;

export type SourceName = Open<keyof typeof SOURCE_LABELS>;

export function sourceLabel(source: SourceName): string {
  return lookup<string>(SOURCE_LABELS, source, (raw) => raw);
}
