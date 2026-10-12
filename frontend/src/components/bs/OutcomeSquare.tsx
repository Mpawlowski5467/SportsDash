export type OutcomeLetter = "W" | "L" | "D";

const OUTCOME_NAME: Record<OutcomeLetter, string> = {
  W: "Win",
  L: "Loss",
  D: "Draw",
};

interface Props {
  result: OutcomeLetter;
  /** Edge in px at the default scale: 24 lists, 26 form strips, 30 tables, 34 the form header. */
  size?: 24 | 26 | 30 | 34;
  /**
   * Accessible name. Defaults to "Win" / "Loss" / "Draw"; pass null when a
   * parent already names the whole sequence (a form strip).
   */
  label?: string | null;
  className?: string;
}

/**
 * A result, told by fill rather than colour: a win is a solid block, a loss
 * a 2px outline, and a draw a mid-tone fill between the two.
 */
export default function OutcomeSquare({
  result,
  size = 26,
  label,
  className,
}: Props) {
  const classes = ["bs-wl", `bs-wl--${result.toLowerCase()}`];
  if (size !== 26) classes.push(`bs-wl--${size}`);
  if (className) classes.push(className);
  const name = label === undefined ? OUTCOME_NAME[result] : label;
  return (
    <span
      className={classes.join(" ")}
      {...(name === null
        ? { "aria-hidden": true }
        : { role: "img", "aria-label": name })}
    >
      {result}
    </span>
  );
}

/** A run of results as one image with one spoken sequence. */
export function FormStrip({
  results,
  size = 26,
  label,
  className,
}: {
  results: readonly OutcomeLetter[];
  size?: 24 | 26 | 30 | 34;
  /** Lead-in for the spoken sequence, e.g. "Athletics, last five". */
  label: string;
  className?: string;
}) {
  const spoken = results
    .map((result) => OUTCOME_NAME[result].toLowerCase())
    .join(", ");
  return (
    <span
      role="img"
      aria-label={`${label}: ${spoken}`}
      className={"bs-form" + (className ? ` ${className}` : "")}
    >
      {results.map((result, index) => (
        <OutcomeSquare key={index} result={result} size={size} label={null} />
      ))}
    </span>
  );
}
