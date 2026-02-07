"""Tests for ddbj_search_api.schemas.dbxrefs."""
import pytest
from pydantic import ValidationError

from ddbj_search_api.schemas.dbxrefs import DbXrefsFullResponse


# === DbXrefsFullResponse ===


class TestDbXrefsFullResponse:
    """DbXrefsFullResponse: all dbXrefs for an entry."""

    def test_basic_construction(self) -> None:
        resp = DbXrefsFullResponse(
            dbXrefs=[
                {"identifier": "BS1", "type": "biosample", "url": "http://x"},
            ],
        )
        assert len(resp.db_xrefs) == 1

    def test_empty_db_xrefs(self) -> None:
        resp = DbXrefsFullResponse(dbXrefs=[])
        assert resp.db_xrefs == []

    def test_alias_serialization(self) -> None:
        resp = DbXrefsFullResponse(dbXrefs=[])
        data = resp.model_dump(by_alias=True)
        assert "dbXrefs" in data
        assert "db_xrefs" not in data

    def test_missing_db_xrefs_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            DbXrefsFullResponse()  # type: ignore[call-arg]
