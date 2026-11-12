"""Project 1: HTTP server. Black-box tests of the student's ``webServer``.

Each test starts the server in a fresh directory with ``data/`` (file1-3.html,
image1-2.jpg, a generated binary image8.jpg) and decoy files it must refuse,
then sends raw HTTP requests over TCP. 471 = default zip; 560 adds HEAD, POST
and SIGINT tests (``make_zip.sh p1_webserver 560``) and drops "HEAD/POST -> 400".
"""

from __future__ import annotations

import signal
import time

import pytest

from framework.common_tests import test_build  # noqa: F401  (graded build step)

from . import harness
from .harness import Response, Server

HIDDEN = "after_published"
CTYPES = {".html": "text/html", ".jpg": "image/jpeg"}


def _expect(server: Server, name: str) -> bytes:
    return (server.data / name).read_bytes()


def _check_ok(resp: Response, expected: bytes, ctype: str, body: bool = True) -> list[str]:
    """Problems with a 200 response carrying ``expected`` (or just its headers if not ``body``)."""
    if resp.status != 200:
        return [f"Expected status 200, got {resp.status_line or 'no status line'}."]
    problems = []
    if resp.content_length is None:
        problems.append("Missing or malformed Content-Length header.")
    elif resp.content_length != len(expected):
        problems.append(f"Content-Length is {resp.content_length}; the file is {len(expected)} bytes.")
    if resp.content_type != ctype:
        problems.append(f"Content-Type should be {ctype}, got {resp.headers.get('content-type', '(missing)')!r}.")
    if body:
        got = resp.body[:len(expected)]
        if got != expected:
            if len(resp.body) < len(expected):
                problems.append(f"Body is incomplete: got {len(resp.body)} of {len(expected)} bytes.")
            else:
                i = next(i for i, (a, b) in enumerate(zip(got, expected)) if a != b)
                problems.append(f"Body differs from the file starting at byte {i} "
                                f"(sent 0x{got[i]:02x}, file has 0x{expected[i]:02x}).")
    return problems


def _log_exchange(score, request: bytes | str, resp: Response) -> None:
    req = request if isinstance(request, str) else request.decode("latin-1")
    req = req.replace("\r\n", "\\r\\n\n").rstrip("\n")
    score.log(f"Request:\n{req}\n\nResponse:\n{resp.summary()}")


def _fail(problems: list[str], server: Server | None = None) -> None:
    if problems:
        msg = "\n".join(problems)
        if server is not None and not server.bg.alive():
            msg += "\n" + server.describe()
        pytest.fail(msg, pytrace=False)


def _graded_paths(server: Server, score, paths: dict[str, int], accept: tuple[int, ...]) -> None:
    """Request each path; full credit for a status in ``accept``, else 0. ``paths`` maps path -> weight."""
    earned, problems = 0.0, []
    for path, weight in paths.items():
        resp = server.get(path)
        leaked = harness.SECRET in resp.raw
        ok = resp.status in accept and not leaked
        earned += weight * ok
        score.log(f"[{'ok' if ok else 'FAIL'}] GET {path} -> {resp.status_line or resp.summary()}")
        if leaked:
            problems.append(f"GET {path} sent the contents of a file that must not be served.")
        elif not ok:
            problems.append(f"GET {path}: expected {' or '.join(map(str, accept))}, got {resp.status_line or resp.summary()}.")
    score.set_fraction(earned / sum(paths.values()))
    _fail(problems, server)


# ------------------------------------------------------------ basic GET

@pytest.mark.gs(weight=5, number="1.1", visibility="visible",
                name="Starts, listens on a TCP port and prints the port number")
def test_prints_port(server, score):
    score.log(f"Server output:\n{server.output() or '(none)'}")
    if not server.port_printed:
        pytest.fail(f"Your server is listening on port {server.port} but never printed that number. "
                    "Print it (and flush, e.g. with std::endl) so the grader knows where to connect.",
                    pytrace=False)


@pytest.mark.gs(weight=10, number="1.2", visibility="visible",
                name="GET /file1.html returns 200 and the exact file contents")
def test_get_html(server, score):
    resp = server.get("/file1.html")
    _log_exchange(score, "GET /file1.html HTTP/1.0\r\n\r\n", resp)
    expected = _expect(server, "file1.html")
    if resp.status != 200:
        _fail([f"Expected status 200, got {resp.status_line or resp.summary()}."], server)
    body = resp.body[:len(expected)]
    if body != expected:
        _fail([f"The body did not match data/file1.html ({len(resp.body)} bytes received, file is {len(expected)})."])


@pytest.mark.gs(weight=10, number="1.3", visibility="visible",
                name="200 responses have a valid status line, Content-Type and Content-Length")
def test_headers(server, score):
    checks = {}
    for name in ("file2.html", "image1.jpg"):
        resp = server.get(f"/{name}")
        _log_exchange(score, f"GET /{name} HTTP/1.0\r\n\r\n", resp)
        exp = _expect(server, name)
        ctype = CTYPES[name[name.rindex("."):]]
        checks[f"{name}: status line looks like 'HTTP/1.0 200 OK'"] = resp.status == 200
        checks[f"{name}: Content-Length equals the file size ({len(exp)})"] = resp.content_length == len(exp)
        checks[f"{name}: Content-Type is {ctype}"] = resp.content_type == ctype
        checks[f"{name}: header lines end with \\r\\n and a blank line ends the header"] = resp.crlf_header
    score.set_fraction(sum(checks.values()) / len(checks))
    score.log("\n".join(f"[{'ok' if v else 'FAIL'}] {k}" for k, v in checks.items()))
    _fail([f"Failed: {k}" for k, v in checks.items() if not v], server)


@pytest.mark.gs(weight=15, number="1.4", visibility="visible",
                name="GET /image1.jpg returns the exact binary file as image/jpeg")
def test_get_image(server, score):
    resp = server.get("/image1.jpg")
    _log_exchange(score, "GET /image1.jpg HTTP/1.0\r\n\r\n", resp)
    problems = _check_ok(resp, _expect(server, "image1.jpg"), "image/jpeg")
    if problems and resp.status == 200:
        body_ok = resp.body[:resp.content_length or 0] == _expect(server, "image1.jpg")
        score.set_fraction(0.7 if body_ok else 0.2)
    _fail(problems, server)


@pytest.mark.gs(weight=10, number="1.5", visibility="visible",
                name="GET for a well-formed name that doesn't exist (/file7.html, /image5.jpg) returns 404")
def test_404_missing(server, score):
    _graded_paths(server, score, {"/file7.html": 1, "/image5.jpg": 1}, (404,))


@pytest.mark.gs(weight=10, number="1.6", visibility="visible",
                name="Refuses files that don't match fileX.html / imageX.jpg with 404, even if they exist")
def test_404_disallowed(server, score):
    _graded_paths(server, score, {"/badname.html": 1, "/file10.html": 1, "/secret.txt": 1, "/file1.htm": 1},
                  (404,))


@pytest.mark.gs(weight=5, number="1.7", visibility="visible",
                name="Malformed requests and unknown methods return 400")
def test_400_malformed(server, score):
    requests = {
        "BREW /file1.html HTTP/1.0\r\n\r\n": 1,
        "This is not HTTP\r\n\r\n": 1,
        "GET\r\n\r\n": 1,
    }
    earned, problems = 0, []
    for raw, w in requests.items():
        resp = server.request(raw.encode())
        ok = resp.status == 400
        earned += w * ok
        score.log(f"[{'ok' if ok else 'FAIL'}] {raw.strip()!r} -> {resp.status_line or resp.summary()}")
        if not ok:
            problems.append(f"{raw.strip()!r}: expected 400, got {resp.status_line or resp.summary()}.")
    score.set_fraction(earned / sum(requests.values()))
    _fail(problems, server)


@pytest.mark.gs(weight=5, number="1.8", visibility="visible", exclude_variants=["560"],
                name="HEAD and POST requests return 400 (not implemented in CSCI 471)")
def test_400_head_post(server, score):
    reqs = ["HEAD /file1.html HTTP/1.0\r\n\r\n",
            "POST /file4.html HTTP/1.0\r\nContent-Length: 5\r\n\r\nhello"]
    earned, problems = 0, []
    for raw in reqs:
        resp = server.request(raw.encode())
        ok = resp.status == 400
        earned += ok
        score.log(f"[{'ok' if ok else 'FAIL'}] {raw.split(chr(13))[0]} -> {resp.status_line or resp.summary()}")
        if not ok:
            problems.append(f"{raw.split(chr(13))[0]}: expected 400, got {resp.status_line or resp.summary()}.")
    score.set_fraction(earned / len(reqs))
    _fail(problems, server)


@pytest.mark.gs(weight=5, number="1.9", visibility="visible",
                name="Keeps serving: handles many requests in a row, including errors")
def test_many_requests(server, score):
    sequence = [("/file1.html", 200), ("/nope.html", 404), ("/file2.html", 200), ("BAD", 400),
                ("/image1.jpg", 200), ("/file9.html", 404), ("/file3.html", 200)] * 2
    good = 0
    for path, want in sequence:
        resp = server.request(b"BAD REQUEST\r\n\r\n") if path == "BAD" else server.get(path)
        ok = resp.status == want
        good += ok
        if not ok:
            score.log(f"Request {good + 1} ({'malformed' if path == 'BAD' else 'GET ' + path}) "
                      f"expected {want}, got {resp.status_line or resp.summary()}")
            break
    score.set_fraction(good / len(sequence))
    score.log(f"{good}/{len(sequence)} requests in a row answered correctly.")
    if good < len(sequence):
        _fail(["The server stopped answering correctly partway through a series of requests."], server)


# ---------------------------------------------------------------- hidden

@pytest.mark.gs(weight=5, number="2.1", visibility=HIDDEN,
                name="Binary files: larger JPEGs (including NUL, CR and LF bytes) arrive byte-for-byte")
def test_binary_files(server, score):
    problems = []
    for name in ("image2.jpg", harness.GENERATED_IMAGE):
        resp = server.get(f"/{name}", timeout=10)
        p = _check_ok(resp, _expect(server, name), "image/jpeg")
        score.log(f"[{'ok' if not p else 'FAIL'}] GET /{name} ({len(_expect(server, name))} bytes)")
        problems += [f"/{name}: {x}" for x in p]
    score.set_fraction(1 - 0.5 * len({p.split(':')[0] for p in problems}))
    _fail(problems, server)


@pytest.mark.gs(weight=5, number="2.2", visibility=HIDDEN,
                name="Ignores request header lines (browser/curl-style request)")
def test_ignores_headers(server, score):
    raw = (f"GET /file3.html HTTP/1.1\r\nHost: localhost:{server.port}\r\nUser-Agent: curl/7.81.0\r\n"
           "Accept: */*\r\nAccept-Language: en-US,en;q=0.9\r\nConnection: close\r\n"
           "X-Long-Header: " + "x" * 600 + "\r\n\r\n").encode()
    resp = server.request(raw)
    _log_exchange(score, raw[:200] + b"...", resp)
    _fail(_check_ok(resp, _expect(server, "file3.html"), "text/html"), server)


@pytest.mark.gs(weight=5, number="2.3", visibility=HIDDEN,
                name="Handles a request that arrives a few bytes at a time")
def test_fragmented_request(server, score):
    raw = b"GET /file2.html HTTP/1.0\r\nUser-Agent: slow-client\r\n\r\n"
    resp = server.request(raw, chunk=3, pause=0.03, timeout=10)
    _log_exchange(score, "(sent 3 bytes at a time) " + raw.decode(), resp)
    _fail(_check_ok(resp, _expect(server, "file2.html"), "text/html"), server)


@pytest.mark.gs(weight=5, number="2.4", visibility=HIDDEN,
                name="Path tricks (../, extra directories) never reach files outside the allowed set")
def test_path_tricks(server, score):
    _graded_paths(server, score, {"/../file1.html": 1, "/../secret.html": 1, "/data/../../image1.jpg": 1,
                                  "/file1.html.bak": 1, "/./../file1.html": 1}, (400, 404))


@pytest.mark.gs(weight=5, number="2.5", visibility=HIDDEN,
                name="Survives clients that disconnect early, then keeps serving")
def test_early_disconnect(server, score):
    harness.connect_and_close(server.port)
    harness.connect_and_close(server.port, b"GET /file1")
    resp = server.get("/file1.html")
    _log_exchange(score, "(after one client connected and hung up, and another sent half a request) "
                         "GET /file1.html HTTP/1.0\r\n\r\n", resp)
    if not server.bg.alive():
        _fail(["The server exited after a client disconnected early."], server)
    _fail(_check_ok(resp, _expect(server, "file1.html"), "text/html"), server)


# --------------------------------------------------------------- CSCI 560

@pytest.mark.gs(weight=5, number="3.1", visibility="visible", variants=["560"],
                name="560: HEAD returns the same headers as GET but no body")
def test_head(server, score):
    expected = _expect(server, "image1.jpg")
    resp = server.request(b"HEAD /image1.jpg HTTP/1.0\r\n\r\n")
    _log_exchange(score, "HEAD /image1.jpg HTTP/1.0\r\n\r\n", resp)
    problems = _check_ok(resp, expected, "image/jpeg", body=False)
    if resp.body:
        problems.append(f"A HEAD response must not include a body (got {len(resp.body)} bytes after the header).")
    missing = server.request(b"HEAD /file7.html HTTP/1.0\r\n\r\n")
    score.log(f"HEAD /file7.html -> {missing.status_line or missing.summary()}")
    if missing.status != 404:
        problems.append(f"HEAD /file7.html (missing file): expected 404, got {missing.status_line or 'nothing'}.")
    score.set_fraction(1 - len(problems) / 4)
    _fail(problems, server)


def _post(server: Server, name: str, body: bytes) -> Response:
    raw = f"POST /{name} HTTP/1.0\r\nContent-Type: application/octet-stream\r\nContent-Length: {len(body)}\r\n\r\n"
    return server.request(raw.encode() + body, timeout=10)


@pytest.mark.gs(weight=5, number="3.2", visibility="visible", variants=["560"],
                name="560: POST to a new file saves the body and returns 201 Created")
def test_post_create(server, score):
    body = b"<html><body>posted by the autograder</body></html>\n"
    resp = _post(server, "file4.html", body)
    _log_exchange(score, f"POST /file4.html HTTP/1.0\r\nContent-Length: {len(body)}\r\n\r\n{body.decode()}", resp)
    problems = []
    if resp.status != 201:
        problems.append(f"Expected 201 Created, got {resp.status_line or resp.summary()}.")
    saved = server.data / "file4.html"
    if not saved.exists():
        problems.append("data/file4.html was not created.")
    elif saved.read_bytes() != body:
        problems.append(f"data/file4.html holds {len(saved.read_bytes())} bytes that don't match the request body.")
    score.set_fraction(1 - len(problems) / 2)
    _fail(problems, server)


@pytest.mark.gs(weight=5, number="3.3", visibility="visible", variants=["560"],
                name="560: POST to an existing file overwrites it and returns 200 OK")
def test_post_overwrite(server, score):
    body = b"<html>short</html>\n"   # shorter than file2.html: catches missing truncation
    resp = _post(server, "file2.html", body)
    _log_exchange(score, f"POST /file2.html HTTP/1.0\r\nContent-Length: {len(body)}\r\n\r\n{body.decode()}", resp)
    problems = []
    if resp.status != 200:
        problems.append(f"Expected 200 OK, got {resp.status_line or resp.summary()}.")
    saved = (server.data / "file2.html").read_bytes()
    if saved != body:
        problems.append("data/file2.html does not contain exactly the posted body"
                        + (" (the old contents were not truncated)." if saved.startswith(body) else "."))
    score.set_fraction(1 - len(problems) / 2)
    _fail(problems, server)


@pytest.mark.gs(weight=5, number="3.4", visibility=HIDDEN, variants=["560"],
                name="560: POST of binary data, then GET returns it unchanged")
def test_post_binary_roundtrip(server, score):
    body = harness.generated_image()[:20_000]
    resp = _post(server, "image6.jpg", body)
    score.log(f"POST /image6.jpg ({len(body)} binary bytes) -> {resp.status_line or resp.summary()}")
    problems = []
    if resp.status != 201:
        problems.append(f"Expected 201 Created, got {resp.status_line or resp.summary()}.")
    got = server.get("/image6.jpg", timeout=10)
    score.log(f"GET /image6.jpg -> {got.status_line or got.summary()}")
    problems += _check_ok(got, body, "image/jpeg")
    score.set_fraction(1 - min(len(problems), 2) / 2)
    _fail(problems, server)


@pytest.mark.gs(weight=5, number="3.5", visibility="visible", variants=["560"],
                name="560: catches SIGINT (Ctrl-C) and shuts down cleanly")
def test_sigint(server, score):
    resp = server.get("/file1.html")
    if resp.status != 200:
        score.log(f"(GET /file1.html before the signal -> {resp.status_line or resp.summary()})")
    server.bg.signal(signal.SIGINT)
    t0 = time.monotonic()
    code = server.bg.wait(3)
    if code is None:
        _fail(["The server was still running 3 s after SIGINT."], server)
    score.log(f"Exited {time.monotonic() - t0:.2f} s after SIGINT with status {code}.")
    if code < 0:
        _fail(["The server was killed by SIGINT instead of catching it. Install a handler (sigaction/signal) "
               "that closes the listening socket and exits."])
