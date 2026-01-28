from unittest.mock import patch

from ddbj_search_api.utils import inside_container


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
