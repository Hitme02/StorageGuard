import pytest

from storageguard.bench.handbuilt import mini_estate


@pytest.fixture()
def estate():
    """(builder, ids) for the hand-built mini estate."""
    return mini_estate()


@pytest.fixture()
def twin(estate):
    return estate[0].twin


@pytest.fixture()
def ids(estate):
    return estate[1]
