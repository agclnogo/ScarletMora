"""波形ビュー（Canvas）。

- ピーク包絡（面）+ RMS（面）で「波形らしく」描く
- 切点（marks の境界）を縦線で出し、ドラッグで動かせる
- 再生ヘッド、句の色分け、モーラ帯
"""

from __future__ import annotations

import tkinter as tk

import numpy as np

from . import theme as T

COMPRESS = 0.55          # 振幅の圧縮（小さい音の形も見えるように）


class WaveView(tk.Canvas):
    HOP_SEC = 0.002          # 包絡の刻み

    def __init__(self, master, on_seek=None, on_boundary=None, on_drag_end=None,
                 on_double=None, on_select=None, on_view=None, **kw):
        super().__init__(master, bg=T.BG1, highlightthickness=0, bd=0, **kw)
        self.on_seek = on_seek
        self.on_boundary = on_boundary
        self.on_drag_end = on_drag_end
        self.on_double = on_double
        self.on_select = on_select
        self.on_view = on_view            # 表示範囲が変わったとき（スライダー同期用）

        self.duration = 0.0
        self.lo = np.zeros(1, dtype=np.float32)
        self.hi = np.zeros(1, dtype=np.float32)
        self.rms = np.zeros(1, dtype=np.float32)
        self.t0 = 0.0
        self.t1 = 1.0
        self.playhead = 0.0
        self.marks: list = []
        self.sel = -1
        self.link = True          # 切点の連動（OFF で片側だけ動かせる）
        self._drag = None          # (index, "start"|"end")
        self._hover = -1

        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-1>", self._on_dbl)
        self.bind("<Motion>", self._on_motion)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Shift-MouseWheel>", self._on_shift_wheel)

    # ---------------------------------------------------------------- データ
    def notify_view(self) -> None:
        """表示範囲が変わったことを外へ知らせる。"""
        if self.on_view is None:
            return
        try:
            self.on_view(self.t0, self.t1)
        except Exception:
            pass

    def set_audio(self, audio) -> None:
        x = np.asarray(audio.data, dtype=np.float32)
        sr = int(audio.sr) or 1
        self.duration = len(x) / sr
        hop = max(1, int(sr * self.HOP_SEC))
        n = len(x) // hop
        if n < 1:
            self.lo = self.hi = self.rms = np.zeros(1, dtype=np.float32)
        else:
            y = x[: n * hop].reshape(n, hop)
            self.lo = y.min(axis=1)
            self.hi = y.max(axis=1)
            self.rms = np.sqrt((y * y).mean(axis=1))
        self.t0, self.t1 = 0.0, max(0.05, self.duration)
        self.playhead = 0.0
        self.redraw()
        self.notify_view()

    def set_marks(self, marks: list) -> None:
        self.marks = marks or []
        if self.sel >= len(self.marks):
            self.sel = len(self.marks) - 1
        self.redraw()

    def set_view(self, t0: float, t1: float) -> None:
        t0 = max(0.0, t0)
        t1 = min(self.duration or t1, t1)
        if t1 - t0 < 0.05:
            t1 = t0 + 0.05
        self.t0, self.t1 = t0, t1
        self.redraw()
        self.notify_view()

    def fit(self) -> None:
        self.t0, self.t1 = 0.0, max(0.05, self.duration)
        self.redraw()
        self.notify_view()

    def zoom(self, factor: float, center: float | None = None) -> None:
        c = self.playhead if center is None else center
        old = max(1e-6, self.t1 - self.t0)
        span = max(0.08, min(self.duration or old, old * factor))
        a = c - (c - self.t0) * (span / old)
        self.set_view(a, a + span)

    def center_on(self, a: float, b: float, min_window: float = 0.6) -> None:
        """a..b を画面の中央に置く。窓幅は音の長さから決める。"""
        w = max(1e-3, b - a)
        span = max(w * 2.2, min_window)
        dur = self.duration or span
        span = min(span, dur)
        mid = (a + b) / 2.0
        t0 = min(max(0.0, mid - span / 2.0), max(0.0, dur - span))
        self.set_view(t0, min(dur, t0 + span))

    def focus_span(self, a: float, b: float, pad: float = 0.15) -> None:
        span = max(0.2, b - a)
        self.set_view(max(0.0, a - span * pad), min(self.duration, b + span * pad))

    def set_playhead_live(self, t: float) -> None:
        """再生中の更新用: 画面外に出たら作り直し、それ以外は座標だけ動かす。"""
        t = max(0.0, min(self.duration or t, t))
        self.playhead = t
        if t < self.t0 or t > self.t1 or not self.find_withtag("ph"):
            self.redraw()
            return
        _w, top, h = self._geo()
        x = self.t2x(t)
        self.coords("ph", x, top - 6, x, top + h)
        self.coords("ph2", x - 6, top - 13, x + 6, top - 13, x, top - 2)
        self.itemconfigure("tt", text=f"{t:6.2f}s")

    def set_playhead(self, t: float, move: bool = True) -> None:
        self.playhead = max(0.0, min(self.duration or t, t))
        if move and (self.playhead < self.t0 or self.playhead > self.t1):
            self.focus_span(self.playhead, self.playhead + (self.t1 - self.t0) * 0.9, 0.0)
        self.redraw()

    def select(self, idx: int, scroll: bool = False) -> None:
        self.sel = idx
        if scroll and 0 <= idx < len(self.marks):
            m = self.marks[idx]
            if m.start < self.t0 or m.end > self.t1:
                self.focus_span(m.start, m.end, 0.8)
        self.redraw()

    # ---------------------------------------------------------------- 変換
    def _geo(self):
        w = max(1, self.winfo_width())
        top = 22
        strip = 42 if self.marks else 0
        h = max(40, self.winfo_height() - top - strip - 12)
        return w, top, h

    def t2x(self, t: float) -> float:
        w, _top, _h = self._geo()
        return (t - self.t0) / max(1e-9, self.t1 - self.t0) * w

    def x2t(self, x: float) -> float:
        w, _top, _h = self._geo()
        return self.t0 + (x / max(1, w)) * (self.t1 - self.t0)

    # ---------------------------------------------------------------- 描画
    def redraw(self) -> None:
        self.delete("all")
        w, top, h = self._geo()
        bot = top + h

        # 句の下地（交互に少し明るく）
        for k, (a, b) in enumerate(self._phrases()):
            if k % 2:
                self.create_rectangle(self.t2x(a), top, self.t2x(b), bot,
                                      fill="#1b1617", outline="")

        self._ruler(w, top, bot)
        self.create_line(0, top + h / 2, w, top + h / 2, fill="#2a2124")

        if len(self.lo) > 1:
            xs, mins, maxs, rms = self._envelope(w)
            ms_per_px = (self.t1 - self.t0) / max(1, w) * 1000
            ymid, amp = top + h / 2, h / 2 * 0.96

            def yy(v):
                return ymid - np.sign(v) * (np.abs(v) ** COMPRESS) * amp

            if ms_per_px <= 6:          # 拡大時だけピークの「毛」を描く
                up = [(x, yy(v)) for x, v in zip(xs, maxs)]
                dn = [(x, yy(v)) for x, v in zip(xs, mins)][::-1]
                pts = [c for p in up + dn for c in p]
                if len(pts) >= 6:
                    self.create_polygon(*pts, fill="#2c2225", outline="")

            up2 = [(x, ymid - (v ** COMPRESS) * amp) for x, v in zip(xs, rms)]
            dn2 = [(x, ymid + (v ** COMPRESS) * amp) for x, v in zip(xs, rms)][::-1]
            pts2 = [c for p in up2 + dn2 for c in p]
            if len(pts2) >= 6:
                self.create_polygon(*pts2, fill="#6b2530", outline="")
            if len(up2) >= 2:
                self.create_line(*[c for p in up2 for c in p], fill="#b8556a", width=1)

        self._mora_strip(w, bot)

        zoomed = (self.t1 - self.t0) / max(1, w) * 1000 <= 6
        for i, m in enumerate(self.marks):
            for tval, is_end in ((m.start, False), (m.end, True)):
                if not is_end and i != 0:
                    continue                      # 開始線は i=0 だけ（他は前の終わりと同一）
                x = self.t2x(tval)
                if -2 <= x <= w + 2:
                    sel_line = (i == self.sel and is_end)
                    if sel_line:
                        self.create_line(x, top - 4, x, bot, fill="#ff3355", width=2)
                    elif zoomed:
                        self.create_line(x, top - 4, x, bot, fill="#4a3438", width=1)
                    else:
                        # 引いた状態では上下の目盛りだけ（全面に線を引くと縞になる）
                        self.create_line(x, top - 2, x, top + 9, fill="#5c4448")
                        self.create_line(x, bot - 9, x, bot + 2, fill="#5c4448")

        if 0 <= self.sel < len(self.marks):
            m = self.marks[self.sel]
            self.create_rectangle(self.t2x(m.start), top, self.t2x(m.end), bot,
                                  outline="#ff3355", width=1)

        x = self.t2x(self.playhead)
        if -1 <= x <= w + 1:
            self.create_line(x, top - 6, x, bot, fill="#ff6b80", width=2, tags=("ph",))
            self.create_polygon(x - 6, top - 13, x + 6, top - 13, x, top - 2,
                                fill="#ff6b80", outline="", tags=("ph2",))

    def _phrases(self) -> list[tuple[float, float]]:
        out = []
        cur = None
        for m in self.marks:
            if cur is None or m.phrase != cur[0]:
                if cur is not None:
                    out.append((cur[1], cur[2]))
                cur = (m.phrase, m.start, m.end)
            else:
                cur = (cur[0], cur[1], m.end)
        if cur is not None:
            out.append((cur[1], cur[2]))
        return out

    def _ruler(self, w: int, top: int, bot: int) -> None:
        span = self.t1 - self.t0
        step = 1.0
        for cand in (0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 60, 120):
            step = cand
            if span / cand <= 10:
                break
        t = np.floor(self.t0 / step) * step
        while t <= self.t1 + 1e-9:
            x = self.t2x(t)
            if 0 <= x <= w:
                self.create_line(x, top + 1, x, bot, fill="#241d1e")
                self.create_text(x + 3, top - 10, anchor="w", fill=T.DIM,
                                 font=("Consolas", 9), text=self._fmt(t))
            t += step

    @staticmethod
    def _fmt(t: float) -> str:
        m, s = divmod(max(0.0, t), 60)
        return f"{int(m)}:{s:05.2f}" if m else f"{s:.2f}"

    def _envelope(self, w: int):
        n = len(self.lo)
        dur = self.duration or 1.0
        i0 = max(0, min(n - 1, int(self.t0 / dur * n)))
        i1 = max(i0 + 1, min(n, int(self.t1 / dur * n)))
        cols = max(1, min(w, i1 - i0))
        edges = np.clip(np.linspace(i0, i1, cols + 1).astype(int), 0, n - 1)
        lo = np.minimum.reduceat(self.lo, edges[:-1])
        hi = np.maximum.reduceat(self.hi, edges[:-1])
        # 平均は reduceat だと最終区間が「配列の終わりまで」になってしまうので累積和で
        cum = np.concatenate(([0.0], np.cumsum(self.rms, dtype=np.float64)))
        rms = (cum[edges[1:]] - cum[edges[:-1]]) / np.maximum(1, np.diff(edges))
        # 縮小時は少し均して「音の塊」に見えるようにする
        ms_per_px = (self.t1 - self.t0) / max(1, cols) * 1000
        k = int(np.clip(round(ms_per_px / 5), 1, 15))
        if k > 1 and cols > k * 2:
            kern = np.ones(k) / k
            lo = np.convolve(lo, kern, mode="same")
            hi = np.convolve(hi, kern, mode="same")
            rms = np.convolve(rms, kern, mode="same")
        xs = np.linspace(0, w, cols)
        return xs, lo.astype(float), hi.astype(float), rms.astype(float)

    def _mora_strip(self, w: int, bot: int) -> None:
        y0 = bot + 6
        self.create_line(0, y0 - 2, w, y0 - 2, fill="#2e2326")
        for i, m in enumerate(self.marks):
            x0, x1 = self.t2x(m.start), self.t2x(m.end)
            if x1 < 0 or x0 > w:
                continue
            fill = "#7a3a26" if m.flags else "#4a3a3f"
            if i == self.sel:
                fill = T.SCARLET
            self.create_rectangle(x0, y0, max(x0 + 1, x1 - 1), y0 + 29, fill=fill, outline="")
            if i > 0:
                self.create_line(x0, y0 - 1, x0, y0 + 29, fill="#8e6f75")
            if x1 - x0 >= 15:
                self.create_text((x0 + x1) / 2, y0 + 15, text=m.mora,
                                 fill="#ffffff" if i == self.sel else T.FG,
                                 font=("Microsoft YaHei UI", 12))

    # ---------------------------------------------------------------- 操作
    def _nearest_boundary(self, x: float, tol: int = 6):
        """x に近い切点。(i, side) を返す。無ければ (-1, "end")。

        連動 ON のときは従来どおり「i の終わり」だけを見る（音は常に密着）。
        連動 OFF のときは開始・終了の両方を見て、掴んだ側だけ動かせる。
        """
        best, bi, bside = tol + 1, -1, "end"
        for i, m in enumerate(self.marks):
            cand = [(m.end, "end")]
            if not self.link:
                cand.append((m.start, "start"))
            for val, side in cand:
                d = abs(self.t2x(val) - x)
                if d < best:
                    best, bi, bside = d, i, side
        return bi, bside

    def _strip_zone(self, y: float) -> bool:
        """y が下のモーラ帯（切点をつかめる場所）かどうか。

        波形本体と帯の境目を厳密に分ける。本体側で切点をつかんでしまうと
        「押しても再生位置が動かない」という勘違いのもとになるため。
        """
        if not self.marks:
            return False
        _w, top, h = self._geo()
        bot = top + h
        return bot + 2 <= y <= bot + 40

    def _hit_tol(self) -> float:
        """切点をつかめる距離（px）。縮小して線が密なときは自動で狭くする。

        整首表示だと切点が数 px 間隔になるので、固定 7px だと帯のどこを
        押しても切点をつかんでしまい、再生位置を動かせなくなる。
        """
        tol = 7.0
        if len(self.marks) < 2:
            return tol
        w = max(1, self.winfo_width())
        widths = []
        for m in self.marks:
            x0, x1 = self.t2x(m.start), self.t2x(m.end)
            if x1 < -2 or x0 > w + 2:
                continue
            widths.append(max(0.0, x1 - x0))
        if not widths:
            return tol
        widths.sort()
        med = widths[len(widths) // 2]          # 音 1 個ぶんの中央値
        return max(2.0, min(tol, med * 0.4))

    def _on_press(self, e):
        # 波形本体をクリックしたら「再生位置を動かす」だけ。
        # 切点（箱）を動かせるのは下のモーラ帯で、しかも線の近くだけ。
        if self._strip_zone(e.y):
            idx, side = self._nearest_boundary(e.x, tol=self._hit_tol())
            if idx >= 0:
                self._drag = (idx, side)
                self.select(idx)
                if self.on_select:
                    self.on_select(idx)
                return
        t = self.x2t(e.x)
        self.set_playhead(t, move=False)
        if self.on_seek:
            self.on_seek(t)

    def _on_drag(self, e):
        if self._drag is None:
            t = self.x2t(e.x)
            self.set_playhead(t, move=False)
            if self.on_seek:
                self.on_seek(t)
            return
        i, side = self._drag
        t = self.x2t(e.x)
        if self.link:
            if i >= len(self.marks) - 1:
                return
            lo = self.marks[i].start + 0.02
            hi = self.marks[i + 1].end - 0.02
            t = max(lo, min(hi, t))
            self.marks[i].end = t
            self.marks[i + 1].start = t
        elif side == "start":
            m = self.marks[i]
            m.start = max(0.0, min(t, m.end - 0.02))
        else:
            m = self.marks[i]
            m.end = min(self.duration, max(m.start + 0.02, t))
        if self.on_boundary:
            self.on_boundary(i, t)
        self.redraw()

    def _on_release(self, _e):
        if self._drag is not None:
            idx, self._drag = self._drag[0], None
            if self.on_drag_end:
                self.on_drag_end(idx)

    def _on_dbl(self, e):
        t = self.x2t(e.x)
        self.set_playhead(t, move=False)
        if self.on_double:
            self.on_double(t)

    def _on_motion(self, e):
        if self._strip_zone(e.y):
            idx, _side = self._nearest_boundary(e.x, tol=self._hit_tol())
            self.configure(cursor="sb_h_double_arrow" if idx >= 0 else "hand2")
        else:
            self.configure(cursor="crosshair")

    def _on_wheel(self, e):
        self.zoom(0.8 if e.delta > 0 else 1.25, self.x2t(e.x))

    def _on_shift_wheel(self, e):
        span = (self.t1 - self.t0)
        d = span * 0.15 * (-1 if e.delta > 0 else 1)
        self.set_view(self.t0 + d, self.t1 + d)
