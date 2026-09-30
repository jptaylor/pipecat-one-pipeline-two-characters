import { Panel } from "@/components/panel"
import { ConnectButton } from "@/components/pipecat/connect-button"
import { CAST } from "@/room/cast"

/** Before a session: who is in the room, and the button that connects. The button follows the
 * transport through connecting (click again to cancel) and offers a retry. */
export function ConnectScreen({
  onConnect,
  onDisconnect,
  error,
}: {
  onConnect: () => Promise<void>
  onDisconnect: () => Promise<void>
  error: string | null
}) {
  return (
    <main className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto py-8">
      <div className="my-auto w-full max-w-md space-y-7 px-2">
        <h2 className="text-center text-2xl font-medium tracking-tight">
          one pipeline, six voices.
        </h2>

        <Panel title="connect" footnote="jev · phonellm · cartesia">
          <div className="space-y-4 px-5 pt-6 pb-5">
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-muted-foreground">
              {CAST.map((c) => (
                <p key={c.id}>
                  <span style={{ color: c.color }}>
                    ● {c.name.toLowerCase()}
                  </span>
                  <span className="text-muted-foreground/60">
                    {" "}
                    · {c.role.toLowerCase()}
                  </span>
                </p>
              ))}
            </div>
            <p className="text-muted-foreground/70">
              They all listen, and each will tell you their favourite colour.
              Say hello and everyone answers; talk to one, or a few, and only
              they will.
            </p>
            <ConnectButton
              onConnect={() => void onConnect()}
              onDisconnect={() => void onDisconnect()}
              className="h-11 w-full tracking-wider uppercase"
            />
          </div>
        </Panel>

        <div
          className="min-h-12 text-center"
          aria-live="polite"
          aria-atomic="true"
        >
          {error && (
            <p role="alert" className="break-words text-inactive">
              {error}
            </p>
          )}
        </div>
      </div>
    </main>
  )
}
