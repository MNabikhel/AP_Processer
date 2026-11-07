"""What AP Coder's SQL uses that is SQLite's own, counted where it is (D-190): the inventory behind docs/POSTGRES.md,
which a move to PostgreSQL works through.

    python scripts/sql_inventory.py              each construct: how many times, the files with the most
    python scripts/sql_inventory.py --markdown   the same as docs/POSTGRES.md's table
    python scripts/sql_inventory.py --json       every construct's count in every file

The SQL is the text of the string literals of ``ap_coder/`` (an f-string's own text, its ``{...}`` left out; not the
docstrings) that read as SQL (an upper-case SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, PRAGMA, BEGIN, WHERE
or VALUES in them); a construct of the driver (``lastrowid``, ``executescript``...) is counted in the code itself.
A count is of uses as written: a helper used by fifty queries counts once, where it is written (``_in_list``'s
``json_each``: once, in ``core/store/dialect.py``). Only the standard library; nothing connects to anything.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ap_coder"
DIALECT = "ap_coder/core/store/dialect.py"
SQL = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|PRAGMA|BEGIN|WHERE|VALUES|FROM|JOIN|LIKE|GLOB|COALESCE|CAST|"
    r"CASE|ORDER BY|GROUP BY|IS (?:NOT )?NULL|NOT NULL|EXISTS|DISTINCT|LIMIT|AND|OR)\b"
)


@dataclass(frozen=True)
class Construct:
    name: str
    pattern: str  # a regular expression in the SQL; with ``code``: "call:NAME", "attr:NAME" or "module:NAME"
    postgres: str  # what PostgreSQL has for it
    seam: str = ""  # the name in core/store/dialect.py that covers it, when there is one
    code: bool = False  # a construct of the driver, found in the code (a call, an attribute) rather than the SQL
    flags: int = 0
    ratchet: bool = False  # new code writes it through the seam: its uses elsewhere may only fall (tests/meta)


NOCASE = re.IGNORECASE
CONSTRUCTS = [
    Construct("`?` placeholders", r"\?", "`%s` (psycopg); one translation where the connection is wrapped",
              "`PARAM`"),
    Construct("`?1` numbered placeholders (among those above)", r"\?\d", "`%(name)s`, or the value bound again"),
    Construct("`INSERT OR IGNORE`", r"\bINSERT OR IGNORE\b", "`INSERT ... ON CONFLICT DO NOTHING` (unique keys "
              "only: a NULL in a NOT NULL column fails there)", "`insert_or_ignore`", ratchet=True),
    Construct("`INSERT OR REPLACE`", r"\bINSERT OR REPLACE\b", "an upsert of every column (the row is kept, not "
              "deleted and inserted)", "`insert_or_replace`", ratchet=True),
    Construct("`UPDATE OR IGNORE`", r"\bUPDATE OR IGNORE\b", "none: `WHERE NOT EXISTS` the row it would collide "
              "with, or a savepoint", "`update_or_ignore`", ratchet=True),
    Construct("`ON CONFLICT ... DO` (an upsert)", r"\bON CONFLICT\b", "the same (portable)", "`upsert`"),
    Construct("`json_each` (lists bound as one value)", r"\bjson_each\(", "`jsonb_array_elements_text(%s::jsonb)` "
              "or `= ANY(%s)`", "`in_list` (`_base._in_list`)", flags=NOCASE, ratchet=True),
    Construct("`json_extract`", r"\bjson_extract\(", "`(x)::jsonb #>> '{key}'`", "`json_get`", flags=NOCASE,
              ratchet=True),
    Construct("`json_valid`", r"\bjson_valid\(", "`pg_input_is_valid(x, 'jsonb')` (16 and newer)", "`json_valid`",
              flags=NOCASE, ratchet=True),
    Construct("other JSON (`json_set`, `json_object`, `json_group_array`, `json_array`)",
              r"\bjson_(?:set|object|group_array|group_object|array|insert|replace|remove|type)\(",
              "`jsonb_set`, `jsonb_build_object`, `jsonb_agg`, `jsonb_build_array`", flags=NOCASE),
    Construct("`julianday('now')` (the database's clock)", r"\bjulianday\('now'\)",
              "`EXTRACT(EPOCH FROM statement_timestamp())`", "`NOW_DAYS`, `NOW_SECONDS`", flags=NOCASE, ratchet=True),
    Construct("other date functions (`julianday`, `date`, `datetime`, `unixepoch`, `strftime`)",
              r"\b(?:julianday\((?!'now'\))|date\(|datetime\(|unixepoch\(|strftime\()",
              "`::date`, `::timestamp`, `EXTRACT`, `to_char`", flags=NOCASE),
    Construct("`group_concat`", r"\bgroup_concat\(", "`string_agg(x::text, sep)`", "`group_concat`", flags=NOCASE,
              ratchet=True),
    Construct("`LIKE` (A to Z without case in SQLite)", r"\bLIKE\b", "`ILIKE` (PostgreSQL's LIKE minds the case)",
              "`like`", ratchet=True),
    Construct("`GLOB`", r"\bGLOB\b", "`LIKE` or `~` (a regular expression)"),
    Construct("`COLLATE NOCASE`", r"\bCOLLATE NOCASE\b", "`lower(x)` or a case-insensitive collation", "`nocase`",
              flags=NOCASE, ratchet=True),
    Construct("`rowid`", r"\browid\b", "none that lasts: a key column of its own (`GENERATED ... AS IDENTITY`)",
              "`ROWID`", flags=NOCASE, ratchet=True),
    Construct("`AUTOINCREMENT`", r"\bAUTOINCREMENT\b", "`GENERATED ALWAYS AS IDENTITY`"),
    Construct("`BEGIN IMMEDIATE` (the write lock)", r"\bBEGIN IMMEDIATE\b", "the transaction psycopg opens, and "
              "`pg_advisory_xact_lock` or `SELECT ... FOR UPDATE`", "`begin_write`", ratchet=True),
    Construct("`PRAGMA table_info`", r"\bPRAGMA table_info\b", "`information_schema.columns`",
              "`columns`, `add_column`", flags=NOCASE, ratchet=True),
    Construct("other `PRAGMA`s", r"\bPRAGMA (?!table_info\b)\w+", "`SET` (`lock_timeout`...), or none needed",
              "`settle`, `busy_timeout`, `set_busy_timeout`, `data_version`", flags=NOCASE, ratchet=True),
    Construct("`sqlite_master`", r"\bsqlite_(?:master|schema)\b", "`information_schema.tables`, `pg_indexes`",
              "`table_names`", flags=NOCASE, ratchet=True),
    Construct("triggers", r"\bCREATE TRIGGER\b", "the same in PL/pgSQL (`CREATE FUNCTION ... RETURNS trigger`)",
              flags=NOCASE),
    Construct("full-text index (FTS5)", r"(?i:\bCREATE VIRTUAL TABLE\b|\bfts5\b)|\bMATCH\b", "`pg_trgm` or "
              "`tsvector`"),
    Construct("`ap_fold` (the store's own SQL function)", r"\bap_fold\(", "`lower(unaccent(x))` (the `unaccent` "
              "extension)"),
    Construct("SQLite's other functions (`printf`, `typeof`, `hex`, `char`, `instr`, `ifnull`, `iif`, `total`)",
              r"\b(?:printf|typeof|hex|char|instr|ifnull|iif|total)\(", "`format`, `pg_typeof`, `encode(x, 'hex')`, "
              "`chr`, `strpos`, `COALESCE`, `CASE`, `COALESCE(SUM(x), 0)`", flags=NOCASE),
    Construct("`executescript`", "call:executescript", "`execute` of each statement", code=True),
    Construct("`lastrowid`", "attr:lastrowid", "`INSERT ... RETURNING id`", code=True),
    Construct("`total_changes`", "attr:total_changes", "none: a counter of the session's own writes", code=True),
    Construct("`in_transaction`", "attr:in_transaction", "`conn.info.transaction_status`", "`begin_write`",
              code=True),
    Construct("`create_function` (a Python SQL function)", "call:create_function", "an SQL function in the "
              "database", code=True),
    Construct("`backup` (the database copied whole)", "call:backup", "`pg_dump` / `pg_basebackup`", code=True),
    Construct("`sqlite3` in the code (its `connect`, `Row`, errors; type hints left out)",
              "module:sqlite3", "psycopg's `connect`, `rows.dict_row`, `errors.*`",
              code=True),
]  # fmt: skip
RATCHETED = [c for c in CONSTRUCTS if c.ratchet]


_DEFINED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
# A file that may hold any of it: SQL's words that start a statement or a clause, or the driver's names.
_ANY = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|PRAGMA|BEGIN|WHERE|VALUES|FROM|JOIN|LIKE|GLOB|"
    r"ORDER BY|sqlite3|executescript|lastrowid|total_changes|in_transaction|create_function|backup)\b"
)


def scan(source: str) -> tuple[list[str], dict[str, int]]:
    """(the SQL among ``source``'s string literals: each string, an f-string's literal parts joined and an implicit
    concatenation as one, that reads as SQL, docstrings left out; the driver's constructs in its code: {"call:NAME":
    calls of a method NAME, "attr:NAME": reads of an attribute NAME, "module:sqlite3": names taken from the sqlite3
    module, its types in hints left out}), in one walk of its syntax tree."""
    skip: set[int] = set()
    texts, uses = [], {}
    for node in ast.walk(ast.parse(source)):
        keys = []
        if isinstance(node, _DEFINED) and node.body:  # (a parent is walked before its children)
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
        elif isinstance(node, ast.JoinedStr):
            parts = [v for v in node.values if isinstance(v, ast.Constant)]
            skip |= {id(v) for v in parts}
            texts.append(" ".join(str(v.value) for v in parts))
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, str) and id(node) not in skip:
                texts.append(node.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            keys.append(f"call:{node.func.attr}")
        elif isinstance(node, ast.Attribute):
            keys.append(f"attr:{node.attr}")
            if isinstance(node.value, ast.Name) and node.attr not in ("Connection", "Cursor"):
                keys.append(f"module:{node.value.id}")
        for key in keys:
            uses[key] = uses.get(key, 0) + 1
    return [t for t in texts if SQL.search(t)], uses


def count(root: Path = APP) -> dict[str, dict[str, int]]:
    """{construct's name: {file (from the repository's top): uses}}, each file of ``root``."""
    found: dict[str, dict[str, int]] = {c.name: {} for c in CONSTRUCTS}
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        if not _ANY.search(source):  # (most files: no SQL, no driver, nothing to parse)
            continue
        texts, code = scan(source)
        name, sql = path.relative_to(ROOT).as_posix(), "\n".join(texts)
        for c in CONSTRUCTS:
            n = code.get(c.pattern, 0) if c.code else len(re.findall(c.pattern, sql, c.flags))
            if n:
                found[c.name][name] = n
    return found


def outside_dialect(found: dict[str, dict[str, int]]) -> dict[str, int]:
    """Each construct's uses outside ``core/store/dialect.py``."""
    return {name: sum(n for file, n in files.items() if file != DIALECT) for name, files in found.items()}


def _where(files: dict[str, int], most: int = 4) -> str:
    top = sorted(files.items(), key=lambda kv: (-kv[1], kv[0]))
    said = ", ".join(f"{f.removeprefix('ap_coder/')} {n}" for f, n in top[:most])
    return said + (f", {len(top) - most} more files" if len(top) > most else "")


def markdown(found: dict[str, dict[str, int]]) -> str:
    rows = ["| Construct | Uses (outside `core/store`) | Where (the most) | PostgreSQL | The seam (`dialect`) |",
            "|---|---|---|---|---|"]  # fmt: skip
    for c in CONSTRUCTS:
        files = found[c.name]
        outside = sum(n for f, n in files.items() if not f.startswith("ap_coder/core/store/"))
        uses = f"{sum(files.values())} ({outside})"
        rows.append(f"| {c.name} | {uses} | {_where(files) or '-'} | {c.postgres} | {c.seam or '-'} |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--markdown", action="store_true", help="the table of docs/POSTGRES.md")
    parser.add_argument("--json", action="store_true", help="every count in every file")
    args = parser.parse_args(argv)
    found = count()
    if args.json:
        print(json.dumps(found, indent=1, sort_keys=True))
    elif args.markdown:
        print(markdown(found))
    else:
        for c in CONSTRUCTS:
            print(f"{sum(found[c.name].values()):6d}  {c.name}: {_where(found[c.name]) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
