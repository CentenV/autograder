import pytest

from framework.build import Project


@pytest.fixture(scope="session")
def project() -> Project:
    return Project(name="Routing", executables=("router",))
