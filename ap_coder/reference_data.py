"""Enterprise reference data: GL Chart of Accounts, Cost Centers and Tax Codes.

Each list is loaded from CSV or JSON and rendered into a compact Markdown
snapshot that is injected into the LLM system prompt.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

UNASSIGNED = "UNASSIGNED"

_FALSE_VALUES = {"0", "false", "no", "n", "inactive"}


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
            try:
                rates.add(round(float(row.get("rate", "")), 6))
            except ValueError:
                continue
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


def load_table(path: str | Path, kind: str, key_column: str) -> ReferenceTable:
    path = Path(path)
    raw_rows = _read_rows(path)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_rows, start=1):
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k is not None}
        if key_column not in row:
            raise ValueError(f"{path}: missing required column {key_column!r}")
        code = row[key_column]
        if not code:
            raise ValueError(f"{path}: row {i} has an empty {key_column!r}")
        if code in seen:
            raise ValueError(f"{path}: duplicate {key_column} {code!r}")
        # Optional "active" column lets finance keep retired codes in the file.
        if row.get("active", "").lower() in _FALSE_VALUES:
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
