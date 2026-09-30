"""The room in text, on the live Jev and PhoneLLM: no audio, no pipeline, the same decisions.

    uv run python scripts/converse.py                 # type as the user; an empty line quits
    uv run python scripts/converse.py --script FILE   # one user line per line of FILE
    uv run python scripts/converse.py --no-welcome

Each user line is routed by Jev (`addressee`) to one character, or to both, who answer in turn,
each with its own view of the whole conversation, as `director.py` does in the bot.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from openai import AsyncOpenAI
from pipecat.classifiers.jev.classifier import JevClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services  # noqa: E402
from cast import prompt  # noqa: E402
from config import (  # noqa: E402
    LLM_TEMPERATURE,
    LLM_TOKENS,
    RECENCY_WEIGHT,
    Settings,
    load_cast,
    load_environment,
)
from room import (  # noqa: E402
    USER,
    Cue,
    Reading,
    Referee,
    Transcript,
    plan_route,
    plan_welcome,
    weigh,
)

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def show(reading: Reading) -> None:
    odds = "  ".join(f"{k} {v:.2f}" for k, v in reading.probabilities.items())
    if reading.raw:
        raw = "  ".join(f"{k} {v:.2f}" for k, v in reading.raw.items())
        odds += f"  (jev: {raw}; x{reading.weight:g} {reading.favoured})"
    extra = " (cached)" if reading.cached else ""
    error = f" ERROR {reading.error}" if reading.error else ""
    print(f"{DIM}  jev {reading.kind:<8} {reading.ms:4.0f} ms{extra}  {odds}{error}{RESET}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", type=Path)
    parser.add_argument("--no-welcome", action="store_true")
    args = parser.parse_args()

    load_environment()
    settings = Settings.from_env()
    cast = load_cast()
    names = {c.id: c.name for c in cast}
    prompts = {cast[0].id: prompt(cast[0], cast[1]), cast[1].id: prompt(cast[1], cast[0])}
    transcript = Transcript(cast)
    jev = services.jev(settings)
    referee = Referee(JevClassifier(client=jev), cast)
    client = AsyncOpenAI(api_key=settings.phonellm_api_key, base_url=settings.phonellm_url)

    async def speak(cue: Cue) -> str:
        messages = [{"role": "system", "content": prompts[cue.speaker]}]
        messages += transcript.view(cue.speaker, cue.note)
        started = time.perf_counter()
        reply = await client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,  # type: ignore[arg-type]
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_TOKENS,
            extra_body=services.EXTRA_BODY,
        )
        text = (reply.choices[0].message.content or "").strip()
        ms = (time.perf_counter() - started) * 1000
        print(f"{BOLD}{names[cue.speaker]}{RESET} {DIM}({cue.reason}, {ms:.0f} ms){RESET}: {text}")
        return text

    async def play(cues: list[Cue]) -> None:
        for cue in cues:
            transcript.add(cue.speaker, await speak(cue))

    lines = args.script.read_text().splitlines() if args.script else None
    print(f"{DIM}PhoneLLM {settings.llm_model}, Jev {referee.model}{RESET}")
    await services.warm_llm(settings)
    addressed: str | None = None  # who the user spoke to last
    try:
        if not args.no_welcome:
            await play(plan_welcome(transcript))
        while True:
            if lines is not None:
                if not lines:
                    break
                said = lines.pop(0).strip()
                if not said or said.startswith("#"):
                    continue
                print(f"{BOLD}You{RESET}: {said}")
            else:
                said = input(f"{BOLD}You{RESET}: ").strip()
                if not said:
                    break
            history = list(transcript.lines)
            transcript.add(USER, said)
            reading = await referee.addressee(transcript, history, said, last_addressed=addressed)
            reading = weigh(reading, addressed, RECENCY_WEIGHT)
            show(reading)
            addressed = reading.choice or addressed
            await play(plan_route(reading, transcript))
    finally:
        await referee.close()
        await jev.close()
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
