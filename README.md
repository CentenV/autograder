# Networks autograder (Gradescope)

Autograder for the CSCI 471/560 programming projects. Student C/C++
code is built with its own Makefile and tested outside through its
CLI, files, and sockets. A small pytest plugin turns test results into
Gradescope's `results.json`.

| Project | Status |
| --- | --- |
| `p1_webserver` (HTTP server) | done: 14 tests (75 pts visible, 25 hidden); 560 zip swaps 1 test for 5 (+20) |
| `p2_rft` (Reliable File Transfer, Go-Back-N) | done: 12 tests (75 pts visible, 25 hidden); 560 zip adds 4 tests (+20) |
| `p4_routing` (Routing) | done: 9 cases (2 visible, 7 hidden) + stdin/stdout defaults |
| `p3_traceroute` (Traceroute) | done: 13 tests on a simulated network (65 pts visible, 35 hidden) |

## Layout

```
pyproject.toml            package metadata + dev pytest settings
conftest.py               loads autograder.plugin + autograder.fixtures (shipped in each zip)
src/autograder/           the framework: reusable, project-agnostic (`import autograder`)
  plugin.py               gs marker, `score` fixture, results.json writer
  fixtures.py             build / executable / workdir fixtures
  build.py                unpack submission (tarball, loose files, or dir), run make
  proc.py                 run with timeout, process-group kill, capped output, rlimits
  config.py               paths and the unprivileged student user (env-overridable)
  common_tests.py         test_build, imported by every project
  gradescope/             run_autograder, setup.sh, requirements.txt, pytest.ini (copied to the zip root)
tests/                    unit tests for the framework (not shipped)
tools/                    make_zip.sh, selfcheck.py, docker_test.sh (not shipped)
projects/                 project-specific autograders
  <name>/                 conftest.py (defines `project`), test.py (the graded tests), data -- shipped as-is
  solutions/<name>/       reference solutions + variants.json for selfcheck (not shipped)
  tests/                  unit tests for project helpers (not shipped)
build/, dist/             make_zip.sh staging dirs and zips (gitignored)
```

Each `projects/<name>/` is a top-level Python package (`p4_routing`,
`p3_traceroute`, ...), so the names `tests` and `solutions` are reserved.
Project code imports the framework with `from autograder import proc` /
`from autograder.build import Project`.

## Writing tests

Each project's graded tests go in `projects/<name>/test.py`. That file is the entry point: `run_autograder` passes it to pytest by path, so test code in any other file only runs if `test.py` imports it.

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
- **471 vs 560:** mark tests with `gs(variants=["560"])`. Then `tools/make_zip.sh p1_webserver 560` builds the 560 zip, and the default zip leaves those tests out. `gs(exclude_variants=["560"])` does the reverse: a 471 rule that 560 replaces (e.g. "HEAD returns 400").
- **Servers:** `proc.start(cmd, cwd=...)` runs a student program in the background (same user, rlimits and process-group kill as `proc.run`); read its output with `.output()` and end it with `.stop()` or a `with` block.
- **Build failures:** if the build fails, `test_build` shows the compiler output. Every other test that uses `executable` scores 0 with "Not run: your submission did not build".

## Security model

On Gradescope the framework runs as root. Student code is compiled and run as the unprivileged `student` user (`AUTOGRADER_STUDENT_USER`). It works only in `/autograder/work`, and `/autograder/source` (tests and expected outputs) plus `/autograder/results` are `chmod 700`. Pass `privileged=True` to `proc.run` when a project needs root, e.g. for raw sockets in traceroute.

## Verifying

```sh
python3 -m pytest                       # framework + project-helper unit tests (config in pyproject.toml)
python3 tools/selfcheck.py p4_routing   # good + broken reference solutions end to end
DOCKER=podman tools/docker_test.sh p4_routing path/to/submission   # in Gradescope's Ubuntu 22.04 image
```

`selfcheck.py` packages the zip (the 560 zip for variants with `"zip_variant": "560"`), builds a `student1.tgz` from `projects/solutions/<project>/` for each variant in `variants.json`, runs `run_autograder`, and checks the expected pass/fail pattern.

## Project 4 notes

- **Cases:** cases live in `projects/p4_routing/cases/<name>/` (interfaces, routes, input, expected output, and `meta.json` with weight/visibility/description). To add a case by hand, write the three input files, then run `python3 -m p4_routing.reference p4_routing/cases/<name> --write` from `projects/`.
- **Regenerating:** `python3 -m p4_routing.make_cases` (from `projects/`) regenerates the authored hidden cases.
- **Comparison rules:** lines are compared positionally, and any run of whitespace between tokens is accepted. Each case gets per-line partial credit, and extra lines cost credit.
- **Debug output:** the case tests pass `-d 0`. The stdin/stdout test omits `-d` to exercise the default, and it compares only lines in the output format.

## Project 3 notes

- **Simulated network:** `projects/p3_traceroute/tracesim.c` is compiled at grading time and loaded with `LD_PRELOAD`. It replaces raw sockets with a fake network. Routers answer with Time Exceeded, `*` hops stay silent, and the destination sends an Echo Reply. Every datagram the student sends is logged as JSON lines. No root, internet access, or extra container capabilities are needed, and student code runs as the unprivileged `student` user.
- **Scenarios:** scenarios are defined in `sim.py`. `basic` (visible) is 5 routers then the destination. `silent` (visible) has a silent router at TTL 3. `two_silent` has silent routers at TTL 2 and 5. `unreachable` has a 40-router path with no reply, so it checks the stop after 30 datagrams. `noise` sends unrelated ICMP before each real reply.
- **Grading:** tests check the datagrams themselves: a hand-built IPv4 header, protocol 1, the destination, Echo Request type/code, the checksum, TTLs 2, 3, …, and the ≥15 s wait at silent hops. They also check the printed output: router IPs in order (by subsequence), `No response with TTL of X`, and no noise addresses. Most tests give partial credit.
- **Time:** a correct submission takes about 50 s, because the silent hops really wait 15 s. A badly broken one can take about 5 min. Set the Gradescope timeout to at least 10 minutes.
- **Static binaries:** a statically linked binary can't be intercepted. The tests report this to the student explicitly.

## Project 1 notes

- **Setup:** each test starts `webServer` in a fresh directory containing `data/` (`file1-3.html` and `image1-2.jpg` from the course skeleton, plus a generated 60 KB `image8.jpg` full of NUL/CR/LF bytes). Decoys sit next to them (`badname.html`, `file10.html`, `file1.htm`, `file1.html.bak`, `secret.txt`) and one level up (`file1.html`, `image1.jpg`, `secret.html`). Any response containing the decoy marker text counts as a leak.
- **Port:** the server's printed output is scanned for 4–5 digit numbers. On Linux they are matched against the process group's LISTEN sockets in `/proc`. If `/proc/<pid>/fd` can't be read (another user's process without `CAP_SYS_PTRACE`, as in containers), the sockets are matched by owner uid instead, and stdout is line-buffered with `stdbuf`. No probe connections are made. If the server listens but never prints the port, only test 1.1 fails and the other tests still run.
- **Requests** are raw bytes over a socket, so the grader sees exactly what the server sends. Line endings are parsed leniently (`\n` alone is accepted), except in the header-format check.
- **471 vs 560:** the default zip grades HEAD/POST → 400 (5 pts). The 560 zip (`make_zip.sh p1_webserver 560`) drops that test and adds HEAD, POST create (201), POST overwrite (200), a binary POST round-trip, and SIGINT handling. The SIGINT test checks that the server exits within 3 s by itself rather than being killed by the signal. Totals are 100 (471) and 120 (560).
- **Time:** about 15 s for a correct server.

## Project 2 notes

- **Server:** `projects/p2_rft/gbn.py` is a Python port of the instructor's `rft-server` (github.com/promig3/RFT-Server). It has the same receiver FSM, `computeChecksum`, ACK-every-datagram behaviour, zero-length end of file, and 1 s linger. The port also logs every datagram and makes faults repeatable: seeded random loss/corruption in both directions, scripted drops, and delayed ACKs. While an ACK is delayed the server keeps receiving, so the window can be measured. It listens on 127.0.0.1 in 12000–13000, because the skeleton client rejects other ports. Wire format: `struct datagramS`, 268 bytes, little-endian.
- **Window check:** when datagram `seq` arrives, `seq − (highest ACK actually transmitted)` is an upper bound on the client's unacknowledged count. A value above N is therefore a real violation, with no false positives. Scenario `slow_acks` (5 ms ACK delay) is used for both the ≤10 check and the pipelining check (≥5 in flight).
- **Scenarios:** text and 64 KB binary with no faults; 5%/5% loss+corruption (visible); empty file and exact multiples of 255 bytes; first datagram dropped twice; EOF datagram dropped twice; ACKs with garbled ackNum and a bad checksum mixed with data loss; 15%/15% loss+corruption (hidden). Main runs pass no `-d`, so a missing `-d` costs only test 1.7.
- **Stalls:** a client is killed once the server has heard nothing from it for 10 s. That catches clients that never retransmit, or that finish but never exit, without waiting out the full timeout.
- **560:** `-w 5` (≤5 in flight) and `-w 20` (>10 in flight). The `-t` test drops datagram 5 once and checks that the retransmission gap under `-t 400` is ≥250 ms and more than 2× the gap under `-t 50`. The report test looks for the throughput and the total time in the output. Totals are 100 (471) and 120 (560).
- **Time:** about 10 s for a correct client and about 1.5 min for one that never retransmits.
