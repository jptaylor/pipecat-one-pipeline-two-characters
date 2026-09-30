"""Settings from the repo-root `.env` (explicitly set variables win), the cast from the repo-root
`characters.json`, and every tunable the room uses."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# --- Models -----------------------------------------------------------------------------------

JEV_MODEL = "jev-1.13.0"  # the floors below were tried on this version
DEEPGRAM_MODEL = "nova-3-general"

# --- Workers ----------------------------------------------------------------------------------

ROOM = "room"  # the main worker: transport, STT, the director, TTS

# --- The director -----------------------------------------------------------------------------

# Lines of the conversation Jev is shown with each question: enough to keep the introductions in
# view, so "so who liked blue?" can find who said it.
HISTORY_LINES = 30
# Whoever the user spoke to last counts this many times as likely before Jev's reading of the new
# words (1 turns it off). Kept light: Jev already sees who that was, so this only settles
# near-ties, and a name, a question about what someone said, or a correction always wins.
RECENCY_WEIGHT = 1.2
INCLUDED_FLOOR = 0.5  # a group turn: how sure Jev must be that a character is one of those asked
# After a character's line, Jev decides whether someone else answers it (this sure, at least), and
# whether the exchange has run its course. The characters bounce off each other until Jev hands
# the floor back to the user, or, as a backstop only, this many replies in a row.
REPLY_FLOOR = 0.5
CLOSE_FLOOR = 0.5  # ...unless Jev is this sure the exchange has run its course
MAX_BOUNCES = 6
MAX_CAST = 8
JEV_TIMEOUT_S = 3.0
JEV_CACHE_SIZE = 256  # questions asked while the user spoke make the final one free
ROUTE_WAIT_S = 1.5  # a route never waits longer than this on Jev: then the last speaker answers
HANDOVER_WAIT_S = 10.0  # the second of two answers waits at most this long for the first to play

# --- The characters' LLM ----------------------------------------------------------------------

LLM_TOKENS = 200
LLM_TEMPERATURE = 0.0  # PhoneLLM must run at temperature 0: never change this
# Also counts the prompt's tokens (vLLM): without it, a character at temperature 0 soon answers
# with a line it already said, word for word.
LLM_REPETITION_PENALTY = float(os.getenv("LLM_REPETITION_PENALTY") or 1.1)  # 1.0 turns it off
LLM_WARM_TIMEOUT_S = 240.0  # the hosted endpoint scales to zero; a cold start takes minutes


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    role: str
    tagline: str
    voice: str  # Cartesia voice id
    topics: str  # what they know best: Jev routes a question with no name by it
    colour: str  # their favourite colour (and their tint on screen)

    def brief(self) -> str:
        return f"{self.name}, the {self.role.lower()}: {self.tagline}"


def load_cast() -> tuple[Character, ...]:
    """The characters, from `characters.json` (the client reads the same file)."""
    fields = ("id", "name", "role", "tagline", "voice", "topics", "colour")
    cast = tuple(
        Character(**{k: entry[k] for k in fields})
        for entry in json.loads((ROOT / "characters.json").read_text())
    )
    ids = [c.id for c in cast]
    if not 2 <= len(cast) <= MAX_CAST or len(set(ids)) != len(ids):
        raise ValueError(f"characters.json must list 2 to {MAX_CAST} characters, ids distinct")
    return cast


def load_environment() -> None:
    load_dotenv(ROOT / ".env", override=False)


def require(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"set {name} in the repo-root .env")
    return value


@dataclass(frozen=True)
class Settings:
    phonellm_api_key: str
    phonellm_url: str
    llm_model: str
    jev_api_key: str
    jev_model: str
    cartesia_api_key: str
    deepgram_api_key: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            phonellm_api_key=require("PHONELLM_API_KEY"),
            phonellm_url=require("PHONELLM_BASE_URL"),
            llm_model=require("LLM_MODEL"),
            jev_api_key=require("JEV_API_KEY"),
            jev_model=(os.getenv("JEV_MODEL") or JEV_MODEL).strip(),
            cartesia_api_key=require("CARTESIA_API_KEY"),
            deepgram_api_key=require("DEEPGRAM_API_KEY"),
        )
