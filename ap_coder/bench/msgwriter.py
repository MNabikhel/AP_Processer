"""Write Outlook .msg emails (a Compound File, MS-CFB version 3, holding MS-OXMSG properties), for the tests and
the email benchmark: AP Coder only reads .msg files, but made-up ones are needed to test that reading.

The layout is the plain one Outlook's own files use: streams under 4,096 bytes in the mini stream, larger ones in
whole 512-byte sectors, strings as UTF-16 (``001F``), attachments in ``__attach_version1.0_#NNNNNNNN`` storages
and an attached email as an embedded message (``__substg1.0_3701000D``).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..intake.msgfile import SIGNATURE

_FREE, _END, _FAT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
SECTOR, MINI, CUTOFF = 512, 64, 4096


@dataclass
class _Node:
    name: str
    data: bytes | None = None  # a stream; None: a storage
    children: list[_Node] = field(default_factory=list)


@dataclass
class Attachment:
    name: str
    data: bytes
    mime: str = "application/pdf"
    content_id: str = ""
    hidden: bool = False  # PidTagAttachmentHidden, as Outlook sets it on the pictures shown in the email's text


@dataclass
class Message:
    subject: str = ""
    sender_name: str = ""
    sender_email: str = ""
    body: str = ""
    html: str = ""
    rtf: str = ""  # a rich-text body, written compressed (PidTagRtfCompressed)
    attachments: list[Attachment] = field(default_factory=list)
    attached_emails: list[Message] = field(default_factory=list)


def _compressed_rtf(raw: bytes) -> bytes:
    """``raw`` as compressed RTF (MS-OXRTFCP, "LZFu"), every byte a literal, then the end mark (no CRC checked)."""
    pos, out = (207 + len(raw)) % 4096, bytearray()
    groups = [raw[i : i + 8] for i in range(0, len(raw), 8)]
    if not groups or len(groups[-1]) == 8:
        groups.append(b"")
    for n, group in enumerate(groups):
        last = n == len(groups) - 1
        out.append(1 << len(group) if last else 0)
        out += group
        if last:
            out += struct.pack(">H", pos << 4)
    return struct.pack("<IIII", len(out) + 12, len(raw), 0x75465A4C, 0) + bytes(out)


def _text(tag: str, value: str) -> _Node:
    return _Node(f"__substg1.0_{tag}001F", value.encode("utf-16-le"))  # no terminating null, as Outlook writes it


def _props(n: int, top: bool) -> _Node:
    return _Node("__properties_version1.0", b"\x00" * (32 if top else 24 if n else 8))


def _message_nodes(msg: Message, top: bool) -> list[_Node]:
    nodes = [_props(1, top), _text("001A", "IPM.Note")]  # the message class: an email
    if top:  # the named-property mapping every .msg carries (empty: no named properties)
        nodes.append(_Node("__nameid_version1.0", children=[_Node(f"__substg1.0_000{n}0102", b"") for n in (2, 3, 4)]))
    for tag, value in (
        ("0037", msg.subject),
        ("0C1A", msg.sender_name),
        ("0C1F", msg.sender_email),
        ("5D01", msg.sender_email),
        ("1000", msg.body),
    ):
        if value:
            nodes.append(_text(tag, value))
    if msg.html:
        nodes.append(_Node("__substg1.0_10130102", msg.html.encode()))
    if msg.rtf:
        nodes.append(_Node("__substg1.0_10090102", _compressed_rtf(msg.rtf.encode("latin-1"))))
    n = 0
    for a in msg.attachments:
        props = _props(0, False)
        if a.hidden:  # one fixed-size property: tag, flags (readable, writable), value
            props = _Node(props.name, (props.data or b"") + struct.pack("<IIQ", 0x7FFE000B, 6, 1))
        kids = [props, _Node("__substg1.0_37010102", a.data), _text("3707", a.name), _text("3704", a.name)]
        if a.mime:
            kids.append(_text("370E", a.mime))
        if a.content_id:
            kids.append(_text("3712", a.content_id))
        nodes.append(_Node(f"__attach_version1.0_#{n:08X}", children=kids))
        n += 1
    for inner in msg.attached_emails:
        embedded = _Node("__substg1.0_3701000D", children=_message_nodes(inner, False))
        nodes.append(
            _Node(
                f"__attach_version1.0_#{n:08X}",
                children=[_props(0, False), embedded, _text("3707", inner.subject or "email")],
            )
        )
        n += 1
    return nodes


def _key(node: _Node) -> tuple[int, str]:
    return len(node.name), node.name.upper()  # the order MS-CFB keeps siblings in


def compound_file(children: list[_Node]) -> bytes:
    """A Compound File (version 3) whose root storage holds ``children``."""
    entries: list[dict] = []
    mini = bytearray()
    big: list[tuple[int, bytes]] = []  # (entry index, data) of streams of at least CUTOFF bytes

    def add(node: _Node) -> int:
        i = len(entries)
        entry = {
            "name": node.name,
            "type": 2 if node.data is not None else 1,
            "left": _FREE,
            "right": _FREE,
            "child": _FREE,
            "start": _END,
            "size": 0,
        }
        entries.append(entry)
        if node.data is not None:
            entry["size"] = len(node.data)
            if 0 < len(node.data) < CUTOFF:
                entry["start"] = len(mini) // MINI
                mini.extend(node.data + b"\x00" * (-len(node.data) % MINI))
            elif node.data:
                big.append((i, node.data))
        else:
            entry["child"] = tree(node.children)
        return i

    def tree(kids: list[_Node]) -> int:
        """A balanced binary tree of siblings (sorted the MS-CFB way); its top entry's index."""
        kids = sorted(kids, key=_key)
        if not kids:
            return _FREE
        mid = len(kids) // 2
        i = add(kids[mid])
        entries[i]["left"] = tree(kids[:mid])
        entries[i]["right"] = tree(kids[mid + 1 :])
        return i

    entries.append(
        {"name": "Root Entry", "type": 5, "left": _FREE, "right": _FREE, "child": _FREE, "start": _END, "size": 0}
    )
    entries[0]["child"] = tree(children)
    sectors = lambda n: -(-n // SECTOR)  # noqa: E731
    fat: list[int] = []

    def chain(n: int) -> int:
        if not n:
            return _END
        start = len(fat)
        fat.extend(range(start + 1, start + n))
        fat.append(_END)
        return start

    body = bytearray()
    if mini:
        entries[0]["start"] = chain(sectors(len(mini)))
        entries[0]["size"] = len(mini)
        body += mini + b"\x00" * (-len(mini) % SECTOR)
    for i, data in big:
        entries[i]["start"] = chain(sectors(len(data)))
        body += data + b"\x00" * (-len(data) % SECTOR)
    n_mini = len(mini) // MINI
    mini_fat = [*range(1, n_mini), _END] if n_mini else []
    for e in entries:  # each small stream is one run in the mini stream: chain its mini sectors
        if e["type"] == 2 and 0 < e["size"] < CUTOFF:
            last = e["start"] + -(-e["size"] // MINI) - 1
            mini_fat[last] = _END
    mf_bytes = struct.pack(f"<{len(mini_fat)}I", *mini_fat) if mini_fat else b""
    first_mini_fat = chain(sectors(len(mf_bytes))) if mf_bytes else _END
    body += mf_bytes + b"\x00" * (-len(mf_bytes) % SECTOR)
    directory = bytearray()
    for e in entries:
        name = e["name"].encode("utf-16-le") + b"\x00\x00"
        directory += name.ljust(64, b"\x00") + struct.pack(
            "<HBB3I", len(name), e["type"], 1, e["left"], e["right"], e["child"]
        )
        directory += b"\x00" * 16 + struct.pack("<I", 0) + b"\x00" * 16 + struct.pack("<IQ", e["start"], e["size"])
    while len(directory) % SECTOR:  # unused entries: no name, no siblings
        directory += b"\x00" * 64 + struct.pack("<HBB3I", 0, 0, 0, _FREE, _FREE, _FREE) + b"\x00" * 48
    first_dir = chain(sectors(len(directory)))
    body += directory
    n_fat = 1
    while (len(fat) + n_fat) > n_fat * (SECTOR // 4):
        n_fat += 1
    if n_fat > 109:
        raise ValueError("email too large for this writer")
    fat_start = len(fat)
    fat.extend([_FAT] * n_fat)
    fat.extend([_FREE] * (n_fat * (SECTOR // 4) - len(fat)))
    body += struct.pack(f"<{len(fat)}I", *fat)
    difat = list(range(fat_start, fat_start + n_fat)) + [_FREE] * (109 - n_fat)
    header = SIGNATURE + b"\x00" * 16 + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\x00" * 6
    header += struct.pack(
        "<IIIIIIIII", 0, n_fat, first_dir, 0, CUTOFF, first_mini_fat, -(-len(mf_bytes) // SECTOR), _END, 0
    )
    header += struct.pack("<109I", *difat)
    return bytes(header + body)


def build_msg(msg: Message) -> bytes:
    """The bytes of an Outlook .msg file holding ``msg``."""
    return compound_file(_message_nodes(msg, True))
