"""The session's Pipecat services and clients, built from `Settings`."""

from __future__ import annotations

import time

from loguru import logger
from openai import AsyncOpenAI
from pipecat.classifiers.jev.client import JevClient
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService

from config import (
    DEEPGRAM_MODEL,
    JEV_TIMEOUT_S,
    LLM_REPETITION_PENALTY,
    LLM_TEMPERATURE,
    LLM_TOKENS,
    LLM_WARM_TIMEOUT_S,
    Character,
    Settings,
)

#: The request body PhoneLLM needs beyond the OpenAI parameters.
EXTRA_BODY = {
    "chat_template_kwargs": {"enable_thinking": False},
    "repetition_penalty": LLM_REPETITION_PENALTY,
}


class PhoneLLMService(OpenAILLMService):
    """PhoneLLM on its OpenAI-compatible endpoint. Its chat template has no developer role."""

    supports_developer_role = False


def llm(settings: Settings, system_prompt: str) -> PhoneLLMService:
    """One character's LLM: PhoneLLM, with the character's prompt as its system prompt."""
    return PhoneLLMService(
        api_key=settings.phonellm_api_key,
        base_url=settings.phonellm_url,
        settings=OpenAILLMService.Settings(
            model=settings.llm_model,
            system_instruction=system_prompt,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_TOKENS,
            extra={"extra_body": EXTRA_BODY},
        ),
    )


def stt(settings: Settings, cast: tuple[Character, Character]) -> DeepgramSTTService:
    return DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        settings=DeepgramSTTService.Settings(model=DEEPGRAM_MODEL, keyterm=[c.name for c in cast]),
    )


def tts(settings: Settings, voice: str) -> CartesiaTTSService:
    """One TTS for both characters: the bridge switches its voice to whoever is speaking."""
    return CartesiaTTSService(
        api_key=settings.cartesia_api_key,
        settings=CartesiaTTSService.Settings(voice=voice),
    )


def jev(settings: Settings) -> JevClient:
    return JevClient(api_key=settings.jev_api_key, model=settings.jev_model, timeout=JEV_TIMEOUT_S)


async def warm_llm(settings: Settings) -> None:
    """Wake the hosted PhoneLLM endpoint, which scales to zero: a cold start takes minutes."""
    client = AsyncOpenAI(
        api_key=settings.phonellm_api_key,
        base_url=settings.phonellm_url,
        timeout=LLM_WARM_TIMEOUT_S,
        max_retries=0,
    )
    started = time.perf_counter()
    try:
        await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": "Say hi."}],
            max_tokens=4,
            temperature=0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        logger.info(f"PhoneLLM: warm ({time.perf_counter() - started:.1f} s)")
    except Exception as error:  # noqa: BLE001 — the session goes on; its first turn will wait
        logger.warning(
            f"PhoneLLM: warm-up failed after {time.perf_counter() - started:.1f} s: {error}"
        )
    finally:
        await client.close()
