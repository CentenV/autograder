"""Project 2: Reliable File Transfer with Go-Back-N. Black-box tests of ``rft-client``.

The client sends to an instrumented Python copy of the instructor's rft-server
(see gbn.py) on localhost. Each scenario runs the client once (cached); tests
grade the delivered file plus what the server saw: sequence numbers,
checksums, end-of-file datagram and how many datagrams were unacknowledged.
471 = default zip; 560 adds -w / -t / throughput tests (make_zip.sh p2_rft 560).
"""

from __future__ import annotations

import re

import pytest

from autograder.common_tests import test_build  # noqa: F401  (graded build step)

from . import gbn
from .gbn import Faults, Transfer

HIDDEN = "after_published"
WINDOW = 10
SLOW_ACKS = Faults(ack_delay=0.005)

SCENARIOS = {
    # name: (data, faults, timeout)
    "text": (gbn.text_file(6_000, 1), None, 60),
    "binary": (gbn.binary_file(64 * 1024 + 17, 2), None, 90),
    "slow_acks": (gbn.binary_file(15_000, 3), SLOW_ACKS, 90),
    "lossy": (gbn.binary_file(30_000, 4), Faults(loss=0.05, corrupt=0.05, seed=7), 150),
    "empty": (b"", None, 30),
    "exact": (gbn.text_file(255 * 40, 5), None, 60),
    "first_lost": (gbn.text_file(3_000, 6), Faults(drop_data={1: 2}), 60),
    "eof_lost": (gbn.text_file(3_000, 8), Faults(drop_eof=2), 60),
    "mangled": (gbn.binary_file(20_000, 9), Faults(loss=0.08, mangle_acks=0.3, seed=11), 150),
    "heavy": (gbn.binary_file(20_000, 10), Faults(loss=0.15, corrupt=0.15, seed=13), 240),
}


def _run(transfer, name: str, args: tuple[str, ...] = (), data_name: str | None = None) -> Transfer:
    data, faults, timeout = SCENARIOS[data_name or name]
    return transfer(name, data, faults, args, timeout)


def _grade_delivery(t: Transfer, score) -> None:
    problems = t.problems()
    score.log(t.report() if problems else "\n".join(t.report().splitlines()[:2]))
    if problems:
        if t.rx.finished or bytes(t.rx.output) == t.data:
            score.set_fraction(0.5 if bytes(t.rx.output) == t.data else 0.25)
        pytest.fail("\n".join(problems), pytrace=False)


# ------------------------------------------------------------ basic transfer

@pytest.mark.gs(weight=15, number="1.1", visibility="visible",
                name="No loss: transfers a text file intact and exits")
def test_text_transfer(transfer, score):
    _grade_delivery(_run(transfer, "text"), score)


@pytest.mark.gs(weight=15, number="1.2", visibility="visible",
                name="No loss: transfers a 64 KB binary file (all byte values) intact and exits")
def test_binary_transfer(transfer, score):
    _grade_delivery(_run(transfer, "binary"), score)


@pytest.mark.gs(weight=10, number="1.3", visibility="visible",
                name="Datagrams: sequence numbers start at 1 and go up by one, valid checksums, "
                     "zero-length datagram at the end")
def test_datagram_format(transfer, score):
    t = _run(transfer, "binary")
    rx = t.rx
    if not rx.received:
        score.log(t.report())
        pytest.fail("The server received no datagrams.", pytrace=False)
    seqs = sorted({d.seq for d in rx.received if d.cksum_ok})
    payloads: dict[int, set[bytes]] = {}
    for d in rx.received:
        if d.cksum_ok:
            payloads.setdefault(d.seq, set()).add(d.payload)
    last = max(seqs) if seqs else 0
    eof = [d for d in rx.received if d.cksum_ok and d.seq == last]
    checks = {
        "the first datagram has sequence number 1": rx.received[0].seq == 1,
        "every datagram has a correct checksum (computeChecksum)": all(d.cksum_ok for d in rx.received),
        "sequence numbers are consecutive with no gaps (1, 2, 3, ...)": seqs == list(range(1, last + 1)),
        "retransmissions carry the same data as the original": all(len(p) == 1 for p in payloads.values()),
        "the last datagram has payloadLength 0": bool(eof) and all(d.length == 0 for d in eof),
    }
    score.set_fraction(sum(checks.values()) / len(checks))
    score.log("\n".join(f"[{'ok' if v else 'FAIL'}] {k}" for k, v in checks.items()))
    score.log(t.report())
    failed = [k for k, v in checks.items() if not v]
    if failed:
        pytest.fail("Failed: " + "; ".join(failed), pytrace=False)


@pytest.mark.gs(weight=10, number="1.4", visibility="visible",
                name="Window: never more than 10 unacknowledged datagrams (server ACKs slowly)")
def test_window_limit(transfer, score):
    t = _run(transfer, "slow_acks")
    score.log(f"Most unacknowledged datagrams seen at once: {t.rx.max_outstanding} (limit {WINDOW}).")
    score.log(t.report())
    if len(t.rx.received) < 2 * WINDOW:
        pytest.fail("Too few datagrams were sent to check the window; the transfer must work first.",
                    pytrace=False)
    if t.rx.max_outstanding > WINDOW:
        pytest.fail(f"Sent sequence number(s) more than {WINDOW} beyond the last ACK: up to "
                    f"{t.rx.max_outstanding} datagrams were unacknowledged at once.", pytrace=False)


@pytest.mark.gs(weight=5, number="1.5", visibility="visible",
                name="Pipelining: keeps several datagrams in flight instead of stop-and-wait")
def test_pipelining(transfer, score):
    t = _run(transfer, "slow_acks")
    n = t.rx.max_outstanding
    score.log(f"Most unacknowledged datagrams seen at once: {n} (Go-Back-N with N={WINDOW} should reach {WINDOW}).")
    if n >= 5:
        return
    score.set_fraction(0.5 if n >= 2 else 0)
    pytest.fail("The client waited for ACKs instead of filling its window of 10 datagrams.", pytrace=False)


@pytest.mark.gs(weight=15, number="1.6", visibility="visible",
                name="5% loss and 5% corruption in both directions: file arrives intact")
def test_lossy_transfer(transfer, score):
    _grade_delivery(_run(transfer, "lossy"), score)


@pytest.mark.gs(weight=5, number="1.7", visibility="visible",
                name="-d DEBUG_LEVEL is accepted and changes how much output is printed")
def test_debug_flag(transfer, score):
    data = SCENARIOS["text"][0]
    counts, broken = {}, []
    for level in ("0", "6", "4", "5"):
        t = transfer(f"text-d{level}", data, None, ("-d", level), 60)
        counts[f"-d {level}"] = len((t.result.out + t.result.err).splitlines())
        if not t.complete:
            broken.append(level)
        if max(counts.values()) - min(counts.values()) >= 2:
            break
    score.log("Lines of output (stdout + stderr): " + ", ".join(f"{k}: {v}" for k, v in counts.items()))
    varies = max(counts.values()) - min(counts.values()) >= 2
    if broken:
        score.set_fraction(0.5 if varies else 0)
        pytest.fail(f"The transfer did not complete with -d {', -d '.join(broken)}.", pytrace=False)
    if not varies:
        score.set_fraction(0.5)
        pytest.fail("-d is accepted, but no debug level changed the amount of output.", pytrace=False)


# ---------------------------------------------------------------- hidden

@pytest.mark.gs(weight=5, number="2.1", visibility=HIDDEN,
                name="Edge sizes: an empty file and a file that is an exact multiple of 255 bytes")
def test_edge_sizes(transfer, score):
    problems = []
    for name in ("empty", "exact"):
        t = _run(transfer, name)
        p = t.problems()
        score.log(f"[{'ok' if not p else 'FAIL'}] {name}: {len(t.data)}-byte file")
        if p:
            score.log(t.report())
        problems += [f"{len(t.data)}-byte file: {x}" for x in p]
    score.set_fraction(1 - 0.5 * len({p.split(':')[0] for p in problems}))
    if problems:
        pytest.fail("\n".join(problems), pytrace=False)


@pytest.mark.gs(weight=5, number="2.2", visibility=HIDDEN,
                name="First datagram lost twice: times out, retransmits and completes")
def test_first_lost(transfer, score):
    _grade_delivery(_run(transfer, "first_lost"), score)


@pytest.mark.gs(weight=5, number="2.3", visibility=HIDDEN,
                name="End-of-file datagram lost twice: retransmits it until ACKed")
def test_eof_lost(transfer, score):
    _grade_delivery(_run(transfer, "eof_lost"), score)


@pytest.mark.gs(weight=5, number="2.4", visibility=HIDDEN,
                name="Ignores corrupted ACKs (bad checksum, wrong ackNum)")
def test_corrupted_acks(transfer, score):
    _grade_delivery(_run(transfer, "mangled"), score)


@pytest.mark.gs(weight=5, number="2.5", visibility=HIDDEN,
                name="15% loss and 15% corruption in both directions: file arrives intact")
def test_heavy_loss(transfer, score):
    _grade_delivery(_run(transfer, "heavy"), score)


# --------------------------------------------------------------- CSCI 560

@pytest.mark.gs(weight=5, number="3.1", visibility="visible", variants=["560"],
                name="560: -w 5 limits the window to 5 datagrams")
def test_window_flag_small(transfer, score):
    t = _run(transfer, "w5", ("-w", "5"), data_name="slow_acks")
    n = t.rx.max_outstanding
    score.log(f"With -w 5: most unacknowledged datagrams at once = {n}.")
    problems = t.problems()
    if n > 5:
        problems.append(f"Up to {n} datagrams were unacknowledged at once; -w 5 allows 5.")
    if problems:
        score.log(t.report())
        pytest.fail("\n".join(problems), pytrace=False)


@pytest.mark.gs(weight=5, number="3.2", visibility="visible", variants=["560"],
                name="560: -w 20 lets more than 10 datagrams be in flight")
def test_window_flag_large(transfer, score):
    t = _run(transfer, "w20", ("-w", "20"), data_name="slow_acks")
    n = t.rx.max_outstanding
    score.log(f"With -w 20: most unacknowledged datagrams at once = {n}.")
    problems = t.problems()
    if n <= WINDOW:
        problems.append(f"Never had more than {n} datagrams in flight; -w 20 should allow up to 20.")
    if n > 20:
        problems.append(f"Up to {n} datagrams were unacknowledged at once; -w 20 allows 20.")
    if problems:
        score.log(t.report())
        pytest.fail("\n".join(problems), pytrace=False)


@pytest.mark.gs(weight=5, number="3.3", visibility="visible", variants=["560"],
                name="560: -t sets the retransmission timeout (compares -t 50 and -t 400)")
def test_timeout_flag(transfer, score):
    data = gbn.text_file(4_000, 12)
    gaps = {}
    for ms in (50, 400):
        t = transfer(f"t{ms}", data, Faults(drop_data={5: 1}), ("-t", str(ms)), 60)
        sends = t.rx.sends_of(5)
        gaps[ms] = sends[1].t - sends[0].t if len(sends) >= 2 else None
        score.log(f"-t {ms}: datagram 5 was dropped once; retransmitted after "
                  + (f"{gaps[ms] * 1000:.0f} ms" if gaps[ms] is not None else "(never)")
                  + ("" if t.complete else "  [transfer did not complete]"))
    if None in gaps.values():
        pytest.fail("The dropped datagram was not retransmitted in both runs.", pytrace=False)
    if not (gaps[400] >= 0.25 and gaps[400] > 2 * gaps[50]):
        pytest.fail("The retransmission delay did not follow -t (expected about 50 ms vs 400 ms).",
                    pytrace=False)


@pytest.mark.gs(weight=5, number="3.4", visibility="visible", variants=["560"],
                name="560: reports the total transfer time and the throughput")
def test_reports_throughput(transfer, score):
    t = _run(transfer, "binary")
    out = t.result.out + "\n" + t.result.err
    tail = "\n".join(out.strip().splitlines()[-15:])
    score.log(f"Last lines of output:\n{tail or '(none)'}")
    throughput = re.search(r"through\s*-?put|\b[KMG]?i?(?:bytes|bits|B|b)\s*(?:/|per)\s*(?:s|sec|second)\b|[KMG]?bps\b",
                           out, re.IGNORECASE)
    elapsed = re.search(r"\btime\b|elapsed|duration|took|\d\s*(?:ms|s|sec|secs|seconds|milliseconds)\b",
                        out, re.IGNORECASE)
    if throughput and elapsed:
        return
    score.set_fraction(0.5 if throughput or elapsed else 0)
    missing = [w for w, ok in (("throughput", throughput), ("total time", elapsed)) if not ok]
    pytest.fail(f"Could not find the {' or '.join(missing)} in the output (run without -d).", pytrace=False)
