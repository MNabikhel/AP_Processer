"""Enterprise reference data: GL Chart of Accounts, Cost Centers and Tax Codes.

Each list is loaded from CSV or JSON and rendered into a compact Markdown
snapshot that is injected into the LLM system prompt.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

UNASSIGNED = "UNASSIGNED"

_FALSE_VALUES = {"0", "false", "no", "n", "inactive"}

# Header names commonly produced by ERP exports (Dynamics 365, SAP, NetSuite,
# Sage, ...), matched case/punctuation-insensitively and renamed to the
# canonical column so files load without manual editing.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "gl_code": (
        "gl_code",
        "gl",
        "gl_account",
        "gl_account_number",
        "account",
        "account_number",
        "account_no",
        "account_code",
        "main_account",
        "mainaccount",
        "g_l_account",
        "natural_account",
        "saknr",
        "hkont",
        "ledger_account",
    ),
    "cost_center": (
        "cost_center",
        "cost_centre",
        "costcenter",
        "costcentre",
        "cost_center_code",
        "cost_centre_code",
        "cc",
        "department_code",
        "dept_code",
        "kostl",
    ),
    "tax_code": ("tax_code", "taxcode", "vat_code", "tax_group", "sales_tax_code", "tax_id", "mwskz"),
    "rate": ("rate", "tax_rate", "vat_rate", "percent", "percentage", "rate_percent"),
    "active": ("active", "is_active", "enabled", "status_active"),
}


def _norm_header(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", name.strip().lower()).strip("_")


def _canonical_headers(headers: list[str]) -> dict[str, str]:
    """Map each original header to its canonical name (unchanged when no alias matches)."""
    mapping = {h: h.strip() for h in headers}
    present = {_norm_header(h) for h in headers}
    for canonical, aliases in COLUMN_ALIASES.items():
        if canonical in present:
            continue  # an exact canonical column always wins
        for header in headers:
            if _norm_header(header) in aliases:
                mapping[header] = canonical
                break
    return mapping


@dataclass(frozen=True)
class ReferenceTable:
    """A list of codes plus descriptive columns the LLM can reason over."""

    kind: str
    key_column: str
    rows: list[dict[str, str]]

    @property
    def codes(self) -> list[str]:
        return [row[self.key_column] for row in self.rows]

    def get(self, code: str) -> dict[str, str] | None:
        for row in self.rows:
            if row[self.key_column] == code:
                return row
        return None

    def to_markdown(self) -> str:
        if not self.rows:
            return "_(none provided)_"
        columns = list(self.rows[0].keys())
        lines = [
            "| " + " | ".join(columns) + " |",
            "| " + " | ".join("---" for _ in columns) + " |",
        ]
        for row in self.rows:
            cells = [str(row.get(col, "")).replace("|", "/").replace("\n", " ") for col in columns]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)


@dataclass(frozen=True)
class ReferenceData:
    chart_of_accounts: ReferenceTable
    cost_centers: ReferenceTable
    tax_codes: ReferenceTable | None = None
    notes: list[str] = field(default_factory=list)

    def tax_rate_values(self) -> set[float]:
        if not self.tax_codes:
            return set()
        rates = set()
        for row in self.tax_codes.rows:
            rate = _parse_rate(row.get("rate", ""))
            if rate is not None:
                rates.add(rate)
        return rates

    def to_prompt_context(self) -> str:
        sections = [
            "### GL Chart of Accounts\n" + self.chart_of_accounts.to_markdown(),
            "### Cost Centers\n" + self.cost_centers.to_markdown(),
        ]
        if self.tax_codes:
            sections.append(
                "### Tax Codes (rate is a decimal fraction, e.g. 0.2 = 20%)\n" + self.tax_codes.to_markdown()
            )
        if self.notes:
            sections.append("### Coding Policy Notes\n" + "\n".join(f"- {n}" for n in self.notes))
        return "\n\n".join(sections)


def _read_rows(path: Path) -> list[dict[str, str]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as fh:
            return [dict(row) for row in csv.DictReader(fh)]
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # Accept {"items": [...]} or any single top-level list value.
            lists = [v for v in data.values() if isinstance(v, list)]
            if len(lists) != 1:
                raise ValueError(f"{path}: expected a JSON array or an object with one array")
            data = lists[0]
        if not isinstance(data, list):
            raise ValueError(f"{path}: expected a JSON array of objects")
        return [{k: "" if v is None else str(v) for k, v in row.items()} for row in data]
    raise ValueError(f"{path}: unsupported reference file type {suffix!r} (use .csv or .json)")


def _parse_rate(value: str) -> float | None:
    """Accept 0.2, 20, "20%" or "20,0" and return a decimal fraction (values >= 1 are percentages)."""
    text = str(value).strip().rstrip("%").replace(",", ".").strip()
    try:
        rate = float(text)
    except ValueError:
        return None
    if rate >= 1 or str(value).strip().endswith("%"):  # no real tax rate is 100%+
        rate /= 100
    return round(rate, 6)


def load_table(path: str | Path, kind: str, key_column: str) -> ReferenceTable:
    path = Path(path)
    raw_rows = _read_rows(path)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    headers = [k for k in (raw_rows[0].keys() if raw_rows else []) if k is not None]
    rename = _canonical_headers(headers)
    if raw_rows and key_column not in rename.values():
        raise ValueError(
            f"{path}: missing required column {key_column!r}. Found columns: {', '.join(headers)}. "
            f"Rename your code column to {key_column!r} (accepted aliases: {', '.join(COLUMN_ALIASES[key_column])})"
        )
    for i, raw in enumerate(raw_rows, start=1):
        row = {rename.get(k, k.strip()): (v or "").strip() for k, v in raw.items() if k is not None}
        if key_column == "tax_code" and "rate" in row:
            rate = _parse_rate(row["rate"])
            if rate is None:
                raise ValueError(f"{path}: row {i} has an unreadable rate {row['rate']!r}")
            row["rate"] = f"{rate:g}"
        code = row[key_column]
        if not code:
            raise ValueError(f"{path}: row {i} has an empty {key_column!r}")
        if code in seen:
            raise ValueError(f"{path}: duplicate {key_column} {code!r}")
        # Optional "active" column lets finance keep retired codes in the file.
        if row.get("active", "").strip().lower() in _FALSE_VALUES:
            continue
        row.pop("active", None)
        seen.add(code)
        rows.append(row)
    if not rows:
        raise ValueError(f"{path}: no active {kind} rows found")
    return ReferenceTable(kind=kind, key_column=key_column, rows=rows)


def load_reference_data(
    chart_of_accounts: str | Path,
    cost_centers: str | Path,
    tax_codes: str | Path | None = None,
    policy_notes: str | Path | None = None,
) -> ReferenceData:
    notes: list[str] = []
    if policy_notes:
        notes = [
            line.strip().lstrip("-* ").strip()
            for line in Path(policy_notes).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    return ReferenceData(
        chart_of_accounts=load_table(chart_of_accounts, "GL account", "gl_code"),
        cost_centers=load_table(cost_centers, "cost center", "cost_center"),
        tax_codes=load_table(tax_codes, "tax code", "tax_code") if tax_codes else None,
        notes=notes,
    )
