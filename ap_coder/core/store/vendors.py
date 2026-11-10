"""The vendor list: the vendor master imported from the ERP (``import_vendor_master``), the vendors added on the review
screen, how an invoice's vendor is found among those whose names read alike (``find_vendor``, ``vendor_key_for``),
each vendor's invoices and terms, the Vendors page's summaries, and where we are (``our_province``,
``site_provinces``)."""

from __future__ import annotations

import bisect
import json
import re
import sqlite3
import threading
from types import MappingProxyType
from typing import Any

from ..caches import MB, Bounded, size_of
from ..keys import ADDED_HERE, BUSINESS_NUMBER, norm_tax_number, same_tax_number
from ..memory import _LEGAL_SUFFIXES, _normalise, vendor_key
from ._base import (
    _ACTIVE_IN,
    ACTIVE_STATUSES,
    FAILED,
    _clean_code,
    _in_list,
    _now,
)
from .capture import capture_lookup
from .dialect import ROWID, like
from .learning import one_snapshot
from .reports import OWN_WORK
from .summary import fetch_rows

# An invoice's coding as approved, else as read (a blank one counts as none), for json_extract: no invoice's whole AI
# output is parsed for a few fields.
_DOC = "COALESCE(NULLIF(NULLIF(final_output, 'null'), '{}'), NULLIF(ai_output, 'null'), '{}')"

# Each vendor's invoices (``vendor_invoices``), kept in this process for the vendors asked most recently (D-160): at
# most VENDOR_ROWS_KEPT rows in all (about 1 KB a row: 50 MB), one vendor up to half of them (a year's biggest vendor
# has about 23,000), and VENDOR_KEYS_KEPT vendors (each name typed on the review screen is asked for, most with no
# invoices), the rows held counted as they are kept and dropped, and within VENDOR_ROWS_MOST bytes (D-195). Each with
# its stamp: (the database's ``summary_id``, the last change of the vendors' change log the rows hold, D-174, and the
# vendor's count of changes then, B-1811).
VENDOR_ROWS_KEPT = 50_000
VENDOR_KEYS_KEPT = 4_096
VENDOR_ROWS_MOST = 64 * MB
_VENDOR_ROWS = Bounded("vendors' invoices", VENDOR_ROWS_MOST, size=lambda kept: size_of(kept[1]))
_VENDOR_ROWS_HELD = 0
_VENDOR_ROWS_LOCK = threading.Lock()
VENDORS_SETTLE = OWN_WORK  # the Vendors page's counts and spend: behind a change by ten times their work (B-2044)
TOTALS_FORMAT = "vendor-totals-1"  # their format as kept for every process (``shared_answer``, D-198)
# JD Edwards' hold payment codes that put a supplier on hold (a setting; the Vendors page's import shows it). "N", its
# usual value, and blank never do (B-2081). "Y" and "1" hold payments and stop new vouchers, "2" holds payments only;
# another code a company uses ("0") holds once it is listed.
HOLD_CODES_SETTING = "jde_hold_codes"
HOLD_CODES = "Y, 1, 2"
NEVER_HOLD = ("", "N")


def listed(text: Any) -> list[str]:
    """A setting's list as its items, in the order written: "Y, 1, 2" is ["Y", "1", "2"]."""
    return [item.strip() for item in str(text or "").split(",") if item.strip()]


def hold_code(code: Any, holding: list[str]) -> str:
    """JD Edwards' hold payment code as a vendor keeps it (``vendors.erp_hold``): the code when it is one of
    ``holding`` (``Store.hold_codes``), "" when it is not a hold ("N", blank, a code not listed). The one place that
    says what a code means: whatever reads ``erp_hold`` reads a hold (B-2081)."""
    code = str(code or "").strip().upper()
    return code if code not in NEVER_HOLD and code in {c.upper() for c in holding} else ""


# The words an ERP's names carry for "retired: do not use" (a setting; a star before or after a name marks one too).
# Defined here, in core, because the store reads it (D-192): the one default for whatever else skips such names.
SKIP_WORDS_SETTING = "skip_words"
SKIP_WORDS = "DO NOT USE, DNU"
MARKED = "marked {} in the supplier master"  # the note of a vendor on hold for its marker (B-2083)
MARKED_LIKE = MARKED.format("%")
_WAS_MARKED = like("vendors.notes")  # (with MARKED_LIKE) a vendor whose note is a marker's: on hold for it


def _markers(words: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    """Each skip word as it is said ("DO NOT USE") and as it is found: a word of its own, however it is spaced (``re``
    keeps the few patterns compiled)."""
    found = []
    for word in words:
        parts = [re.escape(p) for p in re.split(r"[^0-9A-Za-z]+", word) if p]
        if parts:
            pattern = r"(?<![0-9a-z])" + r"[^0-9a-z]*".join(parts) + r"(?![0-9a-z])"
            found.append((" ".join(word.split()).upper(), re.compile(pattern, re.IGNORECASE)))
    return found


def name_marker(name: Any, words: list[str]) -> tuple[str, str]:
    """(the name without it, the marker) of a name the ERP marks as retired: one of ``words`` (``Store.skip_words``)
    in any case and spacing ("NORTHERN PAPER CO  DO NOT USE", "DNU - Old Print Shop"), or a star before or after the
    name ("*LAKESIDE CATERING"); the name as it is and "" for any other. The marker is not part of the vendor's key:
    the supplier is found by its real name, and stopped (B-2083)."""
    text = str(name or "")
    for said, pattern in _markers(words):
        found = pattern.search(text)
        if found:
            return f"{text[: found.start()]} {text[found.end() :]}".strip(), said
    plain = text.strip()
    if plain.startswith("*") or plain.endswith("*"):
        return plain.strip("* "), "*"
    return text, ""


# Words no person's name has: a name with one of them is a company's, however it is written.
_COMPANY_WORDS = _LEGAL_SUFFIXES | {
    "of", "the", "for", "group", "groupe", "service", "services", "associates", "associes", "partners", "holdings",
    "enterprises", "entreprises", "city", "ville", "town", "university", "club", "association", "foundation",
}  # fmt: skip
_NOT_IN_A_NAME = re.compile(r"[\d&/@+#]")
Person = tuple[tuple[str, ...], str, tuple[str, ...]]  # (the surname's words, the first name, middle names or initials)


def person_name(name: Any) -> Person | None:
    """A person's name written as an ERP lists people, "LAST, FIRST M." ("DOE, JANE Q." is (("doe",), "jane",
    ("q",))); None for any other name. Only a name with exactly one comma, no digit and no company word is read so:
    "Smith, Brown & Co" and "Toronto, City of" are companies."""
    text = str(name or "")
    if text.count(",") != 1 or _NOT_IN_A_NAME.search(text):
        return None
    last, given = (tuple(_normalise(part).split()) for part in text.split(","))
    if not (1 <= len(last) <= 3 and 1 <= len(given) <= 3) or _COMPANY_WORDS & {*last, *given}:
        return None
    return last, given[0], given[1:]


def _plain_words(name: Any) -> tuple[str, ...]:
    """The words of a name written in the usual order ("Jane Q. Doe") when it could be a person's: 2 to 6 words, no
    comma, digit or company word; () otherwise."""
    text = str(name or "")
    words = tuple(_normalise(text).split())
    if "," in text or _NOT_IN_A_NAME.search(text) or not 2 <= len(words) <= 6 or _COMPANY_WORDS & set(words):
        return ()
    return words


def _same_middles(a: tuple[str, ...], b: tuple[str, ...], exactly: bool = False) -> bool:
    """Whether two people's middle names can be one person's: either gives none ("Jane Doe" for "DOE, JANE Q."), or
    each is the other's or its initial ("q" for "quinn"). "Q." and "R." are two people. ``exactly``: as many, too."""
    if not a or not b:
        return not exactly or a == b
    return len(a) == len(b) and all(x.startswith(y) or y.startswith(x) for x, y in zip(a, b, strict=True))


def same_person(person: Person, other: Any, exactly: bool = False) -> bool:
    """Whether ``other`` (a name as printed or as a vendor's, in either order) names ``person``: the same surname and
    first name, and middle names that can be the same (``_same_middles``). A different first name is another person,
    whatever the surname."""
    last, first, middles = person
    theirs = person_name(other)
    if theirs:
        return theirs[:2] == (last, first) and _same_middles(theirs[2], middles, exactly)
    words = _plain_words(other)
    cut = len(words) - len(last)
    return cut >= 1 and words[0] == first and words[cut:] == last and _same_middles(words[1:cut], middles, exactly)


def _keep_vendor_rows(key: tuple[str, str], stamp: tuple[Any, int, int], rows: list[Any]) -> None:
    """``rows`` kept as the newest; the vendors asked longest ago go while over either cap. A vendor of more rows than
    half the cap is not kept (what was kept of it goes): the other half stays for the others (B-1721)."""
    global _VENDOR_ROWS_HELD
    with _VENDOR_ROWS_LOCK:
        before = _VENDOR_ROWS.pop(key, None)
        _VENDOR_ROWS_HELD -= len(before[1]) if before else 0
        if len(rows) > VENDOR_ROWS_KEPT // 2:
            return
        _VENDOR_ROWS[key] = (stamp, rows)  # (the ones used longest ago go past VENDOR_ROWS_MOST)
        _VENDOR_ROWS_HELD = sum(len(kept[1]) for kept in _VENDOR_ROWS.values())
        while _VENDOR_ROWS_HELD > VENDOR_ROWS_KEPT or len(_VENDOR_ROWS) > VENDOR_KEYS_KEPT:
            _, (_, dropped) = _VENDOR_ROWS.popitem(last=False)
            _VENDOR_ROWS_HELD -= len(dropped)


def _vendor_invoice_rows(conn: sqlite3.Connection, key: str, ids: list[int] | None) -> list[dict[str, Any]]:
    """``Store._vendor_invoices`` on ``conn``."""
    doc = _DOC
    fields = ", ".join(
        f"json_extract({doc}, '$.{field}') AS {name}"
        for field, name in (
            ("gst_hst_registration_number", "gst_hst_number"), ("qst_registration_number", "qst_number"),
            ("pst_registration_number", "pst_number"), ("remit_bank_account", "bank_account"),
            ("original_invoice_number", "original_invoice_number"),
        )
    )  # fmt: skip
    some = "" if ids is None else f" AND {_in_list('id')}"
    rows = [
        dict(r)
        for r in conn.execute(
            f"""SELECT id, status, export_batch, vendor_name, invoice_number, invoice_date, currency,
                      grand_total, created_at, reviewed_at, {fields}
               FROM invoices WHERE vendor_key = ?{some} ORDER BY id DESC""",
            (key,) if ids is None else (key, json.dumps(ids)),
        )
    ]
    for r in rows:
        for name in ("gst_hst_number", "qst_number", "pst_number", "bank_account", "original_invoice_number"):
            r[name] = r[name] or ""
    return rows


def _read_only(rows: list[dict[str, Any]]) -> list[Any]:
    """Rows as kept for every caller: each read-only (``MappingProxyType``), so none changes another's."""
    return [MappingProxyType(r) for r in rows]


def _newest_first(row: Any) -> int:
    """A vendor's invoice's place in ``vendor_invoices``' order (the newest first)."""
    return -row["id"]


def _distinct(first: dict[str, Any], row: dict[str, Any]) -> bool:
    """Whether two vendor master rows whose names read the same are two suppliers: each has its own ERP ID and its own
    GST/HST number (B-930). Without both numbers it reads as the same supplier set up twice: merged, as before."""
    a, b = str(first.get("erp_id") or "").strip(), str(row.get("erp_id") or "").strip()
    if not a or not b or a == b:
        return False
    gst_a, gst_b = str(first.get("gst") or ""), str(row.get("gst") or "")
    return bool(gst_a and gst_b) and not same_tax_number(gst_a, gst_b)


def _people_keys(rows: list[tuple[str, str]], words: list[str]) -> dict[str, str]:
    """{a person's name in the usual order, as a vendor key: the ERP ID} for the vendors (name, ERP ID) listed "LAST,
    FIRST M.": "jane q doe", and "jane doe" too when only one vendor can be meant, as ``find_vendor`` matches a
    person. So an invoice printed "Jane Q. Doe" gets the supplier number of "DOE, JANE Q."."""
    full: dict[str, set[str]] = {}
    short: dict[str, set[str]] = {}
    for name, ident in rows:
        person = person_name(name_marker(name, words)[0])
        if person:
            last, first, middles = person
            full.setdefault(" ".join((first, *middles, *last)), set()).add(ident)
            short.setdefault(" ".join((first, *last)), set()).add(ident)
    keys = {key: next(iter(found)) for key, found in short.items() if len(found) == 1 and key not in full}
    return keys | {key: next(iter(found)) for key, found in full.items() if len(found) == 1}


def _master_rows_as_kept(rows: list[dict[str, Any]], holding: list[str], words: list[str]) -> list[dict[str, Any]]:
    """Each row of a vendor master import (a copy) as it is kept: ``key``, its name's key without a retired supplier's
    marker; ``marked``, the note of a marked one, which is on hold (B-2083); ``hold``, JD Edwards' hold payment code
    when it is one that holds (B-2082)."""
    kept = []
    for row in rows:
        plain, marker = name_marker(row["vendor_name"], words)
        row = {**row, "key": vendor_key(plain) or vendor_key(row["vendor_name"]),
               "marked": MARKED.format(marker) if marker else ""}  # fmt: skip
        if marker:
            row["status"], row["status_given"] = "on_hold", True
        if row.get("hold") is not None:
            row["hold"] = hold_code(row["hold"], holding)
        kept.append(row)
    return kept


def _retired_twin(first: dict[str, Any], row: dict[str, Any]) -> bool:
    """Whether ``row`` is a retired supplier (marked) by the name of one in use (``first``), under an ERP ID of its
    own: two vendors, the retired one the second, never merged into the one in use (B-2083)."""
    ident = str(row.get("erp_id") or "").strip()
    return bool(row.get("marked") and not first.get("marked") and ident
                and ident != str(first.get("erp_id") or "").strip())  # fmt: skip


def alike_names(printed: str, name: str) -> bool:
    """Whether ``name`` (what AP approved) can be what ``printed`` (what was read) misreads: the same letters but a few
    ("N0rthwind Supp1y" for "Northwind Supply"), or the one's words all in the other ("NORTHWIND" for "Northwind Supply
    Ltd."). The customer's name read as the vendor's, or another vendor's, is not (D-119)."""
    from difflib import SequenceMatcher

    a, b = vendor_key(printed or ""), vendor_key(name or "")
    if not a or not b or a == b:
        return False
    if SequenceMatcher(None, a.replace(" ", ""), b.replace(" ", "")).ratio() >= 0.75:
        return True
    wa, wb = set(a.split()), set(b.split())
    return min(len(wa), len(wb)) >= 1 and (wa <= wb or wb <= wa) and len("".join(wa & wb)) >= 4


class VendorsMixin:
    # --- A vendor name read wrong the same way, as AP corrected it (D-119) -------------------------------------------

    def remember_vendor_alias(self, printed: str, vendor_name: str, gst_number: Any = "", *,
                              invoice_id: int | None = None) -> bool:  # fmt: skip
        """An approval that corrected the vendor name read (``printed``) to ``vendor_name``: the next invoice that reads
        so is that vendor's (``vendor_alias``). With ``vendor_name`` the name read itself (kept as read), a name AP
        corrected before is no longer taken for another. Kept with the approval (``invoice_id``): withdrawn with it.
        False when nothing was remembered (the names do not look alike)."""
        key = vendor_key(printed or "")
        same = key == vendor_key(vendor_name or "")
        if not key or not vendor_name or (not same and not alike_names(printed, vendor_name)):
            return False
        with self._conn() as conn:
            if (
                same
                and conn.execute("SELECT 1 FROM vendor_aliases WHERE alias_key = ? LIMIT 1", (key,)).fetchone() is None
            ):
                return False  # a name approved as read, and never corrected: nothing to undo
            if invoice_id is not None:
                conn.execute("DELETE FROM vendor_aliases WHERE invoice_id = ? AND alias_key = ?", (invoice_id, key))
            conn.execute(
                "INSERT INTO vendor_aliases (alias_key, printed, vendor_name, gst_number, invoice_id, at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    key,
                    str(printed).strip(),
                    str(vendor_name).strip(),
                    str(gst_number or "").strip(),
                    invoice_id,
                    _now(),
                ),
            )
        return True

    def vendor_alias(self, name: str | None, gst_number: Any = None) -> dict[str, Any] | None:
        """The vendor an invoice whose vendor name reads ``name`` is, as AP corrected that name on an earlier invoice:
        {"vendor_name", "gst_number", "printed", "confirmed": approvals that said so since it was last approved
        otherwise}. None when AP never corrected this name, when its last approval kept it as read, or when the invoice
        shows a GST/HST number that is not that vendor's."""
        key = vendor_key(name or "")
        if not key:
            return None
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM vendor_aliases WHERE alias_key = ? ORDER BY id DESC LIMIT 50", (key,))]  # fmt: skip
        if not rows or vendor_key(rows[0]["vendor_name"]) == key:
            return None
        target = vendor_key(rows[0]["vendor_name"])
        confirmed = 0
        for r in rows:
            if vendor_key(r["vendor_name"]) != target:
                break
            confirmed += 1
        number, known = str(gst_number or "").strip(), str(rows[0]["gst_number"] or "")
        if number and known and not same_tax_number(number, known):
            return None
        return {"vendor_name": rows[0]["vendor_name"], "gst_number": known, "printed": rows[0]["printed"],
                "confirmed": confirmed}  # fmt: skip

    def _aliased(self, name: str | None, gst_number: Any = None) -> str | None:
        """The vendor name ``name`` reads as, AP corrected (``vendor_alias``), when no vendor goes by ``name`` itself;
        None otherwise."""
        key = vendor_key(name or "")
        if not key:
            return None
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM vendor_aliases WHERE alias_key = ? LIMIT 1", (key,)).fetchone() is None:
                return None  # the usual case: one look, no other
            if conn.execute("SELECT 1 FROM vendors WHERE vendor_key = ?", (key,)).fetchone() is not None:
                return None  # a vendor of that very name
        alias = self.vendor_alias(name, gst_number)
        return alias["vendor_name"] if alias else None

    # --- Vendors ----------------------------------------------------------------------------------------

    def get_vendor(self, key: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM vendors WHERE vendor_key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def held_vendors(self) -> list[dict[str, Any]]:
        """The vendors on hold, here or in JD Edwards: kept in each process until the vendor list changes
        (``changes/vendors``, D-159), not read out of 20,000 vendors at each check (D-174). Each caller gets rows of
        its own."""
        kept = self.summary_memo("held_vendors", self._held_vendors, topic="", changes="vendors")
        return [dict(r) for r in kept]

    def _held_vendors(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return fetch_rows(conn, "SELECT * FROM vendors WHERE status = 'on_hold' OR COALESCE(erp_hold, '') != ''")

    def find_vendor(self, name: str | None, gst_number: Any = None, erp_id: Any = None) -> dict[str, Any] | None:
        """The vendor list's row for an invoice's vendor: among the vendors whose names read as this one (the vendor
        master keeps a second one under "<name> #<ERP ID>", B-930), the one with the invoice's GST/HST number, else
        its ERP ID, else the one under the name itself (B-1021). None when no vendor goes by this name. One vendor by
        the name (the usual case): that vendor, whatever number the invoice shows (the checks then say it differs)."""
        key = vendor_key(name or "")
        if not key:
            return None
        aliased = self._aliased(name, gst_number)
        if aliased:  # a name AP corrected to this vendor's (D-119)
            name, key = aliased, vendor_key(aliased)
        with self._conn() as conn:
            # The keys starting "<key> #" are those from "<key> #" up to "<key> $": a seek in the vendor key's index,
            # not a read of the whole list (B-1582); in the order they were kept, as before.
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM vendors WHERE vendor_key = ? OR (vendor_key >= ? AND vendor_key < ?) ORDER BY rowid",
                (key, f"{key} #", f"{key} $"),
            )]  # fmt: skip
        own = next((r for r in rows if r["vendor_key"] == key), None)
        if not rows:  # no vendor under the name as written: its supplier without a marker, or the person it names
            return self._vendor_named_otherwise(name or "")
        if len(rows) < 2:
            return own
        number = norm_tax_number(gst_number)
        if sum(c.isdigit() for c in number) >= 9:
            found = [r for r in rows if r.get("expected_gst") and same_tax_number(r["expected_gst"], number)]
            if found:
                return own if own in found else found[0]
        ident = str(erp_id or "").strip()
        if ident:
            found = [r for r in rows if str(r.get("erp_id") or "").strip() == ident]
            if found:
                return own if own in found else found[0]
        return own

    def skip_words(self) -> list[str]:
        """The words that mark a retired supplier in the ERP's names (SKIP_WORDS unless the setting lists others)."""
        return listed(self.get_setting(SKIP_WORDS_SETTING, SKIP_WORDS))

    def _vendors_like(self, key: str, more: bool = False) -> list[dict[str, Any]]:
        """The vendors under ``key`` and its second ones' ("<key> #<ERP ID>"); ``more``: every key starting with it
        as a word. A seek in the vendor key's index (B-1582), in the order they were kept."""
        with self._conn() as conn:
            return fetch_rows(conn, "SELECT * FROM vendors WHERE vendor_key = ? OR (vendor_key >= ? AND vendor_key "
                              f"< ?) ORDER BY {ROWID}", (key, f"{key} " if more else f"{key} #",
                                                         f"{key}!" if more else f"{key} $"))  # fmt: skip

    def _vendor_named_otherwise(self, name: str) -> dict[str, Any] | None:
        """The vendor of a name no vendor is kept under as it is written. A name with a retired supplier's marker
        (picked from the list as the master shows it: "NORTHERN PAPER CO  DO NOT USE") is the supplier kept under
        the name without it, the one shown under that very name first (B-2083). A person's name is the one vendor
        that person is, written the other way round or without a middle initial: "Jane Q. Doe" and "Jane Doe" for
        "DOE, JANE Q.", "Doe, Jane Q." for "Jane Q. Doe" (``same_person``); none when two vendors could be."""
        words = self.skip_words()
        plain, marker = name_marker(name, words)
        if marker and (key := vendor_key(plain)):
            rows = self._vendors_like(key)
            shown = [r for r in rows if vendor_key(r["display_name"]) == vendor_key(name)]
            if found := (shown or [r for r in rows if r["vendor_key"] == key]):
                return found[0]
        asked = person_name(plain)
        if asked:  # "DOE, JANE Q.": the vendors "LAST, FIRST …" and the ones starting with the first name
            starts = [" ".join((*asked[0], asked[1])), asked[1]]
        else:  # "Jane Q. Doe": the vendors "LAST, FIRST …", for each way its last words can be the surname
            given = _plain_words(plain)
            starts = [" ".join((*given[cut:], given[0])) for cut in range(max(1, len(given) - 3), len(given))]

        def fits(row: dict[str, Any], exactly: bool = False) -> bool:
            theirs = name_marker(row["display_name"], words)[0]
            if asked:
                return same_person(asked, theirs, exactly)
            person = person_name(theirs)
            return person is not None and same_person(person, plain, exactly)

        rows = [row for start in starts for row in self._vendors_like(start, more=True) if fits(row)]
        found = list({row["vendor_key"]: row for row in rows}.values())
        if len(found) > 1:  # "DOE, JANE" and "DOE, JANE Q." for "Jane Doe": the one written with as much
            found = [row for row in found if fits(row, exactly=True)]
        return found[0] if len(found) == 1 else None

    def vendor_key_for(self, name: str | None, gst_number: Any = None) -> str:
        """The vendor key an invoice's vendor is kept under (its history, its lessons, its checks): ``vendor_key`` of
        the name, or the key of the vendor list's second vendor by that name it is (``find_vendor``, B-1021)."""
        key = vendor_key(name or "")
        if not key:
            return ""
        found = self.find_vendor(name, gst_number)
        if found:
            return found["vendor_key"]
        aliased = self._aliased(name, gst_number)  # a name AP corrected, of a vendor not in the list (D-119)
        return vendor_key(aliased) if aliased else key

    def save_vendor(
        self, key: str, display_name: str, status: str = "active", expected_gst: str = "", notes: str = "",
        actor: str | None = None, qst_number: str | None = None, pst_number: str | None = None,
        province: str | None = None,
    ) -> None:  # fmt: skip
        """Add or change a vendor. QST, PST and province left None keep what the vendor has."""
        if status not in ("active", "on_hold"):
            raise ValueError(f"unknown vendor status {status!r}")
        before = self.get_vendor(key) or {}
        values = {"status": status, "expected_gst": expected_gst.strip(), "notes": notes.strip()}
        extra = {"qst_number": qst_number, "pst_number": pst_number, "province": province}
        values.update({k: str(v).strip().upper() if k == "province" else str(v).strip()
                       for k, v in extra.items() if v is not None})  # fmt: skip
        keep = {k: before.get(k) or "" for k in extra if extra[k] is None}
        row = {**keep, **values}
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO vendors (vendor_key, display_name, status, expected_gst, notes, updated_at, qst_number,
                   pst_number, province)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(vendor_key) DO UPDATE SET
                   display_name = excluded.display_name, status = excluded.status,
                   expected_gst = excluded.expected_gst, notes = excluded.notes, updated_at = excluded.updated_at,
                   qst_number = excluded.qst_number, pst_number = excluded.pst_number, province = excluded.province""",
                (key, display_name, status, values["expected_gst"], values["notes"], _now(), row["qst_number"],
                 row["pst_number"], row["province"]),
            )  # fmt: skip
            changed = {k: v for k, v in values.items() if before.get(k, "active" if k == "status" else "") != v}
            if changed:
                # A vendor's note is in a check's words only while it is on hold, or added from the review screen
                # (VENDOR_ON_HOLD, VENDOR_ADDED_HERE): another one's changes no check (D-155).
                said = status == "on_hold" or before.get("erp_hold") or before.get("in_master") == ADDED_HERE
                self._log(conn, "vendor_updated", actor=actor, detail={"vendor": display_name, **changed},
                          checked=not before or set(changed) != {"notes"} or bool(said))  # fmt: skip

    def import_vendor_master(
        self, rows: list[dict[str, Any]], actor: str | None = None, only_new: bool = False, by_erp_id: bool = False,
        log: bool = True,
    ) -> dict[str, Any]:  # fmt: skip
        """Import the ERP's vendor list (``vendors.master_rows`` output). Each vendor is matched by name;
        what the file gives (ERP ID, GST/HST #, terms, default GL, status) replaces what was there; what
        it has no column for is kept.
        Rows whose names normalise to the same vendor are merged (on hold if any of them is) and reported
        in ``duplicates``: often the same supplier set up twice in the ERP. With ``only_new``, vendors
        already in the list are left alone (``inserted`` lists the new ones). ``by_erp_id`` (the JD Edwards sync,
        which brings the list a page or a night's changes at a time, D-181): the vendors already there count as rows
        before these, so a row goes to the vendor of its name holding its ERP ID ("Acme", or "Acme #4002" the second
        of two, B-930) and another ERP ID's row of that name is merged into that vendor or is the second vendor, as
        in one file. ``log``: the import's event (the sync logs one for all its pages).
        A supplier whose name the ERP marks as retired ("… DO NOT USE", ``name_marker``) is kept under its name
        without the marker, on hold, with a note that says so; when the list has a supplier of that name in use too,
        that one is the vendor of the name and the retired one is kept under its ERP ID (B-2083)."""
        added = updated = 0
        now = _now()
        merged, names = self._master_rows_merged(rows, by_erp_id)
        duplicates = [n for n in names.values() if len(n) > 1]
        inserted: list[str] = []
        with self._conn() as conn:
            existing = {r["vendor_key"] for r in conn.execute("SELECT vendor_key FROM vendors")}
            for key, row in merged.items():
                if only_new and key in existing:
                    continue
                marked = row.get("marked") or ""
                # A marked supplier shows the master's name, on hold with its note (AP's own note is kept); one no
                # longer marked there (its note is still the marker's) is active again under the master's name.
                conn.execute(
                    f"""INSERT INTO vendors (vendor_key, display_name, status, expected_gst, notes, updated_at, erp_id,
                       terms, default_gl, in_master, qst_number, pst_number, province, erp_hold)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                       ON CONFLICT(vendor_key) DO UPDATE SET
                       display_name = CASE WHEN excluded.notes != '' OR {_WAS_MARKED}
                                      THEN excluded.display_name ELSE vendors.display_name END,
                       notes = CASE WHEN excluded.notes != '' AND vendors.notes = '' THEN excluded.notes
                               WHEN excluded.notes = '' AND {_WAS_MARKED} THEN '' ELSE vendors.notes END,
                       erp_hold = CASE WHEN ? THEN excluded.erp_hold ELSE vendors.erp_hold END,
                       qst_number = CASE WHEN excluded.qst_number != '' THEN excluded.qst_number
                                    ELSE vendors.qst_number END,
                       pst_number = CASE WHEN excluded.pst_number != '' THEN excluded.pst_number
                                    ELSE vendors.pst_number END,
                       province = CASE WHEN excluded.province != '' THEN excluded.province ELSE vendors.province END,
                       erp_id = CASE WHEN ? THEN excluded.erp_id ELSE vendors.erp_id END,
                       terms = CASE WHEN ? THEN excluded.terms ELSE vendors.terms END,
                       default_gl = CASE WHEN ? THEN excluded.default_gl ELSE vendors.default_gl END,
                       in_master = 1, updated_at = excluded.updated_at,
                       expected_gst = CASE WHEN excluded.expected_gst != '' THEN excluded.expected_gst
                                      ELSE vendors.expected_gst END,
                       status = CASE WHEN ? THEN excluded.status WHEN {_WAS_MARKED} THEN 'active'
                                ELSE vendors.status END""",
                    (key, row["vendor_name"], row.get("status") or "active", row.get("gst") or "", marked, now,
                     row.get("erp_id") or "", row.get("terms") or "", _clean_code(row.get("default_gl")),
                     row.get("qst") or "", row.get("pst") or "", row.get("province") or "", row.get("hold") or "",
                     MARKED_LIKE, MARKED_LIKE,
                     *(row.get(f) is not None for f in ("hold", "erp_id", "terms", "default_gl")),
                     bool(row.get("status_given")), MARKED_LIKE),
                )  # fmt: skip
                if key in existing:
                    updated += 1
                else:
                    added += 1
                    inserted.append(key)
                    existing.add(key)
            if log:
                self._log(conn, "vendors_imported", actor=actor, detail={
                    "added": added, "updated": updated, "duplicates": len(duplicates),
                })  # fmt: skip
        return {"added": added, "updated": updated, "duplicates": duplicates, "inserted": inserted}

    def _master_rows_merged(
        self, rows: list[dict[str, Any]], by_erp_id: bool
    ) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
        """({the key each vendor of an import is kept under: its row, the rows of one vendor merged}, {key: the names
        merged under it}): ``import_vendor_master``'s rules, before anything is written."""
        merged: dict[str, dict[str, Any]] = {}
        names: dict[str, list[str]] = {}
        words = self.skip_words()
        # the suppliers in use first: of two by one name, the one not marked is the vendor of the name (B-2083)
        rows = sorted(_master_rows_as_kept(rows, self.hold_codes(), words), key=lambda row: bool(row["marked"]))
        known = self._master_rows_named(rows, words) if by_erp_id else {}
        for row in rows:
            key = row["key"]
            if not key:
                continue
            if known and key not in merged:
                key = self._earlier_row(key, row, known, merged, names)
            first = merged.get(key)
            if first is not None and (_distinct(first, row) or _retired_twin(first, row)):
                # Two suppliers whose names read the same, each with its own ERP ID (and GST/HST number): two vendors,
                # the second kept under its ERP ID, not merged into the first (B-930). So is a retired supplier by
                # the name of one in use (B-2083).
                key = f"{key} #{row['erp_id'].strip()}"
                if key not in merged:
                    merged[key] = dict(row)
                    continue
            names.setdefault(key, []).append(row["vendor_name"])
            if key in merged:
                first = merged[key]
                if row.get("status") == "on_hold":
                    first["status"], first["status_given"] = "on_hold", True
                for field in ("erp_id", "gst", "terms", "default_gl", "qst", "pst", "province", "hold", "marked"):
                    first[field] = first.get(field) or row.get(field) or first.get(field)
            else:
                merged[key] = dict(row)
        return merged, names

    def _master_rows_named(self, rows: list[dict[str, Any]], words: list[str]) -> dict[str, dict[str, Any]]:
        """The vendors already there under these rows' names, or a second one's ("<name> #<ERP ID>"), as rows of the
        import (``_master_rows_as_kept``'s), by key."""
        keys = set()
        for row in rows:
            if key := row["key"]:
                keys |= {key, f"{key} #{str(row.get('erp_id') or '').strip()}"}
        with self._conn() as conn:
            found = fetch_rows(conn, "SELECT vendor_key, display_name, erp_id, expected_gst, terms, default_gl, "
                               "qst_number, pst_number, province FROM vendors WHERE " + _in_list("vendor_key"),
                               (json.dumps(sorted(keys)),))  # fmt: skip
        there = [{"vendor_name": r["display_name"], "erp_id": r["erp_id"], "gst": r["expected_gst"],
                  "terms": r["terms"], "default_gl": r["default_gl"], "qst": r["qst_number"],
                  "pst": r["pst_number"], "province": r["province"]} for r in found]  # fmt: skip
        return {r["vendor_key"]: row for r, row in zip(found, _master_rows_as_kept(there, [], words), strict=True)}

    @staticmethod
    def _earlier_row(key: str, row: dict[str, Any], known: dict[str, dict[str, Any]],
                     merged: dict[str, dict[str, Any]], names: dict[str, list[str]]) -> str:  # fmt: skip
        """``by_erp_id``: the key of the vendor already there that ``row`` is (its name's, holding its ERP ID, or the
        second one's of its name), else ``key``; a vendor there of that name with another ERP ID is put in ``merged``
        as an earlier row (its own values kept when the row is merged into it). A retired one there (marked) gives
        the name to a row in use: it is put under its own ERP ID, the second of the two (B-2083)."""
        erp_id = str(row.get("erp_id") or "").strip()
        for own in (key, f"{key} #{erp_id}"):
            if erp_id and str((known.get(own) or {}).get("erp_id") or "").strip() == erp_id:
                return own
        there = known.get(key)
        if there and str(there.get("erp_id") or "").strip() and erp_id:
            if _retired_twin(row, there):
                merged[f"{key} #{there['erp_id'].strip()}"] = dict(there)
                return key
            merged[key] = dict(there)
            names[key] = [there["vendor_name"]]
        return key

    def our_province(self) -> str:
        """Where invoices are delivered to us when they print no ship-to (Settings → Review), "" if not set."""
        return self.get_setting("our_province").strip().upper()

    def site_provinces(self) -> dict[str, str]:
        """{cost center: province} for sites in other provinces than ours (Settings → Review: "CC400=BC" per line)."""
        out: dict[str, str] = {}
        for line in re.split(r"[\n,;]+", self.get_setting("site_provinces")):
            code, sep, province = line.partition("=")
            if sep and code.strip() and province.strip():
                out[code.strip()] = province.strip().upper()
        return out

    def vendor_choices(self) -> list[dict[str, Any]]:
        """The vendors a reviewer picks from on the review screen: the vendor master's and the ones added there; with
        no vendor master, every vendor with an approved invoice too. Sorted by name."""
        with self._conn() as conn:
            rows = fetch_rows(conn, "SELECT * FROM vendors WHERE in_master IN (1, 2)")
            if not any(r["in_master"] == 1 for r in rows):
                known = {r["vendor_key"] for r in rows}
                # the tax numbers AP gave such a vendor on the Vendors page come along (the review screen fills them)
                own = {r["vendor_key"]: r for r in fetch_rows(conn, "SELECT * FROM vendors WHERE in_master = 0")}
                for r in self.summary_memo("approved_vendors", self._approved_vendors, "approved_vendors"):
                    if r["vendor_key"] not in known:
                        numbers = {c: own.get(r["vendor_key"], {}).get(c) or ""
                                   for c in ("expected_gst", "qst_number", "pst_number")}  # fmt: skip
                        rows.append({"vendor_key": r["vendor_key"], "display_name": r["name"], "in_master": 0,
                                     **numbers})  # fmt: skip
        return sorted(rows, key=lambda r: str(r.get("display_name") or "").lower())

    def _approved_vendors(self) -> list[dict[str, Any]]:
        """Every vendor with an approved invoice, its name (``vendor_choices``)."""
        with self._conn() as conn:
            self._summary_fresh(conn)
            return fetch_rows(conn, "SELECT vendor_key, MAX(vendor_name) name FROM invoice_summary WHERE status = "
                                    "'approved' AND vendor_key != '' GROUP BY vendor_key")  # fmt: skip

    def add_vendor_here(
        self, name: str, *, gst: str = "", qst: str = "", pst: str = "", province: str = "", actor: str | None = None,
        invoice_id: int | None = None,
    ) -> str:  # fmt: skip
        """A new vendor added to the vendor list from the review screen (``vendors.ADDED_HERE``): every invoice from
        it needs a person until it is in the vendor master imported from the ERP. Returns its key. A vendor already
        in the list is refused (ValueError)."""
        key = vendor_key(name)
        if not key:
            raise ValueError("Type the vendor's name.")
        before = self.get_vendor(key) or {}
        if before.get("in_master"):
            raise ValueError(f"{before.get('display_name') or name} is already in the vendor list: pick it.")
        note = f"Added by {actor or 'a reviewer'} on {_now()[:10]}"
        note += f" from invoice #{invoice_id}." if invoice_id else "."
        notes = "\n".join(n for n in (note, str(before.get("notes") or "").strip()) if n)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO vendors (vendor_key, display_name, status, expected_gst, notes, updated_at, qst_number,
                   pst_number, province, in_master) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 2)
                   ON CONFLICT(vendor_key) DO UPDATE SET display_name = excluded.display_name,
                   expected_gst = excluded.expected_gst, notes = excluded.notes, updated_at = excluded.updated_at,
                   qst_number = excluded.qst_number, pst_number = excluded.pst_number, province = excluded.province,
                   in_master = 2""",
                (key, name.strip(), before.get("status") or "active", gst.strip(), notes, _now(), qst.strip(),
                 pst.strip(), province.strip().upper()),
            )  # fmt: skip
            self._log(conn, "vendor_added", invoice_id=invoice_id, actor=actor, detail={
                "vendor": name.strip(), "gst": gst.strip(), "qst": qst.strip(), "pst": pst.strip(),
                "province": province.strip().upper()})  # fmt: skip
        return key

    def delete_vendors(self, keys: list[str], actor: str | None = None) -> int:
        """Remove vendors from the vendor list (e.g. the demo's sample vendor master), logged: an on-hold vendor
        removed lifts its hold."""
        where, params = _in_list("vendor_key"), (json.dumps(list(keys)),)
        with self._conn() as conn:
            names = [r[0] or r[1] for r in conn.execute(f"SELECT display_name, vendor_key FROM vendors WHERE {where}",
                                                         params)]  # fmt: skip
            count = conn.execute(f"DELETE FROM vendors WHERE {where}", params).rowcount
            if count:
                self._log(conn, "vendors_deleted", actor=actor, detail={"vendors": names})
            return count

    def has_vendor_master(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM vendors WHERE in_master = 1 LIMIT 1").fetchone() is not None

    def master_vendors(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM vendors WHERE in_master = 1")]

    def master_vendor_ids(self) -> list[tuple[str, str]]:
        """(vendor key, ERP ID) of each vendor of the vendor master: what Import invoices' setup step counts, at every
        view (``master_vendors``' every column of 20,000 vendors took 0.23 s, D-166)."""
        with self._conn() as conn:
            return [tuple(r) for r in conn.execute("SELECT vendor_key, erp_id FROM vendors WHERE in_master = 1")]

    def master_vendors_of(self, keys: set[str], ids: set[str]) -> dict[str, dict[str, Any]]:
        """The vendor master's vendors of these keys or ERP IDs, by key: what the JD Edwards sync reports changed."""
        sql = (
            "SELECT vendor_key, display_name, erp_id, expected_gst, terms, status, qst_number, pst_number, province, "
            "erp_bank, erp_hold FROM vendors WHERE in_master = 1 AND {} IN (SELECT value FROM json_each(?))"
        )
        found: dict[str, dict[str, Any]] = {}
        with self._conn() as conn:
            for column, values in (("vendor_key", keys), ("erp_id", ids)):
                for r in conn.execute(sql.format(column), (json.dumps(sorted(values)),)):
                    found[r["vendor_key"]] = dict(r)
        return found

    def hold_codes(self) -> list[str]:
        """JD Edwards' hold payment codes that put a supplier on hold (HOLD_CODES unless the setting lists others)."""
        return listed(self.get_setting(HOLD_CODES_SETTING)) or listed(HOLD_CODES)

    def set_erp_bank_and_hold(self, values: dict[str, tuple[str, str]]) -> None:
        """{ERP ID: (the bank account JDE pays on, JDE's hold code)} on the vendor master's vendors of those IDs (kept
        apart from AP's own hold, D-105): only where either changed. The hold code is kept when it is one that holds
        (``hold_code``), blank otherwise."""
        holding = self.hold_codes()
        kept = [(bank, hold_code(hold, holding), erp_id) for erp_id, (bank, hold) in values.items()]
        with self._conn() as conn:
            conn.executemany("UPDATE vendors SET erp_bank = ?1, erp_hold = ?2 WHERE erp_id = ?3 AND in_master = 1 AND "
                             "(erp_bank != ?1 OR erp_hold != ?2)", kept)  # fmt: skip

    def vendor_ids(self) -> dict[str, str]:
        """{vendor_key: ERP vendor ID} from the vendor master, plus {"gst:<number>": ERP vendor ID} so an
        invoice whose vendor name differs but whose GST/HST number matches still gets its ID. Kept in each process
        until the vendor list changes (``changes/vendors``, D-159): each check asks it twice. Shared: read it, don't
        change it."""
        return self.summary_memo("vendor_ids", self._vendor_ids, topic="", changes="vendors")

    def _vendor_ids(self) -> dict[str, str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT vendor_key, erp_id, expected_gst FROM vendors WHERE erp_id != ''").fetchall()
        ids = {r[0]: r[1] for r in rows}
        for r in rows:
            number = norm_tax_number(r[2])
            if sum(c.isdigit() for c in number) >= 9:
                ids.setdefault(f"gst:{number}", r[1])
                if number[:9].isdigit():  # bn: the master keeps only the Business Number; bnx: the full account
                    ids.setdefault(f"{'bn' if len(number) == 9 else 'bnx'}:{number[:9]}", r[1])
        with self._conn() as conn:  # the people ("LAST, FIRST M."), by their names as a claim prints them
            people = conn.execute("SELECT display_name, erp_id FROM vendors WHERE erp_id != '' AND "
                                  f"{like('display_name')}", ("%,%",)).fetchall()  # fmt: skip
        for key, ident in _people_keys([tuple(r) for r in people], self.skip_words()).items():
            ids.setdefault(key, ident)
        return ids

    def master_by_tax_number(self, number: Any) -> list[dict[str, Any]]:
        """The vendor master's vendors whose GST/HST number is the registration ``number`` is (``same_tax_number``),
        in the list's order: for an invoice not found by name. Only those vendors are read, found by the master's
        numbers kept in each process until the vendor list changes (``changes/vendors``, D-159)."""
        text = norm_tax_number(number)
        if not text:
            return []
        exact, business = self.summary_memo("master_numbers", self._master_numbers, topic="", changes="vendors")
        found = {*exact.get(text, ()), *(business.get(text[:9], ()) if BUSINESS_NUMBER.fullmatch(text) else ())}
        if not found:
            return []
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM vendors WHERE rowid IN (SELECT value FROM json_each(?)) AND in_master = 1 "
                "ORDER BY rowid", (json.dumps(sorted(found)),),
            )]  # fmt: skip
        return [r for r in rows if same_tax_number(r["expected_gst"], number)]

    def _master_numbers(self) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
        """({a GST/HST number as compared: the rowids of the master's vendors with it}, {a 9-digit Business Number:
        the rowids of those whose number is it, with or without its RT suffix}): every vendor ``same_tax_number``
        could match is in one of them."""
        with self._conn() as conn:
            rows = conn.execute("SELECT rowid, expected_gst FROM vendors WHERE in_master = 1 AND expected_gst != ''")
            exact: dict[str, list[int]] = {}
            business: dict[str, list[int]] = {}
            for rowid, gst in rows:
                text = norm_tax_number(gst)
                if text:
                    exact.setdefault(text, []).append(rowid)
                    if BUSINESS_NUMBER.fullmatch(text):
                        business.setdefault(text[:9], []).append(rowid)
        return exact, business

    def all_vendor_terms(self) -> dict[str, str]:
        """{vendor_key: payment terms} for every vendor that has terms in the vendor master."""
        with self._conn() as conn:
            return {r[0]: r[1] for r in conn.execute("SELECT vendor_key, terms FROM vendors WHERE terms != ''")}

    def vendor_terms(self, key: str) -> str:
        """The vendor master's payment terms for this vendor ('' if none)."""
        if not key:
            return ""
        with self._conn() as conn:
            row = conn.execute("SELECT terms FROM vendors WHERE vendor_key = ?", (key,)).fetchone()
        return row[0] if row else ""

    def vendor_invoices(self, key: str, shared: bool = False) -> list[dict[str, Any]]:
        """This vendor's invoices (newest first) with the GST/HST number and bank account each one carried: the vendor
        checks and the tax-number notes both ask, at every check of the review screen. Kept in this process for each
        vendor (``_keep_vendor_rows``, D-160) on the same database (its ``summary_id``), and brought up to date from the
        vendors' change log (``vendor_changes``, kept by triggers: an invoice of its added or deleted, or a column
        listed here changed, whoever changed it), read with the stamp in one statement: only the invoices changed since
        are read again (D-174), all of them once the log no longer holds every change since (``VENDOR_CHANGES_KEPT``).
        A vendor too big to keep is read once in a page's session (``_session_memo``, B-1721). Inside a write under way
        they are read afresh and not kept: it may yet be undone. Each caller gets rows of its own; ``shared``: the rows
        kept, read-only (a check reads 23,000 of them, twice)."""
        kept = (str(self.path), key)
        with _VENDOR_ROWS_LOCK:
            hit = _VENDOR_ROWS.get(kept)
        rows = None
        with self._conn() as conn:
            if conn.in_transaction:
                return self._vendor_invoices(key, conn=conn)
            with one_snapshot(conn):  # (the log and the invoices it names read in one moment, B-1812)
                summary_id, first, last, count, logged, changed = conn.execute(
                    "SELECT (SELECT value FROM settings WHERE key = 'summary_id'), (SELECT MIN(seq) FROM "
                    "vendor_changes), (SELECT MAX(seq) FROM vendor_changes), (SELECT COALESCE(CAST(value AS INTEGER), "
                    "0) FROM settings WHERE key = 'changes/vendor/' || ?2), (SELECT COUNT(*) FROM vendor_changes WHERE "
                    "seq > ?1 AND vendor_key = ?2), (SELECT json_group_array(DISTINCT invoice_id) FROM vendor_changes "
                    "WHERE seq > ?1 AND vendor_key = ?2)",
                    (hit[0][1] if hit else None, key),
                ).fetchone()  # fmt: skip  (``seq > NULL``: none, when none is kept)
                stamp = (summary_id, last or 0, count or 0)
                # Every change since logged: the log still holds them all, and the vendor's count of changes (which
                # an older AP Coder's triggers keep too, writing no log) moved by as many (B-1811).
                if hit is not None and hit[0][0] == summary_id and hit[0][1] >= (first or 1) - 1 and (
                        stamp[2] - hit[0][2] == logged):  # fmt: skip
                    ids = json.loads(changed)
                    rows = self._vendor_rows_changed(key, hit[1], ids, conn) if ids else hit[1]
        if rows is None:
            rows = self._session_memo(("vendor_invoices", key, stamp), lambda: _read_only(self._vendor_invoices(key)),
                                      shared=True)  # fmt: skip
        if hit is not None and hit[0] == stamp:
            with _VENDOR_ROWS_LOCK:
                if kept in _VENDOR_ROWS:
                    _VENDOR_ROWS.move_to_end(kept)
        else:
            _keep_vendor_rows(kept, stamp, rows)
        return rows if shared else [r.copy() for r in rows]

    def _vendor_rows_changed(self, key: str, rows: list[Any], ids: list[int], conn: sqlite3.Connection) -> list[Any]:
        """``rows``, this vendor's invoices as kept (newest first), with the invoices ``ids`` read again (on ``conn``,
        in the moment the log was read): each changed, added, deleted, or moved to or from this vendor. A copy: the
        rows kept may be another caller's meanwhile."""
        fresh = _read_only(self._vendor_invoices(key, ids, conn=conn))
        rows = list(rows)
        for invoice_id in ids:  # found by its place in the order, not by looking at each row
            at = bisect.bisect_left(rows, -invoice_id, key=_newest_first)
            if at < len(rows) and rows[at]["id"] == invoice_id:
                del rows[at]
        for row in fresh:
            bisect.insort(rows, row, key=_newest_first)
        return rows

    def approved_numbers(self, keys: list[str]) -> dict[str, list[tuple[Any, Any]]]:
        """{vendor key: [(the GST/HST number, the bank account) its approved invoices carried, each pair once, the
        latest first]}, for the vendors ``keys`` (the vendors on hold: ``coding.vendors.held_vendor``), in one read; ""
        for one not carried. Kept in each process until an invoice of one of them changes (their counts,
        ``changes/vendor/<key>``, read first; not any approval of any vendor's, D-174) or other vendors are asked:
        each check asks it twice. Each caller gets lists of its own."""
        asked = tuple(keys)
        with self._conn() as conn:
            counts = tuple(tuple(r) for r in conn.execute(
                "SELECT key, value FROM settings WHERE key IN (SELECT 'changes/vendor/' || value FROM json_each(?)) "
                "ORDER BY key", (json.dumps(asked),),
            ))  # fmt: skip
        kept = self.summary_memo("approved_numbers", lambda: self._approved_numbers(list(asked)), topic="",
                                 asked=(asked, counts))  # fmt: skip
        return {key: list(numbers) for key, numbers in kept.items()}

    def _approved_numbers(self, keys: list[str]) -> dict[str, list[tuple[Any, Any]]]:
        with self._conn() as conn:  # (``+status``: by the vendors' index, not every approved invoice's)
            rows = conn.execute(
                f"SELECT vendor_key, json_extract({_DOC}, '$.gst_hst_registration_number'), json_extract({_DOC}, "
                "'$.remit_bank_account') FROM invoices WHERE vendor_key IN (SELECT value FROM json_each(?)) AND "
                "+status = 'approved' ORDER BY id DESC", (json.dumps(keys),)
            ).fetchall()  # fmt: skip
        out: dict[str, dict[tuple[Any, Any], None]] = {}
        for key, gst, bank in rows:
            out.setdefault(key, {})[(gst or "", bank or "")] = None
        return {key: list(numbers) for key, numbers in out.items()}

    def _vendor_invoices(self, key: str, ids: list[int] | None = None, *,
                         conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:  # fmt: skip
        """``vendor_invoices`` read from the invoices; with ``ids``, only those of them still this vendor's (on
        ``conn``, when given)."""
        if conn is None:
            with self._conn() as own:
                return _vendor_invoice_rows(own, key, ids)
        return _vendor_invoice_rows(conn, key, ids)

    def vendor_invoice_dates(self) -> list[dict[str, Any]]:
        """Vendor, date and total of every invoice in review or approved (for ``recurring.detect``), read for the work
        and kept nowhere: what is kept is its answer, once for every process (``recurring.detected``, D-175), not these
        rows in each worker that worked it out (about 140 MB at 300,000 invoices, B-1803)."""
        return self._vendor_invoice_dates()

    def _vendor_invoice_dates(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            self._summary_fresh(conn)
            return fetch_rows(
                conn,
                "SELECT vendor_key, vendor_name, invoice_date, grand_total, currency FROM invoice_summary "
                "WHERE vendor_key != '' AND +status IN " + _ACTIVE_IN + " ORDER BY id",  # as the invoices are kept
                ACTIVE_STATUSES,
            )

    def _vendor_totals(self) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]]]:
        """(each vendor's invoice counts and dates, {vendor: {currency: approved spend}}): the invoices' part of
        ``vendor_summaries``, from the invoices' summary."""
        with self._conn() as conn:
            self._summary_fresh(conn)
            rows = fetch_rows(
                conn,
                """SELECT vendor_key, MAX(vendor_name) vendor_name, COUNT(*) invoices,
                              SUM(status = 'approved') approved, SUM(status = 'review') to_review,
                              SUM(CASE WHEN status = 'approved' AND COALESCE(NULLIF(currency, ''), 'CAD') = 'CAD'
                                       THEN grand_total ELSE 0 END) spend_cad,
                              MIN(invoice_date) first_invoice, MAX(invoice_date) last_invoice,
                              MIN(created_at) first_seen
                       FROM invoice_summary WHERE vendor_key != '' AND status != 'failed' GROUP BY vendor_key""",
            )
            spend: dict[str, dict[str, float]] = {}
            for r in fetch_rows(
                conn,
                """SELECT vendor_key, COALESCE(NULLIF(UPPER(TRIM(currency)), ''), 'CAD') currency,
                          SUM(grand_total) total
                   FROM invoice_summary WHERE vendor_key != '' AND status = 'approved' GROUP BY vendor_key, 2""",
            ):
                spend.setdefault(r["vendor_key"], {})[r["currency"]] = round(r["total"] or 0, 2)
        return rows, spend

    def has_other_vendors(self, key: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM invoices WHERE vendor_key != ? AND vendor_key != '' AND status != ? LIMIT 1",
                (key, FAILED),
            ).fetchone()
        return row is not None

    def vendor_summaries(self, settled: bool = False) -> list[dict[str, Any]]:
        """One row per vendor seen on an invoice: counts, spend and dates (read again once an invoice changed), AI
        accuracy and AP's settings. ``settled``: for the Vendors page, which only shows them, the counts, spend and
        dates up to ``VENDORS_SETTLE`` seconds (or ten times their work) behind a change (D-197), one answer for every
        process, on the company server worked out again by its background process (D-198); the vendor list's own
        fields are read afresh either way."""
        if settled and self.background_answers:
            totals, spend = self.shared_answer("vendor_totals", self._vendor_totals, version=TOTALS_FORMAT,
                                               decode=tuple, settle=VENDORS_SETTLE, again=[])  # fmt: skip
        else:  # (a laptop's, settled too, kept in the process: written nowhere, B-2048)
            totals, spend = self.summary_memo("vendor_totals", self._vendor_totals,
                                              settle=VENDORS_SETTLE if settled else 0.0)  # fmt: skip
        rows = [dict(r) for r in totals]
        lessons = self.summary_memo("vendor_lessons", self._vendor_lessons, topic="", changes="lessons")  # (D-162)
        with self._conn() as conn:  # of the vendor list, only these vendors' rows (not all the vendor master's)
            listed = (json.dumps([r["vendor_key"] for r in rows]),)
            masters = {r["vendor_key"]: dict(r) for r in conn.execute(
                f"SELECT * FROM vendors WHERE {_in_list('vendor_key')}", listed)}  # fmt: skip
        rates = self.fx_rates()
        for r in rows:
            # Per currency: dollars and euros do not add up, and a USD-only vendor did not spend 0.00 CAD.
            r["spend"] = dict(sorted(spend.get(r["vendor_key"], {}).items()))
            # In CAD at the Settings rates (for sorting; a currency without a rate counts at face value).
            r["spend_in_cad"] = round(sum(t * rates.get(c, 1.0) for c, t in r["spend"].items()), 2)
            fb = lessons.get(r["vendor_key"]) or {}
            r["lessons"] = fb.get("lessons", 0)
            r["accuracy"] = (fb["accepted"] / fb["lines"]) if fb.get("lines") else None
            master = masters.get(r["vendor_key"]) or {}
            r["status"] = master.get("status", "active")
            r["expected_gst"] = master.get("expected_gst", "")
            for field in ("qst_number", "pst_number", "province", "erp_hold"):  # erp_hold: JD Edwards' own hold
                r[field] = master.get(field) or ""
            r["notes"] = master.get("notes", "")
            for field in ("erp_id", "terms", "default_gl"):
                r[field] = master.get(field) or ""
            r["in_master"] = master.get("in_master") == 1
            r["added_here"] = master.get("in_master") == 2
            r["has_record"] = bool(master)  # in the vendor list, added here or set up by AP (on hold, notes)
        return sorted(rows, key=lambda r: -r["spend_in_cad"])

    def _vendor_lessons(self) -> dict[str, dict[str, Any]]:
        """{vendor: its lessons, the lines AP reviewed and those it confirmed}: ``vendor_summaries``' part of the
        coding memory, kept until a lesson changes (``changes/lessons``)."""
        with self._conn() as conn:
            return {
                r["vendor_key"]: dict(r)
                for r in conn.execute(
                    """SELECT vendor_key, COUNT(*) lessons, SUM(outcome NOT IN ('history', 'coded', 'bulk')) lines,
                              SUM(outcome = 'accepted') accepted FROM feedback GROUP BY vendor_key"""
                )
            }

    def vendor_name_checks(self, ids: list[int]) -> dict[int, str]:
        """{invoice id: how sure the capture is of its vendor name} ("verified", "likely", "check", "missing"), in
        one query; an invoice with no capture is left out (the review queue's "check" mark, B-471)."""
        if not ids:
            return {}
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT invoice_id, {capture_lookup('$.fields.vendor_name.status', 'name_status')} "
                f"FROM invoice_capture WHERE {_in_list('invoice_id')}",
                (json.dumps([int(i) for i in ids]),),
            ).fetchall()
        return {int(r[0]): str(r[1] or "") for r in rows}

    def vendor_gl_usage(self, key: str) -> list[dict[str, Any]]:
        """GL accounts reviewers used for this vendor's lines, most used first."""
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """SELECT final_gl gl_code, COUNT(*) lines, SUM(outcome = 'corrected') corrected FROM feedback
                       WHERE vendor_key = ? GROUP BY final_gl ORDER BY lines DESC""",
                    (key,),
                )
            ]

    @staticmethod
    def _merge_vendor_rows(conn: sqlite3.Connection, old: str, key: str) -> None:
        """The vendor list row under ``old`` folded into the one under ``key`` (the same vendor under today's rules),
        then removed: on hold if either is (a row left under a key no invoice gives any more would quietly lift the
        hold), both rows' notes kept, a field blank in one filled from the other."""
        kept = conn.execute("SELECT * FROM vendors WHERE vendor_key = ?", (key,)).fetchone()
        gone = conn.execute("SELECT * FROM vendors WHERE vendor_key = ?", (old,)).fetchone()
        if kept is None or gone is None:
            return
        values: dict[str, Any] = {}
        for column in kept.keys():
            if column in ("vendor_key", "updated_at"):
                continue
            a, b = kept[column], gone[column]
            if column == "status":
                values[column] = "on_hold" if "on_hold" in (a, b) else a
            elif column == "notes":
                values[column] = "; ".join(dict.fromkeys(n for n in (a, b) if n))
            elif column == "in_master":
                values[column] = max(a or 0, b or 0)
            else:
                values[column] = a if a not in (None, "") else b
        assignments = ", ".join(f"{column} = ?" for column in values)
        conn.execute(f"UPDATE vendors SET {assignments}, updated_at = ? WHERE vendor_key = ?",
                     (*values.values(), _now(), key))  # fmt: skip
        conn.execute("DELETE FROM vendors WHERE vendor_key = ?", (old,))
        from .accounts import count_masters_change

        count_masters_change(conn)
