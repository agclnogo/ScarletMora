"""ScarletPhoneme のメインウィンドウ。

黒地に緋色。上が音庫バー、その下に大きなタブ 3 つ
（切点校对 / 音库索引 / 音库管理）。主要 2 機能はこの窓の中に直接出る。
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import importer, paths
from . import theme as T
from .bank_win import BankPane
from .library import Library
from .review_win import ReviewPane

APP_TITLE = "ScarletPhoneme"
SUB = "音素裁切音库管理器"

TABS = (
    ("review", "切点校对", "改切点 / 试听"),
    ("bank", "音库索引", "按音试听"),
    ("manage", "音库管理", "导入 / 分析 / 导出"),
)


def _hms(sec: float) -> str:
    """秒 → 「1分05秒」/「12秒」。残り時間表示用。"""
    s = int(max(0.0, sec))
    if s < 60:
        return f"{s}秒"
    return f"{s // 60}分{s % 60:02d}秒"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.configure(bg=T.BG0)
        self.title(f"{APP_TITLE} — {SUB}")
        self.geometry("1440x900")
        self.minsize(1180, 760)
        T.apply(self)

        self.lib: Library | None = None
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.tab = "review"
        self._panes: dict[str, tk.Frame] = {}
        self._logs: list[str] = []
        self._logwin: tk.Toplevel | None = None

        self._build_ui()
        T.dark_titlebar(self)
        self._load_libraries()
        self.after(120, self._pump)
        self.bind("<Control-s>", lambda _e: self._key("save"))
        self.bind("<Control-z>", lambda _e: self._key("undo"))
        self.bind("<space>", lambda _e: self._key("space"))
        self.bind("<Prior>", lambda _e: self._key("prev_file"))
        self.bind("<Next>", lambda _e: self._key("next_file"))
        self.bind("<Control-l>", lambda _e: self.show_log())

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        # ---------------- 上: ブランド + 音庫選択
        head = tk.Frame(self, bg=T.BG0)
        head.pack(fill="x", padx=16, pady=(12, 8))
        tk.Label(head, text="●", bg=T.BG0, fg=T.SCARLET, font=("Arial", 16)).pack(side="left")
        tk.Label(head, text=" ScarletPhoneme", bg=T.BG0, fg=T.FG,
                 font=("Microsoft YaHei UI", 17, "bold")).pack(side="left")

        T.ScarletButton(head, "帮助", self.about, kind="flat").pack(side="right")
        T.ScarletButton(head, "检查环境", self.check_env_dialog, kind="flat").pack(
            side="right", padx=6)
        T.ScarletButton(head, "＋ 新建音库", self.new_library, kind="ghost").pack(
            side="right", padx=(10, 6))
        self.v_lib = tk.StringVar()
        self.cmb_lib = ttk.Combobox(head, textvariable=self.v_lib, state="readonly",
                                    width=22, font=T.FONT)
        self.cmb_lib.pack(side="right", padx=(8, 0))
        self.cmb_lib.bind("<<ComboboxSelected>>", self._on_lib_pick)
        tk.Label(head, text="音库", bg=T.BG0, fg=T.DIM, font=T.FONT).pack(side="right")

        # ---------------- タブ
        tabbar = tk.Frame(self, bg=T.BG0)
        tabbar.pack(fill="x", padx=16)
        self.tabs: dict[str, T.NavTab] = {}
        for key, label, sub in TABS:
            t = T.NavTab(tabbar, label, lambda k=key: self.show_tab(k), sub=sub)
            t.pack(side="left", padx=(0, 8))
            self.tabs[key] = t

        # ---------------- 本体
        body = tk.Frame(self, bg=T.BG0)
        body.pack(fill="both", expand=True, padx=16, pady=(10, 6))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.host = body

        # ---------------- 下: 状態 + 進捗
        foot = tk.Frame(self, bg=T.BG2)
        foot.pack(fill="x", side="bottom")
        self.lbl_status = tk.Label(foot, text="就绪", bg=T.BG2, fg=T.FG2, font=T.FONT,
                                   anchor="w")
        self.lbl_status.pack(side="left", padx=12, pady=5)
        self.lbl_prog = tk.Label(foot, text="", bg=T.BG2, fg=T.SCARLET_HI, font=T.FONT_MONO,
                                 width=18, anchor="e")
        self.lbl_prog.pack(side="right", padx=(8, 4), pady=5)
        self.pbar = ttk.Progressbar(foot, mode="determinate", length=220,
                                    style="Scarlet.Horizontal.TProgressbar")
        self.pbar.pack(side="right", padx=8, pady=5)
        T.ScarletButton(foot, "日志", self.show_log, kind="flat").pack(side="right", pady=3)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------ タブ
    def show_tab(self, key: str, force: bool = False) -> None:
        if key == self.tab and not force:
            return
        if not force and not self._leave_current():
            return
        self.tab = key
        for k, t in self.tabs.items():
            t.set_active(k == key)
        for p in self._panes.values():
            p.grid_remove()
        pane = self._pane(key)
        if pane is None:
            if key != "manage" and self.lib and not self.lib.tracks:
                # 曲目が無いなら管理タブへ逃がす（起動時にモーダルで止まらないように）
                self.set_status("这个音库还没有曲目，先去「音库管理」导入素材")
                if self.tab != "manage":
                    self.tab = "manage"
                    for k, t in self.tabs.items():
                        t.set_active(k == "manage")
                    for p in self._panes.values():
                        p.grid_remove()
                    mp = self._pane("manage")
                    if mp is not None:
                        mp.grid(row=0, column=0, sticky="nsew")
            return
        pane.grid(row=0, column=0, sticky="nsew")

    def _leave_current(self) -> bool:
        p = self._panes.get(self.tab)
        if self.tab == "review" and p is not None and not p.can_leave():
            return False
        return True

    def _pane(self, key: str) -> tk.Frame | None:
        if not self.lib:
            messagebox.showinfo("先建音库", "还没有音库。\n点右上「＋ 新建音库」建一个，"
                                            "再到「音库管理」导入素材。", parent=self)
            return None
        cached = self._panes.get(key)
        if cached is not None and cached.winfo_exists():
            if key == "manage":
                cached.refresh()
            return cached
        if key == "review":
            if not self.lib.tracks:
                return None
            p = ReviewPane(self.host, self.lib, log=self.log, on_saved=self._on_saved)
        elif key == "bank":
            if not self.lib.tracks:
                return None
            p = BankPane(self.host, self.lib, log=self.log)
        else:
            p = ManagePane(self.host, self, self.lib)
        self._panes[key] = p
        p.grid(row=0, column=0, sticky="nsew")
        return p

    def _drop_panes(self) -> None:
        for p in list(self._panes.values()):
            try:
                if hasattr(p, "shutdown"):
                    p.shutdown()
                p.destroy()
            except Exception:
                pass
        self._panes.clear()

    def _on_saved(self, name: str) -> None:
        self.set_status(f"已保存 {name} · 切点已写回")
        bank = self._panes.get("bank")
        if bank is not None and bank.winfo_exists():
            bank.reload()
        manage = self._panes.get("manage")
        if manage is not None and manage.winfo_exists():
            manage.refresh()
        self._refresh_tab_subs()

    def _refresh_tab_subs(self) -> None:
        if not self.lib:
            return
        st = self.lib.stats()
        self.tabs["review"].set_active(self.tab == "review",
                                       sub=f"{st['tracks']} 曲 / {st['mora_total']} 音")
        self.tabs["bank"].set_active(self.tab == "bank",
                                     sub=f"{st['mora_kind']} 种 / 可疑 {st['flagged']}")
        self.tabs["manage"].set_active(self.tab == "manage", sub=self.lib.name)

    # ------------------------------------------------------------------ 鍵
    def _key(self, action: str) -> str:
        w = self.focus_get()
        if isinstance(w, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox)):
            return ""
        p = self._panes.get(self.tab)
        if p is None:
            return ""
        if self.tab == "review":
            if action == "space":
                p.toggle_play()
            elif action == "save":
                p.save()
            elif action == "undo":
                p.undo()
            elif action == "prev_file":
                p.step_file(-1)
            elif action == "next_file":
                p.step_file(1)
        elif self.tab == "bank" and action == "space":
            p.play_selected()
        return "break"

    # ------------------------------------------------------------------ ログ
    def log(self, msg: str) -> None:
        self._logs.append(msg)
        if len(self._logs) > 2000:
            self._logs.pop(0)
        self.set_status(msg)
        if self._logwin is not None and self._logwin.winfo_exists():
            self._logwin.append(msg)

    def set_status(self, msg: str) -> None:
        self.q.put(("status", msg))

    def show_log(self) -> None:
        if self._logwin is not None and self._logwin.winfo_exists():
            self._logwin.lift()
            self._logwin.focus_force()
            return
        self._logwin = LogWindow(self, self._logs)

    # ------------------------------------------------------------------ ポンプ
    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "status":
                    self.lbl_status.configure(text=payload)
                elif kind == "stage":
                    # 工程の切れ目（模型読み込み / 索引 / 切片 …）。ログにも残して固まってない事を示す
                    self.log(payload)
                    self.lbl_prog.configure(text="…")
                elif kind == "progress":
                    done, total, text = payload
                    if str(self.pbar.cget("mode")) != "determinate":
                        self.pbar.stop()
                        self.pbar.configure(mode="determinate")
                    if total:
                        self.pbar.configure(maximum=total, value=done)
                        self.lbl_prog.configure(
                            text=f"{done}/{total} · {int(100 * done / total)}%")
                    self.lbl_status.configure(text=text)
                elif kind == "refresh":
                    self._refresh_after_work()
                elif kind == "busy":
                    self.set_busy(payload)
                elif kind == "done_import":
                    self._after_import(payload)
                elif kind == "done_analyze":
                    self._after_analyze(payload)
        except queue.Empty:
            pass
        self.after(120, self._pump)

    def _refresh_after_work(self) -> None:
        if not self.lib:
            return
        self.lib.load(reload=True)
        manage = self._panes.get("manage")
        if manage is not None and manage.winfo_exists():
            manage.refresh()
        review = self._panes.get("review")
        if review is not None and review.winfo_exists():
            review.refresh_tracks()
        self._refresh_tab_subs()

    def set_busy(self, flag: bool) -> None:
        self.busy = flag
        if flag:
            # 何も流れてこない間（模型読み込みなど）も「動いてる」と分かるように
            self.pbar.stop()
            self.pbar.configure(mode="indeterminate", value=0)
            self.pbar.start(14)
            self.lbl_prog.configure(text="准备中…")
        else:
            self.pbar.stop()
            self.pbar.configure(mode="determinate", maximum=100, value=0)
            self.lbl_prog.configure(text="")
        self.configure(cursor="watch" if flag else "")

    # ------------------------------------------------------------------ 音庫
    def _load_libraries(self) -> None:
        libs = Library.list_all()
        names = [l.name for l in libs]
        self.cmb_lib.configure(values=names)
        last = (paths.load_settings().get("last_library") or "").strip()
        pick = None
        for l in libs:
            if os.path.abspath(l.path) == os.path.abspath(last):
                pick = l
        if pick is None and libs:
            pick = libs[0]
        if pick is None:
            self.lib = None
            self.v_lib.set("")
            self.set_status("还没有音库：点右上「＋ 新建音库」")
            return
        self.lib = pick
        self.v_lib.set(pick.name)
        pick.touch()
        self._refresh_tab_subs()
        self.show_tab(self.tab, force=True)

    def _on_lib_pick(self, _e=None) -> None:
        name = self.v_lib.get()
        for l in Library.list_all():
            if l.name == name:
                if self.lib and os.path.abspath(l.path) == os.path.abspath(self.lib.path):
                    return
                if not self._leave_current():
                    self.v_lib.set(self.lib.name if self.lib else "")
                    return
                self._drop_panes()
                self.lib = l
                l.touch()
                self._refresh_tab_subs()
                self.show_tab(self.tab, force=True)
                self.set_status(f"切换到音库 {l.name}")
                return

    def new_library(self) -> None:
        NewLibraryDialog(self)

    def _do_create(self, name: str, mode: str, then_import: bool) -> None:
        if self.lib and not self._leave_current():
            return
        try:
            lib = Library.create(name, audio_mode=mode)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("新建失败", str(e), parent=self)
            return
        self._drop_panes()
        self.log(f"音库を作成: {lib.name}")
        self.lib = lib
        self._load_libraries()
        self.show_tab("manage", force=True)
        if then_import:
            self.import_dialog()

    def import_dialog(self) -> None:
        if not self.lib:
            messagebox.showinfo("先建音库", "先新建或选择一个音库。", parent=self)
            return
        ImportDialog(self, self.lib)

    def _after_import(self, report: dict) -> None:
        added = len(report.get("added", []))
        rep = len(report.get("replaced", []))
        skip = len(report.get("skipped", []))
        self._refresh_after_work()
        self.log(f"导入完成: 新增 {added} / 更新 {rep} / 跳过 {skip}")
        for f in report.get("failed", [])[:3]:
            self.log(f"  失败: {f}")
        if added + rep and messagebox.askyesno(
                "导入完成", f"新增 {added} 本、更新 {rep} 本。\n\n现在就做 CTC 对齐分析吗？",
                parent=self):
            self.analyze_current()

    def open_folder(self, path: str) -> None:
        if not path or not os.path.isdir(path):
            return
        try:
            os.startfile(path)          # noqa: S606
        except Exception:
            subprocess.Popen(["explorer", path])

    def open_track(self, name: str) -> None:
        self.show_tab("review")
        p = self._panes.get("review")
        if p is not None and p.winfo_exists():
            p.select_track(name)

    # ------------------------------------------------------------------ 解析
    def check_env_dialog(self) -> None:
        py = paths.find_python()
        ok, msg = paths.has_torch(py)
        txt = f"Python: {py or '（未找到）'}\n检查: {msg}"
        txt += ("\n\n可以运行 CTC 对齐分析。" if ok else
                "\n\n分析功能需要 torch / transformers / numpy。\n"
                "安装（命令提示符里）：\n    pip install torch transformers numpy")
        messagebox.showinfo("解析环境", txt, parent=self)

    def analyze_current(self, only: list[str] | None = None) -> None:
        lib = self.lib
        if not lib or self.busy:
            return
        py = paths.find_python()
        if not py:
            messagebox.showerror("找不到 Python", "分析需要本机的 Python + torch。", parent=self)
            return
        ok, msg = paths.has_torch(py, timeout=30)
        if not ok:
            messagebox.showerror("缺少依赖", f"{py}\n{msg}\n\n需要: pip install torch "
                                            "transformers numpy", parent=self)
            return
        if not os.path.isdir(paths.default_model_dir()):
            messagebox.showerror("缺少模型", "找不到声学模型：\n" +
                                 paths.default_model_dir(), parent=self)
            return
        if not only:
            todo = [t for t in lib.tracks if (t.get("text") or "").strip()]
            if not todo:
                messagebox.showinfo("没有可分析的曲目", "这些曲目都没有原文。", parent=self)
                return
            if not messagebox.askyesno("开始分析",
                                       f"对 {len(todo)} 本曲目做 CTC 对齐？\n"
                                       "全部重算会覆盖现有切点（校对过的也会没）。\n\n"
                                       "选「否」= 只算还没分析的。", parent=self):
                pending = [t["name"] for t in todo
                           if not os.path.exists(lib.marks_path(t["name"]))]
                if not pending:
                    self.log("没有未分析的曲目。")
                    return
                only = pending

        worker = paths.worker_script()
        if not os.path.isfile(worker):
            messagebox.showerror("找不到分析脚本", "需要磁盘上的 worker.py：\n" + worker +
                                 "\n\nexe 旁边的 app\\scarlet\\ 文件夹别删。", parent=self)
            return
        args = [py, "-u", worker, "align", "--library", lib.path,
                "--shift", str(lib.settings.get("shift", 0.02))]
        if only:
            args += ["--only", ",".join(only)]
        self.set_busy(True)
        n = len(only or lib.tracks)
        self.pbar.configure(value=0, maximum=max(1, n))
        self.lbl_prog.configure(text=f"0/{n}")
        self.log(f"开始分析 {n} 本（先加载模型，约 10~30 秒后才出第一条）…")
        threading.Thread(target=self._run_worker, args=(args, lib), daemon=True).start()

    def _run_worker(self, args: list[str], lib: Library) -> None:
        ok = 0
        noise = 0          # 既知の書式でない行（Python の起動失敗など）を何行か見せる
        try:
            flags = 0x08000000 if os.name == "nt" else 0
            p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace",
                                 bufsize=1, creationflags=flags)
            assert p.stdout is not None
            t0 = time.monotonic()
            sum_mora = sum_clips = 0
            for line in p.stdout:
                line = line.rstrip()
                if not line:
                    continue
                if line.startswith("PROGRESS "):
                    parts = line.split(" ", 3)
                    try:
                        done, total, name = int(parts[1]), int(parts[2]), parts[3]
                    except Exception:
                        continue
                    spent = time.monotonic() - t0
                    done_before = max(0, done - 1)          # まだ処理中の 1 本は除く
                    per = spent / done_before if done_before else 0.0
                    eta = per * (total - done_before)
                    self.q.put(("progress", (
                        done - 1, total,
                        f"分析中 {done}/{total} · 已用 {_hms(spent)} · "
                        f"剩余约 {_hms(eta)} · {name}")))
                elif line.startswith("STAGE "):
                    self.q.put(("stage", line[6:]))
                elif line.startswith("CLIP "):
                    parts = line.split(" ")
                    try:
                        i, total = int(parts[1]), int(parts[2])
                    except Exception:
                        continue
                    self.q.put(("progress", (i, total, f"导出切片 {i}/{total}")))
                elif line.startswith("SUM "):
                    parts = line.split(" ")
                    try:
                        sum_mora, sum_clips = int(parts[2]), int(parts[3])
                    except Exception:
                        pass
                elif line.startswith("OK "):
                    ok += 1
                    self.log(line)
                elif line.startswith(("WARN", "ERROR")):
                    self.log(line)
                elif noise < 5:                       # 想定外の行は数行だけ出す
                    noise += 1
                    self.log("解析输出: " + line)
            p.wait()
            if ok == 0 and p.returncode:
                self.log(f"分析没有成功（退出码 {p.returncode}），见上面日志")
        except Exception as e:  # noqa: BLE001
            self.log("分析进程失败: " + str(e))
            self.log(traceback.format_exc()[-400:])
        self.q.put(("done_analyze", {"ok": ok, "lib": lib,
                                     "mora": sum_mora, "clips": sum_clips}))

    def _after_analyze(self, info: dict) -> None:
        self.set_busy(False)
        try:
            info["lib"].load(reload=True)
            info["lib"].write_project()
        except Exception:  # noqa: BLE001
            pass
        self.q.put(("refresh", None))
        ok = info["ok"]
        mora, clips = info.get("mora", 0), info.get("clips", 0)
        self.log(f"分析结束: 成功 {ok} 本 · {mora} 音 · 切片 {clips} 个")
        if ok:
            messagebox.showinfo(
                "分析完成",
                f"成功 {ok} 本\n共 {mora} 个音（モーラ）\n切片 {clips} 个\n\n"
                f"已写好: bank.csv / bank_summary.txt / report.txt /\n"
                f"clips\\ / clips_all\\ / clips.csv\n\n"
                f"接下来去「切点校对」按置信度从低往上修。",
                parent=self)
        else:
            messagebox.showwarning(
                "分析没有成功",
                "一本都没分析出来。点右下「日志」看细节。\n"
                "常见原因: 没装 torch / 模型文件夹被移动 / 曲目没有原文。",
                parent=self)

    # ------------------------------------------------------------------ 索引
    def rebuild_index(self) -> None:
        lib = self.lib
        if not lib or self.busy:
            return
        self.set_busy(True)
        self.log("正在重建索引…")

        def work():
            try:
                r = lib.build_bank()
                self.log(f"索引完成: {len(r.get('rows', []))} 行 / "
                         f"{len(r.get('count', {}))} 种音")
            except Exception as e:  # noqa: BLE001
                self.log(f"索引失败: {e}")
            self.q.put(("busy", False))
            self.q.put(("refresh", None))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ 切片
    def export_clips(self) -> None:
        lib = self.lib
        if not lib or self.busy:
            return
        s = lib.settings
        dlg = tk.Toplevel(self, bg=T.BG0)
        dlg.title("导出切片")
        dlg.transient(self)
        dlg.grab_set()
        T.dark_titlebar(dlg)
        frm = tk.Frame(dlg, bg=T.BG0, padx=18, pady=16)
        frm.pack(fill="both", expand=True)
        tk.Label(frm, text="导出切片", bg=T.BG0, fg=T.FG,
                 font=("Microsoft YaHei UI", 16, "bold")).grid(row=0, column=0, columnspan=2,
                                                              sticky="w", pady=(0, 12))
        vars_ = {}
        for i, (lbl, key, val) in enumerate(
                [("切片前余白 (秒)", "pad_head", s["pad_head"]),
                 ("切片后余白 (秒)", "pad_tail", s["pad_tail"]),
                 ("边界整体后移 (秒)", "shift", 0.0)], start=1):
            tk.Label(frm, text=lbl, bg=T.BG0, fg=T.FG2, font=T.FONT).grid(
                row=i, column=0, sticky="w", pady=5)
            v = tk.StringVar(value=str(val))
            ttk.Entry(frm, textvariable=v, width=12).grid(row=i, column=1, sticky="w", padx=10)
            vars_[key] = v
        keep_all = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="同时保留全部候选（clips_all）", variable=keep_all).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=10)

        def go():
            try:
                ph = float(vars_["pad_head"].get())
                pt = float(vars_["pad_tail"].get())
                sh = float(vars_["shift"].get())
            except ValueError:
                messagebox.showerror("数值错误", "请输入数字", parent=dlg)
                return
            dlg.destroy()
            self.set_busy(True)

            def work():
                try:
                    r = lib.export_clips(pad_head=ph, pad_tail=pt, shift=sh,
                                         all_takes=keep_all.get())
                    self.log(f"切片完成: {r['total']} 本 / {r['mora']} 种")
                    self.open_folder(r["dir_best"])
                except Exception as e:  # noqa: BLE001
                    self.log(f"切片失败: {e}")
                self.q.put(("busy", False))

            threading.Thread(target=work, daemon=True).start()

        bar = tk.Frame(frm, bg=T.BG0)
        bar.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        T.ScarletButton(bar, "导出", go).pack(side="right")
        T.ScarletButton(bar, "取消", dlg.destroy, kind="flat").pack(side="right", padx=8)

    # ------------------------------------------------------------------ 拼接
    def render_song(self) -> None:
        lib = self.lib
        if not lib or self.busy:
            return
        lyrics = filedialog.askopenfilename(
            parent=self, title="选择歌词文本（每行一句）",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not lyrics:
            return
        self.set_busy(True)
        self.log(f"开始拼接: {os.path.basename(lyrics)}")

        def work():
            try:
                plan = lib.make_plan(lyrics)
                self.log("拼句表: " + plan["dir"])
                r = lib.render(lyrics)
                self.log(f"拼接完成: {r['lines']} 句 / {r['segments']} 音 / "
                         f"{r['duration']:.1f}s")
                if r.get("subs"):
                    self.log("  代用: " + ", ".join(f"{k} x{v}" for k, v in r["subs"].items()))
                if r.get("missing"):
                    self.log("  缺失: " + ", ".join(f"{k} x{v}" for k, v in r["missing"].items()))
                self.open_folder(r["dir"])
            except Exception as e:  # noqa: BLE001
                self.log(f"拼接失败: {e}")
            self.q.put(("busy", False))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ 其他
    def about(self) -> None:
        from . import __version__
        messagebox.showinfo(
            "关于", f"{APP_TITLE} {__version__}\n{SUB}\n\n"
                    "素材 → 强制对齐 → 切点校对 → 切片 → 拼接\n"
                    "快捷键：空格 播放/暂停 · Ctrl+S 保存 · Ctrl+Z 撤销 · "
                    "PageUp/PageDown 换曲 · Ctrl+L 日志\n\n"
                    f"数据目录: {paths.app_root()}",
            parent=self)

    def on_close(self) -> None:
        if not self._leave_current():
            return
        self._drop_panes()
        self.destroy()


# ====================================================================== 音库管理
class ManagePane(tk.Frame):
    """音库の情報 + 取り込み/解析/書き出し。曲目一覧もここ。"""

    def __init__(self, master, app: App, lib: Library):
        super().__init__(master, bg=T.BG0)
        self.app = app
        self.lib = lib
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        cpanel = T.RoundPanel(self)
        cpanel.grid(row=0, column=0, sticky="ew")
        card = cpanel.body
        card.columnconfigure(0, weight=1)
        self.lbl_name = tk.Label(card, text=lib.name, bg=T.BG1, fg=T.FG,
                                 font=("Microsoft YaHei UI", 20, "bold"), anchor="w")
        self.lbl_name.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 0))
        self.lbl_meta = tk.Label(card, text="", bg=T.BG1, fg=T.DIM, font=T.FONT_MONO,
                                 anchor="w")
        self.lbl_meta.grid(row=1, column=0, sticky="w", padx=16)
        self.lbl_stat = tk.Label(card, text="", bg=T.BG1, fg=T.FG2, font=T.FONT,
                                 anchor="w", justify="left")
        self.lbl_stat.grid(row=2, column=0, sticky="w", padx=16, pady=(6, 12))
        cpanel.fit()

        acts = tk.Frame(self, bg=T.BG0)
        acts.grid(row=1, column=0, sticky="ew", pady=10)
        for i in range(4):
            acts.columnconfigure(i, weight=1)
        grid = [
            ("导入素材", self.app.import_dialog, "solid"),
            ("重新分析", self.app.analyze_current, "solid"),
            ("重建索引", self.app.rebuild_index, "ghost"),
            ("导出切片", self.app.export_clips, "ghost"),
            ("拼接成歌", self.app.render_song, "ghost"),
            ("打开文件夹", lambda: self.app.open_folder(self.lib.path), "ghost"),
            ("删除音库", self._delete, "danger"),
        ]
        for i, (text, cmd, kind) in enumerate(grid):
            T.ScarletButton(acts, text, cmd, kind=kind).grid(
                row=i // 4, column=i % 4, sticky="ew", padx=3, pady=3)

        head = tk.Frame(self, bg=T.BG0)
        head.grid(row=2, column=0, sticky="ew", pady=(4, 4))
        tk.Label(head, text="曲目", bg=T.BG0, fg=T.SCARLET_HI, font=T.FONT_B).pack(side="left")
        tk.Label(head, text="双击一行 → 进切点校对", bg=T.BG0, fg=T.DIM,
                 font=T.FONT).pack(side="right")

        wpanel = T.RoundPanel(self)
        wpanel.grid(row=3, column=0, sticky="nsew")
        wrap = wpanel.body
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        cols = ("name", "dur", "status", "conf", "moras", "text")
        self.tbl = ttk.Treeview(wrap, columns=cols, show="headings")
        for c, t, w, anchor in (("name", "文件", 190, "w"), ("dur", "长度", 80, "center"),
                                ("status", "状态", 86, "center"), ("conf", "置信", 70, "center"),
                                ("moras", "音数", 70, "center"), ("text", "原文", 520, "w")):
            self.tbl.heading(c, text=t)
            self.tbl.column(c, width=w, anchor=anchor, stretch=(c == "text"))
        self.tbl.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tbl.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=1)
        self.tbl.configure(yscrollcommand=sb.set)
        self.tbl.bind("<Double-Button-1>", self._open_row)
        self.refresh()

    def _open_row(self, _e=None) -> None:
        sel = self.tbl.selection()
        if sel:
            self.app.open_track(self.tbl.item(sel[0], "values")[0])

    def _delete(self) -> None:
        if not messagebox.askyesno("删除音库",
                                   f"「{self.lib.name}」を削除します。\n"
                                   f"音声・切点・書き出しがすべて消えます。よろしいですか？",
                                   icon="warning", parent=self):
            return
        self.lib.delete()
        self.app.log(f"删除音库 {self.lib.name}")
        self.app.lib = None
        self.app._drop_panes()
        self.app._load_libraries()

    def refresh(self) -> None:
        self.lib.load(reload=True)
        st = self.lib.stats()
        mode = "复制进音库" if st["audio_mode"] == "copy" else "引用原位置"
        self.lbl_name.configure(text=st["name"])
        self.lbl_meta.configure(text=f"{st['path']}    [{mode}]")
        self.lbl_stat.configure(
            text=f"曲目 {st['tracks']}   已分析 {st['analyzed']}   "
                 f"音 {st['mora_total']}（{st['mora_kind']} 种）   可疑 {st['flagged']}   "
                 f"时长 {st['duration']}s   {st['size_mb']}MB   更新 {st['updated']}")
        self.tbl.delete(*self.tbl.get_children())
        label = {"analyzed": "已分析", "imported": "未分析", "failed": "失败"}
        for t in self.lib.tracks:
            mp = self.lib.marks_path(t["name"])
            n = 0
            if os.path.exists(mp):
                try:
                    n = max(0, sum(1 for _ in open(mp, encoding="utf-8-sig")) - 1)
                except Exception:
                    n = 0
            self.tbl.insert("", "end", values=(
                t.get("name", ""),
                f"{float(t.get('duration') or 0):.2f}s",
                label.get(t.get("status", ""), t.get("status", "")),
                f"{float(t.get('avg_conf') or 0):.2f}",
                n,
                (t.get("text") or "")[:80],
            ))


# ====================================================================== 日志窗
class LogWindow(tk.Toplevel):
    def __init__(self, master, lines: list[str]):
        super().__init__(master, bg=T.BG0)
        self.title("日志 · ScarletPhoneme")
        self.geometry("760x460")
        self.transient(master)
        T.dark_titlebar(self)
        frm = tk.Frame(self, bg=T.BG0, padx=12, pady=10)
        frm.pack(fill="both", expand=True)
        tk.Label(frm, text="操作记录", bg=T.BG0, fg=T.SCARLET_HI, font=T.FONT_B).pack(
            anchor="w", pady=(0, 6))
        self.txt = ScrolledText(frm, wrap="word", bg=T.BG2, fg=T.FG2, bd=0, relief="flat",
                                insertbackground=T.SCARLET_HI, font=T.FONT_MONO)
        self.txt.pack(fill="both", expand=True)
        T.style_scrollbar(self.txt.vbar)
        self.txt.insert("end", "\n".join(lines) + ("\n" if lines else "（暂时没有记录）\n"))
        self.txt.configure(state="disabled")
        self.txt.see("end")

    def append(self, msg: str) -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", msg + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")


# ====================================================================== 对话框
class NewLibraryDialog(tk.Toplevel):
    def __init__(self, app: App):
        super().__init__(app, bg=T.BG0)
        self.app = app
        self.title("新建音库")
        self.transient(app)
        self.grab_set()
        self.resizable(False, False)
        T.dark_titlebar(self)
        frm = tk.Frame(self, bg=T.BG0, padx=18, pady=16)
        frm.pack(fill="both", expand=True)

        tk.Label(frm, text="新建音库", bg=T.BG0, fg=T.FG,
                 font=("Microsoft YaHei UI", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        tk.Label(frm, text="名称", bg=T.BG0, fg=T.FG2, font=T.FONT).grid(
            row=1, column=0, sticky="w", pady=5)
        self.name = tk.StringVar(value="新音库")
        e = ttk.Entry(frm, textvariable=self.name, width=34, font=T.FONT)
        e.grid(row=1, column=1, sticky="ew", pady=5, padx=10)
        e.focus_set()
        e.select_range(0, "end")

        tk.Label(frm, text="音频存放", bg=T.BG0, fg=T.FG2, font=T.FONT).grid(
            row=2, column=0, sticky="nw", pady=(12, 5))
        self.mode = tk.StringVar(value="copy")
        mf = tk.Frame(frm, bg=T.BG0)
        mf.grid(row=2, column=1, sticky="w", padx=10, pady=(12, 5))
        ttk.Radiobutton(mf, text="复制进音库（推荐，可整体搬走）", value="copy",
                        variable=self.mode).pack(anchor="w")
        ttk.Radiobutton(mf, text="只引用原位置（省空间，原文件动了就断）", value="reference",
                        variable=self.mode).pack(anchor="w")

        self.then_import = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="建好后马上导入素材", variable=self.then_import).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(14, 0))

        bar = tk.Frame(frm, bg=T.BG0)
        bar.grid(row=4, column=0, columnspan=2, sticky="e", pady=(18, 0))
        T.ScarletButton(bar, "创建", self.ok).pack(side="right")
        T.ScarletButton(bar, "取消", self.destroy, kind="flat").pack(side="right", padx=8)
        self.bind("<Return>", lambda _e: self.ok())
        self.bind("<Escape>", lambda _e: self.destroy())

    def ok(self) -> None:
        name = (self.name.get() or "").strip()
        if not name:
            messagebox.showerror("名称不能为空", "给音库起个名字。", parent=self)
            return
        flag, mode = self.then_import.get(), self.mode.get()
        self.destroy()
        self.app._do_create(name, mode, flag)


class ImportDialog(tk.Toplevel):
    """素材の取り込みウィザード。"""

    def __init__(self, app: App, lib: Library):
        super().__init__(app, bg=T.BG0)
        self.app = app
        self.lib = lib
        self.files: list[str] = []
        self.report: dict | None = None
        self.title(f"导入素材 — {lib.name}")
        self.transient(app)
        self.grab_set()
        self.geometry("940x700")
        T.dark_titlebar(self)

        frm = tk.Frame(self, bg=T.BG0, padx=16, pady=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(6, weight=1)

        tk.Label(frm, text="导入素材", bg=T.BG0, fg=T.FG,
                 font=("Microsoft YaHei UI", 14, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        tk.Label(frm, text="音频", bg=T.BG0, fg=T.FG2, font=T.FONT).grid(
            row=1, column=0, sticky="w")
        self.folder = tk.StringVar()
        ttk.Entry(frm, textvariable=self.folder, font=T.FONT).grid(
            row=1, column=1, sticky="ew", padx=8)
        b1 = tk.Frame(frm, bg=T.BG0)
        b1.grid(row=1, column=2)
        T.ScarletButton(b1, "选文件夹", self.pick_folder, kind="flat").pack(side="left")
        T.ScarletButton(b1, "选文件", self.pick_files, kind="flat").pack(side="left", padx=4)

        tk.Label(frm, text="原文", bg=T.BG0, fg=T.FG2, font=T.FONT).grid(
            row=2, column=0, sticky="w", pady=(8, 0))
        self.text_path = tk.StringVar()
        ttk.Entry(frm, textvariable=self.text_path, font=T.FONT).grid(
            row=2, column=1, sticky="ew", padx=8, pady=(8, 0))
        T.ScarletButton(frm, "选文本", self.pick_text, kind="flat").grid(row=2, column=2,
                                                                       pady=(8, 0))

        opts = tk.Frame(frm, bg=T.BG0)
        opts.grid(row=3, column=0, columnspan=3, sticky="w", pady=10)
        self.recursive = tk.BooleanVar(value=False)
        self.copy_audio = tk.BooleanVar(value=(lib.audio_mode == "copy"))
        self.conflict = tk.StringVar(value="skip")
        ttk.Checkbutton(opts, text="包含子文件夹", variable=self.recursive).pack(side="left")
        ttk.Checkbutton(opts, text="复制音频进音库", variable=self.copy_audio).pack(
            side="left", padx=14)
        tk.Label(opts, text="重名时", bg=T.BG0, fg=T.FG2, font=T.FONT).pack(
            side="left", padx=(14, 4))
        ttk.Radiobutton(opts, text="跳过", value="skip", variable=self.conflict).pack(side="left")
        ttk.Radiobutton(opts, text="覆盖", value="replace", variable=self.conflict).pack(side="left")

        tk.Label(frm, text="原文格式：文件名<TAB>原文（一行一本），或 文件名/换行/原文 两行一本。"
                           "没有文件名时按文件名顺序对应。",
                  bg=T.BG0, fg=T.DIM, font=T.FONT, wraplength=860, justify="left").grid(
            row=4, column=0, columnspan=3, sticky="w")

        b2 = tk.Frame(frm, bg=T.BG0)
        b2.grid(row=5, column=0, columnspan=3, sticky="ew", pady=10)
        T.ScarletButton(b2, "预览匹配", self.preview, kind="ghost").pack(side="left")
        self.btn_import = T.ScarletButton(b2, "开始导入", self.do_import)
        self.btn_import.pack(side="left", padx=8)
        self.btn_import.configure(state="disabled")

        self.out = ScrolledText(frm, wrap="word", bg=T.BG1, fg=T.FG2, bd=0, relief="flat",
                                insertbackground=T.SCARLET_HI, font=T.FONT_MONO)
        self.out.grid(row=6, column=0, columnspan=3, sticky="nsew")
        T.style_scrollbar(self.out.vbar)
        self.out.insert("end", "选好音频（和原文）后点「预览匹配」。\n")
        self.out.configure(state="disabled")

        bar = tk.Frame(frm, bg=T.BG0)
        bar.grid(row=7, column=0, columnspan=3, sticky="e", pady=(10, 0))
        T.ScarletButton(bar, "关闭", self.destroy, kind="flat").pack(side="right")

    def pick_folder(self) -> None:
        d = filedialog.askdirectory(parent=self, title="选音频文件夹")
        if d:
            self.folder.set(d)
            self.files = []
            self.preview()

    def pick_files(self) -> None:
        fs = filedialog.askopenfilenames(parent=self, title="选音频文件",
                                         filetypes=[("WAV", "*.wav"), ("所有文件", "*.*")])
        if fs:
            self.files = list(fs)
            self.folder.set(f"（已选 {len(fs)} 个文件）")
            self.preview()

    def pick_text(self) -> None:
        f = filedialog.askopenfilename(parent=self, title="选原文文本",
                                       filetypes=[("文本", "*.txt"), ("所有文件", "*.*")])
        if f:
            self.text_path.set(f)
            self.preview()

    def _say(self, text: str) -> None:
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("end", text)
        self.out.configure(state="disabled")

    def preview(self) -> None:
        folder = self.folder.get() if os.path.isdir(self.folder.get()) else ""
        try:
            r = importer.collect(folder=folder, files=self.files,
                                 text_path=self.text_path.get(),
                                 recursive=self.recursive.get())
        except Exception as e:  # noqa: BLE001
            self._say(f"读取失败: {e}")
            self.btn_import.configure(state="disabled")
            return
        self.report = r
        lines = importer.describe(r)
        if r["matched"]:
            lines += ["", "前 15 本："]
            for m in r["matched"][:15]:
                lines.append(f"  {m['name']}  ←  {(m['text'] or '（无原文）')[:56]}")
        self._say("\n".join(lines))
        self.btn_import.configure(state="normal" if r["matched"] else "disabled")

    def do_import(self) -> None:
        if not self.report or not self.report["matched"]:
            return
        items = [{"name": m["name"], "text": m["text"], "audio": m["audio"]}
                 for m in self.report["matched"]]
        if self.text_path.get() and os.path.isfile(self.text_path.get()):
            try:
                self.lib.save_transcript_copy(importer.read_text(self.text_path.get()))
            except Exception:  # noqa: BLE001
                pass
        self.btn_import.configure(state="disabled")
        copy, conflict = self.copy_audio.get(), self.conflict.get()
        self._say(f"正在导入 {len(items)} 本…（复制音频={copy}）")
        app = self.app

        def work():
            try:
                rep = self.lib.import_items(items, copy_audio=copy, on_conflict=conflict)
            except Exception as e:  # noqa: BLE001
                app.log(f"导入失败: {e}")
                rep = {"added": [], "replaced": [], "skipped": [], "failed": [str(e)]}
            app.q.put(("done_import", rep))

        threading.Thread(target=work, daemon=True).start()
        self.after(400, self.destroy)


# ====================================================================== 入口
def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        return selftest()
    app = App()
    app.mainloop()
    return 0


def selftest() -> int:
    """exe 動作確認用。画面は出さずに主要部品を点検し logs/selftest.txt に残す。"""
    out: list[str] = []

    def say(msg: str) -> None:
        out.append(msg)
        try:
            print(msg, flush=True)
        except Exception:
            pass

    ok_all = True
    say(f"frozen={paths.is_frozen()}")
    say(f"root={paths.app_root()}")
    say(f"python={paths.find_python()}")
    wpath = paths.worker_script()
    say(f"worker={wpath} ({'ok' if os.path.isfile(wpath) else 'MISSING'})")
    if not os.path.isfile(wpath):
        ok_all = False
    libs = [l for l in Library.list_all() if l.tracks] or Library.list_all()
    say(f"libraries: {len(libs)}")
    if libs:
        lib = libs[0]
        st = lib.stats()
        say(f"first: {lib.name} tracks={st['tracks']} moras={st['mora_total']}")
        try:
            from .core.audio import read_audio
            from .player import make_player

            t = lib.tracks[0]
            au = read_audio(lib.audio_path(t))
            pl = make_player()
            say(f"  audio {au.duration:.2f}s @ {au.sr}Hz · player "
                f"{'MCI' if pl.available() else 'none'}")
            if pl.available():
                pl.open(lib.audio_path(t))
                say(f"  player length {pl.length:.2f}s")
                pl.close()
        except Exception as e:  # noqa: BLE001
            ok_all = False
            say(f"  FAIL {type(e).__name__}: {e}")
        try:
            app = App()
            app.update_idletasks()
            app.update()
            for key in ("review", "bank", "manage"):
                app.show_tab(key, force=True)
                app.update_idletasks()
                app.update()
                p = app._panes.get(key)
                say(f"  pane {key}: {'ok' if p is not None else 'missing'}")
            rv = app._panes.get("review")
            if rv is not None:
                say(f"  review marks={len(rv.marks)}")
                # 波形の当たり判定: 本体=再生位置だけ / 音名帯の線=切点ドラッグ
                try:
                    app.show_tab("review", force=True)
                    app.update_idletasks()
                    app.update()
                    wv = rv.wave
                    wv.update_idletasks()
                    wv.update()
                    _w, _top, _h = wv._geo()
                    if rv.marks:
                        i = min(5, len(rv.marks) - 1)
                        tgt = (rv.marks[i].start + rv.marks[i].end) / 2
                        before = [(m.start, m.end) for m in rv.marks]
                        wv.event_generate("<Button-1>", x=max(1, int(wv.t2x(tgt))),
                                          y=int(_top + _h / 2))
                        app.update_idletasks()
                        app.update()
                        ok = (wv._drag is None and abs(wv.playhead - tgt) < 0.05
                              and [(m.start, m.end) for m in rv.marks] == before)
                        say(f"  wave body-click {'ok' if ok else 'FAIL'}")
                        ok_all = ok_all and ok
                        j = min(7, len(rv.marks) - 1)
                        bx = int(wv.t2x(rv.marks[j].end))
                        by = int(_top + _h + 12)
                        ph = wv.playhead
                        wv.event_generate("<Button-1>", x=bx, y=by)
                        app.update_idletasks()
                        app.update()
                        ok2 = wv._drag is not None and abs(wv.playhead - ph) <= 1e-9
                        say(f"  wave strip-grab {'ok' if ok2 else 'FAIL'}")
                        ok_all = ok_all and ok2
                        wv.event_generate("<ButtonRelease-1>", x=bx, y=by)
                except Exception as e:  # noqa: BLE001
                    ok_all = False
                    say(f"  wave FAIL {type(e).__name__}: {e}")
            bk = app._panes.get("bank")
            if bk is not None:
                say(f"  bank takes={len(bk.rows)} kinds={len(bk.by_mora)}")
            app._drop_panes()
            app.destroy()
        except Exception as e:  # noqa: BLE001
            ok_all = False
            say(f"  window FAIL {type(e).__name__}: {e}")
            say(traceback.format_exc()[-400:])
    else:
        say("SKIP: no library")

    out.append("SELFTEST OK" if ok_all else "SELFTEST FAILED")
    try:
        with open(os.path.join(paths.logs_dir(), "selftest.txt"), "w", encoding="utf-8") as f:
            f.write(chr(10).join(out) + chr(10))
    except Exception:
        pass
    print(out[-1], flush=True)
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
