"""The room: one conversation between the user and a cast of characters, and Jev's read of it.

`Transcript` is the conversation as it happened, who said what. Each character's LLM sees it from
its own seat (`Transcript.view`): its own lines are its replies, and everyone else's are user
turns marked with who spoke. Every character is always shown the whole conversation, so nobody
misses what was said while someone else had the floor.

`Referee` asks Jev who the user is talking to, in one request: a choice of one character or a
group, and for each character whether they're one of those asked (only a group turn uses those).
It is asked while the user is still speaking too, so by the time their turn ends the answer is
usually cached. Jev sees the recent conversation, so "so who liked blue?" finds who said it.
`weigh` then applies a light recency prior: whoever the user spoke to last is nudged ahead, which
only settles near-ties.

Jev also reads every line a character says (`Referee.reply`): should someone else at the table
answer it before the user speaks again, and who? That lets the characters bounce off each other
("Theo would burn water" gets a word from Theo) until Jev hands the conversation back to the user.
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
from pipecat.classifiers.base_classifier import (
    BaseClassifier,
    ChoiceQuestion,
    ChoiceResult,
    ClassifierQuestion,
    ClassifierResult,
    YesNoQuestion,
    YesNoResult,
)

from config import HISTORY_LINES, INCLUDED_FLOOR, JEV_CACHE_SIZE, Character

USER = "user"
GROUP = "group"
ADDRESSEE = "addressee"  # the choice question
INCLUDED = "with:"  # prefix of the per-character yes/no questions
REPLY = "reply:"  # the questions after a character's line, one set per speaker
NEXT = "next"  # ...who answers the line, or the user
CLOSED = "closed"  # ...whether the exchange has run its course

# What a character is told about the moment, appended to its view as a `[Note: …]` line. A group
# is told who was asked and who has already answered: "it's your turn now" keeps PhoneLLM from
# answering for the table or announcing that it will answer later.
NOTE_SWITCH = "The person wants your answer now, not {other}'s."
NOTE_GROUP_FIRST = (
    "The person said that to {who}. You answer first: reply to the person in one short sentence "
    "of your own."
)
NOTE_GROUP_NEXT = (
    "The person said that to {who}, and {done} {have} already replied. It's your turn now: reply "
    "to the person in one short sentence of your own."
)
NOTE_WELCOME_FIRST = (
    "The person has just walked in, and everyone at the table is saying hello in turn. You go "
    "first: introduce yourself in one short sentence and say your favourite colour."
)
NOTE_WELCOME_NEXT = (
    "The person has just walked in, and everyone at the table is saying hello in turn; {done} "
    "{have} already. It's your turn now: introduce yourself in one short sentence and say your "
    "favourite colour."
)
NOTE_REPLY = (
    "{other} just spoke to you or about you. It's your turn: answer {other} in one or two short "
    "sentences, with the person listening."
)
NOTE_CARRY_ON = "Carry on."
NOTE_JOINED = "The person joins the room."


def normalize(text: str) -> str:
    return " ".join(text.split())


def names(labels: Sequence[str]) -> str:
    """'Maya', 'Maya and Theo', 'Maya, Theo and Juno'."""
    if len(labels) <= 1:
        return "".join(labels)
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


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

    def run(self, lines: Sequence[Line]) -> int:
        """Character lines at the end of `lines`, since the user last spoke."""
        count = 0
        for line in reversed(lines):
            if line.speaker == USER:
                break
            count += 1
        return count

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
        self,
        history: Sequence[Line],
        latest: Line,
        last_addressed: Sequence[str] = (),
        run: int | None = None,
    ) -> dict[str, Any]:
        """Jev's state: who is in the room, the recent conversation, who the user spoke to last
        (for a user's line) or how long the characters have been talking among themselves (for a
        character's), and the line in question."""
        cast = list(self.cast.values())
        said: dict[str, Any] = {"speaker": self.label(latest.speaker), "said": latest.text}
        if latest.speaker == USER:
            said["heard_via"] = "live speech-to-text, so a name may be misheard"
        state: dict[str, Any] = {
            "setting": (
                f"A spoken conversation around a kitchen table between a user and "
                f"{len(cast)} characters: {names([c.name for c in cast])}. Everyone hears "
                "everything that is said."
            ),
            "characters": {c.name: {"role": c.role, "knows_best": c.topics} for c in cast},
            "conversation": [
                {
                    "speaker": self.label(line.speaker),
                    "said": line.text + (" (cut off)" if line.interrupted else ""),
                }
                for line in history[-HISTORY_LINES:]
            ],
        }
        if last_addressed:
            everyone = len(last_addressed) == len(cast)
            listed = names([self.label(c) for c in last_addressed])
            state["user_last_spoke_to"] = "everyone" if everyone else listed
        if run is not None:
            state["character_turns_since_user_spoke"] = run
        state["latest"] = said
        return state


# --- Jev's questions --------------------------------------------------------------------------


def addressee_question(cast: Sequence[Character]) -> ChoiceQuestion:
    """Who the user is talking to in `latest`: one character, or a group."""
    return ChoiceQuestion(
        instructions={
            "question": (
                "The user just said `latest`. Who are they talking to, and so who should answer?"
            ),
            "clues": [
                "A name the user says tells you who, even when speech-to-text misspells it.",
                "A question about something one of them said earlier ('so who liked blue?', "
                "'which of you had the boat?') goes to whoever said it: look for it in "
                "`conversation`.",
                "A reply to what a character just said or asked goes to that character.",
                "A follow-up that carries on the user's previous question, such as 'and number?', "
                "'why?' or 'really?', goes to whoever they spoke to last (`user_last_spoke_to`), "
                "unless it names someone else.",
                "A correction such as 'not you' or 'I meant the other one' goes to someone other "
                "than the character who just spoke.",
                "With no name and nothing to reply to, the subject decides: whoever knows it best "
                "in `characters`.",
                "A greeting, a goodbye or thanks with no name ('hello!', 'hi there', 'thanks!') "
                "is for everyone.",
            ],
        },
        options={
            **{c.id: {"who": c.name} for c in cast},
            GROUP: {
                "who": (
                    "several of them at once: everyone, or two or more of them named together; "
                    "each of them answers in turn"
                ),
                "examples": [
                    "Hello!",
                    "Who's here?",
                    "What do you all think?",
                    "Can you each introduce yourselves?",
                    f"{cast[0].name} and {cast[1].name}, what do you reckon?",
                    "Thanks, everyone.",
                ],
            },
        },
    )


def included_question(character: Character) -> YesNoQuestion:
    """Whether `character` is one of those the user wants an answer from (used for a group)."""
    name = character.name
    return YesNoQuestion(
        instructions=(
            f"In `latest`, does the user want an answer from {name}, alone or together with others?"
        ),
        yes=(
            f"{name} is named, or included in 'everyone' or 'you all', or the user greets, thanks "
            f"or says goodbye to the whole table, or asks about something {name} said, or carries "
            f"on their previous question and {name} was among those they spoke to last "
            "(`user_last_spoke_to`)"
        ),
        no=f"the user is talking to someone else, or to a few others that leave {name} out",
    )


def reply_question(speaker: Character, cast: Sequence[Character]) -> ChoiceQuestion:
    """After `speaker`'s line: does someone else at the table answer it, or is it the user's go?

    Every option is described, not just named: with bare names beside a described "the user",
    Jev gave a line pointing the user to Maya ("that's Maya's colour") back to the user.
    """
    s = speaker.name
    options: dict[str, Any] = {
        c.id: {
            "what": (
                f"{c.name}: {s} speaks to or about {c.name}, or points the user to {c.name}: asks "
                f"them something, teases them, says something about them, or says the user's "
                f"question is really theirs ('that's {c.name}'s', 'ask {c.name}')"
            )
        }
        for c in cast
        if c.id != speaker.id
    }
    options[USER] = {
        "what": (
            f"the user: {s} answers the user without pointing them to anyone else, asks the user "
            "something, or just agrees, thanks or wraps up"
        )
    }
    return ChoiceQuestion(
        instructions={
            "question": (
                f"{s} just said `latest` out loud, with the user and everyone at the table "
                "listening. Who speaks next: someone else at the table, or the user?"
            ),
            "clues": [
                "Pointing the user to someone ('that's Maya's colour', 'ask Theo', 'Edith knows') "
                "hands that person the floor, even while answering the user.",
                "Someone teased, asked or talked about gets to answer when there's something for "
                "them to say.",
                "A passing mention in a line that isn't about them, with nothing for them to add, "
                "needs no answer.",
            ],
        },
        options=options,
    )


def closed_question(speaker: Character) -> YesNoQuestion:
    """After `speaker`'s line: has the characters' exchange run its course?"""
    s = speaker.name
    return YesNoQuestion(
        instructions=(
            f"The characters at the table have said `character_turns_since_user_spoke` lines "
            f"since the user last spoke, the latest being {s}'s (`latest`). Has this exchange "
            "run its course, so the conversation should go back to the user?"
        ),
        yes=(
            "the latest line adds nothing new for anyone to answer: it agrees, thanks, wraps "
            "up, or repeats the same kind of joke or jab as before; or four or more lines have "
            "gone by"
        ),
        no=(
            "the exchange is still building: the latest line asks someone something, or makes a "
            "new joke, jab or claim someone would want to answer, and fewer than four lines have "
            "gone by"
        ),
    )


# --- Asking Jev -------------------------------------------------------------------------------


@dataclass
class Reading:
    """One answer from Jev, as the debug panel shows it."""

    kind: str  # "preview" (words still being spoken), "route" (a user turn) or "reply" (a line)
    speaker: str  # whose words were read: USER, or a character for a reply
    heard: str
    choice: str | None
    probabilities: dict[str, float] = field(default_factory=dict)  # the choice: each id, GROUP
    included: dict[str, float] = field(default_factory=dict)  # each id: one of those asked?
    closed: float | None = None  # a reply read: has the characters' exchange run its course?
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

    def addressed(self, character: str) -> float:
        """How likely `character` is being talked to: alone, or as one of a group."""
        return self.p(character) + self.p(GROUP) * self.included.get(character, 0.0)

    def group(self, order: Sequence[str]) -> list[str]:
        """Who answers a group turn: everyone Jev is sure is included, most surely asked first,
        or, when fewer than two are, everyone at the table in `order`."""
        members = [c for c in order if self.included.get(c, 0.0) >= INCLUDED_FLOOR]
        if len(members) < 2:
            return list(order)
        return sorted(members, key=lambda c: -self.included.get(c, 0.0))

    def to_message(self) -> dict[str, Any]:
        def rounded(values: dict[str, float]) -> dict[str, float]:
            return {k: round(v, 4) for k, v in values.items()}

        return {
            "type": "jev",
            "kind": self.kind,
            "speaker": self.speaker,
            "heard": self.heard,
            "choice": self.choice,
            "probabilities": rounded(self.probabilities),
            "included": rounded(self.included),
            "addressed": {
                c: round(self.addressed(c), 4)
                for c in (self.included or [k for k in self.probabilities if k != USER])
            },
            # Who answers: the one chosen, or the group in the order they will speak.
            "members": (self.group(list(self.included)) if self.choice == GROUP else [self.choice])
            if self.choice
            else [],
            "closed": None if self.closed is None else round(self.closed, 4),
            "ms": round(self.ms),
            "cached": self.cached,
            "state": self.state,
            "error": self.error,
            "raw": rounded(self.raw) if self.raw else None,
            "favoured": self.favoured,
            "weight": self.weight,
            "at": time.time(),
        }


class Referee:
    """Asks Jev who the user is talking to (the choice and every inclusion, in one request), and,
    after a character's line, whether someone else should answer it.

    Answers are cached by question and state, and a request already in flight is shared, so a read
    made early (while the user is still speaking, or while a line is still playing) answers the
    final one when nothing has changed.
    """

    def __init__(self, classifier: BaseClassifier, cast: Sequence[Character]) -> None:
        self._classifier = classifier
        self._questions: dict[str, dict[str, ClassifierQuestion]] = {
            ADDRESSEE: {
                ADDRESSEE: addressee_question(cast),
                **{f"{INCLUDED}{c.id}": included_question(c) for c in cast},
            },
            **{
                f"{REPLY}{c.id}": {NEXT: reply_question(c, cast), CLOSED: closed_question(c)}
                for c in cast
            },
        }
        self._cache: OrderedDict[str, asyncio.Task[dict[str, ClassifierResult]]] = OrderedDict()
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
        last_addressed: Sequence[str] = (),
        kind: str = "route",
    ) -> Reading:
        state = transcript.for_jev(history, Line(USER, normalize(heard)), last_addressed)
        reading = Reading(kind, USER, normalize(heard), None, state=state)
        results = await self._ask(reading, ADDRESSEE)
        if results is not None:
            choice = results[ADDRESSEE]
            assert isinstance(choice, ChoiceResult)
            reading.choice = choice.choice
            reading.probabilities = dict(choice.probabilities)
            for name, result in results.items():
                if name.startswith(INCLUDED) and isinstance(result, YesNoResult):
                    reading.included[name.removeprefix(INCLUDED)] = result.probability
        return reading

    async def reply(
        self, transcript: Transcript, history: Sequence[Line], speaker: str, line: str, run: int
    ) -> Reading:
        """After `speaker`'s `line`: who, if anyone, answers it (`USER`: nobody, the user's go).
        `run` is how many character turns there have been since the user last spoke."""
        state = transcript.for_jev(history, Line(speaker, normalize(line)), run=run)
        reading = Reading("reply", speaker, normalize(line), None, state=state)
        results = await self._ask(reading, f"{REPLY}{speaker}")
        if results is not None:
            choice, closed = results[NEXT], results[CLOSED]
            assert isinstance(choice, ChoiceResult) and isinstance(closed, YesNoResult)
            reading.choice = choice.choice
            reading.probabilities = dict(choice.probabilities)
            reading.closed = closed.probability
        return reading

    async def _ask(self, reading: Reading, questions: str) -> dict[str, ClassifierResult] | None:
        """Ask one set of questions about `reading.state`, sharing the cache; fills in the
        reading's timing, and its error if Jev couldn't answer."""
        started = time.perf_counter()
        key = json.dumps([questions, reading.state], sort_keys=True)
        task = self._cache.get(key)
        reading.cached = task is not None and task.done()
        if task is None:
            task = asyncio.create_task(
                self._classifier.ask(reading.state, self._questions[questions])
            )
            task.add_done_callback(lambda t, key=key: self._forget_failure(key, t))
            self._cache[key] = task
            while len(self._cache) > JEV_CACHE_SIZE:
                self._cache.popitem(last=False)
            self.asked += 1
        else:
            self._cache.move_to_end(key)
            self.cached += 1
        try:
            # Shielded: a caller that gives up leaves the answer to the cache.
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 — a failed read is shown, never fatal
            reading.error = f"{type(error).__name__}: {error}"
            return None
        finally:
            reading.ms = 0.0 if reading.cached else (time.perf_counter() - started) * 1000

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
    """A recency prior on Jev's choice: `favoured` (whoever the user spoke to last, a character
    or GROUP) counts `weight` times as likely before the words are weighed, then the choice is
    renormalised. Jev's probabilities are calibrated, so a light weight only settles near-ties:
    a name, a question about what someone said, or a correction outweighs it."""
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
    reason: str  # "addressed", "group", "welcome", "reply", "continue" or "fallback"
    note: str | None = None


def group_cues(
    members: Sequence[str], transcript: Transcript, reason: str, first: str, next_: str
) -> list[Cue]:
    """One turn each, in order; each is told who was asked and who has answered already."""
    labels = [transcript.label(m) for m in members]
    who = "everyone at the table" if len(members) == len(transcript.cast) else names(labels)
    cues = []
    for i, member in enumerate(members):
        note = first if i == 0 else next_
        done, have = names(labels[:i]), "has" if i == 1 else "have"
        cues.append(Cue(member, reason, note.format(who=who, done=done, have=have)))
    return cues


def plan_route(reading: Reading, transcript: Transcript) -> list[Cue]:
    """The turns a user's line starts: the one character it was said to, or each of a group in
    turn. Without an answer from Jev, whoever spoke last carries on."""
    ids = list(transcript.cast)
    if reading.choice == GROUP:
        members = reading.group(ids)
        return group_cues(members, transcript, "group", NOTE_GROUP_FIRST, NOTE_GROUP_NEXT)
    if reading.choice in ids:
        # Talking to someone who didn't just speak: tell them so, or a correction such as
        # "no, I was asking the other one" reads to them as not meant for them.
        last = transcript.last_character()
        switch = last is not None and last != reading.choice
        note = NOTE_SWITCH.format(other=transcript.label(last)) if switch and last else None
        return [Cue(reading.choice, "addressed", note)]
    return [Cue(transcript.last_character() or ids[0], "fallback")]


def plan_welcome(transcript: Transcript) -> list[Cue]:
    """Everyone says hello in turn, with their favourite colour."""
    everyone = list(transcript.cast)
    return group_cues(everyone, transcript, "welcome", NOTE_WELCOME_FIRST, NOTE_WELCOME_NEXT)


def plan_reply(
    reading: Reading, transcript: Transcript, floor: float, close_floor: float, run: int
) -> tuple[Cue | None, str]:
    """Someone answering a character's line, if Jev reads it as theirs to answer and the exchange
    hasn't run its course, and why: "reply", or why the floor goes back to the user ("user":
    Jev's choice; "unsure": the one it leant to was under `floor`; "closed"; "error"). `run` is
    the lines since the user spoke: the first of them (a character answering the user) always
    gets its comeback, and only after that can Jev close the exchange."""
    who = reading.choice
    if reading.error or who is None:
        return None, "error"
    if who == USER or who not in transcript.cast:
        return None, "user"
    if reading.p(who) < floor:
        return None, "unsure"
    if run > 1 and reading.closed is not None and reading.closed >= close_floor:
        return None, "closed"
    note = NOTE_REPLY.format(other=transcript.label(reading.speaker))
    return Cue(who, "reply", note), "reply"
