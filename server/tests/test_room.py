"""The room's logic offline: each character's view, Jev's state, routing, recency and the cache."""

from __future__ import annotations

import asyncio

import pytest
from pipecat.classifiers.base_classifier import BaseClassifier, ChoiceResult, ClassifierResult

from config import load_cast
from room import (
    BOTH,
    NOTE_BOTH_FIRST,
    NOTE_SWITCH,
    USER,
    Reading,
    Referee,
    Transcript,
    plan_route,
    plan_welcome,
    weigh,
)

MAYA, THEO = (c.id for c in load_cast())


@pytest.fixture
def transcript() -> Transcript:
    t = Transcript(load_cast())
    t.add(MAYA, "Hi, I'm Maya.")
    t.add(THEO, "And I'm Theo.")
    t.add(USER, "What should I cook tonight?")
    t.add(THEO, "Something simple.")
    return t


def reading(choice: str, **probabilities: float) -> Reading:
    return Reading("route", USER, "…", choice, probabilities)


def test_each_character_sees_their_own_lines_as_replies(transcript: Transcript) -> None:
    theo = transcript.view(THEO)
    assert [m["role"] for m in theo] == ["user", "assistant", "user", "assistant", "user"]
    assert theo[0]["content"] == "[Maya] Hi, I'm Maya."
    assert theo[-1]["content"] == "[Note: Carry on.]"

    maya = transcript.view(MAYA, "Say hi.")
    # Her own opening line needs a user turn before it; everyone else's lines merge into one.
    assert [m["role"] for m in maya] == ["user", "assistant", "user"]
    assert maya[-1]["content"] == (
        "[Theo] And I'm Theo.\n[User] What should I cook tonight?\n[Theo] Something simple."
        "\n[Note: Say hi.]"
    )


def test_a_line_cut_off_says_so(transcript: Transcript) -> None:
    transcript.add(MAYA, "Well, I think", interrupted=True)
    assert transcript.view(MAYA)[-2]["content"] == "Well, I think…"
    assert transcript.view(THEO)[-1]["content"].endswith("[Maya] Well, I think (cut off)")


def test_jev_is_told_who_the_user_spoke_to_last(transcript: Transcript) -> None:
    state = transcript.for_jev(transcript.lines, transcript.lines[-1], THEO)
    assert state["user_last_spoke_to"] == "Theo"
    assert list(state)[-1] == "latest"
    assert transcript.for_jev([], transcript.lines[0], BOTH)["user_last_spoke_to"] == (
        "both Maya and Theo"
    )
    assert "user_last_spoke_to" not in transcript.for_jev([], transcript.lines[0])


def test_recency_tips_a_torn_reading_but_not_a_clear_one() -> None:
    torn = weigh(reading(MAYA, **{MAYA: 0.5, THEO: 0.45, BOTH: 0.05}), THEO, 2.0)
    assert torn.choice == THEO
    assert torn.raw == {MAYA: 0.5, THEO: 0.45, BOTH: 0.05}
    assert sum(torn.probabilities.values()) == pytest.approx(1.0)

    clear = weigh(reading(MAYA, **{MAYA: 0.97, THEO: 0.02, BOTH: 0.01}), THEO, 2.0)
    assert clear.choice == MAYA

    unchanged = reading(MAYA, **{MAYA: 0.6, THEO: 0.4})
    assert weigh(unchanged, None, 2.0) is unchanged
    assert weigh(unchanged, THEO, 1.0) is unchanged


def test_a_route_to_the_other_character_tells_them(transcript: Transcript) -> None:
    [cue] = plan_route(reading(MAYA, **{MAYA: 0.9}), transcript)
    assert (cue.speaker, cue.note) == (MAYA, NOTE_SWITCH.format(other="Theo"))
    [cue] = plan_route(reading(THEO, **{THEO: 0.9}), transcript)
    assert (cue.speaker, cue.note) == (THEO, None)


def test_both_answer_in_turn_led_by_whoever_jev_leant_to(transcript: Transcript) -> None:
    first, second = plan_route(reading(BOTH, **{MAYA: 0.3, THEO: 0.05, BOTH: 0.65}), transcript)
    assert (first.speaker, first.note) == (MAYA, NOTE_BOTH_FIRST)
    assert second.speaker == THEO and "Maya has answered" in (second.note or "")
    # Too close to call: whoever didn't speak last goes first.
    first, _ = plan_route(reading(BOTH, **{MAYA: 0.1, THEO: 0.1, BOTH: 0.8}), transcript)
    assert first.speaker == MAYA


def test_without_an_answer_the_last_speaker_carries_on(transcript: Transcript) -> None:
    [cue] = plan_route(Reading("route", USER, "…", None, error="timeout"), transcript)
    assert (cue.speaker, cue.reason) == (THEO, "fallback")
    assert [c.speaker for c in plan_welcome(transcript)] == [MAYA, THEO]


class CountingClassifier(BaseClassifier):
    """Answers every question the same way, slowly, and counts what it was asked."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def _ask(self, state, questions):
        self.calls += 1
        await asyncio.sleep(0.05)
        options = {MAYA: 0.1, THEO: 0.8, BOTH: 0.1}
        results: dict[str, ClassifierResult] = {
            name: ChoiceResult(choice=THEO, probabilities=options, confidence=0.7)
            for name in questions
        }
        return results, None


async def test_the_frontrun_answers_the_final_read(transcript: Transcript) -> None:
    classifier = CountingClassifier()
    referee = Referee(classifier, load_cast())
    history = list(transcript.lines)
    # The partial's read is still in flight when the turn ends: the final one shares it.
    preview = asyncio.create_task(
        referee.addressee(transcript, history, "and then", kind="preview")
    )
    await asyncio.sleep(0)
    final = await referee.addressee(transcript, history, "and  then")
    assert (await preview).choice == final.choice == THEO
    assert classifier.calls == 1
    # Once it has landed, the same words cost nothing.
    again = await referee.addressee(transcript, history, "and then")
    assert again.cached and again.ms == 0 and classifier.calls == 1
    # Who the user spoke to last is part of the question, so it is asked afresh.
    await referee.addressee(transcript, history, "and then", last_addressed=THEO)
    assert classifier.calls == 2
