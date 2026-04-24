"""DSL compiler for Solr edismax q string (Stage 3b).

SSOT: search-backends.md §バックエンド変換 (L520, L542-543, L560-575).

Dialect:
- ``arsa``: ARSA (Solr 4.4.0)。Tier 1 (``PrimaryAccessionNumber`` / ``Definition`` /
  ``AllText`` / ``Organism`` / ``Lineage`` / ``Date``) + Tier 2 ``publication`` →
  ``ReferencePubmedID`` + Trad Tier 3 (``Division`` / ``MolecularType`` / ``SequenceLength`` /
  ``FeatureQualifier`` / ``ReferenceJournal``)。``date_modified`` / ``date_created`` /
  ``date`` alias、submitter (organization は ARSA にない)、ES-only / Taxonomy 系 Tier 3 は degenerate。
- ``txsearch``: TXSearch (Solr 4.4.0)。Tier 1 (``tax_id`` / ``scientific_name`` / ``text``) +
  Taxonomy Tier 3 (``rank`` / ``lineage`` / ``kingdom`` / ... / ``common_name``; 10 field)。
  organism 自体が Taxonomy のため ``organism`` + 日付 + Tier 2 + Trad/ES-only Tier 3 は degenerate。
  ``japanese_name`` は staging TXSearch の schema に不在のため allowlist 外。

degenerate は leaf を ``(-*:*)`` (no-match リテラル) に置換。ツリー構造は維持する。
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from ddbj_search_api.search.dsl.allowlist import FIELD_TYPES, OPERATOR_BY_KIND
from ddbj_search_api.search.dsl.ast import FieldClause, Node, Range
from ddbj_search_api.search.phrase import escape_solr_phrase

SolrDialect: TypeAlias = Literal["arsa", "txsearch"]

# === ARSA (Trad) field map ===

_ARSA_FIELD_MAP: dict[str, tuple[str, ...]] = {
    # === Tier 1 ===
    "identifier": ("PrimaryAccessionNumber",),
    "title": ("Definition",),
    "description": ("AllText",),
    "organism": ("Organism", "Lineage"),
    "date_published": ("Date",),
    # === Tier 2 ===
    "publication": ("ReferencePubmedID",),
    # submitter は ARSA に相当 field なし → _ARSA_UNAVAILABLE で degenerate
    # === Tier 3 Trad only ===
    "division": ("Division",),
    "molecular_type": ("MolecularType",),
    "sequence_length": ("SequenceLength",),
    "feature_gene_name": ("FeatureQualifier",),
    "reference_journal": ("ReferenceJournal",),
}

_ARSA_UNAVAILABLE: frozenset[str] = frozenset(
    {
        # Tier 1 日付 alias
        "date_modified",
        "date_created",
        "date",
        # Tier 2: ARSA に organization 相当 field なし
        "submitter",
        # Tier 3 ES-only: BioProject / SRA / JGA / GEA / MetaboBank 系
        "project_type",
        "grant_agency",
        "library_strategy",
        "library_source",
        "library_layout",
        "platform",
        "instrument_model",
        "study_type",
        "experiment_type",
        "submission_type",
        # Tier 3 Taxonomy-only: TXSearch 系
        "rank",
        "lineage",
        "kingdom",
        "phylum",
        "class",
        "order",
        "family",
        "genus",
        "species",
        "common_name",
    },
)

# === TXSearch (Taxonomy) field map ===

_TXSEARCH_FIELD_MAP: dict[str, tuple[str, ...]] = {
    # === Tier 1 ===
    "identifier": ("tax_id",),
    "title": ("scientific_name",),
    "description": ("text",),
    # === Tier 3 Taxonomy ===
    "rank": ("rank",),
    "lineage": ("lineage",),
    "kingdom": ("kingdom",),
    "phylum": ("phylum",),
    "class": ("class",),
    "order": ("order",),
    "family": ("family",),
    "genus": ("genus",),
    "species": ("species",),
    "common_name": ("common_name",),
    # japanese_name は staging TXSearch の schema luke で field 不在のため allowlist 外
}

_TXSEARCH_UNAVAILABLE: frozenset[str] = frozenset(
    {
        # Tier 1: organism + 日付 (Taxonomy は日付概念なし、organism 自体が Taxonomy)
        "organism",
        "date_published",
        "date_modified",
        "date_created",
        "date",
        # Tier 2: TXSearch に organization / publication 相当 field なし
        "submitter",
        "publication",
        # Tier 3 ES-only
        "project_type",
        "grant_agency",
        "library_strategy",
        "library_source",
        "library_layout",
        "platform",
        "instrument_model",
        "study_type",
        "experiment_type",
        "submission_type",
        # Tier 3 Trad-only
        "division",
        "molecular_type",
        "sequence_length",
        "feature_gene_name",
        "reference_journal",
    },
)

_NO_MATCH_LITERAL = "(-*:*)"


def compile_to_solr(ast: Node, *, dialect: SolrDialect) -> str:
    """Convert a validated AST to an edismax ``q`` string for the given Solr dialect."""
    return _compile_node(ast, dialect=dialect)


def _compile_node(node: Node, *, dialect: SolrDialect) -> str:
    if isinstance(node, FieldClause):
        return _compile_leaf(node, dialect=dialect)
    children_q = [_compile_node(c, dialect=dialect) for c in node.children]
    if node.op == "AND":
        return "(" + " AND ".join(children_q) + ")"
    if node.op == "OR":
        return "(" + " OR ".join(children_q) + ")"
    return f"(NOT {children_q[0]})"


def _compile_leaf(clause: FieldClause, *, dialect: SolrDialect) -> str:
    if dialect == "arsa":
        if clause.field in _ARSA_UNAVAILABLE:
            return _NO_MATCH_LITERAL
        solr_fields = _ARSA_FIELD_MAP.get(clause.field)
    else:
        if clause.field in _TXSEARCH_UNAVAILABLE:
            return _NO_MATCH_LITERAL
        solr_fields = _TXSEARCH_FIELD_MAP.get(clause.field)
    if not solr_fields:
        return _NO_MATCH_LITERAL
    if len(solr_fields) == 1:
        return _basic_leaf(solr_fields[0], clause)
    return "(" + " OR ".join(_basic_leaf(f, clause) for f in solr_fields) + ")"


def _basic_leaf(solr_field: str, clause: FieldClause) -> str:
    field_type = FIELD_TYPES[clause.field]
    op = OPERATOR_BY_KIND[(field_type, clause.value_kind)]
    value = clause.value
    if op == "between" and isinstance(value, Range):
        from_v = _format_date_for_solr(value.from_) if field_type == "date" else value.from_
        to_v = _format_date_for_solr(value.to) if field_type == "date" else value.to
        return f"{solr_field}:[{from_v} TO {to_v}]"
    if not isinstance(value, str):
        raise TypeError(f"expected str value for field {clause.field!r}")
    if clause.value_kind == "date":
        formatted = _format_date_for_solr(value) if field_type == "date" else value
        return f"{solr_field}:{formatted}"
    if clause.value_kind == "wildcard":
        return f"{solr_field}:{value}"
    # word / phrase は両方 quote (Solr edismax metachar 解釈回避)
    escaped = escape_solr_phrase(value)
    return f'{solr_field}:"{escaped}"'


def _format_date_for_solr(iso: str) -> str:
    """YYYY-MM-DD → YYYYMMDD (ARSA ``Date`` field format)."""
    return iso.replace("-", "")
