import { RTVIEvent } from "@pipecat-ai/client-js"
import { useRTVIClientEvent } from "@pipecat-ai/client-react"
import { useCallback } from "react"

import { useRoom } from "@/room/store"
import type { ServerMessage } from "@/room/types"

const TYPES = new Set(["jev", "turn", "line", "speaker", "cast"])

/** RTVI → the room's store: the director's messages (`speaker` says whose voice is playing),
 * and the user's speech. */
export function RoomSync() {
  useRTVIClientEvent(
    RTVIEvent.ServerMessage,
    useCallback((data: unknown) => {
      const message = data as ServerMessage
      if (message && TYPES.has(message.type))
        useRoom.getState().receive(message)
    }, [])
  )
  useRTVIClientEvent(
    RTVIEvent.UserTranscript,
    useCallback((data: { text: string; final: boolean }) => {
      if (data.text.trim()) useRoom.getState().hear(data.text, data.final)
    }, [])
  )
  useRTVIClientEvent(
    RTVIEvent.UserStartedSpeaking,
    useCallback(() => useRoom.getState().setUserSpeaking(true), [])
  )
  useRTVIClientEvent(
    RTVIEvent.UserStoppedSpeaking,
    useCallback(() => useRoom.getState().setUserSpeaking(false), [])
  )
  useRTVIClientEvent(
    RTVIEvent.Connected,
    useCallback(() => useRoom.getState().reset(), [])
  )
  return null
}
