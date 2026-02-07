"""Hypothesis custom strategies for ddbj-search-api tests."""
from typing import List

from hypothesis import strategies as st

from ddbj_search_api.schemas.common import DbType

# === DbType ===

db_type_values: List[str] = [e.value for e in DbType]
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
