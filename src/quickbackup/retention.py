"""Retention: keep only the newest N archives in the destination."""

from __future__ import annotations

from pathlib import Path

from .manifest import manifest_path_for

ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tar.xz")


def find_archives(destination: Path) -> list[Path]:
    found = [path for path in destination.rglob("*") if path.is_file()]
    return [path for path in found if path.name.endswith(ARCHIVE_SUFFIXES)]


def prune(destination: Path, keep: int) -> list[Path]:
    """Delete all but the ``keep`` newest archives (and their manifests)."""
    if keep < 0:
        raise ValueError("keep must be >= 0")

    archives = sorted(
        find_archives(destination),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed: list[Path] = []
    for archive in archives[keep:]:
        manifest = manifest_path_for(archive)
        archive.unlink()
        if manifest.exists():
            manifest.unlink()
        removed.append(archive)
    return removed
