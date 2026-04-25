"""converter スキーマで必須化された配列フィールドの一覧。

ddbj-search-converter の `docs/data-architecture.md § 配列フィールドの契約` と同期する。
api 側 unit / integration test から重複定義を避けるため共通モジュール化している。
"""

from __future__ import annotations

REQUIRED_LIST_FIELDS_BIOPROJECT: list[str] = [
    "distribution",
    "projectType",
    "relevance",
    "organization",
    "publication",
    "grant",
    "externalLink",
    "dbXrefs",
    "parentBioProjects",
    "childBioProjects",
    "sameAs",
]

REQUIRED_LIST_FIELDS_BIOSAMPLE: list[str] = [
    "distribution",
    "derivedFrom",
    "organization",
    "model",
    "dbXrefs",
    "sameAs",
]

REQUIRED_LIST_FIELDS_SRA: list[str] = [
    "distribution",
    "organization",
    "publication",
    "libraryStrategy",
    "librarySource",
    "librarySelection",
    "instrumentModel",
    "derivedFrom",
    "dbXrefs",
    "sameAs",
]

REQUIRED_LIST_FIELDS_JGA: list[str] = [
    "distribution",
    "organization",
    "publication",
    "grant",
    "externalLink",
    "studyType",
    "datasetType",
    "vendor",
    "dbXrefs",
    "sameAs",
]

REQUIRED_LIST_FIELDS_GEA: list[str] = [
    "distribution",
    "organization",
    "publication",
    "experimentType",
    "dbXrefs",
    "sameAs",
]

REQUIRED_LIST_FIELDS_METABOBANK: list[str] = [
    "distribution",
    "organization",
    "publication",
    "studyType",
    "experimentType",
    "submissionType",
    "dbXrefs",
    "sameAs",
]
