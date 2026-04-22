"""Shared fixtures for dblink.client tests.

Each test starts with a cleared connection cache so that module-level
state does not leak across tests (e.g. when two tests reuse the same
file name under different ``tmp_path`` directories on the same worker).
"""

from __future__ import annotations

import collections.abc

import pytest

from ddbj_search_api.dblink import client as dblink_client


@pytest.fixture(autouse=True)
def _reset_conn_cache() -> collections.abc.Iterator[None]:
    dblink_client._reset_cache()
    yield
    dblink_client._reset_cache()
