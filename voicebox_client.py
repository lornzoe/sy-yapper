"""Thin client for the local Voicebox REST API.

https://github.com/jamiepine/voicebox -- runs locally, default port 17493.
"""
from __future__ import annotations

import logging

import requests

from errors import ConfigError

logger = logging.getLogger(__name__)


class VoiceboxClient:
    def __init__(
        self, base_url: str, language: str = "en", engine: str = "chatterbox_turbo", timeout: float = 60.0
    ):
        self.base_url = base_url.rstrip("/")
        self.language = language
        self.engine = engine
        self.timeout = timeout

    def list_profiles(self) -> list[dict]:
        resp = requests.get(f"{self.base_url}/profiles", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def resolve_profile_id(self, name_or_id: str) -> str:
        try:
            profiles = self.list_profiles()
        except requests.RequestException as exc:
            raise ConfigError(
                f"Couldn't reach Voicebox at {self.base_url} ({exc}). "
                "Is the Voicebox app running?"
            ) from exc

        if not profiles:
            raise ConfigError(
                "Voicebox has no voice profiles yet. Open the Voicebox app and create one first."
            )

        if name_or_id:
            for p in profiles:
                if p.get("id") == name_or_id or p.get("name", "").lower() == name_or_id.lower():
                    return p["id"]
            names = ", ".join(f"{p['name']} ({p['id']})" for p in profiles)
            raise ConfigError(f"No Voicebox profile matches '{name_or_id}'. Available: {names}")

        logger.warning(
            "VOICEBOX_PROFILE not set; defaulting to first profile '%s'. Available profiles: %s",
            profiles[0]["name"],
            ", ".join(p["name"] for p in profiles),
        )
        return profiles[0]["id"]

    def synthesize(self, text: str, profile_id: str) -> bytes:
        resp = requests.post(
            f"{self.base_url}/generate/stream",
            json={
                "profile_id": profile_id,
                "text": text,
                "language": self.language,
                "engine": self.engine,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.content
