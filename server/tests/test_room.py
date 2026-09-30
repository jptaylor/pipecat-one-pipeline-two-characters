"""The room's logic offline: each character's view, Jev's state, routing, groups, recency and the
cache."""

from __future__ import annotations

import asyncio

import pytest
from pipecat.classifiers.base_classifier import (
    BaseClassifier,
    ChoiceQuestion,
    ChoiceResult,
    ClassifierResult,
    YesNoResult,
)

from config import load_cast
from room import (
    GROUP,
    NOTE_SWITCH,
    USER,
    Reading,
    Referee,
    Transcript,
    plan_reply,
    plan_route,
    plan_welcome,
    weigh,
)

CAST = load_cast()
IDS = [c.id for c in CAST]
MAYA, THEO, JUNO, OTTO = IDS[:4]


@pytest.fixture
def transcript() -> Transcript:
    t = Transcript(CAST)
    t.add(MAYA, "Hi, I'm Maya, and I love green.")
    t.add(OTTO, "Otto here. Blue, like the sea.")
    t.add(USER, "What should I cook tonight?")
    t.add(THEO, "Something simple.")
    return t


def reading(
    choice: str, probabilities: dict[str, float], included: dict[str, float] | None = None
) -> Reading:
    return Reading("route", USER, "…", choice, probabilities, included or {})


def test_each_character_sees_their_own_lines_as_replies(transcript: Transcript) -> None:
    theo = transcript.view(THEO)
    assert [m["role"] for m in theo] == ["user", "assistant", "user"]
    assert theo[0]["content"] == (
        "[Maya] Hi, I'm Maya, and I love green.\n[Otto] Otto here. Blue, like the sea."
        "\n[User] What should I cook tonight?"
    )
    assert theo[-1]["content"] == "[Note: Carry on.]"
    # Her own opening line needs a user turn before it.
    maya = transcript.view(MAYA, "Say hi.")
    assert [m["role"] for m in maya] == ["user", "assistant", "user"]
    assert maya[-1]["content"].endswith("[Theo] Something simple.\n[Note: Say hi.]")


def test_a_line_cut_off_says_so(transcript: Transcript) -> None:
    transcript.add(JUNO, "Well, I think", interrupted=True)
    assert transcript.view(JUNO)[-2]["content"] == "Well, I think…"
    assert transcript.view(THEO)[-1]["content"].endswith("[Juno] Well, I think (cut off)")


def test_jev_sees_the_conversation_and_who_was_spoken_to(transcript: Transcript) -> None:
    state = transcript.for_jev(transcript.lines, transcript.lines[-1], [OTTO])
    assert state["user_last_spoke_to"] == "Otto"
    assert "Otto here. Blue, like the sea." in [line["said"] for line in state["conversation"]]
    assert list(state)[-1] == "latest"
    pair = transcript.for_jev([], transcript.lines[0], [MAYA, THEO])
    assert pair["user_last_spoke_to"] == "Maya and Theo"
    assert transcript.for_jev([], transcript.lines[0], IDS)["user_last_spoke_to"] == "everyone"
    assert "user_last_spoke_to" not in transcript.for_jev([], transcript.lines[0])


def test_a_light_recency_prior_only_settles_near_ties() -> None:
    torn = weigh(reading(MAYA, {MAYA: 0.48, THEO: 0.44, GROUP: 0.08}), THEO, 1.2)
    assert torn.choice == THEO
    assert torn.raw == {MAYA: 0.48, THEO: 0.44, GROUP: 0.08}
    assert sum(torn.probabilities.values()) == pytest.approx(1.0)
    leaning = weigh(reading(MAYA, {MAYA: 0.6, THEO: 0.35, GROUP: 0.05}), THEO, 1.2)
    assert leaning.choice == MAYA
    unchanged = reading(MAYA, {MAYA: 0.6, THEO: 0.4})
    assert weigh(unchanged, None, 1.2) is unchanged
    assert weigh(unchanged, THEO, 1.0) is unchanged


def test_a_route_to_someone_new_tells_them(transcript: Transcript) -> None:
    [cue] = plan_route(reading(MAYA, {MAYA: 0.9}), transcript)
    assert (cue.speaker, cue.note) == (MAYA, NOTE_SWITCH.format(other="Theo"))
    [cue] = plan_route(reading(THEO, {THEO: 0.9}), transcript)
    assert (cue.speaker, cue.note) == (THEO, None)


def test_a_group_answers_in_turn_most_surely_asked_first(transcript: Transcript) -> None:
    asked = {c: 0.1 for c in IDS} | {JUNO: 0.8, OTTO: 0.95}
    first, second = plan_route(reading(GROUP, {GROUP: 0.9}, asked), transcript)
    assert [first.speaker, second.speaker] == [OTTO, JUNO]
    assert first.note and "said that to Otto and Juno. You answer first" in first.note
    assert second.note and "Otto has already replied. It's your turn now" in second.note


def test_a_group_nobody_is_sure_of_is_everyone(transcript: Transcript) -> None:
    cues = plan_route(reading(GROUP, {GROUP: 1.0}, {MAYA: 0.6}), transcript)
    assert [c.speaker for c in cues] == IDS
    assert "everyone at the table" in (cues[0].note or "")


def test_everyone_says_hello_with_their_favourite_colour(transcript: Transcript) -> None:
    cues = plan_welcome(transcript)
    assert [c.speaker for c in cues] == IDS
    assert all("favourite colour" in (c.note or "") for c in cues)
    assert "Maya and Theo have already" in (cues[2].note or "")


def test_without_an_answer_the_last_speaker_carries_on(transcript: Transcript) -> None:
    [cue] = plan_route(Reading("route", USER, "…", None, error="timeout"), transcript)
    assert (cue.speaker, cue.reason) == (THEO, "fallback")


def reply(speaker: str, choice: str, p: float, closed: float) -> Reading:
    odds = {choice: p, USER: 1 - p} if choice != USER else {USER: p}
    return Reading("reply", speaker, "…", choice, odds, closed=closed)


def test_a_character_answers_when_jev_reads_the_line_as_theirs(transcript: Transcript) -> None:
    cue, why = plan_reply(reply(OTTO, THEO, 0.8, 0.2), transcript, 0.5, 0.5, run=2)
    assert cue is not None and (cue.speaker, cue.reason, why) == (THEO, "reply", "reply")
    assert "Otto just spoke to you or about you" in (cue.note or "")
    # Not sure enough, or the user's turn: nobody answers, and it says why.
    assert plan_reply(reply(OTTO, THEO, 0.45, 0.2), transcript, 0.5, 0.5, run=2) == (
        None,
        "unsure",
    )
    assert plan_reply(reply(OTTO, USER, 0.9, 0.2), transcript, 0.5, 0.5, run=2) == (None, "user")


def test_jev_closes_an_exchange_but_never_before_the_first_comeback(
    transcript: Transcript,
) -> None:
    closed = reply(OTTO, THEO, 0.9, 0.8)
    assert plan_reply(closed, transcript, 0.5, 0.5, run=3) == (None, "closed")
    # The first line after the user's (a character answering them) always gets its comeback.
    cue, why = plan_reply(closed, transcript, 0.5, 0.5, run=1)
    assert cue is not None and why == "reply"


def test_jev_is_told_how_long_the_characters_have_talked(transcript: Transcript) -> None:
    assert transcript.run(transcript.lines) == 1
    transcript.add(MAYA, "Oh, Theo.")
    assert transcript.run(transcript.lines) == 2
    state = transcript.for_jev(transcript.lines[:-1], transcript.lines[-1], run=2)
    assert state["character_turns_since_user_spoke"] == 2
    assert "user_last_spoke_to" not in state


class CountingClassifier(BaseClassifier):
    """Answers every question the same way, slowly, and counts the requests."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def _ask(self, state, questions):
        self.calls += 1
        await asyncio.sleep(0.05)
        results: dict[str, ClassifierResult] = {}
        for name, question in questions.items():
            if isinstance(question, ChoiceQuestion) and name == "next":
                odds = {option: 0.0 for option in question.options} | {JUNO: 0.7, USER: 0.3}
                results[name] = ChoiceResult(choice=JUNO, probabilities=odds, confidence=0.5)
            elif isinstance(question, ChoiceQuestion):
                odds = {option: 0.0 for option in question.options} | {GROUP: 0.9, THEO: 0.1}
                results[name] = ChoiceResult(choice=GROUP, probabilities=odds, confidence=0.8)
            elif name == "closed":
                results[name] = YesNoResult(probability=0.25)
            else:
                p = 0.9 if name.endswith((THEO, OTTO)) else 0.1
                results[name] = YesNoResult(probability=p)
        return results, None


async def test_one_request_answers_the_choice_and_every_inclusion(
    transcript: Transcript,
) -> None:
    classifier = CountingClassifier()
    referee = Referee(classifier, CAST)
    history = list(transcript.lines)
    # The partial's read is still in flight when the turn ends: the final one shares it.
    preview = asyncio.create_task(
        referee.addressee(transcript, history, "and then", kind="preview")
    )
    await asyncio.sleep(0)
    final = await referee.addressee(transcript, history, "and  then")
    assert (await preview).choice == final.choice == GROUP
    assert classifier.calls == 1
    assert final.included == {c: 0.9 if c in (THEO, OTTO) else 0.1 for c in IDS}
    assert final.group(IDS) == [THEO, OTTO]
    assert final.addressed(THEO) == pytest.approx(0.1 + 0.9 * 0.9)
    # Once it has landed, the same words cost nothing.
    again = await referee.addressee(transcript, history, "and then")
    assert again.cached and again.ms == 0 and classifier.calls == 1
    # Who the user spoke to last is part of the question, so it is asked afresh.
    await referee.addressee(transcript, history, "and then", last_addressed=[THEO])
    assert classifier.calls == 2


async def test_a_reply_read_asks_who_answers_and_whether_it_is_closed(
    transcript: Transcript,
) -> None:
    classifier = CountingClassifier()
    referee = Referee(classifier, CAST)
    history = list(transcript.lines)
    # Asked while the line is still playing, then again when it ends: one request.
    early = asyncio.create_task(referee.reply(transcript, history, THEO, "Juno, sing!", 1))
    await asyncio.sleep(0)
    late = await referee.reply(transcript, history, THEO, "Juno,  sing!", 1)
    assert (await early).choice == late.choice == JUNO
    assert late.closed == 0.25 and classifier.calls == 1
    # The speaker is never an option: they don't answer themselves.
    assert THEO not in late.probabilities and USER in late.probabilities
