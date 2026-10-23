from pathlib import Path

import pytest

from quickbackup.interactive import run_wizard


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    (home / "docs").mkdir(parents=True)
    (home / "docs" / "note.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def feed(monkeypatch, answers):
    iterator = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(iterator))


def test_wizard_creates_archive(home: Path, tmp_path: Path, monkeypatch) -> None:
    feed(monkeypatch, ["1", "копировать", "s", "да", "zip", "from wizard"])
    dest = tmp_path / "backups"

    code = run_wizard(destination=dest)

    assert code == 0
    archives = list(dest.rglob("*.zip"))
    assert len(archives) == 1
    assert "from_wizard" in archives[0].name


def test_wizard_quit(home: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    feed(monkeypatch, ["q"])
    assert run_wizard(destination=tmp_path / "backups") == 0
    assert "Отменено" in capsys.readouterr().out


def test_wizard_without_selection(home: Path, tmp_path: Path, monkeypatch) -> None:
    feed(monkeypatch, ["s"])
    assert run_wizard(destination=tmp_path / "backups") == 1


def test_wizard_remove_and_navigate(home: Path, tmp_path: Path, monkeypatch) -> None:
    # add the folder, try to remove it, then start with an empty list
    feed(monkeypatch, ["1", "копировать", "r", "1", "s"])
    assert run_wizard(destination=tmp_path / "backups") == 1


def test_wizard_eof_cancels(home: Path, tmp_path: Path, monkeypatch) -> None:
    def raise_eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    assert run_wizard(destination=tmp_path / "backups") == 0
