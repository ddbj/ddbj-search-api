"""Tests for ES HTTP client (ddbj_search_api.es.client).

These tests mock httpx to verify the client sends correct requests
and handles responses/errors properly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ddbj_search_api.es.client import es_get_source_stream, es_head_exists, es_search


def _mock_response(
    json_data: dict[str, Any],
    status_code: int = 200,
) -> httpx.Response:
    """Create an httpx.Response for test assertions."""
    return httpx.Response(
        status_code,
        json=json_data,
        request=httpx.Request("POST", "http://localhost:9200/test"),
    )


@pytest.fixture
def mock_client() -> AsyncMock:
    """Create a mock httpx.AsyncClient."""
    return AsyncMock(spec=httpx.AsyncClient)


# ===================================================================
# es_search
# ===================================================================


class TestEsSearch:
    """es_search sends POST to /{index}/_search."""

    @pytest.mark.asyncio
    async def test_returns_response_json(
        self,
        mock_client: AsyncMock,
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

    @pytest.mark.asyncio
    async def test_posts_to_correct_endpoint(
        self,
        mock_client: AsyncMock,
    ) -> None:
        mock_client.post.return_value = _mock_response({"hits": {}})

        await es_search(mock_client, "bioproject", {"query": {}})

        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/bioproject/_search"

    @pytest.mark.asyncio
    async def test_adds_track_total_hits(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """track_total_hits is always added to the request body."""
        mock_client.post.return_value = _mock_response({"hits": {}})

        await es_search(mock_client, "entries", {"query": {"match_all": {}}})

        call_args = mock_client.post.call_args
        body = call_args[1]["json"]
        assert body["track_total_hits"] is True

    @pytest.mark.asyncio
    async def test_does_not_mutate_input_body(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """The caller's body dict must not be modified."""
        mock_client.post.return_value = _mock_response({"hits": {}})
        original: dict[str, Any] = {"query": {"match_all": {}}}

        await es_search(mock_client, "entries", original)

        assert "track_total_hits" not in original

    @pytest.mark.asyncio
    async def test_forwards_body_contents(
        self,
        mock_client: AsyncMock,
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

    @pytest.mark.asyncio
    async def test_raises_on_500(self, mock_client: AsyncMock) -> None:
        mock_client.post.return_value = _mock_response(
            {"error": "internal"},
            status_code=500,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await es_search(mock_client, "entries", {})

    @pytest.mark.asyncio
    async def test_raises_on_400(self, mock_client: AsyncMock) -> None:
        mock_client.post.return_value = _mock_response(
            {"error": "bad request"},
            status_code=400,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await es_search(mock_client, "entries", {})


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

    @pytest.mark.asyncio
    async def test_returns_response_on_found(
        self,
        mock_client: AsyncMock,
    ) -> None:
        stream_resp = _mock_stream_response(200)
        mock_client.send.return_value = stream_resp

        result = await es_get_source_stream(
            mock_client,
            "bioproject",
            "PRJDB1",
        )
        assert result is not None
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_none_on_404(
        self,
        mock_client: AsyncMock,
    ) -> None:
        stream_resp = _mock_stream_response(404)
        mock_client.send.return_value = stream_resp

        result = await es_get_source_stream(
            mock_client,
            "bioproject",
            "NOTEXIST",
        )
        assert result is None
        stream_resp.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_source_includes_parameter(
        self,
        mock_client: AsyncMock,
    ) -> None:
        stream_resp = _mock_stream_response(200)
        mock_client.send.return_value = stream_resp

        await es_get_source_stream(
            mock_client,
            "bioproject",
            "PRJDB1",
            source_includes="dbXrefs",
        )

        build_args = mock_client.build_request.call_args
        assert build_args[0][0] == "GET"
        url = build_args[0][1]
        assert "/bioproject/_source/PRJDB1" in url

    @pytest.mark.asyncio
    async def test_raises_on_500(
        self,
        mock_client: AsyncMock,
    ) -> None:
        stream_resp = _mock_stream_response(500)
        mock_client.send.return_value = stream_resp

        with pytest.raises(httpx.HTTPStatusError):
            await es_get_source_stream(
                mock_client,
                "bioproject",
                "PRJDB1",
            )

    @pytest.mark.asyncio
    async def test_source_excludes_parameter(
        self,
        mock_client: AsyncMock,
    ) -> None:
        stream_resp = _mock_stream_response(200)
        mock_client.send.return_value = stream_resp

        await es_get_source_stream(
            mock_client,
            "bioproject",
            "PRJDB1",
            source_excludes="dbXrefs",
        )

        build_args = mock_client.build_request.call_args
        params = build_args[1].get("params", {})
        assert params["_source_excludes"] == "dbXrefs"

    @pytest.mark.asyncio
    async def test_no_params_when_omitted(
        self,
        mock_client: AsyncMock,
    ) -> None:
        stream_resp = _mock_stream_response(200)
        mock_client.send.return_value = stream_resp

        await es_get_source_stream(
            mock_client,
            "bioproject",
            "PRJDB1",
        )

        build_args = mock_client.build_request.call_args
        params = build_args[1].get("params", {})
        assert "_source_includes" not in params
        assert "_source_excludes" not in params

    @pytest.mark.asyncio
    async def test_both_includes_and_excludes(
        self,
        mock_client: AsyncMock,
    ) -> None:
        stream_resp = _mock_stream_response(200)
        mock_client.send.return_value = stream_resp

        await es_get_source_stream(
            mock_client,
            "bioproject",
            "PRJDB1",
            source_includes="identifier,type",
            source_excludes="dbXrefs",
        )

        build_args = mock_client.build_request.call_args
        params = build_args[1].get("params", {})
        assert params["_source_includes"] == "identifier,type"
        assert params["_source_excludes"] == "dbXrefs"


# ===================================================================
# es_head_exists
# ===================================================================


class TestEsHeadExists:
    """es_head_exists: document existence check via HEAD."""

    @pytest.mark.asyncio
    async def test_returns_true_on_200(
        self,
        mock_client: AsyncMock,
    ) -> None:
        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        response.raise_for_status = MagicMock()
        mock_client.head.return_value = response

        result = await es_head_exists(mock_client, "bioproject", "PRJDB1")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_404(
        self,
        mock_client: AsyncMock,
    ) -> None:
        response = MagicMock(spec=httpx.Response)
        response.status_code = 404
        mock_client.head.return_value = response

        result = await es_head_exists(mock_client, "bioproject", "NOTEXIST")
        assert result is False

    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(
        self,
        mock_client: AsyncMock,
    ) -> None:
        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        response.raise_for_status = MagicMock()
        mock_client.head.return_value = response

        await es_head_exists(mock_client, "biosample", "SAMD001")

        mock_client.head.assert_awaited_once_with("/biosample/_source/SAMD001")

    @pytest.mark.asyncio
    async def test_raises_on_500(
        self,
        mock_client: AsyncMock,
    ) -> None:
        response = MagicMock(spec=httpx.Response)
        response.status_code = 500
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error",
            request=httpx.Request("HEAD", "http://localhost:9200/test"),
            response=httpx.Response(500),
        )
        mock_client.head.return_value = response

        with pytest.raises(httpx.HTTPStatusError):
            await es_head_exists(mock_client, "bioproject", "PRJDB1")
