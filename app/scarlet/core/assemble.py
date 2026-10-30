"""歌詞を実際に音にする（拼句）。

切点表から音を拾って、短いクロスフェードで繋ぐ。
音高は触らない（必要になったら別工程）。
"""

from __future__ import annotations

import csv
import os

import numpy as np

from . import kana as K
from .audio import read_audio, write_wav
from .bank import load_all
from .plan import _score, _substitutes


def load_bank_map(out_dir: str) -> dict[str, list[dict]]:
    data = load_all(out_dir)
    bank: dict[str, list[dict]] = {}
    for name, marks in data.items():
        for m in marks:
            bank.setdefault(m.mora, []).append({
                "mora": m.mora, "source": name,
                "start": m.start, "end": m.end, "dur": m.dur,
                "conf": m.conf, "flags": m.flags, "mark": m,
                "pos": "句头" if m.idx == 0 or (marks[m.idx - 1].phrase != m.phrase) else "句中",
            })
    return bank


def choose_line(bank: dict[str, list[dict]], moras: list[K.Mora],
                allow_sub: bool = True) -> list[dict]:
    """1 行分の音を選ぶ。同じ断片の使い回しは避ける。"""
    used_clips: set = set()
    used_sources: set = set()
    out: list[dict] = []
    prev_vowel = None
    for m in moras:
        cands_pool = bank.get(m.text, [])
        if m.text == "ー" and prev_vowel:
            # 長音は直前のモーラと同じ母音でないと別の音に聞こえる
            same = [c for c in cands_pool
                    if (c["mark"].phones or "").split("/")[0] == prev_vowel]
            if same:
                cands_pool = same
        cands = sorted(cands_pool,
                       key=lambda r: -_score(r, used_clips, used_sources))
        if cands:
            best = cands[0]
            used_clips.add((best["source"], best["start"]))
            used_sources.add(best["source"])
            out.append({"mora": m.text, "pick": best, "kind": "ok", "backup": cands[1:3]})
            prev_vowel = K.vowel_of(m) or prev_vowel
            continue
        if allow_sub:
            subs = [s for s in _substitutes(bank, m.text) if bank.get(s)]
            if subs:
                s = subs[0]
                sc = sorted(bank[s], key=lambda r: -_score(r, used_clips, used_sources))
                best = sc[0]
                used_clips.add((best["source"], best["start"]))
                used_sources.add(best["source"])
                out.append({"mora": m.text, "pick": best, "kind": "sub",
                            "sub_from": s, "backup": sc[1:3]})
                prev_vowel = K.vowel_of(m) or prev_vowel
                continue
        out.append({"mora": m.text, "pick": None, "kind": "missing", "backup": []})
        prev_vowel = K.vowel_of(m) or prev_vowel
    return out


def _rms_db(x: np.ndarray) -> float:
    if len(x) == 0:
        return -120.0
    r = float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))
    return 20 * np.log10(max(r, 1e-9))


def _fade_in(x: np.ndarray, sr: int, ms: float) -> np.ndarray:
    n = min(int(sr * ms / 1000), max(0, len(x)))
    if n <= 0:
        return x
    y = x.copy()
    y[:n] *= np.linspace(0, 1, n, dtype=np.float32)
    return y


def _fade_out(x: np.ndarray, sr: int, ms: float) -> np.ndarray:
    n = min(int(sr * ms / 1000), max(0, len(x)))
    if n <= 0:
        return x
    y = x.copy()
    y[-n:] *= np.linspace(1, 0, n, dtype=np.float32)
    return y


def _cut(au, m, head: float, tail: float, sr: int) -> np.ndarray:
    a = max(0.0, m.start - head)
    b = min(au.duration, m.end + tail)
    return au.data[int(a * au.sr):int(b * au.sr)].astype(np.float32)


def _normalize(x: np.ndarray, target_db: float) -> np.ndarray:
    cur = _rms_db(x)
    if cur < -70:
        return x
    g = 10 ** ((target_db - cur) / 20.0)
    return np.clip(x * g, -0.99, 0.99).astype(np.float32)


def _place(out: np.ndarray, seg: np.ndarray, start: int, xf: int) -> None:
    """out の start に seg を置く。start が前の音と重なる場合、先頭 xf サンプルを等パワーで混ぜる。"""
    end = start + len(seg)
    if end > len(out):
        seg = seg[: len(out) - start]
        end = len(out)
    if xf > 0 and start > 0 and len(seg) > xf:
        ramp = np.linspace(0, 1, xf, dtype=np.float32)
        # 等パワー（二乗和が 1）で混ぜると、繋ぎ目が小さくならない
        mix = np.sqrt(1.0 - ramp) * out[start:start + xf] + np.sqrt(ramp) * seg[:xf]
        out[start:start + xf] = mix
        out[start + xf:end] = seg[xf:]
    else:
        out[start:end] = seg


def assemble(out_dir: str, src_dir: str, lyrics_path: str, dst: str = "render",
             head: float = 0.008, tail: float = 0.015, fade_ms: float = 5.0,
             crossfade_ms: float = 8.0, target_db: float = -18.0,
             slot: float | None = None, line_gap: float = 0.45,
             allow_sub: bool = True, limit: int = 0) -> dict:
    bank = load_bank_map(out_dir)
    if not bank:
        raise RuntimeError("音库が空。先に align と bank を実行して")

    raw = open(lyrics_path, encoding="utf-8-sig").read()
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if limit:
        lines = lines[:limit]

    out_dir_full = os.path.join(out_dir, dst)
    os.makedirs(out_dir_full, exist_ok=True)

    cache: dict[str, object] = {}
    timeline: list[dict] = []
    renders: list[np.ndarray] = []
    sr_used = 44100
    missing_total: dict[str, int] = {}
    sub_total: dict[str, int] = {}

    for ln, line in enumerate(lines, 1):
        kana = K.normalize(K.to_kana(line))
        moras = [m for m in K.mora_split(kana) if m.kind != "pause"]
        picked = choose_line(bank, moras, allow_sub=allow_sub)

        # 1 行ぶんの長さを見積もる
        segs = []
        for p in picked:
            if not p["pick"]:
                dur = slot or 0.22
                segs.append((p, None, dur))
                continue
            src = p["pick"]["source"]
            if src not in cache:
                cache[src] = read_audio(os.path.join(src_dir, src + ".wav"))
            au = cache[src]
            sr_used = au.sr
            seg = _cut(au, p["pick"]["mark"], head, tail, sr_used)
            if slot and len(seg) > int(slot * sr_used):
                seg = seg[: int(slot * sr_used)]
            segs.append((p, seg, len(seg) / sr_used))

        if not segs:
            continue
        xf = int(crossfade_ms / 1000 * sr_used)
        total = sum(len(s[1]) for s in segs) + xf * 4 + 64
        out = np.zeros(max(1, total), dtype=np.float32)
        cursor = 0
        n_seg = len(segs)
        for i, (p, seg, d) in enumerate(segs):
            xf_here = xf if (i > 0 and p["pick"] and segs[i - 1][0]["pick"]) else 0
            start = 0 if i == 0 else max(0, cursor - xf_here)
            if p["pick"] is None:
                seg = np.zeros(int((slot or 0.22) * sr_used), dtype=np.float32)
                missing_total[p["mora"]] = missing_total.get(p["mora"], 0) + 1
            else:
                seg = seg.copy()
                # 端のフェードは行の最初と最後だけ。繋ぎ目はクロスフェードに任せる
                if i == 0:
                    seg = _fade_in(seg, sr_used, max(fade_ms, 2.0))
                if i == n_seg - 1:
                    seg = _fade_out(seg, sr_used, max(fade_ms, 8.0))
                seg = _normalize(seg, target_db)
                if p["kind"] == "sub":
                    key = p["mora"] + "→" + p.get("sub_from", "")
                    sub_total[key] = sub_total.get(key, 0) + 1
            _place(out, seg, start, xf_here)
            timeline.append({
                "line": ln, "idx": len(timeline) + 1, "mora": p["mora"],
                "kind": p["kind"],
                "source": (p["pick"]["source"] if p["pick"] else ""),
                "src_start": (round(p["pick"]["start"], 3) if p["pick"] else ""),
                "src_end": (round(p["pick"]["end"], 3) if p["pick"] else ""),
                "conf": (round(p["pick"]["conf"], 2) if p["pick"] else ""),
                "sub_from": p.get("sub_from", ""),
                "out_start": round(start / sr_used, 3),
                "out_end": round((start + len(seg)) / sr_used, 3),
            })
            cursor = start + len(seg)
        out = out[:cursor]

        name = f"line{ln:03d}.wav"
        write_wav(os.path.join(out_dir_full, name), out, sr_used)
        renders.append(out)
        renders.append(np.zeros(int(line_gap * sr_used), dtype=np.float32))

    full = np.concatenate(renders) if renders else np.zeros(1, dtype=np.float32)
    write_wav(os.path.join(out_dir_full, "all.wav"), full, sr_used)

    with open(os.path.join(out_dir, "timeline.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["line", "idx", "mora", "kind", "source",
                                          "src_start", "src_end", "conf", "sub_from",
                                          "out_start", "out_end"])
        w.writeheader()
        for r in timeline:
            w.writerow(r)

    return {
        "lines": len(renders) // 2, "segments": len(timeline),
        "duration": len(full) / sr_used, "dir": out_dir_full,
        "missing": missing_total, "subs": sub_total,
    }
