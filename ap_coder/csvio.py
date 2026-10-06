"""CSV reading that copes with files saved by Excel or an ERP export."""

from __future__ import annotations

import csv
from pathlib import Path


def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    """Rows of a CSV saved as UTF-8 (with or without BOM), falling back to Windows-1252."""
    path = Path(path)
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with path.open(newline="", encoding=encoding) as fh:
                return [dict(row) for row in csv.DictReader(fh)]
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path}: could not read the file; save it as 'CSV UTF-8' and try again")
