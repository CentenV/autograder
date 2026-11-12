"""Project 2 grader internals: an instrumented Go-Back-N receiver and client runner.

``Receiver`` reimplements the instructor's rft-server (promig3/RFT-Server,
rft-server.cpp + networkLayer.cpp) in Python so every datagram can be
recorded and loss/corruption can be seeded or scripted:

  * expectedSeqNum starts at 1; a datagram with a valid checksum and
    seqNum == expectedSeqNum is delivered and ACKed (ackNum = its seqNum);
    every received datagram, valid or not, is answered with the latest ACK.
  * payloadLength 0 ends the file. Afterwards loss/corruption stop and the
    receiver keeps re-ACKing zero-length datagrams until 1 s of silence.
  * Random loss/corruption apply to both directions until end of file;
    corruption increments the checksum, exactly like the original.

Wire format is ``struct datagramS`` from datagram.h on x86-64:
uint32 seqNum, uint32 ackNum, uint16 checksum, uint8 payloadLength,
uint8 data[255], 2 bytes padding = 268 bytes, little-endian.
"""

from __future__ import annotations

import random
import socket
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from autograder import config, proc

MAX_PAYLOAD = 255
FMT = struct.Struct("<IIHB255s2x")
SIZE = FMT.size  # 268
PORT_RANGE = (12000, 13000)   # the skeleton client rejects other ports
LINGER = 1.0
STALL = 10.0                  # kill the client if the server hears nothing for this long


def checksum(seq: int, ack: int, length: int, data: bytes) -> int:
    """computeChecksum() from datagram.cpp (16-bit wrap-around sum)."""
    total = (seq & 0xFFFF) + (seq >> 16) + (ack & 0xFFFF) + (ack >> 16) + length + sum(data[:length])
    return total & 0xFFFF


def pack(seq: int, ack: int, length: int = 0, data: bytes = b"", cksum: int | None = None) -> bytes:
    if cksum is None:
        cksum = checksum(seq, ack, length, data)
    return FMT.pack(seq, ack, cksum & 0xFFFF, length, data.ljust(MAX_PAYLOAD, b"\0")[:MAX_PAYLOAD])


@dataclass
class Datagram:
    t: float
    seq: int
    ack: int
    length: int
    cksum_ok: bool
    size: int                   # bytes actually received
    outstanding: int            # seq - highest ACK sent so far (upper bound on the client's window use)
    fate: str = "ok"            # ok | lost | corrupted
    payload: bytes = b""


@dataclass
class Faults:
    """What the simulated network does. Random faults use ``seed`` so runs are repeatable."""
    loss: float = 0.0
    corrupt: float = 0.0
    seed: int = 0
    ack_delay: float = 0.0               # seconds before each ACK goes out (receiving continues meanwhile)
    drop_data: dict[int, int] = field(default_factory=dict)   # seqNum -> times to drop it
    drop_eof: int = 0                    # times to drop the zero-length datagram
    mangle_acks: float = 0.0             # probability an ACK's ackNum is garbled (bad checksum)


class Receiver(threading.Thread):
    def __init__(self, faults: Faults | None = None):
        super().__init__(daemon=True)
        self.faults = faults or Faults()
        self.rng = random.Random(self.faults.seed)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.port = self._bind()
        self.sock.settimeout(0.05)
        self.received: list[Datagram] = []
        self.acks_sent: list[tuple[float, int, str]] = []   # (t, ackNum, fate)
        self.output = bytearray()
        self.finished = False            # zero-length datagram accepted
        self.errors: list[str] = []
        self._halt = threading.Event()
        self._drops = dict(self.faults.drop_data)
        self._eof_drops = self.faults.drop_eof
        self._t0 = time.monotonic()
        self._max_ack_tx = 0

    def _bind(self) -> int:
        ports = list(range(*PORT_RANGE))
        random.shuffle(ports)
        for port in ports:
            try:
                self.sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
        raise RuntimeError("no free UDP port in 12000-13000")

    # ----------------------------------------------------------------- loop

    def stop(self) -> None:
        self._halt.set()
        self.join(5)
        self.sock.close()

    def run(self) -> None:
        expected = 1
        last_ack = 0
        self._max_ack_tx = 0
        client = None
        idle_since = None
        pending: deque[tuple[float, int, bool]] = deque()   # (due, ackNum, faults) for delayed ACKs
        while not self._halt.is_set():
            now = time.monotonic()
            while pending and pending[0][0] <= now:
                _, a, f = pending.popleft()
                self._send(client, a, faults=f)
            wait = 0.05 if not pending else max(0.0005, min(0.05, pending[0][0] - now))
            self.sock.settimeout(wait)
            try:
                raw, client = self.sock.recvfrom(4096)
            except socket.timeout:
                if self.finished and not pending and idle_since is not None \
                        and time.monotonic() - idle_since > LINGER:
                    return
                continue
            except OSError:
                return
            now = time.monotonic()
            idle_since = self.last_heard = now
            seq, ack, cks, length, data = FMT.unpack(raw[:SIZE].ljust(SIZE, b"\0"))
            ok = checksum(seq, ack, length, data) == cks
            d = Datagram(now - self._t0, seq, ack, length, ok, len(raw), seq - self._max_ack_tx,
                         payload=bytes(data[:length]))
            self.received.append(d)

            if self.finished:
                # Linger: re-ACK duplicate end-of-file datagrams, no faults.
                if ok and length == 0:
                    pending.append((now + self.faults.ack_delay, last_ack, False))
                continue

            if self._inbound_lost(d):
                d.fate = "lost"
                continue
            if self.faults.corrupt and self.rng.random() < self.faults.corrupt:
                d.fate = "corrupted"
                ok = False

            if ok and seq == expected:
                if length == 0:
                    self.finished = True
                else:
                    self.output += data[:length]
                last_ack = expected
                expected += 1
            pending.append((now + self.faults.ack_delay, last_ack, not self.finished))

    def _inbound_lost(self, d: Datagram) -> bool:
        if d.cksum_ok and d.length == 0 and self._eof_drops > 0:
            self._eof_drops -= 1
            return True
        if d.cksum_ok and self._drops.get(d.seq, 0) > 0 and d.length > 0:
            self._drops[d.seq] -= 1
            return True
        return bool(self.faults.loss) and self.rng.random() < self.faults.loss

    def _send(self, client, ack: int, faults: bool) -> bool:
        """Send an ACK; return True if it actually went out (not lost)."""
        t = time.monotonic() - self._t0
        if faults and self.faults.loss and self.rng.random() < self.faults.loss:
            self.acks_sent.append((t, ack, "lost"))
            return False
        pkt, fate = pack(0, ack), "ok"
        if faults and self.faults.corrupt and self.rng.random() < self.faults.corrupt:
            pkt, fate = pack(0, ack, cksum=checksum(0, ack, 0, b"") + 1), "corrupted"
        elif faults and self.faults.mangle_acks and self.rng.random() < self.faults.mangle_acks:
            pkt, fate = pack(0, ack + 7, cksum=checksum(0, ack, 0, b"")), "mangled"
        try:
            self.sock.sendto(pkt, client)
        except OSError as e:
            self.errors.append(str(e))
            return False
        self.acks_sent.append((t, ack, fate))
        self._max_ack_tx = max(self._max_ack_tx, ack)
        return True

    # -------------------------------------------------------------- analysis

    @property
    def max_outstanding(self) -> int:
        return max((d.outstanding for d in self.received if d.cksum_ok), default=0)

    def sends_of(self, seq: int) -> list[Datagram]:
        return [d for d in self.received if d.seq == seq and d.cksum_ok]

    def stats(self) -> str:
        lost = sum(d.fate == "lost" for d in self.received)
        corrupted = sum(d.fate == "corrupted" for d in self.received)
        acks_lost = sum(f == "lost" for _, _, f in self.acks_sent)
        acks_bad = sum(f in ("corrupted", "mangled") for _, _, f in self.acks_sent)
        return (f"Server received {len(self.received)} datagrams ({lost} dropped, {corrupted} corrupted by the "
                f"simulated network) and sent {len(self.acks_sent)} ACKs ({acks_lost} dropped, {acks_bad} corrupted).")

    def describe(self, limit: int = 25) -> str:
        lines = []
        for d in self.received[:limit]:
            lines.append(f"  t={d.t:7.3f}s seq={d.seq} len={d.length} checksum={'ok' if d.cksum_ok else 'BAD'}"
                         + ("" if d.size == SIZE else f" size={d.size}B (expected {SIZE})")
                         + ("" if d.fate == "ok" else f"  [{d.fate} by the network]"))
        if len(self.received) > limit:
            lines.append(f"  ... {len(self.received) - limit} more")
        return "\n".join(lines) or "  (none)"


# ------------------------------------------------------------------ running

@dataclass
class Transfer:
    name: str
    data: bytes
    args: list[str]
    result: proc.RunResult
    rx: Receiver
    stalled: bool = False       # killed because the server heard nothing for STALL seconds

    @property
    def complete(self) -> bool:
        return self.rx.finished and bytes(self.rx.output) == self.data

    def command(self) -> str:
        return "rft-client " + " ".join(self.args)

    def problems(self) -> list[str]:
        out = []
        got = bytes(self.rx.output)
        if got != self.data:
            if len(got) < len(self.data) and self.data.startswith(got):
                out.append(f"The server received only the first {len(got)} of {len(self.data)} bytes.")
            elif len(got) != len(self.data):
                out.append(f"The server saved {len(got)} bytes; the file is {len(self.data)} bytes, "
                           "and the contents differ.")
            else:
                i = next(i for i, (a, b) in enumerate(zip(got, self.data)) if a != b)
                out.append(f"The received file differs from the original starting at byte {i}.")
        if not self.rx.finished:
            out.append("The server never received the end-of-file datagram (payloadLength 0, next sequence number).")
        if self.stalled and self.rx.finished:
            out.append(f"The transfer finished, but rft-client did not exit; it was killed after sending "
                       f"nothing for {STALL:.0f} s.")
        elif self.stalled:
            out.append(f"rft-client stopped sending (nothing for {STALL:.0f} s) without finishing and was killed. "
                       "Lost datagrams must be retransmitted when the timer expires.")
        elif self.result.timed_out:
            out.append(f"rft-client was still running after {self.result.duration:.0f} s and was killed.")
        elif self.result.returncode not in (0, None):
            out.append(self.result.describe(tail=600))
        return out

    def report(self) -> str:
        err = self.result.err.strip()
        err = ("..." + err[-1500:]) if len(err) > 1500 else err
        return "\n".join([
            f"$ {self.command()}   ({len(self.data)}-byte file, finished in {self.result.duration:.1f} s)",
            self.rx.stats(),
            "First datagrams the server saw:",
            self.rx.describe(),
            *([f"Client output (stderr):\n{err}"] if err else []),
        ])


def run(executable: Path, name: str, data: bytes, faults: Faults | None = None,
        extra_args: tuple[str, ...] = (), timeout: float = 60.0) -> Transfer:
    workdir = config.fresh_dir(config.work_root() / "runs" / name)
    src = workdir / "input.dat"
    src.write_bytes(data)
    src.chmod(0o644)
    rx = Receiver(faults)
    rx.start()
    args = ["-h", "localhost", "-p", str(rx.port), "-f", "input.dat", *extra_args]
    stalled = False
    try:
        with proc.start([executable, *args], cwd=workdir) as client:
            deadline = time.monotonic() + timeout
            while client.wait(0.1) is None and time.monotonic() < deadline:
                if time.monotonic() - rx.last_heard > STALL:
                    stalled = True
                    break
            result = client.stop()
        # Give the receiver a moment to process anything still queued.
        deadline = time.monotonic() + 0.5
        while not rx.finished and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        rx.stop()
    return Transfer(name, data, args, result, rx, stalled)


# --------------------------------------------------------------- test files

def text_file(size: int, seed: int) -> bytes:
    rng = random.Random(seed)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,;:!?-"
    lines, total = [], 0
    while total < size:
        line = "".join(rng.choice(alphabet) for _ in range(rng.randint(20, 90))) + "\n"
        lines.append(line)
        total += len(line)
    return "".join(lines).encode()[:size]


def binary_file(size: int, seed: int) -> bytes:
    rng = random.Random(seed)
    data = bytearray(rng.randbytes(size))
    data[:256] = bytes(range(256))   # every byte value, including NUL, CR, LF
    return bytes(data[:size])
