"""切点校对ペイン（メインウィンドウに埋め込む）。

上: 曲目選択 + 再生バー / 中: 波形 / 右: 現在の音と切点表。
波形の切点線をドラッグするか「起点/終点←播放头」で詰める。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from . import theme as T
from .core import kana as K
from .core.audio import read_audio
from .core.store import Mark, read_marks, write_audacity_labels, write_marks
from .library import Library, safe_name
from .player import make_player
from .waveview import WaveView


class ReviewPane(tk.Frame):
    def __init__(self, master, lib: Library, log=None, on_saved=None):
        super().__init__(master, bg=T.BG0)
        self.lib = lib
        self.log = log or (lambda _m: None)
        self.on_saved = on_saved
        self.tracks = list(lib.tracks)
        self.fi = 0
        self.marks: list[Mark] = []
        self.dirty = False
        self._paused = False             # 一時停止中か（再生位置を保つ）
        self.loop = False                # 単音ループ（「循环」ボタン）
        self.link = True                 # 切点の連動（OFF で隙間が作れる）
        self.nudge_step = 0.010          # 一括オフセットの刻み
        self.v_all = None                # 偏移の対象（全部 or 選択）
        self.audio = None
        self._undo: list[list[tuple[float, float]]] = []
        self._drag_snapshot = False
        self._pan_guard = False          # スライダーと表示範囲の往復更新よけ
        self._pan_drag = False
        self.player = make_player(self)
        self._build()
        if self.tracks:
            self.load_file(0)
        else:
            self.lbl_status.configure(text="这个音库里还没有曲目")

    # ---------------------------------------------------------------- 画面
    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # 3 つの区画の幅は仕切りをドラッグして変えられる（PanedWindow）
        pw = tk.PanedWindow(self, orient="horizontal", bg=T.LINE2, bd=0,
                            sashwidth=9, sashrelief="flat", showhandle=False,
                            opaqueresize=True)
        pw.grid(row=0, column=0, sticky="nsew")
        self.pw = pw

        # --- 左: 曲目一覧（ドロップダウンではなく常設のリスト）
        lp = T.RoundPanel(pw, width=300)
        left = lp.body
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)

        head = tk.Frame(left, bg=T.BG1)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 2))
        tk.Label(head, text="曲目", bg=T.BG1, fg=T.SCARLET_HI,
                 font=T.FONT_T).pack(side="left")
        self.lbl_tracks = tk.Label(head, text="", bg=T.BG1, fg=T.DIM, font=T.FONT_MONO)
        self.lbl_tracks.pack(side="right", pady=(6, 0))
        self.lbl_file = tk.Label(left, text="", bg=T.BG1, fg=T.WARN, font=T.FONT_B,
                                 anchor="w")
        self.lbl_file.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 4))

        self.tlist = ttk.Treeview(left, columns=("name", "moras"), show="headings",
                                  selectmode="browse")
        self.tlist.heading("name", text="曲目")
        self.tlist.heading("moras", text="音")
        self.tlist.column("name", width=198, anchor="w")
        self.tlist.column("moras", width=62, anchor="e", stretch=False)
        self.tlist.grid(row=2, column=0, sticky="nsew", padx=(10, 0), pady=(0, 10))
        lsb = ttk.Scrollbar(left, orient="vertical", command=self.tlist.yview)
        lsb.grid(row=2, column=1, sticky="ns", pady=(0, 10), padx=(0, 6))
        self.tlist.configure(yscrollcommand=lsb.set)
        self.tlist.tag_configure("na", foreground=T.DIM)
        self.tlist.bind("<<TreeviewSelect>>", self._on_list_pick)
        self.tlist.bind("<ButtonRelease-1>", self._on_list_click, add="+")

        pw.add(lp, minsize=180, width=300, stretch="never")
        mid = tk.Frame(pw, bg=T.BG0)
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(1, weight=1)
        pw.add(mid, minsize=420, stretch="always")

        # --- 再生バー（2 段: 再生系 / 保存系）
        bp = T.RoundPanel(mid)
        bp.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        bi = bp.body
        bi.columnconfigure(6, weight=1)
        self.btn_play = T.ScarletButton(bi, "▶  播放", self.toggle_play)
        self.btn_play.grid(row=0, column=0, sticky="w")
        T.ScarletButton(bi, "⟲ 句首", self.to_phrase_start, kind="ghost").grid(
            row=0, column=1, padx=(6, 0))
        T.ScarletButton(bi, "上一句", lambda: self.step_phrase(-1), kind="ghost").grid(
            row=0, column=3, padx=(18, 0))
        T.ScarletButton(bi, "下一句", lambda: self.step_phrase(1), kind="ghost").grid(
            row=0, column=4, padx=(6, 0))

        self.lbl_time = tk.Label(bi, text="0.00 / 0.00", bg=T.BG2, fg=T.SCARLET_HI,
                                 font=T.FONT_MONO_B, padx=12, pady=5)
        self.lbl_time.grid(row=0, column=6, sticky="e")

        T.ScarletButton(bi, "保存  Ctrl+S", self.save).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        T.ScarletButton(bi, "保存并下一首 →", self.save_next, kind="ghost").grid(
            row=1, column=2, columnspan=2, sticky="ew", padx=(6, 0), pady=(8, 0))
        self.lbl_dirty = tk.Label(bi, text="", bg=T.BG1, fg=T.WARN, font=T.FONT_B)
        self.lbl_dirty.grid(row=1, column=4, columnspan=2, sticky="w", padx=10, pady=(8, 0))

        for r in (0, 1):
            bi.rowconfigure(r, weight=1)
        bi.update_idletasks()
        bp.fit()

        # --- 波形
        wp = T.RoundPanel(mid)
        wp.grid(row=1, column=0, sticky="nsew")
        wavf = wp.body
        wavf.columnconfigure(0, weight=1)
        wavf.rowconfigure(2, weight=1)
        hb = tk.Frame(wavf, bg=T.BG1)
        hb.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
        # 右側から先に詰める（原文が長いときにボタンが押し出されないように）
        self.btn_link = T.ScarletButton(hb, "吸附", self.toggle_link, kind="ghost")
        self.btn_link.pack(side="right")
        self.btn_link._base_text = "吸附"
        self.btn_focus = T.ScarletButton(hb, "循环", self.toggle_loop, kind="ghost")
        self.btn_focus.pack(side="right", padx=(0, 6))
        self.btn_focus._base_text = "循环"
        # 起動時の見た目（点灯＝オン）を合わせておく
        self._paint_toggle(self.btn_focus, self.loop)
        self._paint_toggle(self.btn_link, self.link)
        self.lbl_src = tk.Label(hb, text="", bg=T.BG1, fg=T.FG2, font=T.FONT, anchor="w",
                                justify="left")
        self.lbl_src.pack(side="left", fill="x", expand=True)

        self.wave = WaveView(wavf, on_seek=self._on_seek, on_boundary=self._on_boundary,
                             on_drag_end=self._on_drag_end, on_double=self._on_seek_play,
                             on_select=self._on_wave_select, on_view=self._on_wave_view)
        self.wave.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 4))
        self.wave.link = self.link

        # --- 表示範囲スライダー（マウスで横に動かして表示位置をずらす）
        pbar = tk.Frame(wavf, bg=T.BG1)
        pbar.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        pbar.columnconfigure(0, weight=1)
        self.pan = T.PanSlider(pbar, command=self._on_pan)
        self.pan.grid(row=0, column=0, sticky="ew")
        self.lbl_pan = tk.Label(pbar, text="", bg=T.BG1, fg=T.FG2, font=T.FONT_MONO,
                                width=19, anchor="e")
        self.lbl_pan.grid(row=0, column=1, sticky="e", padx=(12, 0))

        # --- 右: 現在の音 + 切点表
        rp = T.RoundPanel(pw, width=470)
        pw.add(rp, minsize=340, width=470, stretch="never")
        right = rp.body
        right.columnconfigure(0, weight=1)
        right.rowconfigure(6, weight=1)

        tk.Label(right, text="当前音", bg=T.BG1, fg=T.SCARLET_HI, font=T.FONT_B,
                 anchor="w").grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))

        card = tk.Frame(right, bg=T.BG2)
        card.grid(row=1, column=0, sticky="ew", padx=14)
        card.columnconfigure(1, weight=1)
        self.lbl_mora = tk.Label(card, text="—", bg=T.BG2, fg=T.FG, font=T.FONT_HUGE)
        self.lbl_mora.grid(row=0, column=0, rowspan=2, padx=(16, 16), pady=8)
        self.lbl_phones = tk.Label(card, text="", bg=T.BG2, fg=T.FG2, font=T.FONT_MONO,
                                   anchor="w")
        self.lbl_phones.grid(row=0, column=1, sticky="ew", pady=(12, 0))

        kv = tk.Frame(card, bg=T.BG2)
        kv.grid(row=1, column=1, sticky="ew", pady=(0, 10))
        self.kv: dict[str, tk.Label] = {}
        for j, key in enumerate(("起点", "终点", "时长", "置信", "峰值", "落差")):
            tk.Label(kv, text=key, bg=T.BG2, fg=T.DIM, font=T.FONT_S) \
                .grid(row=(j // 3) * 2, column=j % 3, sticky="w", padx=(0, 18))
            v = tk.Label(kv, text="—", bg=T.BG2, fg=T.FG, font=T.FONT_MONO_B)
            v.grid(row=(j // 3) * 2 + 1, column=j % 3, sticky="w", padx=(0, 18))
            self.kv[key] = v

        self.lbl_flags = tk.Label(card, text="", bg=T.BG2, fg=T.WARN, font=T.FONT,
                                  anchor="w", justify="left", wraplength=390)
        self.lbl_flags.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 10))

        edit = tk.Frame(right, bg=T.BG1)
        edit.grid(row=2, column=0, sticky="ew", padx=14, pady=10)
        edit.columnconfigure((0, 1), weight=1)
        T.ScarletButton(edit, "起点←播放头", self.set_start_here, kind="ghost",
                        font=T.FONT).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        T.ScarletButton(edit, "终点←播放头", self.set_end_here, kind="ghost",
                        font=T.FONT).grid(row=0, column=1, sticky="ew")
        T.ScarletButton(edit, "偏移 −10ms", lambda: self.nudge(-self.nudge_step),
                        kind="ghost", font=T.FONT).grid(
            row=1, column=0, sticky="ew", padx=(0, 6), pady=(6, 0))
        T.ScarletButton(edit, "偏移 +10ms", lambda: self.nudge(self.nudge_step),
                        kind="ghost", font=T.FONT).grid(
            row=1, column=1, sticky="ew", pady=(6, 0))
        self.v_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(edit, text="全部音一起偏移（不勾 = 只动切点表里选中的）",
                        variable=self.v_all).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.lbl_status = tk.Label(right, text="点波形 = 定位播放头 · 拖下方假名带的竖线 = 改切点 · 空格播放",
                                   bg=T.BG1, fg=T.FG2, font=T.FONT, anchor="w",
                                   wraplength=410, justify="left")
        self.lbl_status.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 10))

        head2 = tk.Frame(right, bg=T.BG1)
        head2.grid(row=4, column=0, sticky="ew", padx=14)
        tk.Label(head2, text="切点表", bg=T.BG1, fg=T.SCARLET_HI, font=T.FONT_B).pack(
            side="left")
        self.lbl_cnt = tk.Label(head2, text="", bg=T.BG1, fg=T.DIM, font=T.FONT)
        self.lbl_cnt.pack(side="right")

        cols = ("i", "mora", "start", "end", "dur", "conf", "flag")
        self.tbl = ttk.Treeview(right, columns=cols, show="headings", height=14,
                                selectmode="extended")
        for c, t, w, anchor in (("i", "#", 36, "center"),
                                ("mora", "音（罗马音）", 78, "center"),
                                ("start", "起点", 74, "e"), ("end", "终点", 74, "e"),
                                ("dur", "时长", 62, "e"), ("conf", "置信", 56, "e"),
                                ("flag", "标记", 66, "w")):
            self.tbl.heading(c, text=t)
            self.tbl.column(c, width=w, anchor=anchor, stretch=(c == "flag"))
        self.tbl.grid(row=6, column=0, sticky="nsew", padx=(14, 0), pady=(4, 14))
        tsb = ttk.Scrollbar(right, orient="vertical", command=self.tbl.yview)
        tsb.grid(row=6, column=1, sticky="ns", pady=(4, 0), padx=(0, 6))
        hsb = ttk.Scrollbar(right, orient="horizontal", command=self.tbl.xview)
        hsb.grid(row=7, column=0, sticky="ew", padx=(14, 0), pady=(0, 14))
        self.tbl.configure(yscrollcommand=tsb.set, xscrollcommand=hsb.set)
        # <<TreeviewSelect>> はプログラムから selection_set しても飛んでくるので使わない。
        # ユーザーのクリック（と方向キー）だけで「定位＋中央寄せ」する。
        self.tbl.bind("<ButtonRelease-1>", self._on_tbl_click, add="+")
        self.tbl.bind("<KeyRelease-Up>", lambda _e: self._apply_tbl_sel(), add="+")
        self.tbl.bind("<KeyRelease-Down>", lambda _e: self._apply_tbl_sel(), add="+")
        self.tbl.tag_configure("flag", foreground=T.WARN)

    # ---------------------------------------------------------------- 曲目
    def _refresh_list(self) -> None:
        self._filling = True
        try:
            self.tlist.delete(*self.tlist.get_children())
            for i, t in enumerate(self.tracks):
                name = t.get("name", "")
                done = os.path.exists(self.lib.marks_path(name))
                moras = ""
                mp = self.lib.marks_path(name)
                if done:
                    try:
                        moras = str(len(read_marks(mp)))
                    except Exception:
                        moras = "?"
                self.tlist.insert("", "end", iid=str(i), tags=() if done else ("na",),
                                  values=(name, moras))
            self.lbl_tracks.configure(text=f"{len(self.tracks)} 首")
            if 0 <= self.fi < len(self.tracks):
                iid = str(self.fi)
                if self.tlist.exists(iid):
                    self.tlist.selection_set(iid)
                    self.tlist.focus(iid)
                    self.tlist.see(iid)
                name = self.tracks[self.fi].get("name", "")
                done = os.path.exists(self.lib.marks_path(name))
                self.lbl_file.configure(text="" if done else "未分析")
        finally:
            self._filling = False

    def _on_list_pick(self, _e=None) -> None:
        if getattr(self, "_filling", False):
            return
        sel = self.tlist.selection()
        if not sel:
            return
        i = int(sel[0])
        if i != self.fi:
            self.load_file(i)
            if self.fi != i:             # 未保存でストップされた → 選択を戻す
                self._refresh_list()

    def _on_list_click(self, e) -> None:
        """同じ曲目をもう一度クリック = 整首表示（「整首」ボタンの代わり）。"""
        iid = self.tlist.identify_row(e.y)
        if iid and iid == str(self.fi):
            self._fit()

    def load_file(self, i: int, ask: bool = True) -> None:
        if not self.tracks:
            return
        if ask and self.dirty:
            ans = messagebox.askyesnocancel(
                "未保存",
                f"「{self.tracks[self.fi]['name']}」的切点改了还没保存。\n\n"
                "是：保存后切换　　否：放弃修改　　取消：留在这里",
                parent=self)
            if ans is None:
                return
            if ans:
                self.save(quiet=True)
        self.fi = i
        t = self.tracks[i]
        name = t["name"]
        wav = self.lib.audio_path(t)
        self.player.stop()
        self._paused = False
        self.btn_play.configure(text="▶  播放")

        self.audio = read_audio(wav)
        self.wave.set_audio(self.audio)

        mp = self.lib.marks_path(name)
        self.marks = read_marks(mp) if os.path.exists(mp) else []
        self.wave.set_marks(self.marks)
        self.wave.select(0 if self.marks else -1)
        self._undo.clear()
        self.dirty = False
        self.lbl_dirty.configure(text="")
        try:
            self.player.open(wav)
        except Exception as e:  # noqa: BLE001
            self.log(f"播放失败: {e}")

        self.lbl_time.configure(text=f"{0.0:6.2f} / {self.audio.duration:.2f}")
        src = (t.get("text") or "").strip()
        self.lbl_src.configure(text=src[:38] + ("…" if len(src) > 38 else ""))
        self.lbl_cnt.configure(text=f"{len(self.marks)} 音")
        self.fill_table()
        self._refresh_list()
        self._refresh_card()
        self.lbl_status.configure(text="未修改")

    def refresh_tracks(self) -> None:
        self.tracks = list(self.lib.tracks)
        self.fi = max(0, min(self.fi, len(self.tracks) - 1)) if self.tracks else 0
        self._refresh_list()

    def select_track(self, name: str) -> None:
        for i, t in enumerate(self.tracks):
            if t.get("name") == name:
                self.load_file(i)
                return

    # ---------------------------------------------------------------- 表
    def fill_table(self) -> None:
        self.tbl.delete(*self.tbl.get_children())
        for i, m in enumerate(self.marks):
            self.tbl.insert("", "end", iid=str(i), tags=("flag",) if m.flags else (),
                            values=(i, K.mora_label(m.mora), f"{m.start:.3f}", f"{m.end:.3f}",
                                    f"{m.dur * 1000:.0f}", f"{m.conf:.2f}", m.flags))
        if self.marks:
            self._sync_table_sel(max(0, self.wave.sel))

    def _sync_table_sel(self, i: int) -> None:
        iid = str(i)
        if not self.tbl.exists(iid):
            return
        # すでにこの行だけが選ばれているなら触らない
        # （selection_set は同じ行でも <<TreeviewSelect>> を投げるので無限ループになる）
        if tuple(self.tbl.selection()) == (iid,):
            return
        self.tbl.selection_set(iid)
        self.tbl.see(iid)
        self.tbl.focus(iid)

    def _on_tbl_click(self, e) -> None:
        """切点表をクリック → その音に定位。"""
        iid = self.tbl.identify_row(e.y)
        if not iid:
            return
        if iid not in self.tbl.selection():
            self.tbl.selection_set(iid)
        self._apply_tbl_pick(int(iid))

    def _apply_tbl_sel(self) -> None:
        sel = self.tbl.selection()
        if sel:
            self._apply_tbl_pick(int(sel[0]))

    def _on_tbl_pick(self, _e=None) -> None:
        self._apply_tbl_sel()

    def _apply_tbl_pick(self, i: int) -> None:
        if getattr(self, "_picking", False):     # 選択→再生→選択 の再入を防ぐ
            return
        if not (0 <= i < len(self.marks)):
            return
        m = self.marks[i]
        self._picking = True
        try:
            # 再生中/一時停止中だと浮標があとから再生位置に戻されるので、まず止める
            self.player.stop()
            self._paused = False
            self.btn_play.configure(text="▶  播放")
            # 浮標はこの音の頭、表示はこの音が真ん中に来るように
            self.wave.set_playhead(m.start, move=False)
            if i != self.wave.sel:
                self.wave.select(i)
            self.wave.center_on(m.start, m.end)
            self._refresh_card()
            if self.loop:                   # 循环 ON ならそのまま鳴らす
                self._play_from(m.start)
        finally:
            self._picking = False

    # ---------------------------------------------------------------- 表示範囲
    def _on_wave_view(self, t0: float, t1: float) -> None:
        """波形の表示範囲が変わった → スライダーの可動域と位置を合わせる。"""
        dur = float(self.wave.duration or 0.0)
        span = max(1e-3, t1 - t0)
        hi = max(0.0, dur - span)
        if not self._pan_drag:
            # 全域表示でも押せば拡大できるので、見た目は常に有効のまま
            self.pan.set_range(hi, t0, enabled=True)
        self.lbl_pan.configure(text=f"{t0:.2f}-{t1:.2f}s / {dur:.2f}s")

    def _on_pan(self, value: float, frac: float) -> None:
        """スライダーを動かした → 表示範囲をずらす。"""
        dur = float(self.wave.duration or 0.0)
        if dur <= 0:
            return
        span = self.wave.t1 - self.wave.t0
        if span >= dur - 1e-6:
            # 全域表示のまま押された → まず「押した場所」を中心に拡大する
            # （拡大しないと動かす余地が無くて「効かない」ように見える）
            span = min(dur, max(2.0, min(8.0, dur / 4.0)))
            t0 = frac * dur - span / 2.0
        else:
            t0 = value
        t0 = max(0.0, min(t0, max(0.0, dur - span)))
        self._pan_drag = True
        try:
            self.wave.set_view(t0, min(dur, t0 + span))
            self.pan.set_range(max(0.0, dur - span), t0, enabled=True)
        finally:
            self._pan_drag = False
        self.lbl_pan.configure(
            text=f"{t0:.2f}-{min(dur, t0 + span):.2f}s / {dur:.2f}s")

    def _on_wave_select(self, i: int) -> None:
        """波形側で音が選ばれた（境界を掴んだ等）。

        ここでは絶対に表示範囲を動かさない。マウス操作で勝手に視界が
        ジャンプするのが鬱陶しいため、寄せるのは切点表クリックと
        「聚焦本音」ボタンのときだけにする。
        """
        self._sync_table_sel(i)
        self._refresh_card()
        if self.loop and 0 <= i < len(self.marks):
            self._play_from(self.marks[i].start)

    def _refresh_card(self) -> None:
        i = self.wave.sel
        if not (0 <= i < len(self.marks)):
            self.lbl_mora.configure(text="—")
            self.lbl_phones.configure(text="")
            for v in self.kv.values():
                v.configure(text="—", fg=T.FG)
            self.lbl_flags.configure(text="")
            return
        m = self.marks[i]
        self.lbl_mora.configure(text=m.mora)
        rom = K.mora_romaji(m.mora)
        self.lbl_phones.configure(
            text=f"{rom + '   ' if rom else ''}/{m.phones}/   句 {m.phrase}   #{i}")
        self.kv["起点"].configure(text=f"{m.start:.3f}s", fg=T.FG)
        self.kv["终点"].configure(text=f"{m.end:.3f}s", fg=T.FG)
        self.kv["时长"].configure(text=f"{m.dur * 1000:.0f}ms", fg=T.FG)
        conf = m.conf
        self.kv["置信"].configure(text=f"{conf:.2f}",
                                  fg=T.BAD if conf < 0.3 else (T.WARN if conf < 0.6 else T.OK))
        self.kv["峰值"].configure(text=f"{m.peak_db:.1f}dB", fg=T.FG)
        self.kv["落差"].configure(text=f"{m.dip:.1f}dB", fg=T.FG)
        self.lbl_flags.configure(text=("⚠ " + m.flags.replace(",", "  ")) if m.flags else "")

    # ---------------------------------------------------------------- 再生
    def _tick(self, pos: float, playing: bool) -> None:
        if playing:
            self.wave.set_playhead_live(pos)
        elif not self._paused:
            # 再生が終わった時だけ、終了位置に置く（一時停止中は触らない）
            self.wave.set_playhead(pos, move=False)
        dur = self.audio.duration if self.audio else 0.0
        self.lbl_time.configure(text=f"{pos:6.2f} / {dur:.2f}")
        if not playing and not self._paused:      # 止まった時だけ表示を戻す
            self.btn_play.configure(text="▶  播放")

    def toggle_play(self) -> None:
        if self.player.is_playing():
            self.player.pause()
            self._paused = True
            self.btn_play.configure(text="▶  继续")
            return
        if self._paused:                          # 続きから（位置は保つ）
            self._paused = False
            self.player.resume()
            self.btn_play.configure(text="❚❚ 暂停")
            return
        self._play_from(self.wave.playhead)

    def _play_from(self, t: float) -> None:
        self._paused = False
        # 「循环本音」ON、または「聚焦本音」ON のときは選択中の音だけを繰り返す
        if self.loop and 0 <= self.wave.sel < len(self.marks):
            m = self.marks[self.wave.sel]
            a, b = self._loop_span(m)
            self.player.play(a, b, loop=True, on_tick=self._tick)
            self.btn_play.configure(text="❚❚ 暂停")
            self._sync_table_sel(self.wave.sel)
            self._refresh_card()
            return
        dur = self.audio.duration if self.audio else 0.0
        i = self._mark_at(t)
        if i >= 0:
            _pa, pb = self._phrase_span(i)
            a = max(0.0, min(t, dur))          # 選んだ位置そのものから
            b = pb if pb > a + 0.05 else min(dur, a + 2.0)
        else:
            a, b = max(0.0, t), min(dur, t + 2.0)
        self.player.play(a, b, loop=False, on_tick=self._tick)
        self.btn_play.configure(text="❚❚ 暂停")
        if i >= 0:
            self.wave.select(i)
            self._sync_table_sel(i)
            self._refresh_card()

    def _on_seek(self, t: float) -> None:
        self.player.stop()
        self._paused = False
        self.btn_play.configure(text="▶  播放")
        self.wave.set_playhead(t, move=False)
        i = self._mark_at(t)
        if i >= 0:
            self.wave.select(i)
            self._sync_table_sel(i)
            self._refresh_card()
            if self.loop:                 # 循環中なら、置いた音をそのまま回す
                self._play_from(self.marks[i].start)

    def _on_seek_play(self, t: float) -> None:
        self._on_seek(t)
        self._play_from(t)

    def to_phrase_start(self) -> None:
        i = self.wave.sel
        if i < 0:
            return
        a, _b = self._phrase_span(i)
        self.player.stop()
        self._paused = False
        self.wave.set_playhead(a)
        self.lbl_status.configure(text=f"句首 {a:.3f}s")

    def toggle_loop(self) -> None:
        """「循环」: 選んだ音を繰り返し再生するモード。"""
        self.loop = not self.loop
        self._paint_toggle(self.btn_focus, self.loop)
        if self.loop and 0 <= self.wave.sel < len(self.marks):
            self.lbl_status.configure(text="循环：开（反复放当前音，切点改了自动跟上）")
            self._play_from(self.wave.playhead)
        else:
            self.lbl_status.configure(text="循环：开" if self.loop else "循环：关")
            if not self.loop:
                self.player.stop()
                self.btn_play.configure(text="▶  播放")

    def toggle_link(self) -> None:
        """切点の吸附。OFF にすると片側だけ動かせて、音の間に隙間が作れる。"""
        self.link = not self.link
        self.wave.link = self.link
        self._paint_toggle(self.btn_link, self.link)
        self.lbl_status.configure(
            text="吸附：开（拖一条线两边一起动，音紧贴）" if self.link
            else "吸附：关（只动一侧，音之间可以留空隙）")

    @staticmethod
    def _paint_toggle(btn, on: bool) -> None:
        base = getattr(btn, "_base_text", btn.cget("text"))
        if on:
            btn.configure(bg=T.SCARLET, fg="#ffffff", text=base)
        else:
            btn.configure(bg=T.BG2, fg=T.FG2, text=base)

    # ---------------------------------------------------------------- 位置
    def _mark_at(self, t: float) -> int:
        """t を含む音。境界ちょうどは「これから始まる音」の側に入れる。"""
        for i, m in enumerate(self.marks):
            if m.start - 1e-6 <= t < m.end - 1e-6:
                return i
        if self.marks and abs(t - self.marks[-1].end) < 1e-6:
            return len(self.marks) - 1
        return -1

    def _phrase_span(self, i: int) -> tuple[float, float]:
        if not self.marks:
            return 0.0, 0.0
        ph = self.marks[i].phrase
        same = [m for m in self.marks if m.phrase == ph]
        return same[0].start, same[-1].end

    def step_phrase(self, d: int) -> None:
        if not self.marks:
            return
        i = self.wave.sel if self.wave.sel >= 0 else 0
        phrases = sorted({m.phrase for m in self.marks})
        k = phrases.index(self.marks[i].phrase) + d
        if 0 <= k < len(phrases):
            idx = next(j for j, m in enumerate(self.marks) if m.phrase == phrases[k])
            self.wave.select(idx, scroll=True)
            self._sync_table_sel(idx)
            self._refresh_card()

    def focus_mark(self) -> None:
        i = self.wave.sel
        if 0 <= i < len(self.marks):
            m = self.marks[i]
            self.wave.center_on(m.start, m.end)
            self.lbl_status.configure(
                text=f"已聚焦 #{i} {K.mora_label(m.mora)}  {m.start:.3f}–{m.end:.3f}s")

    def _fit(self) -> None:
        self.wave.fit()

    # ---------------------------------------------------------------- 編集
    def _snapshot(self) -> None:
        self._undo.append([(m.start, m.end) for m in self.marks])
        if len(self._undo) > 60:
            self._undo.pop(0)

    def _mark_dirty(self) -> None:
        self.dirty = True
        self.lbl_dirty.configure(text="● 未保存")

    def _loop_span(self, m) -> tuple[float, float]:
        """単音ループの区間。置いた浮標がこの音の中なら、そこから鳴らす。"""
        dur = self.audio.duration if self.audio else m.end
        a = m.start
        ph = self.wave.playhead
        if m.start - 1e-6 <= ph <= m.end + 1e-6:
            a = max(m.start, min(ph, m.end - 0.02))
        b = min(dur, m.end + 0.04)
        return a, b

    def _retarget_loop(self) -> None:
        """切点を直したら、いま鳴っているループの長さも更新する。"""
        if not self.loop or not (0 <= self.wave.sel < len(self.marks)):
            return
        if not (self.player.is_playing() or self._paused):
            return
        try:
            a, b = self._loop_span(self.marks[self.wave.sel])
            self.player.set_segment(a, b)
        except Exception:
            pass

    def _on_boundary(self, i: int, _t: float) -> None:
        if not self._drag_snapshot:
            self._snapshot()
            self._drag_snapshot = True
        self._mark_dirty()
        self._retarget_loop()
        self.fill_table()
        self._refresh_card()

    def _on_drag_end(self, _i: int) -> None:
        self._drag_snapshot = False

    def undo(self) -> None:
        if not self._undo:
            self.lbl_status.configure(text="没有可撤销的操作")
            return
        snap = self._undo.pop()
        for m, (a, b) in zip(self.marks, snap):
            m.start, m.end = a, b
        self._mark_dirty()
        self._retarget_loop()
        self.wave.set_marks(self.marks)
        self.fill_table()
        self._refresh_card()
        self.lbl_status.configure(text="已撤销")

    def set_start_here(self) -> None:
        i = self.wave.sel
        if not (0 <= i < len(self.marks)):
            return
        self._snapshot()
        t = self.wave.playhead
        if self.link:
            lo = self.marks[i - 1].start + 0.02 if i > 0 else 0.0
            self.marks[i].start = min(max(lo, t), self.marks[i].end - 0.02)
            if i > 0:
                self.marks[i - 1].end = self.marks[i].start
        else:
            # 連動オフ: 隣は動かさない（＝隙間ができる）
            lo = self.marks[i - 1].start + 0.02 if i > 0 else 0.0
            self.marks[i].start = min(max(lo, t), self.marks[i].end - 0.02)
        self._after_edit()

    def set_end_here(self) -> None:
        i = self.wave.sel
        if not (0 <= i < len(self.marks)):
            return
        self._snapshot()
        t = self.wave.playhead
        if self.link:
            hi = (self.marks[i + 1].end - 0.02 if i < len(self.marks) - 1
                  else self.audio.duration)
            self.marks[i].end = max(min(hi, t), self.marks[i].start + 0.02)
            if i < len(self.marks) - 1:
                self.marks[i + 1].start = self.marks[i].end
        else:
            # 連動オフ: 次の音の頭はそのまま（＝隙間ができる）
            hi = (self.marks[i + 1].start if i < len(self.marks) - 1
                  else self.audio.duration)
            self.marks[i].end = max(min(hi, t), self.marks[i].start + 0.02)
        self._after_edit()

    def _after_edit(self) -> None:
        self._mark_dirty()
        self._retarget_loop()
        self.wave.set_marks(self.marks)
        self.fill_table()
        self._refresh_card()
        self.lbl_status.configure(text="已修改（记得保存）")

    # ---------------------------------------------------------------- 一括偏移
    def _target_indices(self) -> list[int]:
        if self.v_all is not None and self.v_all.get():
            return list(range(len(self.marks)))
        idxs = []
        for iid in self.tbl.selection():
            try:
                idxs.append(int(iid))
            except ValueError:
                pass
        return sorted(i for i in idxs if 0 <= i < len(self.marks))

    def nudge(self, d: float) -> None:
        """選んだ音（または全部）の切点をまとめて d 秒ずらす。"""
        if not self.marks:
            return
        idxs = self._target_indices()
        if not idxs:
            self.lbl_status.configure(text="先在切点表里选音（或勾上「全部」）")
            return
        dur = self.audio.duration if self.audio else self.marks[-1].end
        self._snapshot()
        for i in idxs:
            m = self.marks[i]
            a = max(0.0, min(m.start + d, dur - 0.02))
            b = max(a + 0.02, min(m.end + d, dur))
            m.start, m.end = a, b
        self._mark_dirty()
        self.wave.set_marks(self.marks)
        self.fill_table()
        self.tbl.selection_remove(*self.tbl.selection())
        for i in idxs:
            iid = str(i)
            if self.tbl.exists(iid):
                self.tbl.selection_add(iid)
        self._refresh_card()
        self.lbl_status.configure(
            text=f"偏移 {d * 1000:+.0f}ms · {len(idxs)} 个音"
                 + ("（全部）" if self.v_all is not None and self.v_all.get() else ""))

    # ---------------------------------------------------------------- 保存
    def save(self, quiet: bool = False) -> bool:
        if not self.tracks:
            return False
        name = self.tracks[self.fi]["name"]
        marks = self.marks
        try:
            write_marks(self.lib.marks_path(name), marks)
            labels = os.path.join(self.lib.path, "labels", f"{safe_name(name)}.txt")
            write_audacity_labels(labels, marks)
            t = self.lib.track(name)
            if t is not None:
                t["status"] = "analyzed" if marks else t.get("status", "imported")
                t["avg_conf"] = round(sum(m.conf for m in marks) / len(marks), 3) if marks else 0.0
            self.lib.save()
            self.lib.write_project()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("保存失败", str(e), parent=self)
            return False
        self.dirty = False
        self.lbl_dirty.configure(text="")
        self.lbl_status.configure(text=f"已保存：{name} · {len(marks)} 个切点")
        if self.on_saved:
            self.on_saved(name)
        if not quiet:
            self.log(f"校对保存 {name}: {len(marks)} 音")
        return True

    def save_next(self) -> None:
        if not self.tracks:
            return
        self.save()
        if self.fi + 1 < len(self.tracks):
            self.load_file(self.fi + 1, ask=False)
        else:
            self.lbl_status.configure(text="已经是最后一条")

    # ---------------------------------------------------------------- 後始末
    def can_leave(self) -> bool:
        """タブや音庫を切り替えてよいか（未保存なら保存するか聞く）。"""
        if not self.dirty:
            return True
        ans = messagebox.askyesnocancel(
            "未保存", f"{self.tracks[self.fi]['name']} 的切点改了还没保存。\n\n"
                     "「是」保存后离开 ·「否」放弃修改 ·「取消」留在这里", parent=self)
        if ans is None:
            return False
        if ans:
            return self.save(quiet=True)
        return True

    def shutdown(self) -> None:
        try:
            self.player.stop()
            self.player.close()
        except Exception:
            pass
