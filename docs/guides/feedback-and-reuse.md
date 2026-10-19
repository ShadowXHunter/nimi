# Feedback and reuse

## Rating replies

React to an RCA reply in the chat thread:

| Reaction | Verdict |
|---|---|
| 👍 | correct |
| 🤔 | partially right |
| 👎 | wrong |

Replies posted in the last `NIMI_REACTION_DAYS` (7) days are re-checked every 10 minutes, wherever they
were posted (posts in a [webhook space](where-replies-go.md) count too, if the token's account can read it). Reactions are
stored in `reactions` and turned into `rca_feedback` rows; removing a reaction marks its feedback removed.
Skin-tone variants count the same.

Feedback is used in three places:

- **Reuse:** an RCA rated 👎 is never reused.
- **The skill's history:** previous RCAs come with their feedback counts, and the skill is told to treat
  ones rated wrong with distrust.
- **Quality tracking:** the `rca_feedback_summary` view.

## When an earlier RCA is reused

Alerts often repeat while an incident is ongoing. Instead of a new RCA for each repeat, triage can reuse
an earlier one **per subject** (a table, service, deployment or job the message is about):

- only RCAs for messages with the **same signature**,
- from the last `NIMI_REUSE_DAYS` (7) days,
- never one rated 👎,
- never one from an `rca --no-post` run (nobody saw it, and the reply couldn't link to it),
- and only if it is the **same ongoing incident**: if history shows the subject recovered in between (an
  OK message, or a firing message that no longer listed it), it is a new incident and gets a fresh RCA.

A reused reply says which earlier RCA covers the alert, links to it and repeats its TL;DR. When only some subjects are
covered, the fresh RCA analyzes just the new ones and the reply lists both ("partial"). When in doubt,
triage runs a fresh RCA: a wrong reuse is worse than an extra RCA.
