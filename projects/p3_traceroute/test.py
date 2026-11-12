"""Project 3: Traceroute. Black-box tests of the student's ``traceroute``.

The program runs against a simulated network (see sim.py / tracesim.c): its
raw sockets are faked, every datagram it sends is recorded, and routers /
the destination answer according to a scenario. Tests grade both the packets
sent and what the program prints.
"""

from __future__ import annotations

import pytest

from autograder.common_tests import test_build  # noqa: F401  (graded build step)

from . import sim

HIDDEN = "after_published"


def _fail_if(problems: list[str], trace: sim.Trace, score, show_sends: bool = True) -> None:
    if show_sends:
        score.log("Datagrams your program sent:\n" + trace.describe_sends())
    score.log(trace.transcript())
    if trace.result.timed_out:
        score.log(f"Note: {trace.result.describe(tail=0)}")
    if problems:
        pytest.fail("\n".join(problems), pytrace=False)


def _check_ttls(trace: sim.Trace) -> tuple[float, list[str]]:
    expected, got = trace.scenario.expected_ttls, trace.ttls
    if got == expected:
        return 1.0, []
    msg = f"Expected TTLs {expected[0]}..{expected[-1]} ({len(expected)} datagrams), got {got[:40]}"
    if not got:
        return 0.0, ["Your program sent no datagrams."]
    steps_ok = all(isinstance(b, int) and isinstance(a, int) and b == a + 1 for a, b in zip(got, got[1:]))
    if steps_ok and got[0] != sim.FIRST_TTL:
        return 0.5, [msg, f"The first datagram must have TTL {sim.FIRST_TTL} (yours had {got[0]})."]
    matched = sum(1 for a, b in zip(expected, got) if a == b)
    return matched / len(expected) * 0.5, [msg]


def _routers_check(trace: sim.Trace) -> tuple[float, list[str]]:
    expected = trace.scenario.expected_routers
    found = max(sim.routers_found(expected, trace.output), sim.routers_found(expected, trace.all_output))
    if found == len(expected):
        return 1.0, []
    printed = set(sim.ips_in(trace.all_output))
    missing = [ip for ip in expected if ip not in printed]
    problems = [f"Printed {found}/{len(expected)} router addresses in the right order."]
    if missing:
        problems.append("Never printed: " + ", ".join(missing[:10]) + (" ..." if len(missing) > 10 else ""))
    return found / len(expected), problems


# ------------------------------------------------------------- basic path

@pytest.mark.gs(weight=10, number="1.1", visibility="visible",
                name="Basic path: sends well-formed ICMP Echo Requests in hand-built IPv4 datagrams")
def test_packet_format(trace, score):
    t = trace("basic")
    sends = t.sends
    if not sends:
        _fail_if(["Your program sent no datagrams on a raw socket."], t, score)
    ok = [s for s in sends if s.get("ip_ok")]
    checks = {
        "IPv4 header built by your program (IPPROTO_RAW socket or IP_HDRINCL)":
            all(s.get("built_ip") for s in sends),
        "valid IPv4 header (version 4, header length >= 20 bytes)": len(ok) == len(sends),
        "IP protocol field is 1 (ICMP)": bool(ok) and all(s["proto"] == 1 for s in ok),
        f"destination address is the -t target ({t.scenario.dest})":
            bool(ok) and all(s["dst"] == t.scenario.dest for s in ok),
        "ICMP type 8 (Echo Request), code 0": bool(ok) and all(s["type"] == 8 and s["code"] == 0 for s in ok),
        "ICMP checksum correct on every datagram": bool(ok) and all(s["cksum_ok"] for s in ok),
    }
    score.set_fraction(sum(checks.values()) / len(checks))
    score.log("\n".join(f"[{'ok' if v else 'FAIL'}] {k}" for k, v in checks.items()))
    _fail_if([f"Failed: {k}" for k, v in checks.items() if not v], t, score)


@pytest.mark.gs(weight=10, number="1.2", visibility="visible",
                name="Basic path: first TTL is 2 and each datagram's TTL is one more than the last")
def test_ttl_sequence(trace, score):
    t = trace("basic")
    frac, problems = _check_ttls(t)
    score.set_fraction(frac)
    _fail_if(problems, t, score)


@pytest.mark.gs(weight=15, number="1.3", visibility="visible",
                name="Basic path: prints the address of each router that sends TTL Exceeded, in order")
def test_prints_routers(trace, score):
    t = trace("basic")
    frac, problems = _routers_check(t)
    score.set_fraction(frac)
    score.log("Expected routers (TTL 2 onward): " + ", ".join(t.scenario.expected_routers))
    _fail_if(problems, t, score, show_sends=False)


@pytest.mark.gs(weight=10, number="1.4", visibility="visible",
                name="Basic path: stops sending and exits after the Echo Reply")
def test_exits_after_reply(trace, score):
    t = trace("basic")
    problems = []
    if t.reply_time is None:
        problems.append("Your program never got an Echo Reply from the destination "
                        f"(it should after sending TTL {t.scenario.expected_ttls[-1]}).")
        score.set(0)
    else:
        if t.sends_after_reply:
            problems.append(f"Sent {len(t.sends_after_reply)} more datagram(s) after the Echo Reply arrived.")
        if t.result.timed_out:
            problems.append("Did not exit after the Echo Reply arrived.")
        score.set_fraction(1 - 0.5 * len(problems))
    _fail_if(problems, t, score)


# ------------------------------------------------------------ silent hop

@pytest.mark.gs(weight=10, number="2.1", visibility="visible",
                name='Silent router: prints "No response with TTL of 3"')
def test_silent_message(trace, score):
    t = trace("silent")
    found = sim.no_response_ttls(t.all_output)
    if 3 in found and set(found) == {3}:
        score.log(t.transcript())
        return
    score.set_fraction(0.5 if 3 in found else 0)
    problem = ('Expected the line "No response with TTL of 3" (the router at TTL 3 never answers).'
               if 3 not in found else
               f'Printed "No response" for TTLs {sorted(set(found))}, but only TTL 3 went unanswered.')
    _fail_if([problem], t, score, show_sends=False)


@pytest.mark.gs(weight=5, number="2.2", visibility="visible",
                name="Silent router: waits at least 15 seconds before sending the next datagram")
def test_silent_wait(trace, score):
    t = trace("silent")
    times = {s.get("ttl"): s["t"] for s in t.sends}
    if 3 not in times or 4 not in times:
        _fail_if(["Never sent both the TTL 3 and TTL 4 datagrams, so the wait could not be measured."], t, score)
    gap = times[4] - times[3]
    score.log(f"Waited {gap:.1f} s after the unanswered TTL 3 datagram.")
    if gap < sim.SILENT_WAIT - 0.5:
        _fail_if([f"Moved on after {gap:.1f} s; the spec requires waiting at least 15 s for a response."],
                 t, score, show_sends=False)


@pytest.mark.gs(weight=5, number="2.3", visibility="visible",
                name="Silent router: keeps going after the silent hop and reaches the destination")
def test_silent_continues(trace, score):
    t = trace("silent")
    ttl_frac, problems = _check_ttls(t)
    r_frac, r_problems = _routers_check(t)
    reached = t.reply_time is not None and not t.sends_after_reply
    if not reached:
        problems.append("Did not finish the trace with the destination's Echo Reply.")
    score.set_fraction((ttl_frac + r_frac + reached) / 3)
    _fail_if(problems + r_problems, t, score)


# ------------------------------------------------------- hidden scenarios

@pytest.mark.gs(weight=10, number="3.1", visibility=HIDDEN,
                name="Unreachable destination: sends exactly 30 datagrams (TTL 2-31) and stops")
def test_thirty_limit(trace, score):
    t = trace("unreachable")
    frac, problems = _check_ttls(t)
    if t.result.timed_out:
        problems.append("Did not exit after sending 30 datagrams.")
        frac *= 0.5
    score.set_fraction(frac)
    _fail_if(problems, t, score)


@pytest.mark.gs(weight=5, number="3.2", visibility=HIDDEN,
                name="Unreachable destination: prints all 30 routers")
def test_thirty_routers(trace, score):
    t = trace("unreachable")
    frac, problems = _routers_check(t)
    score.set_fraction(frac)
    _fail_if(problems, t, score, show_sends=False)


@pytest.mark.gs(weight=10, number="4.1", visibility=HIDDEN,
                name="Ignores unrelated ICMP (echo replies/requests from other hosts, Destination Unreachable)")
def test_noise(trace, score):
    t = trace("noise")
    r_frac, problems = _routers_check(t)
    ttl_frac, ttl_problems = _check_ttls(t)
    problems += ttl_problems
    leaked = sorted(set(sim.ips_in(t.output)) & set(sim.NOISE_IPS))
    if leaked:
        problems.append("Printed addresses of unrelated ICMP senders as if they were routers: " + ", ".join(leaked))
    score.set_fraction(0.5 * r_frac + 0.25 * ttl_frac + 0.25 * (not leaked))
    _fail_if(problems, t, score)


@pytest.mark.gs(weight=5, number="4.2", visibility=HIDDEN,
                name="Two silent routers (TTL 2 and 5): reports both and completes the trace")
def test_two_silent(trace, score):
    t = trace("two_silent")
    found = set(sim.no_response_ttls(t.all_output))
    problems = []
    msg_ok = found == {2, 5}
    if not msg_ok:
        problems.append(f'Expected "No response with TTL of X" for TTLs 2 and 5; found it for {sorted(found)}.')
    r_frac, r_problems = _routers_check(t)
    reached = t.reply_time is not None and not t.sends_after_reply
    if not reached:
        problems.append("Did not finish the trace with the destination's Echo Reply.")
    score.set_fraction((msg_ok + r_frac + reached) / 3)
    _fail_if(problems + r_problems, t, score)


# ---------------------------------------------------------------- verbose

@pytest.mark.gs(weight=5, number="5.1", visibility="visible",
                name="-d DEBUG_LEVEL changes how much debugging output is printed")
def test_debug_flag(trace, executables, project, shim, score):
    base = trace("basic")
    if base.reply_time is None:
        pytest.fail("Not run: the basic trace must work first (see the tests above).", pytrace=False)
    exe = executables[project.executables[0]]
    counts = {"(no -d)": len(base.all_output.splitlines())}
    broken = []
    for level in ("0", "1", "2", "3", "4", "5", "6"):
        r = sim.run(exe, shim, base.scenario, ["-t", base.scenario.dest, "-d", level], tag=f"-d{level}", timeout=20)
        counts[f"-d {level}"] = len(r.all_output.splitlines())
        if r.ttls != base.scenario.expected_ttls or r.result.timed_out:
            broken.append(level)
        if max(counts.values()) - min(counts.values()) >= 2 and not broken:
            break
    score.log("Lines of output (stdout + stderr): " + ", ".join(f"{k}: {v}" for k, v in counts.items()))
    varies = max(counts.values()) - min(counts.values()) >= 2
    if broken:
        score.set_fraction(0.5 if varies else 0)
        pytest.fail(f"The trace did not work correctly with -d {', -d '.join(broken)}.", pytrace=False)
    if not varies:
        score.set_fraction(0.5)
        pytest.fail("-d is accepted, but no debug level changed the amount of output.", pytrace=False)
