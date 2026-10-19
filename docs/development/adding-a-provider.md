# Adding a provider

To support another chat platform (Slack, Teams, …):

1. **Create `nimi_service/providers/<name>/`** with:
    - a client with the platform's auth, rate limits and retry policy (see `gchat/client.py`: per-channel
      and per-project limiters, backoff on 429/5xx, posts not retried after a possible send);
    - rendering of payloads into readable `rendered_text` (cards, attachments, mentions);
    - a `Provider` subclass implementing every method in `providers/base.py`.
2. **Normalize carefully:**
    - `external_id` must be unique per provider and stable;
    - set `thread_external_id`, and `is_thread_reply` for replies;
    - map sender types to `bot` / `human` (this decides what is ever analyzed);
    - normalize emoji so 👍 / 🤔 / 👎 match `reactions.VERDICTS` (strip skin tones).
3. **Register it** in `providers/__init__.py`.
4. **Add its credentials** as settings in `config.py` (plus `env.example` and the settings reference).
5. **Test** normalization and rendering with real payloads (see the Google Chat tests in
   `tests/test_parse.py`), then on a scratch database:
   ```bash
   .venv/bin/python -m nimi_service add-channel <name> <channel id> <label> --rca
   .venv/bin/python -m nimi_service ingest-once
   ```

Nothing else changes: the database, triage, the skill and the workflow are provider-neutral.
