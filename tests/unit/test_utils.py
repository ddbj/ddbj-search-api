"""Tests for ddbj_search_api.utils."""
from typing import Any, Dict, List

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ddbj_search_api.utils import (count_db_xrefs_by_type, entry_to_dict,
                                   truncate_db_xrefs)
from ddbj_search_converter.schema import BioProject, Organism, Xref


# === Helper: minimal BioProject for testing ===


def _make_bioproject(**overrides: Any) -> BioProject:
    """Create a minimal BioProject for testing."""
    defaults: Dict[str, Any] = {
        "identifier": "PRJDB1",
        "properties": None,
        "distribution": [],
        "isPartOf": "BioProject",
        "type": "bioproject",
        "objectType": "BioProject",
        "name": "Test",
        "url": "https://example.com",
        "organism": None,
        "title": "Test Project",
        "description": "A test project.",
        "organization": [],
        "publication": [],
        "grant": [],
        "externalLink": [],
        "dbXrefs": [],
        "sameAs": [],
        "status": "live",
        "accessibility": "public-access",
        "dateCreated": None,
        "dateModified": None,
        "datePublished": None,
    }
    defaults.update(overrides)

    return BioProject(**defaults)


# === entry_to_dict ===


class TestEntryToDict:
    """entry_to_dict: convert Pydantic model to serialisable dict."""

    def test_returns_dict_with_aliases(self) -> None:
        entry = _make_bioproject()
        result = entry_to_dict(entry)
        assert "type" in result
        assert "isPartOf" in result
        assert result["identifier"] == "PRJDB1"

    def test_includes_properties_by_default(self) -> None:
        entry = _make_bioproject(properties={"key": "value"})
        result = entry_to_dict(entry)
        assert "properties" in result
        assert result["properties"] == {"key": "value"}

    def test_excludes_properties_when_false(self) -> None:
        entry = _make_bioproject(properties={"key": "value"})
        result = entry_to_dict(entry, include_properties=False)
        assert "properties" not in result

    def test_properties_absent_when_none_and_excluded(self) -> None:
        entry = _make_bioproject(properties=None)
        result = entry_to_dict(entry, include_properties=False)
        assert "properties" not in result


# === truncate_db_xrefs ===


class TestTruncateDbXrefs:
    """truncate_db_xrefs: truncate list to limit."""

    def test_empty_list(self) -> None:
        assert truncate_db_xrefs([], 10) == []

    def test_limit_zero_returns_empty(self) -> None:
        xrefs = [{"type": "biosample", "identifier": "BS1", "url": "http://x"}]
        assert truncate_db_xrefs(xrefs, 0) == []

    def test_list_shorter_than_limit(self) -> None:
        xrefs = [{"type": "biosample", "identifier": f"BS{i}", "url": "http://x"} for i in range(5)]
        result = truncate_db_xrefs(xrefs, 10)
        assert len(result) == 5

    def test_list_equal_to_limit(self) -> None:
        xrefs = [{"type": "biosample", "identifier": f"BS{i}", "url": "http://x"} for i in range(10)]
        result = truncate_db_xrefs(xrefs, 10)
        assert len(result) == 10

    def test_list_longer_than_limit(self) -> None:
        xrefs = [{"type": "biosample", "identifier": f"BS{i}", "url": "http://x"} for i in range(20)]
        result = truncate_db_xrefs(xrefs, 10)
        assert len(result) == 10

    def test_preserves_order(self) -> None:
        xrefs = [{"type": "biosample", "identifier": f"BS{i}", "url": "http://x"} for i in range(5)]
        result = truncate_db_xrefs(xrefs, 3)
        assert result[0]["identifier"] == "BS0"
        assert result[2]["identifier"] == "BS2"


class TestTruncateDbXrefsPBT:
    """Property-based tests for truncate_db_xrefs."""

    @given(
        n=st.integers(min_value=0, max_value=100),
        limit=st.integers(min_value=0, max_value=100),
    )
    def test_result_length_at_most_limit(self, n: int, limit: int) -> None:
        xrefs: List[Dict[str, Any]] = [
            {"type": "biosample", "identifier": f"BS{i}", "url": "http://x"}
            for i in range(n)
        ]
        result = truncate_db_xrefs(xrefs, limit)
        assert len(result) <= limit
        assert len(result) == min(n, limit)


# === count_db_xrefs_by_type ===


class TestCountDbXrefsByType:
    """count_db_xrefs_by_type: group and count by type field."""

    def test_empty_list(self) -> None:
        assert count_db_xrefs_by_type([]) == {}

    def test_single_type(self) -> None:
        xrefs = [
            {"type": "biosample", "identifier": "BS1"},
            {"type": "biosample", "identifier": "BS2"},
        ]
        result = count_db_xrefs_by_type(xrefs)
        assert result == {"biosample": 2}

    def test_multiple_types(self) -> None:
        xrefs = [
            {"type": "biosample", "identifier": "BS1"},
            {"type": "sra-run", "identifier": "SRR1"},
            {"type": "biosample", "identifier": "BS2"},
            {"type": "sra-run", "identifier": "SRR2"},
            {"type": "sra-run", "identifier": "SRR3"},
        ]
        result = count_db_xrefs_by_type(xrefs)
        assert result == {"biosample": 2, "sra-run": 3}

    def test_missing_type_field_uses_unknown(self) -> None:
        xrefs = [{"identifier": "MYSTERY"}]
        result = count_db_xrefs_by_type(xrefs)
        assert result == {"unknown": 1}


class TestCountDbXrefsByTypePBT:
    """Property-based tests for count_db_xrefs_by_type."""

    @given(
        types=st.lists(
            st.sampled_from(["biosample", "sra-run", "bioproject"]),
            min_size=0,
            max_size=50,
        )
    )
    def test_total_count_equals_input_length(
        self, types: List[str]
    ) -> None:
        xrefs = [{"type": t, "identifier": f"ID{i}"} for i, t in enumerate(types)]
        result = count_db_xrefs_by_type(xrefs)
        assert sum(result.values()) == len(types)
