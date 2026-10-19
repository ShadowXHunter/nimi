# Platform profiles

Profiles teach NIMI your platforms' log formats, item names to mask in signatures, and triage notes.
They are loaded from **`local/*.yaml`** in this folder. `local/` is git-ignored: it describes *your*
platforms.

`examples/` holds documented samples. They are **never loaded**, so their patterns can't match your
messages by accident. To start from one:

```bash
mkdir -p local && cp examples/example.yaml local/myplatform.yaml   # then edit: name, layouts, masks, notes
```

Check a profile against stored messages, then apply it:

```bash
.venv/bin/python -m nimi_service parse-test --text "<a log line>"
.venv/bin/python -m nimi_service parse-test --channel <label> --kind other
.venv/bin/python -m nimi_service reparse
```

No profiles at all is fine: unknown formats get the generic parse (only less precise grouping).
See the documentation: *Guides → Platform profiles*.
