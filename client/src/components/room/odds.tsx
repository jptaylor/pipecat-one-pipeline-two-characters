import { colorOf, labelOf } from "@/room/cast"
import { meter, orderOptions, percent } from "@/room/odds"
import { cn } from "@/lib/utils"

export function Meter({
  p,
  color,
  cells = 20,
  dim = false,
}: {
  p: number
  color: string
  cells?: number
  dim?: boolean
}) {
  const { filled, empty } = meter(p, cells)
  return (
    <span className="whitespace-pre" aria-hidden>
      <span style={{ color, opacity: dim ? 0.55 : 1 }}>{filled}</span>
      <span className="text-muted-foreground/30">{empty}</span>
    </span>
  )
}

/** One row per option: its name, a meter, and the number; the chosen one bright. */
export function OddsBars({
  probabilities,
  choice,
  chosen = [],
  cells = 20,
  className,
}: {
  probabilities: Record<string, number>
  choice?: string | null
  /** Options to highlight besides `choice` (a group's members). */
  chosen?: string[]
  cells?: number
  className?: string
}) {
  return (
    <div className={cn("grid", className)}>
      {orderOptions(probabilities).map((option) => {
        const p = probabilities[option] ?? 0
        const picked = option === choice || chosen.includes(option)
        return (
          <div key={option} className="flex items-center gap-2 whitespace-pre">
            <span
              className={picked ? "text-foreground" : "text-muted-foreground"}
            >
              {picked ? "❯ " : "  "}
              {labelOf(option).toLowerCase().padEnd(5, " ")}
            </span>
            <Meter p={p} color={colorOf(option)} cells={cells} dim={!picked} />
            <span
              className={cn(
                "tabular-nums",
                picked ? "text-foreground" : "text-muted-foreground"
              )}
            >
              {percent(p)}
            </span>
          </div>
        )
      })}
    </div>
  )
}
