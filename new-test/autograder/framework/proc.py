"""Run student programs with timeouts, resource limits, and bounded output capture."""

from __future__ import annotations

import os
import resource
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import config

MAX_CAPTURE = 1 << 20        # bytes of stdout/stderr kept in memory
MAX_FILE_SIZE = 64 << 20     # RLIMIT_FSIZE for student processes (output files, logs)


@dataclass
class RunResult:
    cmd: list[str]
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    duration: float

    @property
    def out(self) -> str:
        return self.stdout.decode(errors="replace")

    @property
    def err(self) -> str:
        return self.stderr.decode(errors="replace")

    def describe(self, tail: int = 1500) -> str:
        """Short student-facing summary of how the process ended, with stderr tail."""
        cmd = " ".join([Path(self.cmd[0]).name, *self.cmd[1:]])
        if self.timed_out:
            status = f"`{cmd}` did not finish within {self.duration:.0f} s and was killed."
        elif self.returncode is not None and self.returncode < 0:
            try:
                sig = signal.Signals(-self.returncode).name
            except ValueError:
                sig = f"signal {-self.returncode}"
            status = f"`{cmd}` crashed ({sig})."
        else:
            status = f"`{cmd}` exited with status {self.returncode}."
        err = self.err.strip()
        if err and tail > 0:
            status += f"\nLast stderr output:\n{err[-tail:]}"
        return status


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_SIZE, MAX_FILE_SIZE))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _user_kwargs(privileged: bool) -> dict:
    user = None if privileged else config.student_user()
    if user is None:
        return {}
    return {"user": user.pw_uid, "group": user.pw_gid, "extra_groups": []}


def _env(cwd: Path, extra: dict | None) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(cwd),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    if extra:
        env.update(extra)
    return env


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _read_capped(f) -> bytes:
    f.seek(0)
    data = f.read(MAX_CAPTURE + 1)
    if len(data) > MAX_CAPTURE:
        data = data[:MAX_CAPTURE] + b"\n... [output truncated by autograder] ...\n"
    return data


def run(
    cmd: Sequence[str | os.PathLike],
    *,
    cwd: Path,
    timeout: float,
    stdin: bytes | None = None,
    env: dict | None = None,
    privileged: bool = False,
) -> RunResult:
    """Run ``cmd`` to completion in its own process group.

    On timeout the whole process group is SIGKILLed so forked children can't
    outlive the test. Student code runs as AUTOGRADER_STUDENT_USER unless
    ``privileged`` is set (needed e.g. for raw sockets).
    """
    argv = [str(c) for c in cmd]
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        start = time.monotonic()
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            env=_env(cwd, env),
            start_new_session=True,
            preexec_fn=_limits,
            **_user_kwargs(privileged),
        )
        timed_out = False
        try:
            proc.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
            proc.wait()
        # Kill anything the program left running in the background.
        _kill_group(proc)
        return RunResult(
            cmd=argv,
            returncode=None if timed_out else proc.returncode,
            stdout=_read_capped(out),
            stderr=_read_capped(err),
            timed_out=timed_out,
            duration=time.monotonic() - start,
        )
