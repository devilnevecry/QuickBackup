"""Command-line interface and the backup pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .archive import (
    Collection,
    archive_extension,
    collect,
    normalize_format,
    write_archive,
)
from .errors import DestinationError, QuickBackupError
from .manifest import (
    MANIFEST_VERSION,
    Manifest,
    manifest_path_for,
    sha256_file,
    verify_archive,
    write_manifest,
)
from .retention import prune

LOGGER = logging.getLogger("quickbackup")
DEFAULT_DESTINATION = Path.home() / "Backups"


@dataclass
class BackupResult:
    archive: Path
    manifest: Manifest
    duration: float
    pruned: list[Path]


def safe_component(value: str, limit: int = 60) -> str:
    """Turn arbitrary user text into one safe path component."""
    cleaned = "".join(
        character if character.isalnum() or character in "-_. " else "_"
        for character in value
    )
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = re.sub(r"[\s_]+", "_", cleaned.strip(" ."))
    return cleaned.strip(" .")[:limit]


def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        size /= 1024.0
        if size < 1024 or unit == "TiB":
            return f"{size:.2f} {unit}"
    return f"{size} B"  # pragma: no cover


def _check_destination(destination: Path, sources: Sequence[Path]) -> None:
    destination = destination.resolve()
    for source in sources:
        if source.is_dir() and (
            destination == source or destination.is_relative_to(source)
        ):
            raise DestinationError(
                f"destination {destination} is inside source {source}; choose another "
                "directory or exclude it"
            )
    if destination.exists() and not destination.is_dir():
        raise DestinationError(f"destination is not a directory: {destination}")


def _unique_target(target: Path) -> Path:
    if not target.exists():
        return target
    stem, suffix = target.name, ""
    for known in (".tar.gz", ".tar.xz", ".zip", ".tar"):
        if stem.endswith(known):
            stem, suffix = stem[: -len(known)], known
            break
    counter = 1
    while True:
        candidate = target.with_name(f"{stem}-{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def build_archive_name(comment: str, name: str | None) -> str:
    base = safe_component(name) if name else time.strftime("%H-%M-%S")
    comment_part = safe_component(comment)
    if comment_part:
        base = f"{base}_{comment_part}"
    if not base:
        base = time.strftime("%H-%M-%S")
    return base


def run_backup(
    sources: Sequence[Path],
    destination: Path,
    *,
    archive_format: str = "zip",
    name: str | None = None,
    comment: str = "",
    excludes: Sequence[str] = (),
    follow_symlinks: bool = False,
    dry_run: bool = False,
    verify: bool = False,
    keep: int | None = None,
) -> BackupResult:
    """Create one archive and (optionally) verify and prune old ones."""
    started = time.monotonic()
    destination = Path(destination).expanduser()
    resolved_sources = [Path(source).expanduser().resolve() for source in sources]
    if not resolved_sources:
        raise QuickBackupError("nothing to back up: no sources given")

    _check_destination(destination, resolved_sources)
    fmt = normalize_format(archive_format)

    collection: Collection = collect(
        resolved_sources, excludes=excludes, follow_symlinks=follow_symlinks
    )
    if not collection.entries:
        raise QuickBackupError("sources are empty; nothing to archive")

    archive_name = build_archive_name(comment, name)
    day_directory = destination / time.strftime("%Y-%m-%d")
    target = _unique_target(day_directory / (archive_name + archive_extension(fmt)))

    if dry_run:
        manifest = Manifest(
            version=MANIFEST_VERSION,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            archive=target.name,
            sha256="",
            bytes=0,
            format=fmt,
            file_count=collection.file_count,
            source_bytes=collection.total_bytes,
            sources=[str(source) for source in resolved_sources],
            skipped=[{"path": item.path, "reason": item.reason} for item in collection.skipped],
        )
        return BackupResult(target, manifest, time.monotonic() - started, [])

    day_directory.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name("." + target.name + ".part")
    pruned: list[Path] = []
    try:
        write_archive(collection.entries, temporary, fmt)
        manifest = Manifest(
            version=MANIFEST_VERSION,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            archive=target.name,
            sha256=sha256_file(temporary),
            bytes=temporary.stat().st_size,
            format=fmt,
            file_count=collection.file_count,
            source_bytes=collection.total_bytes,
            sources=[str(source) for source in resolved_sources],
            skipped=[{"path": item.path, "reason": item.reason} for item in collection.skipped],
        )
        if verify:
            verify_archive(temporary, manifest)
        os.replace(temporary, target)
        write_manifest(manifest, manifest_path_for(target))
        if keep is not None:
            pruned = prune(destination, keep)
    finally:
        if temporary.exists():
            temporary.unlink()

    return BackupResult(target, manifest, time.monotonic() - started, pruned)


def _print_result(result: BackupResult, *, as_json: bool, dry_run: bool) -> None:
    manifest = result.manifest
    if as_json:
        payload = {
            "status": "dry-run" if dry_run else "ok",
            "archive": str(result.archive),
            "format": manifest.format,
            "files": manifest.file_count,
            "source_bytes": manifest.source_bytes,
            "archive_bytes": manifest.bytes,
            "sha256": manifest.sha256 or None,
            "duration_seconds": round(result.duration, 3),
            "skipped": len(manifest.skipped),
            "pruned": [str(path) for path in result.pruned],
        }
        if manifest.source_bytes and not dry_run:
            payload["saved_percent"] = round(
                (1 - manifest.bytes / manifest.source_bytes) * 100, 2
            )
        print(json.dumps(payload, ensure_ascii=False))
        return

    print("{}: {}".format("Would create" if dry_run else "Archive", result.archive))
    print(f"Format: {manifest.format}")
    print(f"Files: {manifest.file_count}")
    print(f"Source size: {format_bytes(manifest.source_bytes)}")
    if not dry_run:
        print(f"Archive size: {format_bytes(manifest.bytes)}")
        if manifest.source_bytes:
            saved = (1 - manifest.bytes / manifest.source_bytes) * 100
            if saved >= 0:
                print(f"Saved: {saved:.2f}%")
            else:
                print(f"Overhead: {-saved:.2f}% (archive is larger than the source)")
        print(f"SHA-256: {manifest.sha256}")
    if manifest.skipped:
        print(f"Skipped: {len(manifest.skipped)} item(s) (excluded, symlinks or special files)")
    for path in result.pruned:
        print(f"Pruned: {path}")
    print(f"Duration: {result.duration:.2f} s")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quickbackup",
        description=(
            "Back up files and directories into ZIP or TAR archives. "
            "Run without arguments for the interactive wizard."
        ),
    )
    parser.add_argument("sources", nargs="*", help="files or directories to archive")
    parser.add_argument(
        "-d", "--dest", default=str(DEFAULT_DESTINATION),
        help="destination directory (default: ~/Backups)",
    )
    parser.add_argument(
        "-f", "--format", default="zip",
        help="zip, tar, tar.gz, tgz, tar.xz or txz (default: zip)",
    )
    parser.add_argument("-n", "--name", help="archive name without extension")
    parser.add_argument("-c", "--comment", default="", help="comment added to the name")
    parser.add_argument(
        "-x", "--exclude", action="append", default=[], metavar="GLOB",
        help="skip paths matching this glob (repeatable)",
    )
    parser.add_argument(
        "--follow-symlinks", action="store_true",
        help="follow symlinks (loop protection is built in)",
    )
    parser.add_argument("--dry-run", action="store_true", help="show what would be archived")
    parser.add_argument(
        "--verify", action="store_true",
        help="re-read the archive and compare it with the manifest",
    )
    parser.add_argument(
        "--keep", type=int, metavar="N",
        help="keep only the N newest archives in the destination",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress log output")
    parser.add_argument("--log", metavar="FILE", help="write a rotating log to FILE")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return parser


def configure_logging(quiet: bool, log_file: str | None) -> None:
    LOGGER.handlers.clear()
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    if not quiet:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOGGER.addHandler(console)
    if log_file:
        from logging.handlers import RotatingFileHandler

        handler = RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOGGER.addHandler(handler)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.quiet, args.log)

    try:
        if not args.sources:
            if not sys.stdin.isatty():
                print(
                    "quickbackup: no sources given and stdin is not a terminal",
                    file=sys.stderr,
                )
                return 2
            from .interactive import run_wizard

            return run_wizard(
                destination=Path(args.dest),
                archive_format=args.format,
                excludes=args.exclude,
                verify=args.verify,
                keep=args.keep,
            )

        result = run_backup(
            [Path(source) for source in args.sources],
            Path(args.dest),
            archive_format=args.format,
            name=args.name,
            comment=args.comment,
            excludes=args.exclude,
            follow_symlinks=args.follow_symlinks,
            dry_run=args.dry_run,
            verify=args.verify,
            keep=args.keep,
        )
        _print_result(result, as_json=args.json, dry_run=args.dry_run)
        if not args.dry_run and not args.quiet:
            LOGGER.info("backup completed: %s", result.archive)
        return 0
    except QuickBackupError as exc:
        LOGGER.error("%s", exc)
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1
    except KeyboardInterrupt:
        LOGGER.warning("interrupted")
        return 130
