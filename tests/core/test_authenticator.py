"""Authenticator codes (core/authenticator.py, D-146): RFC 6238's codes, the drift allowed, the secret's sealing with
the server's key file and the otpauth:// address."""

from __future__ import annotations

import base64
import re
import sys

import pytest

from ap_coder.core import authenticator

# RFC 6238, Appendix B: the time, then the 8-digit code with SHA-1, SHA-256 and SHA-512 (each its own seed).
SEEDS = {"sha1": b"12345678901234567890", "sha256": b"12345678901234567890123456789012",
         "sha512": b"1234567890123456789012345678901234567890123456789012345678901234"}  # fmt: skip
VECTORS = [
    (59, "94287082", "46119246", "90693936"),
    (1111111109, "07081804", "68084774", "25091201"),
    (1111111111, "14050471", "67062674", "99943326"),
    (1234567890, "89005924", "91819424", "93441116"),
    (2000000000, "69279037", "90698825", "38618901"),
    (20000000000, "65353130", "77737706", "47863826"),
]


@pytest.mark.parametrize("when, sha1, sha256, sha512", VECTORS)
def test_the_codes_are_rfc_6238s_test_vectors(when, sha1, sha256, sha512):
    for algorithm, expected in (("sha1", sha1), ("sha256", sha256), ("sha512", sha512)):
        assert authenticator.totp(SEEDS[algorithm], when, digits=8, algorithm=algorithm) == expected


def test_an_app_gets_six_digits_of_sha1_every_30_seconds_from_a_base32_secret():
    secret = base64.b32encode(SEEDS["sha1"]).decode()
    assert authenticator.code(secret, 59) == "287082"  # the last six digits of the RFC's 94287082
    assert authenticator.code(secret.lower(), 59) == authenticator.code(" ".join(re.findall("....", secret)), 59)
    assert authenticator.code(secret, 60) != authenticator.code(secret, 59)  # a new step


def test_a_new_secret_is_160_random_bits_in_base32():
    first, second = authenticator.new_secret(), authenticator.new_secret()
    assert first != second and re.fullmatch(r"[A-Z2-7]{32}", first)
    assert len(base64.b32decode(first)) == 20


def test_one_step_of_drift_either_way_is_accepted_two_are_not():
    secret, now = authenticator.new_secret(), 1_800_000_015.0
    step = int(now // 30)
    for drift in (-1, 0, 1):
        assert authenticator.matching_step(secret, authenticator.code(secret, now + 30 * drift), now) == step + drift
    for drift in (-2, 2):
        assert authenticator.matching_step(secret, authenticator.code(secret, now + 30 * drift), now) is None


def test_a_step_already_used_is_refused_and_so_is_an_earlier_one():
    secret, now = authenticator.new_secret(), 1_800_000_015.0
    step = int(now // 30)
    typed = authenticator.code(secret, now)
    assert authenticator.matching_step(secret, typed, now, after=step - 1) == step
    assert authenticator.matching_step(secret, typed, now, after=step) is None  # replayed
    assert authenticator.matching_step(secret, authenticator.code(secret, now - 30), now, after=step) is None


@pytest.mark.parametrize("typed", ["", "12345", "1234567", "abcdef", "12 34 5x"])
def test_what_is_not_six_digits_never_matches(typed):
    assert authenticator.matching_step(authenticator.new_secret(), typed, 1_800_000_015.0) is None


def test_spaces_typed_in_a_code_are_ignored():
    secret, now = authenticator.new_secret(), 1_800_000_015.0
    typed = authenticator.code(secret, now)
    assert authenticator.matching_step(secret, f" {typed[:3]} {typed[3:]} ", now) == int(now // 30)


def test_the_key_file_is_made_once_and_read_back(tmp_path):
    assert authenticator.load_key(tmp_path) is None  # not made by reading
    key = authenticator.load_key(tmp_path, create=True)
    assert len(key) == 32 and authenticator.load_key(tmp_path) == key
    assert authenticator.load_key(tmp_path, create=True) == key  # never made again over it
    if sys.platform != "win32":
        assert (tmp_path / authenticator.KEY_FILE).stat().st_mode & 0o077 == 0  # this account's alone


def test_a_damaged_key_file_is_no_key(tmp_path):
    (tmp_path / authenticator.KEY_FILE).write_text("not a key\n", encoding="utf-8")
    assert authenticator.load_key(tmp_path) is None


def test_a_sealed_secret_opens_only_with_its_key_and_purpose():
    key, other = b"k" * 32, b"o" * 32
    sealed = authenticator.seal(key, b"JBSWY3DPEHPK3PXP", b"authenticator")
    assert b"JBSWY3DPEHPK3PXP" not in sealed
    assert authenticator.unseal(key, sealed, b"authenticator") == b"JBSWY3DPEHPK3PXP"
    assert authenticator.unseal(other, sealed, b"authenticator") is None
    assert authenticator.unseal(key, sealed, b"temporary-password") is None
    assert authenticator.unseal(key, sealed[:-1] + bytes([sealed[-1] ^ 1]), b"authenticator") is None  # changed
    assert authenticator.unseal(key, b"short", b"authenticator") is None


def test_the_secret_as_kept_in_the_database_hides_it():
    key, secret = b"k" * 32, authenticator.new_secret()
    kept = authenticator.seal_secret(key, secret)
    assert secret not in kept and authenticator.open_secret(key, kept) == secret
    assert authenticator.open_secret(b"o" * 32, kept) is None and authenticator.open_secret(key, "") is None


def test_the_otpauth_address_names_ap_coder_and_the_username():
    uri = authenticator.otpauth_uri("ann.lee@north", "JBSWY3DPEHPK3PXP")
    assert uri == "otpauth://totp/AP%20Coder:ann.lee%40north?secret=JBSWY3DPEHPK3PXP&issuer=AP%20Coder"
