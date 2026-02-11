"""Tests for ddbj_search_api.schemas.service_info."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ddbj_search_api.schemas.service_info import ServiceInfoResponse


class TestServiceInfoResponse:
    """ServiceInfoResponse: service metadata."""

    def test_basic_construction(self) -> None:
        resp = ServiceInfoResponse(
            name="DDBJ Search API",
            version="0.1.0",
            description="Search API for DDBJ.",
            elasticsearch="ok",
        )
        assert resp.name == "DDBJ Search API"
        assert resp.version == "0.1.0"
        assert resp.description == "Search API for DDBJ."
        assert resp.elasticsearch == "ok"

    def test_elasticsearch_unavailable(self) -> None:
        resp = ServiceInfoResponse(
            name="API",
            version="0.1.0",
            description="desc",
            elasticsearch="unavailable",
        )
        assert resp.elasticsearch == "unavailable"

    def test_invalid_elasticsearch_status_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            ServiceInfoResponse(
                name="API",
                version="0.1.0",
                description="desc",
                elasticsearch="down",  # type: ignore[arg-type]
            )

    def test_missing_name_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            ServiceInfoResponse(  # type: ignore[call-arg]
                version="0.1.0",
                description="desc",
                elasticsearch="ok",
            )

    def test_missing_version_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            ServiceInfoResponse(  # type: ignore[call-arg]
                name="API",
                description="desc",
                elasticsearch="ok",
            )

    def test_missing_description_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            ServiceInfoResponse(  # type: ignore[call-arg]
                name="API",
                version="0.1.0",
                elasticsearch="ok",
            )

    def test_missing_elasticsearch_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            ServiceInfoResponse(  # type: ignore[call-arg]
                name="API",
                version="0.1.0",
                description="desc",
            )
