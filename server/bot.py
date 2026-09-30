"""One conversation, a table of characters. Run with `uv run bot.py -t webrtc` (localhost:7860).

One worker per character, and the room, share one runner and its bus (`director.py` has the
details):

    room   transport → Deepgram → Hearing → user aggregator → Router → CastBridge → Cartesia
           → transport → Playback → assistant aggregator
    maya, theo, …   a character each: PhoneLLM with their prompt, active only on their turns

Jev reads who each user turn is said to (while it is still being spoken, too): one character, or a
group who answer in turn. The director hands each turn over with the whole conversation, and the
bridge switches Cartesia to the speaker's voice. The session ends when the client leaves.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.classifiers.jev.classifier import JevClassifier
from pipecat.frames.frames import InputAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

import services
from cast import CharacterWorker, prompt
from config import ROOM, Settings, load_cast, load_environment
from director import CastBridge, Director
from room import Referee

load_environment()

TRANSPORT_PARAMS = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    settings = Settings.from_env()
    cast = load_cast()
    first = cast[0]
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    warming = asyncio.create_task(services.warm_llm(settings))

    jev = services.jev(settings)
    director = Director(cast, Referee(JevClassifier(client=jev), cast))

    context = LLMContext()
    aggregators = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )
    bridge = CastBridge(
        director,
        voice=first.voice,
        bus=runner.bus,
        worker_name=ROOM,
        exclude_frames=(InputAudioRawFrame,),  # the microphone's audio stays in the room
        name="CastBridge",
    )
    pipeline = Pipeline(
        [
            transport.input(),
            services.stt(settings, cast),
            director.hearing(),
            aggregators.user(),
            director.router(),
            bridge,
            services.tts(settings, first.voice),
            transport.output(),
            director.playback(),
            aggregators.assistant(),
        ]
    )
    room = PipelineWorker(
        pipeline,
        name=ROOM,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )
    director.worker = room

    @aggregators.assistant().event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message: AssistantTurnStoppedMessage):
        director.spawn(director.line_spoken(message.content, message.interrupted), "line")

    # The characters say hello once the client is listening and every worker has started.
    ready: set[str] = set()

    async def ready_for(what: str) -> None:
        ready.add(what)
        if ready == {"client", "workers"}:
            await director.welcome()

    @room.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        await ready_for("client")

    @runner.event_handler("on_ready")
    async def on_ready(runner):
        await ready_for("workers")

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Session: client connected")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Session: client left")
        await runner.cancel()

    logger.info(
        f"Session: {', '.join(c.name for c in cast)} on {settings.llm_model}, "
        f"Jev {settings.jev_model}"
    )
    try:
        await jev.connect()
        await runner.add_workers(
            *(CharacterWorker(c, services.llm(settings, prompt(c, cast))) for c in cast),
            room,
        )
        await runner.run()
    finally:
        warming.cancel()
        await director.close()
        await jev.close()
        referee = director.referee
        logger.info(f"Session: ended (Jev asked {referee.asked}, {referee.cached} from the cache)")


async def bot(runner_args: RunnerArguments) -> None:
    """The dev runner's entry point: one session per client."""
    if not getattr(getattr(runner_args, "cli_args", None), "verbose", 0):
        logger.remove()
        logger.add(sys.stderr, level=os.getenv("BOT_LOG_LEVEL", "INFO").upper())
    transport = await create_transport(runner_args, TRANSPORT_PARAMS)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    # The runner loads dotenv with override=True; explicitly set variables must win.
    configured = dict(os.environ)
    from pipecat.runner.run import main

    os.environ.update(configured)
    settings = Settings.from_env()  # a missing key fails now, not when the first client connects
    # The hosted PhoneLLM scales to zero; start waking it before anyone connects.
    threading.Thread(target=lambda: asyncio.run(services.warm_llm(settings)), daemon=True).start()
    main()
