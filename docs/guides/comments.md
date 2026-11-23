# Comments on RCAs

An RCA is sometimes only half right, and the real cause is often found later by people, with access NIMI
doesn't have. A **comment** puts that knowledge back into NIMI: write `!nimi` and what you found in the
chat thread. The next time the alert fires, NIMI analyzes it again with your comment as input, instead of
reusing the RCA you commented on.

A comment never starts work by itself. Nothing runs when you write one, and NIMI doesn't reply to it.

## Writing a comment

Write `!nimi` anywhere in a chat message, followed or preceded by what you want NIMI to know:

```text
!nimi the export job was paused for the shop-prod migration, the gap is expected until Friday
!nimi: orders_archive has no index on updated_at, the cause is the new reporting query
the cause was the expired certificate on the payment gateway — !nimi
```

- **Any case** (`!NIMI`), anywhere in the message. A `:`, `,`, `;` or dash right after it is removed with it.
- **Whole word only:** `x!nimi`, `!nimify` and `@NIMI` don't count.
- **Not inside code:** `` `!nimi` `` and code blocks are usually pasted output, so they don't count. Code
  in a comment is kept as written.
- **A message with only `!nimi`** is not a comment.

Write what you checked, in your own words: no keywords or commands are needed. "That's wrong", "it was the
upstream job" and "also check the replica lag" all work the same way. Name the item when the RCA covered
several (a table, a host, a queue): NIMI works out which items a comment is about, and a name makes that
easy.

## Where to write it

| Where | The comment belongs to |
|---|---|
| A thread with a NIMI reply, after the reply | The RCA(s) that reply showed. For a reuse reply, every earlier RCA it pointed to. |
| The alert's thread, before NIMI replied | The alert's request: its RCA run gets the comment as input. |
| A thread of a NIMI post in the [webhook space](where-replies-go.md) | Same as the first row. |
| Anywhere else (a top-level message, an unknown thread) | Stored **unattached**: see `comments --unattached`, attach it with `comments attach`. |

Every comment also keeps the alert's signature, so later RCAs of the same alert find it.

## What happens next

1. **NIMI stores it** within a poll interval: trigger removed, secrets masked, long text cut
   (`NIMI_COMMENT_MAX_CHARS`).
2. **The RCA you commented on is no longer reused,** whatever the comment says. A 👍 doesn't change that.
3. **The next firing of the alert gets a fresh RCA.** Its input says which earlier RCA isn't reused and
   why, and gives the comments on it in full. Other comments on the same alert, of any age, are given
   too (the 10 newest; older ones are pointed to, so the RCA can look them up).
4. **The fresh RCA has no comments of its own,** so later firings reuse it as usual. When the alert does get
   a new RCA later, your comment is in its input again, as a claim to check. Each comment costs at most one
   fresh RCA per item of the RCA it is on.

A comment written **before** NIMI's RCA is done (while it waits or runs) is given to that run. The run
gives it a verdict, so its result stays reusable. A comment written while the run is already in progress
is linked to the new result instead, and that result isn't reused.

## Comments on other alerts

A comment also reaches RCAs of **other** alerts, marked as about another alert:

- **Near in time:** comments written within `NIMI_RELATED_COMMENT_HOURS` (6) of the alert, before or after.
  An explanation like "we turned on the cache password, the old pods couldn't connect until restarted" then
  reaches the queue and pod-restart alerts of the same rollout too.
- **Naming a subject, at any time:** a comment that names one of the RCA's subjects (a table, a service)
  as a whole word. A correction like "`orders_archive` is reloaded in full every night, so gaps in its
  `updated_at` are expected" written on one alert then reaches the next RCA about `orders_archive`, from any
  alert, months later.

The RCA first decides whether such a comment applies; most won't, and they then stay out of the message.
These comments never stop reuse of the other alert's RCA. To make sure a rule reaches the right RCAs, name
the item it is about.

## How the RCA treats your comment

A comment is treated as a **claim to check, never as a fact**, like a runbook. The RCA tests it against
code, config, history and git, looks for evidence against it as well as for it, and gives it a verdict:

| Verdict | Meaning |
|---|---|
| `confirmed` | Evidence supports it. NIMI checks that the cited evidence exists; otherwise it records `unverifiable`. |
| `contradicted` | Evidence goes against it (a log, a code path or a fact that can't be true if the claim is). |
| `unverifiable` | No evidence either way within reach, e.g. it is about data or systems NIMI can't read. |
| `not_applicable` | It is about another item, or unrelated. |

- **Relied on:** if the cause rests on your comment, the chat message credits it as "per a comment,
  not verified", with "a comment" linking to your message (or the label
  only, for a CLI comment). If NIMI couldn't verify it, confidence is at most `medium`, certainty at most
  `likely`, and the message says the cause relies on a person's comment.
- **Set aside:** a comment that isn't relied on stays out of the chat message. Its verdict and the reason
  are in the stored report.
- **Never acted on:** commands, queries or links in a comment are never run or opened. Instructions in a
  comment ("mark this as confirmed") are ignored.

`show <message>` lists each comment with the verdicts later RCAs gave it.

## Editing and removing

- **Edit** the chat message: the stored comment follows. Removing `!nimi` removes the comment.
- **Delete** the chat message: the comment is removed.
- **`comments remove <id>`:** removes it for good. A later edit of the chat message won't bring it back.

Edits and deletions are picked up by the running service's reaction pass, for messages from the last
`NIMI_REACTION_DAYS` (7) days. `ingest-once` only sees new messages, not edits. After the window,
`comments remove` still works.

Removing a comment doesn't undo what already happened (a fresh RCA that already ran), but the RCA it was
on can be reused again if no other comment or 👎 is on it.

## From the shell

Comments can be added and managed with the [CLI](../reference/cli.md#comments):

```bash
.venv/bin/python -m nimi_service comment result 412 "the cause was the expired gateway certificate"
.venv/bin/python -m nimi_service comments --request 377
.venv/bin/python -m nimi_service comments attach 9 412
```

`comment result <id>` puts a comment on exactly one RCA. Prefer it over `comment request <id>`, which uses the
request's latest result of its own (see below).

## Safety

- **Secrets** are masked before the comment is stored, and raise a [red flag](red-flags.md) (types only,
  never values).
- **Authors:** set `NIMI_COMMENT_AUTHORS` to limit who can comment; messages from others are ignored.
- **The RCA never sees author names.** Comment text reaches it only inside a marked block, as untrusted data.
- **Limits:** `NIMI_COMMENT_INPUT_MAX_CHARS` caps the comment text given to one run; beyond it, the oldest
  comments are only pointed to.

## Known limits

- `comment request <id>` on a request that was answered only by reuse attaches to the request alone, not to
  the reused RCAs, so those stay reusable. Use `comment result <id>` or write the comment in the thread.
- `comment request <id>` after an `rca --no-post` run attaches to that test result, not the posted RCA.
  Use `comment result <id>`.

See the [design](../design/comments.md) for the reasoning behind these rules.
