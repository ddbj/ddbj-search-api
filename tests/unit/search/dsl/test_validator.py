"""Tests for ddbj_search_api.search.dsl.validator (Stage 2).

SSOT:
- search.md §演算子とフィールドの組み合わせ (L225-236)
- search-backends.md §値のバリデーション (L400-414)
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.schemas.db_portal import DbPortalDb
from ddbj_search_api.search.dsl import DslError, ErrorType, parse
from ddbj_search_api.search.dsl.allowlist import ALL_ALLOWED_FIELDS
from ddbj_search_api.search.dsl.validator import DEFAULT_MAX_NODES, validate


class TestAllowedFields:
    @pytest.mark.parametrize(
        "field",
        [
            "identifier",
            "title",
            "description",
            "organism",
            "date_published",
            "date_modified",
            "date_created",
            "date",
        ],
    )
    def test_tier1_fields_accepted_in_cross_mode(self, field: str) -> None:
        if field == "identifier":
            dsl = f"{field}:PRJDB1"
        elif field == "organism":
            dsl = f"{field}:human"
        elif field.startswith("date"):
            dsl = f"{field}:2024-01-01"
        else:
            dsl = f"{field}:cancer"
        validate(parse(dsl), mode="cross")

    def test_unknown_field_rejected(self) -> None:
        ast = parse("foo:bar")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.unknown_field
        assert exc_info.value.column == 1
        # detail に候補一覧が含まれる
        assert "identifier" in exc_info.value.detail

    def test_unknown_field_position_reported_mid_expr(self) -> None:
        ast = parse("title:a AND foo:bar")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.unknown_field
        # "foo:bar" は column 13
        assert exc_info.value.column == 13


class TestValueKindOperatorCompat:
    @pytest.mark.parametrize(
        "dsl",
        [
            "identifier:[a TO b]",  # identifier は range 不可
            "title:2024-01-01",  # text は date 不可
            "date:cancer*",  # date alias x wildcard 不可
            "date_published:2024*",  # date 型 x wildcard 不可 (YYYYMMDD format への暗黙変換は無い)
            "date_published:cancer",  # date は word 不可
            "organism:cancer*",  # organism は wildcard 不可
            "organism:2024-01-01",  # organism は date 不可
            "description:[a TO b]",  # text は range 不可
        ],
    )
    def test_invalid_operator_rejected(self, dsl: str) -> None:
        ast = parse(dsl)
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.invalid_operator_for_field

    @pytest.mark.parametrize(
        "dsl",
        [
            "identifier:PRJDB1",
            "identifier:PRJ*",
            'identifier:"PRJDB1"',
            "title:cancer",
            'title:"cancer treatment"',
            "title:canc*",
            "organism:human",
            'organism:"Homo sapiens"',
            "date_published:2024-01-01",
            "date_published:[2020-01-01 TO 2024-12-31]",
            "date:[2020-01-01 TO 2024-12-31]",
            "date:2024-01-01",
        ],
    )
    def test_valid_combinations_accepted(self, dsl: str) -> None:
        ast = parse(dsl)
        validate(ast, mode="cross")


class TestDateFormat:
    def test_non_leap_year_feb_29_rejected(self) -> None:
        ast = parse("date_published:2023-02-29")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.invalid_date_format

    def test_leap_year_feb_29_accepted(self) -> None:
        ast = parse("date_published:2024-02-29")
        validate(ast, mode="cross")

    def test_invalid_month_rejected(self) -> None:
        ast = parse("date_published:2024-99-99")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.invalid_date_format

    def test_range_invalid_from_rejected(self) -> None:
        ast = parse("date_published:[2023-02-29 TO 2024-12-31]")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.invalid_date_format

    def test_range_invalid_to_rejected(self) -> None:
        ast = parse("date_published:[2024-01-01 TO 2024-99-99]")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.invalid_date_format

    def test_range_from_greater_than_to_accepted(self) -> None:
        # SSOT 未明記、Lucene 挙動に合わせ 0 件扱いとして通す
        ast = parse("date_published:[2024-12-31 TO 2020-01-01]")
        validate(ast, mode="cross")


class TestMissingValue:
    def test_empty_phrase_raises_missing_value(self) -> None:
        ast = parse('title:""')
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.missing_value


class TestNestDepth:
    def test_depth_5_accepted(self) -> None:
        # 5 iteration で 5 BoolOp ネスト → max_depth=5 の境界で accept
        dsl = "title:a"
        for i in range(5):
            dsl = f"({dsl} AND title:v{i})"
        validate(parse(dsl), mode="cross")

    def test_depth_6_rejected(self) -> None:
        # 6 iteration で 6 BoolOp ネスト → max_depth=5 を超過で reject
        dsl = "title:a"
        for i in range(6):
            dsl = f"({dsl} AND title:v{i})"
        ast = parse(dsl)
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.nest_depth_exceeded

    def test_custom_max_depth(self) -> None:
        ast = parse("title:a AND title:b")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross", max_depth=0)
        assert exc_info.value.type == ErrorType.nest_depth_exceeded


class TestNestNodes:
    """AST ノード総数上限 (`max_nodes`) を超えると `nest_depth_exceeded` を返す。

    深さは OK でも横幅 (`a OR b OR ... OR z`) で爆発するケースをガードする。
    既存の ``nest-depth-exceeded`` slug を流用 (validator.py コメントの方針)。
    """

    def test_single_clause_accepted(self) -> None:
        # 単独 leaf = 1 node、max_nodes=1 で境界 OK
        validate(parse("title:cancer"), mode="cross", max_nodes=1)

    def test_total_count_boundary_accepted(self) -> None:
        # AND 二項 = root + 2 leaves = 3 nodes、max_nodes=3 で境界 OK
        validate(parse("title:a AND title:b"), mode="cross", max_nodes=3)

    def test_total_count_exceeded_rejected(self) -> None:
        # AND 二項 = 3 nodes、max_nodes=2 で reject
        ast = parse("title:a AND title:b")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross", max_nodes=2)
        assert exc_info.value.type == ErrorType.nest_depth_exceeded
        assert "total node count 3" in exc_info.value.detail
        assert "exceeds limit 2" in exc_info.value.detail

    def test_wide_or_at_shallow_depth_rejected_by_node_count(self) -> None:
        # 深さ 1 でも横幅で爆発するケース (depth check では止まらない)
        # OR 5 leaves = root + 5 = 6 nodes、max_depth=2 (OK) でも max_nodes=5 で reject
        ast = parse("title:a OR title:b OR title:c OR title:d OR title:e")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross", max_depth=2, max_nodes=5)
        assert exc_info.value.type == ErrorType.nest_depth_exceeded
        assert "total node count" in exc_info.value.detail

    def test_node_count_check_runs_before_depth(self) -> None:
        # 深さも幅も両方超過 → どちらが先に raise しても slug は同じだが、
        # detail で「total node count」と「nest depth」を区別できる必要がある
        ast = parse("title:a OR title:b OR title:c")  # 1 + 3 = 4 nodes
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross", max_depth=10, max_nodes=2)
        # depth は 2 で OK だが nodes が 4 > 2 で reject
        assert "total node count" in exc_info.value.detail

    def test_default_max_nodes_is_512(self) -> None:
        # 引数省略時の default は 512 (DEFAULT_MAX_NODES) を踏襲
        assert DEFAULT_MAX_NODES == 512


class TestMode:
    def test_cross_mode_with_tier1_accepted(self) -> None:
        validate(parse("title:cancer"), mode="cross")

    def test_single_mode_with_tier1_accepted(self) -> None:
        validate(parse("title:cancer"), mode="single", db=DbPortalDb.bioproject)

    def test_single_mode_unknown_field_still_rejected(self) -> None:
        ast = parse("foo:bar")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="single", db=DbPortalDb.bioproject)
        assert exc_info.value.type == ErrorType.unknown_field


class TestBoolCombinations:
    def test_and_with_valid_leaves_accepted(self) -> None:
        validate(parse("title:cancer AND organism:human"), mode="cross")

    def test_or_with_invalid_leaf_rejected(self) -> None:
        ast = parse("title:cancer OR date:cancer*")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.invalid_operator_for_field

    def test_not_with_valid_leaf_accepted(self) -> None:
        validate(parse("NOT title:cancer"), mode="cross")


# === Tier 2/3 validator tests ===


class TestTier2CrossModeAccepted:
    """Tier 2 (submitter / publication) は cross mode で accept される。"""

    def test_submitter_word_accepted(self) -> None:
        validate(parse('submitter:"Tokyo University"'), mode="cross")

    def test_submitter_phrase_accepted(self) -> None:
        validate(parse('submitter:"National Institute of Genetics"'), mode="cross")

    def test_submitter_wildcard_accepted(self) -> None:
        validate(parse("submitter:Tok*"), mode="cross")

    def test_publication_word_accepted(self) -> None:
        validate(parse("publication:12345678"), mode="cross")

    def test_publication_wildcard_accepted(self) -> None:
        validate(parse("publication:123*"), mode="cross")


class TestTier3CrossModeReject:
    """Tier 3 は cross mode で field-not-available-in-cross-db で reject される。"""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            # BioProject
            ("project_type", "BioProject"),
            ("grant_agency", "JSPS"),
            # SRA
            ("library_strategy", "WGS"),
            ("library_source", "GENOMIC"),
            ("library_layout", "SINGLE"),
            ("platform", "ILLUMINA"),
            ("instrument_model", "NovaSeq"),
            # JGA
            ("study_type", "Cohort"),
            # GEA / MetaboBank
            ("experiment_type", "RNA-Seq"),
            ("submission_type", "metabolite"),
            # Trad
            ("division", "BCT"),
            ("molecular_type", "DNA"),
            ("feature_gene_name", "BRCA1"),
            ("reference_journal", "Nature"),
            # Taxonomy
            ("rank", "species"),
            ("lineage", "Eukaryota"),
            ("kingdom", "Animalia"),
            ("phylum", "Chordata"),
            ("class", "Mammalia"),
            ("order", "Primates"),
            ("family", "Hominidae"),
            ("genus", "Homo"),
            ("species", "sapiens"),
            ("common_name", "human"),
        ],
    )
    def test_each_tier3_field_rejected_in_cross_mode(self, field: str, value: str) -> None:
        ast = parse(f"{field}:{value}")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.field_not_available_in_cross_db

    def test_sequence_length_range_rejected_in_cross_mode(self) -> None:
        ast = parse("sequence_length:[100 TO 5000]")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.field_not_available_in_cross_db

    def test_detail_contains_single_db_hint(self) -> None:
        ast = parse("library_strategy:WGS")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert "use db=sra" in exc_info.value.detail

    def test_detail_contains_multiple_db_hints(self) -> None:
        """候補 DB が複数ある field (grant_agency) は全て列挙する。"""
        ast = parse('grant_agency:"National Institutes of Health"')
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert "use db=bioproject or db=jga" in exc_info.value.detail

    def test_detail_contains_field_name(self) -> None:
        ast = parse("platform:ILLUMINA")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert "'platform'" in exc_info.value.detail

    def test_detail_contains_column(self) -> None:
        ast = parse("title:cancer AND platform:ILLUMINA")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        # "title:cancer AND " で 17 文字、"platform" は column 18 開始
        assert exc_info.value.column == 18


class TestTier3SingleModeAccepted:
    """Tier 3 は single mode (db 指定) で accept される。"""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("project_type", "BioProject"),
            ("library_strategy", "WGS"),
            ("study_type", "Cohort"),
            ("division", "BCT"),
            ("rank", "species"),
        ],
    )
    def test_single_mode_accepts_tier3(self, field: str, value: str) -> None:
        validate(parse(f"{field}:{value}"), mode="single", db=DbPortalDb.bioproject)

    def test_sequence_length_range_accepted_in_single_mode(self) -> None:
        validate(parse("sequence_length:[100 TO 5000]"), mode="single", db=DbPortalDb.trad)

    def test_sequence_length_eq_accepted_in_single_mode(self) -> None:
        validate(parse("sequence_length:1000"), mode="single", db=DbPortalDb.trad)


class TestEnumValueKindCompat:
    """enum 型フィールドは word / phrase のみ accept。"""

    @pytest.mark.parametrize(
        "dsl",
        [
            "library_strategy:WGS",
            'library_strategy:"VIRAL RNA"',  # 空白含みは phrase 必須
            "library_source:GENOMIC",
            "platform:ILLUMINA",
            "rank:species",
            "project_type:BioProject",
            "division:BCT",
            "molecular_type:DNA",
        ],
    )
    def test_enum_word_or_phrase_accepted(self, dsl: str) -> None:
        validate(parse(dsl), mode="single", db=DbPortalDb.bioproject)

    @pytest.mark.parametrize(
        "dsl",
        [
            "library_strategy:WGS*",  # wildcard
            "platform:ILLU*",
            "rank:2024-01-01",  # date
            "project_type:[A TO B]",  # range
        ],
    )
    def test_enum_wildcard_range_date_rejected(self, dsl: str) -> None:
        ast = parse(dsl)
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="single", db=DbPortalDb.bioproject)
        assert exc_info.value.type == ErrorType.invalid_operator_for_field


class TestNumberValueKindCompat:
    """number 型フィールド (sequence_length) は word / range のみ accept。"""

    def test_number_eq_accepted(self) -> None:
        validate(parse("sequence_length:5000"), mode="single", db=DbPortalDb.trad)

    def test_number_range_accepted(self) -> None:
        validate(parse("sequence_length:[100 TO 5000]"), mode="single", db=DbPortalDb.trad)

    @pytest.mark.parametrize(
        "dsl",
        [
            'sequence_length:"5000"',  # phrase
            "sequence_length:500*",  # wildcard
            "sequence_length:2024-01-01",  # date
        ],
    )
    def test_number_phrase_wildcard_date_rejected(self, dsl: str) -> None:
        ast = parse(dsl)
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="single", db=DbPortalDb.trad)
        assert exc_info.value.type == ErrorType.invalid_operator_for_field


class TestNumberDigitValidation:
    """number 型の値は digit のみ受け付ける (invalid_operator_for_field を流用)。"""

    def test_non_digit_word_rejected(self) -> None:
        ast = parse("sequence_length:abc")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="single", db=DbPortalDb.trad)
        assert exc_info.value.type == ErrorType.invalid_operator_for_field

    def test_non_digit_range_from_rejected(self) -> None:
        ast = parse("sequence_length:[abc TO 5000]")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="single", db=DbPortalDb.trad)
        assert exc_info.value.type == ErrorType.invalid_operator_for_field

    def test_non_digit_range_to_rejected(self) -> None:
        ast = parse("sequence_length:[100 TO xyz]")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="single", db=DbPortalDb.trad)
        assert exc_info.value.type == ErrorType.invalid_operator_for_field

    def test_digit_range_accepted(self) -> None:
        validate(parse("sequence_length:[100 TO 5000]"), mode="single", db=DbPortalDb.trad)


class TestValidatorPBT:
    @given(
        field=st.sampled_from(["title", "description", "organism"]),
        word=st.text(
            alphabet=st.characters(
                min_codepoint=ord("0"),
                max_codepoint=ord("z"),
                whitelist_categories=("Ll", "Lu", "Nd"),
                whitelist_characters="_",
            ),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=50, deadline=None)
    def test_word_values_for_text_organism_accepted(self, field: str, word: str) -> None:
        validate(parse(f"{field}:{word}"), mode="cross")

    @given(
        unknown=st.text(
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z"), whitelist_characters="_"),
            min_size=3,
            max_size=20,
        ).filter(lambda s: s not in ALL_ALLOWED_FIELDS),
    )
    @settings(max_examples=30, deadline=None)
    def test_random_unknown_field_rejected(self, unknown: str) -> None:
        ast = parse(f"{unknown}:value")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.unknown_field


# === PBT for enum / number value_kind compatibility ===


_ENUM_FIELDS = [
    "project_type",
    "library_strategy",
    "library_source",
    "library_layout",
    "platform",
    "study_type",
    "division",
    "molecular_type",
    "rank",
]
_ENUM_DBS: dict[str, DbPortalDb] = {
    "project_type": DbPortalDb.bioproject,
    "library_strategy": DbPortalDb.sra,
    "library_source": DbPortalDb.sra,
    "library_layout": DbPortalDb.sra,
    "platform": DbPortalDb.sra,
    "study_type": DbPortalDb.jga,
    "division": DbPortalDb.trad,
    "molecular_type": DbPortalDb.trad,
    "rank": DbPortalDb.taxonomy,
}


class TestTier3PBT:
    """hypothesis PBT: enum / number field x value_kind の互換性."""

    @given(
        field=st.sampled_from(_ENUM_FIELDS),
        value=st.text(
            alphabet=st.characters(
                min_codepoint=ord("A"),
                max_codepoint=ord("z"),
                whitelist_categories=("Lu", "Ll", "Nd"),
                whitelist_characters="-_",
            ),
            min_size=1,
            max_size=15,
        ),
    )
    @settings(max_examples=40, deadline=None)
    def test_enum_word_always_accepted(self, field: str, value: str) -> None:
        db = _ENUM_DBS[field]
        validate(parse(f"{field}:{value}"), mode="single", db=db)

    @given(
        field=st.sampled_from(_ENUM_FIELDS),
        suffix=st.text(
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
            min_size=1,
            max_size=10,
        ),
    )
    @settings(max_examples=30, deadline=None)
    def test_enum_wildcard_always_rejected(self, field: str, suffix: str) -> None:
        db = _ENUM_DBS[field]
        ast = parse(f"{field}:{suffix}*")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="single", db=db)
        assert exc_info.value.type == ErrorType.invalid_operator_for_field

    @given(
        value=st.integers(min_value=0, max_value=1_000_000),
    )
    @settings(max_examples=40, deadline=None)
    def test_number_digit_value_accepted(self, value: int) -> None:
        validate(parse(f"sequence_length:{value}"), mode="single", db=DbPortalDb.trad)

    @given(
        low=st.integers(min_value=0, max_value=500_000),
        high=st.integers(min_value=0, max_value=1_000_000),
    )
    @settings(max_examples=40, deadline=None)
    def test_number_range_digit_accepted(self, low: int, high: int) -> None:
        validate(
            parse(f"sequence_length:[{low} TO {high}]"),
            mode="single",
            db=DbPortalDb.trad,
        )

    @given(
        value=st.text(
            alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
            min_size=1,
            max_size=10,
        ),
    )
    @settings(max_examples=30, deadline=None)
    def test_number_non_digit_rejected(self, value: str) -> None:
        ast = parse(f"sequence_length:{value}")
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="single", db=DbPortalDb.trad)
        assert exc_info.value.type == ErrorType.invalid_operator_for_field

    @given(field=st.sampled_from(sorted(set(_ENUM_FIELDS) | {"sequence_length"})))
    @settings(max_examples=20, deadline=None)
    def test_any_tier3_rejected_in_cross_mode(self, field: str) -> None:
        """全 Tier 3 (enum + number) は cross mode で reject される (SRA subtype や DB 関係なく)."""
        dsl = f"{field}:val" if field != "sequence_length" else f"{field}:100"
        ast = parse(dsl)
        with pytest.raises(DslError) as exc_info:
            validate(ast, mode="cross")
        assert exc_info.value.type == ErrorType.field_not_available_in_cross_db
