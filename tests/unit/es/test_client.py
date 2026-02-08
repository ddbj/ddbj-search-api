"""Tests for ES HTTP client (ddbj_search_api.es.client).

These tests mock httpx to verify the client sends correct requests
and handles responses/errors properly.
"""
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ddbj_search_api.es.client import (es_get_source_stream, es_search,
                                      es_search_with_script_fields)


def _mock_response(
    json_data: Dict[str, Any],
    status_code: int = 200,
) -> httpx.Response:
    """Create an httpx.Response for test assertions."""
    return httpx.Response(
        status_code,
        json=json_data,
        request=httpx.Request("POST", "http://localhost:9200/test"),
    )


@pytest.fixture()
def mock_client() -> AsyncMock:
    """Create a mock httpx.AsyncClient."""
    return AsyncMock(spec=httpx.AsyncClient)


# ===================================================================
# es_search
# ===================================================================


class TestEsSearch:
    """es_search sends POST to /{index}/_search."""

    @pytest.mark.asyncio()
    async def test_returns_response_json(
        self, mock_client: AsyncMock,
    ) -> None:
        es_response = {
            "hits": {
                "total": {"value": 42, "relation": "eq"},
                "hits": [
                    {"_source": {"identifier": "PRJDB1"}},
                ],
            },
        }
        mock_client.post.return_value = _mock_response(es_response)

        result = await es_search(mock_client, "entries", {})
        assert result == es_response

    @pytest.mark.asyncio()
    async def test_posts_to_correct_endpoint(
        self, mock_client: AsyncMock,
    ) -> None:
        mock_client.post.return_value = _mock_response({"hits": {}})

        await es_search(mock_client, "bioproject", {"query": {}})

        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/bioproject/_search"

    @pytest.mark.asyncio()
    async def test_adds_track_total_hits(
        self, mock_client: AsyncMock,
    ) -> None:
        """track_total_hits is always added to the request body."""
        mock_client.post.return_value = _mock_response({"hits": {}})

        await es_search(mock_client, "entries", {"query": {"match_all": {}}})

        call_args = mock_client.post.call_args
        body = call_args[1]["json"]
        assert body["track_total_hits"] is True

    @pytest.mark.asyncio()
    async def test_does_not_mutate_input_body(
        self, mock_client: AsyncMock,
    ) -> None:
        """The caller's body dict must not be modified."""
        mock_client.post.return_value = _mock_response({"hits": {}})
        original = {"query": {"match_all": {}}}

        await es_search(mock_client, "entries", original)

        assert "track_total_hits" not in original

    @pytest.mark.asyncio()
    async def test_forwards_body_contents(
        self, mock_client: AsyncMock,
    ) -> None:
        mock_client.post.return_value = _mock_response({"hits": {}})
        body = {
            "query": {"match_all": {}},
            "from": 10,
            "size": 20,
            "sort": [{"datePublished": {"order": "asc"}}],
        }

        await es_search(mock_client, "entries", body)

        sent_body = mock_client.post.call_args[1]["json"]
        assert sent_body["query"] == body["query"]
        assert sent_body["from"] == 10
        assert sent_body["size"] == 20
        assert sent_body["sort"] == body["sort"]


class TestEsSearchErrors:
    """es_search raises on non-2xx responses."""

    @pytest.mark.asyncio()
    async def test_raises_on_500(self, mock_client: AsyncMock) -> None:
        mock_client.post.return_value = _mock_response(
            {"error": "internal"}, status_code=500,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await es_search(mock_client, "entries", {})

    @pytest.mark.asyncio()
    async def test_raises_on_400(self, mock_client: AsyncMock) -> None:
        mock_client.post.return_value = _mock_response(
            {"error": "bad request"}, status_code=400,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await es_search(mock_client, "entries", {})


# ===================================================================
# es_search_with_script_fields
# ===================================================================


class TestEsSearchWithScriptFields:
    """es_search_with_script_fields: single doc with script_fields."""

    @pytest.mark.asyncio()
    async def test_returns_merged_source(
        self, mock_client: AsyncMock,
    ) -> None:
        es_response = {
            "hits": {
                "total": {"value": 1, "relation": "eq"},
                "hits": [{
                    "_source": {
                        "identifier": "PRJDB1",
                        "type": "bioproject",
                    },
                    "fields": {
                        "dbXrefsTruncated": [
                            {"identifier": "BS1", "type": "biosample"},
                        ],
                        "dbXrefsCountByType": [{"biosample": 5}],
                    },
                }],
            },
        }
        mock_client.post.return_value = _mock_response(es_response)

        result = await es_search_with_script_fields(
            mock_client, "bioproject", "PRJDB1", 100,
        )
        assert result is not None
        assert result["identifier"] == "PRJDB1"
        assert result["dbXrefs"] == [
            {"identifier": "BS1", "type": "biosample"},
        ]
        assert result["dbXrefsCount"] == {"biosample": 5}

    @pytest.mark.asyncio()
    async def test_returns_none_when_not_found(
        self, mock_client: AsyncMock,
    ) -> None:
        es_response = {
            "hits": {
                "total": {"value": 0, "relation": "eq"},
                "hits": [],
            },
        }
        mock_client.post.return_value = _mock_response(es_response)

        result = await es_search_with_script_fields(
            mock_client, "bioproject", "NOTEXIST", 100,
        )
        assert result is None

    @pytest.mark.asyncio()
    async def test_posts_to_correct_endpoint(
        self, mock_client: AsyncMock,
    ) -> None:
        es_response = {
            "hits": {"total": {"value": 0, "relation": "eq"}, "hits": []},
        }
        mock_client.post.return_value = _mock_response(es_response)

        await es_search_with_script_fields(
            mock_client, "biosample", "SAMD1", 50,
        )

        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/biosample/_search"

    @pytest.mark.asyncio()
    async def test_request_body_structure(
        self, mock_client: AsyncMock,
    ) -> None:
        """Verify the request body contains expected keys."""
        es_response = {
            "hits": {"total": {"value": 0, "relation": "eq"}, "hits": []},
        }
        mock_client.post.return_value = _mock_response(es_response)

        await es_search_with_script_fields(
            mock_client, "bioproject", "PRJDB1", 200,
        )

        body = mock_client.post.call_args[1]["json"]
        assert body["query"] == {"term": {"_id": "PRJDB1"}}
        assert body["size"] == 1
        assert "dbXrefs" in body["_source"]["excludes"]
        assert "dbXrefsTruncated" in body["script_fields"]
        assert "dbXrefsCountByType" in body["script_fields"]
        limit_param = body["script_fields"]["dbXrefsTruncated"]["script"]["params"]["limit"]
        assert limit_param == 200


    @pytest.mark.asyncio()
    async def test_normalizes_single_db_xref(
        self, mock_client: AsyncMock,
    ) -> None:
        """Single xref: ES returns [{...}], result is a one-element list."""
        es_response = {
            "hits": {
                "total": {"value": 1, "relation": "eq"},
                "hits": [{
                    "_source": {
                        "identifier": "PRJDB2",
                        "type": "bioproject",
                    },
                    "fields": {
                        "dbXrefsTruncated": [
                            {"identifier": "BS1", "type": "biosample"},
                        ],
                        "dbXrefsCountByType": [{"biosample": 1}],
                    },
                }],
            },
        }
        mock_client.post.return_value = _mock_response(es_response)

        result = await es_search_with_script_fields(
            mock_client, "bioproject", "PRJDB2", 100,
        )
        assert result is not None
        assert result["dbXrefs"] == [
            {"identifier": "BS1", "type": "biosample"},
        ]
        assert result["dbXrefsCount"] == {"biosample": 1}

    @pytest.mark.asyncio()
    async def test_handles_empty_db_xrefs(
        self, mock_client: AsyncMock,
    ) -> None:
        """Zero xrefs: ES omits the key, result is an empty list."""
        es_response = {
            "hits": {
                "total": {"value": 1, "relation": "eq"},
                "hits": [{
                    "_source": {
                        "identifier": "PRJDB3",
                        "type": "bioproject",
                    },
                    "fields": {},
                }],
            },
        }
        mock_client.post.return_value = _mock_response(es_response)

        result = await es_search_with_script_fields(
            mock_client, "bioproject", "PRJDB3", 100,
        )
        assert result is not None
        assert result["dbXrefs"] == []
        assert result["dbXrefsCount"] == {}


class TestEsSearchWithScriptFieldsErrors:

    @pytest.mark.asyncio()
    async def test_raises_on_500(self, mock_client: AsyncMock) -> None:
        mock_client.post.return_value = _mock_response(
            {"error": "internal"}, status_code=500,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await es_search_with_script_fields(
                mock_client, "bioproject", "PRJDB1", 100,
            )


# ===================================================================
# es_get_source_stream
# ===================================================================


def _mock_stream_response(
    status_code: int = 200,
) -> MagicMock:
    """Create a mock httpx.Response for streaming tests."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.aclose = AsyncMock()
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error",
            request=httpx.Request("GET", "http://localhost:9200/test"),
            response=httpx.Response(status_code),
        )

    return response


class TestEsGetSourceStream:
    """es_get_source_stream: streaming _source retrieval."""

    @pytest.mark.asyncio()
    async def test_returns_response_on_found(
        self, mock_client: AsyncMock,
    ) -> None:
        stream_resp = _mock_stream_response(200)
        mock_client.send.return_value = stream_resp

        result = await es_get_source_stream(
            mock_client, "bioproject", "PRJDB1",
        )
        assert result is not None
        assert result.status_code == 200

    @pytest.mark.asyncio()
    async def test_returns_none_on_404(
        self, mock_client: AsyncMock,
    ) -> None:
        stream_resp = _mock_stream_response(404)
        mock_client.send.return_value = stream_resp

        result = await es_get_source_stream(
            mock_client, "bioproject", "NOTEXIST",
        )
        assert result is None
        stream_resp.aclose.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_source_includes_parameter(
        self, mock_client: AsyncMock,
    ) -> None:
        stream_resp = _mock_stream_response(200)
        mock_client.send.return_value = stream_resp

        await es_get_source_stream(
            mock_client, "bioproject", "PRJDB1",
            source_includes="dbXrefs",
        )

        build_args = mock_client.build_request.call_args
        assert build_args[0][0] == "GET"
        url = build_args[0][1]
        assert "/bioproject/_source/PRJDB1" in url

    @pytest.mark.asyncio()
    async def test_raises_on_500(
        self, mock_client: AsyncMock,
    ) -> None:
        stream_resp = _mock_stream_response(500)
        mock_client.send.return_value = stream_resp

        with pytest.raises(httpx.HTTPStatusError):
            await es_get_source_stream(
                mock_client, "bioproject", "PRJDB1",
            )
