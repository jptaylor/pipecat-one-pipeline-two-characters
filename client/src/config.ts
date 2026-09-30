import type { APIRequest } from "@pipecat-ai/client-js"

/**
 * The bot's `/start` endpoint: the Pipecat dev runner (`cd server && uv run bot.py`) serves it on
 * :7860. It answers with a session id (and ICE servers); the SmallWebRTC transport then posts its
 * offer beside it.
 */
const START_URL =
  import.meta.env.VITE_BOT_START_URL || "http://localhost:7860/start"

export const START_BOT: APIRequest = {
  endpoint: START_URL,
  requestData: { transport: "webrtc", enableDefaultIceServers: true },
}
