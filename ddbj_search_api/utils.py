from pathlib import Path
from typing import Any

from pydantic import BaseModel


def inside_container() -> bool:
    """Docker または Podman コンテナ内で実行中かどうかを判定する。"""
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def entry_to_dict(
    entry: BaseModel,
    *,
    by_alias: bool = True,
    trim_properties: bool = False,
) -> dict[str, Any]:
    """エントリモデルを辞書に変換する。trim_properties=True 時は properties フィールドを除外する。"""
    exclude: set[str] | None = {"properties"} if trim_properties else None
    return entry.model_dump(by_alias=by_alias, exclude=exclude)
