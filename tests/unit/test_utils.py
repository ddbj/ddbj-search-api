"""Tests for ``ddbj_search_api.utils``.

Focuses on ``parse_facets`` semantics: every ``Facets`` field is
optional, and ``None`` indicates "not aggregated" while ``[]``
indicates "aggregated but no buckets". Organism bucket carries a
sub-aggregation that becomes the ``label`` field on
``OrganismFacetBucket``.
"""

from __future__ import annotations

import logging
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


def _organism_bucket(tax_id: str, count: int, name: str | None) -> dict[str, Any]:
    """Build an ``organism`` ES bucket with the ``name`` sub-aggregation.

    Pass ``name=None`` to emulate the data-quality edge case where
    ``organism.identifier`` exists but ``organism.name`` is missing on
    every doc inside the bucket (the sub-aggregation produces no buckets).
    """
    if name is None:
        sub_buckets: list[dict[str, Any]] = []
    else:
        sub_buckets = [{"key": name, "doc_count": count}]
    return {
        "key": tax_id,
        "doc_count": count,
        "name": {"buckets": sub_buckets},
    }


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
                organism=[_organism_bucket("9606", 10, "Homo sapiens")],
                accessibility=[{"key": "unrestricted", "doc_count": 5}],
            )
        )
        assert facets.organism is not None
        assert len(facets.organism) == 1
        assert facets.organism[0].value == "9606"
        assert facets.organism[0].count == 10
        assert facets.organism[0].label == "Homo sapiens"
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
            # db-portal sidebar 拡張で追加された 6 facet
            "relevance",
            "package",
            "model",
            "libraryLayout",
            "analysisType",
            "datasetType",
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
            "relevance": "relevance",
            "package": "package",
            "model": "model",
            "libraryLayout": "library_layout",
            "analysisType": "analysis_type",
            "datasetType": "dataset_type",
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
                "organism": {"buckets": [_organism_bucket("9606", 1, "Homo sapiens")]},
                "unknownAgg": {"buckets": [{"key": "x", "doc_count": 1}]},
            }
        )
        assert facets.organism is not None
        assert facets.organism[0].value == "9606"
        assert facets.organism[0].label == "Homo sapiens"


class TestParseFacetsOrganism:
    """Organism facet specifics: TaxID-as-value, label sourced from a
    ``name`` sub-aggregation, fallback when the sub-aggregation is empty.
    """

    def test_value_is_tax_id_string(self) -> None:
        """value should carry the TaxID (string) so callers can re-inject
        it into ``?organism=`` (which validates against ``^\\d+$``)."""
        facets = parse_facets(
            _aggregations(organism=[_organism_bucket("562", 1232567, "Escherichia coli")])
        )
        assert facets.organism is not None
        assert facets.organism[0].value == "562"
        assert facets.organism[0].label == "Escherichia coli"
        assert facets.organism[0].count == 1232567

    def test_bucket_order_preserved_from_es(self) -> None:
        """parse_facets must not re-sort buckets — ES already returns them
        in doc_count desc order, and changing it would break the UI's
        prefix-truncation expectations."""
        facets = parse_facets(
            _aggregations(
                organism=[
                    _organism_bucket("9606", 100, "Homo sapiens"),
                    _organism_bucket("562", 50, "Escherichia coli"),
                    _organism_bucket("10090", 25, "Mus musculus"),
                ]
            )
        )
        assert facets.organism is not None
        assert [b.value for b in facets.organism] == ["9606", "562", "10090"]
        assert [b.count for b in facets.organism] == [100, 50, 25]

    def test_name_sub_agg_picks_first_bucket_as_label(self) -> None:
        """sub-agg ``size=1`` yields the doc_count-most-frequent name
        because ES already orders sub-buckets by doc_count desc; the
        first sub-bucket is the representative."""
        bucket = {
            "key": "9606",
            "doc_count": 100,
            "name": {
                "buckets": [
                    # ES ``terms`` sub-agg returns doc_count desc; we only
                    # ask for ``size=1`` in production (es/query.py), but
                    # the parser should still pick the first bucket if
                    # multiple slip through (e.g. test fixture, future
                    # change to size=N).
                    {"key": "Homo sapiens", "doc_count": 90},
                    {"key": "Homo Sapiens", "doc_count": 10},
                ],
            },
        }
        facets = parse_facets(_aggregations(organism=[bucket]))
        assert facets.organism is not None
        assert facets.organism[0].label == "Homo sapiens"

    def test_empty_name_sub_agg_falls_back_to_tax_id_with_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When the ``name`` sub-aggregation produces no buckets (data
        quality issue: organism.identifier set but organism.name missing
        on every doc), label falls back to the TaxID itself so the
        bucket still satisfies ``OrganismFacetBucket`` (label required),
        and a warning is emitted."""
        with caplog.at_level(logging.WARNING, logger="ddbj_search_api.utils"):
            facets = parse_facets(
                _aggregations(organism=[_organism_bucket("99999", 3, name=None)])
            )
        assert facets.organism is not None
        assert facets.organism[0].value == "99999"
        # Fallback: label == value when sub-agg is empty.
        assert facets.organism[0].label == "99999"
        # Warning surface for downstream investigation.
        assert any(
            "no organism.name sub-bucket" in record.getMessage() for record in caplog.records
        )

    def test_missing_name_key_treated_as_empty_sub_agg(self) -> None:
        """Defensive: if ES ever omits the ``name`` sub-agg key entirely
        for a bucket (shouldn't happen given _FACET_AGG_SPECS, but the
        parser must not raise KeyError)."""
        bucket = {"key": "9606", "doc_count": 1}  # no ``name`` sub-agg at all
        facets = parse_facets(_aggregations(organism=[bucket]))
        assert facets.organism is not None
        assert facets.organism[0].value == "9606"
        # Falls back to value, like the empty-sub-agg case.
        assert facets.organism[0].label == "9606"
