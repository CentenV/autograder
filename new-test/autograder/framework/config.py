"""Locations and settings, overridable with environment variables for local runs.

| Variable                   | Gradescope default        |
| -------------------------- | ------------------------- |
| AUTOGRADER_SUBMISSION_DIR  | /autograder/submission    |
| AUTOGRADER_WORK_DIR        | /autograder/work          |
| AUTOGRADER_STUDENT_USER    | (unset) run student code as this unprivileged user when root |
"""

from __future__ import annotations

import os
import pwd
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path


def submission_dir() -> Path:
    return Path(os.environ.get("AUTOGRADER_SUBMISSION_DIR", "/autograder/submission"))


@lru_cache(maxsize=None)
def work_root() -> Path:
    env = os.environ.get("AUTOGRADER_WORK_DIR")
    if env:
        root = Path(env)
    elif os.access("/autograder", os.W_OK):
        root = Path("/autograder/work")
    else:
        root = Path(tempfile.mkdtemp(prefix="autograder-work-"))
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o755)
    return root


@lru_cache(maxsize=None)
def student_user() -> pwd.struct_passwd | None:
    """The unprivileged account student code runs as, or None to run as ourselves."""
    name = os.environ.get("AUTOGRADER_STUDENT_USER")
    if not name or os.geteuid() != 0:
        return None
    return pwd.getpwnam(name)


def give_to_student(path: Path) -> None:
    """Make a path (recursively) owned by the student user, if one is configured."""
    user = student_user()
    if user is None:
        return
    for p in [path, *path.rglob("*")] if path.is_dir() else [path]:
        os.lchown(p, user.pw_uid, user.pw_gid)


def fresh_dir(path: Path) -> Path:
    """Create an empty directory the student's program can write to."""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    path.chmod(0o755)
    give_to_student(path)
    return path
