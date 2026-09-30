import { useEffect, useRef } from "react"

import { Panel } from "@/components/panel"
import { TextInput } from "@/components/pipecat/text-input"
import { UserAudioControl } from "@/components/pipecat/user-audio-control"
import { colorOf, labelOf } from "@/room/cast"
import { percent } from "@/room/odds"
import { heardSoFar, type Line, useRoom } from "@/room/store"
import type { JevReading } from "@/room/types"

/** Jev's call on a line of yours: who it went to, how sure, and how long the bot waited. */
function Verdict({ reading }: { reading: JevReading }) {
  const choice = reading.choice ?? ""
  return (
    <span className="whitespace-pre text-muted-foreground">
      <span className="text-agent">jev</span> ❯{" "}
      <span style={{ color: colorOf(choice) }}>
        {labelOf(choice).toLowerCase()}
      </span>{" "}
      <span className="text-foreground tabular-nums">
        {percent(reading.probabilities[choice])}
      </span>
      <span className="text-muted-foreground/60">
        {" · "}
        {reading.error
          ? "error"
          : reading.cached
            ? "frontrun"
            : `${reading.ms}ms`}
      </span>
    </span>
  )
}

function Row({ line }: { line: Line }) {
  const user = line.speaker === "user"
  return (
    <li className="grid gap-0.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <span style={{ color: colorOf(line.speaker) }}>
          {labelOf(line.speaker).toLowerCase()}
        </span>
        {line.route && <Verdict reading={line.route} />}
      </div>
      <p className={user ? "text-client" : "text-foreground"}>
        {user && "❯ "}
        {line.text}
        {line.interrupted && <span className="text-inactive"> ⌁ cut off</span>}
      </p>
    </li>
  )
}

/** What you're saying right now, while Jev reads along. */
function Live() {
  const heard = useRoom(heardSoFar)
  const speaking = useRoom((s) => s.userSpeaking)
  const preview = useRoom((s) => s.preview)
  if (!heard && !speaking) return null
  return (
    <li className="grid gap-0.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <span className="text-client">you</span>
        {preview && <Verdict reading={preview} />}
      </div>
      <p className="text-client">
        ❯ {heard}
        <span className="ml-0.5 animate-terminal-caret">▌</span>
      </p>
    </li>
  )
}

/** The one conversation both characters hear, and how you add to it. */
export function ConversationPanel() {
  const lines = useRoom((s) => s.lines)
  const heard = useRoom(heardSoFar)
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" })
  }, [lines, heard])

  return (
    <Panel title="conversation" className="flex min-h-0 flex-col">
      <div className="flex min-h-0 flex-1 flex-col px-4 pt-5 pb-3">
        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          <ol className="grid gap-3">
            {lines.map((line) => (
              <Row key={line.id} line={line} />
            ))}
            <Live />
          </ol>
          {lines.length === 0 && !heard && (
            <p className="text-muted-foreground/70">
              ▌ say hello, ask one of them something, or say “not you, the other
              one”
            </p>
          )}
          <div ref={end} />
        </div>
        <div className="mt-3 flex items-center gap-2 border-t border-border pt-2.5">
          <TextInput
            className="flex-1"
            noInject
            placeholder="type to the room…"
          />
          <UserAudioControl />
        </div>
      </div>
    </Panel>
  )
}
