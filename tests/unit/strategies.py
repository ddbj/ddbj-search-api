"""Hypothesis custom strategies for ddbj-search-api tests."""

from __future__ import annotations

from hypothesis import strategies as st

from ddbj_search_api.schemas.common import DbType
from ddbj_search_api.schemas.dblink import AccessionType

# ``ddbj_search_api.search.phrase`` is the SSOT for the auto-phrase trigger
# sets; re-exported below under the legacy ``*_TRIGGERS`` aliases so existing
# callers keep working without drifting from the production constants.
from ddbj_search_api.search.phrase import (
    ES_AUTO_PHRASE_CHARS as ES_AUTO_PHRASE_TRIGGERS,
)
from ddbj_search_api.search.phrase import (
    SOLR_AUTO_PHRASE_CHARS as SOLR_AUTO_PHRASE_TRIGGERS,
)

# === DbType ===

db_type_values: list[str] = [e.value for e in DbType]
valid_db_types = st.sampled_from(db_type_values)

# === Pagination ===

valid_page = st.integers(min_value=1, max_value=10000)
invalid_page = st.integers(max_value=0)
valid_per_page = st.integers(min_value=1, max_value=100)
invalid_per_page_low = st.integers(max_value=0)
invalid_per_page_high = st.integers(min_value=101)

# === dbXrefsLimit ===

valid_db_xrefs_limit = st.integers(min_value=0, max_value=1000)
invalid_db_xrefs_limit_low = st.integers(max_value=-1)
invalid_db_xrefs_limit_high = st.integers(min_value=1001)

# === Bulk API ids ===

valid_id = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=30,
)
short_id = st.from_regex(r"[A-Z]{2,6}[0-9]{1,6}", fullmatch=True)
valid_bulk_ids = st.lists(valid_id, min_size=1, max_size=100)
oversized_bulk_ids = st.lists(short_id, min_size=1001, max_size=1050)

# === FacetBucket ===

valid_facet_count = st.integers(min_value=0)
valid_facet_value = st.text(min_size=1, max_size=100)

# === Pagination response ===

valid_total = st.integers(min_value=0)

# === AccessionType (dblink) ===

accession_type_values: list[str] = [e.value for e in AccessionType]
valid_accession_types = st.sampled_from(accession_type_values)
valid_accession_id = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=30,
)

# === BioProject accession (umbrella tree) ===

bioproject_accession = st.from_regex(r"PRJ(DB|NA|EB)[0-9]{1,7}", fullmatch=True)

# === Auto-phrase triggers (re-exported above from the production module) ===

__all__ = [
    "ES_AUTO_PHRASE_TRIGGERS",
    "SOLR_AUTO_PHRASE_TRIGGERS",
    "accession_type_values",
    "alphanumeric_no_trigger",
    "bioproject_accession",
    "db_type_values",
    "invalid_db_xrefs_limit_high",
    "invalid_db_xrefs_limit_low",
    "invalid_page",
    "invalid_per_page_high",
    "invalid_per_page_low",
    "oversized_bulk_ids",
    "short_id",
    "text_with_trigger",
    "valid_accession_id",
    "valid_accession_types",
    "valid_bulk_ids",
    "valid_db_types",
    "valid_db_xrefs_limit",
    "valid_facet_count",
    "valid_facet_value",
    "valid_id",
    "valid_page",
    "valid_per_page",
    "valid_total",
]


def alphanumeric_no_trigger(trigger_chars: frozenset[str]) -> st.SearchStrategy[str]:
    """Alphanumeric text excluding trigger char, comma, quote, whitespace."""
    return st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N"),
            blacklist_characters='",' + "".join(sorted(trigger_chars)) + " \t\r\n",
        ),
        min_size=1,
        max_size=30,
    )


def text_with_trigger(trigger_chars: frozenset[str]) -> st.SearchStrategy[str]:
    """Text guaranteed to contain at least one trigger char (sandwiched)."""
    inner = alphanumeric_no_trigger(trigger_chars)
    return st.builds(
        lambda prefix, trigger, suffix: prefix + trigger + suffix,
        inner,
        st.sampled_from(sorted(trigger_chars)),
        inner,
    )
