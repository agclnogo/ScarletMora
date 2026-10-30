"""拼句子ペイン（第 4 の主機能）。

左で音を検索 → 中央の候補を下の時間軸へドラッグ → 1 行ずつ並べて試聴。
結果は libraries/<音库>/edit.json に保存し、書き出しは render() と同じ形。
"""

from __future__ import annotations

import copy
import math
import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from types import SimpleNamespace

from . import theme as T
from .core import assemble as A
from .core import kana as K
from .core.audio import read_audio
from .core.store import read_marks
from .library import Library
from .player import make_player
from .waveview import WaveView

# 「拼接成歌」と同じ既定値
HEAD = 0.008
TAIL = 0.015
FADE_MS = 5.0
XFADE_MS = 8.0
TARGET_DB = -18.0
SLOT = 0.22
LINE_GAP = 0.45

RULER_H = 24
TRACK_H = 56
TRACK_GAP = 8
NAME_W = 154
MIN_SPAN = 0.05


class AssemblePane(tk.Frame):
    def __init__(self, master, lib: Library, log=None, app=None):
        super().__init__(master, bg=T.BG0)
        self.lib = lib
        self.log = log or (lambda _m: None)
        self.app = app

        self.data = lib.load_edit()
        self.tracks: list[dict] = list(self.data.get("tracks") or [])
        if not self.tracks:
            self.tracks = [self._new_track(1)]

        self.cur = 0
        self.sel: tuple[int, int] | None = None
        self.snap = True
        self.dirty = False

        self._undo: list[list[dict]] = []
        self._redo: list[list[dict]] = []
        self._save_job = None
        self._drag_item: dict | None = None
        self._drag_cand: dict | None = None
        self._insert: tuple[int, int] | None = None

        self._t0 = 0.0
        self._t1 = 1.0
        self._duration = 0.5
        self._playing = False
        self._play_t = 0.0

        self._rows: list[dict] = []
        self.by_mora: dict[str, list[dict]] = {}
        self.cur_mora = ""
        self._takes: list[dict] = []
        self._audio_cache: dict[str, object] = {}
        self._wave_src: tuple[str, object] | None = None
        self._preview_name = f"_preview_{os.getpid()}.wav"

        self.player = make_player(self)
        self._build()
        self.reload_candidates()
        self._recalc()
        self._fit()
        self.redraw()

    # ================================================================ 画面
    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=3)
        self.rowconfigure(2, weight=3)
        self.rowconfigure(3, weight=0)

        # --- 上: 操作バー
        bp = T.RoundPanel(self)
        bp.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        bar = bp.body
        bar.columnconfigure(9, weight=1)
        self.btn_preview = T.ScarletButton(bar, "▶  试听本轨", self.toggle_preview)
        self.btn_preview.grid(row=0, column=0, padx=(12, 0), pady=8, sticky="w")
        T.ScarletButton(bar, "■ 停止", self.stop_preview, kind="ghost").grid(
            row=0, column=1, padx=(6, 0), pady=8)
        T.ScarletButton(bar, "保存", self.save, kind="ghost").grid(
            row=0, column=2, padx=(18, 0), pady=8)
        T.ScarletButton(bar, "撤销", self.undo, kind="ghost").grid(
            row=0, column=3, padx=(6, 0), pady=8)
        T.ScarletButton(bar, "重做", self.redo, kind="ghost").grid(
            row=0, column=4, padx=(6, 0), pady=8)
        T.ScarletButton(bar, "＋ 轨", self.add_track, kind="ghost").grid(
            row=0, column=5, padx=(18, 0), pady=8)
        T.ScarletButton(bar, "删轨", self.delete_track, kind="ghost").grid(
            row=0, column=6, padx=(6, 0), pady=8)
        self.btn_snap = T.ScarletButton(bar, "吸附", self.toggle_snap, kind="ghost")
        self.btn_snap.grid(row=0, column=7, padx=(18, 0), pady=8)
        self.btn_snap._base_text = "吸附"
        self._paint_toggle(self.btn_snap, self.snap)
        T.ScarletButton(bar, "从歌词自动填充", self.autofill, kind="ghost").grid(
            row=0, column=8, padx=(18, 0), pady=8)
        T.ScarletButton(bar, "导出 render", self.export, kind="solid").grid(
            row=0, column=9, padx=(18, 12), pady=8, sticky="e")
        self.lbl_msg = tk.Label(bar, text="拖候选到下方轨道 · 双击加入本轨",
                                bg=T.BG1, fg=T.DIM, font=T.FONT, anchor="e")
        self.lbl_msg.grid(row=0, column=10, padx=(12, 12), pady=8, sticky="e")
        bp.update_idletasks()
        bp.fit()

        # --- 中: 音一覧 + 候補
        top = tk.Frame(self, bg=T.BG0)
        top.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        top.columnconfigure(1, weight=1)
        top.rowconfigure(0, weight=1)

        lp = T.RoundPanel(top, width=300)
        lp.grid(row=0, column=0, sticky="ns", padx=(0, 8))
        left = lp.body
        left.columnconfigure(0, weight=1)
        left.rowconfigure(3, weight=1)
        tk.Label(left, text="音 / 母音", bg=T.BG1, fg=T.SCARLET_HI, font=T.FONT_B,
                 anchor="w").grid(row=0, column=0, columnspan=2, sticky="ew",
                                  padx=10, pady=(10, 4))
        self.v_filter = tk.StringVar()
        ent = ttk.Entry(left, textvariable=self.v_filter)
        ent.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 4))
        ent.bind("<KeyRelease>", lambda _e: self.fill_mora_list())

        self.v_clean = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text="只看干净", variable=self.v_clean,
                        command=self.fill_takes).grid(row=2, column=0, columnspan=2,
                                                      sticky="w", padx=8, pady=(0, 4))

        self.mlist = ttk.Treeview(left, columns=("n", "clean"), show="tree headings",
                                  selectmode="browse")
        self.mlist.heading("#0", text="音")
        self.mlist.heading("n", text="条")
        self.mlist.heading("clean", text="干净")
        self.mlist.column("#0", width=96, anchor="w", stretch=False)
        self.mlist.column("n", width=44, anchor="e", stretch=False)
        self.mlist.column("clean", width=58, anchor="e", stretch=False)
        self.mlist.grid(row=3, column=0, sticky="nsew", padx=(8, 0), pady=(0, 10))
        msb = ttk.Scrollbar(left, orient="vertical", command=self.mlist.yview)
        msb.grid(row=3, column=1, sticky="ns", pady=(0, 10))
        self.mlist.configure(yscrollcommand=msb.set)
        self.mlist.bind("<<TreeviewSelect>>", self._on_mora_pick)

        rp = T.RoundPanel(top)
        rp.grid(row=0, column=1, sticky="nsew")
        right = rp.body
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        hb = tk.Frame(right, bg=T.BG1)
        hb.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 2))
        self.lbl_cur = tk.Label(hb, text="—", bg=T.SCARLET, fg="#ffffff",
                                font=T.FONT_T, padx=14, pady=0)
        self.lbl_cur.pack(side="left")
        self.lbl_cur_sub = tk.Label(hb, text="", bg=T.BG1, fg=T.DIM, font=T.FONT)
        self.lbl_cur_sub.pack(side="left", padx=12)
        T.ScarletButton(hb, "＋ 加到本轨", self.add_candidate, kind="ghost",
                        font=T.FONT).pack(side="right", padx=(8, 0))
        tk.Label(hb, text="双击加入 / 拖到下面轨道", bg=T.BG1, fg=T.DIM,
                 font=T.FONT).pack(side="right")

        cols = ("src", "start", "end", "dur", "conf", "pos", "flag")
        self.tbl = ttk.Treeview(right, columns=cols, show="headings", selectmode="browse")
        for c, t, w, anchor in (("src", "来源", 230, "w"), ("start", "起点", 78, "e"),
                                ("end", "终点", 78, "e"), ("dur", "时长", 66, "e"),
                                ("conf", "置信", 58, "e"), ("pos", "位置", 62, "center"),
                                ("flag", "标记", 150, "w")):
            self.tbl.heading(c, text=t)
            self.tbl.column(c, width=w, anchor=anchor, stretch=(c in ("flag", "src")))
        self.tbl.grid(row=2, column=0, sticky="nsew", padx=(12, 0), pady=(0, 12))
        tsb = ttk.Scrollbar(right, orient="vertical", command=self.tbl.yview)
        tsb.grid(row=2, column=1, sticky="ns", pady=(0, 12))
        self.tbl.configure(yscrollcommand=tsb.set)
        self.tbl.tag_configure("flag", foreground=T.WARN)
        self.tbl.bind("<Double-Button-1>", lambda _e: self.add_candidate())
        self.tbl.bind("<<TreeviewSelect>>", lambda _e: self._show_take_wave())
        self.tbl.bind("<ButtonPress-1>", self._cand_press)
        self.tbl.bind("<B1-Motion>", self._cand_motion)
        self.tbl.bind("<ButtonRelease-1>", self._cand_release)

        # --- 下: タイムライン
        tp = T.RoundPanel(self)
        tp.grid(row=2, column=0, sticky="nsew")
        tl = tp.body
        tl.columnconfigure(0, weight=1)
        tl.rowconfigure(1, weight=1)

        head = tk.Frame(tl, bg=T.BG1)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 2))
        tk.Label(head, text="时间轴", bg=T.BG1, fg=T.SCARLET_HI, font=T.FONT_B).pack(
            side="left")
        self.lbl_tracks = tk.Label(head, text="", bg=T.BG1, fg=T.DIM, font=T.FONT)
        self.lbl_tracks.pack(side="right")
        T.ScarletButton(head, "＋ 新轨", self.add_track, kind="ghost").pack(
            side="right", padx=(10, 0))
        tk.Label(head, text="点音块选中 · Delete 删除 · 双击轨名改名 · 滚轮缩放",
                 bg=T.BG1, fg=T.DIM, font=T.FONT).pack(side="right", padx=12)

        self.tl = tk.Canvas(tl, bg=T.BG1, highlightthickness=0, bd=0)
        self.tl.grid(row=1, column=0, sticky="nsew", padx=(10, 0))
        vsb = ttk.Scrollbar(tl, orient="vertical", command=self.tl.yview)
        vsb.grid(row=1, column=1, sticky="ns", padx=(0, 6))
        self.tl.configure(yscrollcommand=vsb.set)
        self.tl.bind("<Configure>", lambda _e: self.redraw())
        self.tl.bind("<Button-1>", self._tl_press)
        self.tl.bind("<B1-Motion>", self._tl_motion)
        self.tl.bind("<ButtonRelease-1>", self._tl_release)
        self.tl.bind("<Double-Button-1>", self._tl_dbl)
        self.tl.bind("<MouseWheel>", self._tl_wheel)
        self.tl.bind("<Shift-MouseWheel>", self._tl_shift_wheel)
        self.tl.bind("<Motion>", self._tl_hover)
        self.tl.bind("<Delete>", lambda _e: self.delete_selected())

        pbar = tk.Frame(tl, bg=T.BG1)
        pbar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 8))
        pbar.columnconfigure(1, weight=1)
        tk.Label(pbar, text="平移", bg=T.BG1, fg=T.DIM, font=T.FONT).grid(
            row=0, column=0, sticky="w")
        self.pan = T.PanSlider(pbar, command=self._on_pan)
        self.pan.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.lbl_pan = tk.Label(pbar, text="", bg=T.BG1, fg=T.FG2, font=T.FONT_MONO,
                                width=22, anchor="e")
        self.lbl_pan.grid(row=0, column=2, padx=(10, 0))
        tk.Label(pbar, text="缩放", bg=T.BG1, fg=T.DIM, font=T.FONT).grid(
            row=1, column=0, sticky="w", pady=(4, 0))
        self.zoom = T.PanSlider(pbar, command=self._on_zoom)
        self.zoom.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(4, 0))
        self.lbl_zoom = tk.Label(pbar, text="", bg=T.BG1, fg=T.FG2, font=T.FONT_MONO,
                                 width=22, anchor="e")
        self.lbl_zoom.grid(row=1, column=2, padx=(10, 0), pady=(4, 0))

        # --- 波形プレビュー（候補 / 选中的音块）
        wp = T.RoundPanel(self)
        wp.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        wp.configure(height=180)
        wf = wp.body
        wf.columnconfigure(0, weight=1)
        wf.rowconfigure(1, weight=1)
        self.lbl_wave = tk.Label(wf, text="波形预览：点候选或音块", bg=T.BG1, fg=T.DIM,
                                 font=T.FONT, anchor="w")
        self.lbl_wave.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))
        self.wave = WaveView(wf, on_seek=self._wave_seek)
        self.wave.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 6))
        # 候補をドラッグしてキャンバス外で離しても取りこぼさないように、
        # ルートにも release を張っておく（_drag_cand が無ければ何もしない）。
        try:
            self.winfo_toplevel().bind("<ButtonRelease-1>", self._cand_release, add="+")
        except Exception:
            pass

    @staticmethod
    def _paint_toggle(btn, on: bool) -> None:
        base = getattr(btn, "_base_text", btn.cget("text"))
        if on:
            btn.configure(bg=T.SCARLET, fg="#ffffff", text=base)
        else:
            btn.configure(bg=T.BG2, fg=T.FG2, text=base)

    # ================================================================ 候補
    def reload_candidates(self) -> None:
        """marks から候補を作り直す（切点校对で保存したら呼ばれる）。"""
        rows: list[dict] = []
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
                rows.append({
                    "mora": m.mora, "source": name, "start": m.start, "end": m.end,
                    "dur": m.dur, "conf": m.conf, "flags": m.flags or "", "pos": pos,
                })
        self._rows = rows
        by: dict[str, list[dict]] = {}
        for r in rows:
            by.setdefault(r["mora"], []).append(r)
        for lst in by.values():
            lst.sort(key=lambda r: -r["conf"])
        self.by_mora = by
        self.fill_mora_list()

    def fill_mora_list(self) -> None:
        q = (self.v_filter.get() or "").strip()
        self.mlist.delete(*self.mlist.get_children())
        for mora, lst in sorted(self.by_mora.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            if q and q not in mora:
                continue
            clean = sum(1 for r in lst if not r["flags"])
            self.mlist.insert("", "end", iid=mora, text=mora, values=(len(lst), clean))
        if self.cur_mora and self.mlist.exists(self.cur_mora):
            self.mlist.selection_set(self.cur_mora)
            self.mlist.see(self.cur_mora)
        elif self.by_mora:
            first = sorted(self.by_mora, key=lambda k: (-len(self.by_mora[k]), k))[0]
            self._select_mora(first)

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
        lst = list(self.by_mora.get(self.cur_mora, []))
        if self.v_clean.get():
            lst = [r for r in lst if not r["flags"]]
        self.tbl.delete(*self.tbl.get_children())
        for i, r in enumerate(lst):
            self.tbl.insert("", "end", iid=str(i), tags=("flag",) if r["flags"] else (),
                            values=(r["source"], f"{r['start']:.3f}", f"{r['end']:.3f}",
                                    f"{r['dur'] * 1000:.0f}", f"{r['conf']:.2f}",
                                    r["pos"], r["flags"]))
        self._takes = lst
        self.lbl_cur.configure(text=self.cur_mora or "—")
        if self.cur_mora:
            clean = sum(1 for r in self.by_mora.get(self.cur_mora, []) if not r["flags"])
            self.lbl_cur_sub.configure(
                text=f"{len(self.by_mora.get(self.cur_mora, []))} 条候选 · 干净 {clean} 条"
                     + ("（已筛）" if self.v_clean.get() else ""))
        else:
            self.lbl_cur_sub.configure(text="")
        if lst:
            self.tbl.selection_set("0")
            self.tbl.focus("0")
            self._show_take_wave()
        else:
            self._clear_wave("这个音没有候选")

    # ---------------------------------------------------------------- 波形
    def _audio_for(self, source: str):
        source = str(source or "")
        if not source:
            return None
        if source in self._audio_cache:
            return self._audio_cache[source]
        t = self.lib.track(source)
        if not t:
            return None
        try:
            au = read_audio(self.lib.audio_path(t))
        except Exception:
            return None
        if len(self._audio_cache) > 4:
            self._audio_cache.pop(next(iter(self._audio_cache)))
        self._audio_cache[source] = au
        return au

    def _clear_wave(self, msg: str = "波形预览：点候选或音块") -> None:
        self._wave_src = None
        try:
            self.wave.delete("all")
            self.wave.marks = []
        except Exception:
            pass
        self.lbl_wave.configure(text=msg)

    def _show_take_wave(self) -> None:
        r = self._cand_row()
        if not r:
            self._clear_wave()
            return
        self._show_wave_for(r["source"], r["start"], r["end"], r["mora"],
                            r.get("conf", 0.0), r.get("flags", ""))

    def _show_wave_for(self, source: str, start: float, end: float, mora: str,
                       conf: float = 0.0, flags: str = "") -> None:
        if not source or end <= start:
            self._clear_wave(f"{mora}：缺失音，没有波形")
            return
        au = self._audio_for(source)
        if au is None:
            self._clear_wave(f"{source}：音频读不到")
            return
        self._wave_src = (source, au)
        self.wave.set_audio(au)
        tag = SimpleNamespace(start=float(start), end=float(end), mora=mora or "",
                              flags=flags or "", phrase=0)
        self.wave.set_marks([tag])
        self.wave.select(0)
        pad = max(0.12, (float(end) - float(start)) * 2.0)
        self.wave.set_view(max(0.0, start - pad), min(au.duration, end + pad))
        self.wave.set_playhead(start, move=False)
        dur = (float(end) - float(start)) * 1000
        self.lbl_wave.configure(
            text=f"波形预览  {source}   {start:.3f}–{end:.3f}s   {dur:.0f}ms   "
                 f"置信 {float(conf):.2f}" + (f"   ⚠ {flags}" if flags else ""))

    def _wave_seek(self, t: float) -> None:
        if not self._wave_src:
            return
        source, au = self._wave_src
        tr = self.lib.track(source)
        if not tr:
            return
        self.stop_preview()
        self.wave.set_playhead(t, move=False)
        try:
            self.player.open(self.lib.audio_path(tr))
            self.player.play(t, min(au.duration, t + 1.0), loop=False,
                             on_tick=self._wave_tick)
        except Exception as e:  # noqa: BLE001
            self.lbl_wave.configure(text=f"播放失败: {e}")

    def _wave_tick(self, pos: float, playing: bool) -> None:
        if playing:
            self.wave.set_playhead_live(pos)
        else:
            self.wave.set_playhead(pos, move=False)

    def _cand_row(self) -> dict | None:
        sel = self.tbl.selection()
        if not sel or not self._takes:
            return None
        try:
            return self._takes[int(sel[0])]
        except (IndexError, ValueError):
            return None

    # ================================================================ 編集
    @staticmethod
    def _new_track(n: int) -> dict:
        return {"name": f"句 {n}", "text": "", "items": []}

    def _snapshot(self) -> list[dict]:
        return copy.deepcopy(self.tracks)

    def _push_undo(self) -> None:
        self._undo.append(self._snapshot())
        if len(self._undo) > 80:
            self._undo.pop(0)
        self._redo.clear()

    def _restored(self, msg: str) -> None:
        if not self.tracks:
            self.tracks = [self._new_track(1)]
        self.cur = max(0, min(self.cur, len(self.tracks) - 1))
        if self.sel is not None:
            ti, ii = self.sel
            if not (0 <= ti < len(self.tracks) and 0 <= ii < len(self.tracks[ti]["items"])):
                self.sel = None
        self._recalc()
        self.redraw()
        self._mark_dirty()
        self.set_status(msg)

    def undo(self) -> None:
        if not self._undo:
            self.set_status("没有可撤销的操作")
            return
        self._redo.append(self._snapshot())
        self.tracks = self._undo.pop()
        self._restored("已撤销")

    def redo(self) -> None:
        if not self._redo:
            self.set_status("没有可重做的操作")
            return
        self._undo.append(self._snapshot())
        self.tracks = self._redo.pop()
        self._restored("已重做")

    def _changed(self, msg: str = "") -> None:
        self._recalc()
        self.redraw()
        self._mark_dirty()
        if msg:
            self.set_status(msg)

    def add_candidate(self, cand: dict | None = None, ti: int | None = None,
                      ii: int | None = None) -> None:
        if cand is None:
            cand = self._cand_row()
        if not cand:
            return
        ti = self.cur if ti is None else max(0, min(ti, len(self.tracks) - 1))
        items = self.tracks[ti]["items"]
        ii = len(items) if ii is None else max(0, min(ii, len(items)))
        self._push_undo()
        item = {
            "mora": cand["mora"], "source": cand["source"],
            "start": round(float(cand["start"]), 3),
            "end": round(float(cand["end"]), 3),
            "kind": "ok", "sub_from": "", "lead": 0.0,
            "conf": round(float(cand["conf"]), 2),
            "flags": cand.get("flags", ""),
        }
        items.insert(ii, item)
        self.cur = ti
        self.sel = (ti, ii)
        self._changed(f"加入 {item['mora']} → 轨 {ti + 1} #{ii + 1}")

    def add_track(self) -> None:
        self._push_undo()
        self.tracks.append(self._new_track(len(self.tracks) + 1))
        self.cur = len(self.tracks) - 1
        self.sel = None
        self._recalc()
        self._fit()
        self.redraw()
        self._mark_dirty()
        self.set_status(f"新建轨 {self.cur + 1}")

    def delete_track(self) -> None:
        if len(self.tracks) <= 1:
            self.set_status("至少保留一条轨道")
            return
        tr = self.tracks[self.cur]
        if tr["items"] and not messagebox.askyesno(
                "删轨", f"轨 {self.cur + 1} 里有 {len(tr['items'])} 个音，确定删除？",
                parent=self):
            return
        self._push_undo()
        self.tracks.pop(self.cur)
        self.cur = max(0, min(self.cur, len(self.tracks) - 1))
        self.sel = None
        self._recalc()
        self.redraw()
        self._mark_dirty()
        self.set_status("已删除轨道")

    def delete_selected(self) -> None:
        if not self.sel:
            self.set_status("先点一个音块")
            return
        ti, ii = self.sel
        try:
            it = self.tracks[ti]["items"][ii]
        except (IndexError, KeyError):
            self.sel = None
            return
        self._push_undo()
        self.tracks[ti]["items"].pop(ii)
        self.sel = None
        self._changed(f"删除 {it.get('mora', '')}")

    def toggle_snap(self) -> None:
        self.snap = not self.snap
        self._paint_toggle(self.btn_snap, self.snap)
        self.set_status("吸附：开（音块紧贴，拖拽=换位）" if self.snap
                        else "吸附：关（左右拖=留空隙，最多 2 秒）")

    def step_track(self, d: int) -> None:
        if not self.tracks:
            return
        self.cur = max(0, min(self.cur + d, len(self.tracks) - 1))
        self.sel = None
        self.redraw()
        self.set_status(f"当前：轨 {self.cur + 1}")

    # ================================================================ 保存
    def set_status(self, msg: str) -> None:
        self.lbl_msg.configure(text=msg or "")

    def _mark_dirty(self) -> None:
        self.dirty = True
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.after(800, self._auto_save)

    def _auto_save(self) -> None:
        self._save_job = None
        if self.dirty:
            self.save(quiet=True)

    def save(self, quiet: bool = False) -> bool:
        try:
            self.lib.save_edit({"schema": 1, "tracks": self.tracks})
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("保存失败", str(e), parent=self)
            return False
        self.dirty = False
        if not quiet:
            self.set_status(f"已保存 edit.json · {len(self.tracks)} 轨")
        return True

    def flush(self) -> bool:
        if self._save_job is not None:
            try:
                self.after_cancel(self._save_job)
            except Exception:
                pass
            self._save_job = None
        return self.save(quiet=True) if self.dirty else True

    def can_leave(self) -> bool:
        self.stop_preview()
        return self.flush()

    def shutdown(self) -> None:
        try:
            self.flush()
        finally:
            try:
                self.player.stop()
                self.player.close()
            except Exception:
                pass

    # ================================================================ 自動填充
    def autofill(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="选择歌词文本（每行一句）",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            raw = open(path, encoding="utf-8-sig").read()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("读取失败", str(e), parent=self)
            return
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not lines:
            messagebox.showwarning("空文件", "歌词里没有内容", parent=self)
            return
        if any(t["items"] for t in self.tracks):
            if not messagebox.askyesno(
                    "自动填充", "会替换当前所有轨道（可 Ctrl+Z 撤销），继续？",
                    parent=self):
                return
        bank = A.load_bank_map(self.lib.path)
        if not bank:
            messagebox.showwarning("没有候选", "还没有切点数据，先做分析/校对", parent=self)
            return

        self._push_undo()
        tracks, missing, subs = self._tracks_from_lines(lines)
        self.tracks = tracks or [self._new_track(1)]
        self.cur = 0
        self.sel = None
        self._recalc()
        self._fit()
        self.redraw()
        self._mark_dirty()
        self.set_status(f"自动填充：{len(self.tracks)} 句 / 缺 {missing} 音 / 代用 {subs} 音")

    def _tracks_from_lines(self, lines: list[str]) -> tuple[list[dict], int, int]:
        """歌詞の行 → tracks。ダイアログを介さないのでテストもしやすい。"""
        bank = A.load_bank_map(self.lib.path)
        tracks: list[dict] = []
        missing = 0
        subs = 0
        for ln, line in enumerate(lines, 1):
            try:
                kana = K.normalize(K.to_kana(line))
                moras = [m for m in K.mora_split(kana) if m.kind != "pause"]
            except Exception:
                moras = []
            picked = A.choose_line(bank, moras, allow_sub=True)
            items: list[dict] = []
            for p in picked:
                if p.get("pick"):
                    b = p["pick"]
                    kind = p.get("kind") or "ok"
                    if kind == "sub":
                        subs += 1
                    items.append({
                        "mora": p["mora"], "source": b["source"],
                        "start": round(float(b["start"]), 3),
                        "end": round(float(b["end"]), 3),
                        "kind": kind, "sub_from": p.get("sub_from", ""),
                        "lead": 0.0, "conf": round(float(b["conf"]), 2),
                        "flags": b.get("flags", ""),
                    })
                else:
                    missing += 1
                    items.append({"mora": p["mora"], "source": "", "start": 0.0,
                                  "end": 0.0, "kind": "missing", "sub_from": "",
                                  "lead": 0.0, "conf": 0.0, "flags": ""})
            tracks.append({"name": line[:16] or f"句 {ln}", "text": line,
                           "items": items})
        return tracks, missing, subs

    # ================================================================ 試聴
    def toggle_preview(self) -> None:
        if self.player.is_playing():
            self.stop_preview()
        else:
            self.play_current()

    def play_current(self) -> None:
        self.stop_preview()
        items = list(self.tracks[self.cur]["items"])
        if not items:
            self.set_status("当前轨是空的")
            return
        try:
            path = os.path.join(self.lib.path, "render", self._preview_name)
            try:
                os.remove(path)
            except OSError:
                pass
            path, dur = self.lib.preview_track(
                items, name=self._preview_name, head=HEAD, tail=TAIL, fade_ms=FADE_MS,
                crossfade_ms=XFADE_MS, target_db=TARGET_DB, slot=SLOT)
            self.player.open(path)
            self.player.play(0.0, None, loop=False, on_tick=self._tick)
        except Exception as e:  # noqa: BLE001
            self.set_status(f"试听失败: {e}")
            self.log(f"试听失败: {e}")
            return
        self._playing = True
        self._play_t = 0.0
        self.btn_preview.configure(text="■ 停止试听")
        self.set_status(f"试听轨 {self.cur + 1} · {dur:.2f}s")
        self.redraw()

    def stop_preview(self) -> None:
        try:
            self.player.stop()
        except Exception:
            pass
        try:
            # MCI がファイルを掴んだままだと次の試聴で書き込みに失敗する
            self.player.close()
        except Exception:
            pass
        if self._playing:
            self.set_status("已停止")
        self._playing = False
        self._play_t = 0.0
        self.btn_preview.configure(text="▶  试听本轨")
        self.redraw()

    def _tick(self, pos: float, playing: bool) -> None:
        self._play_t = max(0.0, min(self._duration, pos))
        if not playing:
            self._playing = False
            self.btn_preview.configure(text="▶  试听本轨")
            try:
                self.player.close()
            except Exception:
                pass
            self.redraw()
            return
        y0 = RULER_H + self.cur * (TRACK_H + TRACK_GAP)
        x = self.t2x(self._play_t)
        if self.tl.find_withtag("ph"):
            self.tl.coords("ph", x, y0, x, y0 + TRACK_H)
        else:
            self.redraw()

    # ================================================================ 描画
    def _layout(self, track: dict) -> list[tuple[dict, float, float]]:
        out: list[tuple[dict, float, float]] = []
        cursor = 0.0
        items = track.get("items") or []
        for i, it in enumerate(items):
            if it.get("kind") == "missing":
                dur = SLOT
            else:
                dur = max(0.02, float(it.get("end") or 0) - float(it.get("start") or 0))
                dur += HEAD + TAIL
            lead = max(0.0, float(it.get("lead") or 0.0))
            if i == 0:
                start = 0.0
            elif lead > 0:
                start = cursor + lead
            else:
                start = max(0.0, cursor - XFADE_MS / 1000)
            out.append((it, start, start + dur))
            cursor = start + dur
        return out

    def _max_dur(self) -> float:
        return max([0.5] + [self._layout(t)[-1][2] for t in self.tracks if t.get("items")])

    def _recalc(self) -> None:
        self._duration = self._max_dur()
        span = max(MIN_SPAN, self._t1 - self._t0)
        if span > self._duration:
            span = self._duration
        if self._t0 + span > self._duration:
            self._t0 = max(0.0, self._duration - span)
        self._t1 = min(self._duration, self._t0 + span)
        if self._t1 - self._t0 < MIN_SPAN:
            self._t1 = min(self._duration, self._t0 + MIN_SPAN)
        self.lbl_tracks.configure(text=f"{len(self.tracks)} 轨 · {self._duration:.2f}s")
        self._sync_pan()

    def _fit(self) -> None:
        self._t0 = 0.0
        self._t1 = max(MIN_SPAN, self._duration)
        self._sync_pan()

    def _plot_w(self) -> float:
        return max(10, self.tl.winfo_width() - NAME_W - 10)

    def t2x(self, t: float) -> float:
        span = max(1e-9, self._t1 - self._t0)
        return NAME_W + (t - self._t0) / span * self._plot_w()

    def x2t(self, x: float) -> float:
        span = max(1e-9, self._t1 - self._t0)
        f = (x - NAME_W) / max(1.0, self._plot_w())
        return self._t0 + max(0.0, min(1.0, f)) * span

    def _track_at(self, cy: float) -> int | None:
        if cy < RULER_H:
            return None
        k = int((cy - RULER_H) // (TRACK_H + TRACK_GAP))
        if not (0 <= k < len(self.tracks)):
            return None
        y0 = RULER_H + k * (TRACK_H + TRACK_GAP)
        return k if y0 <= cy <= y0 + TRACK_H else None

    def _sync_pan(self) -> None:
        span = max(1e-3, self._t1 - self._t0)
        hi = max(0.0, self._duration - span)
        self.pan.set_range(hi, self._t0, enabled=True)
        self.lbl_pan.configure(text=f"{self._t0:.2f}-{self._t1:.2f}s / {self._duration:.2f}s")
        self._sync_zoom()

    def _sync_zoom(self) -> None:
        if not hasattr(self, "zoom"):
            return
        dur = max(MIN_SPAN, self._duration)
        span = max(1e-6, self._t1 - self._t0)
        if span >= dur - 1e-9:
            v = 0.0
        else:
            v = math.log(span / dur) / math.log(MIN_SPAN / dur)
        self.zoom.set_range(1.0, max(0.0, min(1.0, v)), enabled=True)
        self.lbl_zoom.configure(text=f"×{dur / span:.1f}")

    def _on_zoom(self, value: float, _frac: float) -> None:
        dur = max(MIN_SPAN, self._duration)
        v = max(0.0, min(1.0, float(value)))
        span = dur * ((MIN_SPAN / dur) ** v)
        center = self._t0 + (self._t1 - self._t0) / 2.0
        a = max(0.0, min(center - span / 2.0, max(0.0, dur - span)))
        self._t0, self._t1 = a, min(dur, a + span)
        self.redraw()

    def _on_pan(self, value: float, frac: float) -> None:
        span = self._t1 - self._t0
        if span >= self._duration - 1e-6:
            span = min(self._duration, max(MIN_SPAN, self._duration / 3.0))
            t0 = frac * self._duration - span / 2
        else:
            t0 = value
        t0 = max(0.0, min(t0, max(0.0, self._duration - span)))
        self._t0, self._t1 = t0, min(self._duration, t0 + span)
        self.redraw()

    def _zoom(self, factor: float, center_x: float) -> None:
        c = self.x2t(center_x)
        old = max(1e-6, self._t1 - self._t0)
        span = max(MIN_SPAN, min(self._duration, old * factor))
        a = c - (c - self._t0) * (span / old)
        a = max(0.0, min(a, max(0.0, self._duration - span)))
        self._t0, self._t1 = a, min(self._duration, a + span)
        self.redraw()

    def redraw(self) -> None:
        c = self.tl
        c.delete("all")
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())
        total_h = RULER_H + (len(self.tracks) + 1) * (TRACK_H + TRACK_GAP) + 12
        plot_w = self._plot_w()

        # ルーラー
        span = max(1e-9, self._t1 - self._t0)
        step = 1.0
        for cand in (0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 60, 120):
            step = cand
            if span / cand <= 10:
                break
        t = (int(self._t0 / step)) * step
        while t <= self._t1 + 1e-9:
            x = self.t2x(t)
            if NAME_W - 1 <= x <= w:
                c.create_line(x, RULER_H - 6, x, total_h, fill="#241d1e")
                c.create_text(x + 3, 6, anchor="w", fill=T.DIM,
                              font=("Consolas", 9), text=f"{t:.2f}")
            t += step

        for ti, tr in enumerate(self.tracks):
            y0 = RULER_H + ti * (TRACK_H + TRACK_GAP)
            if ti == self.cur:
                c.create_rectangle(0, y0, w, y0 + TRACK_H, fill="#241a1c", outline="")
            elif ti % 2:
                c.create_rectangle(0, y0, w, y0 + TRACK_H, fill="#1b1617", outline="")
            c.create_line(0, y0 + TRACK_H, w, y0 + TRACK_H, fill=T.LINE)

            name = str(tr.get("name") or f"句 {ti + 1}")
            if len(name) > 12:
                name = name[:11] + "…"
            c.create_text(10, y0 + TRACK_H / 2, anchor="w", text=f"{ti + 1:02d} {name}",
                          fill=T.SCARLET_HI if ti == self.cur else T.FG2, font=T.FONT)
            c.create_line(NAME_W - 8, y0, NAME_W - 8, y0 + TRACK_H, fill=T.LINE2)

            for ii, (it, a, b) in enumerate(self._layout(tr)):
                x0, x1 = self.t2x(a), self.t2x(b)
                if x1 < NAME_W or x0 > w:
                    continue
                x0 = max(NAME_W, x0)
                x1 = max(x0 + 2, min(w, x1 - 1))
                kind = it.get("kind") or "ok"
                if kind == "missing":
                    fill, fg = "#3a2a2a", T.WARN
                elif kind == "sub":
                    fill, fg = "#7a3a26", "#ffffff"
                else:
                    fill, fg = "#4a3a3f", T.FG
                selected = self.sel == (ti, ii)
                c.create_rectangle(x0, y0 + 6, x1, y0 + TRACK_H - 6, fill=fill,
                                   outline=T.SCARLET_HI if selected else T.LINE2,
                                   width=2 if selected else 1)
                if x1 - x0 >= 18:
                    label = str(it.get("mora") or "?")
                    if kind == "missing":
                        label = "缺" + label
                    c.create_text((x0 + x1) / 2, y0 + TRACK_H / 2, text=label,
                                  fill=fg, font=T.FONT_S)

        if not any(tr.get("items") for tr in self.tracks):
            c.create_text((NAME_W + w) / 2, RULER_H + TRACK_H / 2,
                          text="把上面的候选拖到这里 · 或点「从歌词自动填充」",
                          fill=T.DIM, font=T.FONT)

        # 最後の「＋ 新規トラック」行（ここに落とすと新しい軌ができる）
        add_y = RULER_H + len(self.tracks) * (TRACK_H + TRACK_GAP)
        c.create_rectangle(NAME_W + 6, add_y + 6, w - 10, add_y + TRACK_H - 6,
                           outline=T.LINE2, dash=(4, 3))
        c.create_text(NAME_W + 14, add_y + TRACK_H / 2, anchor="w",
                      text="＋ 拖到这里新建一轨", fill=T.DIM, font=T.FONT)

        if self._insert is not None:
            ti, ii = self._insert
            if 0 <= ti < len(self.tracks):
                lay = self._layout(self.tracks[ti])
                t = lay[ii][1] if ii < len(lay) else (lay[-1][2] if lay else 0.0)
                x = self.t2x(t)
                y0 = RULER_H + ti * (TRACK_H + TRACK_GAP)
                c.create_line(x, y0, x, y0 + TRACK_H, fill=T.SCARLET_HI, width=3)

        if self._playing or self._play_t > 0:
            y0 = RULER_H + self.cur * (TRACK_H + TRACK_GAP)
            x = self.t2x(self._play_t)
            if NAME_W <= x <= w:
                c.create_line(x, y0, x, y0 + TRACK_H, fill="#ff6b80", width=2,
                              tags=("ph",))
                c.create_polygon(x - 5, y0 - 1, x + 5, y0 - 1, x, y0 + 7,
                                 fill="#ff6b80", outline="")

        c.configure(scrollregion=(0, 0, w, total_h))
        self._sync_pan()

    # ================================================================ マウス
    def _tl_press(self, e) -> None:
        self.tl.focus_set()
        cy = self.tl.canvasy(e.y)
        ti = self._track_at(cy)
        if ti is None:
            add_y = RULER_H + len(self.tracks) * (TRACK_H + TRACK_GAP)
            if add_y <= cy <= add_y + TRACK_H:
                self.add_track()
            return
        self.cur = ti
        if e.x < NAME_W:
            self.sel = None
            self.redraw()
            return
        t = self.x2t(e.x)
        hit = None
        x0 = x1 = 0.0
        for ii, (it, a, b) in enumerate(self._layout(self.tracks[ti])):
            if a <= t <= b:
                hit = ii
                x0, x1 = self.t2x(a), self.t2x(b)
                break
        if hit is None:
            self.sel = None
            self.redraw()
            return
        self.sel = (ti, hit)
        it = self.tracks[ti]["items"][hit]
        mode = "move"
        if it.get("kind") != "missing":
            if min(abs(e.x - x0), abs(e.x - x1)) <= 6:
                mode = "trim_l" if abs(e.x - x0) <= abs(e.x - x1) else "trim_r"
        self._drag_item = {
            "ti": ti, "ii": hit, "x": e.x, "y": cy, "mode": mode,
            "lead": float(it.get("lead") or 0.0),
            "start0": float(it.get("start") or 0.0),
            "end0": float(it.get("end") or 0.0),
            "press_t": t,
            "snap": self._snapshot(),
        }
        self._insert = None
        self._show_wave_for(it.get("source", ""), it.get("start", 0.0),
                            it.get("end", 0.0), it.get("mora", ""),
                            it.get("conf", 0.0), it.get("flags", ""))
        self.redraw()

    def _source_dur(self, source: str) -> float:
        au = self._audio_for(source)
        return float(au.duration) if au is not None else 10.0

    def _tl_motion(self, e) -> None:
        d = self._drag_item
        if not d:
            return
        if d["mode"] in ("trim_l", "trim_r"):
            if abs(e.x - d["x"]) < 2:
                return
            it = self.tracks[d["ti"]]["items"][d["ii"]]
            dt = self.x2t(e.x) - d["press_t"]
            if d["mode"] == "trim_l":
                new = max(0.0, min(d["start0"] + dt, d["end0"] - 0.02))
                it["start"] = round(new, 3)
            else:
                hi = max(float(it.get("start") or 0.0) + 0.02,
                         self._source_dur(it.get("source", "")))
                new = max(float(it.get("start") or 0.0) + 0.02,
                          min(d["end0"] + dt, hi))
                it["end"] = round(new, 3)
            self._show_wave_for(it.get("source", ""), it.get("start", 0.0),
                                it.get("end", 0.0), it.get("mora", ""),
                                it.get("conf", 0.0), it.get("flags", ""))
            self.redraw()
            return

        dx = e.x - d["x"]
        if abs(dx) < 4 and self._insert is None:
            return
        if self.snap:
            ti = self._track_at(self.tl.canvasy(e.y))
            if ti is None:
                ti = d["ti"]
            t = self.x2t(e.x)
            lay = self._layout(self.tracks[ti])
            idx = len(lay)
            for ii, (it, a, b) in enumerate(lay):
                if t < (a + b) / 2:
                    idx = ii
                    break
            self._insert = (ti, idx)
            self.redraw()
        else:
            span = max(1e-6, self._t1 - self._t0)
            pps = self._plot_w() / span
            lead = max(0.0, min(2.0, d["lead"] + dx / max(1.0, pps)))
            self.tracks[d["ti"]]["items"][d["ii"]]["lead"] = round(lead, 3)
            self.redraw()

    def _tl_release(self, _e) -> None:
        d, self._drag_item = self._drag_item, None
        if not d:
            self._insert = None
            return
        if d["mode"] in ("trim_l", "trim_r"):
            it = self.tracks[d["ti"]]["items"][d["ii"]]
            if (abs(float(it.get("start") or 0.0) - d["start0"]) > 1e-6
                    or abs(float(it.get("end") or 0.0) - d["end0"]) > 1e-6):
                self._undo.append(d["snap"])
                self._redo.clear()
                self._changed(f"调整 {it.get('mora', '')} "
                              f"{float(it.get('start') or 0):.3f}–"
                              f"{float(it.get('end') or 0):.3f}s")
            else:
                self.redraw()
            return
        if self.snap:
            ins = self._insert
            self._insert = None
            if ins:
                ti, idx = ins
                src = d["ti"]
                same = (ti == src and idx in (d["ii"], d["ii"] + 1))
                if not same:
                    it = self.tracks[src]["items"].pop(d["ii"])
                    if src == ti and idx > d["ii"]:
                        idx -= 1
                    idx = max(0, min(idx, len(self.tracks[ti]["items"])))
                    it["lead"] = 0.0
                    self.tracks[ti]["items"].insert(idx, it)
                    self.cur = ti
                    self.sel = (ti, idx)
                    self._undo.append(d["snap"])
                    self._redo.clear()
                    self._changed(f"移动 {it.get('mora', '')} → 轨 {ti + 1} #{idx + 1}")
                    return
            self.redraw()
        else:
            cur = float(self.tracks[d["ti"]]["items"][d["ii"]].get("lead") or 0.0)
            if abs(cur - d["lead"]) > 1e-6:
                self._undo.append(d["snap"])
                self._redo.clear()
                self._changed(f"留空隙 {cur * 1000:.0f}ms")
            else:
                self.redraw()

    def _tl_hover(self, e) -> None:
        cy = self.tl.canvasy(e.y)
        ti = self._track_at(cy)
        if ti is None:
            add_y = RULER_H + len(self.tracks) * (TRACK_H + TRACK_GAP)
            cursor = "hand2" if add_y <= cy <= add_y + TRACK_H else "arrow"
        elif e.x < NAME_W:
            cursor = "hand2"
        else:
            cursor = "arrow"
            t = self.x2t(e.x)
            for ii, (it, a, b) in enumerate(self._layout(self.tracks[ti])):
                if a <= t <= b:
                    x0, x1 = self.t2x(a), self.t2x(b)
                    edge = min(abs(e.x - x0), abs(e.x - x1))
                    cursor = "sb_h_double_arrow" if edge <= 6 else "hand2"
                    break
        self.tl.configure(cursor=cursor)

    def _tl_dbl(self, e) -> None:
        cy = self.tl.canvasy(e.y)
        ti = self._track_at(cy)
        if ti is None:
            return
        if e.x < NAME_W:
            old = str(self.tracks[ti].get("name") or f"句 {ti + 1}")
            name = simpledialog.askstring("轨名", "轨道名称：", initialvalue=old,
                                          parent=self)
            if name is not None and name.strip() != old:
                self._push_undo()
                self.tracks[ti]["name"] = name.strip() or old
                self._changed("已改名")

    def _tl_wheel(self, e) -> None:
        self._zoom(0.8 if e.delta > 0 else 1.25, e.x)

    def _tl_shift_wheel(self, e) -> None:
        span = self._t1 - self._t0
        d = span * 0.15 * (-1 if e.delta > 0 else 1)
        t0 = max(0.0, min(self._t0 + d, max(0.0, self._duration - span)))
        self._t0, self._t1 = t0, t0 + span
        self.redraw()

    # --- 候補のドラッグ
    def _cand_press(self, e) -> None:
        iid = self.tbl.identify_row(e.y)
        if not iid:
            self._drag_cand = None
            return
        try:
            cand = self._takes[int(iid)]
        except (IndexError, ValueError):
            return
        self._drag_cand = {"cand": cand, "x": e.x_root, "y": e.y_root, "moved": False}

    def _cand_motion(self, e) -> None:
        d = self._drag_cand
        if not d:
            return
        if abs(e.x_root - d["x"]) + abs(e.y_root - d["y"]) > 6:
            d["moved"] = True
            self.set_status("拖到下面轨道上松手")

    def _cand_release(self, e) -> None:
        if not self.winfo_exists():
            return
        d, self._drag_cand = self._drag_cand, None
        if not d or not d.get("moved"):
            return
        cx = e.x_root - self.tl.winfo_rootx()
        cy = e.y_root - self.tl.winfo_rooty()
        if not (0 <= cx <= self.tl.winfo_width() and 0 <= cy <= self.tl.winfo_height()):
            return
        cx = self.tl.canvasx(cx)
        cy = self.tl.canvasy(cy)
        ti = self._track_at(cy)
        t = self.x2t(cx)
        if ti is None:
            add_y = RULER_H + len(self.tracks) * (TRACK_H + TRACK_GAP)
            if add_y <= cy <= add_y + TRACK_H:
                self.add_track()
                ti = len(self.tracks) - 1
                t = 0.0
            else:
                return
        lay = self._layout(self.tracks[ti])
        idx = len(lay)
        for ii, (it, a, b) in enumerate(lay):
            if t < (a + b) / 2:
                idx = ii
                break
        self.add_candidate(d["cand"], ti=ti, ii=idx)

    # ================================================================ 出力
    def export(self) -> None:
        if not any(t.get("items") for t in self.tracks):
            messagebox.showwarning("空轨道", "时间轴上还没有音", parent=self)
            return
        if not self.flush():
            return
        self.set_status("正在导出 render…")
        self.update_idletasks()
        try:
            r = self.lib.render_edit({"schema": 1, "tracks": self.tracks}, dst="render")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("导出失败", str(e), parent=self)
            self.set_status("导出失败")
            return
        msg = (f"导出完成：{r['lines']} 句 / {r['segments']} 音 / "
               f"{r['duration']:.1f}s")
        self.set_status(msg)
        self.log(msg + " → " + r["dir"])
        if r.get("missing"):
            self.log("  缺失: " + ", ".join(f"{k} x{v}" for k, v in r["missing"].items()))
        if r.get("subs"):
            self.log("  代用: " + ", ".join(f"{k} x{v}" for k, v in r["subs"].items()))
        if self.app is not None:
            try:
                self.app.open_folder(r["dir"])
            except Exception:
                pass
