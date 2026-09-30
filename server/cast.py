"""The two characters: their prompts, and `CharacterWorker`, the LLM worker that speaks as one.

A character's turn arrives with its activation: the room activates the character who should speak
and passes that character's view of the whole conversation (`TurnArgs`), and the worker runs its
LLM on it at once. The turn travels inside the activation rather than as a frame over the bridge
because the bus drops frames sent to a worker that isn't active yet, and activation is itself a bus
message: a frame sent right behind it could overtake it. The others stay inactive, so they hear
nothing until their own turn, when they are handed the conversation in full.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from loguru import logger
from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext, LLMContextMessage
from pipecat.services.llm_service import LLMService
from pipecat.workers.base_worker import WorkerActivationArgs
from pipecat.workers.llm import LLMWorker

from config import Character

PROMPTS = Path(__file__).resolve().parent / "prompts"
COUNTS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}


def prompt(me: Character, cast: Sequence[Character]) -> str:
    """`me`'s system prompt: `prompts/character.md`, with `prompts/<id>.md` as their persona and
    everyone else at the table listed."""
    persona_file = PROMPTS / f"{me.id}.md"
    persona = persona_file.read_text().strip() if persona_file.exists() else ""
    others = [c for c in cast if c.id != me.id]
    values = {
        "name": me.name,
        "role": me.role.lower(),
        "tagline": me.tagline,
        "colour": me.colour,
        "count": COUNTS.get(len(others), str(len(others))),
        "others": "\n".join(f"- {c.brief()}" for c in others),
        "example": others[0].name,
    }
    values["persona"] = fill(persona, values)
    return fill((PROMPTS / "character.md").read_text().strip(), values)


def fill(template: str, values: dict[str, str]) -> str:
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: values[m.group(1)], template)


@dataclass
class TurnArgs(WorkerActivationArgs):
    """A character's turn: its view of the conversation, ending with what it should answer."""

    messages: list[dict[str, Any]] | None = None


class CharacterWorker(LLMWorker):
    """Speaks as one character, one turn per activation."""

    def __init__(self, character: Character, llm: LLMService[Any]) -> None:
        super().__init__(character.id, llm=llm, bridged=())
        self.character = character

    async def on_activated(self, args: dict | None) -> None:
        # LLMWorker would append `messages` to a context; the turn is a whole context instead.
        await super().on_activated(None)
        turn = TurnArgs.from_dict(args) if args else TurnArgs()
        if turn.messages:
            logger.debug(f"{self.character.name}: speaking ({len(turn.messages)} messages)")
            context = LLMContext(messages=cast(list[LLMContextMessage], turn.messages))
            await self.queue_frame(LLMContextFrame(context=context))

    async def on_deactivated(self) -> None:
        await super().on_deactivated()
        logger.debug(f"{self.character.name}: listening")
