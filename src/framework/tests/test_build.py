"""Tests for framework.build submission handling and framework.proc."""

import io
import tarfile
from pathlib import Path

import pytest

from framework import proc
from framework.build import Project, SubmissionError, build_submission, prepare_submission

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
