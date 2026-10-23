import json
from pathlib import Path

import pytest

import quickbackup.cli as cli
from quickbackup.cli import main, safe_component
from quickbackup.errors import ArchiveError


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "sub").mkdir(parents=True)
    (root / "readme.txt").write_text("hello", encoding="utf-8")
    (root / "sub" / "code.py").write_text("print(1)\n", encoding="utf-8")
    return root


def run(args):
    return main(args)


def test_backup_creates_archive_and_manifest(source: Path, tmp_path: Path, capsys) -> None:
    dest = tmp_path / "backups"
    code = run([str(source), "-d", str(dest), "--json", "--quiet"])
    assert code == 0

    payload = json.loads(capsys.readouterr().out)
    archive = Path(payload["archive"])
    assert payload["status"] == "ok"
    assert payload["files"] == 2
    assert archive.exists()
    assert manifest_path(archive).exists()
    assert payload["sha256"]


def manifest_path(archive: Path) -> Path:
    return archive.with_name(archive.name + ".manifest.json")


def test_dry_run_writes_nothing(source: Path, tmp_path: Path, capsys) -> None:
    dest = tmp_path / "backups"
    code = run([str(source), "-d", str(dest), "--dry-run", "--json", "--quiet"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry-run"
    assert not dest.exists()


def test_destination_inside_source_is_rejected(source: Path, capsys) -> None:
    code = run([str(source), "-d", str(source / "backups")])
    assert code == 1
    assert "inside source" in capsys.readouterr().err


def test_missing_source_exits_with_error(tmp_path: Path, capsys) -> None:
    code = run([str(tmp_path / "missing"), "-d", str(tmp_path / "out")])
    assert code == 1
    assert "does not exist" in capsys.readouterr().err


def test_verify_flag(source: Path, tmp_path: Path, capsys) -> None:
    code = run([str(source), "-d", str(tmp_path / "b"), "--verify", "--quiet"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Archive:" in out


def test_keep_prunes_old_archives(source: Path, tmp_path: Path, capsys) -> None:
    dest = tmp_path / "backups"
    for name in ("one", "two", "three"):
        assert run([str(source), "-d", str(dest), "-n", name, "--quiet"]) == 0
        capsys.readouterr()

    assert run([str(source), "-d", str(dest), "-n", "four", "--keep", "2", "--quiet"]) == 0
    capsys.readouterr()

    archives = sorted(dest.rglob("*.zip"))
    assert len(archives) == 2
    assert any(archive.name.startswith("four") for archive in archives)


def test_name_collision_gets_suffix(source: Path, tmp_path: Path, capsys) -> None:
    dest = tmp_path / "backups"
    assert run([str(source), "-d", str(dest), "-n", "same", "--quiet"]) == 0
    assert run([str(source), "-d", str(dest), "-n", "same", "--quiet"]) == 0
    capsys.readouterr()

    names = sorted(path.name for path in dest.rglob("*.zip"))
    assert names == ["same-1.zip", "same.zip"]


def test_comment_is_sanitized(source: Path, tmp_path: Path, capsys) -> None:
    dest = tmp_path / "backups"
    assert run([str(source), "-d", str(dest), "-n", "x", "-c", "../evil name", "--quiet"]) == 0
    capsys.readouterr()

    archives = list(dest.rglob("*.zip"))
    assert len(archives) == 1
    assert archives[0].parent == dest / archives[0].parent.name
    assert ".." not in archives[0].name
    assert "/" not in archives[0].name


def test_unknown_format_returns_error(source: Path, tmp_path: Path, capsys) -> None:
    code = run([str(source), "-d", str(tmp_path / "b"), "-f", "rar"])
    assert code == 1
    assert "unknown archive format" in capsys.readouterr().err


def test_atomic_write_leaves_no_partial_file(
    source: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    dest = tmp_path / "backups"

    def broken(entries, target, fmt):
        Path(target).write_bytes(b"partial")
        raise ArchiveError("disk full")

    monkeypatch.setattr(cli, "write_archive", broken)
    code = run([str(source), "-d", str(dest), "--quiet"])
    assert code == 1
    assert "disk full" in capsys.readouterr().err
    assert list(dest.rglob("*.zip")) == []
    assert list(dest.rglob("*.part")) == []


def test_excludes_are_reported(source: Path, tmp_path: Path, capsys) -> None:
    dest = tmp_path / "backups"
    code = run(
        [
            str(source), "-d", str(dest), "--json", "--quiet",
            "-x", "*.py", "-x", "sub",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["files"] == 1
    # The whole excluded directory is pruned, so only one item is reported.
    assert payload["skipped"] == 1


def test_safe_component_strips_path_separators() -> None:
    cleaned = safe_component("../../etc/passwd")
    assert "/" not in cleaned
    assert ".." not in cleaned
    assert not cleaned.startswith(".")
    assert "/" not in safe_component("a/b")
    assert safe_component("  ..  ") == ""
