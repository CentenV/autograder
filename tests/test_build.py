"""Tests for autograder.build submission handling and autograder.proc."""

import io
import tarfile
from pathlib import Path

import pytest

from autograder import proc
from autograder.build import Project, SubmissionError, build_submission, prepare_submission

MAKEFILE = "hello: hello.c\n\tcc -o hello hello.c\n"
HELLO = '#include <stdio.h>\nint main(void){puts("hi");return 0;}\n'


def write_tree(root: Path, files: dict) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def make_tgz(path: Path, files: dict) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for rel, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


@pytest.mark.parametrize("layout", ["flat", "nested", "tarball", "tarball_flat"])
def test_prepare_layouts(tmp_path, layout):
    sub = tmp_path / "sub"
    sub.mkdir()
    files = {"Makefile": MAKEFILE, "hello.c": HELLO}
    if layout == "flat":
        write_tree(sub, files)
    elif layout == "nested":
        write_tree(sub, {f"jdoe/{k}": v for k, v in files.items()})
    elif layout == "tarball":
        make_tgz(sub / "jdoe.tgz", {f"jdoe/{k}": v for k, v in files.items()})
    else:
        make_tgz(sub / "jdoe.tar.gz", files)
    src, _ = prepare_submission(sub, tmp_path / "work")
    assert (src / "Makefile").is_file()


def test_prepare_no_makefile(tmp_path):
    sub = tmp_path / "sub"
    write_tree(sub, {"main.cpp": "int main(){}"})
    with pytest.raises(SubmissionError, match="No Makefile"):
        prepare_submission(sub, tmp_path / "work")


def test_prepare_rejects_path_traversal(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    make_tgz(sub / "evil.tgz", {"../../escape.txt": "x", "jdoe/Makefile": MAKEFILE})
    with pytest.raises(SubmissionError):
        prepare_submission(sub, tmp_path / "work")
    assert not (tmp_path / "escape.txt").exists()


def test_build_success_and_missing_executable(tmp_path):
    sub = tmp_path / "sub"
    write_tree(sub, {"Makefile": MAKEFILE, "hello.c": HELLO})
    ok = build_submission(Project("t", ("hello",)), sub, tmp_path / "w1")
    assert ok.ok and ok.executables["hello"].is_file()
    missing = build_submission(Project("t", ("router",)), sub, tmp_path / "w2")
    assert not missing.ok and "router" in missing.message


def test_run_timeout_kills_children(tmp_path):
    r = proc.run(["sh", "-c", "sleep 30 & sleep 30"], cwd=tmp_path, timeout=0.5)
    assert r.timed_out and r.duration < 5 and "did not finish" in r.describe()


def test_run_captures_and_caps_output(tmp_path, monkeypatch):
    monkeypatch.setattr(proc, "MAX_CAPTURE", 100)
    r = proc.run(["sh", "-c", "yes | head -c 10000; echo err >&2; exit 3"], cwd=tmp_path, timeout=5)
    assert r.returncode == 3 and "truncated" in r.out and r.err.strip() == "err"


def test_background_process_output_and_stop(tmp_path):
    import signal
    import time
    script = "import sys,time\nprint('port 4242', flush=True)\ntime.sleep(30)\n"
    with proc.start(["python3", "-c", script], cwd=tmp_path) as bg:
        deadline = time.monotonic() + 5
        while "4242" not in bg.output()[0] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "port 4242" in bg.output()[0]
        assert bg.alive()
        bg.signal(signal.SIGTERM)
        assert bg.wait(5) is not None
        result = bg.stop()
    assert not result.timed_out and result.returncode == -signal.SIGTERM


def test_background_stop_kills_running(tmp_path):
    bg = proc.start(["sleep", "30"], cwd=tmp_path)
    result = bg.stop()
    assert result.timed_out and not bg.alive()
    assert bg.stop() is result
