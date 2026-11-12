import pytest

from autograder.build import Project

from . import harness


@pytest.fixture(scope="session")
def project() -> Project:
    return Project(name="Web Server", executables=("webServer",))


@pytest.fixture
def server(executable, workdir, score):
    """A freshly started webServer (in ``workdir`` with data/ and decoys) whose port is known."""
    srv = harness.start(executable, workdir)
    try:
        if srv.port is None:
            pytest.fail(f"{srv.startup_problem}\n{srv.describe()}", pytrace=False)
        for note in srv.notes:
            score.log(note)
        yield srv
    finally:
        result = srv.bg.stop()
        print(f"--- server: {result.describe(tail=0)}\n{result.out[-3000:]}\n{result.err[-2000:]}")
