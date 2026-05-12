"""Shared helpers for routers."""

from __future__ import annotations

from ddbj_search_api.schemas.common import DbType


def is_sra(db_type: DbType) -> bool:
    return db_type.value.startswith("sra-")


def is_jga(db_type: DbType) -> bool:
    return db_type.value.startswith("jga-")
