"""音声再生（Windows の MCI を ctypes で叩く）。

外部ライブラリ無しで「wav を開く / 途中から鳴らす / 止める / 現在位置 / 区間ループ」ができる。
wav 以外は非対応（音库は全部 wav）。
"""

from __future__ import annotations

import ctypes
import itertools
import os

_IS_WIN = os.name == "nt"
if _IS_WIN:
    try:
        _winmm = ctypes.WinDLL("winmm")
    except Exception:                      # pragma: no cover
        _winmm = None
else:                                      # pragma: no cover
    _winmm = None

_counter = itertools.count(1)


class PlayerError(RuntimeError):
    pass


def _mci_error(code: int) -> str:
    if not _winmm:
        return f"MCI 不可用 (code={code})"
    buf = ctypes.create_unicode_buffer(512)
    try:
        _winmm.mciGetErrorStringW(code, buf, 512)
    except Exception:                      # pragma: no cover
        return f"code={code}"
    return buf.value or f"code={code}"


class Player:
    """1 本の wav を鳴らす。open → play(start, end) → 自動で止まる。"""

    def __init__(self, widget=None):
        self.widget = widget
        self.alias = ""
        self.path = ""
        self.length = 0.0
        self._tick_job = None
        self._tick_cb = None
        self._seg_end: float | None = None
        self._seg_start = 0.0
        self._loop = False

    # ---------------------------------------------------------------- MCI
    def _cmd(self, cmd: str) -> str:
        if not _winmm:
            raise PlayerError("この環境では音声再生が使えません（Windows のみ）")
        buf = ctypes.create_unicode_buffer(512)
        rc = _winmm.mciSendStringW(cmd, buf, 512, None)
        if rc:
            raise PlayerError(f"{_mci_error(rc)}  ← {cmd}")
        return buf.value

    def available(self) -> bool:
        return bool(_winmm)

    # ---------------------------------------------------------------- open
    def open(self, path: str) -> float:
        """開いて長さ（秒）を返す。同じファイルなら何もしない。"""
        path = os.path.abspath(path)
        if path == self.path and self.alias:
            return self.length
        self.close()
        if not os.path.isfile(path):
            raise PlayerError(f"音声が無い: {path}")
        self.alias = f"sp{next(_counter)}"
        self._cmd(f'open "{path}" type waveaudio alias {self.alias}')
        self.path = path
        try:
            self._cmd(f"set {self.alias} time format milliseconds")
            ms = self._cmd(f"status {self.alias} length")
            self.length = int(ms or 0) / 1000.0
        except PlayerError:
            self.length = 0.0
        return self.length

    def close(self) -> None:
        self.stop_ticker()
        if self.alias and _winmm:
            try:
                self._cmd(f"close {self.alias}")
            except PlayerError:
                pass
        self.alias = ""
        self.path = ""
        self.length = 0.0

    # ---------------------------------------------------------------- 再生
    def position(self) -> float:
        if not self.alias:
            return 0.0
        try:
            return int(self._cmd(f"status {self.alias} position") or 0) / 1000.0
        except PlayerError:
            return 0.0

    def is_playing(self) -> bool:
        if not self.alias:
            return False
        try:
            return self._cmd(f"status {self.alias} mode").strip() == "playing"
        except PlayerError:
            return False

    def seek(self, t: float) -> None:
        if not self.alias:
            return
        ms = max(0, int(round(t * 1000)))
        self._cmd(f"seek {self.alias} to {ms}")

    def play(self, start: float | None = None, end: float | None = None,
             loop: bool = False, on_tick=None) -> None:
        """start 秒から end 秒まで鳴らす（end=None なら最後まで・loop 可）。"""
        if not self.alias:
            raise PlayerError("先に open して")
        if start is None:
            start = 0.0
        self._seg_start = max(0.0, start)
        self._seg_end = end
        self._loop = loop
        self._tick_cb = on_tick
        cmd = f"play {self.alias} from {int(round(self._seg_start * 1000))}"
        if self._seg_end is not None and self._seg_end > self._seg_start:
            # to を付けないと「ファイル末尾まで」鳴ってしまう（tick 頼みになり精度も落ちる）
            cmd += f" to {int(round(self._seg_end * 1000))}"
        self._cmd(cmd)
        self._start_ticker()

    def set_segment(self, start: float, end: float | None) -> None:
        """再生中の区間だけ差し替える（鳴らし直さない）。

        切点を直したとき、次のループから新しい長さで回すために使う。
        """
        self._seg_start = max(0.0, start)
        self._seg_end = end

    def pause(self) -> None:
        if self.alias:
            try:
                self._cmd(f"pause {self.alias}")
            except PlayerError:
                pass

    def resume(self) -> None:
        if self.alias:
            try:
                self._cmd(f"resume {self.alias}")
            except PlayerError:
                pass

    def stop(self) -> None:
        if self.alias:
            try:
                self._cmd(f"seek {self.alias} to 0")
                self._cmd(f"stop {self.alias}")
            except PlayerError:
                pass
        self.stop_ticker()

    # ---------------------------------------------------------------- tick
    def _start_ticker(self) -> None:
        self.stop_ticker()
        if not self.widget:
            return
        self._tick()

    def stop_ticker(self) -> None:
        if self._tick_job and self.widget:
            try:
                self.widget.after_cancel(self._tick_job)
            except Exception:
                pass
        self._tick_job = None

    def mode(self) -> str:
        """playing / paused / stopped（取れなければ空文字）。"""
        if not self.alias:
            return ""
        try:
            return self._cmd(f"status {self.alias} mode").strip()
        except PlayerError:
            return ""

    def _tick(self) -> None:
        if not self.widget or not self.alias:
            return
        pos = self.position()
        mode = self.mode()
        # 鳴り始めは MCI がまだ前の位置を返すことがある → from 未満は出さない
        if pos < self._seg_start - 1e-3:
            pos = self._seg_start
        # 一時停止は「終わり」ではない。ここで stop すると位置が 0 に飛ぶ。
        if mode == "paused":
            if self._tick_cb:
                self._tick_cb(pos, False)
            self._tick_job = self.widget.after(120, self._tick)
            return
        reached_end = self._seg_end is not None and pos >= self._seg_end - 1e-3
        stopped = mode == "stopped" and pos > self._seg_start + 1e-3
        if reached_end or stopped:
            if self._loop and self._seg_end is not None and self._seg_end > self._seg_start:
                try:
                    self._cmd(f"play {self.alias} from {int(round(self._seg_start * 1000))}"
                              f" to {int(round(self._seg_end * 1000))}")
                except PlayerError:
                    pass
                self._tick_job = self.widget.after(33, self._tick)
                return
            end = self._seg_end if self._seg_end is not None else pos
            self.stop()
            if self._tick_cb:
                self._tick_cb(end, False)
            return
        if self._tick_cb:
            self._tick_cb(pos, mode == "playing" or not mode)
        self._tick_job = self.widget.after(33, self._tick)


class NullPlayer:
    """再生できない環境用のダミー（落ちないように）。"""

    length = 0.0
    path = ""

    def available(self) -> bool:
        return False

    def open(self, path: str) -> float:
        return 0.0

    def close(self) -> None:
        pass

    def position(self) -> float:
        return 0.0

    def is_playing(self) -> bool:
        return False

    def seek(self, t: float) -> None:
        pass

    def play(self, *a, **kw) -> None:
        pass

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def stop_ticker(self) -> None:
        pass


def make_player(widget=None):
    return Player(widget) if _winmm else NullPlayer()
