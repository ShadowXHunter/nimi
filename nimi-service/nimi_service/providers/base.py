"""Messaging-provider interface. Everything outside `providers/` talks only to these types.

To add a provider (e.g. Slack): create providers/<name>/ with a class implementing `Provider`, register it in
providers/__init__.py, and register channels with `python -m nimi_service add-channel <name> <channel> <label>`.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, Optional

SENDER_TYPES = ("human", "bot")

# Unicode modifiers that don't change an emoji's meaning (skin tones, variation selector).
_EMOJI_MODIFIERS = {chr(c) for c in range(0x1F3FB, 0x1F400)} | {"️"}


def normalize_unicode_emoji(value: str) -> str:
    return "".join(ch for ch in value if ch not in _EMOJI_MODIFIERS)


@dataclass
class Message:
    """A message in provider-neutral form."""
    external_id: str                       # the provider's ID for the message
    channel_external_id: str               # the provider's ID for the channel / space
    thread_external_id: Optional[str]
    created_at: datetime                   # timezone-aware UTC
    last_update_at: Optional[datetime]
    sender_external_id: Optional[str]
    sender_type: Optional[str]             # "human" | "bot" | None (unknown)
    sender_display_name: Optional[str]
    is_thread_reply: bool
    rendered_text: str                     # human-readable rendering (stored; given to the skill)
    raw: dict                              # original payload (stored; used to re-normalize on reparse)
    reaction_summary: dict = field(default_factory=dict)   # {emoji_key: count}
    parse_text: str = ""                   # plain text for the parser (text + cards/blocks, no headers)
    title_hint: str = ""                   # the first meaningful line (e.g. card / header title)


@dataclass
class Reaction:
    external_id: str
    message_external_id: str
    emoji_key: str                         # normalized unicode emoji, or a provider custom-emoji ID
    emoji_label: Optional[str] = None      # e.g. ":party-parrot:" for custom emojis
    user_external_id: Optional[str] = None
    user_type: Optional[str] = None        # "human" | "bot" | None


class Provider(ABC):
    """One messaging service (Google Chat, Slack, Teams, ...)."""

    name: str

    # --- IDs / normalization (pure, no API calls) ---
    @abstractmethod
    def normalize_channel_id(self, value: str) -> str:
        """Accept user input (e.g. "AAAAxxxxxxx") and return the canonical channel ID."""

    @abstractmethod
    def normalize(self, raw: dict) -> Message:
        """Turn a raw API payload into a Message (also used to re-normalize stored payloads)."""

    # --- API ---
    @abstractmethod
    def iter_messages(self, channel_external_id: str, after: datetime,
                      before: Optional[datetime] = None) -> Iterator[Message]:
        """All messages (top-level + thread replies) created in (after, before), oldest first."""

    @abstractmethod
    def list_reactions(self, message_external_id: str) -> list:
        """Current reactions on a message, as Reaction objects."""

    @abstractmethod
    def post_reply(self, channel_external_id: str, thread_external_id: str, text: str) -> str:
        """Post `text` as a reply in a thread. Returns the new message's external ID."""

    def post_webhook(self, webhook_url: str, text: str, thread_key: str = None) -> tuple:
        """Post `text` to an incoming webhook of this provider as a new top-level message (or, with a
        `thread_key`, into the thread of earlier posts with that key). Returns (message external ID, thread
        external ID). Optional: providers without incoming
        webhooks don't support NIMI_POST_TARGET=webhook."""
        raise NotImplementedError(f"provider {self.name!r} does not support posting to a webhook")

    # --- presentation ---
    @abstractmethod
    def message_link(self, message_external_id: str) -> str:
        """A URL that opens the message in the provider's UI."""

    def bold(self, text: str) -> str:
        """Inline bold in the provider's markup (override if it differs)."""
        return f"*{text}*"

    def link(self, url: str, label: Optional[str] = None) -> str:
        """A link in the provider's markup (override if it differs)."""
        return f"{label} ({url})" if label else url

    def italic(self, text: str) -> str:
        """Inline italic in the provider's markup (override if it differs)."""
        return f"_{text}_"

    def render(self, text: str) -> str:
        """Convert NIMI's simple Markdown (**bold**, _italic_, `code`, ``` blocks, "- " bullets) to the
        provider's markup. Default: unchanged."""
        return text
