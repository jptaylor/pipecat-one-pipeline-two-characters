/**
 * Wire shapes: the RTVI server messages the bot's director sends (`server/director.py`,
 * `server/room.py` `Reading.to_message`).
 */

/** "user" or a character id from characters.json. */
export type Speaker = string

/** One answer from Jev. */
export interface JevReading {
  type: "jev"
  /** preview: words still being spoken; route: a finished user turn; reply: a character's line
   * (does someone else at the table answer it?). */
  kind: "preview" | "route" | "reply"
  speaker: Speaker
  heard: string
  /** The option Jev chose: a character id, or "group". */
  choice: string | null
  /** The choice: each character id, and "group". */
  probabilities: Record<string, number>
  /** For each character: whether they're one of those asked (a group turn uses these). */
  included: Record<string, number>
  /** For each character: how likely they're being talked to, alone or in a group. */
  addressed: Record<string, number>
  /** Who answers, in order: the one chosen, or the group. */
  members: string[]
  /** A reply read: has the characters' exchange run its course? */
  closed: number | null
  /** A reply read: what came of it, the next speaker or "user" (the floor is yours)... */
  next?: string
  /** ...and why: "reply", or the floor is yours because Jev chose you ("user"), the one it
   * leant to was under 50% ("unsure"), the exchange ran its course ("closed"), or "error". */
  why?: "reply" | "user" | "unsure" | "closed" | "error"
  /** How long the bot waited for it: 0 when the frontrun had already cached it. */
  ms: number
  cached: boolean
  /** Exactly what Jev was shown. */
  state: Record<string, unknown>
  error: string | null
  /** With the recency prior applied: Jev's own probabilities, who was favoured, and by how much.
   * `probabilities` and `choice` are then the weighted ones. */
  raw: Record<string, number> | null
  favoured: string | null
  weight: number
  at: number
}

/** A character given the floor. */
export interface TurnMessage {
  type: "turn"
  speaker: Speaker
  reason: "addressed" | "group" | "welcome" | "reply" | "continue" | "fallback"
  note: string | null
  /** The character's view of the conversation, as its LLM was given it. */
  messages: { role: string; content: string }[]
  at: number
}

/** A line of the conversation, as it was said (cut short when interrupted). */
export interface LineMessage {
  type: "line"
  speaker: Speaker
  text: string
  interrupted: boolean
  at: number
}

/** Whose voice is playing: sent as a character's audio starts, and null when it stops. */
export interface SpeakerMessage {
  type: "speaker"
  speaker: Speaker | null
  at: number
}

export interface CastMessage {
  type: "cast"
  characters: { id: string; name: string; role: string }[]
  jev: string | null
}

export type ServerMessage =
  JevReading | TurnMessage | LineMessage | SpeakerMessage | CastMessage
