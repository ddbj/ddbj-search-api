"""Tests for Solr HTTP client (ddbj_search_api.solr.client).

Mirrors the style of ``tests/unit/es/test_client.py``: mock httpx to
verify URL assembly, param forwarding, and raise-on-error semantics.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from ddbj_search_api.solr.client import arsa_search, txsearch_search


def _mock_response(
    json_data: dict[str, Any],
    status_code: int = 200,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=json_data,
        request=httpx.Request("GET", "http://example/select"),
    )


@pytest.fixture
def mock_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


# === arsa_search ===


class TestArsaSearch:
    @pytest.mark.asyncio
    async def test_returns_response_json(self, mock_client: AsyncMock) -> None:
        payload = {"response": {"numFound": 0, "start": 0, "docs": []}}
        mock_client.get.return_value = _mock_response(payload)

        result = await arsa_search(
            mock_client,
            base_url="http://a012:51981/solr",
            core="collection1",
            params={"q": "*:*"},
        )
        assert result == payload

    @pytest.mark.asyncio
    async def test_gets_correct_url(self, mock_client: AsyncMock) -> None:
        mock_client.get.return_value = _mock_response({"response": {}})

        await arsa_search(
            mock_client,
            base_url="http://a012:51981/solr",
            core="collection1",
            params={"q": "cancer"},
        )

        assert mock_client.get.call_args[0][0] == "http://a012:51981/solr/collection1/select"

    @pytest.mark.asyncio
    async def test_uses_core_from_argument(self, mock_client: AsyncMock) -> None:
        mock_client.get.return_value = _mock_response({"response": {}})

        await arsa_search(
            mock_client,
            base_url="http://a012:51981/solr",
            core="arsa",
            params={"q": "*"},
        )

        assert mock_client.get.call_args[0][0] == "http://a012:51981/solr/arsa/select"

    @pytest.mark.asyncio
    async def test_forwards_params(self, mock_client: AsyncMock) -> None:
        mock_client.get.return_value = _mock_response({"response": {}})
        params = {"q": '"HIF-1"', "rows": "0", "wt": "json"}

        await arsa_search(
            mock_client,
            base_url="http://a012:51981/solr",
            core="collection1",
            params=params,
        )

        assert mock_client.get.call_args[1]["params"] == params


class TestArsaSearchErrors:
    @pytest.mark.asyncio
    async def test_raises_on_500(self, mock_client: AsyncMock) -> None:
        mock_client.get.return_value = _mock_response({"error": "internal"}, status_code=500)

        with pytest.raises(httpx.HTTPStatusError):
            await arsa_search(
                mock_client,
                base_url="http://a012:51981/solr",
                core="collection1",
                params={"q": "*"},
            )

    @pytest.mark.asyncio
    async def test_raises_on_400(self, mock_client: AsyncMock) -> None:
        mock_client.get.return_value = _mock_response({"error": "bad"}, status_code=400)

        with pytest.raises(httpx.HTTPStatusError):
            await arsa_search(
                mock_client,
                base_url="http://a012:51981/solr",
                core="collection1",
                params={"q": "*"},
            )

    @pytest.mark.asyncio
    async def test_propagates_timeout(self, mock_client: AsyncMock) -> None:
        mock_client.get.side_effect = httpx.TimeoutException("timed out")

        with pytest.raises(httpx.TimeoutException):
            await arsa_search(
                mock_client,
                base_url="http://a012:51981/solr",
                core="collection1",
                params={"q": "*"},
            )

    @pytest.mark.asyncio
    async def test_propagates_connect_error(self, mock_client: AsyncMock) -> None:
        mock_client.get.side_effect = httpx.ConnectError("refused")

        with pytest.raises(httpx.ConnectError):
            await arsa_search(
                mock_client,
                base_url="http://a012:51981/solr",
                core="collection1",
                params={"q": "*"},
            )


# === txsearch_search ===


class TestTxsearchSearch:
    @pytest.mark.asyncio
    async def test_returns_response_json(self, mock_client: AsyncMock) -> None:
        payload = {"response": {"numFound": 0, "start": 0, "docs": []}}
        mock_client.get.return_value = _mock_response(payload)

        result = await txsearch_search(
            mock_client,
            url="http://localhost:32005/solr-rgm/ncbi_taxonomy/select",
            params={"q": "*"},
        )
        assert result == payload

    @pytest.mark.asyncio
    async def test_uses_full_url_as_is(self, mock_client: AsyncMock) -> None:
        """``txsearch_search`` must pass the URL unchanged (sub-path included)."""
        mock_client.get.return_value = _mock_response({"response": {}})

        await txsearch_search(
            mock_client,
            url="http://localhost:32005/solr-rgm/ncbi_taxonomy/select",
            params={"q": "Homo"},
        )

        assert mock_client.get.call_args[0][0] == "http://localhost:32005/solr-rgm/ncbi_taxonomy/select"

    @pytest.mark.asyncio
    async def test_forwards_params(self, mock_client: AsyncMock) -> None:
        mock_client.get.return_value = _mock_response({"response": {}})
        params = {"q": '"Homo sapiens"', "rows": "20", "wt": "json"}

        await txsearch_search(
            mock_client,
            url="http://localhost:32005/solr-rgm/ncbi_taxonomy/select",
            params=params,
        )

        assert mock_client.get.call_args[1]["params"] == params


class TestTxsearchSearchErrors:
    @pytest.mark.asyncio
    async def test_raises_on_500(self, mock_client: AsyncMock) -> None:
        mock_client.get.return_value = _mock_response({"error": "internal"}, status_code=500)

        with pytest.raises(httpx.HTTPStatusError):
            await txsearch_search(
                mock_client,
                url="http://localhost:32005/solr-rgm/ncbi_taxonomy/select",
                params={"q": "*"},
            )

    @pytest.mark.asyncio
    async def test_propagates_timeout(self, mock_client: AsyncMock) -> None:
        mock_client.get.side_effect = httpx.TimeoutException("timed out")

        with pytest.raises(httpx.TimeoutException):
            await txsearch_search(
                mock_client,
                url="http://localhost:32005/solr-rgm/ncbi_taxonomy/select",
                params={"q": "*"},
            )
