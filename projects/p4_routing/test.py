"""Project 4: Routing. Black-box tests of the student's ``router`` executable.

Each directory under cases/ is one graded test: interfaces.txt, routes.txt,
input.txt, output.txt (expected) and optional meta.json with
{"description", "weight", "visibility"}. Partial credit is per output line.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from autograder import proc
from autograder.common_tests import test_build  # noqa: F401  (graded build step)

from .compare import compare, output_lines, parseable_lines

CASES_DIR = Path(__file__).parent / "cases"
TIMEOUT = 10
CASE_FILES = ("interfaces.txt", "routes.txt", "input.txt")


@dataclass
class Case:
    name: str
    path: Path
    description: str
    weight: float
    visibility: str

    @property
    def expected(self) -> list[str]:
        return output_lines((self.path / "output.txt").read_text())

    def stage(self, workdir: Path) -> None:
        for f in CASE_FILES:
            shutil.copy(self.path / f, workdir / f)


def load_cases() -> list[Case]:
    cases = []
    for d in sorted(p for p in CASES_DIR.iterdir() if (p / "output.txt").exists()):
        meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
        cases.append(Case(
            name=d.name,
            path=d,
            description=meta.get("description", d.name),
            weight=meta.get("weight", 10),
            visibility=meta.get("visibility", "after_published"),
        ))
    # Visible (sample) cases first so students see them at the top.
    return sorted(cases, key=lambda c: (c.visibility != "visible", c.name))


CASES = load_cases()


def _case_param(i: int, case: Case):
    return pytest.param(case, id=case.name, marks=pytest.mark.gs(
        weight=case.weight,
        visibility=case.visibility,
        number=f"1.{i + 1}",
        name=f"Routing: {case.description}",
    ))


@pytest.mark.parametrize("case", [_case_param(i, c) for i, c in enumerate(CASES)])
def test_routing_case(case: Case, executable, workdir, score):
    case.stage(workdir)
    cmd = [executable, "-c", "interfaces.txt", "-r", "routes.txt", "-i", "input.txt", "-o", "output.txt", "-d", "0"]
    score.log("$ router -c interfaces.txt -r routes.txt -i input.txt -o output.txt -d 0")
    result = proc.run(cmd, cwd=workdir, timeout=TIMEOUT)

    if result.timed_out:
        pytest.fail(result.describe(), pytrace=False)
    out_file = workdir / "output.txt"
    if not out_file.is_file():
        pytest.fail(f"No output file was written (expected the file given with -o).\n{result.describe()}",
                    pytrace=False)

    cmp = compare(case.expected, output_lines(out_file.read_text(errors="replace")))
    score.set_fraction(cmp.fraction)
    if result.returncode != 0:
        score.log(f"Note: {result.describe()}")
    if cmp.mismatches or cmp.extra:
        score.log(cmp.details())
        pytest.fail(cmp.summary(), pytrace=False)
    score.log(cmp.summary())


@pytest.mark.gs(weight=5, number="2.1", visibility="visible",
                name="Defaults: reads stdin and writes stdout when -i/-o are omitted")
def test_stdin_stdout_defaults(executable, workdir, score):
    case = next(c for c in CASES if c.name == "sample1")
    case.stage(workdir)
    score.log("$ router -c interfaces.txt -r routes.txt < input.txt")
    result = proc.run([executable, "-c", "interfaces.txt", "-r", "routes.txt"], cwd=workdir, timeout=TIMEOUT,
                      stdin=(workdir / "input.txt").read_bytes())
    if result.timed_out:
        pytest.fail(result.describe(), pytrace=False)
    # Debug logging may share stdout at the default level, so only lines in the
    # output format are compared.
    cmp = compare(case.expected, parseable_lines(result.out))
    score.set_fraction(cmp.fraction)
    if cmp.mismatches or cmp.extra:
        score.log(cmp.details())
        pytest.fail(cmp.summary(), pytrace=False)
    score.log(cmp.summary())
