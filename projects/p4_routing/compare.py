"""Line-by-line comparison of router output against expected output.

The spec's grammar is ``destinationIP:<SP+>InterfaceName<SP+>-><SP+>nextHop``
or ``destinationIP:<SP+>unreachable``, so any run of spaces/tabs is accepted
between tokens. Lines are compared positionally (one output line per input
line, in order).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_IP = r"\d{1,3}(?:\.\d{1,3}){3}"
LINE_RE = re.compile(rf"^\s*({_IP}):\s+(?:(unreachable)|(\S+)\s+->\s+({_IP}))\s*$")


def normalize(line: str) -> str:
    """Canonical form of an output line; unparseable lines just get whitespace collapsed."""
    m = LINE_RE.match(line)
    if not m:
        return " ".join(line.split())
    dest, unreachable, iface, hop = m.groups()
    return f"{dest}: unreachable" if unreachable else f"{dest}: {iface} -> {hop}"


def output_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def parseable_lines(text: str) -> list[str]:
    """Only lines in the output format (for stdout runs where debug output may be mixed in)."""
    return [line for line in text.splitlines() if LINE_RE.match(line)]


@dataclass
class Comparison:
    expected: list[str]
    actual: list[str]
    mismatches: list[tuple[int, str, str | None]] = field(default_factory=list)  # (index, expected, actual)
    extra: list[str] = field(default_factory=list)

    @property
    def correct(self) -> int:
        return len(self.expected) - len(self.mismatches)

    @property
    def fraction(self) -> float:
        if not self.expected:
            return 1.0 if not self.actual else 0.0
        # Extra lines cost one expected line each so spamming output can't help.
        return max(0.0, (self.correct - len(self.extra)) / len(self.expected))

    def summary(self) -> str:
        s = f"{self.correct}/{len(self.expected)} lines correct"
        if self.extra:
            s += f", {len(self.extra)} unexpected extra line(s)"
        return s

    def details(self, limit: int = 15) -> str:
        out = []
        for i, exp, act in self.mismatches[:limit]:
            out.append(f"line {i + 1}:\n  expected: {exp}\n  got:      {act if act is not None else '(missing)'}")
        if len(self.mismatches) > limit:
            out.append(f"... and {len(self.mismatches) - limit} more incorrect line(s)")
        for line in self.extra[:5]:
            out.append(f"extra line: {line}")
        return "\n".join(out)


def compare(expected: list[str], actual: list[str]) -> Comparison:
    c = Comparison(expected, actual)
    for i, exp in enumerate(expected):
        act = actual[i] if i < len(actual) else None
        if act is None or normalize(act) != normalize(exp):
            c.mismatches.append((i, exp, act))
    c.extra = actual[len(expected):]
    return c
