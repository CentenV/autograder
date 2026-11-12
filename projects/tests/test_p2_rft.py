"""Project 2 grader internals: datagram format and the Go-Back-N receiver.

End-to-end behaviour needs a built client; see tools/selfcheck.py p2_rft.
"""

import socket
import time

from p2_rft import gbn


def test_wire_format_matches_datagram_h():
    assert gbn.SIZE == 268  # sizeof(datagramS) on x86-64
    pkt = gbn.pack(1, 0, 3, b"abc")
    seq, ack, cks, length, data = gbn.FMT.unpack(pkt)
    assert (seq, ack, length, data[:3]) == (1, 0, 3, b"abc")
    assert cks == (1 + 3 + sum(b"abc")) & 0xFFFF


def test_checksum_wraps_16_bits():
    assert gbn.checksum(0x0001FFFF, 0, 255, b"\xff" * 255) == (0xFFFF + 1 + 255 + 255 * 255) & 0xFFFF


def _client(rx, packets, delay=0.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1)
    acks = []
    for p in packets:
        s.sendto(p, ("127.0.0.1", rx.port))
        time.sleep(delay)
    try:
        while True:
            acks.append(gbn.FMT.unpack(s.recv(4096))[1])
    except socket.timeout:
        pass
    s.close()
    return acks


def test_receiver_in_order_with_eof_and_bad_checksum():
    rx = gbn.Receiver()
    rx.start()
    pkts = [gbn.pack(1, 0, 2, b"hi"), gbn.pack(3, 0, 1, b"x"), gbn.pack(2, 0, 1, b"!", cksum=0),
            gbn.pack(2, 0, 1, b"!"), gbn.pack(3, 0, 0)]
    acks = _client(rx, pkts, delay=0.02)
    rx.stop()
    assert bytes(rx.output) == b"hi!" and rx.finished
    assert acks == [1, 1, 1, 2, 3]


def test_receiver_window_measure_with_slow_acks():
    rx = gbn.Receiver(gbn.Faults(ack_delay=0.05))
    rx.start()
    _client(rx, [gbn.pack(i, 0, 1, b"a") for i in range(1, 13)])
    rx.stop()
    assert rx.max_outstanding == 12  # all sent before any ACK went out


def test_scripted_drops():
    rx = gbn.Receiver(gbn.Faults(drop_data={1: 1}, drop_eof=1))
    rx.start()
    acks = _client(rx, [gbn.pack(1, 0, 1, b"a"), gbn.pack(1, 0, 1, b"a"), gbn.pack(2, 0, 0),
                        gbn.pack(2, 0, 0)], delay=0.02)
    rx.stop()
    assert [d.fate for d in rx.received] == ["lost", "ok", "lost", "ok"]
    assert acks == [1, 2] and rx.finished and bytes(rx.output) == b"a"
