import { usePipecatClientMediaTrack } from "@pipecat-ai/client-react"
import { useEffect, useRef, useState } from "react"

import { Panel } from "@/components/panel"
import { AudioVisualizerWaveView } from "@/components/pipecat/audio-visualizer-wave"
import { Meter } from "@/components/room/odds"
import { cn } from "@/lib/utils"
import type { Character } from "@/room/cast"
import { percent } from "@/room/odds"
import { characterState, useRoom } from "@/room/store"

/**
 * Retro dither, as in the Deepgram demo: levels well above the default keep the quantisation
 * from knocking the tint off-hue, and alphaLevels 2 hard-stipples the edge so the orb reads as a
 * field of dots. Hoisted: it is compiled into the shader.
 */
const DITHER = { levels: 12, alphaLevels: 2 } as const

const STATUS = {
  speaking: { label: "speaking", tone: "text-active" },
  thinking: { label: "thinking", tone: "text-tool" },
  listening: { label: "listening", tone: "text-muted-foreground/60" },
}

/** The side of the largest square that fits the element. */
function useSquare<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [side, setSide] = useState(280)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setSide(Math.max(120, Math.floor(Math.min(width, height))))
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [ref, side] as const
}

/**
 * One character: their aura, and how sure Jev is that you're talking to them.
 *
 * There is one bot audio track for both voices, so it is routed here: only the character the
 * bot's last `speaker` message named gets the track, and the other's aura rests. While a
 * character has the floor but hasn't been heard yet, their aura pulses (thinking).
 */
export function CharacterPanel({ character }: { character: Character }) {
  const state = useRoom((s) => characterState(s, character.id))
  const reading = useRoom(
    (s) => s.preview ?? s.readings.findLast((r) => r.kind === "route") ?? null
  )
  const botTrack = usePipecatClientMediaTrack("audio", "bot")
  const [box, side] = useSquare<HTMLDivElement>()
  // Talking to them alone, or to both of them.
  const p = reading
    ? (reading.probabilities[character.id] ?? 0) +
      (reading.probabilities["both"] ?? 0)
    : null
  const status = STATUS[state]
  const onFloor = state !== "listening"

  return (
    <Panel
      title={`${character.name.toLowerCase()} · ${character.role.toLowerCase()}`}
      status={
        <span className="text-muted-foreground">
          <span className={cn("mr-1.5", status.tone)}>●</span>
          {status.label}
        </span>
      }
      className="flex min-h-0 flex-col transition-colors duration-500"
      style={{ borderColor: onFloor ? character.color : undefined }}
    >
      <div ref={box} className="relative min-h-0 flex-1 overflow-hidden">
        <div
          className="absolute inset-0 flex items-center justify-center transition-opacity duration-500"
          style={{ opacity: onFloor ? 1 : 0.4 }}
        >
          <AudioVisualizerWaveView
            track={state === "speaking" ? botTrack : null}
            isThinking={state === "thinking"}
            size={Math.min(side, 420)}
            color={character.aura.color}
            accentColor={character.aura.accent}
            colorShift={0.4}
            noHighlight
            amplitude={0.5}
            fill={0.85}
            hollow={0.2}
            core={0.3}
            density={onFloor ? 0.42 : 0.32}
            glow={onFloor ? 0.85 : 0.55}
            dither={DITHER}
          />
        </div>
      </div>
      <div className="flex shrink-0 items-center justify-center gap-2 px-4 pb-4 whitespace-pre">
        {/* Green while it reads words you're still speaking. */}
        <span
          className={reading?.kind === "preview" ? "text-active" : "text-agent"}
        >
          jev
        </span>
        <span className="text-muted-foreground">
          ❯ to {character.name.toLowerCase()}
        </span>
        <Meter p={p ?? 0} color={character.color} cells={16} />
        <span className="text-foreground tabular-nums">
          {p === null ? "   —" : percent(p)}
        </span>
      </div>
    </Panel>
  )
}
