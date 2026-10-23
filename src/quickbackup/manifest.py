"""Archive manifests: checksums, verification and human-readable metadata."""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .errors import VerificationError

MANIFEST_VERSION = 1
_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class Manifest:
    version: int
    created_at: str
    archive: str
    sha256: str
    bytes: int
    format: str
    file_count: int
    source_bytes: int
    sources: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        if data.get("version") != MANIFEST_VERSION:
            raise VerificationError(
                "unsupported manifest version: {!r}".format(data.get("version"))
            )
        return cls(
            version=data["version"],
            created_at=data["created_at"],
            archive=data["archive"],
            sha256=data["sha256"],
            bytes=int(data["bytes"]),
            format=data["format"],
            file_count=int(data["file_count"]),
            source_bytes=int(data["source_bytes"]),
            sources=list(data.get("sources", [])),
            skipped=list(data.get("skipped", [])),
        )


def manifest_path_for(archive: Path) -> Path:
    return archive.with_name(archive.name + ".manifest.json")


def write_manifest(manifest: Manifest, path: Path) -> None:
    temporary = path.with_name("." + path.name + ".part")
    try:
        temporary.write_text(manifest.to_json(), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_manifest(path: Path) -> Manifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VerificationError(f"cannot read manifest {path}: {exc}") from exc
    except ValueError as exc:
        raise VerificationError(f"manifest is not valid JSON: {exc}") from exc
    return Manifest.from_dict(data)


def verify_archive(archive: Path, manifest: Manifest) -> None:
    """Check size, checksum and that every member can be read back."""
    if not archive.is_file():
        raise VerificationError(f"archive is missing: {archive}")

    size = archive.stat().st_size
    if size != manifest.bytes:
        raise VerificationError(
            f"archive size changed: expected {manifest.bytes} bytes, got {size}"
        )

    actual = sha256_file(archive)
    if actual != manifest.sha256:
        raise VerificationError(
            f"archive checksum mismatch: expected {manifest.sha256}, got {actual}"
        )

    try:
        if manifest.format == "zip":
            with zipfile.ZipFile(archive) as handle:
                bad = handle.testzip()
                if bad is not None:
                    raise VerificationError(f"corrupted member: {bad}")
                for info in handle.infolist():
                    if not info.is_dir():
                        with handle.open(info) as member:
                            while member.read(_CHUNK):
                                pass
        else:
            with tarfile.open(archive, "r:*") as handle:
                for member in handle:
                    if member.isfile():
                        extracted = handle.extractfile(member)
                        if extracted is None:
                            raise VerificationError(
                                f"cannot read member: {member.name}"
                            )
                        with extracted:
                            while extracted.read(_CHUNK):
                                pass
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise VerificationError(f"cannot read archive: {exc}") from exc
