#!/usr/bin/env python3
"""PreToolUse guard for the rca-investigator subagent.

Allows only:
  - Read / Grep / Glob on paths inside the registered projects (skills/rca/projects/local/registry.json)
    or the rca skill dir
  - Bash: `git -C <registered project path> log|show|blame|diff|grep ...` with no shell operators
  - WebSearch with plain-text queries (no URLs/domains), to look up answers on the open internet
  - WebFetch is always denied, so links found in alert text/logs are never opened
  - Bash: `<nimi checkout>/nimi-service/bin/nimi-sql "<one SQL statement>"` — read-only SQL over NIMI's history
    (past bot messages, previous RCAs) as a database role that can only read two views
Everything else is denied, so nothing on the local machine can be modified.

Every denial is appended to ~/.local/state/nimi/guard-denials.jsonl (session id, tool, reason, severity:
"low" for the model's own formatting mistakes, "high" for real rule violations). The nimi service turns
these into red flags. Logging never affects the allow/deny decision.
"""
import json
import re
import os
import shlex
import sys
import time

DENIAL_LOG = os.path.join(os.path.expanduser("~"), ".local", "state", "nimi", "guard-denials.jsonl")
_INPUT = {}   # the hook input, kept for denial logging

PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SKILL_DIR = os.path.realpath(os.path.join(PROJECT_DIR, ".claude", "skills", "rca"))
REGISTRY = os.path.join(SKILL_DIR, "projects", "local", "registry.json")   # this deployment's projects (git-ignored)


def load_code_roots():
    """Allowed project paths from the registry. Missing/invalid registry -> no project paths (fail closed)."""
    try:
        with open(REGISTRY) as f:
            projects = json.load(f).get("projects", {})
    except (OSError, ValueError, AttributeError):
        return []
    roots = []
    for project in projects.values():
        for path in (project.get("paths") or []) if isinstance(project, dict) else []:
            if isinstance(path, str) and os.path.isabs(os.path.expanduser(path)):
                real = os.path.realpath(os.path.expanduser(path))
                if real != "/" and real not in roots:
                    roots.append(real)
    return roots


CODE_ROOTS = load_code_roots()
READ_ROOTS = CODE_ROOTS + [SKILL_DIR]

GIT_SUBCOMMANDS = {"log", "show", "blame", "diff", "grep"}
# Git flags that read files outside the repo, write files, or run external programs.
GIT_BLOCKED_FLAGS = (
    "--no-index", "--output", "--contents", "--ext-diff", "--textconv",
    "--open-files-in-pager", "-O", "-f", "--file", "--exec",
)
SHELL_METACHARS = set(";&|`$<>\n\\")

# Read-only SQL over NIMI's history (nimi-service/bin/nimi-sql in the same checkout as this hook; connects as
# the skill reader role).
NIMI_SQL = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "..", "..", "..", "nimi-service", "bin", "nimi-sql"))
NIMI_SQL_RE = re.compile(r"""\s*(\S+)\s+('[^']*'|"[^"]*")\s*""", re.S)


def _summarize_input(tool, ti):
    """What was attempted, without content that could hold sensitive data (e.g. full search queries)."""
    if tool == "Read":
        return {"path": str(ti.get("file_path", ""))[:300]}
    if tool in ("Grep", "Glob"):
        return {"path": str(ti.get("path", ""))[:300]}
    if tool == "Bash":
        return {"command": str(ti.get("command", ""))[:200]}
    if tool == "WebSearch":
        return {"query_length": len(str(ti.get("query", "")))}
    if tool == "WebFetch":
        return {"url_host": re.sub(r"^[a-z]+://([^/]+).*", r"\1", str(ti.get("url", "")))[:200]}
    if tool == "Skill":
        return {"skill": str(ti.get("skill", ""))[:100]}
    return {}


def log_denial(reason, severity):
    try:
        os.makedirs(os.path.dirname(DENIAL_LOG), exist_ok=True)
        tool = _INPUT.get("tool_name", "")
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "session_id": _INPUT.get("session_id"),
                 "agent_id": _INPUT.get("agent_id"), "tool": tool, "severity": severity, "reason": reason[:300],
                 "input": _summarize_input(tool, _INPUT.get("tool_input") or {})}
        with open(DENIAL_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass   # logging must never change the decision


def decide(decision, reason, severity="high"):
    if decision == "deny":
        log_denial(reason, severity)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def under(path, roots):
    real = os.path.realpath(os.path.expanduser(path))
    return any(real == r or real.startswith(r + os.sep) for r in roots)


def allowed_roots_msg():
    if not CODE_ROOTS:
        return f"No project paths are registered (check {REGISTRY}); only the rca skill folder is readable."
    return "RCA may only access the registered project paths: " + ", ".join(CODE_ROOTS) + " (absolute paths)."


def check_path_tool(tool, ti):
    if tool == "Read":
        path = ti.get("file_path", "")
    else:  # Grep / Glob: path is required so the default (cwd) can't be used
        path = ti.get("path")
        if not path:
            decide("deny", f"{tool} must set an explicit `path`. " + allowed_roots_msg(), "low")
        if tool == "Glob":
            pattern = ti.get("pattern", "")
            if os.path.isabs(pattern) or pattern.startswith("~") or ".." in pattern.split("/"):
                decide("deny", "Glob pattern escapes the allowed roots. " + allowed_roots_msg())
    if not os.path.isabs(os.path.expanduser(path)):
        decide("deny", f"{tool} path must be absolute. " + allowed_roots_msg(), "low")
    if not under(path, READ_ROOTS):
        decide("deny", f"{path} is outside the allowed RCA sources. " + allowed_roots_msg())
    decide("allow", "Path is inside an allowed RCA source.")


def check_nimi_sql(cmd):
    if any(c in SHELL_METACHARS for c in cmd):
        decide("deny", (
            "Characters ; & | ` $ < > \\ and newlines are not allowed in Bash commands, even inside the SQL. "
            "Write one statement without them: no trailing ';'; ranges with BETWEEN (e.g. created_at BETWEEN "
            "'2026-01-15 08:00+00' AND '2026-01-15 09:00+00'; open-ended: BETWEEN x AND 'infinity'); "
            "'not equal' as NOT (a = b) or IS DISTINCT FROM; concat() instead of ||; one line."
        ), "low")
    m = NIMI_SQL_RE.fullmatch(cmd)
    if not m or os.path.realpath(os.path.expanduser(m.group(1))) != NIMI_SQL:
        decide("deny", f'Use exactly: {NIMI_SQL} "<one SQL statement>" (the whole query in one '
                       "pair of double quotes, single quotes for SQL strings inside it, nothing else on the line).",
               "low")
    if len(cmd) > 4200:
        decide("deny", "SQL too long (max 4000 characters).", "low")
    decide("allow", "Read-only SQL over NIMI's history.")


def check_bash(ti):
    cmd = ti.get("command", "")
    if os.path.basename((cmd.strip().split() or [""])[0]) == "nimi-sql":
        check_nimi_sql(cmd)
    if any(c in SHELL_METACHARS for c in cmd):
        decide("deny", (
            "Characters ; & | ` $ < > \\ and newlines are not allowed anywhere in Bash commands, even inside quotes. "
            "Run a single `git -C <repo> <log|show|blame|diff|grep> ...` command. Alternatives: "
            "for git --format use spaces or %x09 (tab) as separators, e.g. --format=\"%h %ad %an %s\"; "
            "for alternation in git grep use multiple patterns, e.g. git grep -n -e foo -e bar; "
            "for regex searches (|, \\, escapes) use the Grep tool instead of Bash."
        ), "low")
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        decide("deny", "Could not parse command.", "low")
    if len(tokens) < 4 or tokens[0] != "git" or tokens[1] != "-C":
        decide("deny", "Only `git -C <repo> <log|show|blame|diff|grep> ...` is allowed in RCA.")
    repo = os.path.realpath(os.path.expanduser(tokens[2]))
    if repo not in CODE_ROOTS:
        decide("deny", "git -C target must be a registered project path. " + allowed_roots_msg())
    if tokens[3] not in GIT_SUBCOMMANDS:
        decide("deny", f"git subcommand `{tokens[3]}` not allowed. Allowed: {', '.join(sorted(GIT_SUBCOMMANDS))}.",
               "low" if tokens[3] in ("status", "rev-parse", "ls-files", "branch", "describe") else "high")
    for t in tokens[4:]:
        for flag in GIT_BLOCKED_FLAGS:
            if t == flag or t.startswith(flag + "=") or (len(flag) == 2 and t.startswith(flag) and not t.startswith("--")):
                decide("deny", f"git flag `{t}` is not allowed in RCA.")
    decide("allow", "Read-only git command on an allowed repo.")


URL_LIKE = re.compile(
    r"(?i)([a-z][a-z0-9+.-]*://"            # scheme://
    r"|\bwww\."                             # www.
    r"|\b\d{1,3}(\.\d{1,3}){3}\b"            # IPv4
    r"|\b[a-z0-9-]+(\.[a-z0-9-]+)*\.(com|net|org|io|co|dev|app|cloud|ai|gov|edu|info|biz|me|xyz|internal|local|corp|uk|de|fr|nl|eu|us|ca|au|jp|in|id|sg|br)\b"  # bare domain
    r"|\bsite:)"
)


def check_web_search(ti):
    query = ti.get("query", "")
    if URL_LIKE.search(query):
        decide("deny", "WebSearch queries must be plain text without URLs, domains, or IPs (never search for links from the alert/logs).")
    if ti.get("allowed_domains"):
        decide("deny", "WebSearch must not be restricted to specific domains during RCA.")
    decide("allow", "Plain-text web search is allowed.")


def main():
    data = json.load(sys.stdin)
    _INPUT.update(data if isinstance(data, dict) else {})
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}
    if tool in ("Read", "Grep", "Glob"):
        check_path_tool(tool, ti)
    elif tool == "Bash":
        check_bash(ti)
    elif tool == "Skill":
        # Headless runs (nimi-session-settings.json) guard the main session too; it may only launch /rca.
        if ti.get("skill") == "rca":
            decide("allow", "Launching the rca skill.")
        decide("deny", "Only the rca skill may be used.")
    elif tool == "WebSearch":
        check_web_search(ti)
    elif tool == "WebFetch":
        decide("deny", "Opening URLs is not allowed during RCA (links in the alert/logs must never be opened). Use WebSearch with a plain-text query instead.")
    decide("deny", f"Tool `{tool}` is not allowed during RCA.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # fail closed: any guard bug denies the call
        decide("deny", f"RCA guard error ({type(e).__name__}); call denied.", "medium")
