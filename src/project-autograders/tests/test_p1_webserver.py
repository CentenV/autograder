"""Project 1 grader internals: response parsing and port discovery.

End-to-end behaviour needs a built server; see framework/tools/selfcheck.py p1_webserver.
"""

from p1_webserver import harness
from p1_webserver.harness import Response


def test_response_parsing():
    r = Response(raw=b"HTTP/1.0 200 OK\r\nContent-Type: image/jpeg; x=1\r\ncontent-length: 4\r\n\r\n\x00\r\n\xff")
    assert r.status == 200 and r.content_type == "image/jpeg" and r.content_length == 4
    assert r.body == b"\x00\r\n\xff" and r.crlf_header


def test_response_lenient_newlines_and_garbage():
    r = Response(raw=b"HTTP/1.1 404 Not Found\nContent-Type: text/html\n\nnope")
    assert r.status == 404 and r.body == b"nope" and not r.crlf_header
    assert Response(raw=b"hello").status is None
    assert Response(raw=b"HTTP/1.0 200 OK\r\n").head_end == -1


def test_printed_numbers():
    assert harness.printed_numbers("Using port: 1701\nversion 1.2.3 pid 99 port 65535 70000") == [1701, 65535]


def test_decoys_never_match_served_names():
    import re
    allowed = re.compile(r"^(file[0-9]\.html|image[0-9]\.jpg)$")
    assert all(allowed.match(n) for n in harness.SERVED + (harness.GENERATED_IMAGE,))
    assert not any(allowed.match(n) for n in harness.DECOYS_IN_DATA)
    img = harness.generated_image()
    assert b"\0" in img and b"\r\n" in img and img == harness.generated_image()
