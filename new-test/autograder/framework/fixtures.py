"""Shared fixtures. Each project's conftest.py must define a session-scoped
``project`` fixture returning a :class:`framework.build.Project`."""

from __future__ import annotations

import re
import traceback

import pytest

from . import config
from .build import BuildResult, Project, build_submission

BUILD_FAILED_MSG = "Not run: your submission did not build. See the build test at the top for details."


@pytest.fixture(scope="session")
def project() -> Project:
    raise NotImplementedError("Define a session-scoped `project` fixture in the project's conftest.py")


@pytest.fixture(scope="session")
def build(project: Project) -> BuildResult:
    try:
        return build_submission(project, config.submission_dir(), config.work_root() / "build")
    except Exception:  # never let a harness bug look like a student build failure silently
        traceback.print_exc()
        return BuildResult(ok=False, message="The autograder failed while building your submission. "
                                             "Your instructor can see the details in the autograder log.")


@pytest.fixture(scope="session")
def executables(build: BuildResult) -> dict:
    if not build.ok:
        pytest.fail(BUILD_FAILED_MSG, pytrace=False)
    return build.executables


@pytest.fixture
def executable(executables: dict, project: Project):
    """Path to the project's primary executable."""
    return executables[project.executables[0]]


@pytest.fixture
def workdir(request: pytest.FixtureRequest):
    """A fresh, student-writable directory for this test."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)[-120:]
    return config.fresh_dir(config.work_root() / "tests" / safe)
