"""Tests for ``ddbj_search_api.utils``.

Focuses on ``parse_facets`` semantics: every ``Facets`` field is
optional, and ``None`` indicates "not aggregated" while ``[]``
indicates "aggregated but no buckets".
"""

from __future__ import annotations

from typing import Any

import pytest

from ddbj_search_api.schemas.common import Facets
from ddbj_search_api.utils import parse_facets


def _aggregations(**buckets_per_field: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a minimal ES aggregations dict from per-field bucket lists.

    Keyword names mirror the camelCase field names ES emits and ``Facets``
    accepts as alias keys.
    """

    return {name: {"buckets": buckets} for name, buckets in buckets_per_field.items()}


class TestParseFacetsOptionalSemantics:
    """Aggregations omitted from ES leave the corresponding Facets field
    as ``None`` so callers can distinguish 'not aggregated' from
    'aggregated but no buckets'."""

    def test_empty_aggregations_returns_all_none(self) -> None:
        facets = parse_facets({})
        assert facets.organism is None
        assert facets.accessibility is None
        assert facets.type is None
        assert facets.object_type is None
        assert facets.library_strategy is None
        assert facets.experiment_type is None
        assert facets.study_type is None
        assert facets.submission_type is None

    def test_aggregated_field_with_no_buckets_returns_empty_list(self) -> None:
        """A present aggregation with zero buckets is ``[]`` (distinct
        from the ``None`` returned for an absent aggregation)."""
        facets = parse_facets(_aggregations(organism=[]))
        assert facets.organism == []
        # Other fields stay None because they were not aggregated.
        assert facets.accessibility is None

    def test_aggregated_field_with_buckets_populates_list(self) -> None:
        facets = parse_facets(
            _aggregations(
                organism=[{"key": "Homo sapiens", "doc_count": 10}],
                accessibility=[{"key": "unrestricted", "doc_count": 5}],
            )
        )
        assert facets.organism is not None
        assert len(facets.organism) == 1
        assert facets.organism[0].value == "Homo sapiens"
        assert facets.organism[0].count == 10
        assert facets.accessibility is not None
        assert facets.accessibility[0].value == "unrestricted"

    @pytest.mark.parametrize(
        "agg_name",
        [
            "type",
            "objectType",
            "libraryStrategy",
            "librarySource",
            "librarySelection",
            "platform",
            "instrumentModel",
            "experimentType",
            "studyType",
            "submissionType",
        ],
    )
    def test_individual_optional_field_aggregated(self, agg_name: str) -> None:
        """Each opt-in field maps from its ES aggregation key."""
        facets = parse_facets(_aggregations(**{agg_name: [{"key": "v", "doc_count": 1}]}))
        assert isinstance(facets, Facets)
        attr = {
            "type": "type",
            "objectType": "object_type",
            "libraryStrategy": "library_strategy",
            "librarySource": "library_source",
            "librarySelection": "library_selection",
            "platform": "platform",
            "instrumentModel": "instrument_model",
            "experimentType": "experiment_type",
            "studyType": "study_type",
            "submissionType": "submission_type",
        }[agg_name]
        value = getattr(facets, attr)
        assert value is not None
        assert value[0].value == "v"
        assert value[0].count == 1

    def test_pick_one_field_leaves_others_none(self) -> None:
        """`facets=objectType` で ES が objectType のみ返す状況を再現:
        organism/accessibility は aggregations に含まれないので ``None``
        になり、「集計したが 0 件」(``[]``) と区別できる。
        """
        facets = parse_facets(
            _aggregations(
                objectType=[
                    {"key": "BioProject", "doc_count": 100},
                    {"key": "UmbrellaBioProject", "doc_count": 10},
                ],
            )
        )
        assert facets.object_type is not None
        assert len(facets.object_type) == 2
        assert facets.organism is None
        assert facets.accessibility is None

    def test_unknown_aggregation_key_ignored(self) -> None:
        """ES が想定外のキーを返してもクラッシュしない (parse_facets は
        既知キーだけを拾い、未知キーは silent skip する)。"""
        facets = parse_facets(
            {
                "organism": {"buckets": [{"key": "Homo sapiens", "doc_count": 1}]},
                "unknownAgg": {"buckets": [{"key": "x", "doc_count": 1}]},
            }
        )
        assert facets.organism is not None
        assert facets.organism[0].value == "Homo sapiens"
