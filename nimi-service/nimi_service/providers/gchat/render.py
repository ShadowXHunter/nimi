"""Human-readable rendering of a Chat message: header, text, card contents, attachments, IDs."""
import html
import re

# Covers both cardsV2 and the legacy `cards` format (section "header", keyValue "content").
CARD_TEXT_KEYS = ("title", "subtitle", "header", "topLabel", "text", "content", "bottomLabel")


def html_to_text(value: str) -> str:
    value = re.sub(r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>', r"\2 (\1)", value)
    value = re.sub(r"<br\s*/?>", "\n", value)
    return html.unescape(re.sub(r"<[^>]+>", "", value))


def card_lines(node):
    """Walk a cards/cardsV2 structure and yield its readable text and links."""
    if isinstance(node, dict):
        for key in CARD_TEXT_KEYS:
            if isinstance(node.get(key), str) and node[key].strip():
                yield html_to_text(node[key])
        if "openLink" in node:
            yield f"link: {node['openLink'].get('url')}"
        for key, value in node.items():
            if key != "openLink" and not isinstance(value, str):
                yield from card_lines(value)
    elif isinstance(node, list):
        for item in node:
            yield from card_lines(item)


def first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip().strip("`").strip()
        if line:
            return line
    return ""


def message_cards(m: dict) -> list:
    return list(card_lines([m.get("cards", []), m.get("cardsV2", [])]))


def render_message(m: dict) -> str:
    sender = m.get("sender", {}) or {}
    who = sender.get("displayName") or sender.get("name", "unknown")
    lines = [f"[{m.get('createTime')}] {who}"]
    if m.get("text"):
        lines.append(f"  {m['text']}")
    cards = message_cards(m)
    if cards:
        lines.append("  card:")
        lines.extend("    " + c.replace("\n", "\n    ") for c in cards)
    for a in m.get("attachment", []) or []:
        lines.append(f"  attachment: {a.get('contentName')} ({a.get('contentType')})")
    if not m.get("text") and not cards and not m.get("attachment"):
        lines.append("  (empty)")
    lines.append(f"  message: {m['name']}")
    lines.append(f"  thread:  {(m.get('thread') or {}).get('name', '-')}")
    return "\n".join(lines)
