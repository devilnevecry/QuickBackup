"""Interactive wizard kept for the original QuickBackup workflow."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .cli import _print_result, format_bytes, run_backup
from .errors import QuickBackupError

YES = {"y", "yes", "да", "д"}
NO = {"n", "no", "нет", "н"}


class WizardExit(Exception):
    """Raised when the user leaves the wizard."""


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise WizardExit() from exc


def _ask_yes_no(prompt: str) -> bool | None:
    while True:
        answer = _ask(prompt).lower()
        if answer in YES:
            return True
        if answer in NO:
            return False
        if answer in ("", "q"):
            return None
        print("Введите «да» или «нет».")


def _print_directory(directory: Path) -> tuple[list[Path], list[Path]]:
    try:
        items = sorted(directory.iterdir(), key=lambda item: item.name.lower())
    except OSError as exc:
        print(f"Не удалось прочитать {directory}: {exc}")
        return [], []

    folders = [item for item in items if item.is_dir()]
    files = [item for item in items if not item.is_dir()]

    print(f"\nКаталог: {directory}")
    print("\nПапки:")
    for index, folder in enumerate(folders, start=1):
        print(f"  {index}. {folder.name}/")
    if not folders:
        print("  —")

    print("\nФайлы:")
    offset = len(folders)
    for index, file in enumerate(files, start=offset + 1):
        print(f"  {index}. {file.name}")
    if not files:
        print("  —")

    print(
        "\nВведите номер, 0 — вверх, «l» — список, «r» — убрать, "
        "«d» — каталог назначения, «s» — начать, «q» — выход"
    )
    return folders, files


def _show_selection(selection: Sequence[Path]) -> None:
    print("\nВыбрано:")
    if not selection:
        print("  —")
        return
    for index, path in enumerate(selection, start=1):
        print(f"  {index}. {path}")


def _show_total(selection: Sequence[Path]) -> None:
    total = 0
    for path in selection:
        if path.is_dir():
            total += sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
        elif path.exists():
            total += path.stat().st_size
    print(f"Общий размер: {format_bytes(total)}")


def _remove(selection: list[Path]) -> None:
    if not selection:
        print("Список пуст.")
        return
    _show_selection(selection)
    answer = _ask("Номер для удаления (Enter — отмена): ")
    if not answer:
        return
    try:
        index = int(answer) - 1
    except ValueError:
        print("Нужен номер.")
        return
    if 0 <= index < len(selection):
        removed = selection.pop(index)
        print(f"Удалено из списка: {removed}")
    else:
        print("Нет такого номера.")


def _choose_destination(current: Path) -> Path:
    answer = _ask(f"Каталог для резервных копий [{current}]: ")
    if not answer:
        return current
    candidate = Path(answer).expanduser()
    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Не удалось создать {candidate}: {exc}")
        return current
    return candidate


def _wizard(
    *,
    destination: Path,
    archive_format: str = "zip",
    excludes: Sequence[str] = (),
    verify: bool = False,
    keep: int | None = None,
) -> int:
    """Collect sources interactively and create one archive."""
    directory = Path.home()
    selection: list[Path] = []

    while True:
        folders, files = _print_directory(directory)
        _show_total(selection)
        answer = _ask("Ваш выбор: ").lower()

        if answer in ("q", "quit", "exit", "выход"):
            print("Отменено.")
            return 0
        if answer in ("s", "start", "готово", "начать"):
            break
        if answer in ("l", "list", "список"):
            _show_selection(selection)
            continue
        if answer in ("r", "remove", "убрать"):
            _remove(selection)
            continue
        if answer in ("d", "dest", "каталог"):
            destination = _choose_destination(destination)
            continue
        if answer == "0":
            if directory.parent != directory:
                directory = directory.parent
            else:
                print("Это корневой каталог.")
            continue
        if answer.startswith("a "):
            candidate = Path(answer[2:]).expanduser()
            if not candidate.exists():
                print(f"Путь не найден: {candidate}")
            elif candidate in selection:
                print("Уже в списке.")
            else:
                selection.append(candidate)
                print(f"Добавлено: {candidate}")
            continue

        try:
            index = int(answer)
        except ValueError:
            print("Некорректный ввод.")
            continue

        if index <= 0:
            print("Некорректный номер.")
            continue

        if index <= len(folders):
            folder = folders[index - 1]
            action = _ask(
                f'Папка "{folder.name}": «копировать» или «зайти»? '
            ).lower()
            if action in ("копировать", "copy", "c", "добавить"):
                if folder in selection:
                    print("Уже в списке.")
                else:
                    selection.append(folder)
                    print(f"Добавлено: {folder}")
            elif action in ("зайти", "enter", "e", "открыть"):
                directory = folder
            else:
                print("Не понял. Введите «копировать» или «зайти».")
            continue

        file_index = index - len(folders) - 1
        if 0 <= file_index < len(files):
            file = files[file_index]
            if file in selection:
                print("Уже в списке.")
            else:
                selection.append(file)
                print(f"Добавлено: {file}")
        else:
            print("Нет такого номера.")

    if not selection:
        print("Список пуст — резервировать нечего.")
        return 1

    _show_selection(selection)
    _show_total(selection)

    confirmed = _ask_yes_no("\nСоздать архив? (да/нет): ")
    if not confirmed:
        print("Отменено.")
        return 0

    answer = _ask(f"Формат архива (zip/tar/tar.gz/tar.xz) [{archive_format}]: ")
    if answer:
        archive_format = answer

    comment = _ask("Комментарий (необязательно): ")

    try:
        result = run_backup(
            selection,
            destination,
            archive_format=archive_format,
            comment=comment,
            excludes=excludes,
            verify=verify,
            keep=keep,
        )
    except QuickBackupError as exc:
        print(f"Ошибка: {exc}")
        return 1

    _print_result(result, as_json=False, dry_run=False)
    return 0


def run_wizard(**kwargs) -> int:
    """Run the wizard, turning Ctrl+C / EOF into a clean cancel."""
    try:
        return _wizard(**kwargs)
    except WizardExit:
        print("Отменено.")
        return 0
