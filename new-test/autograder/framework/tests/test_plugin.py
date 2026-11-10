"""Tests for framework.plugin: scoring rules and results.json shape."""

import json

import pytest

TEST_MODULE = '''
import pytest

@pytest.mark.gs(weight=5, number="1", visibility="visible")
def test_pass():
    """Passing test"""

@pytest.mark.gs(weight=5)
def test_fail():
    assert 1 == 2, "student-facing message"

@pytest.mark.gs(weight=10, name="Partial")
def test_partial(score):
    score.log("detail line")
    score.set_fraction(0.25)
    pytest.fail("3 of 4 wrong", pytrace=False)

@pytest.mark.gs(weight=10, name="Partial then pass")
def test_partial_pass(score):
    score.set(7)

@pytest.mark.gs(weight=2)
def test_internal_error():
    raise KeyError("boom")

@pytest.fixture
def broken():
    pytest.fail("Not run: build failed", pytrace=False)

@pytest.mark.gs(weight=3)
def test_setup_failure(broken):
    pass

@pytest.mark.gs(weight=4, name="Case {id} works")
@pytest.mark.parametrize("x", [pytest.param(1, id="one", marks=pytest.mark.gs(weight=9, visibility="hidden")), 2])
def test_param(x):
    assert x == 1

@pytest.mark.gs(weight=1, variants=["560"])
def test_560_only():
    pass

def test_unmarked():
    pass

@pytest.mark.gs(weight=2)
def test_skip():
    pytest.skip("not applicable")
'''


def run(pytester, *args):
    pytester.makepyfile(test_mod=TEST_MODULE)
    out = pytester.path / "results.json"
    pytester.runpytest("-p", "framework.plugin", "--gs-results", str(out), *args)
    data = json.loads(out.read_text())
    return data, {t["name"]: t for t in data["tests"]}


def test_scoring_rules(pytester):
    data, tests = run(pytester)
    assert tests["Passing test"]["score"] == 5
    assert tests["Passing test"]["status"] == "passed"
    assert tests["Passing test"]["visibility"] == "visible"
    assert tests["Passing test"]["number"] == "1"

    assert tests["test_fail"]["score"] == 0
    assert "student-facing message" in tests["test_fail"]["output"]

    partial = tests["Partial"]
    assert partial["score"] == 2.5 and partial["status"] == "failed"
    assert partial["output"].index("3 of 4 wrong") < partial["output"].index("detail line")

    assert tests["Partial then pass"]["score"] == 7
    assert tests["Partial then pass"]["status"] == "failed"  # below max

    internal = tests["test_internal_error"]
    assert internal["score"] == 0 and "internal error" in internal["output"]
    assert "boom" not in internal["output"]  # no grader internals leak to students

    assert tests["test_setup_failure"]["score"] == 0
    assert tests["test_setup_failure"]["output"] == "Not run: build failed"

    assert tests["test_skip"]["score"] == 0 and "not applicable" in tests["test_skip"]["output"]
    assert tests["test_unmarked"]["max_score"] == 0
    assert data["stdout_visibility"] == "hidden"


def test_param_marks_override_and_names(pytester):
    _, tests = run(pytester)
    one = tests["Case one works"]
    assert one["max_score"] == 9 and one["visibility"] == "hidden" and one["score"] == 9
    two = tests["Case 2 works"]
    assert two["max_score"] == 4 and two["score"] == 0


@pytest.mark.parametrize("variant,present", [(None, False), ("471", False), ("560", True)])
def test_variants(pytester, variant, present):
    args = ["--gs-variant", variant] if variant else []
    _, tests = run(pytester, *args)
    assert ("test_560_only" in tests) is present


def test_collection_error_still_writes_results(pytester):
    pytester.makepyfile(test_bad="this is not python(")
    out = pytester.path / "results.json"
    pytester.runpytest("-p", "framework.plugin", "--gs-results", str(out))
    data = json.loads(out.read_text())
    assert data["score"] == 0 and "did not run to completion" in data["output"]


def test_truncate_keeps_both_ends():
    from framework.plugin import truncate
    text = "HEAD" + "x" * 50_000 + "TAIL"
    t = truncate(text, 1000)
    assert len(t) < 1100 and t.startswith("HEAD") and t.endswith("TAIL")
