"""Project 3 grader internals: output parsing and scenario expectations.

The end-to-end behaviour (shim + student program) needs Linux; see
dev/selfcheck.py p3_traceroute.
"""

from p3_traceroute import sim


def test_ips_in_exact_tokens():
    text = "2  172.16.2.1  0.5 ms\nfrom 172.16.3.1.\nversion 1.2.3.4.5 not an ip; 10.0.0.10"
    assert sim.ips_in(text) == ["172.16.2.1", "172.16.3.1", "10.0.0.10"]


def test_no_response_variants():
    text = "No response with TTL of 3\nno  response with ttl of 12\nNo Response With TTL 7\nnothing here"
    assert sim.no_response_ttls(text) == [3, 12, 7]


def test_routers_found_requires_order():
    expected = ["10.0.2.1", "10.0.3.1", "10.0.4.1"]
    assert sim.routers_found(expected, "10.0.2.1\n10.0.3.1\n10.0.4.1") == 3
    assert sim.routers_found(expected, "10.0.4.1\n10.0.3.1\n10.0.2.1") == 1
    assert sim.routers_found(expected, "10.0.2.1 junk 9.9.9.9\n10.0.4.1") == 2


def test_scenario_expectations():
    s = sim.SCENARIOS
    assert s["basic"].expected_ttls == [2, 3, 4, 5, 6]
    assert s["basic"].expected_routers == ["172.16.2.1", "172.16.3.1", "172.16.4.1", "172.16.5.1"]
    assert s["silent"].silent_ttls == [3]
    assert s["two_silent"].silent_ttls == [2, 5]
    assert s["unreachable"].expected_ttls == list(range(2, 32))
    assert len(s["unreachable"].expected_routers) == 30
    for scenario in s.values():
        ips = {scenario.dest, *scenario.expected_routers}
        assert not ips & set(sim.NOISE_IPS)
