"""One row per setting: the single source of truth for the GUI form, the
Config <-> widget mapping, and what gets written back to .env.

Adding a setting later means adding one row here, not touching four files.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from config import DEFAULT_IGNORE_USERS, _bool, _float, _int, _list, _str

Kind = Literal["text", "secret", "bool", "int", "float", "choice", "list", "device", "profile"]


@dataclass(frozen=True)
class FieldSpec:
    attr: str           # Config attribute name
    env_key: str        # .env key
    kind: Kind
    label: str
    default: Any
    tab: str
    help: str = ""
    restart: bool = False   # changing this requires restarting the bot task
    choices: tuple[str, ...] = ()
    minimum: int = 0
    maximum: int = 100000

    def parse(self, env: Mapping[str, str]) -> Any:
        if self.kind == "bool":
            return _bool(self.env_key, self.default, env)
        if self.kind == "int":
            return _int(self.env_key, self.default, env)
        if self.kind == "float":
            return _float(self.env_key, self.default, env)
        if self.kind == "list":
            return _list(self.env_key, self.default, env)
        return _str(self.env_key, self.default, env)

    def serialize(self, value: Any) -> str:
        if self.kind == "bool":
            return "true" if value else "false"
        if self.kind == "list":
            return ",".join(value)
        return str(value)


ENGINES = (
    "chatterbox_turbo",
    "chatterbox",
    "tada",
    "qwen",
    "qwen_custom_voice",
    "luxtts",
    "kokoro",
)

# Engines that can only speak preset voices -- pairing one with a cloned
# profile always fails, so the UI blocks Start on that combination.
PRESET_ONLY_ENGINES = frozenset({"kokoro"})

# Which model(s) back each engine, for the "(not downloaded)" annotation.
ENGINE_MODELS: dict[str, tuple[str, ...]] = {
    "qwen": ("qwen-tts-0.6B", "qwen-tts-1.7B"),
    "qwen_custom_voice": ("qwen-custom-voice-0.6B", "qwen-custom-voice-1.7B"),
    "luxtts": ("luxtts",),
    "chatterbox": ("chatterbox-tts",),
    "chatterbox_turbo": ("chatterbox-turbo",),
    "tada": ("tada-1b", "tada-3b-ml"),
    "kokoro": ("kokoro",),
}

LANGUAGES = (
    "en", "zh", "ja", "ko", "de", "fr", "ru", "pt", "es", "it", "he", "ar",
    "da", "el", "fi", "hi", "ms", "nl", "no", "pl", "sv", "sw", "tr",
)

FIELD_SPECS: tuple[FieldSpec, ...] = (
    # --- Twitch ---
    FieldSpec("twitch_channel", "TWITCH_CHANNEL", "text", "Channel", "", "Twitch",
              "Channel name without the #. This is the chat that gets read aloud.", restart=True),
    FieldSpec("twitch_nick", "TWITCH_NICK", "text", "Nick (optional)", "", "Twitch",
              "Only needed if anonymous read-only login gets flaky.", restart=True),
    FieldSpec("twitch_oauth_token", "TWITCH_OAUTH_TOKEN", "secret", "OAuth token (optional)", "", "Twitch",
              "chat:read scope. Leave blank to read anonymously.", restart=True),
    FieldSpec("ignore_users", "IGNORE_USERS", "list", "Ignore these users", DEFAULT_IGNORE_USERS, "Twitch",
              "One username per line. Their messages are never read aloud."),

    # --- Voice ---
    FieldSpec("voicebox_base_url", "VOICEBOX_BASE_URL", "text", "Voicebox URL",
              "http://127.0.0.1:17493", "Voice",
              "Where the Voicebox app serves its local API.", restart=True),
    FieldSpec("voicebox_profile", "VOICEBOX_PROFILE", "profile", "Voice profile", "", "Voice",
              "Which Voicebox voice speaks. Blank uses the first profile found.", restart=True),
    FieldSpec("voicebox_engine", "VOICEBOX_ENGINE", "choice", "Engine", "chatterbox_turbo", "Voice",
              "TTS engine. Must suit the profile -- some engines speak preset voices only "
              "and cannot run a cloned voice.",
              restart=True, choices=ENGINES),
    FieldSpec("voicebox_language", "VOICEBOX_LANGUAGE", "choice", "Language", "en", "Voice",
              "Language passed to the synthesizer.", restart=True, choices=LANGUAGES),
    FieldSpec("voicebox_timeout", "VOICEBOX_TIMEOUT", "float", "Timeout (s)", 180.0, "Voice",
              "The cold model load happens inside the first request, so this needs headroom.",
              restart=True, minimum=10, maximum=1800),
    FieldSpec("warmup", "WARMUP", "bool", "Warm up on start", True, "Voice",
              "Synthesize a throwaway phrase at startup so the first real message is not "
              "stuck behind the model load.", restart=True),
    FieldSpec("warmup_text", "WARMUP_TEXT", "text", "Warmup phrase", "warming up", "Voice",
              "Never played out loud -- the audio is discarded.", restart=True),

    # --- Audio ---
    FieldSpec("audio_output_device", "AUDIO_OUTPUT_DEVICE", "device", "Output device", "", "Audio",
              "Where speech is played. Blank auto-detects a VB-Cable input.", restart=True),

    # --- Behavior ---
    FieldSpec("speak_username", "SPEAK_USERNAME", "bool", "Speak the username", True, "Behavior",
              'Prefixes each message with "<name> says".'),
    FieldSpec("strip_emotes", "STRIP_EMOTES", "bool", "Strip Twitch emotes", True, "Behavior",
              "Removes emotes using the exact ranges Twitch tags each message with."),
    FieldSpec("strip_emoji", "STRIP_EMOJI", "bool", "Strip unicode emoji", True, "Behavior",
              "Removes emoji from the spoken text."),
    FieldSpec("max_message_length", "MAX_MESSAGE_LENGTH", "int", "Max message length", 300, "Behavior",
              "Longer messages get truncated before being spoken.", minimum=20, maximum=5000),
    FieldSpec("max_queue_size", "MAX_QUEUE_SIZE", "int", "Max queue size", 50, "Behavior",
              "How many messages can back up before new ones are dropped.",
              restart=True, minimum=1, maximum=1000),
)

SPECS_BY_ATTR = {spec.attr: spec for spec in FIELD_SPECS}

# Settings consumed once at construction time -- changing them needs a restart
# of the bot task, because mutating the live Config would silently do nothing.
RESTART_FIELDS = frozenset(spec.attr for spec in FIELD_SPECS if spec.restart)

TABS = ("Twitch", "Voice", "Audio", "Behavior")


def engine_available(engine: str, downloaded: set[str]) -> bool:
    """True if any model backing `engine` is downloaded."""
    return any(m in downloaded for m in ENGINE_MODELS.get(engine, ()))


def validate_profile_engine(profile: dict | None, engine: str) -> str | None:
    """Return a warning if this profile cannot run on this engine."""
    if not profile:
        return None
    if profile.get("voice_type") == "cloned" and engine in PRESET_ONLY_ENGINES:
        return (f"'{engine}' supports preset voices only -- a cloned profile cannot run "
                "on it. Pick chatterbox_turbo, or choose a preset profile.")
    return None
