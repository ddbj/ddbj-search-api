from pathlib import Path


def inside_container() -> bool:
    """Docker または Podman コンテナ内で実行中かどうかを判定する。"""
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
