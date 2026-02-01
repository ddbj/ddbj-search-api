from unittest.mock import patch

from pydantic import BaseModel, Field

from ddbj_search_api.utils import entry_to_dict, inside_container


class TestInsideContainer:
    def test_docker_env_exists(self) -> None:
        with patch("ddbj_search_api.utils.Path.exists", side_effect=[True, False]):
            assert inside_container() is True

    def test_containerenv_exists(self) -> None:
        with patch("ddbj_search_api.utils.Path.exists", side_effect=[False, True]):
            assert inside_container() is True

    def test_both_exist(self) -> None:
        with patch("ddbj_search_api.utils.Path.exists", return_value=True):
            assert inside_container() is True

    def test_neither_exists(self) -> None:
        with patch("ddbj_search_api.utils.Path.exists", return_value=False):
            assert inside_container() is False


class _DummyEntry(BaseModel):
    identifier: str
    title: str
    properties: dict[str, str] = Field(default_factory=dict)


class _DummyAliasEntry(BaseModel):
    identifier: str
    camel_field: str = Field(alias="camelField")


class TestEntryToDict:
    def test_default_includes_properties(self) -> None:
        entry = _DummyEntry(identifier="ID1", title="T", properties={"k": "v"})
        result = entry_to_dict(entry)
        assert "properties" in result
        assert result["properties"] == {"k": "v"}

    def test_trim_properties_true(self) -> None:
        entry = _DummyEntry(identifier="ID1", title="T", properties={"k": "v"})
        result = entry_to_dict(entry, trim_properties=True)
        assert "properties" not in result

    def test_trim_properties_false(self) -> None:
        entry = _DummyEntry(identifier="ID1", title="T", properties={"k": "v"})
        result = entry_to_dict(entry, trim_properties=False)
        assert "properties" in result

    def test_by_alias_default(self) -> None:
        entry = _DummyAliasEntry(camelField="val", identifier="ID1")
        result = entry_to_dict(entry)
        assert "camelField" in result
        assert "camel_field" not in result

    def test_by_alias_false(self) -> None:
        entry = _DummyAliasEntry(camelField="val", identifier="ID1")
        result = entry_to_dict(entry, by_alias=False)
        assert "camel_field" in result
        assert "camelField" not in result
