"""Platform profiles: declarative, per-platform knowledge for the parser and triage (nimi-service/parsers/local/*.yaml).

A profile is data, not code — patterns only. Every message is checked against the layouts of ALL profiles
(a channel can carry messages from several platforms); the first layout that matches wins, and messages no
layout matches fall back to the generic parser. Profile keys:

  name: myplatform
  description: …
  log_layouts:                        # how this platform's log lines look
    - name: logback-dash
      header: '<regex>'               # matched against each line (stripped); named groups:
                                      #   pod or service (required), logger, level, message
      message: next_lines | same_line # where the error text is (same_line needs a `message` group)
      when: {sender: [...], contains: [...]}   # optional: only for these sender names / texts containing …
      priority: 100                   # optional: lower is tried first (default 100)
  subject_masks: ['<regex>', …]       # optional: platform-specific item names to mask in signatures
  triage_notes:                       # optional: guidance shown to triage for matching messages
    - applies_to: {text_contains: [...]}
      note: "…"

Changing a profile changes PARSER_VERSION (it includes a hash of the profiles): run `reparse` afterwards.
"""
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# This deployment's profiles (git-ignored). parsers/examples/ holds documented samples that are never loaded.
PROFILE_DIR = Path(__file__).resolve().parent.parent / "parsers" / "local"
MESSAGE_MODES = ("next_lines", "same_line")
MAX_NOTES_CHARS = 2000


class ProfileError(Exception):
    pass


@dataclass
class Layout:
    profile: str
    name: str
    header: re.Pattern
    message: str = "next_lines"
    senders: tuple = ()
    contains: tuple = ()
    priority: int = 100
    order: int = 0

    @property
    def label(self) -> str:
        return f"{self.profile}/{self.name}"

    def applies(self, text: str, sender: Optional[str]) -> bool:
        if self.senders and (sender or "").strip().lower() not in self.senders:
            return False
        if self.contains and not any(c in (text or "").lower() for c in self.contains):
            return False
        return True


@dataclass
class Note:
    profile: str
    text_contains: tuple
    note: str


@dataclass
class Profiles:
    layouts: list = field(default_factory=list)
    subject_masks: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    fingerprint: str = "none"
    files: list = field(default_factory=list)

    def notes_for(self, text: str) -> list:
        """Triage notes whose applies_to matches the message text (case-insensitive)."""
        low = (text or "").lower()
        out, size = [], 0
        for n in self.notes:
            if any(t in low for t in n.text_contains):
                size += len(n.note)
                if size > MAX_NOTES_CHARS:
                    break
                out.append(n.note)
        return out


def _strings(value, where: str) -> tuple:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ProfileError(f"{where}: expected a string or a list of strings")
    return tuple(v.strip().lower() for v in value)


def _compile(pattern, where: str) -> re.Pattern:
    if not isinstance(pattern, str) or not pattern:
        raise ProfileError(f"{where}: expected a regular expression string")
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ProfileError(f"{where}: invalid regular expression: {e}") from None


def _check_keys(obj, allowed: set, where: str) -> None:
    if not isinstance(obj, dict):
        raise ProfileError(f"{where}: expected a mapping")
    unknown = set(obj) - allowed
    if unknown:
        raise ProfileError(f"{where}: unknown key(s) {sorted(unknown)}; allowed: {sorted(allowed)}")


def parse_profile(doc, source: str, start_order: int = 0) -> tuple:
    """(layouts, subject_masks, notes) from one profile document. Raises ProfileError with the location."""
    _check_keys(doc, {"name", "description", "log_layouts", "subject_masks", "triage_notes"}, source)
    name = doc.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", name):
        raise ProfileError(f"{source}: `name` must be a short lowercase identifier")
    layouts = []
    for i, raw in enumerate(doc.get("log_layouts") or []):
        where = f"{source}: log_layouts[{i}]"
        _check_keys(raw, {"name", "header", "message", "when", "priority"}, where)
        header = _compile(raw.get("header"), f"{where}.header")
        groups = set(header.groupindex)
        if not groups & {"pod", "service"}:
            raise ProfileError(f"{where}.header: needs a named group (?P<pod>…) or (?P<service>…)")
        mode = raw.get("message", "next_lines")
        if mode not in MESSAGE_MODES:
            raise ProfileError(f"{where}.message: one of {MESSAGE_MODES}")
        if mode == "same_line" and "message" not in groups:
            raise ProfileError(f"{where}.header: message: same_line needs a (?P<message>…) group")
        when = raw.get("when") or {}
        _check_keys(when, {"sender", "contains"}, f"{where}.when")
        priority = raw.get("priority", 100)
        if not isinstance(priority, int):
            raise ProfileError(f"{where}.priority: expected an integer")
        lname = raw.get("name")
        if not isinstance(lname, str) or not lname.strip():
            raise ProfileError(f"{where}.name: required")
        layouts.append(Layout(name, lname.strip(), header, mode, _strings(when.get("sender"), f"{where}.when.sender"),
                              _strings(when.get("contains"), f"{where}.when.contains"), priority, start_order + i))
    masks = [_compile(p, f"{source}: subject_masks[{i}]") for i, p in enumerate(doc.get("subject_masks") or [])]
    notes = []
    for i, raw in enumerate(doc.get("triage_notes") or []):
        where = f"{source}: triage_notes[{i}]"
        _check_keys(raw, {"applies_to", "note"}, where)
        applies = raw.get("applies_to") or {}
        _check_keys(applies, {"text_contains"}, f"{where}.applies_to")
        contains = _strings(applies.get("text_contains"), f"{where}.applies_to.text_contains")
        if not contains:
            raise ProfileError(f"{where}.applies_to.text_contains: required (notes are scoped to matching messages)")
        note = raw.get("note")
        if not isinstance(note, str) or not note.strip():
            raise ProfileError(f"{where}.note: required")
        notes.append(Note(name, contains, " ".join(note.split())))
    return layouts, masks, notes


def load(directory: Path = None) -> Profiles:
    directory = Path(directory or PROFILE_DIR)
    files = sorted(directory.glob("*.yaml")) if directory.is_dir() else []
    result, digest, names = Profiles(files=[f.name for f in files]), hashlib.sha256(), set()
    for f in files:
        data = f.read_bytes()
        digest.update(f.name.encode() + b"\0" + data)
        try:
            doc = yaml.safe_load(data) or {}
        except yaml.YAMLError as e:
            raise ProfileError(f"{f.name}: invalid YAML: {e}") from None
        layouts, masks, notes = parse_profile(doc, f.name, start_order=len(result.layouts))
        if doc["name"] in names:
            raise ProfileError(f"{f.name}: duplicate profile name {doc['name']!r}")
        names.add(doc["name"])
        result.layouts += layouts
        result.subject_masks += masks
        result.notes += notes
    result.layouts.sort(key=lambda l: (l.priority, l.order))
    result.fingerprint = digest.hexdigest()[:8] if files else "none"
    return result


_CACHE: Optional[Profiles] = None


def get() -> Profiles:
    """The profiles in nimi-service/parsers/local/, loaded once per process."""
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = load()
        except ProfileError as e:
            raise SystemExit(f"invalid parser profile: {e}") from None
    return _CACHE
