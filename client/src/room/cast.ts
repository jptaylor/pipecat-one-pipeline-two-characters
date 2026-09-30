import castJson from "@cast"

/** A character, from the repo-root characters.json the bot reads too. */
export interface Character {
  id: string
  name: string
  role: string
  tagline: string
  voiceName: string
  /** Their favourite colour's name, which they say when they introduce themselves. */
  colour: string
  /** CSS color: that favourite colour, their tint throughout the screen and their aura. */
  color: string
  /** Their aura's second tone, a lighter shade of the same colour. */
  accent: string
}

export const CAST: Character[] = castJson.map((c) => ({
  id: c.id,
  name: c.name,
  role: c.role,
  tagline: c.tagline,
  voiceName: c.voiceName,
  colour: c.colour,
  color: c.hex,
  accent: c.accent,
}))

export const BY_ID: Record<string, Character> = Object.fromEntries(
  CAST.map((c) => [c.id, c])
)

/** A speaker's or an option's name: a character, "user" or "group". */
export function labelOf(id: string | null | undefined): string {
  if (!id) return "—"
  if (id === "user") return "you"
  return BY_ID[id]?.name ?? id
}

/** A speaker's or an option's color: a character's own, and plain text for you and a group. */
export function colorOf(id: string | null | undefined): string {
  if (id && BY_ID[id]) return BY_ID[id].color
  if (id === "user" || id === "group") return "var(--foreground)"
  return "var(--muted-foreground)"
}

/** Who a group turn went to: "everyone", or the names in the order they answer. */
export function membersLabel(members: string[]): string {
  if (members.length === CAST.length) return "everyone"
  return members.map((m) => labelOf(m).toLowerCase()).join(", ")
}
