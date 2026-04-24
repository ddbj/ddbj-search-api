"""Advanced Search DSL (AP3).

3-stage pipeline:
1. ``parse(dsl)`` → ``ast.Node`` (Stage 1, implemented).
2. ``validate(ast, mode, db)`` → raise ``DslError`` on violation (Stage 2, M2).
3. ``compile_to_es(ast)`` / ``compile_to_solr(ast, dialect)`` → backend query (Stage 3, M3/M4).

Error model: ``DslError(type, detail, column, length)``. ``type_uri(error_type)`` maps
an ``ErrorType`` enum to the full ``https://ddbj.nig.ac.jp/problems/<slug>`` URI.
"""

from ddbj_search_api.search.dsl.compiler_es import compile_to_es
from ddbj_search_api.search.dsl.compiler_solr import SolrDialect, compile_to_solr
from ddbj_search_api.search.dsl.errors import TYPE_URI_PREFIX, DslError, ErrorType, type_uri
from ddbj_search_api.search.dsl.parser import DEFAULT_MAX_LENGTH, parse
from ddbj_search_api.search.dsl.serde import ast_to_json
from ddbj_search_api.search.dsl.validator import DEFAULT_MAX_DEPTH, ValidationMode, validate

__all__ = [
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_LENGTH",
    "TYPE_URI_PREFIX",
    "DslError",
    "ErrorType",
    "SolrDialect",
    "ValidationMode",
    "ast_to_json",
    "compile_to_es",
    "compile_to_solr",
    "parse",
    "type_uri",
    "validate",
]
