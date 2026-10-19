"""The registered projects (repositories the RCA skill reads), as seen by the service.

Read from the skill's registry, nimi-skill/.claude/skills/rca/projects/local/registry.json:

  "projects": {
    "shop": {
      "paths": ["/abs/path/to/shop-copy"],          # local checkout(s) (the guard allows only these)
      "display_name": "acme/shop",                   # optional: how people know it (default: the key)
      "web_url": "https://github.com/acme/shop",     # optional: for code links (default: from `origin`)
      "sync": "origin/main"                          # optional: keep this NIMI-only copy updated (see sync())
    }
  }

Used for:
  - code citations `[label](code:<project>@<commit>/<path>#L<line>)` -> links to the file at that commit;
  - rewriting absolute local paths in replies to `<project>: <path>` (local layouts never reach chat);
  - opt-in sync of NIMI-only copies: fetch + fast-forward to the configured ref, but only when the checkout
    has no uncommitted changes and no local commits; otherwise it's left alone and a red flag is raised.
The skill never runs any of this: it stays read-only.
"""
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

GIT_TIMEOUT = 120
SAFE_PATH_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[\w.@+/-]{1,400}$")
SYNC_REF_RE = re.compile(r"^[\w.-]+/[\w./-]+$")          # <remote>/<branch>, e.g. origin/main


@dataclass
class Project:
    name: str
    paths: list
    display_name: str
    web_url: Optional[str] = None
    sync: Optional[str] = None


@dataclass
class Registry:
    projects: dict = field(default_factory=dict)

    def path_prefixes(self) -> list:
        """(absolute path, display name), longest first, for rewriting local paths."""
        out = [(str(Path(p).expanduser()).rstrip("/"), pr.display_name)
               for pr in self.projects.values() for p in pr.paths]
        return sorted(out, key=lambda x: -len(x[0]))


def registry_file(settings) -> Path:
    return settings.rca_project / ".claude/skills/rca/projects/local/registry.json"


def load(settings) -> Registry:
    """The registry; empty if it's missing or invalid (the guard then allows nothing either)."""
    try:
        data = json.loads(registry_file(settings).read_text())
    except (OSError, ValueError):
        return Registry()
    reg = Registry()
    for name, raw in (data.get("projects") or {}).items():
        if not isinstance(raw, dict) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", name):
            continue
        paths = [p for p in raw.get("paths") or [] if isinstance(p, str) and Path(p).expanduser().is_absolute()]
        sync = raw.get("sync") if isinstance(raw.get("sync"), str) and SYNC_REF_RE.match(raw.get("sync")) else None
        reg.projects[name] = Project(name, paths, str(raw.get("display_name") or name),
                                     normalize_web_url(raw.get("web_url")) if raw.get("web_url") else None, sync)
    return reg


# --- web URLs and code links -------------------------------------------------------------------------

def normalize_web_url(remote: str) -> Optional[str]:
    """A git remote (ssh or https, with or without credentials or .git) -> https web URL; None if unknown.
    git@github.com:acme/shop.git / ssh://git@host/acme/shop / https://user:token@host/acme/shop.git
    -> https://github.com/acme/shop"""
    remote = (remote or "").strip()
    m = (re.match(r"^[\w.-]+@([\w.-]+):(.+?)(?:\.git)?/?$", remote)                       # scp-like ssh
         or re.match(r"^(?:ssh|git|https?)://(?:[^@/]+@)?([\w.-]+)(?::\d+)?/(.+?)(?:\.git)?/?$", remote))
    if not m or not re.fullmatch(r"[\w.-]+/[\w./-]+", m.group(2)):
        return None
    return f"https://{m.group(1)}/{m.group(2)}"


_web_cache: dict = {}


def web_url(project: Project) -> Optional[str]:
    """The project's web URL: configured, else from the first checkout's `origin` remote (cached)."""
    if project.web_url:
        return project.web_url
    if project.name not in _web_cache:
        url = None
        if project.paths:
            try:
                out = subprocess.run(["git", "-C", str(Path(project.paths[0]).expanduser()), "remote", "get-url",
                                      "origin"], capture_output=True, text=True, timeout=10)
                url = normalize_web_url(out.stdout) if out.returncode == 0 else None
            except (OSError, subprocess.TimeoutExpired):
                url = None
        _web_cache[project.name] = url
    return _web_cache[project.name]


def code_url(base: str, commit: str, path: str, line: Optional[int] = None, end: Optional[int] = None) -> Optional[str]:
    """Link to a file (and line) at a commit on GitHub / GitLab / Bitbucket-style hosts; None if unsafe."""
    if not base or not re.fullmatch(r"[0-9a-f]{7,40}", commit or "") or not SAFE_PATH_RE.match(path or ""):
        return None
    host = base.split("/")[2].lower()
    if "bitbucket" in host:
        url = f"{base}/src/{commit}/{path}" + (f"#lines-{line}" + (f":{end}" if end else "") if line else "")
    elif "gitlab" in host:
        url = f"{base}/-/blob/{commit}/{path}" + (f"#L{line}" + (f"-{end}" if end else "") if line else "")
    else:                                            # GitHub and GitHub-like (Gitea, Forgejo)
        url = f"{base}/blob/{commit}/{path}" + (f"#L{line}" + (f"-L{end}" if end else "") if line else "")
    return url


def code_links(registry: Registry) -> dict:
    """project name -> web URL (only projects that have one)."""
    return {name: url for name, p in registry.projects.items() if (url := web_url(p))}


def relativize_paths(text: str, registry: Registry) -> str:
    """Absolute local paths of registered checkouts -> "<display name>: <path in repo>"."""
    for prefix, name in registry.path_prefixes():
        if prefix:
            text = re.sub(re.escape(prefix) + r"/", f"{name}: ", text)
            text = re.sub(re.escape(prefix) + r"(?![\w/.-])", name, text)
    return text


# --- opt-in sync of NIMI-only copies ----------------------------------------------------------------

_last_sync: dict = {}


def _git(path: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=GIT_TIMEOUT)


def sync_project(project: Project) -> tuple:
    """Fetch and fast-forward each checkout of a project to its sync ref. Returns (updated, problems).
    Never discards work: a checkout with uncommitted changes or local commits is skipped (a problem)."""
    remote, _, branch = project.sync.partition("/")
    updated, problems = [], []
    for path in (str(Path(p).expanduser()) for p in project.paths):
        try:
            if _git(path, "status", "--porcelain", "--untracked-files=no").stdout.strip():
                problems.append(f"{project.name}: {path} has uncommitted changes; not synced")
                continue
            fetched = _git(path, "fetch", "--quiet", remote, branch)
            if fetched.returncode != 0:
                problems.append(f"{project.name}: fetch of {project.sync} failed: {fetched.stderr.strip()[:200]}")
                continue
            ahead = _git(path, "rev-list", "--count", f"{project.sync}..HEAD").stdout.strip()
            if ahead not in ("", "0"):
                problems.append(f"{project.name}: {path} has {ahead} local commit(s) not in {project.sync}; not synced")
                continue
            before = _git(path, "rev-parse", "HEAD").stdout.strip()
            moved = _git(path, "checkout", "--quiet", "--detach", project.sync)
            if moved.returncode != 0:
                problems.append(f"{project.name}: checkout of {project.sync} failed: {moved.stderr.strip()[:200]}")
                continue
            if _git(path, "rev-parse", "HEAD").stdout.strip() != before:
                updated.append(path)
        except (OSError, subprocess.TimeoutExpired) as e:
            problems.append(f"{project.name}: sync failed ({type(e).__name__})")
    return updated, problems


def sync_due(settings, now: float = None) -> list:
    """Projects with `sync` whose last sync is older than NIMI_SYNC_MINUTES."""
    now = time.monotonic() if now is None else now
    return [p for p in load(settings).projects.values()
            if p.sync and now - _last_sync.get(p.name, -1e12) >= settings.sync_minutes * 60]


def sync_all(settings, now: float = None) -> tuple:
    """Sync the projects that are due. Returns (updated paths, problems)."""
    now = time.monotonic() if now is None else now
    updated, problems = [], []
    for project in sync_due(settings, now):
        _last_sync[project.name] = now
        u, p = sync_project(project)
        updated += u
        problems += p
    return updated, problems
