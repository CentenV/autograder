import pytest

from autograder.build import Project

from . import sim


@pytest.fixture(scope="session")
def project() -> Project:
    return Project(name="Traceroute", executables=("traceroute",))


@pytest.fixture(scope="session")
def shim():
    return sim.build_shim()


@pytest.fixture(scope="session")
def trace(executables, project, shim):
    """``trace("basic")`` runs the student's traceroute once per scenario and caches it."""
    exe = executables[project.executables[0]]
    cache: dict[str, sim.Trace] = {}

    def get(name: str) -> sim.Trace:
        if name not in cache:
            cache[name] = t = sim.run(exe, shim, sim.SCENARIOS[name])
            print(f"--- scenario {name}: {t.result.describe(tail=0)} in {t.result.duration:.1f}s")
            print(t.describe_sends())
            print(t.result.out[-3000:], t.result.err[-2000:])
        t = cache[name]
        if not t.shim_loaded:
            pytest.fail(
                "The autograder could not attach to your program's sockets, so it could not see any packets. "
                "Make sure traceroute is a normal dynamically linked C/C++ program (don't link with -static).\n"
                + t.result.describe(), pytrace=False)
        return t

    return get
