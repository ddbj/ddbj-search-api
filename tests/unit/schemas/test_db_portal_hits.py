"""Tests for DbPortalHit discriminated union variants.

8 variant (BioProject / BioSample / SRA / JGA / GEA / MetaboBank / Ddbj / Taxonomy)
の各 `type` discriminator 値に対する validate 経路、subtype 固有 field、
`extra="ignore"` 挙動、alias 往復を検証する。SSOT は search-backends.md §L64-69
および converter 側 `schema.py`。
"""

from __future__ import annotations

from typing import Any

import pydantic
import pytest
from ddbj_search_converter.schema import Organism, Organization, Publication

from ddbj_search_api.schemas.db_portal import (
    DbPortalHitBase,
    DbPortalHitBioProject,
    DbPortalHitBioSample,
    DbPortalHitDdbj,
    DbPortalHitGea,
    DbPortalHitJga,
    DbPortalHitMetabobank,
    DbPortalHitSra,
    DbPortalHitTaxonomy,
    _DbPortalHitAdapter,
)


def _validate(payload: dict[str, Any]) -> DbPortalHitBase:
    return _DbPortalHitAdapter.validate_python(payload)  # type: ignore[no-any-return]


class TestDiscriminatorDispatch:
    """`type` discriminator の値に応じて正しい variant に dispatch される。"""

    @pytest.mark.parametrize(
        ("type_value", "expected_class"),
        [
            ("bioproject", DbPortalHitBioProject),
            ("biosample", DbPortalHitBioSample),
            ("sra-submission", DbPortalHitSra),
            ("sra-study", DbPortalHitSra),
            ("sra-experiment", DbPortalHitSra),
            ("sra-run", DbPortalHitSra),
            ("sra-sample", DbPortalHitSra),
            ("sra-analysis", DbPortalHitSra),
            ("jga-study", DbPortalHitJga),
            ("jga-dataset", DbPortalHitJga),
            ("jga-dac", DbPortalHitJga),
            ("jga-policy", DbPortalHitJga),
            ("gea", DbPortalHitGea),
            ("metabobank", DbPortalHitMetabobank),
            ("ddbj", DbPortalHitDdbj),
            ("taxonomy", DbPortalHitTaxonomy),
        ],
    )
    def test_type_dispatches_to_correct_variant(
        self,
        type_value: str,
        expected_class: type[DbPortalHitBase],
    ) -> None:
        h = _validate({"identifier": "X", "type": type_value})
        assert isinstance(h, expected_class)
        assert isinstance(h, DbPortalHitBase)


class TestBioProjectVariant:
    def test_with_organization_publication_grant(self) -> None:
        h = _validate(
            {
                "identifier": "PRJDB1",
                "type": "bioproject",
                "title": "Test BioProject",
                "objectType": "UmbrellaBioProject",
                "organization": [
                    {"name": "DDBJ", "role": "submitter"},
                ],
                "publication": [
                    {"id": "12345678", "dbType": "pubmed"},
                ],
                "grant": [
                    {"id": "G1", "title": "grant title", "agency": [{"name": "JSPS"}]},
                ],
                "status": "public",
                "accessibility": "public-access",
            },
        )
        assert isinstance(h, DbPortalHitBioProject)
        # object_type は Umbrella 区分 (Literal["BioProject", "UmbrellaBioProject"]).
        # alias=objectType で ES から入る.
        assert h.object_type == "UmbrellaBioProject"
        assert h.organization is not None
        assert h.organization[0].name == "DDBJ"
        assert h.organization[0].role == "submitter"
        assert h.publication is not None
        assert h.publication[0].id_ == "12345678"
        assert h.publication[0].dbType == "pubmed"
        assert h.grant is not None
        assert h.grant[0].agency[0].name == "JSPS"

    def test_object_type_literal_rejects_invalid_value(self) -> None:
        # objectType は Umbrella 区分の Literal なので、INSDC project type vocab を
        # 入れると 422 (allowlist 外). ES `projectType` (INSDC list) は別 field.
        with pytest.raises(pydantic.ValidationError):
            _validate(
                {
                    "identifier": "PRJDB1",
                    "type": "bioproject",
                    "objectType": "Genome sequencing",
                },
            )

    def test_project_type_accepts_insdc_list_and_distinct_from_object_type(self) -> None:
        # INSDC project_type (genome / metagenome / 等の list[str]) と Umbrella 区分の
        # object_type が同時に乗り、互いに別 field として読まれることを確認.
        # converter SSOT (schema.py: projectType: list[str], objectType: Literal[2]) と整合.
        h = _validate(
            {
                "identifier": "PRJDB1",
                "type": "bioproject",
                "objectType": "BioProject",
                "projectType": ["genome", "metagenome"],
            },
        )
        assert isinstance(h, DbPortalHitBioProject)
        assert h.object_type == "BioProject"
        assert h.project_type == ["genome", "metagenome"]

    def test_project_type_defaults_to_none_when_absent(self) -> None:
        # ES からの _source に projectType が無いドキュメントも許容 (Hit 側 defensive).
        h = _validate({"identifier": "PRJDB1", "type": "bioproject"})
        assert isinstance(h, DbPortalHitBioProject)
        assert h.project_type is None
        assert h.object_type is None

    def test_project_type_rejects_non_list_str_value(self) -> None:
        # projectType は list[str] のため、ES converter からの型ドリフト
        # ({"projectType": "genome"} のような str) を 422 で検出する.
        with pytest.raises(pydantic.ValidationError):
            _validate(
                {
                    "identifier": "PRJDB1",
                    "type": "bioproject",
                    "projectType": "genome",
                },
            )

    def test_project_type_rejects_list_of_non_str_values(self) -> None:
        # 型ドリフト検出: list[int] は 422.
        with pytest.raises(pydantic.ValidationError):
            _validate(
                {
                    "identifier": "PRJDB1",
                    "type": "bioproject",
                    "projectType": [1, 2],
                },
            )

    def test_with_relevance(self) -> None:
        h = _validate(
            {
                "identifier": "PRJDB2",
                "type": "bioproject",
                "relevance": ["Medical", "ModelOrganism"],
            },
        )
        assert isinstance(h, DbPortalHitBioProject)
        assert h.relevance == ["Medical", "ModelOrganism"]

    def test_relevance_accepts_arbitrary_strings(self) -> None:
        # allowlist は INSDC controlled values を docs で示しているが、Pydantic 側は
        # list[str] で開放 (converter ingestion に未知値が紛れた場合に silently drop しない)。
        h = _validate(
            {
                "identifier": "PRJDB3",
                "type": "bioproject",
                "relevance": ["FreeFormValue"],
            },
        )
        assert isinstance(h, DbPortalHitBioProject)
        assert h.relevance == ["FreeFormValue"]


class TestBioSampleVariant:
    def test_with_package_and_model(self) -> None:
        h = _validate(
            {
                "identifier": "SAMD00000001",
                "type": "biosample",
                "package": {"name": "MIGS.ba", "displayName": "MIGS Bacteria"},
                "model": ["model-a", "model-b"],
                "status": "public",
            },
        )
        assert isinstance(h, DbPortalHitBioSample)
        assert h.package is not None
        assert h.package.name == "MIGS.ba"
        assert h.package.displayName == "MIGS Bacteria"
        assert h.model == ["model-a", "model-b"]

    def test_with_top_level_attributes_camelcase(self) -> None:
        """converter 0.3.0 で top-level 化された host/strain/isolate/geoLocName/collectionDate."""
        h = _validate(
            {
                "identifier": "SAMD00000002",
                "type": "biosample",
                "host": "Homo sapiens",
                "strain": "K12",
                "isolate": "patient-1",
                "geoLocName": "Japan: Tokyo",
                "collectionDate": "2020-04",
            },
        )
        assert isinstance(h, DbPortalHitBioSample)
        assert h.host == "Homo sapiens"
        assert h.strain == "K12"
        assert h.isolate == "patient-1"
        assert h.geo_loc_name == "Japan: Tokyo"
        assert h.collection_date == "2020-04"

    def test_top_level_attributes_default_to_none_when_absent(self) -> None:
        h = _validate({"identifier": "SAMD00000003", "type": "biosample"})
        assert isinstance(h, DbPortalHitBioSample)
        assert h.host is None
        assert h.strain is None
        assert h.isolate is None
        assert h.geo_loc_name is None
        assert h.collection_date is None


class TestSraVariant:
    def test_sra_experiment_with_library_fields(self) -> None:
        h = _validate(
            {
                "identifier": "DRX000001",
                "type": "sra-experiment",
                "libraryStrategy": ["WGS"],
                "librarySource": ["GENOMIC"],
                "libraryLayout": "PAIRED",
                "platform": "ILLUMINA",
                "instrumentModel": ["NovaSeq 6000"],
            },
        )
        assert isinstance(h, DbPortalHitSra)
        assert h.type == "sra-experiment"
        assert h.library_strategy == ["WGS"]
        assert h.library_layout == "PAIRED"
        assert h.platform == "ILLUMINA"

    def test_sra_study_without_library_fields(self) -> None:
        # sra-study には library_* 相当の fields が converter mapping に無い (plan §1.2)
        h = _validate({"identifier": "DRP000001", "type": "sra-study"})
        assert isinstance(h, DbPortalHitSra)
        assert h.type == "sra-study"
        assert h.library_strategy is None
        assert h.library_layout is None
        assert h.platform is None

    def test_sra_analysis_with_analysis_type(self) -> None:
        h = _validate(
            {"identifier": "DRZ000001", "type": "sra-analysis", "analysisType": "REFERENCE_ALIGNMENT"},
        )
        assert isinstance(h, DbPortalHitSra)
        assert h.analysis_type == "REFERENCE_ALIGNMENT"

    def test_sra_experiment_with_library_name_and_protocol(self) -> None:
        """library_name / library_construction_protocol は sra-experiment のみ populate される."""
        h = _validate(
            {
                "identifier": "DRX000002",
                "type": "sra-experiment",
                "libraryName": "lib-001",
                "libraryConstructionProtocol": "PCR-free",
            },
        )
        assert isinstance(h, DbPortalHitSra)
        assert h.library_name == "lib-001"
        assert h.library_construction_protocol == "PCR-free"

    def test_sra_sample_with_geo_loc_collection_date(self) -> None:
        """geo_loc_name / collection_date は sra-sample のみ populate (BioSample と共通 schema)."""
        h = _validate(
            {
                "identifier": "DRS000001",
                "type": "sra-sample",
                "geoLocName": "Japan: Tokyo",
                "collectionDate": "2020-04",
            },
        )
        assert isinstance(h, DbPortalHitSra)
        assert h.geo_loc_name == "Japan: Tokyo"
        assert h.collection_date == "2020-04"

    def test_sra_other_subtypes_leave_subtype_specific_fields_none(self) -> None:
        """sra-submission / sra-study / sra-run は subtype 固有 field を持たず null."""
        for subtype in ("sra-submission", "sra-study", "sra-run"):
            h = _validate({"identifier": "X", "type": subtype})
            assert isinstance(h, DbPortalHitSra)
            assert h.library_name is None, f"{subtype} should have library_name=None"
            assert h.library_construction_protocol is None
            assert h.geo_loc_name is None
            assert h.collection_date is None
            assert h.analysis_type is None


class TestJgaVariant:
    def test_jga_study_with_study_type(self) -> None:
        h = _validate(
            {
                "identifier": "JGAS000001",
                "type": "jga-study",
                "studyType": ["Case-Control"],
                "vendor": ["vendor-a"],
            },
        )
        assert isinstance(h, DbPortalHitJga)
        assert h.type == "jga-study"
        assert h.study_type == ["Case-Control"]

    def test_jga_dataset_with_dataset_type(self) -> None:
        h = _validate(
            {
                "identifier": "JGAD000001",
                "type": "jga-dataset",
                "datasetType": ["Whole genome sequencing"],
            },
        )
        assert isinstance(h, DbPortalHitJga)
        assert h.type == "jga-dataset"
        assert h.dataset_type == ["Whole genome sequencing"]


class TestGeaVariant:
    def test_with_experiment_type(self) -> None:
        h = _validate(
            {
                "identifier": "E-GEAD-1005",
                "type": "gea",
                "experimentType": ["RNA-Seq", "ChIP-Seq"],
                "accessibility": "public-access",
            },
        )
        assert isinstance(h, DbPortalHitGea)
        assert h.experiment_type == ["RNA-Seq", "ChIP-Seq"]


class TestMetabobankVariant:
    def test_with_all_types(self) -> None:
        h = _validate(
            {
                "identifier": "MTBKS102",
                "type": "metabobank",
                "studyType": ["Lipidomics"],
                "experimentType": ["NMR"],
                "submissionType": ["Metabolite"],
            },
        )
        assert isinstance(h, DbPortalHitMetabobank)
        assert h.study_type == ["Lipidomics"]
        assert h.experiment_type == ["NMR"]
        assert h.submission_type == ["Metabolite"]


class TestDdbjVariant:
    def test_with_division_and_sequence_length(self) -> None:
        h = _validate(
            {
                "identifier": "AY967397",
                "type": "ddbj",
                "division": "SYN",
                "molecularType": "DNA",
                "sequenceLength": 5000,
            },
        )
        assert isinstance(h, DbPortalHitDdbj)
        assert h.division == "SYN"
        assert h.molecular_type == "DNA"
        assert h.sequence_length == 5000


class TestTaxonomyVariant:
    def test_with_rank_and_common_name(self) -> None:
        h = _validate(
            {
                "identifier": "9606",
                "type": "taxonomy",
                "rank": "species",
                "commonName": "human",
                "lineage": ["Homo sapiens", "Homo", "Hominidae"],
            },
        )
        assert isinstance(h, DbPortalHitTaxonomy)
        assert h.rank == "species"
        assert h.common_name == "human"
        assert h.lineage == ["Homo sapiens", "Homo", "Hominidae"]

    def test_lineage_as_string_accepted(self) -> None:
        """TXSearch ドキュメントで lineage が scalar で来るケース。"""
        h = _validate(
            {"identifier": "9606", "type": "taxonomy", "lineage": "single-lineage-string"},
        )
        assert isinstance(h, DbPortalHitTaxonomy)
        assert h.lineage == "single-lineage-string"


class TestAliasRoundTrip:
    def test_dump_by_alias_uses_camelcase(self) -> None:
        h = _validate(
            {
                "identifier": "PRJDB1",
                "type": "bioproject",
                "datePublished": "2024-01-15",
                "dateModified": "2024-06-01",
                "dateCreated": "2024-01-01",
                "organization": [{"name": "DDBJ", "organizationType": "institute"}],
                "publication": [{"id": "12345", "dbType": "pubmed"}],
                "grant": [{"id": "G1", "title": None, "agency": []}],
                "externalLink": [{"url": "https://example.com/", "label": "Home"}],
            },
        )
        dumped = h.model_dump(by_alias=True, exclude_none=True)
        assert dumped["type"] == "bioproject"
        assert dumped["datePublished"] == "2024-01-15"
        assert dumped["dateModified"] == "2024-06-01"
        assert dumped["dateCreated"] == "2024-01-01"
        assert dumped["organization"][0]["organizationType"] == "institute"
        assert dumped["publication"][0]["id"] == "12345"
        assert dumped["publication"][0]["dbType"] == "pubmed"
        assert dumped["grant"][0]["id"] == "G1"
        assert dumped["externalLink"][0]["url"] == "https://example.com/"

    def test_dump_without_alias_uses_snake_case(self) -> None:
        h = _validate(
            {"identifier": "X", "type": "bioproject", "datePublished": "2024-01-15"},
        )
        dumped = h.model_dump(by_alias=False, exclude_none=True)
        assert dumped["date_published"] == "2024-01-15"
        # type は alias 無しで定義されているので by_alias=False でも "type"
        assert dumped["type"] == "bioproject"


class TestExtraIgnore:
    def test_unknown_top_level_field_dropped(self) -> None:
        h = _validate(
            {
                "identifier": "PRJDB1",
                "type": "bioproject",
                "some_future_field": "value",
                "projectId": "extra_camel_case_field",
            },
        )
        dumped = h.model_dump(by_alias=True)
        assert "some_future_field" not in dumped
        assert "projectId" not in dumped

    def test_unknown_nested_field_dropped(self) -> None:
        h = _validate(
            {
                "identifier": "PRJDB1",
                "type": "bioproject",
                "organization": [{"name": "DDBJ", "futureOrgField": "drop-me"}],
            },
        )
        assert isinstance(h, DbPortalHitBioProject)
        assert h.organization is not None
        dumped = h.organization[0].model_dump(by_alias=True)
        assert "futureOrgField" not in dumped


class TestHelperDTOs:
    def test_organism(self) -> None:
        o = Organism(name="Homo sapiens", identifier="9606")
        assert o.name == "Homo sapiens"
        assert o.identifier == "9606"

    def test_organization_literal_role(self) -> None:
        with pytest.raises(pydantic.ValidationError):
            Organization(role="unknown-role")  # type: ignore[arg-type]

    def test_publication_alias_id(self) -> None:
        p = Publication.model_validate({"id": "12345", "dbType": "pubmed"})
        assert p.id_ == "12345"
        assert p.dbType == "pubmed"
        dumped = p.model_dump(by_alias=True, exclude_none=True)
        assert dumped["id"] == "12345"
        assert dumped["dbType"] == "pubmed"
