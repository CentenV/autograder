"""Simulated networks for Project 3 and helpers to run/inspect a traceroute.

The student's ``traceroute`` runs with ``tracesim.so`` preloaded (see
tracesim.c), which fakes raw sockets: every datagram it sends is logged and
answered according to a :class:`Scenario`. The log plus the program's output
are what the tests grade.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from autograder import config, proc

SHIM_SOURCE = Path(__file__).parent / "tracesim.c"
NOISE_IPS = ("198.51.100.77", "198.51.100.88", "198.51.100.23", "198.51.100.99")
FIRST_TTL = 2
LAST_TTL = 31  # 30 datagrams: TTL 2..31
SILENT_WAIT = 15.0

_IP_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?!\.?\d)")
_NO_RESPONSE_RE = re.compile(r"no\s+response\s+with\s+(?:a\s+)?ttl\s+(?:of\s+)?(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class Scenario:
    name: str
    dest: str
    hops: tuple[str, ...]          # router for TTL 1..N, "*" = silent
    dest_replies: bool = True
    noise: bool = False
    timeout: float = 40.0

    def router(self, ttl: int) -> str | None:
        return self.hops[ttl - 1] if ttl <= len(self.hops) else None

    @property
    def expected_ttls(self) -> list[int]:
        last = LAST_TTL if not self.dest_replies else min(len(self.hops) + 1, LAST_TTL)
        return list(range(FIRST_TTL, last + 1))

    @property
    def expected_routers(self) -> list[str]:
        """Router addresses the program should print, in order."""
        return [r for t in self.expected_ttls if (r := self.router(t)) and r != "*"]

    @property
    def silent_ttls(self) -> list[int]:
        return [t for t in self.expected_ttls if self.router(t) == "*"]

    def env(self) -> dict:
        return {
            "TRACESIM_DEST": self.dest,
            "TRACESIM_HOPS": ",".join(self.hops),
            "TRACESIM_DEST_REPLIES": "1" if self.dest_replies else "0",
            "TRACESIM_NOISE": "1" if self.noise else "0",
        }


def _routers(prefix: str, n: int) -> tuple[str, ...]:
    return tuple(f"{prefix}.{i}.1" for i in range(1, n + 1))


SCENARIOS = {s.name: s for s in [
    # 5 routers, destination at hop 6: TTL 2..6.
    Scenario("basic", "203.0.113.50", _routers("172.16", 5)),
    # Router at hop 3 never answers.
    Scenario("silent", "198.18.7.9", ("10.20.1.1", "10.20.2.1", "*", "10.20.4.1"), timeout=60),
    # Silent at the very first probe (TTL 2) and again at TTL 5.
    Scenario("two_silent", "203.0.113.200",
             ("10.30.1.1", "*", "10.30.3.1", "10.30.4.1", "*", "10.30.6.1"), timeout=80),
    # Destination never answers and the path is longer than 31 hops.
    Scenario("unreachable", "192.0.2.200", _routers("10.40", 40), dest_replies=False, timeout=60),
    # Unrelated ICMP arrives before every real response.
    Scenario("noise", "203.0.113.77", _routers("10.50", 6), noise=True),
]}


# ---------------------------------------------------------------- shim build

def build_shim() -> Path:
    """Compile tracesim.so somewhere the (unprivileged) student process can read."""
    out_dir = config.work_root() / "tracesim"
    out_dir.mkdir(exist_ok=True)
    out_dir.chmod(0o755)
    out = out_dir / "tracesim.so"
    subprocess.run(["gcc", "-shared", "-fPIC", "-O2", "-Wall", "-o", str(out), str(SHIM_SOURCE), "-ldl"],
                   check=True, capture_output=True, text=True)
    out.chmod(0o755)
    return out


# ------------------------------------------------------------------ running

@dataclass
class Trace:
    scenario: Scenario
    args: list[str]
    result: proc.RunResult
    events: list[dict] = field(default_factory=list)

    @property
    def shim_loaded(self) -> bool:
        return any(e.get("ev") == "init" for e in self.events)

    @cached_property
    def sends(self) -> list[dict]:
        return [e for e in self.events if e.get("ev") == "send"]

    @property
    def ttls(self) -> list[int | None]:
        return [s.get("ttl") for s in self.sends]

    @cached_property
    def reply_time(self) -> float | None:
        for e in self.events:
            if e.get("ev") == "deliver" and e.get("kind") == "echo_reply":
                return e["t"]
        return None

    @property
    def sends_after_reply(self) -> list[dict]:
        if self.reply_time is None:
            return []
        return [s for s in self.sends if s["t"] > self.reply_time]

    @property
    def output(self) -> str:
        return self.result.out

    @property
    def all_output(self) -> str:
        return self.result.out + "\n" + self.result.err

    def command(self) -> str:
        return " ".join(["traceroute", *self.args])

    def describe_sends(self, limit: int = 40) -> str:
        lines = []
        for s in self.sends[:limit]:
            if not s.get("ip_ok"):
                lines.append(f"  [{len(lines) + 1}] malformed IP header (version={s.get('ver')}, "
                             f"header length={s.get('ihl')} bytes, {s.get('len')} bytes total)")
                continue
            lines.append(f"  [{len(lines) + 1}] TTL={s['ttl']} proto={s['proto']} dst={s['dst']} "
                         f"ICMP type={s['type']} code={s['code']} checksum={'ok' if s['cksum_ok'] else 'BAD'}")
        if len(self.sends) > limit:
            lines.append(f"  ... {len(self.sends) - limit} more")
        return "\n".join(lines) if lines else "  (none)"

    def transcript(self, limit: int = 3000) -> str:
        out = self.output.strip() or "(no output on stdout)"
        if len(out) > limit:
            out = out[:limit] + "\n... [truncated]"
        return f"$ {self.command()}\n{out}"


def run(executable: Path, shim: Path, scenario: Scenario, args: list[str] | None = None,
        tag: str = "", timeout: float | None = None) -> Trace:
    args = ["-t", scenario.dest] if args is None else args
    workdir = config.fresh_dir(config.work_root() / "runs" / f"{scenario.name}{tag}")
    log = workdir / "tracesim.log"
    env = {**scenario.env(), "LD_PRELOAD": str(shim), "TRACESIM_LOG": str(log)}
    result = proc.run([executable, *args], cwd=workdir, timeout=timeout or scenario.timeout, env=env)
    events = []
    if log.exists():
        for line in log.read_text(errors="replace").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return Trace(scenario, args, result, events)


# ---------------------------------------------------------- output analysis

def ips_in(text: str) -> list[str]:
    return _IP_RE.findall(text)


def no_response_ttls(text: str) -> list[int]:
    return [int(m) for m in _NO_RESPONSE_RE.findall(text)]


def lcs_len(a: list, b: list) -> int:
    """Length of the longest common subsequence (in-order matches)."""
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def routers_found(expected: list[str], text: str) -> int:
    """How many expected router addresses appear in the output, in order."""
    return lcs_len(expected, ips_in(text))
