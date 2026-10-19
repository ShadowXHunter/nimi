"""Settings: defaults below, overridden by env files, overridden by real environment variables.

Load order (later wins):
  1. defaults in this file
  2. ~/.config/nimi/service.env           (outside the repo; created by db/bootstrap.sh)
  3. nimi-service/.env                    (inside the repo folder, if present; git-ignored)
  4. environment variables (NIMI_*)
NIMI_ENV=/path/to/file replaces 2 and 3 with exactly that file.
See nimi-service/env.example and docs/reference/settings.md for every setting; `python -m nimi_service config`
shows what is in effect.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SERVICE_DIR = Path(__file__).resolve().parent.parent          # nimi-service/
REPO_DIR = SERVICE_DIR.parent                                 # the nimi checkout (nimi-service/, nimi-skill/)
USER_ENV_FILE = Path.home() / ".config/nimi/service.env"
REPO_ENV_FILE = SERVICE_DIR / ".env"


def env_files() -> list:
    """Env files to load, lowest precedence first."""
    explicit = os.environ.get("NIMI_ENV")
    return [Path(explicit)] if explicit else [USER_ENV_FILE, REPO_ENV_FILE]


def _load_env_file(path: Path) -> dict:
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                    value = value[1:-1]
                values[key.strip()] = value
    return values


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _list(value: str) -> tuple:
    return tuple(x.strip() for x in value.split(",") if x.strip())


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _target(value: str) -> str:
    value = value.strip().lower()
    if value not in ("thread", "webhook", "both"):
        raise SystemExit(f"NIMI_POST_TARGET: {value!r}; use thread, webhook or both")
    return value


def _sources(value: str) -> str:
    """Comma-separated subset of user, project, local (empty = no settings layers)."""
    parts = [p.strip() for p in value.split(",") if p.strip()]
    bad = [p for p in parts if p not in ("user", "project", "local")]
    if bad:
        raise SystemExit(f"NIMI_CLAUDE_SETTING_SOURCES: unknown layer(s) {bad}; use user, project, local")
    return ",".join(parts)


@dataclass
class Settings:
    # Database. Credentials are separate settings so they can be injected from a secrets manager
    # (Vault Agent, k8s secrets, ...). The password may come from NIMI_DB_PASSWORD, NIMI_DB_PASSWORD_FILE,
    # or neither (then libpq falls back to ~/.pgpass / trust / peer auth).
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "nimi"
    db_user: str = "nimi_service"
    db_password: str = field(default="", repr=False)
    db_password_file: Optional[Path] = None
    db_sslmode: str = ""

    # Read-only role for the skill's SQL access to history (`nimi-sql`): can read only the skill_messages and
    # skill_rcas views. Created by db/bootstrap.sh; `db upgrade` grants it access to the views.
    db_skill_user: str = "nimi_skill_reader"
    db_skill_password: str = field(default="", repr=False)
    db_skill_password_file: Optional[Path] = None
    skill_history: bool = True          # let RCA runs query history with nimi-sql (past messages and RCAs)

    # Provider credentials. Google Chat: user OAuth token, created by `nimi_service gchat-login`.
    gchat_token_file: Path = Path.home() / ".config/nimi/gchat-token.json"

    # Polling
    poll_seconds: int = 60
    backfill_days: int = 90
    overlap_seconds: int = 120          # re-read this much before the watermark to avoid gaps

    # Reactions -> feedback
    reaction_days: int = 7              # re-check reactions on RCA replies posted within this window
    reaction_interval_minutes: int = 10

    # Triage (model decides reuse vs new; code enforces limits)
    reuse_days: int = 7
    triage_model: str = "haiku"
    triage_timeout_seconds: int = 180
    history_limit: int = 10             # earlier same-signature messages shown to triage (most recent)

    # RCA triggering
    alert_senders: tuple = ()           # sender allowlist (provider IDs); empty = any bot sender
    max_rcas_per_day: int = 50
    max_parallel: int = 2
    max_attempts: int = 2
    rca_timeout_seconds: int = 900
    max_input_chars: int = 20000        # truncate very long messages before passing to the skill

    # RCA runner
    rca_project: Path = REPO_DIR / "nimi-skill"
    sync_minutes: int = 10              # opt-in project sync ("sync" in the registry): at most this often
    claude_bin: str = "claude"

    # Claude account / config for NIMI's Claude calls (see nimi_service/claude_cli.py)
    claude_config_dir: Optional[Path] = None          # unset = the default ~/.claude login and user config
    anthropic_api_key_file: Optional[Path] = None     # file with an API key (API billing instead of the login)
    claude_setting_sources: str = "user,project"      # settings layers for RCA runs (triage loads none)
    claude_pass_env: tuple = ()                       # extra env vars to pass through, e.g. HTTPS_PROXY

    # Admin notifications (red flags): incoming webhook of the admin's Chat space (a secret).
    admin_webhook: str = ""
    guard_denial_log: Path = Path.home() / ".local/state/nimi/guard-denials.jsonl"

    # Claude unavailable (usage limit, credit, auth): pause all Claude calls, then retry
    limit_cooldown_minutes: int = 15     # retry after this long when Claude gives no reset time
    stale_hours: int = 6                 # alerts older than this when NIMI can process them get no RCA
                                         # (after downtime, a usage-limit pause, or a restart)

    # Posting replies (off = dry run). Where: a reply in the alert's thread, a separate space via an incoming
    # webhook (with a link back to the alert), or both.
    post_enabled: bool = False
    post_target: str = "thread"         # thread | webhook | both
    post_webhook: str = field(default="", repr=False)   # incoming webhook URL of the destination space (secret)
    post_max_chars: int = 4000
    reply_disclaimer: str = ""          # first line of replies after "NIMI —"; empty = the built-in default

    # Where each value came from (for `python -m nimi_service config`); not a setting.
    sources: dict = field(default_factory=dict, repr=False)

    def resolved_db_password(self) -> str:
        return _password(self.db_password, self.db_password_file, "NIMI_DB_PASSWORD_FILE")

    def resolved_db_skill_password(self) -> str:
        return _password(self.db_skill_password, self.db_skill_password_file, "NIMI_DB_SKILL_PASSWORD_FILE")

    def _url(self, user: str, password: str):
        from sqlalchemy.engine import URL
        return URL.create("postgresql+psycopg", username=user or None, password=password or None,
                          host=self.db_host or None, port=self.db_port or None, database=self.db_name,
                          query={"sslmode": self.db_sslmode} if self.db_sslmode else {})

    @property
    def db_url(self):
        """SQLAlchemy URL built from the parts (special characters in the password are escaped)."""
        return self._url(self.db_user, self.resolved_db_password())

    @property
    def db_skill_url(self):
        """URL for the skill's read-only role (used only by nimi-sql)."""
        return self._url(self.db_skill_user, self.resolved_db_skill_password())


def _password(value: str, file: Optional[Path], file_setting: str) -> str:
    if value:
        return value
    if file:
        try:
            return file.read_text().strip()
        except OSError as e:
            raise SystemExit(f"cannot read {file_setting} {file}: {e}")
    return ""


# env var -> (Settings field, parser, secret?)
ENV_VARS = {
    "NIMI_DB_HOST": ("db_host", str, False),
    "NIMI_DB_PORT": ("db_port", int, False),
    "NIMI_DB_NAME": ("db_name", str, False),
    "NIMI_DB_USER": ("db_user", str, False),
    "NIMI_DB_PASSWORD": ("db_password", str, True),
    "NIMI_DB_PASSWORD_FILE": ("db_password_file", _path, False),
    "NIMI_DB_SSLMODE": ("db_sslmode", str, False),
    "NIMI_DB_SKILL_USER": ("db_skill_user", str, False),
    "NIMI_DB_SKILL_PASSWORD": ("db_skill_password", str, True),
    "NIMI_DB_SKILL_PASSWORD_FILE": ("db_skill_password_file", _path, False),
    "NIMI_SKILL_HISTORY": ("skill_history", _bool, False),
    "NIMI_GCHAT_TOKEN_FILE": ("gchat_token_file", _path, False),
    "NIMI_POLL_SECONDS": ("poll_seconds", int, False),
    "NIMI_BACKFILL_DAYS": ("backfill_days", int, False),
    "NIMI_REACTION_DAYS": ("reaction_days", int, False),
    "NIMI_TRIAGE_MODEL": ("triage_model", str, False),
    "NIMI_REUSE_DAYS": ("reuse_days", int, False),
    "NIMI_ALERT_SENDERS": ("alert_senders", _list, False),
    "NIMI_MAX_PER_DAY": ("max_rcas_per_day", int, False),
    "NIMI_MAX_PARALLEL": ("max_parallel", int, False),
    "NIMI_RCA_TIMEOUT_SECONDS": ("rca_timeout_seconds", int, False),
    "NIMI_RCA_PROJECT": ("rca_project", _path, False),
    "NIMI_SYNC_MINUTES": ("sync_minutes", int, False),
    "NIMI_CLAUDE_BIN": ("claude_bin", str, False),
    "NIMI_CLAUDE_CONFIG_DIR": ("claude_config_dir", _path, False),
    "NIMI_ANTHROPIC_API_KEY_FILE": ("anthropic_api_key_file", _path, False),
    "NIMI_CLAUDE_SETTING_SOURCES": ("claude_setting_sources", _sources, False),
    "NIMI_CLAUDE_PASS_ENV": ("claude_pass_env", _list, False),
    "NIMI_ADMIN_WEBHOOK": ("admin_webhook", str, True),
    "NIMI_GUARD_DENIAL_LOG": ("guard_denial_log", _path, False),
    "NIMI_LIMIT_COOLDOWN_MINUTES": ("limit_cooldown_minutes", int, False),
    "NIMI_STALE_HOURS": ("stale_hours", int, False),
    "NIMI_POST_ENABLED": ("post_enabled", _bool, False),
    "NIMI_POST_TARGET": ("post_target", _target, False),
    "NIMI_POST_WEBHOOK": ("post_webhook", str, True),
    "NIMI_POST_MAX_CHARS": ("post_max_chars", int, False),
    "NIMI_REPLY_DISCLAIMER": ("reply_disclaimer", str, False),
}


def load_settings() -> Settings:
    values, sources = {}, {}
    for path in env_files():
        for key, value in _load_env_file(path).items():
            values[key], sources[key] = value, str(path)
    for key in list(ENV_VARS) + ["NIMI_DB_DSN"]:
        if key in os.environ:
            values[key], sources[key] = os.environ[key], "environment"

    if "NIMI_DB_DSN" in values:
        raise SystemExit("NIMI_DB_DSN is no longer supported: use NIMI_DB_HOST / NIMI_DB_PORT / NIMI_DB_NAME / "
                         "NIMI_DB_USER / NIMI_DB_PASSWORD (or NIMI_DB_PASSWORD_FILE). See nimi-service/env.example.")
    s = Settings()
    for key, (attr, parse, _secret) in ENV_VARS.items():
        if key in values:
            setattr(s, attr, parse(values[key]))
    s.sources = {key: sources[key] for key in ENV_VARS if key in sources}
    return s


def describe(s: Settings) -> list:
    """(env var, effective value with secrets masked, source) for every setting."""
    rows = []
    for key, (attr, _parse, secret) in ENV_VARS.items():
        value = getattr(s, attr)
        shown = ",".join(value) if isinstance(value, tuple) else str(value)
        if secret and shown:
            shown = "<set, hidden>"
        if value is None:
            shown = ""
        rows.append((key, shown, s.sources.get(key, "default")))
    return rows
