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


class Background:
    """A long-running student program (e.g. a server) started with :func:`start`.

    stdout/stderr go to temporary files that can be read while it runs. Use as
    a context manager, or call :meth:`stop`; the whole process group is killed.
    """

    def __init__(self, argv: list[str], popen: subprocess.Popen, out, err, start: float):
        self.cmd = argv
        self.popen = popen
        self._out, self._err = out, err
        self._start = start
        self.result: RunResult | None = None

    @property
    def pid(self) -> int:
        return self.popen.pid

    def poll(self) -> int | None:
        return self.popen.poll()

    def alive(self) -> bool:
        return self.popen.poll() is None

    def output(self) -> tuple[str, str]:
        """(stdout, stderr) so far."""
        return (_read_capped(self._out).decode(errors="replace"),
                _read_capped(self._err).decode(errors="replace"))

    def signal(self, sig: int) -> None:
        """Send ``sig`` to the program itself (not its process group)."""
        if self.alive():
            try:
                os.kill(self.popen.pid, sig)
            except ProcessLookupError:
                pass

    def wait(self, timeout: float) -> int | None:
        """Wait up to ``timeout`` s for exit; return the exit status or None."""
        try:
            return self.popen.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def stop(self) -> RunResult:
        """Kill the program (and anything it forked); return how it ended."""
        if self.result is None:
            still_running = self.alive()
            _kill_group(self.popen)
            self.popen.wait()
            self.result = RunResult(
                cmd=self.cmd,
                returncode=None if still_running else self.popen.returncode,
                stdout=_read_capped(self._out),
                stderr=_read_capped(self._err),
                timed_out=still_running,
                duration=time.monotonic() - self._start,
            )
            self._out.close()
            self._err.close()
        return self.result

    def __enter__(self) -> "Background":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def start(
    cmd: Sequence[str | os.PathLike],
    *,
    cwd: Path,
    env: dict | None = None,
    privileged: bool = False,
) -> Background:
    """Start ``cmd`` in the background in its own process group (see :func:`run`).

    The returned handle's ``stop()`` result reports ``timed_out=True`` when the
    program was still running and had to be killed.
    """
    argv = [str(c) for c in cmd]
    out, err = tempfile.TemporaryFile(), tempfile.TemporaryFile()
    popen = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=err,
        env=_env(cwd, env),
        start_new_session=True,
        preexec_fn=_limits,
        **_user_kwargs(privileged),
    )
    return Background(argv, popen, out, err, time.monotonic())
