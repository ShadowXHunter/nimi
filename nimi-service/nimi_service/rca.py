"""Run the /rca skill headlessly and read its output. The skill is read-only; this module only reads stdout.

Skill output contract (see SKILL.md "Output contract"):
  - No RCA needed / possible: first line `RCA_OUTCOME: not_applicable - <reason>`
                              or        `RCA_OUTCOME: insufficient_context - <reason>`
  - Normal report: ends with a block
        RCA_FIELDS
        origin: internal | upstream | undetermined
        confidence: high | medium | low
        self_healing: yes | no | unknown
        repo: <repo directory name> | none
        commit: <sha> | none
        sensitive_data: none | input:password, config:webhook-url, ...   (types/locations only, no values)
"""
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import timezone
from pathlib import Path

from .claude_cli import build_env, classify_unavailable, cli_error
from . import chat_message
from .config import SERVICE_DIR

log = logging.getLogger(__name__)

OUTCOME_RE = re.compile(r"^\s*RCA_OUTCOME:\s*(not_applicable|insufficient_context)\b\s*[-:—]?\s*(.*)", re.I | re.M)
FIELDS_RE = re.compile(r"RCA_FIELDS\s*\n((?:\s*[a-z_]+\s*:.*\n?)+)", re.I)
FIELD_VALUES = {
    "origin": {"internal", "upstream", "undetermined"},
    "confidence": {"high", "medium", "low"},
    "self_healing": {"yes", "no", "unknown"},
}


def skill_version(settings) -> str:
    h = hashlib.sha256()
    skill_dir = settings.rca_project / ".claude/skills/rca"
    for f in sorted(skill_dir.rglob("*")):
        if f.is_file():
            h.update(f.relative_to(skill_dir).as_posix().encode())
            h.update(f.read_bytes())
    agent = settings.rca_project / ".claude/agents/rca-investigator.md"
    if agent.exists():
        h.update(agent.read_bytes())
    return h.hexdigest()[:12]


def build_input(request: dict, new_subjects: list, reused: list, history: bool = False) -> str:
    """Text passed after /rca: a context block from the service (scope, history command), then the input."""
    parts = []
    if new_subjects and new_subjects != ["*"]:
        parts.append("- Analyze only these subjects: " + ", ".join(new_subjects))
    for r in reused:
        parts.append(f"- Already analyzed, do not re-analyze: {r['subject']} (earlier RCA #{r['rca_id']})")
    if history:
        parts.append(f'- History SQL: {NIMI_SQL} "<one SQL statement>"')
    if parts:
        parts = ["RCA context (from NIMI):"] + parts + ["---"]
    parts.append(request["input_text"])
    return "\n".join(parts)


SENSITIVE_WHERE = {"input", "code", "config", "logs", "docs", "history"}


def parse_sensitive(value: str) -> list:
    """"input:password, config:webhook-url" -> [{"where": "input", "type": "password"}, ...] (labels only)."""
    items = []
    for part in value.split(","):
        where, _, type_ = part.strip().partition(":")
        where, type_ = where.strip(), type_.strip()
        if not type_:
            where, type_ = "unknown", where
        if re.fullmatch(r"[a-z][a-z0-9-]{0,39}", type_) and (where in SENSITIVE_WHERE or where == "unknown"):
            items.append({"where": where, "type": type_})
    return items[:20]


def parse_output(report: str) -> dict:
    fields = {"outcome": "rca"}
    m = OUTCOME_RE.search(report or "")
    if m and m.start() < 200:                      # marker must be at the very start of the output
        fields["outcome"] = m.group(1).lower()
        fields["error"] = None
        return fields
    fm = FIELDS_RE.search(report or "")
    if not fm:
        # A complete report always ends with RCA_FIELDS; without it the run was cut short (limit, crash).
        return {"outcome": "error", "error": "incomplete RCA output (no RCA_FIELDS block)"}
    if fm:
        for line in fm.group(1).splitlines():
            if ":" not in line:
                continue
            k, v = (x.strip().lower() for x in line.split(":", 1))
            if k in FIELD_VALUES and v in FIELD_VALUES[k]:
                fields[k] = v
            elif k == "repo" and v != "none" and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", v):
                fields["repo"] = v                 # any registered repo's directory name
            elif k == "sensitive_data" and v != "none":
                fields["sensitive_data"] = parse_sensitive(v)
            elif k == "commit" and re.fullmatch(r"[0-9a-f]{7,40}", v):
                fields["commit_sha"] = v
    return fields


# The skill's SQL access to history (bin/nimi-sql -> nimi_service/skill_sql.py). The guard hook allows exactly
# this file; build_input tells the skill its path.
NIMI_SQL = SERVICE_DIR / "bin/nimi-sql"
RUN_DIR = Path.home() / ".cache/nimi/run"


@contextmanager
def history_access(settings):
    """Environment additions that let the run's `nimi-sql` connect as the skill reader role ({} if history
    is off). The password goes into a private temp file (deleted after the run), not the environment;
    NIMI_ENV=/dev/null keeps nimi-sql from reading the service's env files (and the service's password)."""
    if not settings.skill_history:
        yield {}
        return
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(RUN_DIR, 0o700)
    fd, pw_file = tempfile.mkstemp(prefix="skill-db-", dir=RUN_DIR)      # created 0600
    try:
        with os.fdopen(fd, "w") as f:
            f.write(settings.resolved_db_skill_password())
        yield {
            "NIMI_PYTHON": sys.executable,
            "NIMI_ENV": os.devnull,
            "NIMI_DB_HOST": settings.db_host, "NIMI_DB_PORT": str(settings.db_port),
            "NIMI_DB_NAME": settings.db_name, "NIMI_DB_SSLMODE": settings.db_sslmode,
            "NIMI_DB_SKILL_USER": settings.db_skill_user, "NIMI_DB_SKILL_PASSWORD_FILE": pw_file,
        }
    finally:
        os.unlink(pw_file)


def run_rca(settings, request: dict, new_subjects: list, reused: list) -> dict:
    cmd = [
        settings.claude_bin, "-p", "/rca " + build_input(request, new_subjects, reused, settings.skill_history),
        "--settings", str(settings.rca_project / ".claude/nimi-session-settings.json"),
        "--tools", "Read,Grep,Glob,Bash,WebSearch,Skill",
        "--permission-mode", "dontAsk",
        "--setting-sources", settings.claude_setting_sources,   # default user,project (NIMI_CLAUDE_SETTING_SOURCES)
        "--strict-mcp-config",                                  # no MCP servers
        "--output-format", "json",
    ]
    version = skill_version(settings)
    started = time.monotonic()
    try:
        with history_access(settings) as history_env:
            proc = subprocess.run(cmd, cwd=settings.rca_project, capture_output=True, text=True,
                                  env={**build_env(settings), **history_env},
                                  timeout=settings.rca_timeout_seconds, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"outcome": "timeout", "error": f"RCA exceeded {settings.rca_timeout_seconds}s",
                "duration_s": round(time.monotonic() - started, 1), "skill_version": version}
    duration = round(time.monotonic() - started, 1)
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        tail = (proc.stderr or proc.stdout or "")[-500:]
        return {"outcome": "error", "error": f"exit {proc.returncode}; unparseable output: {tail}",
                "duration_s": duration, "skill_version": version}

    result = {
        "report": out.get("result"),
        "cost_usd": out.get("total_cost_usd"),
        "duration_s": round((out.get("duration_ms") or duration * 1000) / 1000, 1),
        "num_turns": out.get("num_turns"),
        "session_id": out.get("session_id"),
        "model": ",".join(sorted((out.get("modelUsage") or {}).keys())) or None,
        "skill_version": version,
    }
    error = cli_error(out, proc)
    if error or not out.get("result"):
        result.update(outcome="error", error=error or "skill run returned no output")
        result["unavailable"] = classify_unavailable(error)
    else:
        result.update(parse_output(out["result"]))
    return result


# --- Reply formatting (provider-aware) -----------------------------------------------------------
# One message per RCA. The skill fills in the chat message's fields (the RCA_CHAT block, JSON; see
# chat_message.py and templates/chat.md); this module renders them in a fixed layout and adds the header, the
# confidence/origin/self-healing line (from RCA_FIELDS, so it always matches), the optional webhook source line
# and the footer, then converts the markup for the provider. The full report stays in the database.

DEFAULT_DISCLAIMER = "Automated RCA, not verified. Based on code and docs that may differ from production."
FEEDBACK_LINE = "Rate it: 👍 correct · 🤔 partly right · 👎 wrong"
SHORTENED_NOTE = "_(shortened; full report stored)_"
SOURCE_LINE_MAX = 300      # room kept free in every reply, so a webhook post can add the source line


def header(provider, text: str) -> str:
    """First line of every NIMI post: "👁️ NIMI — <disclaimer>" (or a notice). Its "👁️ NIMI —" prefix also
    identifies NIMI's own posts."""
    return f"👁️ {provider.bold('NIMI')} — {text}"


def extract_chat(report: str):
    """The skill's raw chat block (RCA_CHAT), or None if it didn't write one."""
    return chat_message.block(report)


def title_of(report: str):
    """The title of a stored report's chat message (or of the report itself), without an "RCA:" prefix."""
    chat, _ = chat_message.parse(report)
    if chat and chat.title:
        return chat.title
    # TODO(old chat format): drop the bold-line search below (keep the "# heading" one: it's the fallback for
    # reports without a usable chat block). Remove once no reusable RCA has a Markdown chat block, i.e.
    # NIMI_REUSE_DAYS (7) days after the JSON chat block's first live run; check skill_rcas first.
    m = re.search(r"^\*\*(?!TL;DR)([^*\n]{3,150})\*\*\s*$", extract_chat(report) or "", re.M) \
        or re.search(r"^#\s+(.{3,150})$", report or "", re.M)
    return re.sub(r"(?i)^(?:rca\s*[:\-–—]\s*)+", "", m.group(1)).strip() if m else None


def tldr_of(report: str):
    """The TL;DR of a stored report's chat message, for reuse replies. Reports from before the JSON chat block
    had a Markdown "**TL;DR:** …" line."""
    text = chat_message.tldr(report)
    if text:
        return text
    # TODO(old chat format): remove this Markdown fallback once no reusable RCA has a Markdown chat block, i.e.
    # NIMI_REUSE_DAYS (7) days after the JSON chat block's first live run; check skill_rcas first.
    m = re.search(r"\*\*TL;DR:?\*\*:?\s*(.+?)(?:\n\s*\n|\Z)", extract_chat(report) or "", re.S)
    return " ".join(m.group(1).split()) if m else None


# Citations written by the skill: [label](msg:<message_id>) / [label](rca:<result_id>). The service turns
# verified ones into links; the skill never writes URLs (Markdown links to URLs are reduced to their label).
REF_RE = re.compile(r"\[([^\]\n]{1,100})\]\((msg|rca):(\d{1,12})\)")
# Code citations: [label](code:<project>@<commit>/<path in repo>#L<line>[-L<line>]) -> link at that commit.
CODE_REF_RE = re.compile(r"\[([^\]\n]{1,100})\]\(code:([a-z0-9][a-z0-9_-]{0,40})@([0-9a-f]{7,40})/([^)#\s]{1,400})"
                         r"(?:#L(\d{1,7})(?:-L?(\d{1,7}))?)?\)")
ID_LABEL_RE = re.compile(r"(?:msg|message|log|alert)?\s*(?:id\s*)?#?\s*\d+", re.I)
URL_LINK_RE = re.compile(r"\[([^\]\n]{1,100})\]\((?:https?|ftp)://[^)\s]*\)")


def find_refs(text: str) -> tuple:
    """(message ids, rca ids) cited in `text`: inline citations plus the chat message's Related entries."""
    msgs, rcas = set(), set()
    for _label, kind, num in REF_RE.findall(text or ""):
        (msgs if kind == "msg" else rcas).add(int(num))
    rel_msgs, rel_rcas = chat_message.related_refs(text)
    return msgs | rel_msgs, rcas | rel_rcas


def resolve_refs(provider, text: str, links: dict, code_bases: dict = None) -> str:
    """Citations -> provider links for verified ones ({("msg"|"rca", id): url}; code: {project: web url});
    unknown ones -> their label. Markdown links to URLs are reduced to their label."""
    from .projects import code_url

    def repl(m):
        label, kind, num = m.group(1), m.group(2), int(m.group(3))
        url = (links or {}).get((kind, num))
        if kind == "msg" and ID_LABEL_RE.fullmatch(label.strip()):     # "msg 1234": an ID means nothing to readers
            label = (links or {}).get(("msg-label", num)) or "message"
        return provider.link(url, label) if url else label

    def code_repl(m):
        label, project, commit, path, line, end = m.groups()
        url = code_url((code_bases or {}).get(project), commit, path, int(line) if line else None,
                       int(end) if end else None)
        return provider.link(url, label) if url else label
    return URL_LINK_RE.sub(r"\1", CODE_REF_RE.sub(code_repl, REF_RE.sub(repl, text or "")))


def _strip_fields(report: str) -> str:
    return chat_message.CHAT_OPEN_RE.sub("", chat_message.CHAT_RE.sub("", FIELDS_RE.sub("", report or ""))).rstrip()


def _status_line(provider, fields: dict) -> str:
    parts = [f"{provider.bold(label + ':')} {fields[key]}" for key, label in
             (("confidence", "Confidence"), ("origin", "Origin"), ("self_healing", "Self-healing")) if fields.get(key)]
    return " · ".join(parts)


def _fallback_body(report: str) -> tuple:
    """(title, body) from the full report when the skill wrote no usable chat message: its title and Summary."""
    text = _strip_fields(report)
    tm = re.search(r"^#\s*(?:RCA:\s*)?(.+)$", text, re.M)
    sm = re.search(r"^##\s*Summary\s*\n(.+?)(?=^##\s|\Z)", text, re.S | re.M)
    body = sm.group(1).strip() if sm else text
    return (tm.group(1).strip() if tm else "RCA"), body + "\n\n_(Short message unavailable: this is the report's summary.)_"


def _earlier_label(info: dict) -> str:
    """How an earlier RCA is named in replies: its title and time, never its database ID."""
    title = (info or {}).get("title") or "earlier RCA"
    if len(title) > 70:
        title = title[:70].rsplit(" ", 1)[0].rstrip(" ,;:-–—") + "…"
    when = (info or {}).get("created_at")
    return f"{title}, {when.astimezone(timezone.utc):%b %d %H:%M} UTC" if when else title


def _reuse_lines(provider, reused: list, reused_replies: dict, earlier: dict = None) -> list:
    """One line per earlier RCA: the subjects it covers (shortened when many), a link to its post (labelled by
    its title and time) and its TL;DR. `earlier`: result_id -> {"title", "tldr", "created_at"}."""
    by_rca = {}
    for r in reused:
        by_rca.setdefault(r["rca_id"], []).append(r["subject"])
    lines = []
    for rca_id, subjects in by_rca.items():
        named = [x for x in subjects if x != "*"]          # "*" = the whole message, not a subject to show
        shown = ", ".join(named[:5]) + (f" and {len(named) - 5} more" if len(named) > 5 else "")
        info = (earlier or {}).get(rca_id) or {}
        label, ref = _earlier_label(info), reused_replies.get(rca_id)
        lines.append("- " + (f"`{shown}` → " if shown else "")
                     + (provider.link(provider.message_link(ref), label) if ref else label))
        if info.get("tldr"):
            when = info.get("created_at")
            age = f" ({when.astimezone(timezone.utc):%b %d})" if when else ""   # its numbers are from then
            lines.append(f"  {provider.bold(f'TL;DR{age}:')} {info['tldr']}")
    return lines


def sensitive_note(input_types) -> list:
    """A visible note when the alert itself seems to contain sensitive data (types only)."""
    if not input_types:
        return []
    kinds = ", ".join(sorted(set(input_types)))
    return [f"⚠️ This alert appears to contain sensitive data ({kinds}). Please remove it from the alert source "
            "and consider rotating any exposed credential.", ""]


def _footer(provider) -> list:
    return ["", FEEDBACK_LINE]


def _assemble(provider, head: list, body: str, tail: list, max_chars: int) -> str:
    """head + body + tail in the provider's markup, truncating only the body so everything else always fits
    (last resort: chat messages are shortened by dropping whole sections first, see format_rca)."""
    head_s = provider.render("\n".join(head))
    tail_s = provider.render("\n".join(tail)) if tail else ""
    body_s = provider.render(body)
    room = max_chars - len(head_s) - len(tail_s) - 2
    if len(body_s) > room:
        marker = "\n… (shortened; full report stored)"
        body_s = body_s[: max(room - len(marker), 0)].rstrip() + marker
    return "\n".join(x for x in (head_s, body_s, tail_s) if x)


def source_line(provider, req: dict) -> str:
    """For posts outside the alert's thread: a short link back to the original message (the header already
    names the alert)."""
    where = f" in {req['channel_label']}" if req.get("channel_label") else ""
    link = provider.link(provider.message_link(req["source_external_id"]), "Link") \
        if req.get("source_external_id") else "unavailable"
    return f"🔔 Original message{where}: {link}"[:SOURCE_LINE_MAX]


def with_source(text: str, line: str) -> str:
    """Insert the source line after a post's title line (the first non-empty line after the NIMI line);
    for one-line posts (notices), after the NIMI line."""
    if not line:
        return text
    lines = (text or "").split("\n")
    title = next((i for i in range(1, min(len(lines), 4)) if lines[i].strip()), None)
    pos = title + 1 if title is not None else 1
    return "\n".join(lines[:pos] + [line] + lines[pos:])


def looks_like_nimi_post(provider, text: str) -> bool:
    """A message NIMI itself posted: it starts with the NIMI header."""
    start = (text or "").lstrip()
    return any(start.startswith(f"👁️ {provider.bold('NIMI')} {sep}") for sep in ("—", "·"))


def format_rca(provider, report: str, reused: list, reused_replies: dict, max_chars: int,
               input_sensitive_types=(), disclaimer_text: str = DEFAULT_DISCLAIMER, source: str = "",
               earlier: dict = None, links: dict = None, registry=None, fallback_title: str = "",
               problems: list = None) -> str:
    """The one chat message for an RCA (or a partly reused one). `links`: verified citation targets;
    `registry` (projects.Registry): for code links and for rewriting local paths to "<project>: <path>".
    `problems` (optional list) collects what was wrong with the skill's chat block, and any shortening."""
    from .projects import code_links, relativize_paths
    problems = [] if problems is None else problems
    fields = parse_output(report)
    chat, issues = chat_message.parse(report)
    problems.extend(issues)
    if chat:
        title = chat.title or " ".join((fallback_title or "RCA").split())[:chat_message.TITLE_MAX]
    else:
        title, fallback = _fallback_body(report)
        if title == "RCA" and fallback_title:
            title = fallback_title
        title = re.sub(r"(?i)^(?:rca\s*[:\-–—]\s*)+", "", " ".join(title.split()))[:chat_message.TITLE_MAX] or "RCA"
    head = [header(provider, provider.italic(disclaimer_text or DEFAULT_DISCLAIMER)), "", provider.bold("RCA: " + title)] \
        + ([source] if source else [])
    tail = _footer(provider)
    bases = code_links(registry) if registry else None

    def finish(markdown: str) -> str:
        body = resolve_refs(provider, markdown, links, bases)
        return relativize_paths(body, registry) if registry else body

    status = _status_line(provider, fields)
    head += [status] if status else []
    if chat and chat.context:                   # one line: main subjects, environment / owner
        head.append(finish(chat.context))
    if reused:
        head += ["", provider.bold("Already analyzed:")] + _reuse_lines(provider, reused, reused_replies, earlier)
    head += [""] + sensitive_note(input_sensitive_types)
    if registry:
        head = [relativize_paths(h, registry) for h in head]

    if not chat:
        return _assemble(provider, head, finish(fallback), tail, max_chars)
    # The skill keeps the message within its budget. Only if it still can't fit the chat limit, whole optional
    # sections are left out (never cut in the middle); text inside a section is never trimmed.
    fixed = len(provider.render("\n".join(head))) + len(provider.render("\n".join(tail))) + 2
    dropped = []
    for extra in (None,) + chat_message.OPTIONAL_DROP_ORDER:
        if extra:
            dropped.append(extra)
        body = finish(chat_message.render(chat, drop=dropped) + (f"\n\n{SHORTENED_NOTE}" if dropped else ""))
        if fixed + len(provider.render(body)) <= max_chars:
            break
    if dropped:
        problems.append("too long for chat: left out " + ", ".join(dropped))
    return _assemble(provider, head, body, tail, max_chars)


def format_reuse(provider, reused: list, reused_replies: dict, max_chars: int, input_sensitive_types=(),
                 disclaimer_text: str = DEFAULT_DISCLAIMER, source: str = "", earlier: dict = None) -> str:
    """No new RCA: the alert is the same ongoing incident as earlier RCA(s)."""
    several = len({r["rca_id"] for r in reused}) > 1
    head = [header(provider, provider.italic(disclaimer_text or DEFAULT_DISCLAIMER)), "",
            provider.bold("Same as earlier RCAs" if several else "Same as an earlier RCA")] \
        + ([source] if source else [])
    head += [""] + sensitive_note(input_sensitive_types)
    body = "\n".join(_reuse_lines(provider, reused, reused_replies, earlier))
    return _assemble(provider, head, body, _footer(provider), max_chars)


def format_limit_notice(provider, disclaimer_text: str = DEFAULT_DISCLAIMER, source: str = "") -> str:
    """Notice while Claude is unavailable. Makes no promise of a later RCA."""
    head = [header(provider, "⏳ Couldn't analyze this alert: the Claude usage limit was reached.")]
    return provider.render("\n".join(head + ([source] if source else [])))
