"""CSV reading that copes with files saved by Excel or an ERP export."""

from __future__ import annotations

import csv
import datetime as dt
import io
from pathlib import Path

_DELIMITERS = ",;\t|"


_SNIFF_LINES = 20


def sniff_delimiter(text: str) -> str:
    """Comma, or semicolon (French-Canadian Excel), tab or pipe: the one that splits the header and the first rows
    into the same number of cells (more than one), read with Excel's quoting. A comma inside a header cell
    ("Catégorie (dépenses, revenus)" in a semicolon file) splits the header line but not the rows under it, so
    one line alone can't tell; the first delimiter listed wins a tie, a comma when nothing fits."""
    sample = text[:65536]
    best, best_score = ",", (0, 0)
    for delimiter in _DELIMITERS:
        try:
            rows = [r for r in csv.reader(io.StringIO(sample), delimiter=delimiter) if any(c.strip() for c in r)]
        except csv.Error:
            continue
        if len(sample) < len(text):
            rows = rows[:-1]  # the last row may be cut off
        rows = rows[: _SNIFF_LINES + 1]
        if not rows or len(rows[0]) < 2:
            continue
        score = (sum(len(r) == len(rows[0]) for r in rows), len(rows[0]))
        if score > best_score:
            best, best_score = delimiter, score
    return best


def _dialect(sample: str) -> type[csv.Dialect]:
    """Excel's dialect with the delimiter that fits the file (``sniff_delimiter``).

    Only the delimiter is guessed: the sniffer's quoting guesses (that "" inside a cell is not an escaped quote)
    are wrong for Excel files. Excel's quoting rules are kept."""
    return type("_Sniffed", (csv.excel,), {"delimiter": sniff_delimiter(sample)})


def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    """Rows of a CSV saved as UTF-8 (with or without BOM), falling back to Windows-1252."""
    path = Path(path)
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        reader = csv.DictReader(io.StringIO(text), dialect=_dialect(text))
        return [dict(row) for row in reader]
    raise ValueError(f"{path}: could not read the file; save it as 'CSV UTF-8' and try again")


def parse_date(text: str, where: str = "") -> dt.date:
    """A date as written in a CSV: 2026-01-31, 2026/01/31, or 31/01/2026 / 01/31/2026 when unambiguous."""
    text = (text or "").strip()
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        pass
    found = set()
    for fmt in ("%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y"):
        try:
            found.add(dt.datetime.strptime(text, fmt).date())
        except ValueError:
            continue
    if len(found) == 1:
        return found.pop()
    problem = "is ambiguous (day/month?)" if found else "is not a date"
    raise ValueError(f"{where}: {text!r} {problem}; write dates as YYYY-MM-DD")
