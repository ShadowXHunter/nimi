# Google Chat access

NIMI reads (and, when enabled, posts) in Google Chat as **your user**, with an OAuth token that you
create once. It doesn't use a bot or a service account: it sees exactly the spaces you are in, and its
thread replies appear as posted by you (with NIMI's header and disclaimer).

You need a Google Cloud project, an OAuth client for it, and one browser login. Google renames console
menus now and then; the steps below say what to do rather than exact menu paths.

## 1. Google Cloud project

Use a project in your organization's Google Cloud (ideally one owned by your team rather than a personal
project), or create one. You need permission to enable APIs and create OAuth clients in it.

## 2. Enable the Google Chat API

In the project's API library, enable **Google Chat API**.

## 3. Configure the Chat app

Google requires a Chat app configuration in the project before the Chat API can be used, even with user
credentials. In the Chat API's **Configuration** page, fill in the minimum: app name (e.g. `NIMI`), an
avatar URL and a description. NIMI doesn't run as a Chat app, so it doesn't need to be published or added
to any space. Without this step, API calls fail with an error saying the Chat app was not found.

## 4. OAuth consent screen

Set up the project's OAuth consent screen (Google Auth Platform):

- **Audience: Internal** if the project belongs to your Google Workspace organization. Only users in your
  organization can log in, and no Google verification is needed.
- **External** works too, but while the app is in **Testing** you must add yourself as a test user, and
  Google expires refresh tokens after 7 days in that state — the service would stop being able to
  refresh its token every week. Prefer Internal.
- Scopes (optional to declare here; the login requests them anyway):
  `https://www.googleapis.com/auth/chat.spaces.readonly`,
  `https://www.googleapis.com/auth/chat.messages.readonly`,
  `https://www.googleapis.com/auth/chat.messages.create`.

## 5. Create an OAuth client

Create an OAuth client ID of application type **Desktop app**, and download its JSON file (named like
`client_secret_<id>.apps.googleusercontent.com.json`). Keep it private; you only need it for logging in.

## 6. Log in

On a machine with a browser, from `nimi-service/`:

```bash
.venv/bin/python -m nimi_service gchat-login --client-secret ~/Downloads/client_secret_XXXX.json
```

A browser opens (if not, open the URL it prints). Log in with the account NIMI should act as and accept
the three permissions. The token is saved to `~/.config/nimi/gchat-token.json` (chmod 600), or to
`NIMI_GCHAT_TOKEN_FILE` if set. The token contains what it needs to refresh itself, so you can delete the
downloaded client file now.

**Server without a browser?** Run `gchat-login` on your own machine, then copy the token file to the
server (same path or `NIMI_GCHAT_TOKEN_FILE`) and `chmod 600` it.

## 7. Check it

```bash
.venv/bin/python -m nimi_service gchat-spaces          # named spaces and their IDs; --all adds direct messages
```

Note the IDs of the spaces to monitor (`spaces/AAAA…`) for [First run](first-run.md).

## What the token can do

| Scope | Used for |
|---|---|
| `chat.spaces.readonly` | Listing spaces and reading their details |
| `chat.messages.readonly` | Reading messages, thread replies and reactions |
| `chat.messages.create` | Posting RCA replies (only when `NIMI_POST_ENABLED=true`) |

It can't edit or delete messages or manage spaces or members. It can read every space **you** can see;
the service only polls the channels you register.

**Treat the token like a password.** Anyone with the file can read your chats and post as you until access
is revoked. It lives outside the repo, and the RCA skill can't read it.

## Keeping it working

The service refreshes the access token automatically (about hourly) and writes the refreshed token back.
It never opens a browser: if the token can't be refreshed, it stops and tells you to log in again.
Run `gchat-login` again when:

- you revoke NIMI's access, or change the account's password (Google may revoke tokens then);
- the OAuth client is deleted or its secret is reset;
- the app is External and in Testing (tokens expire after 7 days);
- the token has not been used for about six months;
- NIMI's required scopes change (the service reports "lacks required scopes").

To revoke access: Google Account → Security → third-party connections → remove the app.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `… not found: log in with … gchat-login …` | No token at `NIMI_GCHAT_TOKEN_FILE`: run `gchat-login`. |
| `… lacks required scopes` | The token was created with other scopes: run `gchat-login` again. |
| `… is invalid and cannot be refreshed` / `invalid_grant` | Token revoked or expired (see the list above): run `gchat-login` again. |
| Error mentioning that the Chat app was not found | Step 3 is missing: configure the Chat app in the project. |
| Login page says access is blocked or needs admin approval | Your Workspace admin restricts third-party apps: ask them to allow the OAuth client ID (or use an Internal app in an organization project). |
| `403` / `404` for a specific space | Your account isn't a member of that space (or it was deleted). |
| `429` warnings in the log | Rate limiting: the client already backs off and retries; see [Messaging providers](../architecture/providers.md#api-limits). |
