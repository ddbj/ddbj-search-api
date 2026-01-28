from ddbj_search_api.schemas.common import DbType, Pagination, ProblemDetails


class TestDbType:
    def test_bioproject(self) -> None:
        assert DbType.bioproject.value == "bioproject"

    def test_biosample(self) -> None:
        assert DbType.biosample.value == "biosample"

    def test_sra_submission(self) -> None:
        assert DbType.sra_submission.value == "sra-submission"

    def test_jga_study(self) -> None:
        assert DbType.jga_study.value == "jga-study"

    def test_member_count(self) -> None:
        assert len(DbType) == 12


class TestPagination:
    def test_basic_creation(self) -> None:
        p = Pagination(page=1, perPage=10, total=100)
        assert p.page == 1
        assert p.per_page == 10
        assert p.total == 100

    def test_alias_serialization(self) -> None:
        p = Pagination(page=1, perPage=10, total=100)
        data = p.model_dump(by_alias=True)
        assert "perPage" in data
        assert data["perPage"] == 10


class TestProblemDetails:
    def test_basic_creation(self) -> None:
        problem = ProblemDetails(
            title="Not Found",
            status=404,
        )  # type: ignore[call-arg]
        assert problem.type == "about:blank"
        assert problem.title == "Not Found"
        assert problem.status == 404
        assert problem.detail is None
        assert problem.instance is None

    def test_with_all_fields(self) -> None:
        problem = ProblemDetails(
            type="about:blank",
            title="Bad Request",
            status=400,
            detail="Invalid parameter",
            instance="/entries/bioproject/INVALID",
        )
        assert problem.title == "Bad Request"
        assert problem.status == 400
        assert problem.detail == "Invalid parameter"
        assert problem.instance == "/entries/bioproject/INVALID"
