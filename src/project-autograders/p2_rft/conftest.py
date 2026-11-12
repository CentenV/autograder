import pytest

from framework.build import Project

from . import gbn


@pytest.fixture(scope="session")
def project() -> Project:
    return Project(name="Reliable File Transfer", executables=("rft-client",))


@pytest.fixture(scope="session")
def transfer(executables, project):
    """``transfer(name, data, faults, args, timeout)`` runs rft-client once per name and caches it."""
    exe = executables[project.executables[0]]
    cache: dict[str, gbn.Transfer] = {}

    def get(name: str, data: bytes, faults: gbn.Faults | None = None, args: tuple[str, ...] = (),
            timeout: float = 60.0) -> gbn.Transfer:
        if name not in cache:
            cache[name] = t = gbn.run(exe, name, data, faults, args, timeout)
            print(f"--- transfer {name}: {t.result.describe(tail=0)} in {t.result.duration:.1f}s; "
                  f"complete={t.complete}; max outstanding={t.rx.max_outstanding}")
            print(t.rx.stats())
            print(t.result.out[-2000:], t.result.err[-2000:])
        return cache[name]

    return get
