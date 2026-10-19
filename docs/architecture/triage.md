# Triage

Triage decides, for each new request, whether a fresh RCA is needed. It is a **hybrid**: a cheap model
call makes the judgement, and code sets the limits and checks the answer.

## Inputs (assembled by the service)

- the new message (up to 6000 characters) and its time;
- **history**: the 10 most recent earlier messages with the same signature within the reuse window, with
  their parsed status;
- **platform notes**: confirmed facts from the [platform profiles](../guides/platform-profiles.md) whose
  `applies_to` matches the message (trusted guidance, unlike the message data);
- **candidates**: earlier RCAs for the same signature from the last `NIMI_REUSE_DAYS` (7) days, never ones
  rated 👎 and never the request's own earlier results (so a re-run doesn't just return its old answer),
  each with its input, analyzed subjects, a report excerpt and feedback.

The new message comes **last** in the prompt, labelled as the message to decide on: alert history is long
and repetitive, and a small model reading it last tends to mistake earlier firings for the current one.

## What the model decides

Per **subject** (a table, dataset, service, deployment — not a pod — job, or `*`):

| Decision | Meaning |
|---|---|
| `reuse` | every subject is covered by a candidate RCA of the same ongoing incident |
| `new` | a fresh RCA (lists the subjects to analyze) |
| `partial` | some subjects reused, some new |
| `not_applicable` | no RCA: OK/resolved, informational, or a test message |

Rules it follows include: a subject that recovered in between (an OK message, or a firing message that no
longer listed it) is a new incident; an error log that hadn't appeared for about a day is a new incident;
sender and webhook names are never evidence of a test; when unsure, run a fresh RCA. It also reports the
**types** of any sensitive data in the message (never values).

## What code enforces

- Only candidates the service offered can be reused; IDs are checked.
- Subjects must appear in the new message: any the model took from history are dropped (if none remain,
  the RCA covers the whole message).
- Invalid or unparseable output falls back to a fresh RCA (and raises a red flag).
- A Claude unavailability error pauses the service instead of falling back.

## Isolation

The triage call has no tools at all: `--tools ""`, no MCP servers, no slash commands, no settings layers,
no session persistence, run from an empty working directory (`~/.cache/nimi/triage`) with a clean
environment. It can't read files, run commands or browse; it only sees the prompt. Because secrets could
be in the message, the code scanner runs first and **holds** requests with critical secrets before any
model sees them.

## Cost

Triage uses `NIMI_TRIAGE_MODEL` (default `haiku`): about $0.001 per message. It is not counted against
the daily RCA cap.
