"""pytest plugin that turns a pytest run into a Gradescope ``results.json``.

Usage in a test module::

    @pytest.mark.gs(weight=5, name="Serves file1.html", visibility="visible")
    def test_get(executable, score):
        ...

Marker keywords (all optional):
    weight      points for the test (default 0)
    name        display name; ``{id}`` is replaced by the parametrize id,
                otherwise the id is appended as `` [id]``. Defaults to the
                first docstring line, then the function name.
    number      Gradescope test number used for ordering, e.g. "1.3"
    visibility  "visible" | "hidden" | "after_due_date" | "after_published"
    tags        list of strings
    variants    course variants the test applies to, e.g. ("560",). Tests
                whose variants don't include --gs-variant are deselected.

``gs`` markers merge from module -> class -> function -> parametrize, so a
module-level ``pytestmark = pytest.mark.gs(visibility="after_published")``
sets defaults for every test in the file.

Scoring:
    * passed, no score set          -> full weight
    * failed / errored, no score set -> 0
    * ``score.set(...)`` called      -> that value, whether or not the test
                                        later fails (partial credit)
Only text logged with ``score.log`` and the failure message are shown to
students. Full tracebacks go to stdout, which only instructors see.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

MAX_TEST_OUTPUT = 10_000
MAX_TOP_OUTPUT = 10_000

INTERNAL_ERROR_MSG = (
    "The autograder hit an internal error while running this test. "
    "This is not necessarily a problem with your code; your instructor "
    "can see the details in the autograder log."
)


def truncate(text: str, limit: int = MAX_TEST_OUTPUT) -> str:
    """Keep the head and tail of long text so students see both ends."""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head - 80
    omitted = len(text) - head - tail
    return f"{text[:head]}\n\n... [{omitted} characters omitted] ...\n\n{text[-tail:]}"


class Score:
    """Per-test handle for partial credit and student-visible output."""

    def __init__(self, max_score: float):
        self.max_score = max_score
        self.points: float | None = None
        self.lines: list[str] = []

    def set(self, points: float) -> None:
        self.points = max(0.0, min(float(points), float(self.max_score)))

    def set_fraction(self, fraction: float) -> None:
        self.set(fraction * self.max_score)

    def log(self, *parts: object) -> None:
        self.lines.append(" ".join(str(p) for p in parts))


@dataclass
class _Entry:
    name: str
    meta: dict
    score: Score
    failures: list[str] = field(default_factory=list)
    failed: bool = False
    skipped: bool = False

    def to_json(self) -> dict:
        weight = float(self.meta.get("weight", 0))
        if self.skipped and not self.failed:
            points = 0.0
        elif self.score.points is not None:
            points = self.score.points
        else:
            points = 0.0 if self.failed else weight

        output = "\n\n".join(m for m in [*self.failures, *self.score.lines] if m)
        result = {
            "name": self.name,
            "score": round(points, 4),
            "max_score": weight,
            "status": "passed" if (not self.failed and not self.skipped and points >= weight) else "failed",
            "output": truncate(output),
        }
        for key in ("number", "visibility", "tags"):
            if self.meta.get(key) is not None:
                result[key] = list(self.meta[key]) if key == "tags" else self.meta[key]
        return result


@dataclass
class _State:
    results_path: Path | None
    variant: str | None
    start: float
    entries: dict[str, _Entry] = field(default_factory=dict)
    top_output: list[str] = field(default_factory=list)


_STATE = pytest.StashKey[_State]()
_SCORE = pytest.StashKey[Score]()


def gs_meta(item: pytest.Item) -> dict:
    """Merge gs marker kwargs from furthest node to closest (param marks last)."""
    meta: dict = {}
    for node in item.listchain():
        for mark in node.own_markers:
            if mark.name == "gs":
                meta.update(mark.kwargs)
    return meta


def _display_name(item: pytest.Item, meta: dict) -> str:
    name = meta.get("name")
    if not name:
        doc = getattr(getattr(item, "function", None), "__doc__", None)
        name = doc.strip().splitlines()[0] if doc and doc.strip() else item.name.split("[")[0]
    callspec = getattr(item, "callspec", None)
    if callspec is not None:
        name = name.replace("{id}", callspec.id) if "{id}" in name else f"{name} [{callspec.id}]"
    return name


def add_top_output(config: pytest.Config, text: str) -> None:
    """Add a message shown to students above all test results."""
    config.stash[_STATE].top_output.append(text)


def _score_for(item: pytest.Item) -> Score:
    if _SCORE not in item.stash:
        item.stash[_SCORE] = Score(float(gs_meta(item).get("weight", 0)))
    return item.stash[_SCORE]


@pytest.fixture
def score(request: pytest.FixtureRequest) -> Score:
    """Partial credit and student-visible logging for the current test."""
    return _score_for(request.node)


# --------------------------------------------------------------------------- hooks


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("gradescope")
    group.addoption("--gs-results", default=None, help="Write Gradescope results.json to this path.")
    group.addoption(
        "--gs-variant",
        default=os.environ.get("AUTOGRADER_VARIANT") or None,
        help="Course variant (e.g. 471 or 560). Tests marked for other variants are deselected.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "gs(weight=0, name=None, number=None, visibility=None, tags=None, variants=None): Gradescope metadata",
    )
    path = config.getoption("--gs-results")
    config.stash[_STATE] = _State(
        results_path=Path(path) if path else None,
        variant=config.getoption("--gs-variant"),
        start=time.monotonic(),
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    variant = config.stash[_STATE].variant
    keep, drop = [], []
    for item in items:
        variants = gs_meta(item).get("variants")
        if variants is not None and (variant is None or str(variant) not in {str(v) for v in variants}):
            drop.append(item)
        else:
            keep.append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


def _failure_message(call: pytest.CallInfo, phase: str) -> tuple[str, bool]:
    """Return (student-facing message, is_skip)."""
    excinfo = call.excinfo
    if excinfo.errisinstance(pytest.skip.Exception):
        return f"Skipped: {excinfo.value.msg}", True
    if excinfo.errisinstance(pytest.fail.Exception):
        return str(excinfo.value.msg), False
    if excinfo.errisinstance(AssertionError):
        return str(excinfo.value) or "Assertion failed.", False
    if phase == "teardown":
        return f"{INTERNAL_ERROR_MSG} (cleanup: {excinfo.typename})", False
    return f"{INTERNAL_ERROR_MSG} ({excinfo.typename})", False


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    report = yield
    state = item.config.stash[_STATE]
    entry = state.entries.get(item.nodeid)
    if entry is None:
        meta = gs_meta(item)
        entry = state.entries[item.nodeid] = _Entry(_display_name(item, meta), meta, _score_for(item))
    if call.excinfo is not None and not hasattr(report, "wasxfail"):
        message, is_skip = _failure_message(call, call.when)
        if is_skip:
            entry.skipped = True
        else:
            entry.failed = True
        entry.failures.append(message)
    return report


def _build_results(session: pytest.Session, exitstatus: int) -> dict:
    state = session.config.stash[_STATE]
    tests = [e.to_json() for e in state.entries.values()]
    results: dict = {
        "execution_time": int(time.monotonic() - state.start),
        "stdout_visibility": "hidden",
        "tests": tests,
    }
    broken = exitstatus in (
        pytest.ExitCode.INTERRUPTED,
        pytest.ExitCode.INTERNAL_ERROR,
        pytest.ExitCode.USAGE_ERROR,
        pytest.ExitCode.NO_TESTS_COLLECTED,
    )
    top = list(state.top_output)
    if broken:
        top.insert(0, "The autograder did not run to completion. Your instructor has been notified; "
                      "please let them know as well.")
        if not tests:
            results["score"] = 0
    if top:
        results["output"] = truncate("\n\n".join(top), MAX_TOP_OUTPUT)
    return results


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    state = session.config.stash.get(_STATE, None)
    if state is None or state.results_path is None:
        return
    results = _build_results(session, exitstatus)
    state.results_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state.results_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(results, indent=2))
    os.replace(tmp, state.results_path)


def pytest_terminal_summary(terminalreporter, exitstatus: int, config: pytest.Config) -> None:
    state = config.stash.get(_STATE, None)
    if state is None or state.results_path is None or not state.entries:
        return
    tr = terminalreporter
    tr.section("gradescope scores")
    total = earned = 0.0
    for entry in state.entries.values():
        data = entry.to_json()
        total += data["max_score"]
        earned += data["score"]
        mark = "PASS" if data["status"] == "passed" else "FAIL"
        tr.write_line(f"{mark} {data['score']:>6g}/{data['max_score']:<6g} {data['name']}")
    tr.write_line(f"TOTAL {earned:g}/{total:g}")
