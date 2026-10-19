"""Cheap, deterministic parsing of message text: kind, title, status, service, coarse signature.

The signature only groups related messages so triage can see their history. It deliberately does
NOT try to extract subjects (tables, pods, ...): the triage model decides what is new vs reusable.

Log formats are not built in: they come from the platform profiles in nimi-service/parsers/*.yaml
(see profiles.py). Every message is tried against all profiles' layouts; messages no layout matches get the
generic parse (alert status lines, or "other" with item names masked).
PARSER_VERSION includes a hash of the profiles. Bump RULES_VERSION when this module's output changes; after
either change run `python -m nimi_service reparse`.
"""
import re

from . import profiles

RULES_VERSION = "rules-v5"
PARSER_VERSION = f"{RULES_VERSION}+{profiles.get().fingerprint}"

STATUS_RE = re.compile(
    r"^\s*(?:\W+\s*)?\[(Alerting|OK|Triggered|Resolved|No ?Data|Pending|Firing|Unknown)(?::\d+)?\]\s*", re.IGNORECASE
)                                   # also Grafana's "[FIRING:2] ..." / "[RESOLVED:1] ..." (the count is dropped)
CHANGED_STATUS_RE = re.compile(r"\s*changed status to (\w+)", re.IGNORECASE)

# A pod name's generated suffix, stripped to get the service: <service>-deployment-<hash>-<5> / <service>-<hash>-<5>
POD_SUFFIX_RE = re.compile(r"-deployment-[a-z0-9]{6,10}-[a-z0-9]{5}$|-[a-z0-9]{8,10}-[a-z0-9]{5}$")
ERROR_LINE_RE = re.compile(r"(Exception|Error)\b", re.IGNORECASE)
# A concrete exception, e.g. "com.example.shop.catalog.DuplicateItemException: Item lookup returned ..."
QUALIFIED_EXC_RE = re.compile(r"\b[\w$]+(?:\.[\w$]+)*(?:Exception|Error)\b\s*:")
# Kubernetes pod names: <deployment>-<replicaset hash>-<5 chars>. Only the random suffix is masked (the
# deployment name stays), and only if it contains a digit, so words like "dashboard-alert" are left alone.
POD_NAME_RE = re.compile(r"\b([a-z][a-z0-9-]*?)-([a-z0-9]{6,10})-([a-z0-9]{5})\b")


def mask_pods(text: str) -> str:
    """order-worker-deployment-7d9f8c6b5d-x2k4p -> order-worker-deployment-<pod>"""
    def repl(m):
        return f"{m.group(1)}-<pod>" if re.search(r"\d", m.group(2) + m.group(3)) else m.group(0)
    return POD_NAME_RE.sub(repl, text)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip().strip("`").strip()
        if line:
            return line
    return ""


def normalize(text: str) -> str:
    """Remove volatile parts (pod suffixes, object hashes, urls, ids, numbers, timestamps) so repeats match."""
    t = mask_pods(text.lower())                       # before <hex>/<n>, which would split the pod suffix
    t = re.sub(r"(?<=[a-z0-9_$])@[0-9a-f]{4,16}\b", "@<id>", t)   # Java object identity: Order@1b6d3586
    t = re.sub(r"https?://\S+", "<url>", t)
    t = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<uuid>", t)
    t = re.sub(r"\d{4}-\d{2}-\d{2}[ t][\d:.+z-]+", "<ts>", t)
    t = re.sub(r"\b[0-9a-f]{9,}\b", "<hex>", t)
    t = re.sub(r"\d+(\.\d+)?", "<n>", t)
    return re.sub(r"\s+", " ", t).strip()[:300]


def mask_subjects(text: str, prof=None) -> str:
    """Mask item names (tables, schemas, paths, hosts, quoted identifiers, plus the profiles' subject_masks)
    so the signature is the kind of error, not the item. Which items are affected is decided later by triage."""
    t = text
    for pattern in (prof or profiles.get()).subject_masks:
        t = pattern.sub("<name>", t)
    t = re.sub(r"'[^']*'", "<q>", t)
    t = re.sub(r'"[^"]*"', "<q>", t)
    t = re.sub(r"\b[\w-]+(?:\.[\w-]+)+:\d+\b", "<host>", t)          # host.domain:5432
    t = re.sub(r"\b[\w-]+(?:/[\w.-]+)+", "<path>", t)                 # org/db/table, a/b
    t = re.sub(r"\b[\w-]+(?:\.[\w-]+){2,}\b", "<fqn>", t)            # project.dataset.table, pkg.Class
    t = re.sub(r"(\b(?:database|schema|table|column|for)\b:?\s+)[\w./-]+", r"\1<name>", t, flags=re.I)
    return t


def _parse_log(text: str, sender=None, prof=None):
    """A log message, if one of the profiles' layouts matches a line of it (first layout, in priority order)."""
    prof = prof or profiles.get()
    lines = [l.strip().strip("`").strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    for layout in prof.layouts:
        if not layout.applies(text, sender):
            continue
        for idx, line in enumerate(lines):
            m = layout.header.match(line)
            if m:
                return _log_fields(layout, m, line, lines[idx + 1:], prof)
    return None


def _log_fields(layout, m, header: str, rest: list, prof) -> dict:
    g = m.groupdict()
    service = (g.get("service") or POD_SUFFIX_RE.sub("", g.get("pod") or "")).strip()
    logger = (g.get("logger") or "").strip()
    message = (g.get("message") or "").strip() if layout.message == "same_line" else ""
    error_line = (message
                  or next((l for l in rest if QUALIFIED_EXC_RE.search(l)), None)
                  or next((l for l in rest if ERROR_LINE_RE.search(l)), None)
                  or (rest[0] if rest else header))
    title = f"{service}: {logger.rsplit('.', 1)[-1]}: {error_line}"[:300]
    return {
        "kind": "log", "title": title, "status": None, "service": service, "layout": layout.label,
        "signature_key": "log|" + normalize(f"{service}|{logger}|{mask_subjects(error_line, prof)}"),
    }


def parse_text(text: str, first_line_hint: str = "", sender: str = None, prof=None) -> dict:
    """Parse any text (a Chat message's text+cards, or arbitrary pasted text). `sender` (display name) is
    only used by layouts with a `when: {sender: …}` filter."""
    log = _parse_log(text, sender, prof)
    if log:
        return log

    title = first_line_hint or _first_line(text)
    status = None
    sm = STATUS_RE.match(title)
    if sm:
        status = re.sub(r"\s+", " ", sm.group(1)).title()
        title = title[sm.end():].strip()
    else:
        cm = CHANGED_STATUS_RE.search(title)
        if cm:
            status = cm.group(1).title()
    title = CHANGED_STATUS_RE.sub("", title).strip()
    service = None
    bm = re.match(r"^\[([^\]]+)\]\s*", title)        # "[shop order-worker prod] CPU ..."
    if bm:
        service = bm.group(1).strip()
        title = title[bm.end():]
    title = re.sub(r"^\W+", "", title).strip().strip('"\u201c\u201d').strip()[:300]
    kind = "alert" if status else "other"
    key_text = f"[{service}] {title}" if service else title
    if kind == "other":                             # unknown format: group by the kind of message, not the item
        key_text = mask_subjects(key_text, prof)
    return {
        "kind": kind, "title": title, "status": status, "service": service, "layout": None,
        "signature_key": f"{kind}|" + normalize(key_text),
    }


def parse_message(msg) -> dict:
    """Parse a provider-neutral providers.Message (uses its parse_text and title_hint)."""
    return parse_text(msg.parse_text, msg.title_hint, getattr(msg, "sender_display_name", None))


RESOLVED_STATUSES = {"ok", "resolved"}


def looks_resolved(status) -> bool:
    return bool(status) and status.lower() in RESOLVED_STATUSES
