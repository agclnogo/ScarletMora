"""素材の取り込み: 音声ファイル群 × 原文テキスト を突き合わせる。

対応する原文の形（core.pipeline.parse_transcript と同じ）:
  1) ファイル名<TAB>原文            … 1 行 1 本（推奨）
  2) ファイル名 / 改行 / 原文       … 2 行で 1 本（空行区切り）
原文にファイル名が無い場合は、音声のファイル名順に 1 行ずつ対応させる。
"""

from __future__ import annotations

import os

from .core.pipeline import parse_transcript

AUDIO_EXTS = (".wav",)


def scan_audio(folder: str, recursive: bool = False) -> list[str]:
    """フォルダ内の wav を集める（読み込み順はファイル名順）。"""
    out: list[str] = []
    if not folder or not os.path.isdir(folder):
        return out
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for fn in files:
                if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                    out.append(os.path.join(root, fn))
    else:
        for fn in os.listdir(folder):
            p = os.path.join(folder, fn)
            if os.path.isfile(p) and os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                out.append(p)
    out.sort(key=lambda p: os.path.basename(p))
    return out


def read_text(path: str) -> list[tuple[str, str]]:
    pairs = parse_transcript(path)
    out: list[tuple[str, str]] = []
    for name, text in pairs:
        n = name.strip().strip('"').strip("'")
        for ext in AUDIO_EXTS:
            if n.lower().endswith(ext):
                n = n[: -len(ext)]
                break
        out.append((n.strip(), (text or "").strip()))
    return out


def match(audio_files: list[str], pairs: list[tuple[str, str]]) -> dict:
    """音声と原文を突き合わせる。戻り値は取り込み前の下見。"""
    stems = {}
    for p in audio_files:
        stems.setdefault(os.path.splitext(os.path.basename(p))[0], p)

    text_map = {n: t for n, t in pairs}
    named = [n for n, _ in pairs if n]
    matched: list[dict] = []
    used_stem: set[str] = set()

    for name in named:
        p = stems.get(name)
        if not p:
            continue
        used_stem.add(name)
        matched.append({"name": name, "audio": p, "text": text_map.get(name, ""),
                        "has_text": bool(text_map.get(name))})

    # 原文にファイル名が書かれていない（＝並び順だけ）の場合の救済
    unnamed = [t for n, t in pairs if not n]
    if not matched and unnamed:
        rest = [p for p in audio_files]
        for i, p in enumerate(rest):
            stem = os.path.splitext(os.path.basename(p))[0]
            text = unnamed[i] if i < len(unnamed) else ""
            matched.append({"name": stem, "audio": p, "audio": p, "text": text,
                            "has_text": bool(text)})
            used_stem.add(stem)

    audio_only = [p for p in audio_files
                  if os.path.splitext(os.path.basename(p))[0] not in used_stem]
    text_only = [n for n in named if n not in used_stem]
    no_text = [m["name"] for m in matched if not m["has_text"]]
    return {
        "matched": matched,
        "audio_only": audio_only,
        "text_only": text_only,
        "no_text": no_text,
        "audio_files": len(audio_files),
        "pair_lines": len(pairs),
    }


def describe(r: dict) -> list[str]:
    """下見結果を人が読める行に。"""
    lines = [
        f"音声ファイル: {r['audio_files']} 本 / 原文の行: {r['pair_lines']} 本",
        f"突き合わせ成功: {len(r['matched'])} 本"
        + (f"（うち原文なし {len(r['no_text'])} 本）" if r["no_text"] else ""),
    ]
    if r["audio_only"]:
        lines.append(f"原文が見つからない音声: {len(r['audio_only'])} 本")
        lines += ["  - " + os.path.basename(p) for p in r["audio_only"][:12]]
        if len(r["audio_only"]) > 12:
            lines.append(f"  … ほか {len(r['audio_only']) - 12} 本")
    if r["text_only"]:
        lines.append(f"音声が見つからない原文: {len(r['text_only'])} 行")
        lines += ["  - " + n for n in r["text_only"][:12]]
        if len(r["text_only"]) > 12:
            lines.append(f"  … ほか {len(r['text_only']) - 12} 行")
    return lines


def collect(folder: str = "", files: list[str] | None = None,
            text_path: str = "", recursive: bool = False) -> dict:
    """フォルダ or 選択ファイル + テキスト → 下見レポート。"""
    audio = list(files or [])
    if folder:
        audio += scan_audio(folder, recursive=recursive)
    seen: set[str] = set()
    audio = [p for p in audio
             if not (os.path.abspath(p) in seen or seen.add(os.path.abspath(p)))]
    audio.sort(key=lambda p: os.path.basename(p))
    pairs = read_text(text_path) if (text_path and os.path.isfile(text_path)) else []
    return match(audio, pairs)
