"""Project 1 grader internals: run the student's webServer and talk raw HTTP to it.

The server is started in a fresh directory containing ``data/`` (the files it
may serve) plus decoys it must refuse. Its port is taken from what it prints;
on Linux the printed number is confirmed against the process's listening
sockets in /proc (no probe connections, which a fragile server might choke on).
"""

from __future__ import annotations

import os
import random
import re
import shutil
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from framework import config, proc

DATA_SRC = Path(__file__).parent / "data"
STARTUP_TIMEOUT = 5.0
HOST = "127.0.0.1"

# Files served from data/ that match file[0-9].html / image[0-9].jpg.
SERVED = ("file1.html", "file2.html", "file3.html", "image1.jpg", "image2.jpg")
GENERATED_IMAGE = "image8.jpg"        # random bytes incl. NUL, CR, LF: catches text-mode bugs
SECRET = b"TOP SECRET: the web server must never send this file.\n"
# name -> content of files that exist but must NOT be served.
DECOYS_IN_DATA = {
    "badname.html": b"<html><body>badname.html must not be served</body></html>\n" + SECRET,
    "file10.html": b"<html><body>two digits</body></html>\n" + SECRET,
    "file1.htm": b"<html><body>wrong extension</body></html>\n" + SECRET,
    "file1.html.bak": SECRET,
    "secret.txt": SECRET,
}
DECOYS_IN_CWD = {"file1.html": SECRET, "image1.jpg": SECRET, "secret.html": SECRET}

_INT_RE = re.compile(r"(?<![\d.])(\d{4,5})(?![\d.])")


def generated_image() -> bytes:
    rng = random.Random(471)
    data = bytearray(rng.randbytes(60_000))
    data[100:110] = b"\0\0\r\n\r\n\0\n\r\0"   # deliberately awkward bytes early on
    return bytes(data)


def stage(workdir: Path) -> Path:
    """Create data/ and decoys in ``workdir``; return the data directory."""
    data = workdir / "data"
    data.mkdir()
    for name in SERVED:
        shutil.copy(DATA_SRC / name, data / name)
    (data / GENERATED_IMAGE).write_bytes(generated_image())
    for name, content in DECOYS_IN_DATA.items():
        (data / name).write_bytes(content)
    for name, content in DECOYS_IN_CWD.items():
        (workdir / name).write_bytes(content)
    for p in [data, *data.iterdir(), *(workdir / n for n in DECOYS_IN_CWD)]:
        p.chmod(0o777 if p.is_dir() else 0o666)
    config.give_to_student(workdir)
    return data


# ------------------------------------------------------------------- server

def listening_ports(pgid: int) -> set[int] | None:
    """TCP ports in LISTEN state owned by processes in group ``pgid`` (Linux), else None."""
    if not sys.platform.startswith("linux") or not os.path.isdir("/proc/net"):
        return None
    inodes: set[str] = set()
    # Reading another user's /proc/<pid>/fd needs CAP_SYS_PTRACE, which containers
    # usually drop. Then fall back to matching the socket owner's uid instead.
    blind_uids: set[str] = set()
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            stat = (pid_dir / "stat").read_text()
            if int(stat.rsplit(")", 1)[1].split()[2]) != pgid:
                continue
            uid = str(pid_dir.stat().st_uid)
        except (OSError, ValueError, IndexError):
            continue
        try:
            for fd in (pid_dir / "fd").iterdir():
                target = os.readlink(fd)
                if target.startswith("socket:["):
                    inodes.add(target[8:-1])
        except PermissionError:
            blind_uids.add(uid)
        except OSError:
            continue
    ports = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            cols = line.split()
            if len(cols) > 9 and cols[3] == "0A" and (cols[9] in inodes or cols[7] in blind_uids):
                ports.add(int(cols[1].rsplit(":", 1)[1], 16))
    return ports


def printed_numbers(text: str) -> list[int]:
    return [n for n in map(int, _INT_RE.findall(text)) if 1024 <= n <= 65535]


@dataclass
class Server:
    workdir: Path
    data: Path
    bg: proc.Background
    port: int | None = None
    port_printed: bool = False
    startup_problem: str = ""
    notes: list[str] = field(default_factory=list)

    def output(self) -> str:
        out, err = self.bg.output()
        return (out + ("\n[stderr]\n" + err if err.strip() else "")).strip()

    def describe(self) -> str:
        """How the server is doing, for failure messages."""
        if self.bg.alive():
            status = "Your server is still running."
        else:
            status = self.bg.stop().describe(tail=0).replace("exited", "exited early,")
        out = self.output()
        if len(out) > 2000:
            out = "..." + out[-2000:]
        return f"{status}\nServer output:\n{out or '(none)'}"

    def request(self, raw: bytes, **kw) -> "Response":
        assert self.port is not None
        return http_request(self.port, raw, **kw)

    def get(self, path: str, **kw) -> "Response":
        return self.request(f"GET {path} HTTP/1.0\r\n\r\n".encode(), **kw)


def start(executable: Path, workdir: Path, args: tuple[str, ...] = ()) -> Server:
    data = stage(workdir)
    cmd = [str(executable), *args]
    if sys.platform.startswith("linux") and shutil.which("stdbuf"):
        cmd = ["stdbuf", "-oL", "-eL", *cmd]   # line-buffer printf/cout so the port line shows up
    bg = proc.start(cmd, cwd=workdir)
    server = Server(workdir, data, bg)

    deadline = time.monotonic() + STARTUP_TIMEOUT
    while True:
        out, err = bg.output()
        printed = printed_numbers(out + "\n" + err)
        listening = listening_ports(bg.pid)
        if listening is None:          # no /proc: trust the last printed number
            if printed:
                server.port, server.port_printed = printed[-1], True
                time.sleep(0.2)        # give listen() a moment after the print
                break
        else:
            matches = [p for p in printed if p in listening]
            if matches:
                server.port, server.port_printed = matches[-1], True
                break
            if listening and time.monotonic() >= deadline - 3:
                server.port = min(listening)
                break
        if not bg.alive():
            server.startup_problem = "Your server exited before it started listening for connections."
            break
        if time.monotonic() >= deadline:
            server.startup_problem = ("Your server did not print a port number it was listening on within "
                                      f"{STARTUP_TIMEOUT:.0f} s." if listening is None else
                                      f"Your server was not listening on any TCP port after {STARTUP_TIMEOUT:.0f} s.")
            break
        time.sleep(0.05)

    if server.port is not None and not server.port_printed:
        server.notes.append(f"Note: found your server listening on port {server.port}, but it did not print "
                            "that number (or the output was not flushed; end the line with std::endl).")
    return server


# ------------------------------------------------------------------- client

@dataclass
class Response:
    raw: bytes = b""
    error: str = ""                  # connection-level problem, if any
    closed: bool = False             # server closed the connection
    elapsed: float = 0.0

    @property
    def head_end(self) -> int:
        """Index just past the blank line ending the header, or -1."""
        for sep in (b"\r\n\r\n", b"\n\n"):
            i = self.raw.find(sep)
            if i >= 0:
                return i + len(sep)
        return -1

    @property
    def head(self) -> str:
        end = self.head_end
        return (self.raw[:end] if end >= 0 else self.raw).decode("latin-1")

    @property
    def status_line(self) -> str:
        return self.head.split("\n", 1)[0].strip()

    @property
    def status(self) -> int | None:
        m = re.match(r"HTTP/\d+(?:\.\d+)?\s+(\d{3})\b", self.status_line)
        return int(m.group(1)) if m else None

    @property
    def headers(self) -> dict[str, str]:
        out = {}
        for line in self.head.splitlines()[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                out.setdefault(k.strip().lower(), v.strip())
        return out

    @property
    def content_length(self) -> int | None:
        v = self.headers.get("content-length", "")
        return int(v) if v.isdigit() else None

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    @property
    def body(self) -> bytes:
        end = self.head_end
        return self.raw[end:] if end >= 0 else b""

    @property
    def crlf_header(self) -> bool:
        end = self.raw.find(b"\r\n\r\n")
        return end >= 0 and b"\n" not in self.raw[:end].replace(b"\r\n", b"")

    def summary(self, limit: int = 600) -> str:
        if self.error and not self.raw:
            return f"(no response: {self.error})"
        if not self.raw:
            return "(the server sent nothing" + (" and closed the connection)" if self.closed else ")")
        head = self.head.replace("\r", "").rstrip()
        text = head if len(head) <= limit else head[:limit] + " ..."
        body = self.body
        if body:
            text += f"\n<{len(body)} body bytes>"
        if self.head_end < 0:
            text += "\n(the header was never terminated by a blank line)"
        return text


def http_request(port: int, raw: bytes, chunk: int = 0, pause: float = 0.0,
                 timeout: float = 5.0, idle: float = 1.0) -> Response:
    """Send ``raw`` and read the response.

    Reading stops when the server closes the connection, once Content-Length
    body bytes have arrived (plus a short grace period), after ``idle`` seconds
    of silence following a complete header, or after ``timeout`` seconds.
    """
    resp = Response()
    start = time.monotonic()
    try:
        sock = socket.create_connection((HOST, port), timeout=timeout)
    except OSError as e:
        resp.error = f"could not connect to port {port}: {e.strerror or e}"
        return resp
    with sock:
        try:
            if chunk:
                for i in range(0, len(raw), chunk):
                    sock.sendall(raw[i:i + chunk])
                    time.sleep(pause)
            else:
                sock.sendall(raw)
        except OSError as e:
            resp.error = f"connection failed while sending the request: {e.strerror or e}"
        deadline = start + timeout
        buf = bytearray()
        while True:
            now = time.monotonic()
            if now >= deadline:
                resp.error = resp.error or f"timed out after {timeout:.0f} s"
                break
            wait = deadline - now
            resp.raw = bytes(buf)
            if resp.head_end >= 0:
                cl = resp.content_length
                if cl is not None and len(resp.body) >= cl:
                    wait = min(wait, 0.2)
                else:
                    wait = min(wait, idle)
            sock.settimeout(wait)
            try:
                data = sock.recv(65536)
            except socket.timeout:
                if resp.head_end >= 0:
                    break
                continue
            except OSError as e:
                resp.error = resp.error or f"connection error: {e.strerror or e}"
                break
            if not data:
                resp.closed = True
                break
            buf += data
        resp.raw = bytes(buf)
    resp.elapsed = time.monotonic() - start
    return resp


def connect_and_close(port: int, partial: bytes = b"") -> None:
    """Open a connection, optionally send a fragment, then hang up."""
    try:
        with socket.create_connection((HOST, port), timeout=2) as s:
            if partial:
                s.sendall(partial)
                time.sleep(0.2)
    except OSError:
        pass
