import json
import os
from pathlib import Path

import pytest

from quickbackup.archive import collect, write_archive
from quickbackup.errors import VerificationError
from quickbackup.manifest import (
    MANIFEST_VERSION,
    Manifest,
    manifest_path_for,
    read_manifest,
    sha256_file,
    verify_archive,
    write_manifest,
)


def make_manifest(archive: Path, **overrides) -> Manifest:
    data = dict(
        version=MANIFEST_VERSION,
        created_at="2026-10-23T12:00:00+0300",
        archive=archive.name,
        sha256=sha256_file(archive),
        bytes=archive.stat().st_size,
        format="zip",
        file_count=1,
        source_bytes=5,
        sources=["/tmp/src"],
        skipped=[],
    )
    data.update(overrides)
    return Manifest(**data)


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.txt").write_text("hello", encoding="utf-8")
    target = tmp_path / "backup.zip"
    write_archive(collect([source]).entries, target, "zip")
    return target


def test_sha256_matches_hashlib(tmp_path: Path) -> None:
    file = tmp_path / "data.bin"
    file.write_bytes(b"abc")
    assert sha256_file(file) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_manifest_roundtrip(tmp_path: Path, archive: Path) -> None:
    path = manifest_path_for(archive)
    manifest = make_manifest(archive)
    write_manifest(manifest, path)
    assert read_manifest(path) == manifest
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == MANIFEST_VERSION
    assert not list(tmp_path.glob("*.part"))


def test_verify_accepts_good_archive(archive: Path) -> None:
    verify_archive(archive, make_manifest(archive))


def test_verify_detects_tampering(archive: Path) -> None:
    manifest = make_manifest(archive)
    with open(archive, "r+b") as handle:
        handle.seek(10)
        handle.write(b"\x00")
    with pytest.raises(VerificationError, match="checksum mismatch"):
        verify_archive(archive, manifest)


def test_verify_detects_size_change(archive: Path) -> None:
    manifest = make_manifest(archive, bytes=1)
    with pytest.raises(VerificationError, match="size changed"):
        verify_archive(archive, manifest)


def test_verify_missing_archive(tmp_path: Path, archive: Path) -> None:
    manifest = make_manifest(archive)
    with pytest.raises(VerificationError, match="missing"):
        verify_archive(tmp_path / "gone.zip", manifest)


def test_manifest_rejects_unknown_version(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"version": 99}), encoding="utf-8")
    with pytest.raises(VerificationError, match="unsupported manifest version"):
        read_manifest(path)


def test_manifest_permissions_are_preserved(tmp_path: Path, archive: Path) -> None:
    path = manifest_path_for(archive)
    write_manifest(make_manifest(archive), path)
    assert os.path.getsize(path) > 0
