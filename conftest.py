"""Top-level pytest config — make MySQL test DB easier to spin up.

Without a top-level conftest, pytest-django defaults to creating a `test_<dbname>`
DB. CI sets DB_NAME=test_x106 so the test DB is `test_test_x106` — clear but
verbose. We just inherit the default behavior here; this file exists so
collection picks up the tests/ directory.
"""

import pytest


@pytest.fixture(autouse=True)
def _allow_db_for_smoke_tests(db):
    """Smoke tests need DB access (User model imports, etc)."""
    pass
