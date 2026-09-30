# One pipeline, six voices

One conversation around a kitchen table with six characters: Maya (botanist), Theo (chef), Juno
(musician), Otto (sea captain), Felix (engineer) and Edith (historian). Each has their own Cartesia
voice and favourite colour (their colour on screen), and all of them hear everything. When you
connect, they each introduce themselves with their favourite colour.

Jev (TypeSafe's classifier, through Pipecat Classifiers) reads each thing you say, while you're
still saying it, and decides who you're talking to: one of them, or a group. The characters it
picks answer in turn, in their own voices, each with the whole conversation in front of them. "So
who liked blue?" finds Otto from what was said. "Felix and Juno, …" asks just those two, and "and
number?" stays with them. "Hello!" gets everyone.

Jev also reads every line a character says. If it's for someone else at the table to answer (a
question, a tease, "Theo would burn water"), they do, and it can bounce around like that until Jev
decides the exchange has run its course and the floor is yours again. Speaking or typing always
takes the floor.

Pipecat 1.12 workers, PhoneLLM (temperature 0), Deepgram, Cartesia, Jev. The client is Vite +
React + Pipecat UI (shadcn registry), in the Deepgram demo's terminal style.

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
in text, on the live Jev and PhoneLLM (`--script scripts/sample.txt`: the colours, a group, a
follow-up, a greeting; `scripts/bounce.txt`: characters answering each other; `scripts/pointing.txt`: one character
pointing you to another).

## How it works

```
room                 transport → Deepgram → Hearing → user aggregator → Router → CastBridge
                     → Cartesia → transport → Playback → assistant aggregator
maya, theo, … edith  a CharacterWorker each: PhoneLLM with their prompt, active only on their turns
```

- **One transcript, a seat each** (`server/room.py`). The room keeps who said what. When a
  character gets a turn, they're handed the whole conversation from their own seat: their lines
  as their replies, everyone else's as `[User] …` or `[Otto] …`.
- **Jev routes every turn you take, in one request** (`Referee`): a `ChoiceQuestion` (one of the
  six, or a group) plus a `YesNoQuestion` per character (are they one of those asked?). Only a
  group turn uses the yes/no answers, and those who pass answer in turn, most surely asked first.
  Jev sees the characters, the last 30 lines of the conversation (so the introductions stay in
  view), who you spoke to last, and your words. It's asked on your partial transcripts too
  (`Hearing`), so by the time your turn ends the answer is usually already cached.
- **Characters answering characters** (`Referee.reply`, `plan_reply`). After each line (once any
  group has finished), Jev gets two questions in one request: who, if anyone, answers it (the
  other characters, or the user), and has the exchange run its course (yes/no, told how many lines
  the characters have said since you spoke). Someone answers if Jev is at least 50% sure it's
  theirs (`REPLY_FLOOR`) and the exchange isn't closed (`CLOSE_FLOOR`). The first comeback is
  always allowed, and six replies in a row (`MAX_BOUNCES`) is a backstop only; Jev closes well
  before it. The question is asked as soon as the line is written, so its answer is cached by the
  time the line has played.
- **A light recency prior** (`weigh`, `RECENCY_WEIGHT` = 1.2 in `config.py`). Whoever you spoke
  to last gets a nudge before Jev's reading of the new words, then it's renormalised. Jev's
  probabilities are calibrated, so this only settles near-ties: a name, a question about what
  someone said, or "not you" always wins.
- **Handover by activation** (`server/director.py`). The director deactivates whoever had the
  floor and activates the next character's worker, passing their view of the conversation in the
  activation. The turn travels inside the activation because the bus drops frames sent to a
  worker that isn't active yet. In a group, each waits for the line before theirs to finish
  playing, and is told who was asked and who has answered already.
- **One TTS, six voices** (`CastBridge`). The bus bridge switches Cartesia's voice in-band, just
  before a line from a different character.
- **The client is told everything** over RTVI server messages: `jev` (each reading: what it heard,
  what it was shown, the choice, who's included, raw and weighted), `turn` (who has the floor,
  why, and the context they were given), `speaker` (whose voice is playing, sent as the audio
  starts and stops), `line` (the transcript) and `cast`.
- **Six auras** (`client/src/components/room/character-panel.tsx`). There's one bot audio track,
  so the client routes it to the aura of whoever the last `speaker` message named. The others
  rest, and one pulses while its character has the floor but hasn't spoken yet.

The cast (names, roles, voices, favourite colours, what each knows best, which Jev uses to route)
is `characters.json`, read by both the bot and the client: add or change characters there (2 to 8)
and give each a persona in `server/prompts/<id>.md`. The shared prompt is
`server/prompts/character.md`; tunables are in `server/config.py`.

## Notes

- PhoneLLM runs at temperature 0 with `repetition_penalty` 1.1. Without it, the characters soon
  repeat their introductions word for word (`LLM_REPETITION_PENALTY=1.0` to compare).
- Every option in the "who answers" question is described, not just named. With bare names
  beside a described "the user", Jev gave a line that points you to someone ("Maya here… her
  colour is green") back to you: 0.27 for Maya, against 0.86 with the options described.
- Closing is its own yes/no question. Folded into the "who answers" choice, Jev kept picking
  whoever a line ended by naming ("…, Theo"), and polite back-and-forths ran to the backstop.
- A group turn's note says "it's your turn now: reply to the person". Looser wording had PhoneLLM
  announce that it would answer later, or answer for the whole table.
- Measured on typed turns: Jev ~200–300 ms uncached (0 when the frontrun has it), PhoneLLM
  ~300–500 ms to a whole short line.

## Checks

```sh
cd server && uv run ruff check . && uv run pyright && uv run pytest -q
cd client && npx tsc -b && npx eslint .
```
