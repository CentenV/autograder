"""Tests every project includes. Import into a project's test module::

    from framework.common_tests import test_build  # noqa: F401
"""

import pytest


@pytest.mark.gs(weight=0, name="Build submission (make)", number="0", visibility="visible")
def test_build(build, score):
    if build.log:
        score.log(build.log)
    if not build.ok:
        pytest.fail(build.message, pytrace=False)
