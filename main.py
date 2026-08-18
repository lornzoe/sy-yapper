"""Reads Twitch chat and speaks each message through Voicebox TTS, out to the
configured audio device (typically a VB-Audio Cable input, so OBS/Discord/etc.
can pick it up as if it were a microphone).

Running this opens the control panel and starts the bot. Use --cli to run
headless in the terminal instead.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

from audio_player import AudioPlayer, resolve_device
from config import CONFIG, Config
from errors import ConfigError
from twitch_chat import EMOJI_RE, ChatMessage, TwitchChatReader, strip_emotes
from voicebox_client import VoiceboxClient

logger = logging.getLogger("twitch-voicebox-tts")


def clean_message_text(msg: ChatMessage, cfg: Config) -> str | None:
    """The message body as it will be spoken, without the username prefix.

    Returns None if nothing is left (an emote-only message). Kept separate from
    build_spoken_text so the log can name the speaker without duplicating the
    prefix when SPEAK_USERNAME is on.

    Emote removal happens first: the ranges in the IRCv3 `emotes` tag are
    indexed against the raw message, so any trimming has to come after it.
    """
    text = msg.text
    if cfg.strip_emotes:
        text = strip_emotes(text, msg.emotes)
    if text.startswith("\x01ACTION") and text.endswith("\x01"):
        text = text[8:-1]  # strip /me CTCP framing
    if cfg.strip_emoji:
        text = EMOJI_RE.sub(" ", text)
    text = " ".join(text.split())
    if not text:
        return None  # emote-only message
    if len(text) > cfg.max_message_length:
        text = text[: cfg.max_message_length].rsplit(" ", 1)[0] + "..."
    return text


def build_spoken_text(msg: ChatMessage, cfg: Config) -> str | None:
    """The exact line handed to the synthesizer, or None if nothing is left."""
    text = clean_message_text(msg, cfg)
    if text is None:
        return None
    if cfg.speak_username:
        return f"{msg.username} says {text}"
    return text


async def consumer(
    queue: "asyncio.Queue[ChatMessage]",
    tts: VoiceboxClient,
    profile_id: str,
    player: AudioPlayer,
    cfg: Config,
    on_event=None,
) -> None:
    while True:
        msg = await queue.get()
        try:
            body = clean_message_text(msg, cfg)
            if body is None:
                logger.info("Skipping emote-only message from %s", msg.username)
                if on_event:
                    on_event("skipped", msg.username)
                continue
            spoken = f"{msg.username} says {body}" if cfg.speak_username else body
            # The log always names the speaker, even when the username is not
            # spoken aloud -- otherwise there is no way to tell who said what.
            logger.info("[%s said] %s", msg.username, body, extra={"speak": True})
            if on_event:
                on_event("speaking", spoken)
            wav_bytes = await asyncio.to_thread(tts.synthesize, spoken, profile_id)
            await asyncio.to_thread(player.play, wav_bytes)
            if on_event:
                on_event("spoke", spoken)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to speak message from %s", msg.username)
            if on_event:
                on_event("error", msg.username)
        finally:
            queue.task_done()


async def warmup(
    tts: VoiceboxClient, profile_id: str, player: AudioPlayer, cfg: Config, on_event=None
) -> None:
    """Load the TTS model and open the audio device before real messages arrive.

    The synthesized audio is discarded -- nothing is spoken out loud.
    """
    if not cfg.warmup:
        return
    started = time.monotonic()
    logger.info("Warming up %s (first load can take ~30s) ...", cfg.voicebox_engine)
    if on_event:
        on_event("warming", cfg.voicebox_engine)
    try:
        await asyncio.to_thread(tts.synthesize, cfg.warmup_text, profile_id)
        await asyncio.to_thread(player.warmup)
        logger.info("Warmup finished in %.1fs", time.monotonic() - started)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Worth shouting about: if warmup fails, every message is about to fail too.
        logger.warning(
            "Warmup failed after %.1fs (%s); the first message may be slow",
            time.monotonic() - started,
            exc,
            exc_info=True,
        )
        if on_event:
            on_event("warmup_failed", str(exc))


async def producer(
    queue: "asyncio.Queue[ChatMessage]", reader: TwitchChatReader, cfg: Config, on_event=None
) -> None:
    async for msg in reader.messages():
        if msg.username.lower() in cfg.ignore_users:
            continue
        if not msg.text.strip():
            continue
        if queue.full():
            logger.warning("Queue full (%d); dropping message from %s", cfg.max_queue_size, msg.username)
            if on_event:
                on_event("dropped", msg.username)
            continue
        await queue.put(msg)


def build_components(cfg: Config, on_state=None):
    """Resolve everything the bot needs up front. Raises ConfigError if unusable.

    Split out so the GUI can run it on a worker thread and show failures in a
    banner instead of dying.
    """
    cfg.validate()
    tts = VoiceboxClient(
        cfg.voicebox_base_url,
        cfg.voicebox_language,
        cfg.voicebox_engine,
        cfg.voicebox_timeout,
    )
    profile_id = tts.resolve_profile_id(cfg.voicebox_profile)
    player = AudioPlayer(resolve_device(cfg.audio_output_device))
    reader = TwitchChatReader(
        cfg.twitch_channel, cfg.twitch_nick, cfg.twitch_oauth_token, on_state=on_state
    )
    return tts, profile_id, player, reader


async def run_bot(cfg: Config, on_event=None, on_state=None, components=None) -> None:
    """The whole pipeline. Cancelling this coroutine stops everything cleanly."""
    if components is None:
        components = await asyncio.to_thread(build_components, cfg, on_state)
    tts, profile_id, player, reader = components

    queue: "asyncio.Queue[ChatMessage]" = asyncio.Queue(maxsize=cfg.max_queue_size)
    logger.info("Listening to #%s ...", cfg.twitch_channel)

    async def consume_after_warmup() -> None:
        # Chat is read (and queued) during warmup; nothing is spoken until the
        # model is loaded, so no message is lost and none of them stall on it.
        await warmup(tts, profile_id, player, cfg, on_event)
        if on_event:
            on_event("listening", cfg.twitch_channel)
        await consumer(queue, tts, profile_id, player, cfg, on_event)

    # A TaskGroup so one side failing cancels the other, instead of leaving a
    # half-dead bot with a live socket and no consumer.
    async with asyncio.TaskGroup() as tg:
        tg.create_task(producer(queue, reader, cfg, on_event))
        tg.create_task(consume_after_warmup())


async def main(cfg: Config = CONFIG) -> None:
    await run_bot(cfg)


def run_cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        asyncio.run(main())
    except ConfigError as exc:
        raise SystemExit(str(exc))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cli", action="store_true", help="run headless in the terminal, no window"
    )
    parser.add_argument(
        "--no-autostart", action="store_true", help="open the window without starting the bot"
    )
    args = parser.parse_args()

    if args.cli:
        run_cli()
    else:
        # Imported lazily so the CLI path never pulls in Tkinter.
        from gui import run_gui

        run_gui(autostart=not args.no_autostart)
