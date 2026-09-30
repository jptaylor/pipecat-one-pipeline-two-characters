import { create } from "zustand"

import type {
  JevReading,
  LineMessage,
  ServerMessage,
  Speaker,
  TurnMessage,
} from "@/room/types"

const MAX_READINGS = 300

export interface Line extends LineMessage {
  id: number
  /** Jev's reading of a user line: who it was said to. */
  route?: JevReading
  /** Jev's reading of a character's line: does someone at the table answer it? */
  reply?: JevReading
}

interface RoomState {
  lines: Line[]
  /** Every Jev reading, oldest first. */
  readings: JevReading[]
  turns: TurnMessage[]
  /** Jev's latest read of the words the user is still speaking. */
  preview: JevReading | null
  /** The character who has the floor (RTVI `turn`), and whether they've been heard since. */
  active: Speaker | null
  heardSinceTurn: boolean
  /** The character whose voice is playing now (RTVI `speaker`): their aura gets the audio. */
  voice: Speaker | null
  userSpeaking: boolean
  /** What the user has said so far this turn (final segments and the live partial). */
  heardFinals: string[]
  heardInterim: string
  jevModel: string | null
  receive: (message: ServerMessage) => void
  setUserSpeaking: (speaking: boolean) => void
  hear: (text: string, final: boolean) => void
  reset: () => void
}

let nextId = 1

const initial = {
  lines: [] as Line[],
  readings: [] as JevReading[],
  turns: [] as TurnMessage[],
  preview: null,
  active: null,
  heardSinceTurn: false,
  voice: null,
  userSpeaking: false,
  heardFinals: [] as string[],
  heardInterim: "",
  jevModel: null,
}

/** The index of the last line matching `test`, or -1. */
function lastIndex(lines: Line[], test: (line: Line) => boolean): number {
  for (let i = lines.length - 1; i >= 0; i--) if (test(lines[i])) return i
  return -1
}

export const useRoom = create<RoomState>()((set) => ({
  ...initial,

  receive: (message) =>
    set((s) => {
      switch (message.type) {
        case "cast":
          return { jevModel: message.jev }
        case "speaker":
          return {
            voice: message.speaker,
            heardSinceTurn: s.heardSinceTurn || message.speaker === s.active,
          }
        case "turn":
          return {
            turns: [...s.turns, message],
            active: message.speaker,
            heardSinceTurn: false,
          }
        case "line": {
          const line: Line = { ...message, id: nextId++ }
          const heard =
            message.speaker === "user"
              ? { heardFinals: [], heardInterim: "" }
              : {}
          return { lines: [...s.lines, line], ...heard }
        }
        case "jev": {
          const readings = [...s.readings, message].slice(-MAX_READINGS)
          if (message.kind === "preview") return { readings, preview: message }
          // A route belongs to the user line just before it, a reply to its speaker's line.
          const lines = [...s.lines]
          const i = lastIndex(lines, (l) => l.speaker === message.speaker)
          if (i >= 0)
            lines[i] = {
              ...lines[i],
              [message.kind === "reply" ? "reply" : "route"]: message,
            }
          return {
            readings,
            lines,
            preview: message.kind === "route" ? null : s.preview,
          }
        }
      }
    }),

  setUserSpeaking: (speaking) => set({ userSpeaking: speaking }),

  hear: (text, final) =>
    set((s) =>
      final
        ? { heardFinals: [...s.heardFinals, text], heardInterim: "" }
        : { heardInterim: text }
    ),

  reset: () => set({ ...initial }),
}))

/** What the user has said so far this turn. */
export function heardSoFar(s: Pick<RoomState, "heardFinals" | "heardInterim">) {
  return [...s.heardFinals, s.heardInterim].join(" ").trim()
}

/** What a character is doing: speaking (their voice is playing), thinking (they have the floor
 * but haven't been heard yet), or listening. */
export function characterState(
  s: Pick<RoomState, "active" | "voice" | "heardSinceTurn">,
  id: string
): "speaking" | "thinking" | "listening" {
  if (s.voice === id) return "speaking"
  if (s.active === id && !s.heardSinceTurn) return "thinking"
  return "listening"
}
