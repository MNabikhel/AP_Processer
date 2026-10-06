"""CSV reading that copes with files saved by Excel or an ERP export."""

from __future__ import annotations

import csv
import datetime as dt
import io
from pathlib import Path

_DELIMITERS = ",;\t|"


def _dialect(sample: str) -> type[csv.Dialect] | csv.Dialect:
    """Comma, or semicolon (French-Canadian Excel), tab or pipe when the header line uses one of those."""
    header = sample.splitlines()[0] if sample else ""
    try:
        return csv.Sniffer().sniff(header, delimiters=_DELIMITERS)
    except csv.Error:
        return csv.excel


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
