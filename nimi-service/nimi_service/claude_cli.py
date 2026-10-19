"""How the service starts the `claude` CLI: which account/config it uses and which environment it sees.

Claude calls (triage and RCA runs) get a clean, explicit environment instead of inheriting the service's,
so settings from outside NIMI (e.g. an ANTHROPIC_API_KEY exported for another project, ANTHROPIC_BASE_URL,
ANTHROPIC_MODEL) can't silently change which account is used, where requests go, or which model runs.
Account and config come only from NIMI's settings:
  NIMI_CLAUDE_CONFIG_DIR       -> CLAUDE_CONFIG_DIR (separate login, user settings, history); unset = ~/.claude
  NIMI_ANTHROPIC_API_KEY_FILE  -> ANTHROPIC_API_KEY (API billing instead of the stored login)
  NIMI_CLAUDE_PASS_ENV         -> extra variables to pass through (e.g. HTTPS_PROXY,NO_PROXY)
"""
import os
import re
from datetime import datetime, timezone

# Needed for the CLI (and the guard hook it runs via python3) to work at all.
BASE_VARS = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TERM",
             "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
BASE_PREFIXES = ("LC_", "PYENV_")


def cli_error(out: dict, proc) -> str:
    """A short description of a failed `claude -p --output-format json` call, or "" if it succeeded.
    E.g. "Not logged in · Please run /login" when the configured account isn't logged in."""
    detail = (out.get("result") or (proc.stderr or "").strip() or f"exit {proc.returncode}")[:300]
    if out.get("is_error") or proc.returncode != 0 or out.get("subtype") not in (None, "success"):
        return f"claude call failed ({out.get('subtype') or 'error'}): {detail}"
    # Some failures (e.g. "Not logged in" for a slash-command prompt) are reported as a "success" with no
    # model call at all. Every real triage/RCA call uses a model, so no model usage means it failed.
    if not out.get("modelUsage") and not out.get("num_turns"):
        return f"claude call made no model request: {detail}"
    return ""


def build_env(settings, parent=None) -> dict:
    parent = os.environ if parent is None else parent
    env = {k: v for k, v in parent.items() if k in BASE_VARS or k.startswith(BASE_PREFIXES)}
    for name in settings.claude_pass_env:
        if name in parent:
            env[name] = parent[name]
    if settings.claude_config_dir:
        env["CLAUDE_CONFIG_DIR"] = str(settings.claude_config_dir)
    if settings.anthropic_api_key_file:
        try:
            key = settings.anthropic_api_key_file.read_text().strip()
        except OSError as e:
            raise RuntimeError(f"cannot read NIMI_ANTHROPIC_API_KEY_FILE: {type(e).__name__}") from None
        if key:
            env["ANTHROPIC_API_KEY"] = key
    return env


# --- Claude unavailable (usage limits, credit, auth): the service pauses all Claude calls --------------

class ClaudeUnavailable(Exception):
    """Claude can't be used right now (usage limit, credit, auth). The service pauses and retries later."""

    def __init__(self, kind: str, message: str, reset_at=None):
        super().__init__(f"{kind}: {message}")
        self.kind, self.message, self.reset_at = kind, message, reset_at


# (kind, pattern) — matched against Claude Code's error text. Kinds:
#   usage_limit  subscription usage limit / out of extra usage   (resets on its own)
#   rate_limit   throttling that outlasted Claude Code's own retries (resets on its own)
#   credit       API credit balance too low                      (needs an admin)
#   budget       spending / budget limit reached                 (needs an admin)
#   auth         not logged in, invalid key, expired/revoked token (needs an admin)
UNAVAILABLE_PATTERNS = [
    ("usage_limit", re.compile(r"usage limit reached|out of (?:extra )?usage|\blimit reached\b.*\bresets?\b|"
                               r"\b(?:5-hour|weekly|session) limit\b", re.I)),
    ("credit", re.compile(r"credit balance (?:is )?too low", re.I)),
    ("budget", re.compile(r"budget limit reached|spending limit", re.I)),
    ("rate_limit", re.compile(r"rate[_ ]limit|\b429\b|too many requests|overloaded|\b529\b", re.I)),
    ("auth", re.compile(r"not logged in|please run /login|invalid api key|authentication failed|"
                        r"oauth token (?:expired|revoked)|invalid x-api-key", re.I)),
]
NEEDS_ADMIN = {"credit", "budget", "auth"}


def parse_reset_time(text: str):
    """A reset time if Claude's message includes one as a unix timestamp (e.g. "...|1760781600"); else None.
    Other formats ("resets 3pm") are ambiguous without a timezone, so the service uses its cool-down instead."""
    m = re.search(r"(?:\||resets?\D{0,10})(1[6-9]\d{8})\b", text or "")
    if m:
        ts = datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)
        if ts > datetime.now(timezone.utc):
            return ts
    return None


def classify_unavailable(text: str):
    """ClaudeUnavailable for a usage/credit/auth error text, else None."""
    for kind, pattern in UNAVAILABLE_PATTERNS:
        if pattern.search(text or ""):
            return ClaudeUnavailable(kind, (text or "")[:300], parse_reset_time(text))
    return None
