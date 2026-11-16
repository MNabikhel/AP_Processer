"""The plain-HTTPS Document Intelligence client (ap_coder/ai/di_http.py, D-245), against the stand-in on 127.0.0.1
(tests/fake_di.py) with made-up recorded answers: nothing here reaches Azure."""

import socket

import pytest

from ap_coder.ai import di_http
from tests.fake_di import CA_FILE, ENDPOINT, HOST, KEY, PREFIX, SAMPLES, FakeDi, recorded

STEM = "northwind_ON_HST_NW-2026-0912"


@pytest.fixture
def fake():
    with FakeDi() as fake:
        yield fake


def _client(fake, *, pauses=None, key=KEY, **kw):
    asked = []

    def connect_to(host, port):
        asked.append((host, port))
        return fake.connect_to(host, port)

    client = di_http.DiClient(ENDPOINT, key, ca_file=str(CA_FILE), connect_to=connect_to,
                              sleep=(pauses if pauses is not None else []).append, **kw)  # fmt: skip
    client.asked = asked
    return client


def _pdf(stem=STEM):
    return (SAMPLES / f"{stem}.pdf").read_bytes()


def test_a_reading_is_posted_polled_and_deleted_at_the_pinned_version(fake):
    pauses = []
    client = _client(fake, pauses=pauses)
    raw = client.analyze(_pdf(), pages="1-4,9")
    assert raw == recorded(STEM)
    methods = [(m, path) for m, path, _, _ in fake.calls]
    assert methods[0] == ("POST", f"{PREFIX}:analyze")
    assert [m for m, _ in methods] == ["POST", "GET", "GET", "DELETE"]
    assert all(q.get("api-version") == "2024-11-30" for _, _, q, _ in fake.calls)
    assert fake.calls[0][2]["pages"] == "1-4,9"
    assert all(right for *_, right in fake.calls)  # the key went with every request
    assert client.deleted and fake.deleted and not fake.results  # Azure keeps nothing of it
    assert pauses == [di_http.POLL_EVERY, di_http.POLL_EVERY]  # never asked more often than every 2 seconds
    assert {host for host, _ in client.asked} == {HOST}


def test_the_region_azure_names_is_kept(fake):
    fake.region = "canadacentral"
    client = _client(fake)
    client.model_info()
    assert client.region == "canadacentral"


def test_busy_is_tried_again_after_retry_after_then_given_up(fake):
    fake.script("analyze", 429)
    pauses = []
    assert _client(fake, pauses=pauses).analyze(_pdf()) == recorded(STEM)
    assert pauses[0] == 1.0  # the Retry-After
    fake.script("analyze", 429, 429, 429)
    with pytest.raises(di_http.DiError) as busy:
        _client(fake).analyze(_pdf())
    assert busy.value.kind == di_http.UNREACHABLE and not busy.value.permanent
    assert busy.value.status == 429


def test_a_server_error_is_tried_again_twice_at_two_then_five_seconds(fake):
    fake.script("analyze", 503, 502)
    pauses = []
    assert _client(fake, pauses=pauses).analyze(_pdf()) == recorded(STEM)
    assert pauses[:2] == [2.0, 5.0]
    fake.script("analyze", 503, 503, 503)
    with pytest.raises(di_http.DiError) as down:
        _client(fake).analyze(_pdf())
    assert down.value.kind == di_http.UNREACHABLE and down.value.status == 503


def test_a_refused_key_is_never_tried_again_and_never_quoted(fake):
    with pytest.raises(di_http.DiError) as refused:
        _client(fake, key="WrongKey0123456789abcdefWXYZ").analyze(_pdf())
    assert refused.value.kind == di_http.KEY and refused.value.permanent
    assert "refused the key" in str(refused.value) and "WrongKey" not in str(refused.value)
    assert fake.steps("POST") == [f"{PREFIX}:analyze"] and not fake.steps("GET")


def test_a_wrong_endpoint_path_is_not_found(fake):
    fake.script("model", 404)
    with pytest.raises(di_http.DiError) as missing:
        _client(fake).model_info()
    assert missing.value.kind == di_http.NOT_FOUND and missing.value.permanent


def test_a_failed_reading_is_said_and_still_deleted(fake):
    fake.script("analyze", "failed")
    with pytest.raises(di_http.DiError) as failed:
        _client(fake).analyze(_pdf())
    assert failed.value.kind == di_http.FAILED and "corrupted" in str(failed.value)
    assert fake.deleted


def test_a_result_address_on_another_host_is_never_asked(fake):
    fake.script("analyze", "elsewhere")
    client = _client(fake)
    with pytest.raises(di_http.DiError) as refused:
        client.analyze(_pdf())
    assert refused.value.kind == di_http.REFUSED and "elsewhere.example.com" in str(refused.value)
    assert not fake.steps("GET") and not fake.steps("DELETE")
    assert {host for host, _ in client.asked} == {HOST}  # no socket for the other host


def test_any_other_host_is_refused_before_a_socket_opens():
    with pytest.raises(di_http.DiError) as bad:
        di_http.DiClient("https://example.com/", KEY)
    assert bad.value.kind == di_http.REFUSED
    for endpoint in ("http://ap-test.cognitiveservices.azure.com/", "https://cognitiveservices.azure.com/",
                     "https://user:pw@ap-test.cognitiveservices.azure.com/"):  # fmt: skip
        assert di_http.endpoint_host(endpoint) == ""
    client = di_http.DiClient(ENDPOINT, KEY, connect_to=lambda *a: pytest.fail("no socket may open"))
    with pytest.raises(di_http.DiError, match="only contacts"):
        client._send("GET", "https://evil.cognitiveservices.azure.com/x")


def test_a_reading_that_takes_too_long_stops(fake):
    fake.running_polls = 10_000
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    client = di_http.DiClient(ENDPOINT, KEY, ca_file=str(CA_FILE), connect_to=fake.connect_to, sleep=sleep,
                              clock=lambda: now[0], poll_seconds=10)  # fmt: skip
    with pytest.raises(di_http.DiError) as late:
        client.analyze(_pdf())
    assert late.value.kind == di_http.TIMEOUT
    assert len(fake.steps("GET")) == 5 and fake.deleted  # every 2 s for 10 s, then deleted


def test_an_analyze_that_reached_azure_is_never_sent_twice(fake):
    fake.script("analyze", "drop")
    with pytest.raises(di_http.DiError) as broke:
        _client(fake).analyze(_pdf())
    assert broke.value.kind == di_http.UNREACHABLE
    assert len(fake.steps("POST")) == 1  # it may have been read (and billed): not sent again


def test_no_connection_is_tried_again_then_said():
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    pauses = []
    client = di_http.DiClient(ENDPOINT, KEY, connect_to=lambda host, p: ("127.0.0.1", port), sleep=pauses.append)
    with pytest.raises(di_http.DiError) as down:
        client.analyze(b"%PDF-1.4")
    assert down.value.kind == di_http.UNREACHABLE and HOST in str(down.value)
    assert pauses == [2.0, 5.0]


def test_an_untrusted_certificate_is_refused(fake):
    client = di_http.DiClient(ENDPOINT, KEY, connect_to=fake.connect_to, sleep=lambda s: None)  # no test CA
    with pytest.raises(di_http.DiError) as untrusted:
        client.model_info()
    assert untrusted.value.kind == di_http.REFUSED and "certificate" in str(untrusted.value)
    assert not fake.calls


def test_the_model_answers_without_an_invoice_being_sent(fake):
    info = _client(fake).model_info()
    assert info["modelId"] == "prebuilt-invoice" and fake.steps("GET") == [PREFIX] and not fake.steps("POST")
