"""Shared fixtures.

`clear_settings_cache` is the piece worth calling out: `ibi.config.get_settings`
is an `lru_cache`d singleton by design (see config.py's docstring), so any
test that monkeypatches environment variables affecting `Settings` must
clear that cache before and after, or it will silently see another test's
cached settings.
"""

from __future__ import annotations

import pytest

from ibi.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
