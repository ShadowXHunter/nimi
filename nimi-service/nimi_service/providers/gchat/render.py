"""Human-readable rendering of a Chat message: header, text, card contents, attachments, IDs."""
import html
import re

# Covers both cardsV2 and the legacy `cards` format (section "header", keyValue "content").
CARD_TEXT_KEYS = ("title", "subtitle", "header", "topLabel", "text", "content", "bottomLabel")


def html_to_text(value: str, bold: bool = False) -> str:
    """Card HTML -> plain text. `bold` (for display): keep bold as Markdown bold (`**default**`) instead of
    dropping it; senders often mark a label that way (a series name, a field name), and only they know what
    it means (deployments can say so in a profile's message_notes)."""
    if bold:
        value = re.sub(r"<(b|strong)>(.*?)</\1>", _bold, value, flags=re.S | re.I)
    value = re.sub(r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>', r"\2 (\1)", value)
    value = re.sub(r"<br\s*/?>", "\n", value)
    return html.unescape(re.sub(r"<[^>]+>", "", value))


def _bold(m) -> str:
    """One <b>…</b> span as **…**; a span with no visible text (e.g. "<b> </b>") stays unmarked."""
    inner = m.group(2)
    visible = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()     # entities too: "&nbsp;" is blank
    return f"**{inner.strip()}**" if visible else inner


def _pair(node: dict):
    """A legacy keyValue widget (topLabel + content) as one "label: value" line (bold kept, as for every
    displayed text), or None."""
    label, value = node.get("topLabel"), node.get("content")
    if not (isinstance(label, str) and label.strip() and isinstance(value, str) and value.strip()):
        return None
    line = f"{html_to_text(label, bold=True).strip()}: {html_to_text(value, bold=True).strip()}"
    bottom = node.get("bottomLabel")
    return line + (f" ({html_to_text(bottom, bold=True).strip()})"
                   if isinstance(bottom, str) and bottom.strip() else "")


def card_lines(node, display: bool = False):
    """Walk a cards/cardsV2 structure and yield its readable text and links. `display` (rendered_text): a
    keyValue widget (a label and its value, by the card schema) is one "label: value" line, and bold is kept
    as Markdown bold. Without it, each text is its own plain line, as parsing has always seen them:
    signatures are built from these lines, so they must not change. Nothing here guesses what a sender meant."""
    if isinstance(node, dict):
        pair = _pair(node) if display else None
        if pair:
            yield pair
        else:
            for key in CARD_TEXT_KEYS:
                if isinstance(node.get(key), str) and node[key].strip():
                    yield html_to_text(node[key], bold=display)
        if "openLink" in node:
            yield f"link: {node['openLink'].get('url')}"
        for key, value in node.items():
            if key != "openLink" and not isinstance(value, str):
                yield from card_lines(value, display)
    elif isinstance(node, list):
        for item in node:
            yield from card_lines(item, display)


def first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip().strip("`").strip()
        if line:
            return line
    return ""


def message_cards(m: dict, display: bool = False) -> list:
    return list(card_lines([m.get("cards", []), m.get("cardsV2", [])], display))


def render_message(m: dict) -> str:
    sender = m.get("sender", {}) or {}
    who = sender.get("displayName") or sender.get("name", "unknown")
    lines = [f"[{m.get('createTime')}] {who}"]
    if m.get("text"):
        lines.append(f"  {m['text']}")
    cards = message_cards(m, display=True)
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
