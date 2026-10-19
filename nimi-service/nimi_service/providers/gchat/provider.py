"""Google Chat provider (user OAuth token from `nimi_service gchat-login`)."""
import re
from datetime import datetime, timezone
from typing import Iterator, Optional

from ..base import Message, Provider, Reaction, normalize_unicode_emoji
from .client import ChatClient
from .render import first_line, message_cards, render_message

MESSAGE_ID_RE = re.compile(r"^spaces/([^/]+)/messages/([^.]+)\.(.+)$")
SENDER_TYPES = {"HUMAN": "human", "BOT": "bot"}


def rfc3339(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_ts(value):
    """Chat returns e.g. 2026-01-15T10:04:31.123456Z (sometimes nanoseconds); trim to microseconds."""
    if not value:
        return None
    value = value.rstrip("Z")
    if "." in value:
        main, frac = value.split(".", 1)
        value = f"{main}.{frac[:6]}"
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def emoji_key(emoji: dict) -> tuple:
    """(key, label) for a Chat unicode or custom emoji."""
    if emoji.get("unicode"):
        return normalize_unicode_emoji(emoji["unicode"]), None
    custom = emoji.get("customEmoji") or {}
    return custom.get("name") or f"customEmojis/{custom.get('uid')}", custom.get("emojiName")


def reaction_summary(summaries) -> dict:
    """emojiReactionSummaries -> {emoji_key: count}."""
    return {emoji_key(x.get("emoji") or {})[0]: x.get("reactionCount", 0) for x in (summaries or [])}


class GChatProvider(Provider):
    name = "gchat"

    def __init__(self, settings):
        self.client = ChatClient(settings.gchat_token_file)

    def normalize_channel_id(self, value: str) -> str:
        return value if value.startswith("spaces/") else f"spaces/{value}"

    def normalize(self, raw: dict) -> Message:
        sender = raw.get("sender", {}) or {}
        text = raw.get("text") or ""
        cards = message_cards(raw)
        return Message(
            external_id=raw["name"],
            channel_external_id="/".join(raw["name"].split("/")[:2]),
            thread_external_id=(raw.get("thread") or {}).get("name"),
            created_at=parse_ts(raw["createTime"]),
            last_update_at=parse_ts(raw.get("lastUpdateTime")),
            sender_external_id=sender.get("name"),
            sender_type=SENDER_TYPES.get(sender.get("type")),
            sender_display_name=sender.get("displayName"),
            is_thread_reply=bool(raw.get("threadReply")),
            rendered_text=render_message(raw),
            raw=raw,
            reaction_summary=reaction_summary(raw.get("emojiReactionSummaries")),
            parse_text="\n".join([text, *cards]),
            title_hint=first_line(text) or first_line("\n".join(cards)),
        )

    def iter_messages(self, channel_external_id: str, after: datetime,
                      before: Optional[datetime] = None) -> Iterator[Message]:
        for raw in self.client.iter_messages(channel_external_id, rfc3339(after),
                                             rfc3339(before) if before else None):
            yield self.normalize(raw)

    def list_reactions(self, message_external_id: str) -> list:
        out = []
        for r in self.client.list_reactions(message_external_id):
            key, label = emoji_key(r.get("emoji") or {})
            user = r.get("user") or {}
            out.append(Reaction(external_id=r["name"], message_external_id=message_external_id, emoji_key=key,
                                emoji_label=label, user_external_id=user.get("name"),
                                user_type=SENDER_TYPES.get(user.get("type"))))
        return out

    def post_reply(self, channel_external_id: str, thread_external_id: str, text: str) -> str:
        return self.client.reply_in_thread(channel_external_id, thread_external_id, text)

    def render(self, text: str) -> str:
        """Google Chat markup: *bold*, _italic_, `code`, ``` blocks; no headings or tables. Converts **bold**
        to *bold*, "- " bullets to "• ", and "# heading" lines to bold lines, leaving code blocks untouched."""
        out, in_code = [], False
        for line in (text or "").split("\n"):
            if line.lstrip().startswith("```"):
                in_code = not in_code
                out.append(line)
                continue
            if not in_code:
                line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
                line = re.sub(r"^(\s*)[-*] (?=\S)", r"\1• ", line)
                line = re.sub(r"^#{1,6}\s+(.+)$", lambda m: f"*{m.group(1).strip('*')}*", line)
            out.append(line)
        return "\n".join(out)

    def link(self, url: str, label=None) -> str:
        return f"<{url}|{label}>" if label else url

    def post_webhook(self, webhook_url: str, text: str, thread_key: str = None) -> tuple:
        return self.client.post_webhook(webhook_url, text, thread_key)

    def message_link(self, message_external_id: str) -> str:
        """spaces/<space>/messages/<thread>.<msg> -> https://chat.google.com/room/<space>/<thread>/<msg>?cls=10"""
        m = MESSAGE_ID_RE.match(message_external_id or "")
        if not m:
            return message_external_id
        space, thread, msg = m.groups()
        return f"https://chat.google.com/room/{space}/{thread}/{msg}?cls=10"
