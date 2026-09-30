/** Jev's probabilities as text: option order, percentages and block-character meters. */

/** Options in a stable order: the characters, then "both", then "user". */
export function orderOptions(probabilities: Record<string, number>): string[] {
  const rank = (k: string) => (k === "both" ? 1 : k === "user" ? 2 : 0)
  return Object.keys(probabilities).sort((a, b) => rank(a) - rank(b))
}

export function percent(p: number | undefined): string {
  return `${Math.round((p ?? 0) * 100)}%`.padStart(4, " ")
}

const EIGHTHS = ["", "▏", "▎", "▍", "▌", "▋", "▊", "▉"]

/** A probability as a row of block characters: `cells` wide, to an eighth of a cell. */
export function meter(
  p: number,
  cells: number
): { filled: string; empty: string } {
  const eighths = Math.round(Math.min(Math.max(p, 0), 1) * cells * 8)
  const full = Math.floor(eighths / 8)
  const part = EIGHTHS[eighths % 8]
  const filled = "█".repeat(full) + part
  return {
    filled,
    empty: "░".repeat(Math.max(cells - full - (part ? 1 : 0), 0)),
  }
}
