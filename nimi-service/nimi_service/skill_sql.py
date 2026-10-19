"""`nimi-sql '<SQL>'`: the /rca skill's read-only SQL access to NIMI's history.

RCA runs reach it through the committed wrapper bin/nimi-sql, which the guard hook allows as a Bash
command (rca.build_input tells the skill its path). It connects as the skill reader role
(NIMI_DB_SKILL_USER), which can only SELECT the skill_messages and skill_rcas views; the database
enforces that, not this script. This
script adds: one statement per call, a read-only transaction, a 5s statement timeout, row/size caps, and
masking of secrets the scanner recognises. Unlike psql it has no meta-commands, so it can't run shell
commands or write files.
"""
import sys

import psycopg
from psycopg.conninfo import make_conninfo

from .config import load_settings
from .scanner import mask

MAX_ROWS = 100
MAX_CELL = 4000
MAX_OUTPUT = 40000
MAX_SQL = 4000


def connect(settings):
    conninfo = make_conninfo(host=settings.db_host or None, port=settings.db_port or None,
                             dbname=settings.db_name, user=settings.db_skill_user,
                             password=settings.resolved_db_skill_password() or None,
                             sslmode=settings.db_sslmode or None, application_name="nimi-sql",
                             options="-c default_transaction_read_only=on -c statement_timeout=5000")
    conn = psycopg.connect(conninfo, connect_timeout=10)
    conn.read_only = True
    return conn


def _cell(value) -> str:
    if value is None:
        return "NULL"
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    text, _ = mask(text)
    return text if len(text) <= MAX_CELL else text[:MAX_CELL] + f"… [{len(text) - MAX_CELL} more chars]"


def format_rows(columns: list, rows: list, more: bool) -> str:
    """Aligned table for short values; one record per block when any value is long or multi-line."""
    cells = [[_cell(v) for v in row] for row in rows]
    if any(len(c) > 60 or "\n" in c for row in cells for c in row):
        width = max(len(c) for c in columns)
        blocks = []
        for i, row in enumerate(cells, 1):
            lines = [f"-[ row {i} ]-"]
            for name, value in zip(columns, row):
                pad = "\n" + " " * (width + 3)
                lines.append(f"{name.ljust(width)} : {value.replace(chr(10), pad)}")
            blocks.append("\n".join(lines))
        out = "\n".join(blocks)
    else:
        widths = [max([len(c)] + [len(row[i]) for row in cells]) for i, c in enumerate(columns)]
        lines = ["  ".join(c.ljust(w) for c, w in zip(columns, widths)),
                 "  ".join("-" * w for w in widths)]
        lines += ["  ".join(v.ljust(w) for v, w in zip(row, widths)) for row in cells]
        out = "\n".join(lines)
    footer = f"({len(rows)} row{'s' if len(rows) != 1 else ''}"
    footer += f"; more rows exist — add filters or LIMIT, max {MAX_ROWS} shown)" if more else ")"
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + "\n… (output truncated; select fewer columns or rows)"
    return out + "\n" + footer


class Unavailable(Exception):
    """Can't connect (database down, role missing, wrong password)."""


def run(sql: str, settings=None) -> str:
    sql = sql.strip()
    if not sql:
        raise ValueError("empty query")
    if len(sql) > MAX_SQL:
        raise ValueError(f"query longer than {MAX_SQL} characters")
    if ";" in sql:
        raise ValueError("one statement per call, without ';'")
    try:
        conn = connect(settings or load_settings())
    except psycopg.OperationalError:
        raise Unavailable() from None
    with conn, conn.cursor() as cur:
        cur.execute(sql)
        if cur.description is None:
            return "(no result rows)"
        rows = cur.fetchmany(MAX_ROWS + 1)
        columns = [d.name for d in cur.description]
        return format_rows(columns, rows[:MAX_ROWS], len(rows) > MAX_ROWS)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: nimi-sql '<one SQL statement>'", file=sys.stderr)
        return 2
    try:
        print(run(argv[0]))
        return 0
    except ValueError as e:
        print(f"nimi-sql: {e}", file=sys.stderr)
    except psycopg.errors.QueryCanceled:
        print("nimi-sql: query took longer than 5s — narrow the time range or add filters", file=sys.stderr)
    except Unavailable:
        print("nimi-sql: history database unavailable", file=sys.stderr)
    except psycopg.Error as e:
        print(f"nimi-sql: {type(e).__name__}: {(e.diag.message_primary or str(e)).strip()[:500]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
