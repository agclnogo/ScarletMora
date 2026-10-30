"""歌詞 → 拼句表。どのモーラをどのファイルの何秒から取るか。"""

from __future__ import annotations

import csv
import os
import re

from . import kana as K
from .bank import load_all


def _score(row: dict, used_clips: set, used_sources: set) -> float:
    s = float(row["conf"])
    if "低信" in row["flags"]:
        s -= 0.35
    if "短" in row["flags"]:
        s -= 0.15
    if "長" in row["flags"]:
        s -= 0.10
    if "弱" in row["flags"]:
        s -= 0.25
    key = (row["source"], row["start"])
    if key in used_clips:
        s -= 0.60            # 同じ断片の使い回しは避ける
    elif row["source"] in used_sources:
        s -= 0.06
    if row["pos"] == "句头":
        s += 0.02
    return s


def _candidates(bank: dict[str, list], mora: str) -> list[dict]:
    return bank.get(mora, [])


def _vowel_consonant(mora_text: str) -> tuple[str | None, str | None]:
    ms = [m for m in K.mora_split(mora_text) if m.kind != "pause"]
    if not ms:
        return None, None
    m = ms[0]
    v = K.vowel_of(m)
    cons = m.phones[0] if len(m.phones) > 1 else None
    return v, cons


def _substitutes(bank: dict[str, list], mora_text: str, limit: int = 3) -> list[str]:
    """無い音の代わりに使えそうな音を、母音優先・子音一致優先で出す。"""
    v, cons = _vowel_consonant(mora_text)
    if v is None:
        return []
    scored = []
    for k in bank:
        kv, kc = _vowel_consonant(k)
        if kv != v:
            continue
        # 拗音（きゃ等）は拗音どうしで置き換えたほうが聴感が近い
        pal = 0 if (cons and cons.endswith("y")) == (kc and kc.endswith("y")) else 1
        scored.append((0 if kc == cons else 1, pal, -len(bank[k]), k))
    scored.sort()
    return [s[-1] for s in scored[:limit]]


def make_plan(out_dir: str, lyrics_path: str, out_sub: str = "plan") -> None:
    data = load_all(out_dir)
    bank: dict[str, list] = {}
    for name, marks in data.items():
        for m in marks:
            bank.setdefault(m.mora, []).append({
                "mora": m.mora, "source": name, "start": m.start, "end": m.end,
                "dur": m.dur, "conf": m.conf, "flags": m.flags,
                "pos": "句头" if m.idx == 0 or (marks[m.idx - 1].phrase != m.phrase) else "句中",
            })

    raw = open(lyrics_path, encoding="utf-8-sig").read()
    lines = [l.strip() for l in raw.splitlines() if l.strip()]

    plan_dir = os.path.join(out_dir, out_sub)
    os.makedirs(plan_dir, exist_ok=True)

    missing_all: dict[str, int] = {}
    index = []

    for ln, line in enumerate(lines, 1):
        kana = K.normalize(K.to_kana(line))
        moras = [m for m in K.mora_split(kana) if m.kind != "pause"]
        used_clips: set = set()
        used_sources: set = set()
        rows = []
        for i, m in enumerate(moras, 1):
            cands = sorted(_candidates(bank, m.text),
                           key=lambda r: -_score(r, used_clips, used_sources))
            if not cands:
                missing_all[m.text] = missing_all.get(m.text, 0) + 1
                subs = _substitutes(bank, m.text)
                subs = [s for s in subs if _candidates(bank, s)]
                rows.append({"#": i, "mora": m.text, "source": "缺失", "start": "",
                             "end": "", "dur": "", "conf": "", "flags": "",
                             "backup1": "", "backup2": "",
                             "sub": "/".join(subs)})
                continue
            best = cands[0]
            used_clips.add((best["source"], best["start"]))
            used_sources.add(best["source"])
            rows.append({
                "#": i, "mora": m.text, "source": best["source"],
                "start": f"{best['start']:.3f}", "end": f"{best['end']:.3f}",
                "dur": f"{best['dur']*1000:.0f}ms", "conf": f"{best['conf']:.2f}",
                "flags": best["flags"],
                "backup1": _fmt(cands[1]) if len(cands) > 1 else "",
                "backup2": _fmt(cands[2]) if len(cands) > 2 else "",
                "sub": "",
            })

        slug = re.sub(r"[^\w一-龥ぁ-んァ-ヶー]+", "_", line)[:24] or f"line{ln:03d}"
        path = os.path.join(plan_dir, f"{ln:03d}_{slug}.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["#", "mora", "source", "start", "end",
                                              "dur", "conf", "flags", "backup1", "backup2", "sub"])
            w.writeheader()
            for r in rows:
                w.writerow(r)
        miss = [r["mora"] for r in rows if r["source"] == "缺失"]
        index.append((ln, line, len(rows), len(miss), path))

    # まとめ
    lines_out = [f"歌词 {len(lines)} 句 / 音库 {len(bank)} 种" "".join([])]
    lines_out = [f"歌词 {len(lines)} 句 / 音库 {len(bank)} 种", ""]
    for ln, line, n, miss, path in index:
        mark = "OK " if miss == 0 else f"缺{miss}"
        lines_out.append(f"{ln:03d} {mark:<4} {n:3d}音  {os.path.basename(path)}")
    if missing_all:
        lines_out.append("")
        lines_out.append("缺失音汇总（按出现次数）:")
        for k, v in sorted(missing_all.items(), key=lambda x: -x[1]):
            lines_out.append(f"  {k}  x{v}")
    with open(os.path.join(plan_dir, "_summary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines_out))
    print("\n".join(lines_out))


def _fmt(r: dict) -> str:
    return f"{r['source']}@{r['start']:.3f}"
