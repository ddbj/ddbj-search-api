"""Tests for ES HTTP client (ddbj_search_api.es.client).

These tests mock httpx to verify the client sends correct requests
and handles responses/errors properly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ddbj_search_api.es.client import (
    es_close_pit,
    es_get_identifier,
    es_get_source_stream,
    es_head_exists,
    es_open_pit,
    es_search,
    es_search_with_pit,
)


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


class TestEsGetIdentifier:
    """es_get_identifier: lightweight identifier resolution."""

    @pytest.mark.asyncio
    async def test_returns_identifier_on_found(
        self,
        mock_client: AsyncMock,
    ) -> None:
        mock_client.get.return_value = _mock_response(
            {"identifier": "JGAS000001"},
        )
        result = await es_get_identifier(mock_client, "jga-study", "JGAS000556")
        assert result == "JGAS000001"

    @pytest.mark.asyncio
    async def test_returns_id_on_404(
        self,
        mock_client: AsyncMock,
    ) -> None:
        mock_client.get.return_value = _mock_response(
            {},
            status_code=404,
        )
        result = await es_get_identifier(mock_client, "jga-study", "NOTEXIST")
        assert result == "NOTEXIST"

    @pytest.mark.asyncio
    async def test_returns_id_when_field_missing(
        self,
        mock_client: AsyncMock,
    ) -> None:
        mock_client.get.return_value = _mock_response({})
        result = await es_get_identifier(mock_client, "jga-study", "JGAS000556")
        assert result == "JGAS000556"

    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(
        self,
        mock_client: AsyncMock,
    ) -> None:
        mock_client.get.return_value = _mock_response(
            {"identifier": "PRJDB1"},
        )
        await es_get_identifier(mock_client, "bioproject", "PRJDB1")
        call_args = mock_client.get.call_args
        assert call_args[0][0] == "/bioproject/_source/PRJDB1"
        assert call_args[1]["params"]["_source_includes"] == "identifier"

    @pytest.mark.asyncio
    async def test_raises_on_500(
        self,
        mock_client: AsyncMock,
    ) -> None:
        mock_client.get.return_value = _mock_response(
            {"error": "internal"},
            status_code=500,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await es_get_identifier(mock_client, "bioproject", "PRJDB1")


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


# ===================================================================
# es_open_pit
# ===================================================================


class TestEsOpenPit:
    """es_open_pit opens a PIT and returns the ID."""

    @pytest.mark.asyncio
    async def test_returns_pit_id(self, mock_client: AsyncMock) -> None:
        mock_client.post.return_value = _mock_response({"id": "pit_abc123"})

        pit_id = await es_open_pit(mock_client, "biosample")
        assert pit_id == "pit_abc123"
        mock_client.post.assert_called_once_with(
            "/biosample/_pit",
            params={"keep_alive": "5m"},
        )

    @pytest.mark.asyncio
    async def test_custom_keep_alive(self, mock_client: AsyncMock) -> None:
        mock_client.post.return_value = _mock_response({"id": "pit_xyz"})

        await es_open_pit(mock_client, "bioproject", keep_alive="10m")
        mock_client.post.assert_called_once_with(
            "/bioproject/_pit",
            params={"keep_alive": "10m"},
        )

    @pytest.mark.asyncio
    async def test_raises_on_error(self, mock_client: AsyncMock) -> None:
        response = _mock_response({}, status_code=500)
        mock_client.post.return_value = response

        with pytest.raises(httpx.HTTPStatusError):
            await es_open_pit(mock_client, "biosample")


# ===================================================================
# es_close_pit
# ===================================================================


class TestEsClosePit:
    """es_close_pit is best-effort: ignores 404."""

    @pytest.mark.asyncio
    async def test_close_success(self, mock_client: AsyncMock) -> None:
        mock_client.request.return_value = _mock_response(
            {"succeeded": True},
            status_code=200,
        )

        await es_close_pit(mock_client, "pit_abc123")
        mock_client.request.assert_called_once_with(
            "DELETE",
            "/_pit",
            json={"id": "pit_abc123"},
        )

    @pytest.mark.asyncio
    async def test_ignores_404(self, mock_client: AsyncMock) -> None:
        response = MagicMock(spec=httpx.Response)
        response.status_code = 404
        mock_client.request.return_value = response

        await es_close_pit(mock_client, "pit_expired")

    @pytest.mark.asyncio
    async def test_ignores_exceptions(self, mock_client: AsyncMock) -> None:
        mock_client.request.side_effect = Exception("connection error")

        await es_close_pit(mock_client, "pit_broken")


# ===================================================================
# es_search_with_pit
# ===================================================================


class TestEsSearchWithPit:
    """es_search_with_pit sends POST /_search with PIT body."""

    @pytest.mark.asyncio
    async def test_returns_response_json(self, mock_client: AsyncMock) -> None:
        es_response = {
            "pit_id": "pit_updated",
            "hits": {
                "total": {"value": 100, "relation": "eq"},
                "hits": [
                    {
                        "_source": {"identifier": "SAMD00001"},
                        "sort": ["2026-01-15", "SAMD00001"],
                    },
                ],
            },
        }
        mock_client.post.return_value = _mock_response(es_response)

        body = {
            "query": {"match_all": {}},
            "sort": [
                {"datePublished": {"order": "desc"}},
                {"_id": {"order": "asc"}},
            ],
            "size": 10,
            "pit": {"id": "pit_abc123", "keep_alive": "5m"},
            "search_after": ["2026-01-10", "SAMD00000"],
        }
        result = await es_search_with_pit(mock_client, body)
        assert result == es_response

    @pytest.mark.asyncio
    async def test_sends_post_to_search_without_index(
        self,
        mock_client: AsyncMock,
    ) -> None:
        mock_client.post.return_value = _mock_response({"hits": {"total": {"value": 0}, "hits": []}})

        await es_search_with_pit(mock_client, {"query": {"match_all": {}}})
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/_search"

    @pytest.mark.asyncio
    async def test_sets_track_total_hits(self, mock_client: AsyncMock) -> None:
        mock_client.post.return_value = _mock_response({"hits": {"total": {"value": 0}, "hits": []}})

        await es_search_with_pit(mock_client, {"query": {"match_all": {}}})
        call_args = mock_client.post.call_args
        sent_body = call_args[1]["json"]
        assert sent_body["track_total_hits"] is True

    @pytest.mark.asyncio
    async def test_raises_on_error(self, mock_client: AsyncMock) -> None:
        response = _mock_response({}, status_code=404)
        mock_client.post.return_value = response

        with pytest.raises(httpx.HTTPStatusError):
            await es_search_with_pit(mock_client, {"query": {"match_all": {}}})
