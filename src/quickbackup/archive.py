"""Collecting sources and writing archives.

The public API is deliberately small:

* :func:`collect` walks the sources once and returns exactly what will be stored
  (directories included, so empty directories survive a backup);
* :func:`write_archive` writes those entries into ZIP or TAR without re-walking
  the filesystem.
"""

from __future__ import annotations

import fnmatch
import os
import tarfile
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ArchiveError, SourceError

FORMATS = {
    "zip": ".zip",
    "tar": ".tar",
    "tar.gz": ".tar.gz",
    "tgz": ".tar.gz",
    "tar.xz": ".tar.xz",
    "txz": ".tar.xz",
}

_TAR_MODES = {
    ".tar": "w",
    ".tar.gz": "w:gz",
    ".tar.xz": "w:xz",
}


@dataclass(frozen=True)
class Entry:
    """One object to put into the archive."""

    path: Path
    arcname: str
    size: int
    is_dir: bool


@dataclass
class Skipped:
    path: str
    reason: str


@dataclass
class Collection:
    entries: list[Entry] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    total_bytes: int = 0
    file_count: int = 0

    def add(self, entry: Entry) -> None:
        self.entries.append(entry)
        if not entry.is_dir:
            self.total_bytes += entry.size
            self.file_count += 1


def normalize_format(value: str) -> str:
    """Return a canonical format name or raise ``ValueError``."""
    key = value.strip().lower().lstrip(".")
    if key in ("gz", "gzip"):
        key = "tar.gz"
    if key in ("xz",):
        key = "tar.xz"
    if key not in FORMATS:
        raise ArchiveError(
            "unknown archive format {!r}; choose one of: {}".format(
                value, ", ".join(sorted(set(FORMATS)))
            )
        )
    return "tar.gz" if key == "tgz" else "tar.xz" if key == "txz" else key


def _is_excluded(arcname: str, name: str, patterns: Sequence[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatchcase(arcname, pattern):
            return True
        if fnmatch.fnmatchcase(name, pattern):
            return True
    return False


def _collect_dir(
    directory: Path,
    arcname: str,
    *,
    excludes: Sequence[str],
    follow_symlinks: bool,
    collection: Collection,
    seen_dirs: set[str],
) -> None:
    collection.add(Entry(directory, arcname, 0, True))
    try:
        with os.scandir(directory) as iterator:
            children = sorted(iterator, key=lambda item: item.name)
    except OSError as exc:
        raise SourceError(f"cannot read {directory}: {exc}") from exc

    for child in children:
        child_arcname = f"{arcname}/{child.name}"
        if _is_excluded(child_arcname, child.name, excludes):
            collection.skipped.append(Skipped(child_arcname, "excluded"))
            continue

        try:
            is_link = child.is_symlink()
            if is_link and not follow_symlinks:
                collection.skipped.append(Skipped(child_arcname, "symlink skipped"))
                continue

            if child.is_dir(follow_symlinks=follow_symlinks):
                real = os.path.realpath(child.path)
                if real in seen_dirs:
                    collection.skipped.append(Skipped(child_arcname, "symlink loop"))
                    continue
                seen_dirs.add(real)
                _collect_dir(
                    Path(child.path),
                    child_arcname,
                    excludes=excludes,
                    follow_symlinks=follow_symlinks,
                    collection=collection,
                    seen_dirs=seen_dirs,
                )
            elif child.is_file(follow_symlinks=follow_symlinks):
                size = child.stat(follow_symlinks=follow_symlinks).st_size
                collection.add(Entry(Path(child.path), child_arcname, size, False))
            else:
                # Sockets, FIFOs, devices: not meaningful in a backup archive.
                collection.skipped.append(Skipped(child_arcname, "special file"))
        except OSError as exc:
            raise SourceError(f"cannot read {child.path}: {exc}") from exc


def collect(
    sources: Iterable[os.PathLike | str],
    *,
    excludes: Sequence[str] = (),
    follow_symlinks: bool = False,
) -> Collection:
    """Walk ``sources`` and return the entries that will be archived.

    Every source keeps its own basename inside the archive. Two sources with the
    same basename are rejected instead of silently overwriting each other.
    """
    collection = Collection()
    used_names: dict[str, str] = {}

    for raw_source in sources:
        source = Path(raw_source).expanduser()
        if not source.exists() and not source.is_symlink():
            raise SourceError(f"source does not exist: {source}")
        try:
            source = source.resolve()
        except OSError as exc:  # pragma: no cover - platform specific
            raise SourceError(f"cannot resolve {source}: {exc}") from exc

        name = source.name or "root"
        if name in used_names:
            raise SourceError(
                f"two sources have the same name {name!r} ({used_names[name]} and {source}); "
                "rename one of them or back them up separately"
            )
        used_names[name] = str(source)

        if source.is_dir():
            _collect_dir(
                source,
                name,
                excludes=excludes,
                follow_symlinks=follow_symlinks,
                collection=collection,
                seen_dirs={os.path.realpath(source)},
            )
        else:
            size = source.stat().st_size
            collection.add(Entry(source, name, size, False))

    return collection


def archive_extension(fmt: str) -> str:
    return FORMATS[normalize_format(fmt)]


def write_archive(entries: Sequence[Entry], target: Path, fmt: str) -> None:
    """Write ``entries`` to ``target``. ``target`` must be a real path.

    Directories are written explicitly, so empty directories are preserved.
    ZIP is deflated; TAR uses gzip/xz according to ``fmt``.
    """
    fmt = normalize_format(fmt)
    extension = FORMATS[fmt]

    if extension == ".zip":
        with zipfile.ZipFile(
            target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for entry in entries:
                archive.write(entry.path, entry.arcname)
        return

    mode = _TAR_MODES[extension]
    with tarfile.open(target, mode) as archive:
        for entry in entries:
            archive.add(entry.path, arcname=entry.arcname, recursive=False)
