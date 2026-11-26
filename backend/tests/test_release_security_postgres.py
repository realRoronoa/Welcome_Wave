"""Release round, security and configuration, on Postgres as ww_app (row-level security on). Run with a scratch server:

    TEST_PG_ADMIN_URL=postgresql://ww:ww@localhost:55432/postgres pytest tests/test_release_security_postgres.py
"""
from __future__ import annotations

import uuid

import pytest

from app.adapters.postgres import current_org
from tests import test_postgres as pg

ADMIN, _org = pg.ADMIN, pg._org
db, engine, store = pg.db, pg.engine, pg.store  # the scratch database and its fixtures (tests/test_postgres.py)

pytestmark = pytest.mark.skipif(not ADMIN, reason="set TEST_PG_ADMIN_URL to run the Postgres integration tests")


def test_whether_an_organisation_exists_is_known_before_any_tenant_is_set(store):
    current_org.set(None)
    assert store.organization_exists() is False  # a fresh server: sign-up may create the first organisation
    _org(store, "first")
    current_org.set(None)  # sign-up runs before the tenant is known, where row-level security hides every organisation
    assert store.organization_exists() is True


def test_an_unknown_project_is_none_and_a_malformed_id_a_lookup_error(store):
    user, project = _org(store, "lookup")
    assert store.get_project(project.id).id == project.id
    assert store.get_project(str(uuid.uuid4())) is None
    with pytest.raises(LookupError):
        store.get_project("not-a-uuid")
    current_org.set(None)
    assert store.get_project(project.id) is None  # another tenant's project is hidden, not an error
