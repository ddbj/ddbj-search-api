"""ddbj-search-converter の schema contract regression test.

converter 側の schema/field 変更を api 側で早期検知するための test。
主に以下を確認する:

- Publication 再設計 (e 接頭辞撤廃、status 廃止、Reference → reference)
- Grant.agency が list[Organization] 型に統一
- Organization 共通型の role / organizationType Literal
- BioSample.attributes 撤廃、model が list[str] にフラット化
- SRA の Literal 撤廃 (libraryLayout / platform / analysisType / librarySource が free string 系に)
- JGA の新 field 追加
- BioProject / BioSample の isPartOf が lowercase Literal
"""

from __future__ import annotations

from typing import Any

import pytest
from ddbj_search_converter.schema import (
    JGA,
    SRA,
    BioProject,
    BioSample,
    Grant,
    Organization,
    Publication,
)
from pydantic import ValidationError

# === Publication ===


class TestPublicationContract:
    """Publication 再設計: e 接頭辞撤廃 / status 廃止 / reference lowercase."""

    @pytest.mark.parametrize("value", ["pubmed", "doi", "pmc", "other"])
    def test_new_dbtype_values_accepted(self, value: str) -> None:
        pub = Publication(dbType=value)  # type: ignore[arg-type]
        assert pub.dbType == value

    @pytest.mark.parametrize("value", ["ePubmed", "eDOI", "ePMC", "eNotAvailable"])
    def test_legacy_e_prefix_dbtype_rejected(self, value: str) -> None:
        with pytest.raises(ValidationError):
            Publication(dbType=value)  # type: ignore[arg-type]

    def test_status_field_removed(self) -> None:
        assert "status" not in Publication.model_fields

    def test_reference_is_lowercase_field(self) -> None:
        pub = Publication(reference="Nature, 2024")
        assert pub.reference == "Nature, 2024"
        assert "Reference" not in Publication.model_fields


# === Grant ===


class TestGrantContract:
    """Grant.agency が list[Organization]: 旧 Agency 型は廃止."""

    def test_agency_accepts_list_of_organization(self) -> None:
        grant = Grant(
            id="grant-id",
            title="Test Grant",
            agency=[Organization(name="NIH", abbreviation="NIH")],
        )
        assert len(grant.agency) == 1
        assert isinstance(grant.agency[0], Organization)
        assert grant.agency[0].name == "NIH"

    def test_agency_required(self) -> None:
        with pytest.raises(ValidationError):
            Grant(title="Test Grant")  # type: ignore[call-arg]


# === Organization ===


class TestOrganizationContract:
    """Organization 共通型: 全 field optional / role・organizationType は Literal."""

    def test_all_fields_optional(self) -> None:
        org = Organization()
        assert org.name is None
        assert org.abbreviation is None
        assert org.role is None
        assert org.organizationType is None
        assert org.department is None
        assert org.url is None

    @pytest.mark.parametrize("role", ["owner", "participant", "submitter", "broker"])
    def test_role_literal_accepted(self, role: str) -> None:
        org = Organization(role=role)  # type: ignore[arg-type]
        assert org.role == role

    def test_invalid_role_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Organization(role="invalid-role")  # type: ignore[arg-type]

    @pytest.mark.parametrize("org_type", ["institute", "center", "consortium", "lab"])
    def test_organization_type_literal_accepted(self, org_type: str) -> None:
        org = Organization(organizationType=org_type)  # type: ignore[arg-type]
        assert org.organizationType == org_type

    def test_invalid_organization_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Organization(organizationType="invalid-type")  # type: ignore[arg-type]


# === BioSample ===


_BIOSAMPLE_MIN: dict[str, Any] = {
    "identifier": "SAMD00000001",
    "properties": {},
    "distribution": [],
    "derivedFrom": [],
    "organization": [],
    "model": [],
    "isPartOf": "biosample",
    "type": "biosample",
    "name": None,
    "url": "https://example.com/SAMD00000001",
    "organism": None,
    "title": None,
    "description": None,
    "package": None,
    "dbXrefs": [],
    "sameAs": [],
    "status": "public",
    "accessibility": "public-access",
    "dateCreated": None,
    "dateModified": None,
    "datePublished": None,
}


class TestBioSampleContract:
    """BioSample: attributes 撤廃 / model を list[str] にフラット化 / organization 追加."""

    def test_attributes_field_removed(self) -> None:
        assert "attributes" not in BioSample.model_fields

    def test_model_is_list_of_str(self) -> None:
        bs = BioSample(**{**_BIOSAMPLE_MIN, "model": ["Generic.1.0", "MIGS.ba.microbial"]})
        assert bs.model == ["Generic.1.0", "MIGS.ba.microbial"]

    def test_model_rejects_legacy_object_shape(self) -> None:
        with pytest.raises(ValidationError):
            BioSample(**{**_BIOSAMPLE_MIN, "model": [{"name": "Generic.1.0"}]})

    def test_organization_field_added(self) -> None:
        assert "organization" in BioSample.model_fields
        bs = BioSample(**{**_BIOSAMPLE_MIN, "organization": [Organization(name="DDBJ")]})
        assert bs.organization[0].name == "DDBJ"


# === SRA ===


_SRA_MIN: dict[str, Any] = {
    "identifier": "DRR000001",
    "properties": {},
    "distribution": [],
    "organization": [],
    "publication": [],
    "libraryStrategy": [],
    "librarySource": [],
    "librarySelection": [],
    "instrumentModel": [],
    "derivedFrom": [],
    "isPartOf": "sra",
    "type": "sra-run",
    "name": None,
    "url": "https://example.com/DRR000001",
    "organism": None,
    "title": None,
    "description": None,
    "libraryLayout": None,
    "platform": None,
    "analysisType": None,
    "dbXrefs": [],
    "sameAs": [],
    "status": "public",
    "accessibility": "public-access",
    "dateCreated": None,
    "dateModified": None,
    "datePublished": None,
}


class TestSRAContract:
    """SRA: Literal 撤廃で libraryLayout / platform / analysisType / librarySource が free string."""

    @pytest.mark.parametrize(
        "field,value",
        [
            ("libraryLayout", "CUSTOM_LAYOUT_2024"),
            ("platform", "VendorX_NewPlatform"),
            ("analysisType", "VendorSpecificAnalysis"),
        ],
    )
    def test_literal_relaxed_to_free_string(self, field: str, value: str) -> None:
        sra = SRA(**{**_SRA_MIN, field: value})
        assert getattr(sra, field) == value

    def test_library_source_accepts_arbitrary_strings(self) -> None:
        sra = SRA(**{**_SRA_MIN, "librarySource": ["CUSTOM_SOURCE_2024", "ANOTHER"]})
        assert sra.librarySource == ["CUSTOM_SOURCE_2024", "ANOTHER"]

    @pytest.mark.parametrize(
        "field",
        [
            "organization",
            "publication",
            "libraryStrategy",
            "librarySource",
            "librarySelection",
            "libraryLayout",
            "platform",
            "instrumentModel",
            "analysisType",
        ],
    )
    def test_expected_fields_exist(self, field: str) -> None:
        assert field in SRA.model_fields


# === JGA ===


class TestJGAContract:
    """JGA: 新 field (organization/publication/grant/externalLink/studyType/datasetType/vendor) 追加."""

    @pytest.mark.parametrize(
        "field",
        [
            "organization",
            "publication",
            "grant",
            "externalLink",
            "studyType",
            "datasetType",
            "vendor",
        ],
    )
    def test_new_fields_exist(self, field: str) -> None:
        assert field in JGA.model_fields


# === BioProject / BioSample isPartOf lowercase ===


_BIOPROJECT_MIN: dict[str, Any] = {
    "identifier": "PRJDB1",
    "properties": {},
    "distribution": [],
    "projectType": [],
    "relevance": [],
    "organization": [],
    "publication": [],
    "grant": [],
    "externalLink": [],
    "isPartOf": "bioproject",
    "type": "bioproject",
    "objectType": "BioProject",
    "name": None,
    "url": "https://example.com/PRJDB1",
    "organism": None,
    "title": None,
    "description": None,
    "dbXrefs": [],
    "parentBioProjects": [],
    "childBioProjects": [],
    "sameAs": [],
    "status": "public",
    "accessibility": "public-access",
    "dateCreated": None,
    "dateModified": None,
    "datePublished": None,
}


class TestIsPartOfLowercase:
    """isPartOf Literal が lowercase 統一: capitalize 旧値は拒否."""

    def test_bioproject_lowercase_accepted(self) -> None:
        bp = BioProject(**_BIOPROJECT_MIN)
        assert bp.isPartOf == "bioproject"

    def test_bioproject_capitalize_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BioProject(**{**_BIOPROJECT_MIN, "isPartOf": "BioProject"})

    def test_biosample_lowercase_accepted(self) -> None:
        bs = BioSample(**_BIOSAMPLE_MIN)
        assert bs.isPartOf == "biosample"

    def test_biosample_capitalize_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BioSample(**{**_BIOSAMPLE_MIN, "isPartOf": "BioSample"})
