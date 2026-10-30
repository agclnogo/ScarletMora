"""音库索引ペイン（メインウィンドウに埋め込む）。

左でモーラを選ぶ → 右に候補一覧 + 波形プレビュー。ダブルクリック or 空格で試聴。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

from . import theme as T
from .core.audio import read_audio
from .core.store import read_marks
from .library import Library
from .player import make_player
from .waveview import WaveView


class BankPane(tk.Frame):
    PAD_HEAD = 0.035
    PAD_TAIL = 0.06

    def __init__(self, master, lib: Library, log=None):
        super().__init__(master, bg=T.BG0)
        self.lib = lib
        self.log = log or (lambda _m: None)
        self.rows: list[dict] = []
        self.by_mora: dict[str, list[dict]] = {}
        self.cur_mora = ""
        self.loop = False
        self.only_flag = tk.BooleanVar(value=False)
        self.only_head = tk.BooleanVar(value=False)
        self.player = make_player(self)
        self._cache: dict[str, object] = {}
        self._cur_take: dict | None = None
        self._takes: list[dict] = []
        self._build()
        self.reload()

    # ---------------------------------------------------------------- 画面
    def _build(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # --- 左: モーラ一覧
        lpanel = T.RoundPanel(self, width=226)
        lpanel.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        left = lpanel.body
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)
        tk.Label(left, text="母音 / 音", bg=T.BG1, fg=T.SCARLET_HI, font=T.FONT_B,
                 anchor="w").grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        self.v_filter = tk.StringVar()
        e = ttk.Entry(left, textvariable=self.v_filter)
        e.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
        e.bind("<KeyRelease>", lambda _ev: self.fill_mora_list())
        self.mlist = ttk.Treeview(left, columns=("n",), show="tree", selectmode="browse")
        self.mlist.column("#0", width=96)
        self.mlist.column("n", width=54, anchor="e")
        self.mlist.heading("#0", text="音")
        self.mlist.heading("n", text="条数")
        self.mlist.grid(row=2, column=0, sticky="nsew", padx=(8, 0), pady=(0, 10))
        msb = ttk.Scrollbar(left, orient="vertical", command=self.mlist.yview)
        msb.grid(row=2, column=1, sticky="ns", pady=(0, 10))
        self.mlist.configure(yscrollcommand=msb.set)
        self.mlist.bind("<<TreeviewSelect>>", self._on_mora_pick)

        # --- 右
        rpanel = T.RoundPanel(self)
        rpanel.grid(row=0, column=1, sticky="nsew")
        right = rpanel.body
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)

        bar = tk.Frame(right, bg=T.BG1)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 4))
        self.lbl_cur = tk.Label(bar, text="—", bg=T.SCARLET, fg="#ffffff",
                                font=T.FONT_HUGE, padx=18, pady=2)
        self.lbl_cur.pack(side="left")
        self.lbl_cur_sub = tk.Label(bar, text="", bg=T.BG1, fg=T.DIM, font=T.FONT)
        self.lbl_cur_sub.pack(side="left", padx=12, pady=(10, 0))

        T.ScarletButton(bar, "▶  试听", self.play_selected).pack(side="right")
        self.btn_loop = T.ScarletButton(bar, "循环", self.toggle_loop, kind="ghost")
        self.btn_loop.pack(side="right", padx=6)
        T.ScarletButton(bar, "■ 停止", self.stop, kind="ghost").pack(side="right")
        T.ScarletButton(bar, "刷新", self.reload, kind="ghost").pack(side="right", padx=6)

        opt = tk.Frame(right, bg=T.BG1)
        opt.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12)
        ttk.Checkbutton(opt, text="只看可疑（有标记）", variable=self.only_flag,
                        command=self.fill_takes).pack(side="left")
        ttk.Checkbutton(opt, text="只看句头", variable=self.only_head,
                        command=self.fill_takes).pack(side="left", padx=14)
        tk.Label(opt, text="双击一行 = 试听 · 空格 = 试听选中", bg=T.BG1,
                 fg=T.DIM, font=T.FONT).pack(side="right")

        cols = ("src", "start", "end", "dur", "conf", "pos", "flag")
        self.tbl = ttk.Treeview(right, columns=cols, show="headings")
        for c, t, w, anchor in (("src", "来源", 230, "w"), ("start", "起点", 78, "e"),
                                ("end", "终点", 78, "e"), ("dur", "时长", 66, "e"),
                                ("conf", "置信", 58, "e"), ("pos", "位置", 62, "center"),
                                ("flag", "标记", 160, "w")):
            self.tbl.heading(c, text=t)
            self.tbl.column(c, width=w, anchor=anchor, stretch=(c in ("flag", "src")))
        self.tbl.grid(row=3, column=0, sticky="nsew", padx=(12, 0), pady=(6, 8))
        tsb = ttk.Scrollbar(right, orient="vertical", command=self.tbl.yview)
        tsb.grid(row=3, column=1, sticky="ns", pady=(6, 8))
        self.tbl.configure(yscrollcommand=tsb.set)
        self.tbl.bind("<Double-Button-1>", lambda _e: self.play_selected())
        self.tbl.bind("<<TreeviewSelect>>", lambda _e: self.preview_selected())
        self.tbl.tag_configure("flag", foreground=T.WARN)

        prev = tk.Frame(right, bg=T.BG1, height=176)
        prev.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))
        prev.grid_propagate(False)
        prev.columnconfigure(0, weight=1)
        prev.rowconfigure(1, weight=1)
        self.lbl_prev = tk.Label(prev, text="波形预览", bg=T.BG1, fg=T.DIM,
                                 font=T.FONT, anchor="w")
        self.lbl_prev.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        self.wave = WaveView(prev, on_seek=self._preview_seek)
        self.wave.grid(row=1, column=0, sticky="nsew")

    # ---------------------------------------------------------------- データ
    def reload(self) -> None:
        self.rows = []
        for t in self.lib.tracks:
            name = t.get("name", "")
            p = self.lib.marks_path(name)
            if not os.path.exists(p):
                continue
            try:
                marks = read_marks(p)
            except Exception:
                continue
            for i, m in enumerate(marks):
                pos = "句头" if (i == 0 or marks[i - 1].phrase != m.phrase) else "句中"
                self.rows.append({
                    "mora": m.mora, "source": name, "start": m.start, "end": m.end,
                    "dur": m.dur, "conf": m.conf, "flags": m.flags, "pos": pos,
                })
        self.by_mora = {}
        for r in self.rows:
            self.by_mora.setdefault(r["mora"], []).append(r)
        for lst in self.by_mora.values():
            lst.sort(key=lambda r: -r["conf"])
        flagged = sum(1 for r in self.rows if r["flags"])
        self.lbl_prev.configure(
            text=f"波形预览    {len(self.rows)} 音 / {len(self.by_mora)} 种 / 可疑 {flagged}")
        self.fill_mora_list()
        if self.cur_mora in self.by_mora:
            self._select_mora(self.cur_mora)
        elif self.by_mora:
            first = sorted(self.by_mora, key=lambda k: -len(self.by_mora[k]))[0]
            self._select_mora(first)

    def fill_mora_list(self) -> None:
        q = (self.v_filter.get() or "").strip()
        self.mlist.delete(*self.mlist.get_children())
        for mora, lst in sorted(self.by_mora.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            if q and q not in mora:
                continue
            self.mlist.insert("", "end", iid=mora, text=mora, values=(len(lst),))
        if self.cur_mora and self.mlist.exists(self.cur_mora):
            self.mlist.selection_set(self.cur_mora)
            self.mlist.see(self.cur_mora)

    def _select_mora(self, mora: str) -> None:
        self.cur_mora = mora
        if self.mlist.exists(mora):
            self.mlist.selection_set(mora)
            self.mlist.see(mora)
        self.fill_takes()

    def _on_mora_pick(self, _e=None) -> None:
        sel = self.mlist.selection()
        if not sel:
            return
        if sel[0] != self.cur_mora:
            self.cur_mora = sel[0]
            self.fill_takes()

    def fill_takes(self) -> None:
        lst = self.by_mora.get(self.cur_mora, [])
        if self.only_flag.get():
            lst = [r for r in lst if r["flags"]]
        if self.only_head.get():
            lst = [r for r in lst if r["pos"] == "句头"]
        self.tbl.delete(*self.tbl.get_children())
        for i, r in enumerate(lst):
            self.tbl.insert("", "end", iid=str(i), tags=("flag",) if r["flags"] else (),
                            values=(r["source"], f"{r['start']:.3f}", f"{r['end']:.3f}",
                                    f"{r['dur'] * 1000:.0f}", f"{r['conf']:.2f}",
                                    r["pos"], r["flags"]))
        self._takes = lst
        clean = sum(1 for r in lst if not r["flags"])
        self.lbl_cur.configure(text=self.cur_mora or "—")
        self.lbl_cur_sub.configure(
            text=f"{len(lst)} 条候选 · 干净 {clean} 条" if self.cur_mora else "")
        if lst:
            self.tbl.selection_set("0")
            self.tbl.focus("0")
            self.preview_selected()

    # ---------------------------------------------------------------- 再生
    def _row(self) -> dict | None:
        sel = self.tbl.selection()
        if not sel or not self._takes:
            return None
        try:
            return self._takes[int(sel[0])]
        except (IndexError, ValueError):
            return None

    def _audio_for(self, source: str):
        if source in self._cache:
            return self._cache[source]
        t = self.lib.track(source)
        if not t:
            return None
        au = read_audio(self.lib.audio_path(t))
        if len(self._cache) > 4:
            self._cache.pop(next(iter(self._cache)))
        self._cache[source] = au
        return au

    def preview_selected(self) -> None:
        r = self._row()
        if not r:
            return
        au = self._audio_for(r["source"])
        if au is None:
            return
        self._cur_take = r
        self.wave.set_audio(au)
        tag = SimpleNamespace(start=r["start"], end=r["end"], mora=r["mora"],
                              flags=r["flags"], phrase=0)
        self.wave.set_marks([tag])
        self.wave.select(0)
        self.wave.set_view(max(0.0, r["start"] - 0.18), min(au.duration, r["end"] + 0.18))
        self.wave.set_playhead(r["start"], move=False)
        self.lbl_prev.configure(
            text=f"波形预览    {r['source']}   {r['start']:.3f}–{r['end']:.3f}s   "
                 f"{r['dur'] * 1000:.0f}ms   置信 {r['conf']:.2f}"
                 + (f"   ⚠ {r['flags']}" if r["flags"] else ""))

    def _preview_seek(self, t: float) -> None:
        r = self._cur_take
        if not r:
            return
        try:
            au = self._audio_for(r["source"])
            self.player.open(self.lib.audio_path(self.lib.track(r["source"])))
            self.player.play(t, min(au.duration, t + max(0.3, r["dur"] * 2)),
                             loop=False, on_tick=self._tick)
        except Exception as e:  # noqa: BLE001
            self.lbl_prev.configure(text=f"播放失败: {e}")

    def play_selected(self) -> None:
        r = self._row()
        if not r:
            return
        t = self.lib.track(r["source"])
        if not t:
            return
        wav = self.lib.audio_path(t)
        a = max(0.0, r["start"] - self.PAD_HEAD)
        b = r["end"] + self.PAD_TAIL
        try:
            self.player.open(wav)
            self.player.play(a, b, loop=self.loop, on_tick=self._tick)
        except Exception as e:  # noqa: BLE001
            self.lbl_prev.configure(text=f"播放失败: {e}")
            return
        self.lbl_prev.configure(
            text=f"波形预览    {r['source']}   {r['start']:.3f}–{r['end']:.3f}s   "
                 f"{r['dur'] * 1000:.0f}ms   置信 {r['conf']:.2f}"
                 + (f"   ⚠ {r['flags']}" if r["flags"] else ""))

    def _tick(self, pos: float, playing: bool) -> None:
        if self.wave.duration:
            self.wave.set_playhead_live(pos)

    def toggle_loop(self) -> None:
        self.loop = not self.loop
        bg = T.SCARLET if self.loop else T.BG2
        self.btn_loop.configure(text="循环开" if self.loop else "循环", bg=bg,
                                fg="#ffffff" if self.loop else T.FG2,
                                highlightbackground=T.SCARLET if self.loop else T.LINE2)
        self.btn_loop._bg = bg
        self.btn_loop._abg = T.SCARLET_HI if self.loop else T.SCARLET_LO

    def stop(self) -> None:
        self.player.stop()
        self.lbl_prev.configure(text="已停止")

    def shutdown(self) -> None:
        try:
            self.player.stop()
            self.player.close()
        except Exception:
            pass
