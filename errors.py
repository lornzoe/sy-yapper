"""Shared exception types.

Lives in its own module so `config`, `audio_player` and `voicebox_client` can
all raise the same error without importing each other.
"""
from __future__ import annotations


class ConfigError(Exception):
    """A setting is missing, unresolvable, or points at something unreachable.

    The CLI turns this into a friendly `SystemExit`; the GUI shows it in a
    banner and re-enables Start.
    """
