from pathlib import Path


def inside_docker() -> bool:
    return Path("/.dockerenv").exists()
