# Networks autograder (Gradescope)

Black-box autograders for the CSCI 471/560 programming projects. Student C/C++
code is built with its own Makefile and tested from the outside through its
CLI, files, and sockets. A small pytest plugin turns test results into
Gradescope's `results.json`.

| Project | Status |
| --- | --- |
| `p4_routing` (Routing) | done: 9 cases (2 visible, 7 hidden) + stdin/stdout defaults |
| `p3_traceroute` (Traceroute) | done: 13 tests on a simulated network (65 pts visible, 35 hidden) |
| `p1_webserver`, `p2_rft` | not started |

## Layout

```
conftest.py               loads framework.plugin + framework.fixtures for every test
pytest.ini                repo-wide pytest settings (shipped in each zip)
framework/                reusable, project-agnostic
  plugin.py               gs marker, `score` fixture, results.json writer
  fixtures.py             build / executable / workdir fixtures
  build.py                unpack submission (tarball, loose files, or dir), run make
  proc.py                 run with timeout, process-group kill, capped output, rlimits
  config.py               paths and the unprivileged student user (env-overridable)
  common_tests.py         test_build, imported by every project
  gradescope/             run_autograder, setup.sh, requirements.txt (copied to the zip root)
  tools/                  make_zip.sh, selfcheck.py, docker_test.sh (not shipped)
  tests/                  unit tests for the framework itself (not shipped)
project-autograders/      project-specific
  <name>/                 conftest.py (defines `project`), test_*.py, data -- shipped as-is
  solutions/<name>/       reference solutions + variants.json for selfcheck (not shipped)
  tests/                  unit tests for project helpers (not shipped)
```

Each `project-autograders/<name>/` is a top-level Python package (`p4_routing`,
`p3_traceroute`, ...), so the names `tests` and `solutions` are reserved.
Project code imports the framework with `from framework import proc` /
`from framework.build import Project`.

## Writing tests

```python
@pytest.mark.gs(weight=5, name="GET /file1.html returns 200", visibility="visible", number="1.1")
def test_get(executable, workdir, score):
    result = proc.run([executable, ...], cwd=workdir, timeout=10)
    score.log("shown to the student")
    score.set_fraction(0.5)          # optional partial credit
    if bad:
        pytest.fail("short student-facing reason", pytrace=False)
```

- **Scoring:** passing with no `score.set` earns full weight, and failing earns 0. Once `score.set*` is called, that value counts whether or not the test fails afterwards.
- **What students see:** only `score.log(...)` text and failure messages. Exceptions other than assertion and `pytest.fail` failures appear as a generic "internal error". Tracebacks go to stdout, which only instructors see.
- **Marker merging:** `gs` markers merge from module → class → function → `pytest.param(marks=...)`. That means `pytestmark = pytest.mark.gs(visibility="after_published")` sets a default for a whole file.
- **471 vs 560:** mark tests with `gs(variants=["560"])`. Then `framework/tools/make_zip.sh p1_webserver 560` builds the 560 zip, and the default zip leaves those tests out.
- **Build failures:** if the build fails, `test_build` shows the compiler output. Every other test that uses `executable` scores 0 with "Not run: your submission did not build".

## Security model

On Gradescope the framework runs as root. Student code is compiled and run as the unprivileged `student` user (`AUTOGRADER_STUDENT_USER`). It works only in `/autograder/work`, and `/autograder/source` (tests and expected outputs) plus `/autograder/results` are `chmod 700`. Pass `privileged=True` to `proc.run` when a project needs root, e.g. for raw sockets in traceroute.

## Verifying

```sh
python3 -m pytest                                   # framework + project-helper unit tests
python3 framework/tools/selfcheck.py p4_routing     # good + broken reference solutions end to end
DOCKER=podman framework/tools/docker_test.sh p4_routing path/to/submission   # in Gradescope's Ubuntu 22.04 image
```

`selfcheck.py` packages the zip, builds a `student1.tgz` from `project-autograders/solutions/<project>/` for each variant in `variants.json`, runs `run_autograder`, and checks the expected pass/fail pattern.

## Project 4 notes

- **Cases:** cases live in `project-autograders/p4_routing/cases/<name>/` (interfaces, routes, input, expected output, and `meta.json` with weight/visibility/description). To add a case by hand, write the three input files, then run `python3 -m p4_routing.reference p4_routing/cases/<name> --write` from `project-autograders/`.
- **Regenerating:** `python3 -m p4_routing.make_cases` (from `project-autograders/`) regenerates the authored hidden cases.
- **Comparison rules:** lines are compared positionally, and any run of whitespace between tokens is accepted. Each case gets per-line partial credit, and extra lines cost credit.
- **Debug output:** the case tests pass `-d 0`. The stdin/stdout test omits `-d` to exercise the default, and it compares only lines in the output format.

## Project 3 notes

- **Simulated network:** `project-autograders/p3_traceroute/tracesim.c` is compiled at grading time and loaded with `LD_PRELOAD`. It replaces raw sockets with a fake network. Routers answer with Time Exceeded, `*` hops stay silent, and the destination sends an Echo Reply. Every datagram the student sends is logged as JSON lines. No root, internet access, or extra container capabilities are needed, and student code runs as the unprivileged `student` user.
- **Scenarios:** scenarios are defined in `sim.py`. `basic` (visible) is 5 routers then the destination. `silent` (visible) has a silent router at TTL 3. `two_silent` has silent routers at TTL 2 and 5. `unreachable` has a 40-router path with no reply, so it checks the stop after 30 datagrams. `noise` sends unrelated ICMP before each real reply.
- **Grading:** tests check the datagrams themselves: a hand-built IPv4 header, protocol 1, the destination, Echo Request type/code, the checksum, TTLs 2, 3, …, and the ≥15 s wait at silent hops. They also check the printed output: router IPs in order (by subsequence), `No response with TTL of X`, and no noise addresses. Most tests give partial credit.
- **Time:** a correct submission takes about 50 s, because the silent hops really wait 15 s. A badly broken one can take about 5 min. Set the Gradescope timeout to at least 10 minutes.
- **Static binaries:** a statically linked binary can't be intercepted. The tests report this to the student explicitly.
