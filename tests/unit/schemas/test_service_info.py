"""Tests for ddbj_search_api.schemas.service_info."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from ddbj_search_api.schemas.service_info import ServiceInfoResponse

_VALID_ES_STATUSES = ["ok", "unavailable"]


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

    @pytest.mark.parametrize("status", _VALID_ES_STATUSES)
    def test_elasticsearch_literal_accepted(self, status: str) -> None:
        resp = ServiceInfoResponse(
            name="API",
            version="0.1.0",
            description="desc",
            elasticsearch=status,  # type: ignore[arg-type]
        )
        assert resp.elasticsearch == status

    @pytest.mark.parametrize(
        "field",
        ["name", "version", "description", "elasticsearch"],
    )
    def test_missing_field_raises_error(self, field: str) -> None:
        kwargs: dict[str, str] = {
            "name": "API",
            "version": "0.1.0",
            "description": "desc",
            "elasticsearch": "ok",
        }
        del kwargs[field]
        with pytest.raises(ValidationError):
            ServiceInfoResponse(**kwargs)  # type: ignore[arg-type]


class TestServiceInfoResponseEdgeCases:
    """Boundary inputs for ServiceInfoResponse."""

    def test_empty_str_fields_accepted(self) -> None:
        resp = ServiceInfoResponse(
            name="",
            version="",
            description="",
            elasticsearch="ok",
        )
        assert resp.name == ""
        assert resp.version == ""
        assert resp.description == ""

    def test_unicode_metadata_preserved(self) -> None:
        resp = ServiceInfoResponse(
            name="DDBJ 検索 API",
            version="0.1.0",
            description="日本DNAデータバンクの検索API",
            elasticsearch="ok",
        )
        dumped = resp.model_dump()
        assert dumped["name"] == "DDBJ 検索 API"
        assert dumped["description"] == "日本DNAデータバンクの検索API"

    def test_invalid_elasticsearch_status_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ServiceInfoResponse(
                name="API",
                version="0.1.0",
                description="desc",
                elasticsearch="down",  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize("bad", ["", "OK", "Unavailable", "degraded", " ", "ok "])
    def test_invalid_elasticsearch_literal_variants_rejected(self, bad: str) -> None:
        # Literal は完全一致なので大文字違い・空文字・前後空白も拒否される。
        with pytest.raises(ValidationError):
            ServiceInfoResponse(
                name="API",
                version="0.1.0",
                description="desc",
                elasticsearch=bad,  # type: ignore[arg-type]
            )


class TestServiceInfoResponsePBT:
    """Property-based: any str triple + valid Literal round-trips cleanly."""

    @given(
        name=st.text(max_size=200),
        version=st.text(max_size=50),
        description=st.text(max_size=500),
        status=st.sampled_from(_VALID_ES_STATUSES),
    )
    def test_round_trip_preserves_fields(
        self,
        name: str,
        version: str,
        description: str,
        status: str,
    ) -> None:
        resp = ServiceInfoResponse(
            name=name,
            version=version,
            description=description,
            elasticsearch=status,  # type: ignore[arg-type]
        )
        dumped = resp.model_dump()
        assert dumped == {
            "name": name,
            "version": version,
            "description": description,
            "elasticsearch": status,
        }
        rebuilt = ServiceInfoResponse(**dumped)
        assert rebuilt == resp

    @given(
        status=st.text(max_size=20).filter(lambda s: s not in _VALID_ES_STATUSES),
    )
    def test_arbitrary_non_literal_status_rejected(self, status: str) -> None:
        with pytest.raises(ValidationError):
            ServiceInfoResponse(
                name="API",
                version="0.1.0",
                description="desc",
                elasticsearch=status,  # type: ignore[arg-type]
            )
