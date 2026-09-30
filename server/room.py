"""The room: one conversation between the user and two characters, and Jev's questions about it.

`Transcript` is the conversation as it happened, who said what. Each character's LLM sees it from
its own seat (`Transcript.view`): its own lines are its replies, and everyone else's are user
turns marked with who spoke. Both characters are always shown the whole conversation, so neither
misses what was said while the other one had the floor.

`Referee` asks Jev who the user is talking to: one character, or both (who then answer in turn).
It is asked while the user is still speaking too, so by the time their turn ends the answer is
usually cached. `weigh` then applies a recency prior: whoever the user spoke to last is favoured,
so a follow-up such as "and number?" stays with them unless the words clearly say otherwise.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from loguru import logger
from pipecat.classifiers.base_classifier import BaseClassifier, ChoiceQuestion, ChoiceResult

from config import HISTORY_LINES, JEV_CACHE_SIZE, Character

USER = "user"
BOTH = "both"

# What a character is told about the moment, appended to its view as a `[Note: …]` line.
NOTE_WELCOME_FIRST = (
    "The person has just walked in. Say hello and introduce just yourself, in a sentence or two."
)
NOTE_WELCOME_SECOND = "The person has just walked in and {other} said hello. Introduce yourself."
NOTE_SWITCH = "The person wants your answer now, not {other}'s."
NOTE_BOTH_FIRST = "The person is asking you both. Give only your own answer."
NOTE_BOTH_SECOND = "The person asked you both, and {other} has answered. Now give your own answer."
NOTE_CARRY_ON = "Carry on."
NOTE_JOINED = "The person joins the room."


def normalize(text: str) -> str:
    return " ".join(text.split())


@dataclass
class Line:
    speaker: str  # USER or a character id
    text: str
    interrupted: bool = False


class Transcript:
    """Who said what, in order, and how each character sees it."""

    def __init__(self, cast: Sequence[Character]) -> None:
        self.cast = {c.id: c for c in cast}
        self.lines: list[Line] = []

    def add(self, speaker: str, text: str, *, interrupted: bool = False) -> Line:
        line = Line(speaker, normalize(text), interrupted)
        self.lines.append(line)
        return line

    def label(self, speaker: str) -> str:
        return "User" if speaker == USER else self.cast[speaker].name

    def last_character(self) -> str | None:
        return next((ln.speaker for ln in reversed(self.lines) if ln.speaker != USER), None)

    def view(self, me: str, note: str | None = None) -> list[dict[str, str]]:
        """The conversation as `me`'s LLM sees it: its own lines as assistant turns, everyone
        else's as user turns marked `[User]` or `[Name]`, and the moment's note last."""
        messages: list[dict[str, str]] = []

        def say(role: str, content: str) -> None:
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"] += "\n" + content
            else:
                messages.append({"role": role, "content": content})

        for line in self.lines:
            if line.speaker == me:
                say("assistant", line.text + ("…" if line.interrupted else ""))
            else:
                cut = " (cut off)" if line.interrupted else ""
                say("user", f"[{self.label(line.speaker)}] {line.text}{cut}")
        if messages and messages[0]["role"] == "assistant":
            messages.insert(0, {"role": "user", "content": f"[Note: {NOTE_JOINED}]"})
        if note:
            say("user", f"[Note: {note}]")
        elif not messages or messages[-1]["role"] == "assistant":
            say("user", f"[Note: {NOTE_CARRY_ON}]")
        return messages

    def for_jev(
        self, history: Sequence[Line], latest: Line, last_addressed: str | None = None
    ) -> dict[str, Any]:
        """Jev's state: who is in the room, the recent conversation, who the user spoke to last,
        and the line in question."""
        names = [c.name for c in self.cast.values()]
        said: dict[str, Any] = {"speaker": self.label(latest.speaker), "said": latest.text}
        if latest.speaker == USER:
            said["heard_via"] = "live speech-to-text, so a name may be misheard"
        state: dict[str, Any] = {
            "setting": (
                f"A spoken conversation in one room between a user and two characters, "
                f"{names[0]} and {names[1]}. Everyone hears everything that is said."
            ),
            "characters": {
                c.name: {"role": c.role, "knows_best": c.topics} for c in self.cast.values()
            },
            "conversation": [
                {
                    "speaker": self.label(line.speaker),
                    "said": line.text + (" (cut off)" if line.interrupted else ""),
                }
                for line in history[-HISTORY_LINES:]
            ],
        }
        if last_addressed:
            state["user_last_spoke_to"] = (
                f"both {names[0]} and {names[1]}"
                if last_addressed == BOTH
                else self.label(last_addressed)
            )
        state["latest"] = said
        return state


# --- Jev's questions --------------------------------------------------------------------------


def addressee_question(a: Character, b: Character) -> ChoiceQuestion:
    """Who the user is talking to in `latest`: a, b, or both."""
    return ChoiceQuestion(
        instructions={
            "question": (
                "The user just said `latest`. Who are they talking to, and so who should answer?"
            ),
            "clues": [
                "A name the user says tells you who, even when speech-to-text misspells it.",
                "A reply to what a character just said or asked goes to that character.",
                "A follow-up that carries on the user's previous question, such as 'and number?', "
                "'why?' or 'really?', goes to whoever they spoke to last (`user_last_spoke_to`), "
                "unless it names someone else.",
                "'And you?' or 'what about you?' right after one character answered asks the "
                "other one.",
                "A correction such as 'not you', 'no, I was talking to the other one' or 'I meant "
                "you' goes to the character who did not just speak.",
                "With no name and nothing to reply to, the subject decides: whoever knows it best "
                "in `characters`.",
                "A greeting, a goodbye or thanks with no name ('hello!', 'hi there', 'thanks!') "
                "is for both of them, even in the middle of a conversation with one.",
            ],
        },
        options={
            a.id: {"who": a.name},
            b.id: {"who": b.name},
            BOTH: {
                "who": f"both {a.name} and {b.name}: the user wants to hear from each of them",
                "examples": [
                    "Hello!",
                    "Who am I talking to?",
                    "Hi, both of you!",
                    "What do you two think?",
                    "Can you each introduce yourselves?",
                    "Who's there?",
                ],
            },
        },
    )


# --- Asking Jev -------------------------------------------------------------------------------


@dataclass
class Reading:
    """One answer from Jev, as the debug panel shows it."""

    kind: str  # "preview" (words still being spoken) or "route" (a finished user turn)
    speaker: str  # whose words were read (always USER)
    heard: str
    choice: str | None
    probabilities: dict[str, float] = field(default_factory=dict)
    ms: float = 0.0  # how long the caller waited: 0 when the answer was already cached
    cached: bool = False
    state: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # With a recency prior applied (`weigh`): Jev's own probabilities, and who was favoured.
    raw: dict[str, float] | None = None
    favoured: str | None = None
    weight: float = 1.0

    def p(self, option: str) -> float:
        return self.probabilities.get(option, 0.0)

    def to_message(self) -> dict[str, Any]:
        return {
            "type": "jev",
            "kind": self.kind,
            "speaker": self.speaker,
            "heard": self.heard,
            "choice": self.choice,
            "probabilities": {k: round(v, 4) for k, v in self.probabilities.items()},
            "ms": round(self.ms),
            "cached": self.cached,
            "state": self.state,
            "error": self.error,
            "raw": {k: round(v, 4) for k, v in self.raw.items()} if self.raw else None,
            "favoured": self.favoured,
            "weight": self.weight,
            "at": time.time(),
        }


class Referee:
    """Asks Jev who the user is talking to.

    Answers are cached by state and question, and a question already in flight is shared, so the
    read made while the user was still speaking answers the final one when the words match.
    """

    def __init__(self, classifier: BaseClassifier, cast: Sequence[Character]) -> None:
        a, b = cast
        self._classifier = classifier
        self._questions: dict[str, dict[str, ChoiceQuestion]] = {
            "addressee": {"addressee": addressee_question(a, b)},
        }
        self._cache: OrderedDict[str, asyncio.Task[dict[str, ChoiceResult]]] = OrderedDict()
        self.asked = 0
        self.cached = 0

    @property
    def model(self) -> str | None:
        return self._classifier.model

    async def addressee(
        self,
        transcript: Transcript,
        history: Sequence[Line],
        heard: str,
        *,
        last_addressed: str | None = None,
        kind: str = "route",
    ) -> Reading:
        state = transcript.for_jev(history, Line(USER, normalize(heard)), last_addressed)
        return await self._read(kind, USER, heard, state, "addressee", "addressee")

    async def _read(
        self, kind: str, speaker: str, heard: str, state: dict, questions: str, name: str
    ) -> Reading:
        started = time.perf_counter()
        key = json.dumps([questions, state], sort_keys=True)
        task = self._cache.get(key)
        cached = task is not None and task.done()
        if task is None:
            task = asyncio.create_task(self._classifier.choice(state, self._questions[questions]))
            task.add_done_callback(lambda t, key=key: self._forget_failure(key, t))
            self._cache[key] = task
            while len(self._cache) > JEV_CACHE_SIZE:
                self._cache.popitem(last=False)
            self.asked += 1
        else:
            self._cache.move_to_end(key)
            self.cached += 1
        reading = Reading(kind, speaker, normalize(heard), None, cached=cached, state=state)
        try:
            # Shielded: a caller that gives up leaves the answer to the cache.
            result = (await asyncio.shield(task))[name]
            reading.choice = result.choice
            reading.probabilities = dict(result.probabilities)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 — a failed read is shown, never fatal
            reading.error = f"{type(error).__name__}: {error}"
        reading.ms = 0.0 if cached else (time.perf_counter() - started) * 1000
        return reading

    def _forget_failure(self, key: str, task: asyncio.Task) -> None:
        if task.cancelled() or task.exception() is not None:
            if self._cache.get(key) is task:
                del self._cache[key]
            if not task.cancelled():
                logger.warning(f"Jev: {task.exception()}")

    async def close(self) -> None:
        for task in self._cache.values():
            if not task.done():
                task.cancel()
        self._cache.clear()


def weigh(reading: Reading, favoured: str | None, weight: float) -> Reading:
    """A recency prior on Jev's answer: `favoured` (whoever the user spoke to last, a character or
    both) counts `weight` times as likely before the words are weighed, then the probabilities are
    renormalised. Jev's probabilities are calibrated, so this only decides a route when Jev is
    torn: a name or a correction ("not you, the other one") outweighs it."""
    probabilities = reading.probabilities
    if not favoured or weight == 1 or favoured not in probabilities or reading.error:
        return reading
    scaled = {k: p * (weight if k == favoured else 1.0) for k, p in probabilities.items()}
    total = sum(scaled.values()) or 1.0
    weighted = {k: p / total for k, p in scaled.items()}
    return replace(
        reading,
        probabilities=weighted,
        choice=max(weighted, key=lambda k: weighted[k]),
        raw=dict(probabilities),
        favoured=favoured,
        weight=weight,
    )


# --- Who speaks -------------------------------------------------------------------------------


@dataclass
class Cue:
    """A character's turn to speak, why, and the note it is given about the moment."""

    speaker: str
    reason: str  # "addressed", "both", "welcome", "continue" or "fallback"
    note: str | None = None


def order_both(reading: Reading, transcript: Transcript, ids: Sequence[str]) -> tuple[str, str]:
    """Who answers first when the user talks to both: whoever Jev leant towards, or, when it's
    close, whoever didn't speak last."""
    a, b = ids
    if abs(reading.p(a) - reading.p(b)) >= 0.1:
        first = a if reading.p(a) > reading.p(b) else b
    else:
        first = b if transcript.last_character() == a else a
    return first, (b if first == a else a)


def plan_route(reading: Reading, transcript: Transcript) -> list[Cue]:
    """The turns a user's line starts: the one character it was said to, or both in turn. Without
    an answer from Jev, whoever spoke last carries on."""
    ids = list(transcript.cast)
    if reading.choice == BOTH:
        first, second = order_both(reading, transcript, ids)
        return [
            Cue(first, "both", NOTE_BOTH_FIRST),
            Cue(second, "both", NOTE_BOTH_SECOND.format(other=transcript.label(first))),
        ]
    if reading.choice in ids:
        # Talking to the character who didn't just speak: tell them so, or a correction such as
        # "no, I was asking the other one" reads to them as not meant for them.
        last = transcript.last_character()
        switch = last is not None and last != reading.choice
        note = NOTE_SWITCH.format(other=transcript.label(last)) if switch and last else None
        return [Cue(reading.choice, "addressed", note)]
    return [Cue(transcript.last_character() or ids[0], "fallback")]


def plan_welcome(transcript: Transcript) -> list[Cue]:
    first, second = transcript.cast
    return [
        Cue(first, "welcome", NOTE_WELCOME_FIRST),
        Cue(second, "welcome", NOTE_WELCOME_SECOND.format(other=transcript.label(first))),
    ]
