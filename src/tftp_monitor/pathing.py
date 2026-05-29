from pathlib import Path, PurePosixPath


def build_local_cache_path(cache_root: Path, relative_path: str) -> Path:
    return cache_root.joinpath(*PurePosixPath(relative_path).parts)


def build_destination_path(destination_root: str, relative_path: str) -> str:
    return str(PurePosixPath(destination_root).joinpath(*PurePosixPath(relative_path).parts))
