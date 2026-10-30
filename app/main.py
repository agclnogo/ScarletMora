"""exe / ソース実行の入口。

    python app\\main.py            … 管理画面を開く
    python app\\main.py --selftest … 画面なしで主要経路を点検
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _fix_streams() -> None:
    """--noconsole の exe では stdout/stderr が None。print で落ちないようにする。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        from scarlet import paths

        logdir = paths.logs_dir()
    except Exception:
        logdir = HERE
    try:
        f = open(os.path.join(logdir, "stdout.log"), "a", encoding="utf-8", buffering=1)
    except Exception:
        return
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


def main() -> int:
    _fix_streams()
    from scarlet import gui

    return gui.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
