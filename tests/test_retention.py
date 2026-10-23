import os
import time
from pathlib import Path

from quickbackup.manifest import manifest_path_for
from quickbackup.retention import find_archives, prune


def make_archive(directory: Path, name: str, age_seconds: int = 0) -> Path:
    path = directory / name
    path.write_bytes(b"data")
    timestamp = time.time() - age_seconds
    os.utime(path, (timestamp, timestamp))
    return path


def test_find_archives_only_matches_archives(tmp_path: Path) -> None:
    make_archive(tmp_path, "a.zip")
    make_archive(tmp_path, "b.tar.gz")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    names = {path.name for path in find_archives(tmp_path)}
    assert names == {"a.zip", "b.tar.gz"}


def test_prune_keeps_newest(tmp_path: Path) -> None:
    oldest = make_archive(tmp_path, "old.zip", age_seconds=300)
    middle = make_archive(tmp_path, "middle.zip", age_seconds=200)
    newest = make_archive(tmp_path, "new.zip", age_seconds=100)
    manifest = manifest_path_for(oldest)
    manifest.write_text("{}", encoding="utf-8")

    removed = prune(tmp_path, keep=2)

    assert removed == [oldest]
    assert newest.exists() and middle.exists()
    assert not manifest.exists()


def test_prune_zero_removes_everything(tmp_path: Path) -> None:
    make_archive(tmp_path, "a.zip")
    make_archive(tmp_path, "b.zip")
    removed = prune(tmp_path, keep=0)
    assert len(removed) == 2
    assert find_archives(tmp_path) == []


def test_prune_negative_keep_is_rejected(tmp_path: Path) -> None:
    try:
        prune(tmp_path, keep=-1)
    except ValueError as exc:
        assert "keep" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
