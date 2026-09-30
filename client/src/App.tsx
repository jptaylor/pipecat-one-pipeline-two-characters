import type { TransportState } from "@pipecat-ai/client-js"
import {
  PipecatClientProvider,
  usePipecatClientTransportState,
} from "@pipecat-ai/client-react"
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport"
import { PhoneOffIcon } from "lucide-react"

import pipecatLogo from "@/assets/pipecat-logo.svg"
import { BotAudioOutput } from "@/components/pipecat/bot-audio"
import { CharacterPanel } from "@/components/room/character-panel"
import { ConnectScreen } from "@/components/room/connect-screen"
import { ConversationPanel } from "@/components/room/conversation-panel"
import { DebugPanel } from "@/components/room/debug-panel"
import { START_BOT } from "@/config"
import { usePipecatApp } from "@/hooks/use-pipecat-app"
import { cn } from "@/lib/utils"
import { CAST } from "@/room/cast"
import { RoomSync } from "@/room/room-sync"

const SESSION_STATUS: Record<string, { label: string; tone: string }> = {
  idle: { label: "session idle", tone: "text-muted-foreground" },
  starting: { label: "session starting", tone: "text-tool" },
  live: { label: "session live", tone: "text-active" },
  error: { label: "session error", tone: "text-inactive" },
}

function phaseOf(state: TransportState, error: string | null) {
  if (error || state === "error") return "error"
  if (state === "ready") return "live"
  if (state === "disconnected" || state === "initialized") return "idle"
  return "starting"
}

function Session({
  onConnect,
  onDisconnect,
  error,
}: {
  onConnect: () => Promise<void>
  onDisconnect: () => Promise<void>
  error: string | null
}) {
  const transportState = usePipecatClientTransportState() as TransportState
  const session = SESSION_STATUS[phaseOf(transportState, error)]
  const live = transportState === "ready"

  return (
    <div className="flex h-svh flex-col gap-4 p-4 text-[13px] leading-[1.6]">
      <header className="flex h-9 shrink-0 items-center justify-between">
        <h1 className="flex items-center gap-2.5 text-base">
          <img src={pipecatLogo} alt="Pipecat" className="h-[21px] w-auto" />
          <span className="ml-1.5 text-muted-foreground/60">/</span>
          <span className="font-medium text-agent">two voices</span>
        </h1>
        {live && (
          <button
            type="button"
            onClick={() => void onDisconnect()}
            className="flex items-center gap-2 border border-inactive/60 px-4 py-2 text-[13px] leading-none tracking-wider text-inactive uppercase transition-colors hover:border-inactive hover:bg-inactive/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-inactive"
          >
            <PhoneOffIcon className="size-3.5" aria-hidden="true" />
            Hang up
          </button>
        )}
      </header>

      {!live && (
        <ConnectScreen
          onConnect={onConnect}
          onDisconnect={onDisconnect}
          error={error}
        />
      )}
      {/* Mounted while connecting too, so the welcome's first messages aren't missed. */}
      <main
        className={cn(
          "grid min-h-0 flex-1 grid-cols-2 grid-rows-[minmax(0,1.1fr)_minmax(0,1fr)] gap-4",
          !live && "hidden"
        )}
      >
        {CAST.map((c) => (
          <CharacterPanel key={c.id} character={c} />
        ))}
        <ConversationPanel />
        <DebugPanel />
      </main>

      <footer className="flex h-6 shrink-0 items-center text-[13px] font-medium">
        <span className={session.tone}>
          <span className="mr-1.5">▸▸</span>
          {session.label}
        </span>
      </footer>

      <BotAudioOutput />
      <RoomSync />
    </div>
  )
}

/** Built once: changing these would rebuild the client. */
const OPTIONS = {
  transportFactory: () => new SmallWebRTCTransport(),
  startBotParams: START_BOT,
}

export default function App() {
  const { client, connect, disconnect, error } = usePipecatApp(OPTIONS)

  if (!client) {
    return (
      <div className="flex h-svh items-center justify-center text-[13px] text-muted-foreground">
        {error ?? "starting client…"}
      </div>
    )
  }

  return (
    <PipecatClientProvider client={client}>
      <Session onConnect={connect} onDisconnect={disconnect} error={error} />
    </PipecatClientProvider>
  )
}
