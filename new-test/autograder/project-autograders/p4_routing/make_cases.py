"""(Re)generate the authored test cases for Project 4.

    cd project-autograders && python -m p4_routing.make_cases

Writes cases/<name>/{interfaces,routes,input,output}.txt and meta.json.
sample1/ and sample2/ (from the assignment handout) are not touched.
Review the generated output.txt files before publishing. They are what
students are graded against.
"""

from __future__ import annotations

import ipaddress
import json
import random
from pathlib import Path

from .reference import expected_for_case

CASES_DIR = Path(__file__).parent / "cases"

HAND_CASES = {
    "nested_interfaces": {
        "description": "interface subnet nested inside another interface's subnet",
        "interfaces": """\
# The handout's example: eth1's /24 lives inside eth0's /22
eth0 138.67.20.3/22
eth1 138.67.20.254/24
eth2 10.1.1.1/30
""",
        "routes": """\
172.16.0.0/12 138.67.20.100
172.16.5.0/24 138.67.22.1
0.0.0.0/0 10.1.1.2
""",
        "input": """\
138.67.20.9
138.67.21.5
138.67.23.255
138.67.24.1
172.20.1.1
172.16.5.77
10.1.1.2
10.1.1.4
""",
    },
    "longest_prefix": {
        "description": "overlapping routes listed out of order (default route first)",
        "interfaces": """\
eth0 192.0.2.1/24
eth1 198.51.100.1/24
eth2 203.0.113.1/25
eth3 203.0.113.129/25
""",
        "routes": """\
0.0.0.0/0 192.0.2.254
10.0.0.0/8 198.51.100.2
10.20.30.40/32 203.0.113.130
10.20.0.0/16 192.0.2.10
10.20.30.0/24 203.0.113.5
10.128.0.0/9 198.51.100.3
""",
        "input": """\
10.1.2.3
10.20.1.1
10.20.30.1
10.20.30.40
10.20.30.41
10.200.0.1
11.0.0.1
203.0.113.127
203.0.113.128
8.8.4.4
""",
    },
    "unreachable": {
        "description": "no default route: unreachable destinations",
        "interfaces": """\
lan0 192.168.1.1/24
lan1 192.168.2.1/24
""",
        "routes": """\
10.0.0.0/8 192.168.1.50
172.16.0.0/16 192.168.2.50
""",
        "input": """\
10.9.9.9
172.16.3.4
172.17.0.1
192.168.1.77
192.168.3.1
8.8.8.8
""",
    },
    "whitespace_comments": {
        "description": "tabs, leading/trailing whitespace, and indented comments",
        "interfaces": "\t# uplink\n  wan0\t\t100.64.0.1/10   \n\t\tlan0 \t 192.168.50.1/24\t\n   #lan0 192.168.99.1/24\n",
        "routes": "# comment with no space\n#\n\t0.0.0.0/0\t100.64.0.2  \n  192.168.60.0/23   \t192.168.50.254\n",
        "input": "  192.168.50.10\n\t192.168.61.1\t\n   # 1.2.3.4\n8.8.8.8   \n100.100.100.100\n",
    },
    "connected_only": {
        "description": "route table containing only comments",
        "interfaces": """\
eth0 10.0.0.1/8
eth1 10.1.0.1/16
eth2 10.1.1.1/24
""",
        "routes": """\
# No static routes on this router.
#
""",
        "input": """\
10.200.1.1
10.1.200.1
10.1.1.200
11.0.0.1
""",
    },
}

RANDOM_CASES = {
    "random_default": dict(seed=471, default=True, description="large randomized table with default route"),
    "random_no_default": dict(seed=598, default=False, description="large randomized table without default route"),
}


def _rand_net(rng: random.Random, lo: int, hi: int) -> ipaddress.IPv4Network:
    prefix = rng.randint(lo, hi)
    base = rng.choice([10 << 24, (172 << 24) | (16 << 16), (192 << 24) | (168 << 16), rng.getrandbits(32)])
    span_bits = {10 << 24: 24, (172 << 24) | (16 << 16): 20, (192 << 24) | (168 << 16): 16}.get(base, 32)
    addr = base | rng.getrandbits(span_bits) if span_bits < 32 else base
    return ipaddress.ip_network((addr, prefix), strict=False)


def _rand_host(rng: random.Random, net: ipaddress.IPv4Network) -> ipaddress.IPv4Address:
    if net.num_addresses <= 2:
        return net.network_address + rng.randrange(net.num_addresses)
    return net.network_address + rng.randint(1, net.num_addresses - 2)


def random_case(seed: int, default: bool, n_ifaces: int = 8, n_routes: int = 40, n_dests: int = 150) -> dict:
    """Random but valid config: no two entries share a network (so no ties) and
    every next hop sits on a connected subnet."""
    rng = random.Random(seed)
    used: set[ipaddress.IPv4Network] = set()
    ifaces = []
    while len(ifaces) < n_ifaces:
        net = _rand_net(rng, 16, 30)
        if net not in used:
            used.add(net)
            ifaces.append((f"eth{len(ifaces)}", _rand_host(rng, net), net))
    routes = []
    while len(routes) < n_routes:
        net = _rand_net(rng, 8, 32)
        # Skip routes more specific than a connected subnet: the spec doesn't say
        # whether connected or longest-prefix wins there, so don't grade it.
        if net not in used and not any(net.subnet_of(n) for _, _, n in ifaces):
            used.add(net)
            routes.append((net, _rand_host(rng, rng.choice(ifaces)[2])))
    if default:
        routes.insert(rng.randrange(len(routes) + 1), (ipaddress.ip_network("0.0.0.0/0"), _rand_host(rng, ifaces[0][2])))
    dests = []
    for _ in range(n_dests):
        r = rng.random()
        if r < 0.35:
            dests.append(_rand_host(rng, rng.choice(ifaces)[2]))
        elif r < 0.8:
            dests.append(_rand_host(rng, rng.choice(routes)[0]) if routes else ipaddress.ip_address(rng.getrandbits(32)))
        else:
            dests.append(ipaddress.ip_address(rng.getrandbits(32)))
    return {
        "interfaces": "".join(f"{n} {ip}/{net.prefixlen}\n" for n, ip, net in ifaces),
        "routes": "".join(f"{net} {hop}\n" for net, hop in routes),
        "input": "".join(f"{d}\n" for d in dests),
    }


def write_case(name: str, files: dict, meta: dict) -> None:
    d = CASES_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    for key in ("interfaces", "routes", "input"):
        (d / f"{key}.txt").write_text(files[key])
    (d / "output.txt").write_text("\n".join(expected_for_case(d)) + "\n")
    (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"wrote {d}")


def main() -> None:
    for name, spec in HAND_CASES.items():
        write_case(name, spec, {"description": spec["description"], "weight": 10, "visibility": "after_published"})
    for name, spec in RANDOM_CASES.items():
        files = random_case(spec["seed"], spec["default"])
        write_case(name, files, {"description": spec["description"], "weight": 10, "visibility": "after_published"})
    for name, desc in (("sample1", "handout sample 1"), ("sample2", "handout sample 2")):
        meta = CASES_DIR / name / "meta.json"
        if not meta.exists():
            meta.write_text(json.dumps({"description": desc, "weight": 10, "visibility": "visible"}, indent=2) + "\n")


if __name__ == "__main__":
    main()
