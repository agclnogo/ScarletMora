"""見た目: 黒地 + 緋色（スカーレット）のテーマ。

色は全部ここに集約。ボタンは tk.Button を薄くラップして
「緋色の塗り」「黒地に緋色の枠」の 2 種類を使い分ける。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ------------------------------------------------------------------ 色
BG0 = "#0f0c0d"          # 一番下（窓）
BG1 = "#171314"          # パネル
BG2 = "#201a1b"          # 一段浮いた面
BG3 = "#2a2223"          # 入力欄・ホバー
LINE = "#332628"         # 罫線
LINE2 = "#4a3438"
FG = "#f6f0ef"
FG2 = "#d9cdca"
DIM = "#b3a29f"

SCARLET = "#e0183c"
SCARLET_HI = "#ff3355"
SCARLET_LO = "#8c0f24"
SCARLET_DEEP = "#3a0c14"

OK = "#57c98a"
WARN = "#f0a742"
BAD = "#ff5566"
BAR = "#7a649700"

# 波形
WAVE = "#6f5b5e"
WAVE_HI = "#c9b7b9"
GRID = "#241d1e"
SEL = "#ff3355"

FONT_S = ("Microsoft YaHei UI", 10)
FONT = ("Microsoft YaHei UI", 11)
FONT_B = ("Microsoft YaHei UI", 11, "bold")
FONT_T = ("Microsoft YaHei UI", 17, "bold")
FONT_HUGE = ("Microsoft YaHei UI", 26, "bold")
FONT_MONO = ("Consolas", 11)
FONT_MONO_B = ("Consolas", 11, "bold")

RADIUS = 11              # ボタン・タブの角丸
PANEL_RADIUS = 16        # パネルの角丸


def parent_bg(w: tk.Misc) -> str:
    """親の背景色（Canvas を貼るときに隙間が目立たないように）。"""
    try:
        return str(w.cget("bg"))
    except Exception:
        return BG0


def round_rect(c: tk.Canvas, x0: float, y0: float, x1: float, y1: float, r: float, **kw):
    """Canvas に角丸の四角を描く（smooth ポリゴン）。"""
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    pts = [
        x0 + r, y0, x1 - r, y0, x1 - r, y0, x1, y0,
        x1, y0 + r, x1, y1 - r, x1, y1 - r, x1, y1,
        x1 - r, y1, x0 + r, y1, x0 + r, y1, x0, y1,
        x0, y1 - r, x0, y0 + r, x0, y0 + r, x0, y0,
    ]
    return c.create_polygon(pts, smooth=True, **kw)


def apply(root: tk.Misc) -> ttk.Style:
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass

    root.option_add("*Font", FONT)
    root.option_add("*selectBackground", SCARLET_LO)
    root.option_add("*selectForeground", FG)

    st.configure(".", background=BG0, foreground=FG, fieldbackground=BG2,
                 bordercolor=LINE, lightcolor=BG1, darkcolor=BG0,
                 troughcolor=BG1, focuscolor=SCARLET, font=FONT)

    st.configure("TFrame", background=BG1)
    st.configure("Bg.TFrame", background=BG0)
    st.configure("Card.TFrame", background=BG2, relief="flat")
    st.configure("TLabel", background=BG1, foreground=FG)
    st.configure("Bg.TLabel", background=BG0, foreground=FG)
    st.configure("Dim.TLabel", background=BG1, foreground=DIM)
    st.configure("DimBg.TLabel", background=BG0, foreground=DIM)
    st.configure("H1.TLabel", background=BG1, foreground=FG, font=FONT_T)
    st.configure("H2.TLabel", background=BG1, foreground=SCARLET_HI, font=FONT_B)
    st.configure("Mono.TLabel", background=BG1, foreground=FG2, font=FONT_MONO)
    st.configure("MonoBg.TLabel", background=BG0, foreground=FG2, font=FONT_MONO)

    st.configure("TLabelframe", background=BG1, bordercolor=LINE, relief="solid", borderwidth=1)
    st.configure("TLabelframe.Label", background=BG1, foreground=SCARLET_HI, font=FONT_B)

    st.configure("TEntry", fieldbackground=BG3, foreground=FG, insertcolor=SCARLET_HI,
                 bordercolor=LINE2, padding=4)
    st.map("TEntry", bordercolor=[("focus", SCARLET)], fieldbackground=[("focus", BG3)])

    st.configure("TCombobox", fieldbackground=BG3, background=BG2, foreground=FG,
                 arrowcolor=SCARLET_HI, bordercolor=LINE2, padding=3)
    st.map("TCombobox", fieldbackground=[("readonly", BG3)])

    st.configure("TCheckbutton", background=BG1, foreground=FG2)
    st.map("TCheckbutton", background=[("active", BG1)],
           indicatorcolor=[("selected", SCARLET), ("!selected", BG3)])
    st.configure("TRadiobutton", background=BG1, foreground=FG2)
    st.map("TRadiobutton", background=[("active", BG1)],
           indicatorcolor=[("selected", SCARLET), ("!selected", BG3)])

    st.configure("Treeview", background=BG1, fieldbackground=BG1, foreground=FG,
                 bordercolor=LINE, rowheight=30, borderwidth=0, font=FONT)
    st.map("Treeview", background=[("selected", SCARLET_LO)], foreground=[("selected", "#ffffff")])
    st.configure("Treeview.Heading", background=BG2, foreground=SCARLET_HI,
                 relief="flat", font=FONT_B, padding=(10, 6))
    st.map("Treeview.Heading", background=[("active", BG3)])

    st.configure("Vertical.TScrollbar", background=BG2, troughcolor=BG0,
                 bordercolor=BG0, arrowcolor=SCARLET_HI, gripcount=0, width=11)
    st.map("Vertical.TScrollbar", background=[("active", SCARLET_LO)])
    st.configure("Horizontal.TScrollbar", background=BG2, troughcolor=BG0,
                 bordercolor=BG0, arrowcolor=SCARLET_HI, gripcount=0, height=11)

    st.configure("TScale", background=BG1, troughcolor=BG2)
    st.configure("Scarlet.Horizontal.TProgressbar", background=SCARLET, troughcolor=BG2,
                 bordercolor=BG2, lightcolor=SCARLET, darkcolor=SCARLET)
    st.configure("TPanedwindow", background=BG0)
    st.configure("Sash", background=LINE, gripcount=0)
    return st


# ------------------------------------------------------------------ ボタン
from tkinter import font as tkfont


class ScarletButton(tk.Canvas):
    """角丸ボタン。tk.Button と同じ感じで text / command / kind を渡せる。"""

    _STYLE = {
        "solid": (SCARLET, "#ffffff", SCARLET_HI, "#ffffff", SCARLET_DEEP),
        "ghost": (BG2, FG, BG3, "#ffffff", LINE2),
        "flat": (BG1, FG2, BG3, SCARLET_HI, BG1),
        "danger": (BG2, SCARLET_HI, SCARLET_LO, "#ffffff", LINE2),
    }
    PAD_X = 18
    PAD_Y = 8

    def __init__(self, master, text="", command=None, kind="solid", width=None,
                 font=None, height=None, radius=RADIUS, **kw):
        self._kind = kind
        fill, fg, hfill, hfg, outline = self._STYLE.get(kind, self._STYLE["solid"])
        self._fill, self._fg = fill, fg
        self._hfill, self._hfg = hfill, hfg
        self._outline = outline
        self._text = text
        self._font = font or FONT_B
        self._radius = radius
        self._command = command
        self._hover = False
        self._pressed = False
        self._state = "normal"
        w, h = self._want()
        super().__init__(master, bg=parent_bg(master), highlightthickness=0, bd=0,
                         cursor="hand2", width=w, height=h, **kw)
        self._fixed_w = bool(width)
        if height:
            super().configure(height=height)
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)

    # --- 大きさ
    def _want(self) -> tuple[int, int]:
        f = tkfont.Font(font=self._font)
        lines = str(self._text).split("\n")
        w = max((f.measure(s) for s in lines), default=0) + self.PAD_X * 2
        h = f.metrics("linespace") * len(lines) + self.PAD_Y * 2
        return max(20, w), max(24, h)

    # --- 描画
    def _redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            w, h = self._want()
        disabled = self._state == "disabled"
        if disabled:
            fill, fg, outline = BG2, DIM, LINE
        elif self._pressed:
            fill, fg, outline = self._outline if self._kind == "solid" else SCARLET_LO, \
                "#ffffff", SCARLET_LO
        elif self._hover:
            fill, fg, outline = self._hfill, self._hfg, SCARLET_LO
        else:
            fill, fg, outline = self._fill, self._fg, self._outline
        round_rect(self, 1, 1, w - 1, h - 1, self._radius, fill=fill, outline=outline)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)

    # --- 状態
    def _set_hover(self, on: bool) -> None:
        self._hover = on
        self.configure(cursor="arrow" if self._state == "disabled" else "hand2")
        self._redraw()

    def _press(self, _e=None) -> None:
        if self._state == "disabled":
            return
        self._pressed = True
        self._redraw()

    def _release(self, _e=None) -> None:
        if self._state == "disabled":
            return
        was = self._pressed
        self._pressed = False
        self._redraw()
        if was and self._command:
            self._command()

    # --- tk.Button 互換
    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw = {**cnf, **kw}
        text = kw.pop("text", None)
        bg = kw.pop("bg", None) or kw.pop("background", None)
        fg = kw.pop("fg", None) or kw.pop("foreground", None)
        state = kw.pop("state", None)
        for k in ("highlightbackground", "highlightcolor", "activebackground",
                  "activeforeground", "relief", "overrelief", "bd",
                  "disabledforeground"):
            kw.pop(k, None)
        dirty = False
        if text is not None:
            self._text = text
            dirty = True
        if bg is not None:
            self._fill = bg
            self._hfill = BG3
            dirty = True
        if fg is not None:
            self._fg = fg
            dirty = True
        if state is not None:
            self._state = str(state)
            dirty = True
        if dirty:
            if not self._fixed_w and text is not None:
                w, h = self._want()
                tk.Canvas.configure(self, width=w, height=h)
            self._redraw()
        if kw:
            tk.Canvas.configure(self, **kw)

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key in ("bg", "background"):
            return self._fill
        if key in ("fg", "foreground"):
            return self._fg
        if key == "state":
            return self._state
        return tk.Canvas.cget(self, key)

    def invoke(self):
        if self._command and self._state != "disabled":
            self._command()


class RoundPanel(tk.Canvas):
    """角丸のパネル。中身は .body（普通の Frame）に詰める。"""

    def __init__(self, master, radius=PANEL_RADIUS, fill=BG1, outline=LINE,
                 padding=1, **kw):
        super().__init__(master, bg=parent_bg(master), highlightthickness=0, bd=0, **kw)
        self.radius, self.fill, self.outline, self.padding = radius, fill, outline, padding
        self.body = tk.Frame(self, bg=fill)
        self._win = self.create_window(padding, padding, window=self.body, anchor="nw")
        self.bind("<Configure>", self._relayout)

    def fit(self) -> None:
        """中身の大きさに合わせてパネルを縮める（伸ばしたくないときに使う）。"""
        self.body.update_idletasks()
        p = self.padding
        self.configure(width=self.body.winfo_reqwidth() + 2 * p,
                       height=self.body.winfo_reqheight() + 2 * p)

    def _relayout(self, e=None) -> None:
        w = e.width if e is not None else self.winfo_width()
        h = e.height if e is not None else self.winfo_height()
        self.delete("panelbg")
        p = self.padding
        round_rect(self, 0.5, 0.5, w - 0.5, h - 0.5, self.radius,
                   fill=self.fill, outline=self.outline, tags=("panelbg",))
        self.tag_lower("panelbg")
        self.coords(self._win, p, p)
        self.itemconfigure(self._win, width=max(1, w - 2 * p), height=max(1, h - 2 * p))


class Chip(tk.Frame):
    """小さい選択チップ（母音一覧などで使う）。"""

    def __init__(self, master, text, count, command, **kw):
        super().__init__(master, bg=BG2, highlightthickness=1,
                         highlightbackground=LINE, **kw)
        self.command = command
        self.on = False
        self.lbl = tk.Label(self, text=text, bg=BG2, fg=FG, font=FONT_B,
                            padx=8, pady=2, cursor="hand2")
        self.lbl.pack(side="left")
        self.cnt = tk.Label(self, text=str(count), bg=BG2, fg=DIM, font=FONT_MONO,
                            padx=6, cursor="hand2")
        self.cnt.pack(side="left")
        for w in (self, self.lbl, self.cnt):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", lambda _e: self._paint(BG3, FG, SCARLET_HI if self.on else DIM))
            w.bind("<Leave>", lambda _e: self._paint())

    def _paint(self, bg=None, fg=None, cnt=None):
        if bg is None:
            if self.on:
                bg, fg, cnt = SCARLET, "#ffffff", "#ffd9de"
            else:
                bg, fg, cnt = BG2, FG, DIM
        for w in (self, self.lbl, self.cnt):
            w.configure(bg=bg)
        self.lbl.configure(fg=fg)
        self.cnt.configure(fg=cnt)

    def set_on(self, on: bool) -> None:
        self.on = on
        self._paint()

    def _click(self, _e=None):
        if self.command:
            self.command(self)


def separator(master, **kw) -> tk.Frame:
    return tk.Frame(master, bg=LINE, height=1, **kw)


def title(master, text: str, bg: str = BG1) -> tk.Label:
    return tk.Label(master, text=text, bg=bg, fg=FG, font=("Microsoft YaHei UI", 12, "bold"))


def accent_text(master, text: str, bg: str = BG1, size: int = 9) -> tk.Label:
    return tk.Label(master, text=text, bg=bg, fg=SCARLET_HI,
                    font=("Microsoft YaHei UI", size, "bold"))


# ------------------------------------------------------------ 暗色タイトルバー
def dark_titlebar(win, caption: str = BG0, border: str = SCARLET_LO) -> None:
    """Windows のタイトルバーを暗くする（失敗しても無視）。"""
    import os

    if os.name != "nt":
        return
    try:
        import ctypes

        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()

        def ref(hexcolor: str) -> int:
            r = int(hexcolor[1:3], 16)
            g = int(hexcolor[3:5], 16)
            b = int(hexcolor[5:7], 16)
            return r | (g << 8) | (b << 16)

        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))  # dark mode
        for attr, color in ((35, caption), (36, FG), (34, border)):
            c = ctypes.c_int(ref(color))
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(c), ctypes.sizeof(c))
    except Exception:
        pass


def style_scrollbar(sb) -> None:
    """tk.Scrollbar（ScrolledText 内蔵のもの）を暗色に。"""
    try:
        sb.configure(bg=BG2, troughcolor=BG0, activebackground=SCARLET_LO,
                     disabledbackground=BG2, bd=0, relief="flat",
                     highlightthickness=0, elementborderwidth=0, width=11)
    except Exception:
        pass



class NavTab(tk.Canvas):
    """大きいタブ（角丸のカプセル）。選択中は緋色で塗る。"""

    F_MAIN = ("Microsoft YaHei UI", 14, "bold")

    def __init__(self, master, text, command, sub: str = "", width: int = 0):
        super().__init__(master, bg=parent_bg(master), highlightthickness=0, bd=0,
                         cursor="hand2")
        self.command = command
        self.active = False
        self._hover = False
        self._text = text
        self._sub = sub
        f1, f2 = tkfont.Font(font=self.F_MAIN), tkfont.Font(font=FONT_S)
        self._w1 = f1.measure(text)
        self._w2 = f2.measure(sub) if sub else 0
        w = self._w1 + (self._w2 + 18 if sub else 0) + 44
        h = max(f1.metrics("linespace"), f2.metrics("linespace")) + 26
        self.configure(width=max(w, width or 0), height=h)
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Button-1>", lambda _e: self.command and self.command())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))

    def _set_hover(self, on: bool) -> None:
        self._hover = on
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        if self.active:
            fill, fg, sub_fg, outline = SCARLET, "#ffffff", "#ffd6dc", SCARLET_HI
        elif self._hover:
            fill, fg, sub_fg, outline = BG2, FG, DIM, LINE2
        else:
            fill, fg, sub_fg, outline = BG1, FG2, DIM, LINE
        round_rect(self, 1, 1, w - 1, h - 1, RADIUS + 3, fill=fill, outline=outline)
        x = 20
        self.create_text(x, h / 2, anchor="w", text=self._text, fill=fg, font=self.F_MAIN)
        if self._sub:
            self.create_text(x + self._w1 + 12, h / 2, anchor="w", text=self._sub,
                             fill=sub_fg, font=FONT_S)
        if self.active:
            self.create_oval(w - 20, h / 2 - 3, w - 14, h / 2 + 3,
                             fill="#ffffff", outline="")

    def set_active(self, on: bool, sub: str | None = None) -> None:
        self.active = on
        if sub is not None and sub != self._sub:
            self._sub = sub
            self._w2 = tkfont.Font(font=FONT_S).measure(sub)
            # 文字が伸びたら幅も広げる（サブ文字が切れないように）
            self.configure(width=self._w1 + (self._w2 + 18 if sub else 0) + 44)
        self._redraw()


class PanSlider(tk.Canvas):
    """角丸の横スライダー。つまみをドラッグして表示範囲を動かす。

    command(value, fraction) が呼ばれる。
      value    … 可動域の上限に対する位置（全域表示中は常に 0）
      fraction … バー上のクリック位置 0..1（全域表示から一気に拡大するときに使う）
    """

    THUMB_W = 56
    TRACK_H = 10

    def __init__(self, master, command=None, height=26, **kw):
        super().__init__(master, bg=parent_bg(master), highlightthickness=0, bd=0,
                         height=height, cursor="hand2", **kw)
        self._command = command
        self._hi = 0.0
        self._val = 0.0
        self._enabled = True
        self._hover = False
        self._drag = False
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)

    # ---------------------------------------------------------------- 値
    def set_range(self, hi: float, value: float, enabled: bool = True) -> None:
        self._hi = max(0.0, float(hi))
        self._val = min(max(0.0, float(value)), self._hi)
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._redraw()

    def get(self) -> float:
        return self._val

    def set(self, value: float) -> None:
        self._val = min(max(0.0, float(value)), self._hi)
        self._redraw()

    # ---------------------------------------------------------------- 座標
    def _pad(self) -> float:
        return self.THUMB_W / 2

    def _track_span(self) -> float:
        return max(1.0, self.winfo_width() - 2 * self._pad())

    def _x_of(self, v: float) -> float:
        span = self._track_span()
        if self._hi <= 1e-9:
            return self._pad() + span / 2
        return self._pad() + span * max(0.0, min(1.0, v / self._hi))

    def _val_of(self, x: float) -> float:
        span = self._track_span()
        return max(0.0, min(self._hi, (x - self._pad()) / span * self._hi))

    # ---------------------------------------------------------------- 描画
    def _redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        pad, cy = self._pad(), h / 2
        track_h = self.TRACK_H
        # レール
        round_rect(self, pad - 6, cy - track_h / 2, w - pad + 6, cy + track_h / 2,
                   track_h / 2, fill=BG2, outline=LINE)
        # 動いた分（つまみまで）
        cx = self._x_of(self._val)
        if cx - pad > 2:
            round_rect(self, pad - 6, cy - track_h / 2, cx, cy + track_h / 2,
                       track_h / 2, fill=SCARLET_LO, outline="")
        # つまみ（純色・線なし）
        tw, th = self.THUMB_W, min(20.0, h - 6)
        if not self._enabled:
            fill = BG3
        elif self._drag or self._hover:
            fill = SCARLET_HI
        else:
            fill = SCARLET
        round_rect(self, cx - tw / 2, cy - th / 2, cx + tw / 2, cy + th / 2,
                   th / 2, fill=fill, outline="")

    def _set_hover(self, on: bool) -> None:
        self._hover = on
        self._redraw()

    # ---------------------------------------------------------------- 操作
    def _fire(self, x: float) -> None:
        frac = max(0.0, min(1.0, (x - self._pad()) / self._track_span()))
        if self._command:
            self._command(self._val_of(x), frac)

    def _press(self, e) -> None:
        self._drag = True
        self._val = self._val_of(e.x)
        self._redraw()
        self._fire(e.x)

    def _move(self, e) -> None:
        if not self._drag:
            return
        self._val = self._val_of(e.x)
        self._redraw()
        self._fire(e.x)

    def _release(self, _e=None) -> None:
        self._drag = False
        self._redraw()
