"""Configuration loaded from environment variables / a .env file."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields

from dotenv import load_dotenv

from errors import ConfigError

load_dotenv()


def _bool(name: str, default: bool, env: Mapping[str, str] | None = None) -> bool:
    val = (env if env is not None else os.environ).get(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int, env: Mapping[str, str] | None = None) -> int:
    val = (env if env is not None else os.environ).get(name)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val)
    except ValueError:
        # A typo here must not crash at import time, before any UI exists.
        return default


def _float(name: str, default: float, env: Mapping[str, str] | None = None) -> float:
    val = (env if env is not None else os.environ).get(name)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _str(name: str, default: str, env: Mapping[str, str] | None = None) -> str:
    val = (env if env is not None else os.environ).get(name)
    return default if val is None else val


def _list(name: str, default: list[str], env: Mapping[str, str] | None = None) -> list[str]:
    val = (env if env is not None else os.environ).get(name)
    if not val:
        return list(default)
    return [item.strip().lower() for item in val.split(",") if item.strip()]


DEFAULT_IGNORE_USERS = ["nightbot", "streamelements", "streamlabs", "moobot", "wizebot"]


@dataclass
class Config:
    # Twitch
    twitch_channel: str = field(default_factory=lambda: _str("TWITCH_CHANNEL", ""))
    twitch_nick: str = field(default_factory=lambda: _str("TWITCH_NICK", ""))
    twitch_oauth_token: str = field(default_factory=lambda: _str("TWITCH_OAUTH_TOKEN", ""))

    # Voicebox
    voicebox_base_url: str = field(
        default_factory=lambda: _str("VOICEBOX_BASE_URL", "http://127.0.0.1:17493")
    )
    voicebox_profile: str = field(default_factory=lambda: _str("VOICEBOX_PROFILE", ""))
    voicebox_engine: str = field(default_factory=lambda: _str("VOICEBOX_ENGINE", "chatterbox_turbo"))
    voicebox_timeout: float = field(default_factory=lambda: _float("VOICEBOX_TIMEOUT", 180.0))
    voicebox_language: str = field(default_factory=lambda: _str("VOICEBOX_LANGUAGE", "en"))

    # Audio output
    audio_output_device: str = field(default_factory=lambda: _str("AUDIO_OUTPUT_DEVICE", ""))

    # Warmup: synthesize a throwaway phrase at startup so the first real chat
    # message does not pay the model-load cost (~26s for chatterbox_turbo on CPU).
    warmup: bool = field(default_factory=lambda: _bool("WARMUP", True))
    warmup_text: str = field(default_factory=lambda: _str("WARMUP_TEXT", "warming up"))

    # Emote handling
    strip_emotes: bool = field(default_factory=lambda: _bool("STRIP_EMOTES", True))
    strip_emoji: bool = field(default_factory=lambda: _bool("STRIP_EMOJI", True))

    # Behavior
    speak_username: bool = field(default_factory=lambda: _bool("SPEAK_USERNAME", True))
    max_message_length: int = field(default_factory=lambda: _int("MAX_MESSAGE_LENGTH", 300))
    max_queue_size: int = field(default_factory=lambda: _int("MAX_QUEUE_SIZE", 50))
    ignore_users: list[str] = field(
        default_factory=lambda: _list("IGNORE_USERS", DEFAULT_IGNORE_USERS)
    )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        """Build a Config from a mapping (defaults to os.environ).

        The GUI uses this to construct a Config from widget values without
        touching os.environ, so an unsaved edit never leaks into the process.
        """
        if env is None:
            return cls()
        from settings_schema import FIELD_SPECS

        kwargs = {spec.attr: spec.parse(env) for spec in FIELD_SPECS}
        return cls(**kwargs)

    def copy(self) -> "Config":
        return Config(**{f.name: getattr(self, f.name) for f in fields(self)})

    def validate(self) -> None:
        if not self.twitch_channel:
            raise ConfigError(
                "TWITCH_CHANNEL is not set. Copy .env.example to .env and fill it in."
            )


CONFIG = Config()
