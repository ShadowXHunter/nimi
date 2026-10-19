# Messaging providers

Everything provider-specific lives in `nimi_service/providers/`. The rest of the service only uses the
interface in `providers/base.py`:

| Method | Purpose |
|---|---|
| `normalize(raw)` | Provider payload → neutral `Message` (also used by `reparse` on stored payloads). |
| `iter_messages(channel, after, before)` | List messages: top-level and thread replies. |
| `list_reactions(message)` | Individual reactions on a message. |
| `post_reply(channel, thread, text)` | Reply in a thread. |
| `message_link(message)`, `bold(text)`, `link(url)` | Links and markup for replies. |
| `normalize_channel_id(value)` | Canonical channel ID from user input. |

Providers are registered in `providers/__init__.py` (`PROVIDERS = {"gchat": GChatProvider}`). To add one,
see [Adding a provider](../development/adding-a-provider.md).

## Google Chat

- **Auth:** user OAuth token (created once by `gchat-login`, refreshed automatically; see
  [Google Chat access](../getting-started/google-chat-access.md)); the service reads and posts as that user. Scopes: `chat.spaces.readonly`, `chat.messages.readonly`, `chat.messages.create`.
- **Messages:** top-level messages and thread replies are told apart by the thread; sender `type` `BOT` /
  `HUMAN` becomes `bot` / `human`. Cards are rendered into `rendered_text`.
- **Links:** `https://chat.google.com/room/<space>/<thread>/<message>?cls=10`.

### API limits

All calls go through a process-wide rate limiter and retry policy (`providers/gchat/client.py`), well
below Google's limits:

| | Google limit | NIMI |
|---|---|---|
| Reads per space (list/get messages, list reactions) | 15/s, shared by all apps in the space | 5/s |
| Writes per space (post a reply) | 1/s | 1 per 2s |
| Reads per project | 3,000/min | 1,000/min |
| Writes per project | 3,000/min | 300/min |

429 and 5xx responses are retried with truncated exponential backoff and jitter (max 32s, 6 retries).
Posting retries only 429: a 5xx or timeout after sending is not retried, to avoid duplicate replies.
