import { type Encoding, shapePoints, TONES } from "../../lib/encoding";

interface Props {
  encoding: Encoding;
  /** Radius, in the units of the SVG it is drawn in. */
  r: number;
}

/**
 * One state as a mark: its shape, drawn with an ink outline so it reads on
 * either surface, filled with its kind's colour, with its glyph inside.
 * Drawn around the origin: the caller positions it.
 */
export function StateMark({ encoding, r }: Props) {
  const tone = TONES[encoding.tone];
  const outline = { stroke: "var(--ink)", strokeWidth: 1.5 };
  const points = shapePoints(encoding.shape, r);

  return (
    <>
      {encoding.shape === "circle" && <circle r={r * 0.9} fill={tone.fill} {...outline} />}
      {encoding.shape === "ring" && (
        <circle r={r * 0.9} fill="var(--surface)" {...outline} strokeDasharray="3 3" />
      )}
      {encoding.shape === "pill" && (
        <rect x={-r} y={-r * 0.65} width={r * 2} height={r * 1.3} rx={r * 0.65} fill={tone.fill} {...outline} strokeDasharray="3 3" />
      )}
      {points.length > 0 && (
        <polygon points={points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ")} fill={tone.fill} strokeLinejoin="round" {...outline} />
      )}
      <text
        textAnchor="middle"
        dominantBaseline="central"
        fontSize={r * 0.95}
        fontWeight={700}
        fill={encoding.shape === "ring" ? "var(--ink)" : tone.onFill}
        // The triangle's centre of area sits below its centre of radius.
        y={encoding.shape === "triangle" ? r * 0.2 : 0}
        aria-hidden="true"
      >
        {encoding.glyph}
      </text>
    </>
  );
}
