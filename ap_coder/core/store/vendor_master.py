"""The vendor master as the ERP gives it: its import (``import_vendor_master``, a file's rows or a page of the JD
Edwards sync), what its rows say (JD Edwards' hold payment codes, ``hold_code``; a name marked as retired,
``name_marker``; a person listed "LAST, FIRST M.", ``person_name``), the vendor a name is when none is kept under it
as written (``_vendor_named_otherwise``, for ``find_vendor``), and what the rest reads of the master: its vendors,
their ERP IDs (``vendor_ids``), the one a GST/HST number belongs to. The vendor list itself (a vendor's own row, its
invoices, the Vendors page) is ``vendors``, which keeps every name of this part importable from there too."""

from __future__ import annotations

import json
import re
from typing import Any

from ..keys import BUSINESS_NUMBER, norm_tax_number, same_tax_number
from ..memory import _LEGAL_SUFFIXES, _normalise, vendor_key
from ._base import _clean_code, _in_list, _now
from .dialect import ROWID, like
from .summary import fetch_rows

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


# A supplier whose name the ERP marks as retired (the words: the setting ``skip_words``, ``Store.skip_words``, whose
# default is ``vendors.SKIP_WORDS``; a star before or after the name marks one too).
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


def _stopped(row: dict[str, Any]) -> bool:
    """Whether the ERP itself stops this supplier of an import: marked as retired, or its hold payment code holds."""
    return bool(row.get("marked") or row.get("hold"))


def _retired_twin(first: dict[str, Any], row: dict[str, Any]) -> bool:
    """Whether ``row`` is a supplier the ERP stops (``_stopped``) by the name of one in use (``first``), under an ERP
    ID of its own: two vendors, the stopped one the second, never merged into the one in use, which would be held for
    it (B-2083)."""
    ident = str(row.get("erp_id") or "").strip()
    return bool(_stopped(row) and not _stopped(first) and ident and ident != str(first.get("erp_id") or "").strip())


class VendorMasterMixin:
    # --- The vendor master's import -----------------------------------------------------------------------

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
        # the suppliers in use first: of two by one name, the one the ERP does not stop is the vendor of the name
        # (B-2083)
        rows = sorted(_master_rows_as_kept(rows, self.hold_codes(), words), key=_stopped)
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

    # --- What the rest reads of the master ----------------------------------------------------------------

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

    # --- The vendor a name is when none is kept under it as written -----------------------------------------

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
