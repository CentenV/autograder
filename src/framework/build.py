"""Turn whatever the student uploaded into a built directory.

Students are told to submit a tarball of a single directory named with their
username containing a Makefile. In practice Gradescope submissions arrive as:
  * that tarball (``jdoe.tgz``, ``jdoe.tar.gz``, ``jdoe.tar``),
  * the loose files (Makefile, *.cpp, ...), or
  * the directory itself, uploaded via GitHub/zip.
All three are handled: we locate the shallowest directory with a Makefile.
"""

from __future__ import annotations

import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config, proc

ARCHIVE_SUFFIXES = (".tar", ".tgz", ".tar.gz")
MAKEFILE_NAMES = ("Makefile", "makefile", "GNUmakefile")
IGNORED_DIRS = {"__MACOSX", ".git"}


@dataclass
class Project:
    name: str
    executables: tuple[str, ...]
    build_timeout: float = 120.0
    make_args: tuple[str, ...] = ()


@dataclass
class BuildResult:
    ok: bool
    message: str
    log: str = ""
    src_dir: Path | None = None
    executables: dict[str, Path] = field(default_factory=dict)


class SubmissionError(Exception):
    """A problem with the submission's shape, explained to the student."""


def _is_archive(p: Path) -> bool:
    return p.is_file() and p.name.lower().endswith(ARCHIVE_SUFFIXES)


def _extract(archive: Path, dest: Path) -> None:
    try:
        with tarfile.open(archive) as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(dest, filter="data")
            else:  # pragma: no cover - very old Python without PEP 706 backport
                for m in tar.getmembers():
                    target = (dest / m.name).resolve()
                    if not str(target).startswith(str(dest.resolve())) or m.issym() or m.islnk():
                        raise SubmissionError(f"{archive.name}: unsafe entry {m.name!r}")
                tar.extractall(dest)
    except (tarfile.TarError, OSError) as e:
        raise SubmissionError(f"Could not extract {archive.name}: {e}") from e


def _find_makefile_dirs(root: Path, max_depth: int = 3) -> list[Path]:
    found = []
    for mk in root.rglob("*"):
        if mk.name in MAKEFILE_NAMES and mk.is_file():
            rel = mk.relative_to(root)
            if len(rel.parts) - 1 <= max_depth and not IGNORED_DIRS.intersection(rel.parts):
                found.append(mk.parent)
    return sorted(set(found), key=lambda p: (len(p.relative_to(root).parts), str(p)))


def prepare_submission(submission: Path, dest: Path) -> tuple[Path, list[str]]:
    """Copy/extract the submission into ``dest``; return (dir with Makefile, notes)."""
    notes: list[str] = []
    if not submission.is_dir() or not any(submission.iterdir()):
        raise SubmissionError("The submission is empty.")
    raw = dest / "submission"
    shutil.copytree(submission, raw, symlinks=True)

    dirs = _find_makefile_dirs(raw)
    archives = [p for p in raw.rglob("*") if _is_archive(p) and not IGNORED_DIRS.intersection(p.parts)]
    if not dirs and archives:
        if len(archives) > 1:
            notes.append(f"Found several archives; using {archives[0].name}.")
        extracted = dest / "extracted"
        extracted.mkdir()
        _extract(archives[0], extracted)
        notes.append(f"Extracted {archives[0].name}.")
        dirs = _find_makefile_dirs(extracted)

    if not dirs:
        raise SubmissionError(
            "No Makefile found. Submit a tarball of a single directory (named with your username) "
            "containing a Makefile, README.txt and your source files."
        )
    if len(dirs) > 1:
        notes.append(f"Found several Makefiles; building in {dirs[0].name}/.")
    return dirs[0], notes


def _remove_prebuilt(src: Path, project: Project) -> list[str]:
    removed = []
    for name in project.executables:
        if (src / name).is_file():
            (src / name).unlink()
            removed.append(name)
    for obj in src.rglob("*.o"):
        obj.unlink()
        removed.append(str(obj.relative_to(src)))
    return removed


def build_submission(project: Project, submission: Path, work: Path) -> BuildResult:
    work = config.fresh_dir(work)
    try:
        src, notes = prepare_submission(submission, work)
    except SubmissionError as e:
        return BuildResult(ok=False, message=str(e))

    removed = _remove_prebuilt(src, project)
    if removed:
        notes.append("Removed prebuilt files from your submission before building "
                     f"(don't include binaries or object files): {', '.join(removed)}")
    config.give_to_student(work)

    result = proc.run(["make", *project.make_args], cwd=src, timeout=project.build_timeout)
    log = "\n".join([*notes, f"$ make {' '.join(project.make_args)}".rstrip(), result.out + result.err])
    if result.timed_out or result.returncode != 0:
        return BuildResult(ok=False, message=f"Build failed. {result.describe(tail=0)}", log=log, src_dir=src)

    executables, missing = {}, []
    for name in project.executables:
        path = src / name
        if path.is_file() and path.stat().st_mode & 0o111:
            executables[name] = path
        else:
            missing.append(name)
    if missing:
        return BuildResult(
            ok=False,
            message=f"`make` succeeded but did not produce the required executable(s): {', '.join(missing)}. "
                    "Check the executable name in your Makefile.",
            log=log, src_dir=src,
        )
    return BuildResult(ok=True, message="Build succeeded.", log=log, src_dir=src, executables=executables)
