"""Read the guard hook's denial log (written by nimi-skill/.claude/hooks/nimi_guard.py)."""
import json
import logging

log = logging.getLogger(__name__)


def denials_for_session(path, session_id) -> list:
    """Denial entries recorded for one RCA run (matched by Claude Code session id)."""
    if not session_id:
        return []
    out = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("session_id") == session_id:
                    out.append(entry)
    except FileNotFoundError:
        return []
    except OSError:
        log.exception("could not read guard denial log %s", path)
    return out
