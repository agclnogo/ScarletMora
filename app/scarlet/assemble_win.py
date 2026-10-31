"""人力试作（旧「拼句子」）ペイン。

左で音を検索 → 中央の候補を試聴 / 下の時間軸へドラッグ → 1 行ずつ並べて試聴。
結果は libraries/<音库>/edit.json に保存し、書き出しは render() と同じ形。

このタブの設計メモ:
  * 時間軸は「一番下が第一句、上へ行くほど新しい句」。内部の tracks は
    時系列（0 = 第一句）のまま。表示のときだけ上下を反転する。
  * 視口（_t0 / _t1）は内容の長さと切り離してある。音を足しても自動 fit しない。
  * 音块は item["at"]（秒）を唯一の位置情報として持つ。lead は旧データ互換。
  * 试听 / 总波形 / 导出 はすべて core.assemble.render_items を通す。
"""

from __future__ import annotations

import copy
import os
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from types import SimpleNamespace

import numpy as np

from . import theme as T
from .core import assemble as A
from .core import kana as K
from .core.audio import read_audio, write_wav
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

# 画面
TRACK_PAD = 4
RULER_H = 22
TRACK_H = 58
TRACK_GAP = 8
WAVE_H = 80
NAME_W = 196
MIN_SPAN = 0.05
MAX_SPAN = 600.0
EDGE_PX = 7


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
        self.snap = False
        self.dirty = False

        self._undo: list[list[dict]] = []
        self._redo: list[list[dict]] = []
        self._save_job = None
        self._drag_item: dict | None = None
        self._drag_cand: dict | None = None
        self._insert: tuple[int, int] | None = None
        self._ghost: dict | None = None
        self._scrub = False
        self._pause_playhead_follow = False

        # 視口は内容と独立（自動 fit しない）
        self._t0 = 0.0
        self._t1 = 2.0
        self._content_dur = 0.0
        self._playhead = 0.0

        # 総波形（mix）
        self._mix_arr: np.ndarray | None = None
        self._mix_sr = 44100
        self._mix_dur = 0.0
        self._mix_dirty = True
        self._mix_job = None

        # 再生
        self._playing = False
        self._play_kind = ""
        self._play_base = 0.0
        self._playing_item: tuple[int, int] | None = None
        self._cand_playing_key: tuple | None = None
        self._preview_seq = 0
        self._preview_path = ""
        self._cand_job = None
        self._lyric_draft = ""

        self._rows: list[dict] = []
        self.by_mora: dict[str, list[dict]] = {}
        self.cur_mora = ""
        self._takes: list[dict] = []
        self._audio_cache: dict[str, object] = {}
        self._wave_src: tuple[str, object] | None = None

        self.player = make_player(self)
        self._ensure_positions()
        self._build()
        self.reload_candidates()
        self._recalc()
        self._fit()
        self._rebuild_mix()
        self.redraw()

    # ================================================================ 画面
    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # --- 上: 操作バー
        bp = T.RoundPanel(self)
        bp.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        bar = bp.body
        bar.columnconfigure(7, weight=1)
        self.btn_preview = T.ScarletButton(bar, "▶  试听", self.toggle_preview)
        self.btn_preview.grid(row=0, column=0, padx=(12, 0), pady=8, sticky="w")
        T.ScarletButton(bar, "■ 停止", self.stop_preview_now, kind="ghost").grid(
            row=0, column=1, padx=(6, 0), pady=8)
        T.ScarletButton(bar, "保存", self.save, kind="ghost").grid(
            row=0, column=2, padx=(18, 0), pady=8)
        T.ScarletButton(bar, "撤销", self.undo, kind="ghost").grid(
            row=0, column=3, padx=(6, 0), pady=8)
        T.ScarletButton(bar, "重做", self.redo, kind="ghost").grid(
            row=0, column=4, padx=(6, 0), pady=8)
        T.ScarletButton(bar, "从歌词自动填充", self.autofill, kind="ghost").grid(
            row=0, column=5, padx=(18, 0), pady=8)
        T.ScarletButton(bar, "导出 render", self.export, kind="solid").grid(
            row=0, column=6, padx=(18, 0), pady=8)
        self.lbl_msg = tk.Label(bar, text="点候选试听 · ＋ 加到本轨或拖入轨道",
                                bg=T.BG1, fg=T.DIM, font=T.FONT, anchor="e")
        self.lbl_msg.grid(row=0, column=7, padx=(12, 12), pady=8, sticky="e")
        bp.update_idletasks()
        bp.fit()

        # --- 上下分割: 仕切りをドラッグすると時間軸の上端を上へ引ける
        split = tk.PanedWindow(self, orient="vertical", bg=T.LINE2, bd=0,
                               sashwidth=9, sashrelief="flat", showhandle=False,
                               opaqueresize=True)
        split.grid(row=1, column=0, sticky="nsew")
        self.split = split

        # --- 中: 音一覧 + 候補
        top = tk.Frame(split, bg=T.BG0)
        top.columnconfigure(1, weight=1)
        top.rowconfigure(0, weight=1)
        split.add(top, minsize=190, stretch="always")

        lp = T.RoundPanel(top, width=292)
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
        self.mlist.heading("#0", text="音（罗马音）")
        self.mlist.heading("n", text="条")
        self.mlist.heading("clean", text="干净")
        self.mlist.column("#0", width=118, anchor="w", stretch=False)
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
        T.ScarletButton(hb, "＋ 加到本轨", self.add_candidate, kind="ghost",
                        font=T.FONT).pack(side="left", padx=(0, 10))
        self.lbl_cur = tk.Label(hb, text="—", bg=T.SCARLET, fg="#ffffff",
                                font=T.FONT_T, padx=14, pady=0)
        self.lbl_cur.pack(side="left")
        self.lbl_cur_sub = tk.Label(hb, text="", bg=T.BG1, fg=T.DIM, font=T.FONT)
        self.lbl_cur_sub.pack(side="left", padx=12)
        tk.Label(hb, text="单击试听 / 加到本轨或拖入轨道", bg=T.BG1, fg=T.DIM,
                 font=T.FONT).pack(side="right")

        # 来源波形の小プレビュー（総波形とは別。上の小窓に置く）
        sf = tk.Frame(right, bg=T.BG1)
        sf.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(2, 6))
        sf.columnconfigure(0, weight=1)
        sf.rowconfigure(1, weight=1)
        self.lbl_wave = tk.Label(sf, text="来源波形：点候选或音块", bg=T.BG1, fg=T.DIM,
                                 font=T.FONT_S, anchor="w")
        self.lbl_wave.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        self.wave = WaveView(sf, on_seek=self._wave_seek, height=74)
        self.wave.grid(row=1, column=0, sticky="ew")
        sf.rowconfigure(1, minsize=74)

        cols = ("src", "start", "end", "dur", "conf", "pos", "flag")
        self.tbl = ttk.Treeview(right, columns=cols, show="headings", selectmode="browse")
        for c, t, w, anchor in (("src", "来源", 230, "w"), ("start", "起点", 78, "e"),
                                ("end", "终点", 78, "e"), ("dur", "时长", 66, "e"),
                                ("conf", "置信", 58, "e"), ("pos", "位置", 62, "center"),
                                ("flag", "标记", 150, "w")):
            self.tbl.heading(c, text=t)
            self.tbl.column(c, width=w, anchor=anchor, stretch=(c in ("flag", "src")))
        self.tbl.grid(row=2, column=0, sticky="nsew", padx=(12, 0), pady=(0, 10))
        tsb = ttk.Scrollbar(right, orient="vertical", command=self.tbl.yview)
        tsb.grid(row=2, column=1, sticky="ns", pady=(0, 10))
        self.tbl.configure(yscrollcommand=tsb.set)
        self.tbl.tag_configure("flag", foreground=T.WARN)
        self.tbl.tag_configure("playing", background=T.SCARLET_DEEP, foreground="#ffffff")
        self.tbl.bind("<<TreeviewSelect>>", lambda _e: self._show_take_wave())
        self.tbl.bind("<ButtonPress-1>", self._cand_press)
        self.tbl.bind("<B1-Motion>", self._cand_motion)
        self.tbl.bind("<ButtonRelease-1>", self._cand_release)

        # --- 下: タイムライン
        tp = T.RoundPanel(split)
        split.add(tp, minsize=260, stretch="always")
        tl = tp.body
        tl.columnconfigure(0, weight=1)
        tl.rowconfigure(2, weight=1)

        head = tk.Frame(tl, bg=T.BG1)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 2))
        T.ScarletButton(head, "＋ 新轨", self.add_track, kind="ghost").pack(
            side="left", padx=(0, 6))
        T.ScarletButton(head, "删轨", self.delete_track, kind="ghost").pack(
            side="left")
        self.btn_snap = T.ScarletButton(head, "吸附", self.toggle_snap, kind="ghost")
        self.btn_snap.pack(side="left", padx=(6, 0))
        self.btn_snap._base_text = "吸附"
        self._paint_toggle(self.btn_snap, self.snap)
        self.lbl_tracks = tk.Label(head, text="", bg=T.BG1, fg=T.DIM, font=T.FONT)
        self.lbl_tracks.pack(side="right")
        self.lbl_time = tk.Label(head, text="", bg=T.BG1, fg=T.FG2, font=T.FONT_MONO)
        self.lbl_time.pack(side="right", padx=12)
        tk.Label(head, text="Ctrl+滚轮缩放 · 点空白定位 · 点音块试听 · 拖边缘裁剪",
                 bg=T.BG1, fg=T.DIM, font=T.FONT_S).pack(side="right", padx=10)

        # ルーラーは固定（縦スクロールで消えない）
        self.ruler = tk.Canvas(tl, bg=T.BG1, height=RULER_H, highlightthickness=0,
                               bd=0, takefocus=1)
        self.ruler.grid(row=1, column=0, sticky="ew", padx=(10, 0))

        # トラック本体だけが縦スクロールする
        self.tl = tk.Canvas(tl, bg=T.BG1, highlightthickness=0, bd=0, takefocus=1,
                            yscrollincrement=24)
        self.tl.grid(row=2, column=0, sticky="nsew", padx=(10, 0))
        vsb = ttk.Scrollbar(tl, orient="vertical", command=self.tl.yview)
        vsb.grid(row=2, column=1, sticky="ns", padx=(0, 6))
        self.tl.configure(yscrollcommand=vsb.set)
        self.tl.bind("<Configure>", lambda _e: self.redraw())
        self.tl.bind("<Button-1>", self._tl_press)
        self.tl.bind("<B1-Motion>", self._tl_motion)
        self.tl.bind("<ButtonRelease-1>", self._tl_release)
        self.tl.bind("<Double-Button-1>", self._tl_dbl)
        self.tl.bind("<MouseWheel>", self._tl_wheel)
        self.tl.bind("<Control-MouseWheel>", self._tl_wheel)
        self.tl.bind("<Shift-MouseWheel>", self._tl_shift_wheel)
        self.tl.bind("<Motion>", self._tl_hover)
        self.tl.bind("<Delete>", lambda _e: self.delete_selected())
        self.tl.bind("<Control-z>", lambda _e: self.undo())
        self.tl.bind("<Control-y>", lambda _e: self.redo())

        # 総波形も固定（縦スクロールで消えない）
        self.mix = tk.Canvas(tl, bg="#141011", height=WAVE_H, highlightthickness=0,
                             bd=0, takefocus=1)
        self.mix.grid(row=3, column=0, sticky="ew", padx=(10, 0))

        for c in (self.ruler, self.mix):
            c.bind("<Button-1>", self._seek_press)
            c.bind("<B1-Motion>", self._seek_motion)
            c.bind("<ButtonRelease-1>", self._seek_release)
            c.bind("<MouseWheel>", self._tl_wheel)
            c.bind("<Control-MouseWheel>", self._tl_wheel)
            c.bind("<Shift-MouseWheel>", self._tl_shift_wheel)
            c.bind("<Configure>", lambda _e: self.redraw())
            c.bind("<Motion>", lambda _e, cc=c: cc.configure(cursor="crosshair"))
        self.ruler.bind("<Double-Button-1>", self._seek_press)
        self.mix.bind("<Double-Button-1>", self._seek_press)

        # 平移バー: 文字ラベルを置かず、スライダーと右の範囲表示だけ
        pbar = tk.Frame(tl, bg=T.BG1)
        pbar.grid(row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 8))
        pbar.columnconfigure(0, weight=1)
        self.pan = T.PanSlider(pbar, command=self._on_pan)
        self.pan.grid(row=0, column=0, sticky="ew")
        self.lbl_pan = tk.Label(pbar, text="", bg=T.BG1, fg=T.FG2, font=T.FONT_MONO,
                                width=26, anchor="e")
        self.lbl_pan.grid(row=0, column=1, padx=(10, 0))

        # 候補をキャンバス外で離しても取りこぼさないようにルートにも張る
        try:
            self.winfo_toplevel().bind("<ButtonRelease-1>", self._cand_release, add="+")
        except Exception:
            pass
        self.after(120, self._init_sash)

    def _init_sash(self) -> None:
        try:
            if not self.winfo_exists():
                return
            h = self.split.winfo_height()
            if h > 320:
                self.split.sash_place(0, 0, int(h * 0.40))
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
        q = (self.v_filter.get() or "").strip().lower()
        self.mlist.delete(*self.mlist.get_children())
        for mora, lst in sorted(self.by_mora.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            label = K.mora_label(mora)
            if q and q not in mora.lower() and q not in label.lower():
                continue
            clean = sum(1 for r in lst if not r["flags"])
            self.mlist.insert("", "end", iid=mora, text=label, values=(len(lst), clean))
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
            tags = ("flag",) if r["flags"] else ()
            self.tbl.insert("", "end", iid=str(i), tags=tags,
                            values=(r["source"], f"{r['start']:.3f}", f"{r['end']:.3f}",
                                    f"{r['dur'] * 1000:.0f}", f"{r['conf']:.2f}",
                                    r["pos"], r["flags"]))
        self._takes = lst
        self.lbl_cur.configure(text=K.mora_label(self.cur_mora) if self.cur_mora else "—")
        if self.cur_mora:
            clean = sum(1 for r in self.by_mora.get(self.cur_mora, []) if not r["flags"])
            self.lbl_cur_sub.configure(
                text=f"{len(self.by_mora.get(self.cur_mora, []))} 条候选 · 干净 {clean} 条"
                     + ("（已筛）" if self.v_clean.get() else ""))
        else:
            self.lbl_cur_sub.configure(text="")
        self._paint_cand_playing(None)
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

    def _clear_wave(self, msg: str = "来源波形：点候选或音块") -> None:
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
            text=f"来源波形  {source}   {start:.3f}–{end:.3f}s   {dur:.0f}ms   "
                 f"置信 {float(conf):.2f}" + (f"   ⚠ {flags}" if flags else ""))

    def _wave_seek(self, t: float) -> None:
        if not self._wave_src:
            return
        source, au = self._wave_src
        tr = self.lib.track(source)
        if not tr:
            return
        self.stop_preview(quiet=True)
        self.wave.set_playhead(t, move=False)
        try:
            self.player.open(self.lib.audio_path(tr))
            self.player.play(t, min(au.duration, t + 1.0), loop=False,
                             on_tick=self._tick_audio)
        except Exception as e:  # noqa: BLE001
            self.lbl_wave.configure(text=f"播放失败: {e}")
            return
        self._playing = True
        self._play_kind = "source"
        self._play_base = 0.0
        self.btn_preview.configure(text="■ 停止试听")
        self.set_status(f"试听来源 {source} @ {t:.2f}s")

    def _cand_row(self) -> dict | None:
        sel = self.tbl.selection()
        if not sel or not self._takes:
            return None
        try:
            return self._takes[int(sel[0])]
        except (IndexError, ValueError):
            return None

    # ================================================================ データ
    @staticmethod
    def _new_track(n: int) -> dict:
        return {"name": f"句 {n}", "text": "", "items": [], "solo": False, "mute": False}

    def _item_dur(self, it: dict) -> float:
        if (it or {}).get("kind") == "missing":
            return SLOT
        try:
            a = float(it.get("start") or 0.0)
            b = float(it.get("end") or 0.0)
        except (TypeError, ValueError):
            return SLOT
        return max(0.02, b - a) + HEAD + TAIL

    def _ensure_positions(self) -> None:
        """旧 edit.json（at が無い）にタイムライン位置を補う。"""
        for tr in self.tracks:
            items = tr.get("items") or []
            cursor = 0.0
            for i, it in enumerate(items):
                dur = self._item_dur(it)
                at = it.get("at", None)
                try:
                    at = None if at is None or at == "" else float(at)
                except (TypeError, ValueError):
                    at = None
                if at is None:
                    lead = max(0.0, float(it.get("lead") or 0.0))
                    if i == 0:
                        at = 0.0
                    elif lead > 0:
                        at = cursor + lead
                    else:
                        at = max(0.0, cursor - XFADE_MS / 1000.0)
                    it["at"] = round(at, 3)
                cursor = max(cursor, at + dur)

    def _reflow(self, track: dict) -> None:
        """吸附モード: 音块を隙間なく並べ直す（at を振り直す）。"""
        cursor = 0.0
        for i, it in enumerate(track.get("items") or []):
            at = 0.0 if i == 0 else max(0.0, cursor - XFADE_MS / 1000.0)
            it["at"] = round(at, 3)
            it["lead"] = 0.0
            cursor = at + self._item_dur(it)

    def _layout(self, track: dict) -> list[tuple[dict, float, float]]:
        out: list[tuple[dict, float, float]] = []
        for it in track.get("items") or []:
            try:
                a = float(it.get("at") or 0.0)
            except (TypeError, ValueError):
                a = 0.0
            out.append((it, a, a + self._item_dur(it)))
        return out

    def _track_end(self, track: dict) -> float:
        lay = self._layout(track)
        return lay[-1][2] if lay else 0.0

    def _content_duration(self) -> float:
        m = 0.0
        for tr in self.tracks:
            m = max(m, self._track_end(tr))
        return m

    def _snapshot(self) -> list[dict]:
        return copy.deepcopy(self.tracks)

    def _push_undo(self) -> None:
        self._undo.append(self._snapshot())
        if len(self._undo) > 80:
            self._undo.pop(0)
        self._redo.clear()

    def _restored(self, msg: str) -> None:
        if self._playing:
            self.stop_preview(quiet=True)
        if not self.tracks:
            self.tracks = [self._new_track(1)]
        self._ensure_positions()
        self.cur = max(0, min(self.cur, len(self.tracks) - 1))
        if self.sel is not None:
            ti, ii = self.sel
            if not (0 <= ti < len(self.tracks) and 0 <= ii < len(self.tracks[ti]["items"])):
                self.sel = None
        self._playing_item = None
        self._recalc()
        self._mark_mix_dirty()
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
        self._ensure_positions()
        self._recalc()
        self._mark_mix_dirty()
        self.redraw()
        self._mark_dirty()
        if msg:
            self.set_status(msg)

    # ---------------------------------------------------------------- 追加
    @staticmethod
    def _item_from_cand(cand: dict, at: float = 0.0) -> dict:
        return {
            "mora": cand["mora"], "source": cand["source"],
            "start": round(float(cand["start"]), 3),
            "end": round(float(cand["end"]), 3),
            "kind": "ok", "sub_from": "", "lead": 0.0,
            "conf": round(float(cand["conf"]), 2),
            "flags": cand.get("flags", ""),
            "at": round(max(0.0, float(at)), 3),
        }

    def _insert_index(self, track: dict, t: float) -> int:
        lay = self._layout(track)
        for ii, (_it, a, b) in enumerate(lay):
            if t < (a + b) / 2.0:
                return ii
        return len(lay)

    def add_candidate(self, cand: dict | None = None, ti: int | None = None,
                      ii: int | None = None, at: float | None = None) -> None:
        if cand is None:
            cand = self._cand_row()
        if not cand:
            self.set_status("先选一个候选")
            return
        self.stop_preview(quiet=True)
        ti = self.cur if ti is None else max(0, min(ti, len(self.tracks) - 1))
        self._push_undo()
        track = self.tracks[ti]
        items = track["items"]
        if self.snap:
            pos = len(items) if ii is None else max(0, min(ii, len(items)))
            item = self._item_from_cand(cand, at=0.0)
            items.insert(pos, item)
            self._reflow(track)
        else:
            if at is not None:
                free_at = max(0.0, at)
            elif items:
                free_at = self._track_end(track) + 0.02
            else:
                free_at = 0.0
            item = self._item_from_cand(cand, at=free_at)
            pos = self._insert_index(track, free_at)
            items.insert(pos, item)
        self.cur = ti
        self.sel = (ti, pos)
        self._playing_item = None
        self._changed(f"加入 {item['mora']} → 轨 {ti + 1} #{pos + 1}")
        self._see_track(ti)

    def add_track(self) -> None:
        self._push_undo()
        self.tracks.append(self._new_track(len(self.tracks) + 1))
        self.cur = len(self.tracks) - 1
        self.sel = None
        self._recalc()
        self.redraw()
        self._mark_dirty()
        self.tl.yview_moveto(0.0)
        self.set_status(f"新建轨 {self.cur + 1}（最新句在最上面）")

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
        self._playing_item = None
        self._recalc()
        self._mark_mix_dirty()
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
        self._playing_item = None
        self._changed(f"删除 {it.get('mora', '')}")

    def toggle_snap(self) -> None:
        self.snap = not self.snap
        self._paint_toggle(self.btn_snap, self.snap)
        self.set_status("吸附：开（拖音块 = 排序 / 紧贴）" if self.snap
                        else "吸附：关（拖音块 = 自由水平移动，可留空隙 / 重叠）")

    def toggle_solo(self, ti: int) -> None:
        if not (0 <= ti < len(self.tracks)):
            return
        self._push_undo()
        self.tracks[ti]["solo"] = not bool(self.tracks[ti].get("solo"))
        self.stop_preview(quiet=True)
        self._mark_mix_dirty()
        self._recalc()
        self.redraw()
        self._mark_dirty()
        self.set_status(("Solo 开" if self.tracks[ti]["solo"] else "Solo 关")
                        + f" · 轨 {ti + 1}")

    def toggle_mute(self, ti: int) -> None:
        if not (0 <= ti < len(self.tracks)):
            return
        self._push_undo()
        self.tracks[ti]["mute"] = not bool(self.tracks[ti].get("mute"))
        self.stop_preview(quiet=True)
        self._mark_mix_dirty()
        self._recalc()
        self.redraw()
        self._mark_dirty()
        self.set_status(("Mute 开" if self.tracks[ti]["mute"] else "Mute 关")
                        + f" · 轨 {ti + 1}")

    def step_track(self, d: int) -> None:
        if not self.tracks:
            return
        self.cur = max(0, min(self.cur + d, len(self.tracks) - 1))
        self.sel = None
        self.redraw()
        self._see_track(self.cur)
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
            self.lib.save_edit({"schema": 2, "tracks": self.tracks})
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
        self.stop_preview(quiet=True)
        return self.flush()

    def shutdown(self) -> None:
        try:
            self.flush()
        finally:
            self._stop_player()

    # ================================================================ 自動填充
    def autofill(self) -> None:
        lines = self._ask_lyrics()
        if not lines:
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
        self._ensure_positions()
        self._recalc()
        self._mark_mix_dirty()
        self.redraw()
        self._mark_dirty()
        self.set_status(f"自动填充：{len(self.tracks)} 句 / 缺 {missing} 音 / "
                        f"代用 {subs} 音 · 可 Ctrl+Z 撤销")

    def _ask_lyrics(self) -> list[str] | None:
        """歌詞入力の小窓。1 行 = 1 句、假名 / ローマ字のみ。"""
        win = tk.Toplevel(self)
        win.title("从歌词自动填充")
        win.configure(bg=T.BG0)
        win.transient(self.winfo_toplevel())
        win.resizable(True, True)
        win.geometry("800x500")
        win.minsize(640, 400)
        try:
            T.dark_titlebar(win)
        except Exception:
            pass

        body = tk.Frame(win, bg=T.BG0)
        body.pack(fill="both", expand=True, padx=16, pady=14)

        head = tk.Frame(body, bg=T.BG0)
        head.pack(fill="x")
        tk.Label(head, text="歌词（一行一句）", bg=T.BG0, fg=T.SCARLET_HI,
                 font=T.FONT_T, anchor="w").pack(side="left")
        lbl_count = tk.Label(head, text="", bg=T.BG0, fg=T.DIM, font=T.FONT_S)
        lbl_count.pack(side="right")

        tk.Label(body, text="只支持假名 / 罗马音（汉字请先换成假名）",
                 bg=T.BG0, fg=T.DIM, font=T.FONT_S, anchor="w").pack(
            fill="x", pady=(4, 0))
        tk.Label(body, text="格式例：a i shi te ru / あいしてる"
                            "　罗马音可空格，也可连写 aishiteru；斜杠两边写一种即可",
                 bg=T.BG0, fg=T.DIM, font=T.FONT_S, anchor="w").pack(
            fill="x", pady=(0, 8))

        tf = tk.Frame(body, bg=T.BG1)
        tf.pack(fill="both", expand=True)
        tf.columnconfigure(0, weight=1)
        tf.rowconfigure(0, weight=1)
        txt = tk.Text(tf, bg=T.BG2, fg=T.FG, insertbackground=T.FG, font=T.FONT,
                      height=12, relief="flat", highlightthickness=1,
                      highlightbackground=T.LINE2, highlightcolor=T.SCARLET,
                      wrap="word", undo=True)
        txt.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tf, orient="vertical", command=txt.yview)
        sb.grid(row=0, column=1, sticky="ns")
        txt.configure(yscrollcommand=sb.set)
        if self._lyric_draft:
            txt.insert("1.0", self._lyric_draft)

        bottom = tk.Frame(body, bg=T.BG0)
        bottom.pack(fill="x", pady=(8, 0))
        lbl_info = tk.Label(bottom, text="", bg=T.BG0, fg=T.DIM, font=T.FONT_S,
                            anchor="w", justify="left", wraplength=430)
        lbl_info.pack(side="left", fill="x", expand=True)

        result: dict[str, list[str] | None] = {"lines": None}

        def read_lines() -> list[str]:
            raw = txt.get("1.0", "end-1c")
            return [ln.strip() for ln in raw.splitlines() if ln.strip()]

        def refresh(_e=None) -> None:
            try:
                txt.edit_modified(False)
            except Exception:
                pass
            lines = read_lines()
            bad: list[tuple[int, str]] = []
            total = 0
            for i, ln in enumerate(lines, 1):
                prob = K.lyric_problem(ln)
                if prob:
                    bad.append((i, prob))
                    continue
                try:
                    total += len(K.lyric_to_moras(ln))
                except Exception:
                    pass
            lbl_count.configure(text=f"{len(lines)} 行 · {total} 音")
            if bad:
                i, prob = bad[0]
                more = f"｜另有 {len(bad) - 1} 行" if len(bad) > 1 else ""
                lbl_info.configure(text=f"第 {i} 行：{prob}{more}", fg=T.BAD)
            elif lines:
                lbl_info.configure(text="格式 OK · 空行自动忽略", fg=T.DIM)
            else:
                lbl_info.configure(text="", fg=T.DIM)

        def cancel() -> None:
            win.destroy()

        def ok() -> None:
            lines = read_lines()
            if not lines:
                messagebox.showwarning("没有歌词", "请输入至少一行歌词", parent=win)
                return
            bad: list[str] = []
            for i, ln in enumerate(lines, 1):
                prob = K.lyric_problem(ln)
                if prob:
                    bad.append(f"第 {i} 行：{prob}")
            if bad:
                more = f"\n… 还有 {len(bad) - 6} 行" if len(bad) > 6 else ""
                messagebox.showwarning(
                    "格式不对",
                    "只支持假名 / 罗马音（汉字请先换成假名）。\n"
                    + "\n".join(bad[:6]) + more, parent=win)
                return
            result["lines"] = lines
            self._lyric_draft = txt.get("1.0", "end-1c")
            win.destroy()

        def insert_example() -> None:
            sample = "a i shi te ru / あいしてる"
            raw = txt.get("1.0", "end-1c")
            if raw.strip() and not raw.endswith("\n"):
                txt.insert("end", "\n")
            txt.insert("end", sample)
            txt.focus_set()
            refresh()

        T.ScarletButton(bottom, "填充到轨道", ok, kind="solid").pack(side="right")
        T.ScarletButton(bottom, "取消", cancel, kind="ghost").pack(
            side="right", padx=(0, 8))
        T.ScarletButton(bottom, "填入示例", insert_example, kind="ghost",
                        font=T.FONT_S).pack(side="right", padx=(0, 8))

        txt.bind("<<Modified>>", refresh)
        try:
            txt.edit_modified(False)
        except Exception:
            pass
        refresh()
        win.bind("<Escape>", lambda _e: cancel())
        win.bind("<Control-Return>", lambda _e: ok())
        win.protocol("WM_DELETE_WINDOW", cancel)
        txt.focus_set()
        win.update_idletasks()
        try:
            pw = self.winfo_toplevel()
            x = pw.winfo_rootx() + (pw.winfo_width() - win.winfo_reqwidth()) // 2
            y = pw.winfo_rooty() + (pw.winfo_height() - win.winfo_reqheight()) // 2
            win.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass
        win.grab_set()
        win.wait_window()
        return result["lines"]

    def _tracks_from_lines(self, lines: list[str]) -> tuple[list[dict], int, int]:
        """歌詞の行 → tracks。ダイアログを介さないのでテストもしやすい。"""
        bank = A.load_bank_map(self.lib.path)
        tracks: list[dict] = []
        missing = 0
        subs = 0
        for ln, line in enumerate(lines, 1):
            try:
                # ローマ字 / 仮名（/ で両方書いてあれば仮名側）をモーラに
                moras = K.lyric_to_moras(line)
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
                           "items": items, "solo": False, "mute": False})
        return tracks, missing, subs

    # ================================================================ 再生
    def _audible_indices(self) -> list[int]:
        solo = [i for i, t in enumerate(self.tracks) if t.get("solo")]
        if solo:
            return solo
        return [i for i, t in enumerate(self.tracks) if not t.get("mute")]

    def _is_audible(self, ti: int) -> bool:
        return ti in set(self._audible_indices())

    def render_mix(self) -> tuple[np.ndarray, int]:
        """全軌道（Solo / Mute 適用）を 0 秒位置で足し合わせる。"""
        cache: dict = {}
        parts: list[np.ndarray] = []
        sr = 44100
        for ti in self._audible_indices():
            items = list(self.tracks[ti].get("items") or [])
            if not items:
                continue
            try:
                arr, sr, _meta = A.render_items(
                    items, self.lib.audio_dir, head=HEAD, tail=TAIL, fade_ms=FADE_MS,
                    crossfade_ms=XFADE_MS, target_db=TARGET_DB, slot=SLOT,
                    cache=cache, sr_out=None if not parts else sr)
            except Exception as e:  # noqa: BLE001
                self.log(f"mix 计算失败（轨 {ti + 1}）: {e}")
                continue
            parts.append(np.asarray(arr, dtype=np.float32))
        if not parts:
            return np.zeros(1, dtype=np.float32), 44100
        n = max(len(p) for p in parts)
        out = np.zeros(n, dtype=np.float32)
        for p in parts:
            out[: len(p)] += p
        peak = float(np.max(np.abs(out))) if n else 0.0
        if peak > 0.999:
            out *= 0.999 / peak
        return out, sr

    def _mark_mix_dirty(self) -> None:
        self._mix_dirty = True
        if self._mix_job is not None:
            try:
                self.after_cancel(self._mix_job)
            except Exception:
                pass
        self._mix_job = self.after(260, self._rebuild_mix)

    def _rebuild_mix(self) -> None:
        if not self.winfo_exists():
            return
        if self._mix_job is not None:
            try:
                self.after_cancel(self._mix_job)
            except Exception:
                pass
            self._mix_job = None
        try:
            arr, sr = self.render_mix()
        except Exception as e:  # noqa: BLE001
            self.log(f"mix 计算失败: {e}")
            arr, sr = np.zeros(1, dtype=np.float32), 44100
        self._mix_arr = arr
        self._mix_sr = int(sr) or 44100
        self._mix_dur = len(arr) / self._mix_sr
        self._mix_dirty = False
        self.redraw()

    def _stop_player(self) -> None:
        """MCI を閉じて一時ファイルを消す（次回の書き込み失敗を防ぐ）。"""
        try:
            self.player.stop()
        except Exception:
            pass
        try:
            self.player.close()
        except Exception:
            pass
        path, self._preview_path = self._preview_path, ""
        if path:
            for _ in range(3):
                try:
                    os.remove(path)
                    break
                except OSError:
                    pass

    def _new_preview_path(self) -> str:
        self._preview_seq += 1
        d = os.path.join(self.lib.path, "render")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"_preview_{os.getpid()}_{self._preview_seq}.wav")

    def _save_preview(self, arr: np.ndarray, sr: int) -> str:
        path = self._new_preview_path()
        write_wav(path, arr, sr)
        self._preview_path = path
        return path

    def _start_audio(self, path: str, start: float, kind: str, base: float,
                     label: str, dur: float = 0.0) -> bool:
        """プレイヤーは必ず停止済みで呼ぶ（_stop_player の後にパスを作る）。"""
        try:
            self.player.open(path)
            length = float(getattr(self.player, "length", 0.0) or dur or 0.0)
            if length and start >= length - 1e-3:
                start = 0.0
            self.player.play(max(0.0, start), None, loop=False,
                             on_tick=self._tick_audio)
        except Exception as e:  # noqa: BLE001
            # 壊れた wav / MCI エラーで「押しても何も起きない」状態にしない
            self._playing = False
            self._play_kind = ""
            self._stop_player()
            try:
                self.btn_preview.configure(text="▶  试听")
            except Exception:
                pass
            self.set_status(f"播放失败: {e}")
            self.log(f"播放失败: {e}")
            return False
        self._playing = True
        self._play_kind = kind
        self._play_base = max(0.0, base)
        self.btn_preview.configure(text="■ 停止试听")
        self.set_status(label)
        self.redraw()
        return True

    def _tick_audio(self, pos: float, playing: bool) -> None:
        if not self.winfo_exists():
            return
        if not playing:
            self._playing = False
            kind = self._play_kind
            self._play_kind = ""
            if kind in ("mix", "item"):
                self._playhead = max(0.0, self._play_base + pos)
            self._playing_item = None
            self._cand_playing_key = None
            self._stop_player()
            self.btn_preview.configure(text="▶  试听")
            self._paint_cand_playing(None)
            self.redraw()
            return
        if self._play_kind == "source":
            self.wave.set_playhead_live(pos)
            return
        if self._play_kind in ("mix", "item"):
            self._playhead = max(0.0, self._play_base + pos)
            self._move_playhead()
        else:
            self.lbl_time.configure(
                text=f"试听候选 {pos:.2f}s")

    def _move_playhead(self) -> None:
        try:
            x = self.t2x(self._playhead)
            hr = max(1, self.ruler.winfo_height())
            if self.ruler.find_withtag("ph"):
                self.ruler.coords("ph", x, 0, x, hr)
                self.ruler.coords("phtri", x - 5, hr - 9, x + 5, hr - 9, x, hr - 1)
            th = max(self.tl.winfo_height(), self._total_h())
            if self.tl.find_withtag("ph"):
                self.tl.coords("ph", x, 0, x, th)
                self.tl.coords("phtri", x - 5, 0, x + 5, 0, x, 7)
            hm = max(1, self.mix.winfo_height())
            if self.mix.find_withtag("ph"):
                self.mix.coords("ph", x, 0, x, hm)
                self.mix.coords("phtri", x - 5, 1, x + 5, 1, x, 9)
            self.lbl_time.configure(
                text=f"播放头 {self._playhead:.2f}s · 内容 {self._content_dur:.2f}s")
        except Exception:
            pass

    def _seek(self, t: float, redraw: bool = True) -> None:
        self._playhead = max(0.0, float(t))
        self._move_playhead()
        if redraw:
            self.redraw()

    def toggle_preview(self) -> None:
        if self._playing:
            self.stop_preview()
        else:
            self.play_mix()

    def play_mix(self) -> None:
        self.stop_preview(quiet=True)
        if not any(self.tracks[i].get("items") for i in self._audible_indices()):
            self.set_status("没有可播放的音（检查 Solo / Mute）")
            return
        if self._mix_dirty or self._mix_arr is None:
            self._rebuild_mix()
        arr = self._mix_arr
        sr = self._mix_sr
        if arr is None or len(arr) <= 1:
            self.set_status("mix 为空，无法试听")
            return
        path = self._save_preview(arr, sr)
        start = max(0.0, self._playhead)
        if start >= len(arr) / max(1, sr) - 1e-3:
            start = 0.0
            self._playhead = 0.0
        self._playing_item = None
        self._cand_playing_key = None
        self._paint_cand_playing(None)
        self._start_audio(path, start, "mix", 0.0,
                          f"试听 mix · 从 {start:.2f}s 开始 · "
                          f"{len(arr) / max(1, sr):.2f}s")

    def _preview_item(self, ti: int, ii: int) -> None:
        try:
            it = self.tracks[ti]["items"][ii]
        except (IndexError, KeyError):
            return
        src = str(it.get("source") or "")
        if (it.get("kind") == "missing") or not src:
            self.set_status("这个音是缺失音，没有可试听的内容")
            return
        if self._audio_for(src) is None:
            self.set_status(f"音频读不到：{src}")
            return
        self.stop_preview(quiet=True)
        # 重要: 単体試聴では at を 0 に落とす。
        # at を残すと「音の頭までの無音」が wav に入り、再生ヘッドだけ先に走る。
        preview = dict(it)
        preview["at"] = 0.0
        preview["lead"] = 0.0
        try:
            arr, sr, _meta = A.render_items(
                [preview], self.lib.audio_dir, head=HEAD, tail=TAIL, fade_ms=FADE_MS,
                crossfade_ms=XFADE_MS, target_db=TARGET_DB, slot=SLOT)
        except Exception as e:  # noqa: BLE001
            self.set_status(f"试听失败: {e}")
            return
        path = self._save_preview(arr, sr)
        base = float(it.get("at") or 0.0)
        self._playhead = base
        self._playing_item = (ti, ii)
        self._cand_playing_key = None
        self._paint_cand_playing(None)
        ok = self._start_audio(path, 0.0, "item", base,
                               f"试听音块 {it.get('mora', '')} · 轨 {ti + 1}",
                               dur=len(arr) / max(1, sr))
        if not ok:
            self._playing_item = None
            self.redraw()

    def _preview_candidate(self, cand: dict) -> None:
        self._cand_job = None
        if not self.winfo_exists() or not cand:
            return
        if self._audio_for(cand.get("source", "")) is None:
            self.set_status(f"音频读不到：{cand.get('source', '')}")
            return
        self.stop_preview(quiet=True)
        item = self._item_from_cand(cand, at=0.0)
        try:
            arr, sr, _meta = A.render_items(
                [item], self.lib.audio_dir, head=HEAD, tail=TAIL, fade_ms=FADE_MS,
                crossfade_ms=XFADE_MS, target_db=TARGET_DB, slot=SLOT)
        except Exception as e:  # noqa: BLE001
            self.set_status(f"试听失败: {e}")
            return
        path = self._save_preview(arr, sr)
        key = (cand.get("source", ""), float(cand.get("start") or 0.0),
               float(cand.get("end") or 0.0))
        self._cand_playing_key = key
        self._playing_item = None
        ok = self._start_audio(path, 0.0, "cand", 0.0,
                               f"试听候选 {cand.get('mora', '')} · "
                               f"{cand.get('source', '')} {key[1]:.3f}–{key[2]:.3f}s")
        self._paint_cand_playing(key if ok else None)

    def _paint_cand_playing(self, key: tuple | None) -> None:
        self._cand_playing_key = key
        try:
            for i, r in enumerate(self._takes):
                tags: list[str] = []
                if r.get("flags"):
                    tags.append("flag")
                if key is not None and (r.get("source", ""), float(r.get("start") or 0.0),
                                        float(r.get("end") or 0.0)) == key:
                    tags.append("playing")
                self.tbl.item(str(i), tags=tuple(tags))
        except Exception:
            pass

    def stop_preview(self, quiet: bool = False) -> None:
        was = self._playing
        self._stop_player()
        self._playing = False
        self._play_kind = ""
        self._playing_item = None
        self._cand_playing_key = None
        try:
            self.btn_preview.configure(text="▶  试听")
        except Exception:
            pass
        self._paint_cand_playing(None)
        if was and not quiet:
            self.set_status("已停止")
        self.redraw()

    def stop_preview_now(self) -> None:
        self.stop_preview()

    # ================================================================ 描画
    def _display_row(self, ti: int) -> int:
        return len(self.tracks) - 1 - ti

    def _ti_at_row(self, row: int) -> int:
        return len(self.tracks) - 1 - row

    def _tracks_top(self) -> float:
        return TRACK_PAD

    def _track_y(self, ti: int) -> float:
        return self._tracks_top() + self._display_row(ti) * (TRACK_H + TRACK_GAP)

    def _total_h(self) -> float:
        return (TRACK_PAD * 2 + len(self.tracks) * (TRACK_H + TRACK_GAP))

    def _plot_w(self) -> float:
        return max(10.0, self.tl.winfo_width() - NAME_W - 10)

    def t2x(self, t: float) -> float:
        span = max(1e-9, self._t1 - self._t0)
        return NAME_W + (t - self._t0) / span * self._plot_w()

    def x2t(self, x: float) -> float:
        span = max(1e-9, self._t1 - self._t0)
        f = (x - NAME_W) / max(1.0, self._plot_w())
        return self._t0 + max(0.0, min(1.0, f)) * span

    def _track_at(self, cy: float) -> int | None:
        y = cy - self._tracks_top()
        if y < 0:
            return None
        row = int(y // (TRACK_H + TRACK_GAP))
        if not (0 <= row < len(self.tracks)):
            return None
        y0 = self._track_y(self._ti_at_row(row))
        ti = self._ti_at_row(row)
        return ti if y0 <= cy <= y0 + TRACK_H else None

    def _sm_rects(self, ti: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
        cy = self._track_y(ti) + TRACK_H / 2
        s = (NAME_W - 58, cy - 11, NAME_W - 36, cy + 11)
        m = (NAME_W - 32, cy - 11, NAME_W - 10, cy + 11)
        return s, m

    def _sm_hit(self, ti: int, x: float, y: float) -> str | None:
        s, m = self._sm_rects(ti)
        for name, r in (("solo", s), ("mute", m)):
            if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                return name
        return None

    def _pan_hi(self) -> float:
        """平移スライダーの可動域。

        表示幅が内容より大きい（縮小しすぎた）ときでも少し動かせるように、
        右側に表示幅の 15% の余白を確保する。
        """
        span = max(MIN_SPAN, self._t1 - self._t0)
        return max(0.0, self._content_dur - span) + span * 0.15

    def _sync_pan(self) -> None:
        hi = self._pan_hi()
        self.pan.set_range(hi, self._t0, enabled=hi > 1e-6)
        self.lbl_pan.configure(
            text=f"{self._t0:.2f}–{self._t1:.2f}s / 内容 {self._content_dur:.2f}s")
        self.lbl_time.configure(
            text=f"播放头 {self._playhead:.2f}s · 内容 {self._content_dur:.2f}s")

    def _on_pan(self, value: float, _frac: float) -> None:
        span = max(MIN_SPAN, self._t1 - self._t0)
        t0 = max(0.0, min(float(value), self._pan_hi()))
        self._t0, self._t1 = t0, t0 + span
        self.redraw()

    def _zoom_at(self, factor: float, center_x: float) -> None:
        old = max(MIN_SPAN, self._t1 - self._t0)
        span = max(MIN_SPAN, min(MAX_SPAN, old * factor))
        c = self.x2t(center_x)
        f = (c - self._t0) / old if old > 0 else 0.0
        t0 = max(0.0, c - f * span)
        self._t0, self._t1 = t0, t0 + span
        self.redraw()

    def _fit(self) -> None:
        self._content_dur = self._content_duration()
        if self._content_dur <= 1e-6:
            self._t0, self._t1 = 0.0, 2.0
        else:
            self._t0 = 0.0
            self._t1 = max(MIN_SPAN, self._content_dur * 1.02)
        self.redraw()

    def _recalc(self) -> None:
        self._content_dur = self._content_duration()
        self.lbl_tracks.configure(
            text=f"{len(self.tracks)} 轨 · 内容 {self._content_dur:.2f}s")
        self._sync_pan()

    def _see_track(self, ti: int) -> None:
        try:
            self.tl.update_idletasks()
            top = self.tl.canvasy(0)
            bottom = top + max(1, self.tl.winfo_height())
            y0 = self._track_y(ti)
            y1 = y0 + TRACK_H
            total = max(1.0, self._total_h())
            if y0 < top + 4:
                self.tl.yview_moveto(max(0.0, (y0 - 20) / total))
            elif y1 > bottom - 4:
                self.tl.yview_moveto(max(0.0, (y1 - self.tl.winfo_height() + 20) / total))
        except Exception:
            pass

    def _tick_step(self) -> float:
        span = max(1e-9, self._t1 - self._t0)
        step = 1.0
        for cand in (0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300):
            step = cand
            if span / cand <= 10:
                break
        return step

    def redraw(self) -> None:
        if not self.winfo_exists():
            return
        self._redraw_ruler()
        self._redraw_tracks()
        self._redraw_mix()
        self._sync_pan()

    def _redraw_ruler(self) -> None:
        c = self.ruler
        c.delete("all")
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())
        c.create_rectangle(0, 0, w, h, fill=T.BG1, outline="")
        step = self._tick_step()
        t = (int(self._t0 / step)) * step
        while t <= self._t1 + 1e-9:
            x = self.t2x(t)
            if NAME_W - 1 <= x <= w:
                c.create_line(x, h - 6, x, h, fill=T.LINE2)
                c.create_text(x + 3, 4, anchor="w", fill=T.DIM,
                              font=("Consolas", 9), text=f"{t:.2f}")
            t += step
        c.create_line(0, h - 1, w, h - 1, fill=T.LINE)
        c.create_line(NAME_W, 0, NAME_W, h, fill=T.LINE2)
        x = self.t2x(self._playhead)
        c.create_line(x, 0, x, h, fill="#ff6b80", width=2, tags=("ph",))
        c.create_polygon(x - 5, h - 9, x + 5, h - 9, x, h - 1,
                         fill="#ff6b80", outline="", tags=("phtri",))

    def _redraw_tracks(self) -> None:
        c = self.tl
        c.delete("all")
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())
        total_h = max(h, self._total_h())
        step = self._tick_step()

        # 縦グリッド（ルーラーは別キャンバスなので常に見える）
        t = (int(self._t0 / step)) * step
        while t <= self._t1 + 1e-9:
            x = self.t2x(t)
            if NAME_W - 1 <= x <= w:
                c.create_line(x, self._tracks_top(), x, total_h, fill=T.GRID)
            t += step

        # --- トラック（上が新しい句）
        n = len(self.tracks)
        for row in range(n):
            ti = self._ti_at_row(row)
            tr = self.tracks[ti]
            y0 = self._track_y(ti)
            audible = self._is_audible(ti)
            if ti == self.cur:
                c.create_rectangle(0, y0, w, y0 + TRACK_H, fill="#241a1c", outline="")
            elif row % 2:
                c.create_rectangle(0, y0, w, y0 + TRACK_H, fill="#1b1617", outline="")
            c.create_line(0, y0 + TRACK_H, w, y0 + TRACK_H, fill=T.LINE)

            # ヘッダ: 名前 + S / M
            name = str(tr.get("name") or f"句 {ti + 1}")
            if len(name) > 10:
                name = name[:9] + "…"
            c.create_text(10, y0 + TRACK_H / 2, anchor="w",
                          text=f"{ti + 1:02d} {name}",
                          fill=T.SCARLET_HI if ti == self.cur else T.FG2,
                          font=T.FONT)
            s, m = self._sm_rects(ti)
            solo = bool(tr.get("solo"))
            mute = bool(tr.get("mute"))
            c.create_rectangle(*s, fill=T.SCARLET if solo else T.BG2,
                               outline=T.SCARLET_HI if solo else T.LINE2,
                               width=2 if solo else 1)
            c.create_text((s[0] + s[2]) / 2, (s[1] + s[3]) / 2, text="S",
                          fill="#ffffff" if solo else T.DIM, font=T.FONT_S)
            c.create_rectangle(*m, fill=T.WARN if mute else T.BG2,
                               outline=T.WARN if mute else T.LINE2,
                               width=2 if mute else 1)
            c.create_text((m[0] + m[2]) / 2, (m[1] + m[3]) / 2, text="M",
                          fill="#1a1112" if mute else T.DIM, font=T.FONT_S)
            c.create_line(NAME_W - 8, y0, NAME_W - 8, y0 + TRACK_H, fill=T.LINE2)

            # 音块
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
                if not audible:
                    fill, fg = "#2a2324", T.DIM
                selected = self.sel == (ti, ii)
                playing = self._playing_item == (ti, ii)
                if playing:
                    outline, width = "#ffd6dc", 3
                elif selected:
                    outline, width = T.SCARLET_HI, 3
                else:
                    outline, width = T.LINE2, 1
                c.create_rectangle(x0, y0 + 6, x1, y0 + TRACK_H - 6, fill=fill,
                                   outline=outline, width=width)
                if x1 - x0 >= 18:
                    label = str(it.get("mora") or "?")
                    if kind == "missing":
                        label = "缺" + label
                    c.create_text((x0 + x1) / 2, y0 + TRACK_H / 2, text=label,
                                  fill=fg, font=T.FONT_S)
                if selected and x1 - x0 >= 26:
                    # 選択中は左右のつまみを出す
                    c.create_line(x0 + 3, y0 + TRACK_H / 2 - 7, x0 + 3,
                                  y0 + TRACK_H / 2 + 7, fill=T.SCARLET_HI, width=2)
                    c.create_line(x1 - 3, y0 + TRACK_H / 2 - 7, x1 - 3,
                                  y0 + TRACK_H / 2 + 7, fill=T.SCARLET_HI, width=2)

        if not any(tr.get("items") for tr in self.tracks):
            c.create_text((NAME_W + w) / 2, self._tracks_top() + TRACK_H / 2,
                          text="把上面的候选拖到这里 · 或点「从歌词自动填充」",
                          fill=T.DIM, font=T.FONT)

        # --- 挿入位置 / 自由配置のゴースト
        if self._insert is not None:
            ti, ii = self._insert
            if 0 <= ti < len(self.tracks):
                lay = self._layout(self.tracks[ti])
                if ii < len(lay):
                    tx = self.t2x(lay[ii][1])
                elif lay:
                    tx = self.t2x(lay[-1][2])
                else:
                    tx = self.t2x(0.0)
                y0 = self._track_y(ti)
                c.create_line(tx, y0, tx, y0 + TRACK_H, fill=T.SCARLET_HI, width=3)
        if self._ghost is not None:
            ti = self._ghost.get("ti")
            if isinstance(ti, int) and 0 <= ti < len(self.tracks):
                a = float(self._ghost.get("at") or 0.0)
                d = float(self._ghost.get("dur") or SLOT)
                x0, x1 = self.t2x(a), self.t2x(a + d)
                y0 = self._track_y(ti)
                if x1 >= NAME_W and x0 <= w:
                    c.create_rectangle(max(NAME_W, x0), y0 + 6,
                                       max(max(NAME_W, x0) + 2, min(w, x1)),
                                       y0 + TRACK_H - 6, fill=T.SCARLET_DEEP,
                                       outline=T.SCARLET_HI, dash=(4, 3), width=2)

        x = self.t2x(self._playhead)
        c.create_line(x, 0, x, total_h, fill="#ff6b80", width=2, tags=("ph",))
        c.create_polygon(x - 5, 0, x + 5, 0, x, 7,
                         fill="#ff6b80", outline="", tags=("phtri",))
        c.configure(scrollregion=(0, 0, w, total_h))

    def _redraw_mix(self) -> None:
        c = self.mix
        c.delete("all")
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())
        c.create_rectangle(0, 0, w, h, fill="#141011", outline="")
        c.create_line(0, 0, w, 0, fill=T.LINE)
        c.create_text(10, 10, anchor="w", text="总波形（mix）",
                      fill=T.SCARLET_HI, font=T.FONT_B)
        if self._mix_dirty:
            c.create_text(NAME_W + 14, 10, anchor="w", text="计算中…",
                          fill=T.DIM, font=T.FONT_S)
        elif self._mix_arr is None or self._mix_dur <= 0:
            c.create_text(NAME_W + 14, 10, anchor="w",
                          text="还没有可播放内容", fill=T.DIM, font=T.FONT_S)
        elif len(self._mix_arr) > 1:
            arr = self._mix_arr
            sr = self._mix_sr
            top = 20
            bot = h - 4
            cy = (top + bot) / 2
            step = self._tick_step()
            t = (int(self._t0 / step)) * step
            while t <= self._t1 + 1e-9:
                x = self.t2x(t)
                if NAME_W - 1 <= x <= w:
                    c.create_line(x, top, x, bot, fill=T.GRID)
                t += step
            c.create_line(NAME_W, cy, w - 10, cy, fill=T.LINE2)
            c.create_line(NAME_W, top, NAME_W, bot, fill=T.LINE)
            n = max(1, int(w - 10 - NAME_W))
            span = max(1e-9, self._t1 - self._t0)
            half = max(1.0, (bot - top) / 2)
            for px in range(n):
                ta = self._t0 + span * px / n
                tb = self._t0 + span * (px + 1) / n
                a = max(0, int(ta * sr))
                b = min(len(arr), int(tb * sr) + 1)
                if b <= a:
                    continue
                seg = arr[a:b]
                if len(seg) > 8192:
                    # 超長軌でも描画が固まらないように、ピーク確認用に間引く
                    seg = seg[:: len(seg) // 4096]
                lo = float(seg.min())
                hi = float(seg.max())
                x = NAME_W + px
                c.create_line(x, cy - hi * half, x, cy - lo * half,
                              fill=T.WAVE, width=1)
        x = self.t2x(self._playhead)
        c.create_line(x, 0, x, h, fill="#ff6b80", width=2, tags=("ph",))
        c.create_polygon(x - 5, 1, x + 5, 1, x, 9,
                         fill="#ff6b80", outline="", tags=("phtri",))

    # ================================================================ マウス
    def _seek_press(self, e) -> str:
        if self._playing:
            self.stop_preview(quiet=True)
        self._scrub = True
        self._seek(self.x2t(float(e.x)), redraw=False)
        return "break"

    def _seek_motion(self, e) -> str:
        if self._scrub:
            self._seek(self.x2t(float(e.x)), redraw=False)
        return "break"

    def _seek_release(self, _e) -> str:
        self._scrub = False
        return "break"

    def _tl_press(self, e) -> None:
        self.tl.focus_set()
        if self._playing:
            self.stop_preview(quiet=True)
        cx = float(e.x)
        cy = float(self.tl.canvasy(e.y))

        ti = self._track_at(cy)
        if cx < NAME_W:
            if ti is None:
                return
            hit = self._sm_hit(ti, cx, cy)
            if hit == "solo":
                self.toggle_solo(ti)
                return
            if hit == "mute":
                self.toggle_mute(ti)
                return
            self.cur = ti
            self.sel = None
            self.redraw()
            return

        if ti is None:
            # ルーラー / 総波形 / 空白 = 自由に再生位置を置く（ドラッグ可）
            self._seek(self.x2t(cx), redraw=False)
            self._scrub = True
            self.redraw()
            return

        self.cur = ti
        t = self.x2t(cx)
        lay = self._layout(self.tracks[ti])
        hit_idx = None
        edge = None
        for ii in range(len(lay) - 1, -1, -1):
            it, a, b = lay[ii]
            xa, xb = self.t2x(a), self.t2x(b)
            if xa - 1 <= cx <= xb + 1:
                hit_idx = ii
                dl, dr = abs(cx - xa), abs(cx - xb)
                if min(dl, dr) <= EDGE_PX:
                    edge = "l" if dl <= dr else "r"
                break

        if hit_idx is None:
            self.sel = None
            self._seek(t, redraw=False)
            self._scrub = True
            self.redraw()
            return

        self.sel = (ti, hit_idx)
        it = self.tracks[ti]["items"][hit_idx]
        self._show_wave_for(it.get("source", ""), float(it.get("start") or 0.0),
                            float(it.get("end") or 0.0), it.get("mora", ""),
                            float(it.get("conf") or 0.0), it.get("flags", ""))
        mode = "move"
        if (edge and it.get("kind") != "missing"):
            mode = "trim_l" if edge == "l" else "trim_r"
        self._drag_item = {
            "ti": ti, "ii": hit_idx, "x": cx, "y": cy, "mode": mode,
            "press_t": t,
            "at0": float(it.get("at") or 0.0),
            "start0": float(it.get("start") or 0.0),
            "end0": float(it.get("end") or 0.0),
            "src_dur": self._source_dur(it.get("source", "")),
            "moved": False,
            "snap": self._snapshot(),
        }
        # 音块クリックでは再生位置を音の頭に置く（視口は動かさない）
        self._playhead = max(0.0, float(it.get("at") or 0.0))
        self._insert = None
        self._ghost = None
        self.redraw()

    def _source_dur(self, source: str) -> float:
        au = self._audio_for(source)
        return float(au.duration) if au is not None else 10.0

    def _tl_motion(self, e) -> None:
        if self._scrub:
            self._seek(self.x2t(float(e.x)), redraw=False)
            return
        d = self._drag_item
        if not d:
            return
        dx = float(e.x) - d["x"]
        dy = float(self.tl.canvasy(e.y)) - d["y"]
        if abs(dx) < 3 and abs(dy) < 3 and not d["moved"]:
            return
        d["moved"] = True
        if d["mode"] in ("trim_l", "trim_r"):
            self._drag_trim(d, e)
            return
        self._drag_move(d, e)

    def _drag_trim(self, d: dict, e) -> None:
        try:
            it = self.tracks[d["ti"]]["items"][d["ii"]]
        except (IndexError, KeyError):
            return
        dt = self.x2t(float(e.x)) - d["press_t"]
        if d["mode"] == "trim_l":
            lo = max(0.0, d["start0"] - d["at0"])
            hi = max(lo, d["end0"] - 0.02)
            new_start = max(lo, min(hi, d["start0"] + dt))
            delta = new_start - d["start0"]
            it["start"] = round(new_start, 3)
            it["at"] = round(max(0.0, d["at0"] + delta), 3)
        else:
            lo = d["start0"] + 0.02
            hi = max(lo, d["src_dur"])
            new_end = max(lo, min(hi, d["end0"] + dt))
            it["end"] = round(new_end, 3)
        self._show_wave_for(it.get("source", ""), float(it.get("start") or 0.0),
                            float(it.get("end") or 0.0), it.get("mora", ""),
                            float(it.get("conf") or 0.0), it.get("flags", ""))
        self.redraw()
        self.set_status(f"裁剪 {it.get('mora', '')} "
                        f"{float(it.get('start') or 0):.3f}–"
                        f"{float(it.get('end') or 0):.3f}s")

    def _drag_move(self, d: dict, e) -> None:
        cy = float(self.tl.canvasy(e.y))
        cx = float(e.x)
        ti = self._track_at(cy)
        if ti is None:
            ti = d["ti"]
        t = self.x2t(cx)
        if self.snap:
            idx = self._insert_index(self.tracks[ti], t)
            self._insert = (ti, idx)
            self._ghost = None
            self.set_status(f"吸附：插入到轨 {ti + 1} #{idx + 1}"
                            if idx < len(self.tracks[ti]["items"])
                            else f"吸附：插入到轨 {ti + 1} 末尾")
        else:
            free_at = max(0.0, d["at0"] + (t - d["press_t"]))
            it = self.tracks[d["ti"]]["items"][d["ii"]]
            dur = self._item_dur(it)
            self._ghost = {"ti": ti, "at": free_at, "dur": dur}
            self._insert = None
            self.set_status(f"自由移动 → {free_at:.2f}s"
                            + (f" · 轨 {ti + 1}" if ti != d["ti"] else ""))
        self.redraw()

    def _tl_release(self, _e) -> None:
        if self._scrub:
            self._scrub = False
            return
        d, self._drag_item = self._drag_item, None
        if not d:
            self._insert = None
            self._ghost = None
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

        if not d["moved"]:
            # ただのクリック = この音块を試聴
            self._tl_preview_item(d)
            return

        if self.snap:
            ins = self._insert
            self._insert = None
            ti = ins[0] if ins else d["ti"]
            idx = ins[1] if ins else len(self.tracks[ti]["items"])
            self._move_item_snap(d, ti, idx)
        else:
            ghost = self._ghost
            self._ghost = None
            if ghost is not None:
                self._move_item_free(d, int(ghost["ti"]), float(ghost["at"]))
            else:
                self.redraw()

    def _tl_preview_item(self, d: dict) -> None:
        ti, ii = d["ti"], d["ii"]
        if 0 <= ti < len(self.tracks) and 0 <= ii < len(self.tracks[ti]["items"]):
            self._preview_item(ti, ii)

    def _move_item_snap(self, d: dict, ti: int, idx: int) -> None:
        src, ii = d["ti"], d["ii"]
        items = self.tracks[src]["items"]
        if not (0 <= ii < len(items)):
            return
        same = src == ti and idx in (ii, ii + 1)
        if same:
            self.redraw()
            return
        item = items.pop(ii)
        if src == ti and idx > ii:
            idx -= 1
        target = self.tracks[ti]["items"]
        idx = max(0, min(idx, len(target)))
        item["lead"] = 0.0
        item["at"] = 0.0
        target.insert(idx, item)
        self._reflow(self.tracks[ti])
        if src != ti:
            self._reflow(self.tracks[src])
        self.cur = ti
        self.sel = (ti, idx)
        self._undo.append(d["snap"])
        self._redo.clear()
        self._changed(f"移动 {item.get('mora', '')} → 轨 {ti + 1} #{idx + 1}")
        self._see_track(ti)

    def _move_item_free(self, d: dict, ti: int, at: float) -> None:
        src, ii = d["ti"], d["ii"]
        try:
            item = self.tracks[src]["items"].pop(ii)
        except (IndexError, KeyError):
            return
        at = max(0.0, float(at))
        item["at"] = round(at, 3)
        target = self.tracks[ti]["items"]
        idx = 0
        for k, it in enumerate(target):
            if float(it.get("at") or 0.0) < at:
                idx = k + 1
        target.insert(idx, item)
        self.cur = ti
        self.sel = (ti, idx)
        self._undo.append(d["snap"])
        self._redo.clear()
        self._changed(f"自由移动 {item.get('mora', '')} → {at:.2f}s"
                      f" · 轨 {ti + 1}")
        self._see_track(ti)

    def _tl_hover(self, e) -> None:
        cx = float(e.x)
        cy = float(self.tl.canvasy(e.y))
        ti = self._track_at(cy)
        if ti is None:
            self.tl.configure(cursor="crosshair")
            return
        if cx < NAME_W:
            cursor = "hand2" if self._sm_hit(ti, cx, cy) else "arrow"
        else:
            cursor = "crosshair"
            lay = self._layout(self.tracks[ti])
            for ii in range(len(lay) - 1, -1, -1):
                _it, a, b = lay[ii]
                xa, xb = self.t2x(a), self.t2x(b)
                if xa - 1 <= cx <= xb + 1:
                    edge = min(abs(cx - xa), abs(cx - xb))
                    cursor = "sb_h_double_arrow" if edge <= EDGE_PX else "hand2"
                    break
        self.tl.configure(cursor=cursor)

    def _tl_dbl(self, e) -> None:
        cy = float(self.tl.canvasy(e.y))
        ti = self._track_at(cy)
        if ti is None or float(e.x) >= NAME_W:
            return
        if self._sm_hit(ti, float(e.x), cy):
            return
        old = str(self.tracks[ti].get("name") or f"句 {ti + 1}")
        name = simpledialog.askstring("轨名", "轨道名称：", initialvalue=old,
                                      parent=self)
        if name is not None and name.strip() != old:
            self._push_undo()
            self.tracks[ti]["name"] = name.strip() or old
            self._changed("已改名")

    def _tl_wheel(self, e) -> None:
        ctrl = bool(getattr(e, "state", 0) & 0x0004)
        shift = bool(getattr(e, "state", 0) & 0x0001)
        up = 1 if getattr(e, "delta", 0) > 0 else -1
        if ctrl:
            self._zoom_at(0.8 if up > 0 else 1.25, float(e.x))
        elif shift:
            span = self._t1 - self._t0
            d = span * 0.15 * (-1 if up > 0 else 1)
            hi = self._pan_hi()
            t0 = max(0.0, min(self._t0 + d, hi))
            self._t0, self._t1 = t0, t0 + span
            self.redraw()
        else:
            self.tl.yview_scroll(-up * 3, "units")

    def _tl_shift_wheel(self, e) -> None:
        span = self._t1 - self._t0
        up = 1 if getattr(e, "delta", 0) > 0 else -1
        d = span * 0.15 * (-1 if up > 0 else 1)
        hi = self._pan_hi()
        t0 = max(0.0, min(self._t0 + d, hi))
        self._t0, self._t1 = t0, t0 + span
        self.redraw()

    # --- 候補のドラッグ / クリック試聴
    def _cancel_cand_job(self) -> None:
        if self._cand_job is not None:
            try:
                self.after_cancel(self._cand_job)
            except Exception:
                pass
            self._cand_job = None

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
        self._cancel_cand_job()
        self._cand_job = self.after(200, lambda c=cand: self._preview_candidate(c))

    def _cand_motion(self, e) -> None:
        d = self._drag_cand
        if not d:
            return
        if abs(e.x_root - d["x"]) + abs(e.y_root - d["y"]) > 6:
            if not d["moved"]:
                d["moved"] = True
                self._cancel_cand_job()
                if self._playing:
                    self.stop_preview(quiet=True)
            self._cand_ghost(e)
            self.set_status("拖到下面轨道上松手")

    def _cand_ghost(self, e) -> None:
        try:
            cx = e.x_root - self.tl.winfo_rootx()
            cy = e.y_root - self.tl.winfo_rooty()
        except Exception:
            return
        if not (0 <= cx <= self.tl.winfo_width() and 0 <= cy <= self.tl.winfo_height()):
            self._ghost = None
            self._insert = None
            self.redraw()
            return
        cy = float(self.tl.canvasy(cy))
        cx = float(cx)
        ti = self._track_at(cy)
        if ti is None:
            self._ghost = None
            self._insert = None
            self.redraw()
            return
        cand = self._drag_cand["cand"] if self._drag_cand else {}
        dur = max(0.02, float(cand.get("end") or 0) - float(cand.get("start") or 0)) \
            + HEAD + TAIL
        t = self.x2t(cx)
        if self.snap:
            idx = self._insert_index(self.tracks[ti], t)
            self._insert = (ti, idx)
            self._ghost = None
        else:
            self._ghost = {"ti": ti, "at": max(0.0, t), "dur": dur}
            self._insert = None
        self.redraw()

    def _cand_release(self, e) -> None:
        if not self.winfo_exists():
            return
        d, self._drag_cand = self._drag_cand, None
        if not d:
            self._ghost = None
            self._insert = None
            return
        if not d.get("moved"):
            return
        self._cancel_cand_job()
        insert = self._insert
        self._ghost = None
        self._insert = None
        try:
            cx = e.x_root - self.tl.winfo_rootx()
            cy = e.y_root - self.tl.winfo_rooty()
        except Exception:
            self.redraw()
            return
        if not (0 <= cx <= self.tl.winfo_width() and 0 <= cy <= self.tl.winfo_height()):
            self.redraw()
            return
        self.stop_preview(quiet=True)
        cy = float(self.tl.canvasy(cy))
        cx = float(cx)
        self._push_undo()
        ti = self._track_at(cy)
        if ti is None:
            self._undo.pop()
            self.redraw()
            return
        t = self.x2t(cx)
        track = self.tracks[ti]
        item = self._item_from_cand(d["cand"], at=0.0 if self.snap else t)
        if self.snap:
            idx = insert[1] if insert and insert[0] == ti else self._insert_index(track, t)
            idx = max(0, min(idx, len(track["items"])))
            track["items"].insert(idx, item)
            self._reflow(track)
        else:
            idx = 0
            for k, it in enumerate(track["items"]):
                if float(it.get("at") or 0.0) < t:
                    idx = k + 1
            track["items"].insert(idx, item)
        self.cur = ti
        self.sel = (ti, idx)
        self._changed(f"加入 {item['mora']} → 轨 {ti + 1} #{idx + 1}")
        self._see_track(ti)

    # ================================================================ 出力
    def export(self) -> None:
        audible = self._audible_indices()
        if not any(self.tracks[i].get("items") for i in audible):
            messagebox.showwarning("空轨道", "没有可导出的音（检查 Solo / Mute）",
                                   parent=self)
            return
        if not self.flush():
            return
        self.stop_preview(quiet=True)
        self.set_status("正在导出 render…")
        self.update_idletasks()
        try:
            r = self.lib.render_edit({"schema": 2, "tracks": self.tracks}, dst="render")
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
