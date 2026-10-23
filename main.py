"""Backwards-compatible entry point: ``python main.py``.

The implementation lives in the ``quickbackup`` package (``src/`` layout);
install it with ``pip install -e .`` or run ``python -m quickbackup``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from quickbackup.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
