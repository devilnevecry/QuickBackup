import tarfile
import zipfile
from pathlib import Path

import pytest

from quickbackup.archive import collect, normalize_format, write_archive
from quickbackup.errors import ArchiveError, SourceError


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    (root / "sub").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8")
    (root / "sub" / "b.txt").write_text("world", encoding="utf-8")
    (root / "sub" / "skip.log").write_text("noise", encoding="utf-8")
    (root / "имя с пробелом.txt").write_text("кириллица", encoding="utf-8")
    return root


def test_zip_contains_nested_files(tree: Path, tmp_path: Path) -> None:
    """Regression: zipfile.write(directory) alone does not recurse."""
    collection = collect([tree])
    target = tmp_path / "backup.zip"
    write_archive(collection.entries, target, "zip")

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        assert "src/a.txt" in names
        assert "src/sub/b.txt" in names
        assert "src/sub/skip.log" in names
        assert "src/empty/" in names
        assert archive.read("src/sub/b.txt") == b"world"


def test_tar_contains_nested_files(tree: Path, tmp_path: Path) -> None:
    collection = collect([tree])
    target = tmp_path / "backup.tar.gz"
    write_archive(collection.entries, target, "tar.gz")

    with tarfile.open(target, "r:gz") as archive:
        names = set(archive.getnames())
        assert "src/a.txt" in names
        assert "src/sub/b.txt" in names
        assert "src/empty" in names


def test_collect_counts_only_files(tree: Path) -> None:
    collection = collect([tree])
    assert collection.file_count == 4
    assert collection.total_bytes == sum(
        len(value.encode("utf-8"))
        for value in ("hello", "world", "noise", "кириллица")
    )


def test_excludes_apply_to_name_and_path(tree: Path) -> None:
    collection = collect([tree], excludes=["*.log", "src/empty"])
    arcnames = [entry.arcname for entry in collection.entries]
    assert "src/sub/skip.log" not in arcnames
    assert "src/empty" not in arcnames
    assert {item.reason for item in collection.skipped} == {"excluded"}
    assert len(collection.skipped) == 2


def test_source_collision_is_rejected(tmp_path: Path) -> None:
    first = tmp_path / "one" / "data"
    second = tmp_path / "two" / "data"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    with pytest.raises(SourceError, match="same name"):
        collect([first, second])


def test_missing_source_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="does not exist"):
        collect([tmp_path / "nope"])


def test_unknown_format_is_rejected() -> None:
    with pytest.raises(ArchiveError, match="unknown archive format"):
        normalize_format("rar")


def test_format_aliases() -> None:
    assert normalize_format("tgz") == "tar.gz"
    assert normalize_format(".ZIP") == "zip"
    assert normalize_format("txz") == "tar.xz"


def test_symlinks_are_skipped_by_default(tree: Path, tmp_path: Path) -> None:
    link = tree / "link.txt"
    link.symlink_to(tree / "a.txt")

    collection = collect([tree])
    arcnames = [entry.arcname for entry in collection.entries]
    assert "src/link.txt" not in arcnames
    assert any(item.path == "src/link.txt" for item in collection.skipped)


def test_symlink_loop_is_broken_when_following(tree: Path) -> None:
    (tree / "loop").symlink_to(tree)

    collection = collect([tree], follow_symlinks=True)
    arcnames = [entry.arcname for entry in collection.entries]
    assert len(arcnames) == len(set(arcnames))
    assert any(item.reason == "symlink loop" for item in collection.skipped)


def test_single_file_source(tmp_path: Path) -> None:
    file = tmp_path / "report.pdf"
    file.write_bytes(b"%PDF-1.4")
    collection = collect([file])
    assert [entry.arcname for entry in collection.entries] == ["report.pdf"]
    assert collection.total_bytes == 8
