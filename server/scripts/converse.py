"""The room in text, on the live Jev and PhoneLLM: no audio, no pipeline, the same decisions.

    uv run python scripts/converse.py                 # type as the user; an empty line quits
    uv run python scripts/converse.py --script FILE   # one user line per line of FILE
    uv run python scripts/converse.py --no-welcome

Each user line is routed by Jev to one character, or to a group who answer in turn, each with
their own view of the whole conversation. After each line, Jev decides whether someone else at the
table answers it, until it hands the floor back to the user, as `director.py` does in the bot.
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
    CLOSE_FLOOR,
    LLM_TEMPERATURE,
    LLM_TOKENS,
    MAX_BOUNCES,
    RECENCY_WEIGHT,
    REPLY_FLOOR,
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
    plan_reply,
    plan_route,
    plan_welcome,
    weigh,
)

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def show(reading: Reading) -> None:
    top = sorted(reading.probabilities.items(), key=lambda kv: -kv[1])[:3]
    odds = "  ".join(f"{k} {v:.2f}" for k, v in top)
    if reading.choice == "group":
        asked = [f"{k} {v:.2f}" for k, v in reading.included.items() if v >= 0.5]
        odds += "  | asked: " + " ".join(asked)
    if reading.raw:
        top = sorted(reading.raw.items(), key=lambda kv: -kv[1])[:3]
        raw = "  ".join(f"{k} {v:.2f}" for k, v in top)
        odds += f"  (jev: {raw}; x{reading.weight:g} {reading.favoured})"
    if reading.closed is not None:
        odds += f"  | closed {reading.closed:.2f}"
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
    prompts = {c.id: prompt(c, cast) for c in cast}
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
        queue, bounces = list(cues), 0
        while queue:
            cue = queue.pop(0)
            history = list(transcript.lines)
            text = await speak(cue)
            transcript.add(cue.speaker, text)
            if queue or bounces >= MAX_BOUNCES:
                continue
            run = transcript.run(history) + 1
            reading = await referee.reply(transcript, history, cue.speaker, text, run)
            show(reading)
            reply, why = plan_reply(reading, transcript, REPLY_FLOOR, CLOSE_FLOOR, run)
            print(f"{DIM}  → {reply.speaker if reply else 'user'} ({why}){RESET}")
            if reply is not None:
                queue.append(reply)
                bounces += 1

    lines = args.script.read_text().splitlines() if args.script else None
    print(f"{DIM}PhoneLLM {settings.llm_model}, Jev {referee.model}{RESET}")
    await services.warm_llm(settings)
    favoured: str | None = None  # Jev's last choice: a character, or a group
    addressed: list[str] = []  # everyone it cued
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
            reading = weigh(reading, favoured, RECENCY_WEIGHT)
            show(reading)
            cues = plan_route(reading, transcript)
            if reading.choice:
                favoured, addressed = reading.choice, [cue.speaker for cue in cues]
            await play(cues)
    finally:
        await referee.close()
        await jev.close()
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
