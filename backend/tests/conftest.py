"""Test configuration.

Most tests here are pure-unit and need no database — that is deliberate. The
resolver's *decision* logic (name similarity, proximity, merge rules) is
separated from its *persistence* logic precisely so the interesting behaviour
can be tested without Postgres in the loop.
"""

from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def city(settings):
    return settings.city
