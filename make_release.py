r"""用户版 zip を作る。exe / app\ / models\ / 説明をひとまとめにする。

    python make_release.py                 # ScarletMora-<ver>-win64.zip
    python make_release.py --no-model      # モデル抜き（テスト用・軽い）
    python make_release.py --out foo.zip

**モデル（361MB）を入れ忘れると、ユーザー版で「缺少模型」になる**ので、
ここで必ず models\ を入れる。zip は解压即用（Python + torch だけ別途）。

重み（*.safetensors）は deflate してもほとんど縮まないので、そこだけ
圧縮レベルを下げて時間を節約する。.cache / __pycache__ / .pyc は入れない。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
MODEL_NAME = "japanese-hubert-base-phoneme-ctc-v4"
DOCS = ["README.md", "说明.txt", "开发任务.txt", "requirements.txt",
        "requirements-analysis.txt", "build.bat"]
SKIP_DIRS = {"__pycache__", ".cache", ".git"}
SKIP_EXT = {".pyc", ".pyo"}
# ほぼ縮まない → 低圧縮で速く（endswith に渡すので tuple）
FAST_EXT = (".safetensors", ".bin", ".h5", ".onnx", ".pt", ".pth")


def version() -> str:
    path = os.path.join(ROOT, "app", "scarlet", "__init__.py")
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r'__version__\s*=\s*"([^"]+)"', f.read())
        if m:
            return m.group(1)
    except OSError:
        pass
    return "0.0.0"


def collect(with_model: bool) -> list[str]:
    files = ["ScarletMora.exe", *DOCS]
    for top in ("app", "models" if with_model else ""):
        if not top:
            continue
        for base, dirs, names in os.walk(os.path.join(ROOT, top)):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in sorted(names):
                if os.path.splitext(name)[1] in SKIP_EXT:
                    continue
                files.append(os.path.relpath(os.path.join(base, name), ROOT))
    return [f for f in files if os.path.isfile(os.path.join(ROOT, f))]


def check_model() -> str:
    """同梱するモデルが揃っているか。足りなければ理由を返す。"""
    d = os.path.join(ROOT, "models", MODEL_NAME)
    if not os.path.isdir(d):
        return f"没有 {d}"
    for name in ("config.json", "vocab.json"):
        if not os.path.isfile(os.path.join(d, name)):
            return f"缺少 {os.path.join(d, name)}"
    if not any(n.endswith(FAST_EXT) for n in os.listdir(d)):
        return f"缺少权重文件（{d}）"
    return ""


def main(argv: list[str] | None = None) -> int:
    ver = version()
    ap = argparse.ArgumentParser(prog="make_release")
    ap.add_argument("--out", default=os.path.join(ROOT, f"ScarletMora-{ver}-win64.zip"))
    ap.add_argument("--no-model", action="store_true", help="モデルを入れない（軽い）")
    a = ap.parse_args(argv)

    with_model = not a.no_model
    if with_model:
        bad = check_model()
        if bad:
            print(f"模型没准备好: {bad}", file=sys.stderr)
            print("先按 README 的「声学模型」下载，或加 --no-model 出轻量包。",
                  file=sys.stderr)
            return 1

    files = collect(with_model)
    if not any(f == "ScarletMora.exe" for f in files):
        print("没有 ScarletMora.exe，先跑 build.bat", file=sys.stderr)
        return 1

    tmp = a.out + ".tmp"
    total = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files:
            p = os.path.join(ROOT, rel)
            ext = os.path.splitext(p)[1].lower()
            if ext in FAST_EXT:
                # 重みは deflate すると逆に 5% ほど増えるので無圧縮で入れる
                z.write(p, rel.replace(os.sep, "/"), compress_type=zipfile.ZIP_STORED)
            else:
                z.write(p, rel.replace(os.sep, "/"), compresslevel=9)
            total += os.path.getsize(p)
    os.replace(tmp, a.out)

    print(f"wrote {os.path.basename(a.out)}")
    print(f"  entries={len(files)}  解开后={total / 1048576:.0f}MiB  "
          f"zip={os.path.getsize(a.out) / 1048576:.0f}MiB  model={'有' if with_model else '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
