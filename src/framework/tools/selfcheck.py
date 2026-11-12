#!/usr/bin/env python3
"""End-to-end check of a project's autograder against reference solutions.

    python3 framework/tools/selfcheck.py p4_routing [variant ...]

For each variant in project-autograders/solutions/<project>/variants.json this:
  1. packages the autograder exactly as framework/tools/make_zip.sh does (build/<project>/),
  2. builds a student-style tarball (student1.tgz containing student1/) from
     project-autograders/solutions/<project>/ with the variant's -D defines,
  3. runs run_autograder with Gradescope paths redirected to a temp dir,
  4. validates results.json and checks the variant's expectations:
       expect_total: "full" | "zero"
       expect_pass / expect_fail: substrings of test names; every matching
       test must pass / fail, and at least one test must match.
       zip_variant: course variant to package and grade with (e.g. "560").
       linux_only: skip the variant on other platforms (e.g. needs /proc).
Exit status is non-zero if any expectation fails.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_KEYS = {"name", "score", "max_score", "output"}


def package(project: str, variant: str = "") -> Path:
    subprocess.run([str(ROOT / "framework" / "tools" / "make_zip.sh"), project, *([variant] if variant else [])],
                   check=True, stdout=subprocess.DEVNULL)
    return ROOT / "build" / (f"{project}-{variant}" if variant else project)


def make_submission(sol_dir: Path, variant: dict, dest: Path) -> None:
    stage = dest.parent / "student-src" / "student1"
    stage.mkdir(parents=True)
    defines = " ".join(f"-D{d}" for d in variant.get("defines", []))
    for f in sol_dir.iterdir():
        if f.name in ("variants.json", "Makefile.template") or f.name.startswith("."):
            continue
        (stage / f.name).write_bytes(f.read_bytes())
    (stage / "Makefile").write_text((sol_dir / "Makefile.template").read_text().replace("@DEFINES@", defines))
    (stage / "README.txt").write_text("Reference solution used to validate the autograder.\n")
    if variant.get("include_junk_binary"):
        for name in variant.get("junk_names", ["router", "router.o"]):
            (stage / name).write_bytes(b"\x7fELF not really a binary")
            (stage / name).chmod(0o755)
    dest.mkdir(parents=True)
    with tarfile.open(dest / "student1.tgz", "w:gz") as tar:
        tar.add(stage, arcname="student1")


def validate(results: dict) -> list[str]:
    problems = []
    tests = results.get("tests")
    if not isinstance(tests, list) or (not tests and "score" not in results):
        return ["results.json has neither tests nor a top-level score"]
    for t in tests:
        missing = REQUIRED_KEYS - t.keys()
        if missing:
            problems.append(f"{t.get('name')}: missing {missing}")
        elif not (0 <= t["score"] <= t["max_score"]):
            problems.append(f"{t['name']}: score {t['score']} outside [0, {t['max_score']}]")
        if t.get("visibility") not in (None, "visible", "hidden", "after_due_date", "after_published"):
            problems.append(f"{t['name']}: bad visibility {t.get('visibility')}")
    return problems


def check(results: dict, variant: dict) -> list[str]:
    tests = results["tests"]
    earned = sum(t["score"] for t in tests)
    total = sum(t["max_score"] for t in tests)
    problems = validate(results)
    exp = variant.get("expect_total")
    if exp == "full" and earned != total:
        problems.append(f"expected full marks, got {earned}/{total}")
    if exp == "zero" and earned != 0:
        problems.append(f"expected zero, got {earned}/{total}")
    for want, key in (("passed", "expect_pass"), ("failed", "expect_fail")):
        for sub in variant.get(key, []):
            matching = [t for t in tests if sub in t["name"]]
            if not matching:
                problems.append(f"{key}: no test name contains {sub!r}")
            for t in matching:
                if t.get("status") != want:
                    problems.append(f"{key}: {t['name']!r} {t.get('status')} ({t['score']}/{t['max_score']})")
    return problems


def run_variant(project: str, source: Path, name: str, variant: dict, verbose: bool) -> bool:
    sol_dir = ROOT / "project-autograders" / "solutions" / project
    with tempfile.TemporaryDirectory(prefix=f"selfcheck-{name}-") as tmp:
        tmp = Path(tmp)
        tmp.chmod(0o755)  # student code may run as an unprivileged user (see framework/config.py)
        make_submission(sol_dir, variant, tmp / "submission")
        env = dict(os.environ,
                   AUTOGRADER_SOURCE_DIR=str(source),
                   AUTOGRADER_SUBMISSION_DIR=str(tmp / "submission"),
                   AUTOGRADER_RESULTS_DIR=str(tmp / "results"),
                   AUTOGRADER_WORK_DIR=str(tmp / "work"))
        proc = subprocess.run([str(source / "run_autograder")], env=env, capture_output=True, text=True)
        results = json.loads((tmp / "results" / "results.json").read_text())
        problems = check(results, variant) if "tests" in results else [f"no tests: {results.get('output')}"]
        earned = sum(t["score"] for t in results.get("tests", []))
        total = sum(t["max_score"] for t in results.get("tests", []))
        print(f"{'OK  ' if not problems else 'FAIL'} {name:<16} {earned:g}/{total:g}")
        for p in problems:
            print(f"       - {p}")
        if verbose or problems:
            for t in results.get("tests", []):
                print(f"       {t['status']:<6} {t['score']:>5g}/{t['max_score']:<4g} {t['name']}")
        if verbose:
            print(proc.stdout[-4000:], proc.stderr[-2000:])
            print(json.dumps(results, indent=2)[:6000])
        return not problems


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "-v"]
    verbose = "-v" in sys.argv
    if not args:
        print(__doc__)
        return 2
    project, only = args[0], set(args[1:])
    variants = json.loads((ROOT / "project-autograders" / "solutions" / project / "variants.json").read_text())
    selected = {n: v for n, v in variants.items() if not only or n in only}
    for n in [n for n, v in selected.items() if v.get("linux_only") and not sys.platform.startswith("linux")]:
        print(f"SKIP {n:<16} (Linux only)")
        del selected[n]
    sources = {zv: package(project, zv) for zv in {v.get("zip_variant", "") for v in selected.values()}}
    ok = all([run_variant(project, sources[v.get("zip_variant", "")], n, v, verbose) for n, v in selected.items()])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
