"""アプリの置き場所を一元的に解決する。

開発時（ソースから実行）と PyInstaller の onefile exe で
「データの置き場」と「同梱物の置き場」が違うので、ここだけ吸収する。
"""

from __future__ import annotations

import json
import os
import shutil
import sys

APP_NAME = "ScarletMora"
SETTINGS_NAME = "settings.json"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> str:
    """exe（または ScarletMora フォルダ）の場所。データはここに置く。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def resource_dir() -> str:
    """web/ など同梱物。onefile では一時フォルダに展開される。"""
    cands: list[str] = []
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", "") or ""
        if meipass:
            cands.append(os.path.join(meipass, "web"))
    cands.append(os.path.join(app_root(), "web"))
    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.abspath(os.path.join(here, "..", "..", "web")))
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[-1]


def web_file(name: str) -> str:
    return os.path.join(resource_dir(), name)


def worker_script() -> str:
    """外部 Python で走らせる worker.py の実パス。

    worker.py は exe に同梱せず、ディスク上の app\\scarlet\\worker.py を使う。
    frozen では __file__ が _MEIxxxx の一時展開先を指してしまうので、
    exe の隣の app\\scarlet\\worker.py を先に見る。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    cands: list[str] = []
    if is_frozen():
        cands.append(os.path.join(app_root(), "app", "scarlet", "worker.py"))
        meipass = getattr(sys, "_MEIPASS", "") or ""
        if meipass:
            cands.append(os.path.join(meipass, "scarlet", "worker.py"))
    cands.append(os.path.join(here, "worker.py"))
    for c in cands:
        if os.path.isfile(c):
            return c
    return cands[0]


def _ensure(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def libraries_dir() -> str:
    return _ensure(os.path.join(app_root(), "libraries"))


def models_dir() -> str:
    return _ensure(os.path.join(app_root(), "models"))


def default_model_dir() -> str:
    return os.path.join(models_dir(), MODEL_NAME)


def logs_dir() -> str:
    return _ensure(os.path.join(app_root(), "logs"))


# --------------------------------------------------------------------- 声学模型
# モデルはリポジトリには入れないが、ユーザー版 zip には同梱している（361MB）。
#   models\<MODEL_NAME>\        ← 既定（zip に入っている）
#   settings.json の "model"    ← ユーザーが選んだ場所（モデルを別に置いた場合）
# のどちらでもよい。GUI の「选择模型文件夹」はここに保存する。
MODEL_NAME = "japanese-hubert-base-phoneme-ctc-v4"
MODEL_URL = "https://huggingface.co/prj-beatrice/japanese-hubert-base-phoneme-ctc-v4"
MODEL_FILES = ("config.json", "vocab.json")
MODEL_WEIGHTS = (".safetensors", ".bin", ".h5")
MODEL_DOWNLOAD_CMD = (
    "hf download prj-beatrice/japanese-hubert-base-phoneme-ctc-v4 "
    f"--local-dir models\\{MODEL_NAME}"
)


def model_setting() -> str:
    """settings.json に保存されたモデルの場所（未設定なら空）。"""
    return str(load_settings().get("model") or "").strip()


def set_model_dir(path: str) -> None:
    set_setting("model", path or "")


def model_problem(path: str) -> str:
    """モデルフォルダとして使えるか。使えるなら ""、駄目なら理由を返す。

    フォルダがあるだけ（空 / 途中で切れたダウンロード）では足りないので、
    config.json・vocab.json・重みの 3 つが揃っているかまで見る。
    """
    if not path:
        return "没有指定模型文件夹"
    if not os.path.isdir(path):
        return "文件夹不存在"
    missing = [n for n in MODEL_FILES if not os.path.isfile(os.path.join(path, n))]
    if missing:
        return "缺少 " + " / ".join(missing)
    try:
        names = os.listdir(path)
    except OSError as e:
        return f"读不了: {e}"
    if not any(n.endswith(MODEL_WEIGHTS) for n in names):
        return "缺少权重文件（model.safetensors）"
    return ""


def is_model_dir(path: str) -> bool:
    return not model_problem(path)


def normalize_model_path(path: str) -> str:
    """選ばれたフォルダを実際のモデルフォルダに寄せる。

    モデル本体 / その親（models\）/ さらにその親（アプリのフォルダ）の
    どれを選んでも通す。どれも駄目なら、いちばん「それらしい」ものを返す
    （理由の表示に使う）。
    """
    p = (path or "").strip().strip('"')
    if not p:
        return ""
    p = os.path.abspath(os.path.expanduser(p))
    cands = [p, os.path.join(p, MODEL_NAME), os.path.join(p, "models", MODEL_NAME)]
    for c in cands:
        if is_model_dir(c):
            return c
    for c in cands:
        if os.path.isdir(c):
            return c
    return p


def find_model_dir() -> str:
    r"""使えるモデルフォルダを返す。どれも駄目なら ""。

    順番: settings.json → models\<MODEL_NAME> → models\ 直下のサブフォルダ。
    （フォルダ名を変えていても models\ の下なら拾う）
    """
    cands = [model_setting(), default_model_dir()]
    try:
        for name in sorted(os.listdir(models_dir())):
            sub = os.path.join(models_dir(), name)
            if os.path.isdir(sub):
                cands.append(sub)
    except OSError:
        pass
    seen: set[str] = set()
    for raw in cands:
        if not raw:
            continue
        p = normalize_model_path(raw)
        if p in seen:
            continue
        seen.add(p)
        if is_model_dir(p):
            return p
    return ""


def model_hint() -> str:
    """モデルが無いときの案内（ダイアログ / ログ用）。"""
    return (
        "分析（CTC 对齐）需要声学模型，约 361MB。\n"
        "用户版解压后 models\\ 里已经带着，被删掉或挪走了才会走到这里。\n\n"
        f"① 自动找的位置：\n   {default_model_dir()}\n\n"
        "② 模型放在别的地方也行，用「选择模型文件夹」指定一次\n"
        "   （会记进 settings.json 的 \"model\"，以后不用再选）。\n\n"
        "重新下载（任意 Python 环境里）：\n"
        "   pip install huggingface_hub\n"
        f"   {MODEL_DOWNLOAD_CMD}\n\n"
        f"下载页：{MODEL_URL}"
    )


# --------------------------------------------------------------------- 設定
_DEFAULT_SETTINGS = {
    "python": "",
    "port": 8765,
    "last_library": "",
    "shift": 0.02,
    "pad_head": 0.04,
    "pad_tail": 0.07,
}


def settings_path() -> str:
    return os.path.join(app_root(), SETTINGS_NAME)


def load_settings() -> dict:
    s = dict(_DEFAULT_SETTINGS)
    try:
        with open(settings_path(), encoding="utf-8") as f:
            s.update(json.load(f) or {})
    except Exception:
        pass
    return s


def save_settings(s: dict) -> None:
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def set_setting(key: str, value) -> None:
    s = load_settings()
    s[key] = value
    save_settings(s)


# --------------------------------------------------------------------- Python
def find_python(override: str = "") -> str:
    """解析（torch）を任せる外部 Python を探す。見つからなければ空。"""
    cand = (override or load_settings().get("python") or "").strip()
    if cand and os.path.isfile(cand):
        return cand
    if not is_frozen():
        exe = sys.executable
        if exe and os.path.isfile(exe):
            return exe
    for name in ("python", "python3", "py"):
        p = shutil.which(name)
        if p:
            return p
    return ""


def has_torch(python_exe: str, timeout: float = 25.0) -> tuple[bool, str]:
    """外部 Python に torch / transformers が入っているか。"""
    if not python_exe:
        return False, "Python が見つかりません"
    import subprocess

    # 注意: importlib.util はサブモジュールなので明示的に import する
    # （「import importlib」だけだと importlib.util が生えず AttributeError になる）
    code = (
        "import importlib.util as u\n"
        "miss=[]\n"
        "for m in ('torch','transformers','numpy'):\n"
        "    try:\n"
        "        ok = u.find_spec(m) is not None\n"
        "    except Exception:\n"
        "        ok = False\n"
        "    if not ok:\n"
        "        miss.append(m)\n"
        "print('OK' if not miss else 'MISSING:'+','.join(miss))\n"
    )
    try:
        r = subprocess.run([python_exe, "-c", code], capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return False, f"実行できない: {e}"

    out = (r.stdout or "").strip()
    if out.startswith("OK"):
        return True, "OK"
    if out.startswith("MISSING:"):
        miss = [m for m in out[len("MISSING:"):].split(",") if m]
        return False, "不足: " + ", ".join(miss)

    # 想定外の失敗。stderr の最終行だけ見せる（traceback を丸ごと出さない）
    lines = [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()]
    return False, lines[-1][:200] if lines else "判定できませんでした"
