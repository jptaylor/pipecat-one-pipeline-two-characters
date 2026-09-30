# Two voices, one room

One conversation with two characters: Maya, a botanist, and Theo, a chef. Each has their own
Cartesia voice and both hear everything. Jev (TypeSafe's classifier, through Pipecat Classifiers)
reads each thing you say, while you're still saying it, and decides who you're talking to. Then
that character answers, in their voice, with the whole conversation in front of them. Say hello and
they both answer in turn. Talk to one and only they will. "Not you, the other one" sends it across,
and "and why?" stays with whoever you were talking to.

Pipecat 1.12 workers, PhoneLLM (temperature 0), Deepgram, Cartesia, Jev. The client is
Vite + React + Pipecat UI (shadcn registry), in the Deepgram demo's terminal style.

## Run

Settings go in the repo-root `.env` (`.env.example` lists them): `PHONELLM_API_KEY`,
`PHONELLM_BASE_URL`, `LLM_MODEL`, `JEV_API_KEY`, `CARTESIA_API_KEY`, `DEEPGRAM_API_KEY`.

```sh
cd server && uv run bot.py -t webrtc            # http://localhost:7860
cd client && npm install && npm run dev         # http://localhost:5173
```

The hosted PhoneLLM endpoint scales to zero: the bot starts waking it on launch, and a cold start
can take a couple of minutes. The characters say hello when you connect. You can also type to the
room (the box under the conversation).

Without audio: `cd server && uv run python scripts/converse.py` routes and answers the same way
in text, on the live Jev and PhoneLLM (`--script scripts/sample.txt` or `scripts/followups.txt`).

## How it works

```
room   transport → Deepgram → Hearing → user aggregator → Router → CastBridge → Cartesia
       → transport → Playback → assistant aggregator
maya   a CharacterWorker: PhoneLLM with Maya's prompt, active only on her turns
theo   a CharacterWorker: PhoneLLM with Theo's prompt, active only on his turns
```

- **One transcript, two seats** (`server/room.py`). The room keeps who said what. When a
  character gets a turn, they're handed the whole conversation from their own seat: their lines
  as their replies, everyone else's as `[User] …` or `[Theo] …`. So neither misses what was said
  while the other had the floor.
- **Jev routes every turn you take** (`Referee`, one `ChoiceQuestion`): Maya, Theo, or both. It
  sees the characters, the recent conversation, who you spoke to last, and your words. It's asked
  on your partial transcripts too (`Hearing`), so by the time your turn ends the answer is usually
  already cached (a frontrun).
- **A recency prior** (`weigh`, `RECENCY_WEIGHT` in `config.py`). Whoever you spoke to last counts
  twice before Jev's reading of the new words, then it's renormalised. Jev's probabilities are
  calibrated, so this only decides the route when Jev is torn: a name or "not you" still wins.
- **Handover by activation** (`server/director.py`). The director deactivates one character's
  worker and activates the other's, passing their view of the conversation in the activation.
  The turn travels inside the activation because the bus drops frames sent to a worker that isn't
  active yet. "Both" queues the second character until the first one's line has finished playing.
  The characters never talk to each other.
- **One TTS, two voices** (`CastBridge`). The bus bridge switches Cartesia's voice in-band, just
  before a line from the other character.
- **The client is told everything** over RTVI server messages: `jev` (each reading: what it heard,
  what it was shown, the probabilities, raw and weighted), `turn` (who has the floor, why, and the
  context they were given), `speaker` (whose voice is playing, sent as the audio starts and stops),
  `line` (the transcript) and `cast`.
- **Two auras** (`client/src/components/room/character-panel.tsx`). There's one bot audio track,
  so the client routes it to the aura of whoever the last `speaker` message named. The other
  aura rests, and one pulses while its character has the floor but hasn't spoken yet.

Tunables are in `server/config.py`. The prompt is `server/prompts/character.md`, with each
character's persona in `prompts/<id>.md`. The cast (names, roles, voices, what each knows best,
which Jev uses to route) is `characters.json`, read by both the bot and the client.

## Notes

- PhoneLLM runs at temperature 0 with `repetition_penalty` 1.1. Without it, both characters soon
  repeat their introductions word for word (`LLM_REPETITION_PENALTY=1.0` to compare).
- Measured on typed turns: Jev ~200–300 ms uncached (0 when the frontrun has it), PhoneLLM
  ~300–500 ms to a whole short line.

## Checks

```sh
cd server && uv run ruff check . && uv run pyright && uv run pytest -q
cd client && npx tsc -b && npx eslint .
```
