"""
The live tests download from the shared Overpass API, which sometimes
refuses connections or rate-limits for long stretches. NetworkForge
already tries again after a pause; if the download still fails, the
test is skipped (saying so) rather than reported as an error in the
engine. Every other failure fails as usual.
"""

import pytest

from networkforge.errors import OSMDownloadError

REASON = "Overpass API unavailable: "


def _skip_on_download_failure(outcome_generator):
    try:
        return (yield)
    except OSMDownloadError as exc:
        pytest.skip(REASON + str(exc).splitlines()[0][:200])


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    return (yield from _skip_on_download_failure(None))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    return (yield from _skip_on_download_failure(None))
