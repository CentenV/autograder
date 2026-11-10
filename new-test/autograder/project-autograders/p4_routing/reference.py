"""Reference router for Project 4, used to generate and check expected outputs.

    cd project-autograders && python -m p4_routing.reference CASE_DIR [--write]

prints (or writes CASE_DIR/output.txt) the expected output for a case directory
containing interfaces.txt, routes.txt and input.txt.

Semantics (from the assignment):
  * Lines are trimmed; blank lines and lines whose first non-whitespace char
    is '#' are ignored.
  * Directly connected subnets and static routes are searched together with
    longest-prefix match. On equal prefix length, connected subnets win, then
    earlier lines win.
  * Directly connected: next hop = destination.
  * Otherwise the egress interface is the longest-prefix interface subnet
    containing the next hop.
  * No match (only possible without a default route): "unreachable".
"""

from __future__ import annotations

import argparse
import ipaddress
from dataclasses import dataclass
from pathlib import Path


def content_lines(text: str) -> list[str]:
    lines = []
    for raw in text.split("\n"):
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


@dataclass(frozen=True)
class Interface:
    name: str
    network: ipaddress.IPv4Network


@dataclass(frozen=True)
class Route:
    network: ipaddress.IPv4Network
    next_hop: ipaddress.IPv4Address


def parse_interfaces(text: str) -> list[Interface]:
    out = []
    for line in content_lines(text):
        name, cidr = line.split()
        out.append(Interface(name, ipaddress.ip_interface(cidr).network))
    return out


def parse_routes(text: str) -> list[Route]:
    out = []
    for line in content_lines(text):
        cidr, hop = line.split()
        out.append(Route(ipaddress.ip_network(cidr, strict=False), ipaddress.ip_address(hop)))
    return out


def _egress(interfaces: list[Interface], addr: ipaddress.IPv4Address) -> Interface | None:
    best = None
    for iface in interfaces:
        if addr in iface.network and (best is None or iface.network.prefixlen > best.network.prefixlen):
            best = iface
    return best


def route_one(interfaces: list[Interface], routes: list[Route], dest: str) -> str:
    addr = ipaddress.ip_address(dest)
    connected = _egress(interfaces, addr)
    best_route = None
    for r in routes:
        if addr in r.network and (best_route is None or r.network.prefixlen > best_route.network.prefixlen):
            best_route = r
    if connected and (best_route is None or connected.network.prefixlen >= best_route.network.prefixlen):
        return f"{dest}: {connected.name} -> {dest}"
    if best_route is not None:
        iface = _egress(interfaces, best_route.next_hop)
        if iface is None:
            raise ValueError(f"next hop {best_route.next_hop} is not on a connected subnet")
        return f"{dest}: {iface.name} -> {best_route.next_hop}"
    return f"{dest}: unreachable"


def route_all(interfaces_text: str, routes_text: str, input_text: str) -> list[str]:
    interfaces = parse_interfaces(interfaces_text)
    routes = parse_routes(routes_text)
    return [route_one(interfaces, routes, d) for d in content_lines(input_text)]


def expected_for_case(case_dir: Path) -> list[str]:
    read = lambda n: (case_dir / n).read_text()  # noqa: E731
    return route_all(read("interfaces.txt"), read("routes.txt"), read("input.txt"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case_dirs", nargs="+", type=Path)
    ap.add_argument("--write", action="store_true", help="write output.txt instead of printing")
    args = ap.parse_args()
    for case in args.case_dirs:
        lines = expected_for_case(case)
        if args.write:
            (case / "output.txt").write_text("\n".join(lines) + "\n")
            print(f"wrote {case / 'output.txt'} ({len(lines)} lines)")
        else:
            print("\n".join(lines))


if __name__ == "__main__":
    main()
