"""Outlook's .msg emails: what classic desktop Outlook saves when an email is dragged to a folder or *Save As*'d.

A .msg is a Compound File (MS-CFB: a small file system inside one file) holding the email's properties as
streams (MS-OXMSG). This module reads one with the standard library only, so the offline bundle needs no new
package, and returns the parts AP Coder uses: subject, sender, body text and the attachments (an email attached
to the email, e.g. a forwarded one, is opened too).

Every size, chain and nesting depth is bounded: a damaged or hostile file raises ``MsgError`` with the reason
instead of looping or filling the memory.
"""

from __future__ import annotations

import html
import re
import struct
from dataclasses import dataclass, field

SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_FREE, _END, _FAT, _DIFAT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC
_NOSTREAM = 0xFFFFFFFF
_STORAGE, _STREAM, _ROOT = 1, 2, 5
MAX_ENTRIES = 50_000  # directory entries read from one file
MAX_DEPTH = 20  # emails attached to emails attached to emails ...
MAX_ATTACHMENTS = 500

# Property tags (MS-OXPROPS): the stream "__substg1.0_<tag><type>" holds the value.
SUBJECT, BODY, HTML_BODY = "0037", "1000", "1013"
SENDER_NAME, SENDER_EMAIL, SENDER_SMTP, SENT_REPRESENTING = "0C1A", "0C1F", "5D01", "0065"
ATTACH_DATA, ATTACH_NAME, ATTACH_LONG_NAME, ATTACH_MIME, ATTACH_CONTENT_ID = "3701", "3704", "3707", "370E", "3712"
ATTACH_HIDDEN = 0x7FFE000B  # PidTagAttachmentHidden (a boolean): Outlook hides the pictures shown in the text
_ATTACH_PREFIX, _RECIP_PREFIX = "__attach_version1.0_", "__recip_version1.0_"


class MsgError(ValueError):
    """A .msg AP Coder will not open; the message says why in plain words."""


@dataclass
class _Entry:
    name: str
    kind: int
    left: int
    right: int
    child: int
    start: int
    size: int
    children: dict[str, int] = field(default_factory=dict)


class CompoundFile:
    """The streams and storages of a Compound File (version 3 or 4), read from bytes."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 512 or data[:8] != SIGNATURE:
            raise MsgError("it is not an Outlook email (.msg) file")
        self.data = data
        major = struct.unpack_from("<H", data, 0x1A)[0]
        shift = struct.unpack_from("<H", data, 0x1E)[0]
        if (major, shift) not in ((3, 9), (4, 12)):
            raise MsgError("its file format version is not one Outlook writes")
        self.sector = 1 << shift
        self.mini_sector = 1 << struct.unpack_from("<H", data, 0x20)[0]
        n_fat, first_dir = struct.unpack_from("<II", data, 0x2C)
        self.cutoff, first_mini_fat, n_mini_fat, first_difat, n_difat = struct.unpack_from("<IIIII", data, 0x38)
        self.n_sectors = max(0, (len(data) - self.sector) // self.sector)
        fat_sectors = [s for s in struct.unpack_from("<109I", data, 0x4C) if s not in (_FREE, _END)]
        s, seen = first_difat, 0
        per = self.sector // 4 - 1
        while s not in (_FREE, _END) and seen < n_difat + 1 and len(fat_sectors) < self.n_sectors:
            block = self._raw_sector(s)
            fat_sectors += [x for x in struct.unpack_from(f"<{per}I", block) if x not in (_FREE, _END)]
            s = struct.unpack_from("<I", block, per * 4)[0]
            seen += 1
        fat_sectors = fat_sectors[: max(n_fat, 0) or len(fat_sectors)]
        self.fat: list[int] = []
        for fs in fat_sectors:
            self.fat += struct.unpack_from(f"<{self.sector // 4}I", self._raw_sector(fs))
        directory = self._chain_bytes(first_dir)
        self.entries: list[_Entry] = []
        for off in range(0, min(len(directory), MAX_ENTRIES * 128), 128):
            raw = directory[off : off + 128]
            if len(raw) < 128:
                break
            name_len = min(struct.unpack_from("<H", raw, 64)[0], 64)
            name = raw[: max(name_len - 2, 0)].decode("utf-16-le", "replace")
            kind = raw[66]
            left, right, child = struct.unpack_from("<III", raw, 68)
            start = struct.unpack_from("<I", raw, 116)[0]
            size = struct.unpack_from("<Q", raw, 120)[0]
            if major == 3:
                size &= 0xFFFFFFFF
            self.entries.append(_Entry(name, kind, left, right, child, start, size))
        if not self.entries or self.entries[0].kind != _ROOT:
            raise MsgError("it is damaged (no root entry)")
        root = self.entries[0]
        self.mini_stream = self._chain_bytes(root.start, root.size) if root.size else b""
        self.mini_fat: list[int] = []
        if n_mini_fat and first_mini_fat not in (_FREE, _END):
            raw = self._chain_bytes(first_mini_fat)
            self.mini_fat = list(struct.unpack_from(f"<{len(raw) // 4}I", raw))
        self.read = 0  # bytes of streams read so far: never much more than the file holds
        # The storages are walked as a tree from the root, so the order of the entries in the directory does not
        # matter. Each entry belongs to one storage: a crafted file sharing children walks them once.
        walked: set[int] = {0}
        storages = [0]
        while storages:
            e = self.entries[storages.pop()]
            e.children = self._children(e.child, walked)
            storages += [i for i in e.children.values() if self.entries[i].kind == _STORAGE]

    def _raw_sector(self, n: int) -> bytes:
        if n >= self.n_sectors:
            raise MsgError("it is damaged (a sector past the end of the file)")
        off = (n + 1) * self.sector
        return self.data[off : off + self.sector]

    def _chain_bytes(self, start: int, size: int | None = None) -> bytes:
        out, s, steps = bytearray(), start, 0
        while s not in (_END, _FREE):
            if steps > self.n_sectors or s >= len(self.fat):
                raise MsgError("it is damaged (a broken sector chain)")
            out += self._raw_sector(s)
            s = self.fat[s]
            steps += 1
            if size is not None and len(out) >= size:
                break
        return bytes(out[:size] if size is not None else out)

    def _mini_bytes(self, start: int, size: int) -> bytes:
        out, s, steps = bytearray(), start, 0
        limit = len(self.mini_stream) // self.mini_sector + 1
        while s not in (_END, _FREE) and len(out) < size:
            if steps > limit or s >= len(self.mini_fat):
                raise MsgError("it is damaged (a broken mini-sector chain)")
            off = s * self.mini_sector
            out += self.mini_stream[off : off + self.mini_sector]
            s = self.mini_fat[s]
            steps += 1
        return bytes(out[:size])

    def _children(self, first: int, seen: set[int]) -> dict[str, int]:
        """The entries of one storage: its child and that child's siblings, a tree walked without recursion. ``seen``
        is shared by every storage, so an entry another storage (or this one) already holds is not walked again."""
        found: dict[str, int] = {}
        stack = [first]
        while stack:
            i = stack.pop()
            if i == _NOSTREAM or i in seen or i >= len(self.entries):
                continue
            seen.add(i)
            e = self.entries[i]
            found[e.name.upper()] = i
            stack += [e.left, e.right]
        return found

    def stream(self, i: int) -> bytes:
        e = self.entries[i]
        if e.kind != _STREAM:
            return b""
        if e.size > len(self.data) * 2 + 4096:
            raise MsgError("it is damaged (a stream larger than the file)")
        self.read += e.size  # many entries can point at the same data: the sum is bounded, not only each one
        if self.read > len(self.data) * 2 + 65536:
            raise MsgError("it is damaged (its streams add up to far more than the file)")
        if e.size < self.cutoff:
            return self._mini_bytes(e.start, e.size)
        return self._chain_bytes(e.start, e.size)


@dataclass
class MsgAttachment:
    name: str
    content_type: str
    content_id: str
    data: bytes
    inline: bool = False  # a picture shown in the email's text (hidden, or its Content-ID referenced from the HTML)


@dataclass
class MsgEmail:
    subject: str = ""
    sender: str = ""
    body: str = ""
    attachments: list[MsgAttachment] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _text(cf: CompoundFile, storage: int, tag: str) -> str:
    kids = cf.entries[storage].children
    for suffix, codec in (("001F", "utf-16-le"), ("001E", "cp1252")):
        i = kids.get(f"__SUBSTG1.0_{tag}{suffix}")
        if i is not None:
            return cf.stream(i).decode(codec, "replace").rstrip("\x00")
    return ""


def _html(cf: CompoundFile, storage: int) -> str:
    """The HTML body of the message in ``storage`` ("" when it has none)."""
    kids = cf.entries[storage].children
    i = kids.get(f"__SUBSTG1.0_{HTML_BODY}0102")
    if i is not None:
        return cf.stream(i).decode("utf-8", "replace")
    return _text(cf, storage, HTML_BODY)


def _hidden(cf: CompoundFile, storage: int) -> bool:
    """PidTagAttachmentHidden of the attachment in ``storage``: its fixed-size properties stream holds 8 bytes of
    header, then 16 bytes per property (tag, flags, value)."""
    i = cf.entries[storage].children.get("__PROPERTIES_VERSION1.0")
    raw = cf.stream(i) if i is not None else b""
    for off in range(8, len(raw) - 15, 16):
        if struct.unpack_from("<I", raw, off)[0] == ATTACH_HIDDEN:
            return struct.unpack_from("<H", raw, off + 8)[0] != 0
    return False


def html_to_text(value: str) -> str:
    """Readable text from an email's HTML body: scripts and styles dropped, tags removed, entities decoded."""
    value = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", value)
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    return re.sub(r"[ \t\r\f\v]+", " ", value).strip()


def _message(cf: CompoundFile, storage: int, out: MsgEmail, depth: int) -> None:
    """Add the attachments of the message in ``storage`` (and of messages attached to it) to ``out``."""
    stack = [(storage, depth)]
    while stack:
        node, level = stack.pop()
        html_body: str | None = None  # read once, only when an attachment has a Content-ID
        for key, i in sorted(cf.entries[node].children.items()):
            if not key.startswith(_ATTACH_PREFIX.upper()) or cf.entries[i].kind != _STORAGE:
                continue
            if len(out.attachments) >= MAX_ATTACHMENTS:
                out.skipped.append(f"everything after the first {MAX_ATTACHMENTS} attachments (not opened)")
                return
            kids = cf.entries[i].children
            name = _text(cf, i, ATTACH_LONG_NAME) or _text(cf, i, ATTACH_NAME)
            embedded = kids.get(f"__SUBSTG1.0_{ATTACH_DATA}000D")
            if embedded is not None and cf.entries[embedded].kind == _STORAGE:
                if level + 1 > MAX_DEPTH:
                    out.skipped.append(f"emails attached more than {MAX_DEPTH} levels deep (not opened)")
                    continue
                inner = _text(cf, embedded, BODY)
                if inner and len(out.body) < 200_000:
                    out.body += "\n\n" + inner
                stack.append((embedded, level + 1))
                continue
            data_i = kids.get(f"__SUBSTG1.0_{ATTACH_DATA}0102")
            if data_i is None:
                out.skipped.append(f"{name or 'an attachment'} (a link or an object, not a file)")
                continue
            # Outlook keeps the Content-ID of every attachment from Gmail, so it alone does not make a picture inline:
            # it must also be hidden or shown in the HTML text (the .eml rule: a Content-ID, not an attachment).
            content_id = _text(cf, i, ATTACH_CONTENT_ID).strip().strip("<>")
            inline = False
            if content_id:
                if html_body is None:
                    html_body = _html(cf, node)
                inline = _hidden(cf, i) or f"cid:{content_id}".lower() in html_body.lower()
            out.attachments.append(MsgAttachment(name, _text(cf, i, ATTACH_MIME).lower(), content_id,
                                                 cf.stream(data_i), inline))  # fmt: skip


def read_msg(data: bytes) -> MsgEmail:
    """Subject, sender, body text and attachments of an Outlook .msg email."""
    try:
        cf = CompoundFile(data)
    except MsgError:
        raise
    except (struct.error, IndexError, OverflowError, MemoryError) as exc:
        raise MsgError(f"it is damaged ({type(exc).__name__})") from None
    out = MsgEmail()
    try:
        out.subject = _text(cf, 0, SUBJECT)
        name = _text(cf, 0, SENDER_NAME)
        address = _text(cf, 0, SENDER_SMTP) or _text(cf, 0, SENDER_EMAIL)
        out.sender = f"{name} <{address}>" if name and address else name or address
        out.body = _text(cf, 0, BODY)
        if not out.body:
            i = cf.entries[0].children.get(f"__SUBSTG1.0_{HTML_BODY}0102")
            if i is not None:
                out.body = html_to_text(cf.stream(i).decode("utf-8", "replace"))
        _message(cf, 0, out, 0)
    except MsgError:
        raise
    except (struct.error, IndexError, OverflowError, MemoryError) as exc:
        raise MsgError(f"it is damaged ({type(exc).__name__})") from None
    return out
