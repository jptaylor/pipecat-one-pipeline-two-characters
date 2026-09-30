"""`Director`: who speaks next in the room, and the processors that let it hear and steer.

The room worker's pipeline, and where the director sits in it:

    transport → STT → Hearing → user aggregator → Router → CastBridge → TTS → transport → Playback
                                                                               → assistant aggregator

- `Hearing` passes the user's words, partial and final, to the director as they are heard, so
  Jev reads who they are talking to while they are still talking (a frontrun: the final read of
  the same words is then a cache hit).
- `Router` takes each finished user turn (the aggregator's `LLMContextFrame`) out of the stream:
  the director routes it with Jev and hands the turn to one character (or to each of a group,
  one after the other) by activating that character's worker with its view of the conversation.
- `CastBridge` is the bus bridge: the characters' lines come back through it, and it switches the
  TTS to the voice of whoever is speaking, in-band, just before their line.
- `Playback` watches the bot start and stop speaking: the client is told whose voice is playing,
  and when several characters answer, each waits for the line before theirs to finish playing.
- The assistant aggregator's `on_assistant_turn_stopped` tells the director what was actually
  said (cut short if interrupted). If the user spoke to a group, the next of them answers.
  Otherwise Jev reads the line (`Referee.reply`, asked as soon as the line is written): if it's
  for someone else at the table to answer, they do, and so on, until Jev gives the floor back to
  the user (or `MAX_BOUNCES` replies in a row). Speaking or typing always takes the floor.

Everything the director decides goes to the client as RTVI server messages: `jev` (each reading),
`turn` (who has the floor and why), `speaker` (whose voice is playing, sent as the audio starts and
stops), `line` (the transcript) and `cast`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any

from loguru import logger
from pipecat.bus import BusBridgeProcessor
from pipecat.bus.messages import BusFrameMessage, BusMessage
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterimTranscriptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSUpdateSettingsFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.cartesia.tts import CartesiaTTSService

from cast import TurnArgs
from config import (
    CLOSE_FLOOR,
    HANDOVER_WAIT_S,
    MAX_BOUNCES,
    RECENCY_WEIGHT,
    REPLY_FLOOR,
    ROUTE_WAIT_S,
    Character,
)
from room import (
    GROUP,
    USER,
    Cue,
    Line,
    Reading,
    Referee,
    Transcript,
    normalize,
    plan_reply,
    plan_route,
    plan_welcome,
    weigh,
)

LLM_LINE_FRAMES = (LLMFullResponseStartFrame, LLMTextFrame, LLMFullResponseEndFrame)


def user_text(message: Any) -> str:
    """What the user said in one of the aggregator's context messages ("" for anything else)."""
    if not isinstance(message, dict) or message.get("role") != "user":
        return ""
    content = message.get("content")
    return content if isinstance(content, str) else ""


class Director:
    """Decides who speaks, and keeps the one transcript every character is shown."""

    def __init__(self, cast: Sequence[Character], referee: Referee) -> None:
        self.cast = {c.id: c for c in cast}
        self.transcript = Transcript(cast)
        self.referee = referee
        self.worker: PipelineWorker | None = None  # the room worker, set once it exists

        self.active: str | None = None  # the character whose turn it is (their worker is active)
        self.speaking: str | None = None  # whose line is flowing to the TTS
        self._queue: list[Cue] = []  # turns promised after this one (a group, the welcome)
        # Who the user spoke to last: Jev's choice (a character, or a group), for the recency
        # prior, and everyone it cued, for Jev's state.
        self._favoured: str | None = None
        self._addressed: list[str] = []
        self._bounces = 0  # characters answering characters since the user last spoke
        self._generated: dict[str, str] = {}  # each character's latest line, as written

        self._seen = 0  # messages of the aggregator's context already read
        self._turns = 0  # user turns routed: a frontrun read landing after its turn is dropped
        self._finals: list[str] = []  # the user's words so far this turn
        self._interim = ""
        self._preview_want: str | None = None
        self._preview_task: asyncio.Task | None = None

        self._user_speaking = False
        self._line_started = False  # the current turn's audio has started playing
        self._line_done = asyncio.Event()  # ...and has stopped
        self._tasks: set[asyncio.Task] = set()
        self._route_task: asyncio.Task | None = None
        self._handover_task: asyncio.Task | None = None

    # --- Wiring --------------------------------------------------------------------------------

    def spawn(self, coro: Any, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._settled)
        return task

    def _settled(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.opt(exception=task.exception()).error(f"Director: {task.get_name()} failed")

    async def emit(self, data: dict[str, Any]) -> None:
        if self.worker is not None and self.worker.rtvi is not None:
            await self.worker.rtvi.send_server_message(data)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self.referee.close()

    async def send_cast(self) -> None:
        await self.emit(
            {
                "type": "cast",
                "characters": [
                    {"id": c.id, "name": c.name, "role": c.role} for c in self.cast.values()
                ],
                "jev": self.referee.model,
            }
        )

    # --- Turns ---------------------------------------------------------------------------------

    async def welcome(self) -> None:
        """Everyone says hello in turn, with their favourite colour."""
        await self.send_cast()
        cues = plan_welcome(self.transcript)
        self._queue = cues[1:]
        await self.dispatch(cues[0])

    async def dispatch(self, cue: Cue) -> None:
        """Give `cue.speaker` the floor: activate their worker with their view of the whole
        conversation, and quieten whoever had it before."""
        assert self.worker is not None
        messages = self.transcript.view(cue.speaker, cue.note)
        previous, self.active = self.active, cue.speaker
        self._line_started = False
        self._line_done = asyncio.Event()
        if previous is not None and previous != cue.speaker:
            await self.worker.deactivate_worker(previous)
        await self.worker.activate_worker(cue.speaker, args=TurnArgs(messages=messages))
        name = self.cast[cue.speaker].name
        logger.info(f"Director: {name}'s turn ({cue.reason}{f': {cue.note}' if cue.note else ''})")
        await self.emit(
            {
                "type": "turn",
                "speaker": cue.speaker,
                "reason": cue.reason,
                "note": cue.note,
                "messages": messages,
                "at": time.time(),
            }
        )

    async def user_turn(self, context: LLMContext) -> None:
        """A user turn has ended: record it, ask Jev who it was said to, and hand them the turn."""
        messages = context.get_messages()
        fresh = messages[self._seen :]
        self._seen = len(messages)
        self._turns += 1
        self._bounces = 0
        said = normalize(" ".join(user_text(m) for m in fresh))
        self._finals.clear()
        self._interim = ""
        self._preview_want = None
        # A new turn replaces whatever was still to come, typed turns included (they interrupt
        # without the user ever starting to speak).
        self._queue.clear()
        if self._handover_task is not None and not self._handover_task.done():
            self._handover_task.cancel()
        if not said:  # an interruption with no words: whoever had the floor carries on
            await self.dispatch(Cue(self.active or next(iter(self.cast)), "continue"))
            return

        history = list(self.transcript.lines)
        self.transcript.add(USER, said)
        await self.emit_line(self.transcript.lines[-1])
        try:
            reading = await asyncio.wait_for(
                self.referee.addressee(
                    self.transcript, history, said, last_addressed=self._addressed
                ),
                ROUTE_WAIT_S,
            )
        except TimeoutError:
            reading = Reading("route", USER, said, None, error=f"no answer in {ROUTE_WAIT_S} s")
        reading = weigh(reading, self._favoured, RECENCY_WEIGHT)
        await self.emit(reading.to_message())
        self.log_reading(reading)
        cues = plan_route(reading, self.transcript)
        if reading.choice:
            self._favoured = reading.choice
            self._addressed = [cue.speaker for cue in cues]
        self._queue = cues[1:]
        await self.dispatch(cues[0])

    async def heard(self, text: str, final: bool) -> None:
        """Words still being spoken: Jev reads them now, so the final read is ready in time."""
        if final:
            self._finals.append(text)
            self._interim = ""
        else:
            self._interim = text
        so_far = normalize(" ".join([*self._finals, self._interim]))
        if not so_far:
            return
        self._preview_want = so_far
        if self._preview_task is None or self._preview_task.done():
            self._preview_task = self.spawn(self._preview(), "preview")

    async def _preview(self) -> None:
        # One read in flight at a time; when it lands, read the latest words if they changed.
        read: str | None = None
        while self._preview_want and self._preview_want != read:
            read, turn = self._preview_want, self._turns
            history = list(self.transcript.lines)
            reading = await self.referee.addressee(
                self.transcript, history, read, last_addressed=self._addressed, kind="preview"
            )
            if turn != self._turns:  # the turn ended while Jev read it: the route has it
                return
            await self.emit(weigh(reading, self._favoured, RECENCY_WEIGHT).to_message())

    def line_generated(self, speaker: str, text: str) -> None:
        """A character's whole line, as written, before it has finished playing (the TTS's word
        timings drop some punctuation, so this is what gets recorded). Unless a group is still
        answering, Jev reads it now for a reply, so the answer is ready when the line ends."""
        text = normalize(text)
        self._generated[speaker] = text
        if text and not self._queue and self._bounces < MAX_BOUNCES:
            history = list(self.transcript.lines)
            run = self.transcript.run(history) + 1
            reply = self.referee.reply(self.transcript, history, speaker, text, run)
            self.spawn(reply, "reply frontrun")

    async def line_spoken(self, content: str, interrupted: bool) -> None:
        """A character's line has ended: record what was said. If the user spoke to a group, the
        next of them answers; otherwise Jev decides whether someone at the table answers this
        line, or it's the user's turn."""
        speaker = self.speaking
        if speaker is None:
            return
        written = self._generated.pop(speaker, None)
        # A whole line is recorded as written; a line cut short, as far as it was heard.
        text = normalize(content if interrupted or not written else written)
        if not text:
            if interrupted:
                self._queue.clear()
            return
        history = list(self.transcript.lines)
        line = self.transcript.add(speaker, text, interrupted=interrupted)
        await self.emit_line(line)
        if interrupted or self._user_speaking:
            self._queue.clear()
            return
        turn = self._turns
        if self._queue:
            self._handover_task = self.spawn(self._hand_over(self._queue.pop(0), turn), "handover")
            return
        if self._bounces >= MAX_BOUNCES:
            logger.info(f"Director: {MAX_BOUNCES} replies in a row; the floor is the user's")
            return
        run = self.transcript.run(history) + 1
        reading = await self.referee.reply(self.transcript, history, speaker, text, run)
        if turn != self._turns or self._user_speaking:
            return  # the user has spoken since: their turn wins
        cue, why = plan_reply(reading, self.transcript, REPLY_FLOOR, CLOSE_FLOOR, run)
        # What came of it: who answers next, or the user's turn, and why.
        next_ = cue.speaker if cue else USER
        await self.emit({**reading.to_message(), "next": next_, "why": why})
        self.log_reading(reading)
        if cue is not None:
            self._bounces += 1
            self._handover_task = self.spawn(self._hand_over(cue, turn), "reply")

    async def _hand_over(self, cue: Cue, turn: int) -> None:
        # The next line waits for this one to finish playing, unless the user takes the floor.
        try:
            await asyncio.wait_for(self._line_done.wait(), HANDOVER_WAIT_S)
        except TimeoutError:
            logger.warning(f"Director: the line didn't finish playing in {HANDOVER_WAIT_S} s")
        if self._user_speaking or turn != self._turns:
            return
        await self.dispatch(cue)

    # --- What the processors report ------------------------------------------------------------

    async def user_started(self) -> None:
        self._user_speaking = True
        self._queue.clear()
        for task in (self._handover_task, self._route_task):
            if task is not None and not task.done():
                task.cancel()

    async def user_stopped(self) -> None:
        self._user_speaking = False

    async def bot_started(self) -> None:
        self._line_started = True
        # Who is talking, as the audio starts: the client shows their aura.
        await self.emit({"type": "speaker", "speaker": self.speaking, "at": time.time()})

    async def bot_stopped(self) -> None:
        if self._line_started:
            self._line_done.set()
        await self.emit({"type": "speaker", "speaker": None, "at": time.time()})

    def line_starting(self, speaker: str) -> None:
        self.speaking = speaker

    # --- Reporting -----------------------------------------------------------------------------

    async def emit_line(self, line: Line) -> None:
        await self.emit(
            {
                "type": "line",
                "speaker": line.speaker,
                "text": line.text,
                "interrupted": line.interrupted,
                "at": time.time(),
            }
        )

    def log_reading(self, reading: Reading) -> None:
        odds = ", ".join(f"{k} {v:.2f}" for k, v in reading.probabilities.items())
        how = "cached" if reading.cached else f"{reading.ms:.0f} ms"
        error = f" ({reading.error})" if reading.error else ""
        prior = ""
        if reading.choice == GROUP:
            asked = ", ".join(
                f"{c} {reading.included[c]:.2f}" for c in reading.group(list(self.cast))
            )
            odds += f"; asked: {asked}"
        if reading.raw:
            raw = ", ".join(f"{k} {v:.2f}" for k, v in reading.raw.items())
            prior = f" (Jev: {raw}; ×{reading.weight:g} for {reading.favoured})"
        closed = "" if reading.closed is None else f"; closed {reading.closed:.2f}"
        logger.info(f'Jev {reading.kind}: "{reading.heard}" → {odds}{closed}{prior} [{how}]{error}')

    # --- Processors ----------------------------------------------------------------------------

    def hearing(self) -> Hearing:
        return Hearing(self)

    def router(self) -> Router:
        return Router(self)

    def playback(self) -> Playback:
        return Playback(self)


class Hearing(FrameProcessor):
    """Between the STT and the user aggregator: the user's words as they are heard."""

    def __init__(self, director: Director) -> None:
        super().__init__(name="Hearing")
        self._director = director

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if (
            isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame))
            and frame.text.strip()
        ):
            await self._director.heard(frame.text, isinstance(frame, TranscriptionFrame))
        await self.push_frame(frame, direction)


class Router(FrameProcessor):
    """After the user aggregator: each finished user turn goes to the director, not the bridge."""

    def __init__(self, director: Director) -> None:
        super().__init__(name="Router")
        self._director = director

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        director = self._director
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            if frame.speculation:
                logger.warning("Router: a speculative turn isn't routed (eager turn-taking is off)")
                return
            # Routed off the frame loop: Jev takes a few hundred ms, and speech that starts in
            # the meantime cancels the route (the new words make a turn of their own).
            director._route_task = director.spawn(director.user_turn(frame.context), "route")
            return
        if isinstance(frame, UserStartedSpeakingFrame):
            await director.user_started()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            await director.user_stopped()
        await self.push_frame(frame, direction)


class CastBridge(BusBridgeProcessor):
    """The bus bridge to the characters, which also gives each line its speaker's voice.

    A line starting from a character (`LLMFullResponseStartFrame` from their worker) switches the
    TTS to their voice first, in the same stream, so the switch lands exactly between two lines.
    Lines from a character whose turn it no longer is (still in flight after a handover) are
    dropped. Each finished line is passed to the director as written.
    """

    def __init__(self, director: Director, *, voice: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._director = director
        self._voice = voice
        self._text: dict[str, list[str]] = {}

    async def on_bus_message(self, message: BusMessage) -> None:
        director = self._director
        if isinstance(message, BusFrameMessage) and message.source in director.cast:
            frame, speaker = message.frame, message.source
            if isinstance(frame, LLM_LINE_FRAMES) and speaker != director.active:
                return
            if isinstance(frame, LLMFullResponseStartFrame):
                director.line_starting(speaker)
                self._text[speaker] = []
                voice = director.cast[speaker].voice
                if voice != self._voice:
                    self._voice = voice
                    settings = CartesiaTTSService.Settings(voice=voice)
                    await self.push_frame(TTSUpdateSettingsFrame(delta=settings))
            elif isinstance(frame, LLMTextFrame):
                self._text.setdefault(speaker, []).append(frame.text)
            elif isinstance(frame, LLMFullResponseEndFrame):
                director.line_generated(speaker, "".join(self._text.pop(speaker, [])))
        await super().on_bus_message(message)


class Playback(FrameProcessor):
    """After the output transport: when the bot's audio starts and stops playing."""

    def __init__(self, director: Director) -> None:
        super().__init__(name="Playback")
        self._director = director

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            await self._director.bot_started()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            await self._director.bot_stopped()
        await self.push_frame(frame, direction)
