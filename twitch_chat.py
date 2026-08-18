"""Minimal read-only Twitch chat client (IRC over websockets).

Uses anonymous login by default (no token required) -- this works for reading
any public channel's chat, including your own. Pass an OAuth token + nick
only if the anonymous connection turns out to be unreliable.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Callable
from dataclasses import dataclass

import websockets

logger = logging.getLogger(__name__)

TWITCH_WS_URL = "wss://irc-ws.chat.twitch.tv:443"

# Matches PRIVMSG lines, with or without the IRCv3 tags prefix Twitch adds
# when CAP REQ :twitch.tv/tags is negotiated. The tags carry the `emotes`
# ranges, so they are captured rather than skipped over.
PRIVMSG_RE = re.compile(
    r"^(?:@(?P<tags>\S+)\s+)?:(?P<user>[^!]+)!\S+ PRIVMSG #\S+ :(?P<message>.*)$"
)

# Unicode blocks covering emoji, pictographs, dingbats and regional indicators.
EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # pictographs, symbols, supplemental
    "\U00002600-\U000027BF"  # misc symbols + dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators (flags)
    "\U00002B00-\U00002BFF"  # misc symbols and arrows
    "\uFE0F\u20E3"           # variation selector / keycap
    "]+"
)

# IRCv3 tag values escape these five sequences.
TAG_UNESCAPE = ((r"\:", ";"), (r"\s", " "), (r"\r", ""), (r"\n", ""), ("\\\\", "\\"))


def parse_tags(raw: str) -> dict[str, str]:
    """Parse an IRCv3 tag string (`a=1;b=2`) into a dict, unescaping values."""
    tags: dict[str, str] = {}
    for part in raw.split(";"):
        key, _, val = part.partition("=")
        for escaped, plain in TAG_UNESCAPE:
            val = val.replace(escaped, plain)
        tags[key] = val
    return tags


def strip_emotes(text: str, emotes_tag: str) -> str:
    """Remove Twitch's own emotes from `text` using the ranges in the emotes tag.

    The tag looks like `25:0-4,12-16/1902:6-10` -- emote id, then the character
    spans it occupies. Indexes are code points, which is exactly how Python
    slices strings, so they can be used directly.
    """
    if not emotes_tag:
        return text
    spans: list[tuple[int, int]] = []
    for chunk in emotes_tag.split("/"):
        _, _, ranges = chunk.partition(":")
        for r in ranges.split(","):
            start, _, end = r.partition("-")
            try:
                spans.append((int(start), int(end)))
            except ValueError:
                continue
    if not spans:
        return text
    kept, cursor = [], 0
    for start, end in sorted(spans):
        if start > cursor:
            kept.append(text[cursor:start])
        cursor = max(cursor, end + 1)
    kept.append(text[cursor:])
    return "".join(kept)


@dataclass
class ChatMessage:
    username: str
    text: str
    emotes: str = ""


class TwitchChatReader:
    def __init__(
        self,
        channel: str,
        nick: str = "",
        oauth_token: str = "",
        on_state: "Callable[[str], None] | None" = None,
    ):
        self.channel = channel.lower().lstrip("#")
        self.nick = nick or f"justinfan{random.randint(10000, 99999)}"
        self.oauth_token = oauth_token
        # Lets a UI show real connection state instead of scraping log text.
        self.on_state = on_state

    async def messages(self):
        """Async generator yielding ChatMessage objects, reconnecting on drop."""
        backoff = 1
        while True:
            try:
                async with websockets.connect(TWITCH_WS_URL) as ws:
                    if self.oauth_token:
                        token = (
                            self.oauth_token
                            if self.oauth_token.startswith("oauth:")
                            else f"oauth:{self.oauth_token}"
                        )
                        await ws.send(f"PASS {token}")
                    await ws.send(f"NICK {self.nick}")
                    await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                    await ws.send(f"JOIN #{self.channel}")
                    logger.info("Connected to #%s as %s", self.channel, self.nick)
                    if self.on_state:
                        self.on_state("connected")
                    backoff = 1

                    async for raw in ws:
                        for line in raw.split("\r\n"):
                            if not line:
                                continue
                            if line.startswith("PING"):
                                await ws.send(line.replace("PING", "PONG", 1))
                                continue
                            match = PRIVMSG_RE.match(line)
                            if match:
                                raw_tags = match.group("tags")
                                tags = parse_tags(raw_tags) if raw_tags else {}
                                yield ChatMessage(
                                    username=match.group("user"),
                                    text=match.group("message"),
                                    emotes=tags.get("emotes", ""),
                                )
            except (websockets.exceptions.ConnectionClosed, OSError) as exc:
                logger.warning(
                    "Twitch chat connection lost (%s); reconnecting in %ss", exc, backoff
                )
                if self.on_state:
                    self.on_state("reconnecting")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
