"""The chat message of an RCA: the skill fills in fixed fields, NIMI owns the layout.

The skill writes its chat message as JSON with fixed keys between `RCA_CHAT` / `END_RCA_CHAT` (keys and writing
rules: nimi-skill/.claude/skills/rca/templates/chat.md). This module validates it and renders the sections in one
fixed layout (names, order, bullets) as simple Markdown; the provider converts the markup. The skill decides the
content and keeps it within its length budget; NIMI never rewrites or trims text inside a section.

  {"title": "...", "context": "main subjects, environment / owner — one line" | null, "tldr": "...",
   "cause": {"certainty": "confirmed" | "likely", "points": ["..."]},
   "verify": [{"where": "..." | null, "text": "...", "command": "..." | null, "outcomes": ["result → meaning"]}],
   "now": ["..."], "do_not": ["..."],
   "fixes": [{"owner": "...", "items": ["..."]}],
   "also": ["..."],
   "related": [{"ref": "msg:<id>" | "rca:<id>", "label": "...", "why": "..."}]}

title, tldr and cause are required; the other sections are left out when empty. Text fields may contain
`code` spans and citations ([label](msg:/rca:/code:...)), which rca.resolve_refs turns into links.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Optional

CHAT_RE = re.compile(r"^RCA_CHAT[ \t]*\n(.*?)\n[ \t]*END_RCA_CHAT[ \t]*$", re.S | re.M)
CHAT_OPEN_RE = re.compile(r"^RCA_CHAT[ \t]*\n(.*?)(?=^RCA_FIELDS[ \t]*$|\Z)", re.S | re.M)   # no END marker
FENCE_RE = re.compile(r"^```[\w-]*[ \t]*\n(.*?)\n?```[ \t]*$", re.S)

KEYS = ("title", "context", "tldr", "cause", "verify", "now", "do_not", "fixes", "also", "related")
TITLE_MAX = 120
MAX_ITEMS = 8                     # per list: a runaway list is a broken message, not a long one
RELATED_MAX = 3
CERTAINTY_LABEL = {"confirmed": "confirmed", "likely": "likely, to be confirmed"}
REF_RE = re.compile(r"^(msg|rca):(\d{1,12})$")
HEADING_LINE_RE = re.compile(r"^\s*#{1,6}\s+\S.*$", re.M)
# Sentences about earlier RCAs ("This agrees with RCA #2."): on-call needs the finding, not NIMI's history.
INTERNAL_SENTENCE_RE = re.compile(r"[^.!?\n]*\bRCA\s*#\s*\d+[^.!?\n]*(?:[.!?](?=\s|$)|$)", re.I)

# Sections in display order; the ones NIMI may drop (whole) when a message can't fit the chat limit, in order.
OPTIONAL_DROP_ORDER = ("related", "also", "fixes", "verify")


@dataclass
class Chat:
    title: str
    tldr: str
    cause: list
    certainty: str = ""
    context: str = ""
    verify: list = field(default_factory=list)       # [(where, text, command, [outcomes])], "" when not given
    now: list = field(default_factory=list)
    do_not: list = field(default_factory=list)
    fixes: list = field(default_factory=list)        # [(owner, [items])]
    also: list = field(default_factory=list)
    related: list = field(default_factory=list)      # [(kind, id, label, why)]


def block(report: str) -> Optional[str]:
    """The raw RCA_CHAT block (without a surrounding code fence), or None. Without END_RCA_CHAT the block ends
    at RCA_FIELDS (or the end of the output)."""
    m = CHAT_RE.search(report or "") or CHAT_OPEN_RE.search(report or "")
    text = m.group(1).strip() if m else ""
    fm = FENCE_RE.match(text)
    return (fm.group(1).strip() if fm else text) or None


def _text(value, problems: list, where: str, keep_refs: bool = False) -> str:
    """One text value: a string, without Markdown headings, line breaks or (outside Related) RCA chatter."""
    if value is None:
        return ""
    if not isinstance(value, str):
        problems.append(f"{where}: not text")
        return ""
    text = HEADING_LINE_RE.sub("", value)
    if not keep_refs:
        text = INTERNAL_SENTENCE_RE.sub("", text)
    return " ".join(text.split())


def _items(value, problems: list, where: str) -> list:
    """A list of texts (a single text is accepted as a one-item list)."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        problems.append(f"{where}: not a list")
        return []
    out = [t for t in (_text(v, problems, where) for v in value) if t]
    if len(out) > MAX_ITEMS:
        problems.append(f"{where}: {len(out)} items, kept {MAX_ITEMS}")
    return out[:MAX_ITEMS]


def _title(value, problems: list) -> str:
    title = re.sub(r"(?i)^(?:rca\s*[:\-–—]\s*)+", "", _text(value, problems, "title")).strip("*_ ")  # NIMI adds "RCA: "
    if len(title) > TITLE_MAX:
        problems.append(f"title: {len(title)} characters")
        title = title[:TITLE_MAX].rsplit(" ", 1)[0].rstrip(" ,;:-–—") + "…"
    return title


def parse(report: str) -> tuple:
    """(Chat or None, problems). None when the block is missing, isn't a JSON object, or lacks a TL;DR or cause;
    the caller then falls back to the report's summary. `problems` lists what was wrong or ignored."""
    problems = []
    raw = block(report)
    if raw is None:
        return None, ["no RCA_CHAT block"]
    try:
        data = json.loads(raw)
    except ValueError as e:
        return None, [f"RCA_CHAT is not valid JSON ({e.msg} at line {e.lineno})"]
    if not isinstance(data, dict):
        return None, ["RCA_CHAT is not a JSON object"]
    unknown = sorted(set(data) - set(KEYS))
    if unknown:
        problems.append("unknown keys ignored: " + ", ".join(unknown)[:200])

    cause_raw = data.get("cause")
    certainty = ""
    if isinstance(cause_raw, dict):
        certainty = str(cause_raw.get("certainty") or "").strip().lower()
        if certainty and certainty not in CERTAINTY_LABEL:
            problems.append(f"cause.certainty: {certainty[:20]!r}")
            certainty = ""
        cause_raw = cause_raw.get("points", cause_raw.get("text"))
    chat = Chat(title=_title(data.get("title"), problems), tldr=_text(data.get("tldr"), problems, "tldr"),
                cause=_items(cause_raw, problems, "cause"), certainty=certainty)
    if not chat.tldr or not chat.cause:
        return None, problems + ["missing " + " and ".join(k for k, v in (("tldr", chat.tldr), ("cause", chat.cause))
                                                             if not v)]
    if not chat.title:
        problems.append("missing title")
    chat.context = _text(data.get("context"), problems, "context")

    steps = data.get("verify")
    for step in (steps if isinstance(steps, list) else [steps] if steps else [])[:MAX_ITEMS]:
        if isinstance(step, dict):
            text = _text(step.get("text"), problems, "verify")
            command = step.get("command") if isinstance(step.get("command"), str) else ""
            if text or command.strip():          # each step says where it runs: steps often span systems
                chat.verify.append((_text(step.get("where"), problems, "verify.where"), text, command.strip(),
                                    _items(step.get("outcomes"), problems, "verify.outcomes")))
        elif _text(step, problems, "verify"):
            chat.verify.append(("", _text(step, problems, "verify"), "", []))

    chat.now = _items(data.get("now"), problems, "now")
    chat.do_not = _items(data.get("do_not"), problems, "do_not")
    chat.also = _items(data.get("also"), problems, "also")
    fixes = data.get("fixes")
    for group in (fixes if isinstance(fixes, list) else [fixes] if fixes else [])[:MAX_ITEMS]:
        if isinstance(group, dict):
            items = _items(group.get("items"), problems, "fixes")
            if items:
                chat.fixes.append((_text(group.get("owner"), problems, "fixes.owner"), items))
        elif _text(group, problems, "fixes"):
            chat.fixes.append(("", [_text(group, problems, "fixes")]))

    related = data.get("related")
    for item in (related if isinstance(related, list) else [])[:RELATED_MAX]:
        m = REF_RE.match(str(item.get("ref") or "").strip()) if isinstance(item, dict) else None
        label = _text(item.get("label"), problems, "related", keep_refs=True) if isinstance(item, dict) else ""
        if not m or not label:
            problems.append("related: an entry without a valid ref or label was ignored")
            continue
        chat.related.append((m.group(1), int(m.group(2)), label.replace("]", ")").replace("[", "("),
                             _text(item.get("why"), problems, "related", keep_refs=True)))
    return chat, problems


def render(chat: Chat, drop=()) -> str:
    """The message body (everything after the title) in simple Markdown, in the fixed layout. `drop`: optional
    sections to leave out entirely (never TL;DR, cause, now or do_not)."""
    out = [f"**TL;DR:** {chat.tldr}"]

    def section(name: str, lines: list, note: str = ""):
        if lines:
            out.extend(["", f"**{name}**" + (f" ({note})" if note else "")] + lines)

    section("Cause", [f"- {p}" for p in chat.cause], CERTAINTY_LABEL.get(chat.certainty, ""))
    if "verify" not in drop:
        steps = []
        for n, (where, text, command, outcomes) in enumerate(chat.verify, 1):
            steps.append(" ".join(x for x in (f"{n}.", f"_{where}:_" if where else "", text) if x))
            if command:
                steps += ["```", command, "```"]
            steps += [f"   - {x}" for x in outcomes]            # what each result means, under its step
        section("Steps to verify", steps)
    section("Now", [f"- {x}" for x in chat.now])
    section("Do NOT", [f"- {x}" for x in chat.do_not])
    if "fixes" not in drop:
        lines = []
        for owner, items in chat.fixes:
            lines += ([f"_{owner}_"] if owner else []) + [f"- {x}" for x in items]
        section("Permanent fixes", lines)
    if "also" not in drop:
        section("Also", [f"- {x}" for x in chat.also])
    if "related" not in drop:
        section("Related", [f"- [{label}]({kind}:{num})" + (f" — {why}" if why else "")
                            for kind, num, label, why in chat.related])
    return "\n".join(out)


def tldr(report: str) -> Optional[str]:
    chat, _ = parse(report)
    return chat.tldr if chat else None


def related_refs(report: str) -> tuple:
    """(message ids, rca ids) cited in the Related section."""
    chat, _ = parse(report)
    msgs = {num for kind, num, _l, _w in (chat.related if chat else []) if kind == "msg"}
    rcas = {num for kind, num, _l, _w in (chat.related if chat else []) if kind == "rca"}
    return msgs, rcas
