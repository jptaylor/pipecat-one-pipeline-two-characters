import castJson from "@cast"

/** A character, from the repo-root characters.json the bot reads too. */
export interface Character {
  id: string
  name: string
  role: string
  tagline: string
  voiceName: string
  /** CSS color: the character's tint throughout the screen. */
  color: string
  /** CSS variable names for their aura: its color and its second tone. */
  aura: { color: string; accent: string }
}

const COLORS = ["var(--cast-1)", "var(--cast-2)"]

export const CAST: Character[] = castJson.map((c, i) => ({
  id: c.id,
  name: c.name,
  role: c.role,
  tagline: c.tagline,
  voiceName: c.voiceName,
  color: COLORS[i],
  aura: { color: `--cast-${i + 1}`, accent: `--cast-${i + 1}-accent` },
}))

export const BY_ID: Record<string, Character> = Object.fromEntries(
  CAST.map((c) => [c.id, c])
)

/** A speaker's or an option's name: a character, "user" or "both". */
export function labelOf(id: string | null | undefined): string {
  if (!id) return "—"
  if (id === "user") return "you"
  if (id === "both") return "both"
  return BY_ID[id]?.name ?? id
}

/** A speaker's or an option's color: a character's tint, the user's blue, both in plain text. */
export function colorOf(id: string | null | undefined): string {
  if (id && BY_ID[id]) return BY_ID[id].color
  if (id === "user") return "var(--client)"
  if (id === "both") return "var(--foreground)"
  return "var(--muted-foreground)"
}
