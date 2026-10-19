"""Google Chat API access with the user's OAuth token (created by `python -m nimi_service gchat-login`).

All calls go through a process-wide rate limiter and Google's recommended retry policy.
Google's limits (https://developers.google.com/workspace/chat/limits):
  - per space:   15 reads/s (shared by all apps in the space), 1 write/s
  - per project: 3,000 message reads/min, 3,000 reaction reads/min, 3,000 message writes/min
  - exceeded -> HTTP 429; retry with truncated exponential backoff + jitter
We stay well below them, since other apps share the per-space budget.
"""
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import random
import threading
import time
from collections import deque
from typing import Iterator, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/chat.messages.create",
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
]

# Our budgets, as fractions of Google's limits.
SPACE_READS_PER_SEC = 5            # Google: 15/s per space, shared with other apps
SPACE_WRITE_INTERVAL_SEC = 2.0     # Google: 1 write/s per space
PROJECT_READS_PER_MIN = 1000       # Google: 3,000/min per project
PROJECT_WRITES_PER_MIN = 300       # Google: 3,000/min per project

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 6
MAX_BACKOFF_SEC = 32.0


class _SlidingWindow:
    """At most `limit` events per `window` seconds."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window, self.events = limit, window, deque()

    def wait_time(self, now: float) -> float:
        while self.events and now - self.events[0] >= self.window:
            self.events.popleft()
        return 0.0 if len(self.events) < self.limit else self.window - (now - self.events[0])

    def record(self, now: float) -> None:
        self.events.append(now)


class RateLimiter:
    """Shared by all ChatClient instances and threads in the process."""

    def __init__(self):
        self.lock = threading.Lock()
        self.space_reads, self.space_writes = {}, {}
        self.project_reads = _SlidingWindow(PROJECT_READS_PER_MIN, 60.0)
        self.project_writes = _SlidingWindow(PROJECT_WRITES_PER_MIN, 60.0)

    def acquire(self, kind: str, space_id: str) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                if kind == "write":
                    per_space = self.space_writes.setdefault(space_id, _SlidingWindow(1, SPACE_WRITE_INTERVAL_SEC))
                    project = self.project_writes
                else:
                    per_space = self.space_reads.setdefault(space_id, _SlidingWindow(SPACE_READS_PER_SEC, 1.0))
                    project = self.project_reads
                wait = max(per_space.wait_time(now), project.wait_time(now))
                if wait <= 0:
                    per_space.record(now)
                    project.record(now)
                    return
            time.sleep(wait)


LIMITER = RateLimiter()


def _status(e: HttpError):
    return getattr(e, "status_code", None) or (e.resp.status if e.resp is not None else None)


def _backoff(attempt: int) -> float:
    return min(2 ** attempt + random.random(), MAX_BACKOFF_SEC)


def _execute(request, kind: str, space_id: str, retry_statuses=RETRY_STATUSES):
    """Rate-limit, execute, and retry with truncated exponential backoff + jitter."""
    for attempt in range(MAX_RETRIES + 1):
        LIMITER.acquire(kind, space_id)
        try:
            return request.execute(num_retries=0)
        except HttpError as e:
            reason = _status(e)
            if reason not in retry_statuses or attempt == MAX_RETRIES:
                raise
        except (TimeoutError, ConnectionError) as e:
            if kind == "write" or attempt == MAX_RETRIES:
                raise   # a write may have gone through; don't risk a duplicate post
            reason = type(e).__name__
        delay = _backoff(attempt)
        log.warning("Chat API %s on %s (%s); retry %d/%d in %.1fs", reason, space_id, kind,
                    attempt + 1, MAX_RETRIES, delay)
        time.sleep(delay)


WEBHOOK_URL_RE = re.compile(r"^https://chat\.googleapis\.com/v1/(spaces/[A-Za-z0-9_-]+)/messages\?(.+)$")


def webhook_space(webhook_url: str) -> str:
    """spaces/<id> of a Google Chat incoming webhook URL; ValueError (without the URL) if it isn't one."""
    m = WEBHOOK_URL_RE.match(webhook_url or "")
    if not m or "key=" not in m.group(2) or "token=" not in m.group(2):
        raise ValueError("NIMI_POST_WEBHOOK is not a Google Chat incoming webhook URL "
                         "(https://chat.googleapis.com/v1/spaces/<space>/messages?key=…&token=…)")
    return m.group(1)


def post_webhook(webhook_url: str, text: str, thread_key: str = None, timeout: int = 30) -> tuple:
    """Post to an incoming webhook (the message appears from the webhook, not the user). Without a thread_key
    every post is a new top-level message in the space; with one, posts sharing it go into one thread.
    Retries only 429. Errors never include the URL (it holds a secret key)."""
    space = webhook_space(webhook_url)
    url = webhook_url
    if thread_key:
        url += "&" + urllib.parse.urlencode({"threadKey": thread_key[:120],
                                              "messageReplyOption": "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"})
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json; charset=UTF-8"}, method="POST")
    for attempt in range(MAX_RETRIES + 1):
        LIMITER.acquire("write", space)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read() or b"{}")
            return body.get("name", ""), (body.get("thread") or {}).get("name", "")
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == MAX_RETRIES:
                raise RuntimeError(f"webhook post failed: HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"webhook post failed: {type(e.reason).__name__}") from None
        time.sleep(_backoff(attempt))


def _space_of(resource_id: str) -> str:
    parts = (resource_id or "").split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else resource_id


LOGIN_HINT = "log in with `python -m nimi_service gchat-login --client-secret <oauth client file>`"


def _write_token(token_file, creds: Credentials) -> None:
    """Write the token readable only by the owner (created 0600, never briefly world-readable)."""
    fd = os.open(str(token_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())
    os.chmod(token_file, 0o600)


def login(client_secret_file, token_file) -> Credentials:
    """One-time browser login (installed-app OAuth flow) with exactly SCOPES. The saved token contains
    what is needed to refresh it, so the client secret file isn't needed afterwards."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    os.makedirs(os.path.dirname(os.path.abspath(token_file)), mode=0o700, exist_ok=True)
    _write_token(token_file, creds)
    return creds


def get_credentials(token_file) -> Credentials:
    """Load and refresh the stored token. Never opens a browser: the service runs unattended."""
    if not os.path.exists(token_file):
        raise SystemExit(f"{token_file} not found: {LOGIN_HINT}")
    creds = Credentials.from_authorized_user_file(str(token_file))
    if not creds.has_scopes(SCOPES):
        raise SystemExit(f"{token_file} lacks required scopes: {LOGIN_HINT}")
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _write_token(token_file, creds)
        else:
            raise SystemExit(f"{token_file} is invalid and cannot be refreshed: {LOGIN_HINT}")
    return creds


class ChatClient:
    post_webhook = staticmethod(post_webhook)

    def __init__(self, token_file):
        self.token_file = token_file
        self._service = None
        self._creds_token = None

    @property
    def service(self):
        # Rebuild after refresh so long-running processes keep a valid token.
        creds = get_credentials(self.token_file)
        if self._service is None or self._creds_token != creds.token:
            self._service = build("chat", "v1", credentials=creds, cache_discovery=False)
            self._creds_token = creds.token
        return self._service

    def list_spaces(self) -> list:
        """Spaces the user is a member of."""
        out, page_token = [], None
        while True:
            req = self.service.spaces().list(pageSize=1000, pageToken=page_token)
            resp = _execute(req, "read", "spaces")
            out.extend(resp.get("spaces", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    def iter_messages(self, space_id: str, after_rfc3339: str, before_rfc3339: Optional[str] = None) -> Iterator[dict]:
        """All messages (top-level + thread replies) created in (after, before), oldest first."""
        flt = f'createTime > "{after_rfc3339}"'
        if before_rfc3339:
            flt += f' AND createTime < "{before_rfc3339}"'
        page_token = None
        while True:
            req = self.service.spaces().messages().list(
                parent=space_id, filter=flt, orderBy="createTime asc", pageSize=1000, pageToken=page_token)
            resp = _execute(req, "read", space_id)
            yield from resp.get("messages", [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    def list_reactions(self, message_id: str) -> list:
        out, page_token = [], None
        space_id = _space_of(message_id)
        while True:
            req = self.service.spaces().messages().reactions().list(
                parent=message_id, pageSize=200, pageToken=page_token)
            resp = _execute(req, "read", space_id)
            out.extend(resp.get("reactions", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    def reply_in_thread(self, space_id: str, thread_id: str, text: str) -> str:
        """Post `text` as a reply in `thread_id`; returns the new message ID.
        Only 429 is retried: it means the message was not created. A 5xx or timeout after sending is
        ambiguous (it may have been posted), so it is raised instead of risking a duplicate reply."""
        req = self.service.spaces().messages().create(
            parent=space_id, body={"text": text, "thread": {"name": thread_id}},
            messageReplyOption="REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD")
        return _execute(req, "write", space_id, retry_statuses={429})["name"]
