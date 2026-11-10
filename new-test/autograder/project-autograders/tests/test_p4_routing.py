"""Project 4 grader internals: reference router agrees with every committed
expected output, and the comparison logic is lenient only where the spec is."""

from pathlib import Path

import pytest

from p4_routing.compare import compare, normalize
from p4_routing.reference import expected_for_case

CASES = sorted(p for p in (Path(__file__).parent.parent / "p4_routing/cases").iterdir() if p.is_dir())


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_reference_matches_expected(case):
    expected = [l for l in (case / "output.txt").read_text().splitlines() if l.strip()]
    assert expected_for_case(case) == expected


@pytest.mark.parametrize("line", [
    "1.2.3.4: eth0 -> 5.6.7.8",
    "1.2.3.4:\teth0\t->\t5.6.7.8",
    "  1.2.3.4:   eth0   ->   5.6.7.8  ",
])
def test_whitespace_tolerance(line):
    assert normalize(line) == "1.2.3.4: eth0 -> 5.6.7.8"


def test_not_tolerated():
    assert normalize("1.2.3.4 : eth0 -> 5.6.7.8") != "1.2.3.4: eth0 -> 5.6.7.8"
    assert normalize("1.2.3.4: eth0 => 5.6.7.8") != "1.2.3.4: eth0 -> 5.6.7.8"


def test_partial_and_extra_lines():
    exp = ["1.1.1.1: unreachable", "2.2.2.2: e0 -> 2.2.2.2"]
    assert compare(exp, exp).fraction == 1
    assert compare(exp, exp[:1]).fraction == 0.5
    assert compare(exp, exp + ["junk"]).fraction == 0.5
    assert compare(exp, []).fraction == 0
