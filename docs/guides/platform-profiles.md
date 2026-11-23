# Platform profiles

A **platform profile** (`nimi-service/parsers/local/<name>.yaml`, git-ignored) teaches NIMI about one platform's messages
without code changes:

- **log layouts**: how the platform's log lines look, so logs get a proper service, logger, title and
  signature;
- **subject masks**: platform-specific item names to leave out of signatures;
- **triage notes**: facts that help triage, shown only for messages they apply to;
- **message notes**: facts about how a sender's messages are laid out, given to the RCA and triage.

All profiles apply to all messages (see [Profiles are global](#profiles-are-global-not-per-project)).

Profiles are data (patterns). Yours live in `parsers/local/`, which is git-ignored because it describes your
platforms; `parsers/examples/example.yaml` is a documented sample that is **never loaded** (copy it to
`local/` to start). The service is the only thing that reads profiles; the RCA skill always works from the
full message text. With no profiles at all, the generic parse still works.

## Why signatures matter

The parser gives each message a **signature**, its "kind of message". Triage only sees history and earlier
RCAs with the same signature, and `exclude --like` matches by signature. If a format isn't recognised, the
generic fallback still groups messages (masking numbers, IDs, timestamps, pod suffixes, Java object hashes
such as `Order@1b6d3586`, hosts and common item names), so nothing breaks: the cost is extra RCAs, never wrong ones. A layout makes the grouping
precise and the titles readable.

## How messages are matched

Every message is tried against the layouts of **all** profiles, regardless of channel, because one channel
can carry messages from several platforms and formats. Layouts are tried in `priority` order (lower first;
default 100, then file and list order); the **first layout whose header matches a line** wins. Messages no
layout matches get the generic parse.

When two platforms' lines look alike:

- same fields in the same places: it doesn't matter which layout matches; the service still comes from the
  pod/service name in the line;
- similar shape, different meaning: narrow a layout with `when:` (sender display names, or text it must
  contain) and/or give it a lower `priority`.

## Writing a profile

```yaml
name: myplatform                           # short lowercase id
description: Example services logging with a common Java/logback layout

log_layouts:
  - name: level-first
    # [ERROR] [2026-01-15 10:04:31+0000] [shop-api-deployment-7f8e9d0c1b-abcde] [checkout-3] com.example.shop.api.CheckoutController Payment failed …
    header: '^\[(?P<level>ERROR|WARN|FATAL)\]\s*\[(?P<ts>[^\]]+)\]\s*\[(?P<pod>[^\]]+)\](?:\s*\[[^\]]*\])*\s+(?P<logger>[\w$]+(?:\.[\w$]+)+)\s+(?P<message>.*)$'
    message: same_line                     # the error text is on the header line (next_lines: on the lines after)
    # when: {sender: ["Shop API Errors"], contains: ["com.example.shop"]}
    # priority: 50

subject_masks:                             # replaced by <name> in signatures
  - '(?<=for cart )[\w-]+'                 # "Payment failed for cart c-17 …"

triage_notes:
  - applies_to: {text_contains: ["Stock Sync Reminder"]}
    note: >-
      "Stock Sync Reminder" asks the warehouse team to confirm a manual stock sync; it reports no system
      failure.

message_notes:
  - applies_to: {sender: ["Ops Notifier"]}
    note: >-
      Alerts from this notifier end with one block per matched series: the series in bold (a disk, queue
      or host), then its current value on the next line.
```

| Key | Meaning |
|---|---|
| `header` | Regular expression (case-insensitive) matched against each line of the message. Named groups: **`pod`** or **`service`** (required; a pod's generated suffix is stripped to get the service), `logger`, `level`, `message`. |
| `message` | `next_lines` (default): the error is the first exception line after the header. `same_line`: the `message` group. |
| `when` | Optional. `sender`: display names; `contains`: texts the message must contain (any). |
| `priority` | Optional integer, lower first (default 100). |
| `subject_masks` | Regular expressions for item names (databases, tenants, …) to mask in signatures. |
| `triage_notes` | `applies_to.text_contains` (required; any of, case-insensitive) and `note`. |
| `message_notes` | `applies_to` with `sender` (display names) and/or `text_contains` (any of); every key given must match. And `note`. |

The signature of a log is `log|<service>|<logger>|<error text with items masked>`.

**Triage notes** are shown to the triage model as trusted guidance from NIMI's maintainers. Write only
**confirmed facts** that help decide reuse vs new vs not applicable, e.g. "this alert lists only newly
affected tables" or "this alert is a reminder, not a failure". Root causes and runbook knowledge belong
in the project's repo docs, where the RCA skill finds them. The code limits on reuse (only offered
candidates, never one rated 👎, the reuse window) always apply.

**Message notes** are facts about how a sender's messages look or what they mean, given to the RCA (in its
input context) and to triage, only for messages they apply to. NIMI renders messages faithfully and never
guesses a sender's conventions: bold stays `**bold**`, and a card's label/value widget becomes `label: value`.
What a sender means by its layout is yours to state here, per sender: two notifiers can send the same kind
of alert in different layouts. Keep each note short and factual: each kind of note (triage notes, message notes) gets
at most 2,000 characters per message, and a note that doesn't fit in what's left is skipped whole. Notes don't change
parsing, so `reparse` is optional after a note-only change: until you run it, stored parses only show the
old parser version.

### Profiles are global, not per project

The service loads every file in `parsers/local/` and merges them: a profile isn't tied to a project, a
channel or a space, and its file name and `name:` are only labels. Every layout, mask and note applies to
every message, as far as its own scope (`when:`, `applies_to`) allows. So a note scoped to
`sender: ["Ops Notifier"]` reaches that notifier's alerts in every project, whatever file it is in.

Put what several projects share in a file of its own, e.g. one note per notifier your teams use. A profile
needs only `name`; the other keys are optional:

```yaml
# parsers/local/notifiers.yaml
name: notifiers
description: How the alert notifiers we use lay out their messages
message_notes:
  - applies_to: {sender: ["Ops Notifier"], text_contains: ["Grafana"]}
    note: >-
      Grafana alerts from this notifier end with one block per matched series: the series in bold, then
      its current value on the next line.
  - applies_to: {sender: ["Team Alerts Bot"]}
    note: >-
      Each alert lists the affected queues as "queue: depth" lines; depth is the number of waiting jobs.
```

If two notifiers post under the same display name with different layouts, tell them apart with
`text_contains`. A note that is true for only one project's alerts needs a text scope too (e.g.
`text_contains: ["[shop-prod]"]`): the file it is in doesn't limit it.

## Testing and applying a change

```bash
.venv/bin/python -m nimi_service parse-test --text "<a log line or alert>"     # one message, plus matching notes
.venv/bin/python -m nimi_service parse-test --channel app-logs --kind other      # stored messages still unmatched
.venv/bin/python -m nimi_service parse-test --sender "Shop API Errors" --limit 5
.venv/bin/python -m nimi_service reparse                                        # apply to all stored messages
```

`parse-test` writes nothing. For stored messages it shows which layout matched, the title and signature
(with the current one when it would change), and a summary: layouts used, distinct signatures, and how
many messages `reparse` would change.

A profile is validated when the service starts; an invalid one (unknown key, bad regex, missing group)
stops it with the file and location. The parser version includes a hash of all profiles, so stored parses
show which profile version produced them; run `reparse` after every layout or mask change (after a
note-only change it only updates that version).
