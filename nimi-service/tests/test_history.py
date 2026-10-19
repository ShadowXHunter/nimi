"""History access for the skill (nimi-sql): guard rules, output formatting, per-run credentials. No database."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nimi_service import skill_sql
from nimi_service.config import Settings
from nimi_service.rca import NIMI_SQL, build_input, history_access, parse_output

SKILL = Path(__file__).resolve().parents[2] / "nimi-skill/.claude"
GUARD = SKILL / "hooks/nimi_guard.py"


def guard(command, home):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(SKILL.parent), HOME=str(home))   # denial log goes to home
    data = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    out = subprocess.run([sys.executable, str(GUARD)], input=data, capture_output=True, text=True, env=env)
    return json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"]


@pytest.mark.parametrize("command, decision", [
    ("{sql} \"SELECT title FROM skill_messages WHERE created_at BETWEEN '2026-01-15+00' AND 'infinity' LIMIT 5\"",
     "allow"),
    ("{sql} 'select 1'", "allow"),
    ("nimi-sql \"select 1\"", "deny"),                     # must be the checkout's bin/nimi-sql
    ("/tmp/nimi-sql \"select 1\"", "deny"),
    ("{sql} \"select 1 where 2 > 1\"", "deny"),            # metachar inside the SQL
    ("{sql} \"select 1\"; rm -rf x", "deny"),
    ("{sql} \"select 1\" \"select 2\"", "deny"),            # one quoted argument only
    ("{sql} select 1", "deny"),                            # unquoted (globbing)
    ("{sql} \"select $(id)\"", "deny"),
])
def test_guard_nimi_sql(tmp_path, command, decision):
    assert guard(command.replace("{sql}", str(NIMI_SQL)), tmp_path) == decision


def test_run_rejects_multiple_statements_before_connecting():
    with pytest.raises(ValueError):
        skill_sql.run("select 1; drop table x", Settings())
    with pytest.raises(ValueError):
        skill_sql.run("  ", Settings())


def test_format_rows_table_and_records():
    table = skill_sql.format_rows(["id", "title"], [(1, "short")], more=False)
    assert table.splitlines()[0].split() == ["id", "title"] and table.endswith("(1 row)")
    records = skill_sql.format_rows(["id", "text"], [(1, "line one\nline two")], more=True)
    assert "-[ row 1 ]-" in records and "more rows exist" in records
    masked = skill_sql.format_rows(["t"], [("token ghp_" + "a" * 36,)], more=False)
    assert "ghp_" not in masked and "redacted" in masked


def test_history_access_private_temp_credentials():
    with history_access(Settings(db_skill_password="pw")) as env:
        pw_file = env["NIMI_DB_SKILL_PASSWORD_FILE"]
        assert oct(os.stat(pw_file).st_mode & 0o777) == "0o600" and Path(pw_file).read_text() == "pw"
        assert env["NIMI_ENV"] == os.devnull and "NIMI_DB_SKILL_PASSWORD" not in env
    assert not os.path.exists(pw_file)
    with history_access(Settings(skill_history=False)) as env:
        assert env == {}


def test_sensitive_data_from_history():
    fields = parse_output("report\nRCA_FIELDS\norigin: internal\nconfidence: low\nself_healing: no\n"
                          "repo: none\ncommit: none\nsensitive_data: history:password\n")
    assert fields["sensitive_data"] == [{"where": "history", "type": "password"}]


def test_build_input_names_the_history_command():
    text = build_input({"input_text": "alert"}, ["*"], [], history=True)
    assert text.splitlines()[0] == "RCA context (from NIMI):"
    assert f'- History SQL: {NIMI_SQL} "<one SQL statement>"' in text and text.endswith("---\nalert")
    assert build_input({"input_text": "alert"}, ["*"], [], history=False) == "alert"


def test_nimi_sql_wrapper_is_committed_and_executable():
    assert NIMI_SQL.exists() and os.access(NIMI_SQL, os.X_OK)
